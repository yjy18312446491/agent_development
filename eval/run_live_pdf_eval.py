"""Evaluate frozen synthetic PDFs with an explicit, bounded DeepSeek run."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter
from typing import List, Optional, Sequence, Tuple

from app.deepseek_client import DeepSeekClient, DeepSeekConfig, DeepSeekError, PROMPT_VERSION_V2, TEMPERATURE
from app.project_scorer import DIMENSIONS, POINT_LEVELS, make_injected_scorer
from app.screen import screen_many


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "eval" / "live_pdf_cases.json"
FIXTURES = ROOT / "data" / "synthetic" / "resumes" / "live_pdf_eval"
REPORT_DIR = ROOT / "eval" / "reports"
RETRY_BUDGET = 2
DECISION_FIELDS = ("guard_status", "jd_id", "status", "reason_code")


@dataclass(frozen=True)
class PreparedCase:
    label: dict
    path: Path
    mock: dict
    model_input: Optional[Tuple[str, str]]


def _screen(path: Path, client=None) -> dict:
    scorer = make_injected_scorer(client) if client is not None else None
    record = screen_many([path], project_scorer=scorer)[0].finalized.as_dict()
    del record["reason"]
    return record


def _prepare(label: dict) -> PreparedCase:
    case_id = label["id"]
    if not re.fullmatch(r"[a-z][a-z_0-9]*", case_id) or label["pdf"] != case_id + ".pdf":
        raise ValueError("Invalid fixture ID or filename")
    path = FIXTURES / label["pdf"]
    if path.resolve().parent != FIXTURES.resolve() or sha256(path.read_bytes()).hexdigest() != label["sha256"]:
        raise ValueError(f"{case_id}: fixture does not match the frozen SHA-256")

    points = label["project_dimensions"]
    if type(label["model_expected"]) is not bool or label["model_expected"] != (points is not None):
        raise ValueError(f"{case_id}: inconsistent model eligibility")
    if points is not None and (
        not isinstance(points, dict) or points.keys() != set(DIMENSIONS)
        or any(type(point) is not int or point not in POINT_LEVELS for point in points.values())
        or sum(points.values()) != label["project_total"]
    ):
        raise ValueError(f"{case_id}: invalid project dimension labels")
    project = label["project_total"]
    if label["status"] == "REVIEW":
        valid_total = project is None and label["rule_total"] == label["total"] == -1
    else:
        valid_total = type(project) is int and label["total"] == label["rule_total"] + project
        if not label["model_expected"]:
            valid_total = valid_total and project == 0
    if not valid_total:
        raise ValueError(f"{case_id}: inconsistent total labels")

    model_inputs = []

    def record_input(snippet: str, requirements: str) -> str:
        model_inputs.append((snippet, requirements))
        return json.dumps({name: {"points": 0, "evidence": ""} for name in DIMENSIONS})

    mock = _screen(path)
    preview = _screen(path, record_input)
    if (
        any(mock[key] != label[key] or preview[key] != label[key] for key in DECISION_FIELDS)
        or mock["total"] != label["rule_total"] or preview["total"] != mock["total"]
        or mock["scores"] != preview["scores"]
        or len(model_inputs) != int(label["model_expected"])
        or (mock["scores"] is not None and mock["scores"]["project"] != 0)
    ):
        raise ValueError(f"{case_id}: offline screening does not match frozen labels")
    return PreparedCase(label, path, mock, model_inputs[0] if model_inputs else None)


def load_cases() -> Tuple[List[PreparedCase], str]:
    contents = MANIFEST.read_text(encoding="utf-8")
    labels = json.loads(contents)
    case_ids = [label["id"] for label in labels]
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise ValueError("Empty manifest or duplicate case IDs")
    cases = [_prepare(label) for label in labels]
    if not any(case.model_input is not None for case in cases):
        raise ValueError("No model-eligible cases")
    return cases, sha256(contents.encode("utf-8")).hexdigest()


def _run_case(case: PreparedCase, client: DeepSeekClient, allow_retry: bool) -> dict:
    completion = None
    failure = None
    before = client.requests

    def request(snippet: str, requirements: str) -> str:
        nonlocal completion, failure
        if (snippet, requirements) != case.model_input or completion is not None or failure is not None:
            failure = {"code": "UNEXPECTED_MODEL_INPUT", "http_status": None}
            raise DeepSeekError(failure["code"])
        client.max_requests = before + 1 + int(allow_retry)
        try:
            completion = client.complete(snippet, requirements)
        except DeepSeekError as error:
            failure = {"code": error.code, "http_status": error.status}
            raise
        return completion.text

    started = perf_counter()
    live = _screen(case.path, request)
    used = client.requests - before
    eligible = case.model_input is not None
    same_decision = all(live[key] == case.mock[key] for key in DECISION_FIELDS)
    dimensions = None
    absolute_error = None
    full_match = None
    if failure is not None:
        outcome = "failed"
    elif not eligible and completion is None and same_decision and all(
        live[key] == case.mock[key] for key in ("scores", "total", "tier")
    ):
        outcome = "skipped"
    elif (
        completion is not None and same_decision and live["project_source"] == "injected"
        and live["scores"] is not None and case.mock["scores"] is not None
        and {**live["scores"], "project": 0} == case.mock["scores"]
        and live["total"] == case.mock["total"] + live["scores"]["project"]
    ):
        outcome = "valid"
        payload = json.loads(completion.text)
        dimensions = {name: payload[name]["points"] for name in DIMENSIONS}
        absolute_error = abs(live["scores"]["project"] - case.label["project_total"])
        full_match = dimensions == case.label["project_dimensions"]
    else:
        outcome = "not_run" if eligible and completion is None and not used else "failed"
        failure = {"code": live["reason_code"] or "UNEXPECTED_SCREEN_RESULT", "http_status": None}

    transport = None
    if completion is not None:
        transport = {
            "model": completion.model, "system_fingerprint": completion.fingerprint,
            "input_tokens": completion.input_tokens, "output_tokens": completion.output_tokens,
        }
    return {
        "case_id": case.label["id"], "expected": case.label, "mock": case.mock, "live": live,
        "outcome": outcome, "failure": failure, "requests": used, "retries": max(0, used - 1),
        "latency_ms": round((perf_counter() - started) * 1000), "transport": transport,
        "project_dimensions": dimensions, "project_absolute_error": absolute_error,
        "end_to_end_total_delta": live["total"] - case.label["total"] if outcome == "valid" else None,
        "full_match": full_match,
    }


def _commit() -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def run_live(cases: Sequence[PreparedCase], manifest_sha: str, client: DeepSeekClient, model: str) -> dict:
    rows = []
    retries = 0
    first = sum(case.model_input is not None for case in cases)
    started = datetime.now(timezone.utc).isoformat()
    commit = _commit()
    for case in cases:
        row = _run_case(case, client, allow_retry=retries < RETRY_BUDGET)
        retries += row["retries"]
        rows.append(row)

    valid = [row for row in rows if row["outcome"] == "valid"]
    return {
        "started_at_utc": started, "commit": commit, "manifest_sha256_normalized": manifest_sha,
        "provider": "deepseek", "model": model, "prompt_version": PROMPT_VERSION_V2,
        "temperature": TEMPERATURE, "thinking": "disabled",
        "planned_cases": len(cases), "first_attempt_budget": first, "retry_budget": RETRY_BUDGET,
        "request_limit": first + RETRY_BUDGET, "requests": client.requests, "retries": retries,
        "expected_skips": len(cases) - first, "skipped": sum(row["outcome"] == "skipped" for row in rows),
        "failed": sum(row["outcome"] == "failed" for row in rows),
        "not_run": sum(row["outcome"] == "not_run" for row in rows),
        "valid": len(valid), "success_denominator": first, "project_metrics_denominator": len(valid),
        "success_rate": len(valid) / first,
        "full_matches": sum(row["full_match"] for row in valid),
        "project_mae": sum(row["project_absolute_error"] for row in valid) / len(valid) if valid else None,
        "cost": {"amount": None, "basis": "unknown_without_billing_or_verified_prices"},
        "cases": rows,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="enable billable requests for the frozen synthetic PDFs")
    args = parser.parse_args(argv)
    try:
        cases, manifest_sha = load_cases()
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Never print operating-system error details, which may contain local paths.
        detail = str(error) if type(error) is ValueError else "manifest or fixture unreadable"
        print(f"PDF evaluation preflight failed: {detail}", file=sys.stderr)
        return 2

    first = sum(case.model_input is not None for case in cases)
    print(f"Cases={len(cases)}, first attempts={first}, retries<={RETRY_BUDGET}, "
          f"requests<={first + RETRY_BUDGET}, manifest SHA-256={manifest_sha}.")
    for case in cases:
        label = case.label
        print(f"{label['id']}: {label['guard_status']} / {label['jd_id'] or '-'} / "
              f"Mock {label['rule_total']}, {'model' if case.model_input else 'skip'}.")
    if not args.live:
        print("Dry run: 0 network requests.", flush=True)
        return 0

    try:
        config = DeepSeekConfig.from_env_file(ROOT / ".env")
    except DeepSeekError as error:
        print(f"DeepSeek evaluation setup failed: {error.code}", file=sys.stderr)
        return 2

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = REPORT_DIR / f"live-pdf-{timestamp}.json"
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as report_file:
            client = DeepSeekClient(config, max_requests=first + RETRY_BUDGET, prompt_version=PROMPT_VERSION_V2)
            report = run_live(cases, manifest_sha, client, config.model)
            json.dump(report, report_file, ensure_ascii=False, indent=2)
            report_file.write("\n")
    except OSError:
        print("Cannot write the PDF evaluation report.", file=sys.stderr)
        return 2
    print(f"Valid={report['valid']}/{first}, skipped={report['skipped']}, failed={report['failed']}, "
          f"not run={report['not_run']}, requests={report['requests']}; report={output.name}.")
    return int(report["valid"] != first or report["skipped"] != report["expected_skips"])


if __name__ == "__main__":
    raise SystemExit(main())
