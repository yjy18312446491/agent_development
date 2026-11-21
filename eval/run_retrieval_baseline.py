"""Preview an independently reviewed baseline; explicit live requires a new budget approval."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

from app.embedding_client import (
    DIMENSION, MAX_TOKENS, MODEL, EmbeddingClient, EmbeddingConfig, EmbeddingError,
    text_sha, validate_texts,
)
from app.retrieval import retrieve_job_evidence
from eval.run_retrieval_eval import evaluate, load_dataset, recall_at_k


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "eval/reports"
ENV_FILE = ROOT / ".env"
LOCK_NAME = "retrieval-relevance-v1-live.lock"
PRICE = Decimal("0.0000005")
BUDGET = Decimal("0.10")
MAX_HTTP = 3
NOTE = "Keep both experiment locks and reports; do not rerun after failure or interruption."


def batches(dataset):
    # evaluate verifies the in-memory snapshot, not just stored SHA attributes.
    evaluate(dataset)
    documents = tuple(job.raw_text.strip() for job in dataset.jobs)
    queries = tuple(case.query for case in dataset.cases)
    result = (("document", documents), ("query", queries[:10]), ("query", queries[10:]))
    for kind, texts in result:
        validate_texts(texts, kind)
    if sum(len(texts) for _, texts in result) != 15:
        raise ValueError("BASELINE_INPUT_COUNT")
    return result


def preview(dataset):
    groups = batches(dataset)
    report = evaluate(dataset)
    report.update(
        mode="baseline-preview", status="NOT_STARTED", counts_final=True,
        model=MODEL, dimension=DIMENSION, region="cn-beijing", unique_vectors=15,
        max_http_attempts=MAX_HTTP, retry_budget=0, budget_cny=float(BUDGET),
        planned_reservation_cny=float(15 * MAX_TOKENS * PRICE),
        batches=[{"text_type": kind, "text_sha256": [text_sha(t) for t in texts]}
                 for kind, texts in groups],
        total_tokens=0, estimated_cny=0, reserved_cny=0, attempts=[],
        note=NOTE,
    )
    report["limitations"] = [
        "Labels were independently reviewed; no real baseline has run in preview mode.",
        "Three jobs make Recall@3 saturated; there is no quality pass threshold.",
        "No rejection threshold tuning, product wiring or vector persistence.",
    ]
    return report


def destination(output, live):
    prefix = "retrieval-baseline-live-" if live else "retrieval-baseline-preview-"
    path = Path(output) if output else REPORT_DIR / (prefix + uuid4().hex + ".json")
    if (path.resolve().parent != REPORT_DIR.resolve() or path.suffix != ".json"
            or not path.name.startswith(prefix) or path.is_symlink()):
        raise ValueError("BASELINE_REPORT_PATH")
    return path


def _save(report, path, *, initial=False):
    if initial:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".retrieval-baseline-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _lock(report, path):
    with (REPORT_DIR / LOCK_NAME).open("x", encoding="utf-8") as handle:
        json.dump({"run_id": report["run_id"], "report": path.name,
                   "dataset_sha256": report["dataset_sha256"],
                   "evaluation_input_sha256": report["evaluation_input_sha256"],
                   "state": "LOCKED_BEFORE_HTTP", "max_http_attempts": MAX_HTTP,
                   "retry_budget": 0, "budget_cny": float(BUDGET)}, handle)
        handle.flush()
        os.fsync(handle.fileno())


def _source_commit():
    subprocess.run(["git", "cat-file", "-e", "HEAD:eval/run_retrieval_baseline.py"], cwd=ROOT,
                   capture_output=True, check=True, timeout=3)
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "app", "eval", "data/synthetic/retrieval",
                    "data/synthetic/jobs"], cwd=ROOT, capture_output=True, check=True, timeout=3)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, check=True, timeout=3)
    return result.stdout.decode("ascii").strip()


def _rank(dataset, vectors, report, failure):
    # SHA/role lookups only: sorting can never trigger another HTTP request.
    def lookup(texts):
        return tuple(vectors[("query" if i == 0 else "document", text_sha(t))]
                     for i, t in enumerate(texts))

    rows, sums = [], {1: 0.0, 3: 0.0}
    for case, original in zip(dataset.cases, evaluate(dataset)["cases"]):
        row = dict(original)
        if failure:
            row.update(status="NOT_RUN", reason_code=failure)
        else:
            result = retrieve_job_evidence(case.query, dataset.jobs, enabled=True, embedder=lookup)
            ranking = [hit.reference_jd_id for hit in result.hits]
            row.update(status=result.status, reason_code=result.reason_code,
                       rankings=[{"reference_jd_id": h.reference_jd_id, "similarity": h.similarity}
                                 for h in result.hits])
            if result.status == "OK":
                grades = dict(zip((j.jd_id for j in dataset.jobs), case.grades))
                for k in sums:
                    value = recall_at_k(grades, ranking, k)
                    row["recall_at_%d" % k] = value
                    sums[k] += value or 0.0
        rows.append(row)
    failed = sum(r["status"] != "OK" for r in rows)
    report.update(cases=rows, failed_case_count=failed,
                  metrics={"recall_at_%d" % k: sums[k] / report["relevant_case_count"] for k in sums},
                  metrics_status="INCOMPLETE_BASELINE" if failure or failed else "DESCRIPTIVE_BASELINE")


def _execute(dataset, client, report, path):
    reserved = Decimal("0")
    vectors = {}
    failure = None
    interrupted = False
    try:
        if client.requests != 0:
            raise ValueError("BASELINE_CLIENT_REUSED")
        for kind, texts in batches(dataset):
            evaluate(dataset)  # Refuse snapshot changes before each next batch.
            reservation = len(texts) * MAX_TOKENS * PRICE
            if len(report["attempts"]) >= MAX_HTTP or client.requests >= MAX_HTTP:
                raise ValueError("BASELINE_REQUEST_LIMIT")
            if reserved + reservation > BUDGET:
                raise ValueError("BASELINE_BUDGET_LIMIT")
            reserved += reservation
            attempt = {"text_type": kind, "text_sha256": [text_sha(t) for t in texts],
                       "code": "IN_FLIGHT", "total_tokens": None, "estimated_cny": None}
            report["attempts"].append(attempt)
            report.update(reserved_cny=float(reserved), counts_final=False)
            _save(report, path)  # Durable intent before sending, including cost reservation.
            try:
                batch = client.embed(texts, kind)  # Exactly one attempt; never retry.
            except EmbeddingError as error:
                attempt.update(code=error.code, http_status=error.status,
                               total_tokens=error.total_tokens, latency_ms=error.latency_ms,
                               estimated_cny=None if error.total_tokens is None
                               else float(error.total_tokens * PRICE))
                failure = error.code
                break
            attempt.update(code="OK", http_status=batch.http_status, total_tokens=batch.total_tokens,
                           latency_ms=batch.latency_ms, estimated_cny=batch.estimated_cny)
            vectors.update(((kind, digest), vector) for digest, vector in zip(batch.text_sha256, batch.vectors))
            _save(report, path)
    except (KeyboardInterrupt, SystemExit):
        failure, interrupted = "INTERRUPTED", True
    except Exception:
        failure = "BASELINE_RUN_FAILED"
    count = client.requests
    attempts = report["attempts"]
    known = count == len(attempts) and all(a["total_tokens"] is not None for a in attempts)
    report.update(http_attempts=count, real_http_requests=count, counts_final=True,
                  total_tokens=sum(a["total_tokens"] for a in attempts) if known else None,
                  estimated_cny=sum(a["estimated_cny"] for a in attempts) if known else None,
                  finished_at=datetime.now(timezone.utc).isoformat(), mode="live-baseline", provider="dashscope")
    report["limitations"] = [
        "Descriptive results on twelve synthetic queries, not a quality approval or hiring decision.",
        "Three jobs saturate Recall@3; unrelated queries have no rejection threshold.",
        "Failed prefetch makes all queries NOT_RUN; relevant failures remain zero in macro recall.",
        "No product wiring or persistent vectors; cost is estimated, not verified against billing.",
    ]
    try:
        _rank(dataset, vectors, report, failure)
    except Exception:
        failure = failure or "BASELINE_SNAPSHOT_CHANGED"
        # Retain preview rows with undefined metrics rather than evaluate changed inputs.
        report.update(metrics_status="INVALID_SNAPSHOT", metrics={"recall_at_1": None, "recall_at_3": None})
    if not failure and report["failed_case_count"]:
        failure = "BASELINE_RETRIEVAL_FAILED"
    report.update(status="INTERRUPTED" if interrupted else "FAILED" if failure else "SUCCESS",
                  failure_code=failure, quality_conclusion="NOT_ESTABLISHED")
    _save(report, path)
    return 130 if interrupted else 1 if failure else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Only after approval of this new round and budget")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    path = report = None
    owned_report = False
    try:
        path = destination(args.output, args.live)
        if path.exists():
            raise FileExistsError()
        dataset = load_dataset()
        report = preview(dataset)
        if args.live:
            # Refuse before reading credentials when this round already has a lock.
            if (REPORT_DIR / LOCK_NAME).exists():
                raise FileExistsError()
            config = EmbeddingConfig.from_env_file(ENV_FILE)
            report.update(mode="live-baseline", status="ARMED", counts_final=False,
                          run_id=uuid4().hex, started_at=datetime.now(timezone.utc).isoformat(),
                          source_commit=_source_commit(), total_tokens=None, estimated_cny=None,
                          metrics_status="NOT_STARTED")
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        _save(report, path, initial=True)
        owned_report = True
        if args.live:
            _lock(report, path)
            client = EmbeddingClient(config)
            result = _execute(dataset, client, report, path)
        else:
            result = 0
    except (Exception, KeyboardInterrupt, SystemExit):
        # Do not replace evidence owned by another invocation or remove any lock.
        if owned_report:
            report.update(status="FAILED", failure_code="BASELINE_CONTROL_FAILED")
            try:
                _save(report, path)
            except (OSError, ValueError):
                pass
        print("Baseline stopped; inspect the retained report and locks. " + NOTE, file=sys.stderr)
        return 2
    print("Baseline %s; %d HTTP attempts. %s" % (report["status"], report["http_attempts"], NOTE))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
