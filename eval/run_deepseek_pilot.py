"""Run the three-sample DeepSeek project scoring pilot when explicitly enabled."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Optional, Sequence

from app.deepseek_client import DeepSeekClient, DeepSeekConfig, DeepSeekError, PROMPT_VERSION, TEMPERATURE
from app.project_scorer import DIMENSIONS, InvalidProjectResponse, score_project
from app.route import load_jobs


ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "eval" / "project_score_cases.jsonl"
REPORT = ROOT / "eval" / "reports" / "deepseek-smoke.json"
SMOKE_IDS = ("full_30", "tools_only", "jailbreak_tools")
MAX_REQUESTS = 4
STOP_STATUSES = (400, 401, 402, 403, 404)


def _run_case(case, job, client):
    expected = {name: case[name] for name in DIMENSIONS}
    entry = {"case_id": case["case_id"], "job_id": job.jd_id, "expected": expected,
             "expected_total": case["total"]}
    completion = None

    def request(snippet, requirements):
        nonlocal completion
        completion = client.complete(snippet, requirements)
        return completion.text

    start = perf_counter()
    try:
        total = score_project(case["snippet"], job, request)
    except (DeepSeekError, InvalidProjectResponse) as exc:
        entry["status"] = "REVIEW"
        if isinstance(exc, DeepSeekError):
            entry["failure"] = exc.code
            if exc.status is not None:
                entry["http_status"] = exc.status
        else:
            entry["failure"] = "INVALID_PROJECT_RESPONSE"
    else:
        parsed = json.loads(completion.text)
        actual = {name: parsed[name]["points"] for name in DIMENSIONS}
        entry.update(status="SCORED", actual=actual, actual_total=total, matched=actual == expected)
    entry["latency_ms"] = round((perf_counter() - start) * 1000)
    if completion is not None:
        entry.update(response_model=completion.model, system_fingerprint=completion.fingerprint,
                     input_tokens=completion.input_tokens, output_tokens=completion.output_tokens)
    return entry


def run_pilot(cases, jobs, client, model, label_hash):
    report = {
        "provider": "deepseek", "model": model, "prompt_version": PROMPT_VERSION,
        "temperature": TEMPERATURE, "thinking": "disabled", "labels_sha256": label_hash,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested_cases": list(SMOKE_IDS), "request_limit": MAX_REQUESTS,
        "cases": [], "not_run": [],
    }
    for index, case in enumerate(cases):
        entry = _run_case(case, jobs[case["jd_id"]], client)
        report["cases"].append(entry)
        if entry.get("http_status") in STOP_STATUSES or entry.get("failure") == "REQUEST_LIMIT":
            report["not_run"] = [remaining["case_id"] for remaining in cases[index + 1:]]
            break

    valid = [entry for entry in report["cases"] if entry["status"] == "SCORED"]
    accounted = [entry for entry in report["cases"] if entry.get("input_tokens") is not None
                 and entry.get("output_tokens") is not None]
    input_tokens = sum(entry["input_tokens"] for entry in accounted)
    output_tokens = sum(entry["output_tokens"] for entry in accounted)
    report.update(
        requests=client.requests, valid_responses=len(valid),
        exact_matches=sum(entry["matched"] for entry in valid),
        project_mae=round(sum(abs(entry["actual_total"] - entry["expected_total"])
                              for entry in valid) / len(valid), 3) if valid else None,
        dimension_matches={
            name: sum(entry["actual"][name] == entry["expected"][name] for entry in valid)
            for name in DIMENSIONS
        },
        observed_usage={"responses": len(accounted), "input_tokens": input_tokens,
                        "output_tokens": output_tokens},
        estimated_usd_at_peak_without_cache=(
            round((0.30 * input_tokens + 1.20 * output_tokens) / 1_000_000, 7) if accounted else None
        ),
    )
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="make up to four billable requests")
    args = parser.parse_args(argv)
    if not args.live:
        print("Dry run, zero network requests. With --live: " + ", ".join(SMOKE_IDS))
        return 0

    try:
        config = DeepSeekConfig.from_env_file(ROOT / ".env")
        content = LABELS.read_bytes()
        labeled = {case["case_id"]: case for case in map(json.loads, content.decode("utf-8").splitlines())}
        cases = [labeled[case_id] for case_id in SMOKE_IDS]
        jobs = {job.jd_id: job for job in load_jobs()}
        if any(not case["snippet"].strip() or case["jd_id"] not in jobs for case in cases):
            raise ValueError("Invalid smoke cases")
        REPORT.parent.mkdir(parents=True, exist_ok=True)
    except DeepSeekError as exc:
        print(f"DeepSeek pilot setup failed: {exc.code}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print("Cannot prepare the synthetic pilot or report directory.", file=sys.stderr)
        return 2

    client = DeepSeekClient(config, max_requests=MAX_REQUESTS)
    report = run_pilot(cases, jobs, client, config.model, sha256(content).hexdigest())
    try:
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        print("Cannot write the DeepSeek pilot report.", file=sys.stderr)
        return 2
    print(f"DeepSeek pilot: {report['valid_responses']}/{len(cases)} valid, "
          f"{report['exact_matches']}/{len(cases)} match labels, {report['requests']} requests.")
    return 0 if report["exact_matches"] == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
