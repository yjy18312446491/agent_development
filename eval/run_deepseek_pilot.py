"""Run the DeepSeek smoke test or full synthetic evaluation when explicitly enabled."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Optional, Sequence

from app.deepseek_client import DeepSeekClient, DeepSeekConfig, DeepSeekError, PROMPT_VERSION, TEMPERATURE
from app.project_scorer import DIMENSIONS, POINT_LEVELS, InvalidProjectResponse, score_project
from app.route import load_jobs


ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "eval" / "project_score_cases.jsonl"
REPORT = ROOT / "eval" / "reports" / "deepseek-smoke.json"
FULL_REPORT = REPORT.with_name("deepseek-all.json")
SMOKE_IDS = ("full_30", "tools_only", "jailbreak_tools")
MAX_REQUESTS = 4
FULL_MAX_REQUESTS = 13
STOP_STATUSES = (400, 401, 402, 403, 404)
INSTRUCTION_PAIRS = (("tools_only", "jailbreak_tools"), ("full_30", "full_with_please_fullmark"))


def _skip_client(case):
    return case.get("skip_client", False) or not case["snippet"].strip()


def _run_case(case, job, client):
    expected = {name: case[name] for name in DIMENSIONS}
    entry = {"case_id": case["case_id"], "job_id": job.jd_id, "expected": expected,
             "expected_total": case["total"]}
    if _skip_client(case):
        entry.update(status="SKIPPED", skip_client=True, actual=dict.fromkeys(DIMENSIONS, 0),
                     actual_total=0, requests=0, latency_ms=0)
        return entry

    completion = None

    def request(snippet, requirements):
        nonlocal completion
        completion = client.complete(snippet, requirements)
        return completion.text

    start = perf_counter()
    before = client.requests
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
    entry["requests"] = client.requests - before
    if completion is not None:
        entry.update(response_model=completion.model, system_fingerprint=completion.fingerprint,
                     input_tokens=completion.input_tokens, output_tokens=completion.output_tokens)
    return entry


def _instruction_deltas(valid):
    by_id = {entry["case_id"]: entry for entry in valid}
    pairs = []
    for plain_id, instructed_id in INSTRUCTION_PAIRS:
        plain, instructed = by_id.get(plain_id), by_id.get(instructed_id)
        difference = None
        if plain is not None and instructed is not None:
            difference = {
                name: instructed["actual"][name] - plain["actual"][name] for name in DIMENSIONS
            }
            difference["total"] = instructed["actual_total"] - plain["actual_total"]
        pairs.append({"plain": plain_id, "instructed": instructed_id, "delta": difference})
    return pairs


def run_pilot(cases, jobs, client, model, label_hash, *, full=False):
    request_limit = FULL_MAX_REQUESTS if full else MAX_REQUESTS
    requested_nonempty = sum(not _skip_client(case) for case in cases)
    report = {
        "provider": "deepseek", "model": model, "prompt_version": PROMPT_VERSION,
        "temperature": TEMPERATURE, "thinking": "disabled", "labels_sha256": label_hash,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "full" if full else "smoke",
        "requested_cases": [case["case_id"] for case in cases], "request_limit": request_limit,
        "requested_nonempty": requested_nonempty, "cases": [], "not_run": [], "aborted": False,
    }
    later_first = requested_nonempty
    for index, case in enumerate(cases):
        if full and not _skip_client(case):
            later_first -= 1
            # If another request would consume a later first attempt, the client stops.
            client.max_requests = min(client.requests + 2, request_limit - later_first)
        entry = _run_case(case, jobs[case["jd_id"]], client)
        report["cases"].append(entry)
        if entry.get("http_status") in STOP_STATUSES or entry.get("failure") == "REQUEST_LIMIT":
            report["aborted"] = True
            report["not_run"] = [remaining["case_id"] for remaining in cases[index + 1:]]
            break

    valid = [entry for entry in report["cases"] if entry["status"] == "SCORED"]
    accounted = [entry for entry in report["cases"] if entry.get("input_tokens") is not None
                 and entry.get("output_tokens") is not None]
    input_tokens = sum(entry["input_tokens"] for entry in accounted)
    output_tokens = sum(entry["output_tokens"] for entry in accounted)
    report.update(
        requests=client.requests, valid_responses=len(valid),
        retries=sum(max(entry["requests"] - 1, 0) for entry in report["cases"]),
        metrics_denominator=len(valid),
        skipped_local=sum(entry["status"] == "SKIPPED" for entry in report["cases"]),
        invalid_responses=sum(entry["status"] == "REVIEW" for entry in report["cases"]),
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
    if full:
        report["instruction_pairs"] = _instruction_deltas(valid)
    return report


def _load_cases(full):
    content = LABELS.read_bytes()
    labeled = {}
    for line in content.decode("utf-8").splitlines():
        case = json.loads(line)
        case_id, snippet, skip = case["case_id"], case["snippet"], case["skip_client"]
        points = [case[name] for name in DIMENSIONS]
        if (
            not isinstance(case_id, str) or not case_id or case_id in labeled
            or not isinstance(snippet, str) or type(skip) is not bool or skip != (not snippet.strip())
            or any(type(value) is not int or value not in POINT_LEVELS for value in points)
            or type(case["total"]) is not int or case["total"] != sum(points)
            or (skip and case["total"] != 0)
        ):
            raise ValueError("Invalid labeled case")
        labeled[case_id] = case
    if full:
        if len(labeled) != 12 or sum(_skip_client(case) for case in labeled.values()) != 1:
            raise ValueError("The full suite requires 12 cases with one local skip")
        selected = list(labeled.values())
    else:
        selected = [labeled[case_id] for case_id in SMOKE_IDS]
        if any(_skip_client(case) for case in selected):
            raise ValueError("Smoke cases must all call the client")
    return selected, sha256(content).hexdigest()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="enable billable requests")
    parser.add_argument("--all", action="store_true", help="select all 12 synthetic cases")
    args = parser.parse_args(argv)
    if not args.live and not args.all:
        print("Dry run, zero network requests. With --live: " + ", ".join(SMOKE_IDS))
        return 0

    try:
        cases, label_hash = _load_cases(args.all)
        if not args.live:
            print("Dry run, zero network requests. Full suite: " + ", ".join(case["case_id"] for case in cases))
            return 0
        config = DeepSeekConfig.from_env_file(ROOT / ".env")
        jobs = {job.jd_id: job for job in load_jobs()}
        if any(case["jd_id"] not in jobs for case in cases):
            raise ValueError("Unknown job ID")
        output = FULL_REPORT if args.all else REPORT
        output.parent.mkdir(parents=True, exist_ok=True)
    except DeepSeekError as exc:
        print(f"DeepSeek pilot setup failed: {exc.code}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print("Cannot prepare the synthetic pilot or report directory.", file=sys.stderr)
        return 2

    client = DeepSeekClient(config, max_requests=FULL_MAX_REQUESTS if args.all else MAX_REQUESTS)
    report = run_pilot(cases, jobs, client, config.model, label_hash, full=args.all)
    try:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        print("Cannot write the DeepSeek pilot report.", file=sys.stderr)
        return 2
    if args.all:
        print(f"DeepSeek evaluation: {report['valid_responses']}/{report['requested_nonempty']} valid model "
              f"responses, {report['exact_matches']} exact matches, MAE={report['project_mae']}, "
              f"{report['skipped_local']} local skip, {report['requests']} requests.")
        return 1 if report["aborted"] else 0
    print(f"DeepSeek pilot: {report['valid_responses']}/{len(cases)} valid, "
          f"{report['exact_matches']}/{len(cases)} match labels, {report['requests']} requests.")
    return 0 if report["exact_matches"] == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
