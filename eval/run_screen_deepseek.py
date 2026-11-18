"""Run a DeepSeek smoke check against the repository's synthetic screening PDF."""

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Optional, Sequence

from app.deepseek_client import DeepSeekClient, DeepSeekConfig, DeepSeekError, PROMPT_VERSION_V2, TEMPERATURE
from app.project_scorer import DIMENSIONS, make_injected_scorer
from app.screen import ScreenResult, screen_one


ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "data" / "synthetic" / "resumes" / "project_snippets" / "pdf_screen_jd1.pdf"
REPORT = ROOT / "eval" / "reports" / "screen-deepseek.json"
MAX_REQUESTS = 2


def _summary(result: ScreenResult) -> dict:
    score = result.score
    return {
        "extract_status": result.extract_status, "guard_status": result.guard_status,
        "jd_id": score.jd_id, "status": score.status, "reason_code": score.reason_code,
        "total": score.total, "project_source": score.project_source,
        "scores": asdict(score.scores) if score.scores is not None else None,
    }


def _run_live(client: DeepSeekClient, model: str, baseline: ScreenResult) -> dict:
    completion = None
    failure = None

    def request(snippet: str, job_requirements: str) -> str:
        nonlocal completion, failure
        try:
            completion = client.complete(snippet, job_requirements)
        except DeepSeekError as exc:
            failure = {"code": exc.code, "http_status": exc.status}
            raise
        return completion.text

    started_at = datetime.now(timezone.utc).isoformat()
    start = perf_counter()
    result = screen_one(PDF, project_scorer=make_injected_scorer(request))
    latency_ms = round((perf_counter() - start) * 1000)
    score = result.score
    passed = (
        result.extract_status == "OK" and result.guard_status == "PASS"
        and score.status == "SCORED" and score.jd_id == baseline.score.jd_id
        and score.project_source == "injected" and score.scores is not None
        and completion is not None and 1 <= client.requests <= MAX_REQUESTS
        and replace(score.scores, project=0) == baseline.score.scores
        and score.total == baseline.score.total + score.scores.project
    )
    report = {
        "fixture": PDF.name, "started_at_utc": started_at, "passed": passed,
        "provider": "deepseek", "model": model, "prompt_version": PROMPT_VERSION_V2,
        "temperature": TEMPERATURE, "thinking": "disabled", "latency_ms": latency_ms,
        "request_limit": MAX_REQUESTS, "requests": client.requests, "failure": failure,
        "baseline": _summary(baseline), "live": _summary(result),
    }
    if completion is not None:
        report.update(
            response_model=completion.model, system_fingerprint=completion.fingerprint,
            input_tokens=completion.input_tokens, output_tokens=completion.output_tokens,
        )
        if passed:
            payload = json.loads(completion.text)
            report["project_dimensions"] = {name: payload[name]["points"] for name in DIMENSIONS}
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="enable billable requests for the fixed fixture")
    args = parser.parse_args(argv)

    baseline = screen_one(PDF)
    score = baseline.score
    if (
        baseline.extract_status != "OK" or baseline.guard_status != "PASS"
        or score.status != "SCORED" or score.jd_id != "JD1"
        or score.project_source != "mock" or score.scores is None or score.scores.project != 0
    ):
        print("Cannot screen the synthetic fixture with the expected Mock baseline.", file=sys.stderr)
        return 2
    if not args.live:
        print(f"Dry run, zero network requests. {PDF.name}: {score.jd_id}, Mock total={score.total}.")
        return 0

    try:
        config = DeepSeekConfig.from_env_file(ROOT / ".env")
        REPORT.parent.mkdir(parents=True, exist_ok=True)
    except DeepSeekError as exc:
        print(f"DeepSeek screen setup failed: {exc.code}", file=sys.stderr)
        return 2
    except OSError:
        print("Cannot prepare the DeepSeek screen report directory.", file=sys.stderr)
        return 2

    client = DeepSeekClient(config, max_requests=MAX_REQUESTS, prompt_version=PROMPT_VERSION_V2)
    report = _run_live(client, config.model, baseline)
    try:
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        print("Cannot write the DeepSeek screen report.", file=sys.stderr)
        return 2
    live = report["live"]
    print(f"DeepSeek screen: {live['status']}, {live['jd_id']}, total={live['total']}, "
          f"requests={report['requests']}, report={REPORT.name}.")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
