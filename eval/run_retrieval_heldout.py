"""Held-out relevance labels: offline fake vectors only, never HTTP or v1 retuning."""

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Optional, Sequence, Tuple
from uuid import uuid4

from app.retrieval import retrieve_job_evidence
from app.route import JobPosting, load_jobs
from eval.run_retrieval_eval import (
    JOB_FILE_SHAS,
    JOB_IDS,
    JOB_TEXT_SHAS,
    RELEVANCE_THRESHOLD,
    recall_at_k,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/synthetic/retrieval/relevance_heldout_v1.json"
JOBS = ROOT / "data/synthetic/jobs/jobs.json"
REPORT_DIR = ROOT / "eval/reports"
DATASET_SHA = "e1d0b0beb43ee08daafe5721f2de1546839e3b6cddb531847d20590378fc9529"
DATASET_ID = "retrieval-relevance-heldout-v1"
ANNOTATION_STATUS = "draft_pending_independent_review"
V1_DATASET = ROOT / "data/synthetic/retrieval/relevance_v1.json"
V1_DATASET_SHA = "704ff394272ce4b9b3b4cf25b59440d82ea44ffec3d738d965a918377cdfc04b"


@dataclass(frozen=True)
class HeldoutCase:
    case_id: str
    coverage_group: str
    query: str = field(repr=False)
    grades: Tuple[int, ...]
    rationales: Tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class HeldoutDataset:
    jobs: Tuple[JobPosting, ...]
    cases: Tuple[HeldoutCase, ...]
    canonical_sha256: str
    file_sha256: str
    jobs_sha256: str
    input_fingerprint: str


def _text_sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _input_fingerprint(jobs, cases) -> str:
    content = {"jobs": [asdict(job) for job in jobs], "cases": [asdict(case) for case in cases]}
    return sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_KEY")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("NON_FINITE_JSON")


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _parse_cases(raw: dict) -> Tuple[HeldoutCase, ...]:
    expected = {"schema_version", "dataset_id", "annotation_status", "annotation_source",
                "relevance_threshold", "rubric", "job_text_sha256", "cases"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError()
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError()
    if raw["dataset_id"] != DATASET_ID or raw["annotation_status"] != ANNOTATION_STATUS:
        raise ValueError()
    if not _nonempty(raw["annotation_source"]):
        raise ValueError()
    if type(raw["relevance_threshold"]) is not int or raw["relevance_threshold"] != RELEVANCE_THRESHOLD:
        raise ValueError()
    rubric = raw["rubric"]
    if not isinstance(rubric, dict) or set(rubric) != {"0", "1", "2"} or not all(map(_nonempty, rubric.values())):
        raise ValueError()
    if raw["job_text_sha256"] != dict(zip(JOB_IDS, JOB_TEXT_SHAS)):
        raise ValueError()
    rows = raw["cases"]
    if not isinstance(rows, list) or len(rows) != 12:
        raise ValueError()
    cases, seen_queries = [], set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != {"id", "coverage_group", "query", "relevance", "rationale"}:
            raise ValueError()
        if row["id"] != "HV%03d" % index or row["coverage_group"] not in (*JOB_IDS, "none"):
            raise ValueError()
        text = row["query"]
        if not _nonempty(text) or text != text.strip() or len(text.encode("utf-8")) > 2048 or text in seen_queries:
            raise ValueError()
        seen_queries.add(text)
        grades, reasons = row["relevance"], row["rationale"]
        if not isinstance(grades, dict) or set(grades) != set(JOB_IDS):
            raise ValueError()
        if any(type(value) is not int or value not in (0, 1, 2) for value in grades.values()):
            raise ValueError()
        if not isinstance(reasons, dict) or set(reasons) != set(JOB_IDS) or not all(map(_nonempty, reasons.values())):
            raise ValueError()
        group = row["coverage_group"]
        if (group == "none" and any(grades.values())) or (group != "none" and grades[group] != 2):
            raise ValueError()
        cases.append(HeldoutCase(row["id"], group, text, tuple(grades[j] for j in JOB_IDS),
                                 tuple(reasons[j] for j in JOB_IDS)))
    if Counter(c.coverage_group for c in cases) != Counter({"JD1": 3, "JD2": 3, "JD3": 3, "none": 3}):
        raise ValueError()
    return tuple(cases)


def load_dataset(path: Path = DATASET, jobs_file: Path = JOBS,
                 v1_path: Path = V1_DATASET) -> HeldoutDataset:
    """Load the held-out draft. Refuse if v1 gold SHA moved or queries overlap."""
    try:
        contents = path.read_bytes()
        raw = json.loads(contents.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        cases = _parse_cases(raw)
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = sha256(canonical).hexdigest()
        if digest != DATASET_SHA:
            raise ValueError()
        v1_raw = json.loads(v1_path.read_bytes().decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=_invalid_constant)
        v1_canonical = json.dumps(v1_raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if sha256(v1_canonical).hexdigest() != V1_DATASET_SHA:
            raise ValueError()
        v1_queries = {row["query"] for row in v1_raw["cases"]}
        if any(case.query in v1_queries for case in cases):
            raise ValueError()
        job_bytes = jobs_file.read_bytes()
        job_sha = sha256(job_bytes).hexdigest()
        if jobs_file.name != "jobs.json" or job_sha not in JOB_FILE_SHAS:
            raise ValueError()
        jobs = tuple(load_jobs(jobs_file.parent))
        if tuple(j.jd_id for j in jobs) != JOB_IDS or tuple(_text_sha(j.raw_text.strip()) for j in jobs) != JOB_TEXT_SHAS:
            raise ValueError()
        if jobs_file.read_bytes() != job_bytes:
            raise ValueError()
        if any(c.query == j.raw_text.strip() for c in cases for j in jobs):
            raise ValueError()
        return HeldoutDataset(jobs, cases, digest, sha256(contents).hexdigest(), job_sha,
                              _input_fingerprint(jobs, cases))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise ValueError("HELDOUT_DATASET_INVALID") from None


# Artificial 3-D controls, not a model and not derived from relevance labels.
# HV002/HV005/HV008 deliberately put a different job first.
CONTROL_QUERY_VECTORS = {
    "HV001": (3, 2, 1), "HV002": (2, 3, 1), "HV003": (3, 1, 2),
    "HV004": (2, 3, 1), "HV005": (3, 2, 1), "HV006": (1, 3, 2),
    "HV007": (1, 2, 3), "HV008": (3, 2, 1), "HV009": (2, 1, 3),
    "HV010": (1, 1, 1), "HV011": (-1, -2, -3), "HV012": (0, 1, 0),
}
CONTROL_JOB_VECTORS = {"JD1": (1, 0, 0), "JD2": (0, 1, 0), "JD3": (0, 0, 1)}


class FakeVectors:
    def __init__(self, dataset: HeldoutDataset):
        self.calls = 0
        self._vectors = {j.raw_text.strip(): CONTROL_JOB_VECTORS[j.jd_id] for j in dataset.jobs}
        self._vectors.update({c.query: CONTROL_QUERY_VECTORS[c.case_id] for c in dataset.cases})

    def __call__(self, texts):
        self.calls += 1
        try:
            return tuple(self._vectors[text] for text in texts)
        except KeyError:
            raise ValueError("FAKE_VECTOR_MISSING") from None


def evaluate(dataset: HeldoutDataset, *, enabled: bool = False) -> dict:
    if type(enabled) is not bool:
        raise ValueError("HELDOUT_EVAL_FLAG")
    if (dataset.canonical_sha256 != DATASET_SHA
            or _input_fingerprint(dataset.jobs, dataset.cases) != dataset.input_fingerprint):
        raise ValueError("HELDOUT_SNAPSHOT_CHANGED")
    fake = FakeVectors(dataset) if enabled else None
    rows = []
    relevant_count = failed_count = 0
    recall_sums = {1: 0.0, 3: 0.0}
    for case in dataset.cases:
        grades = dict(zip(JOB_IDS, case.grades))
        relevant = [key for key, grade in grades.items() if grade >= RELEVANCE_THRESHOLD]
        relevant_count += bool(relevant)
        result = retrieve_job_evidence(case.query, dataset.jobs, enabled=enabled, embedder=fake, top_k=3)
        ranking = [hit.reference_jd_id for hit in result.hits]
        recalls = {"recall_at_1": None, "recall_at_3": None}
        if result.status == "OK":
            for k in (1, 3):
                value = recall_at_k(grades, ranking, k)
                recalls["recall_at_%d" % k] = value
                recall_sums[k] += value or 0.0
        elif result.status == "REVIEW":
            failed_count += 1
        rows.append({"id": case.case_id, "query_sha256": _text_sha(case.query),
                     "coverage_group": case.coverage_group, "status": result.status,
                     "reason_code": result.reason_code, "relevant_reference_ids": relevant,
                     "rankings": [{"reference_jd_id": hit.reference_jd_id, "similarity": hit.similarity}
                                  for hit in result.hits], **recalls})
    metrics = {"recall_at_%d" % k: recall_sums[k] / relevant_count if enabled and relevant_count else None
               for k in (1, 3)}
    return {
        "mode": "fixed-fake-vectors" if enabled else "disabled-preview",
        "provider": "fixed-3d-controls" if enabled else None,
        "quality_conclusion": "NOT_ESTABLISHED",
        "annotation_status": ANNOTATION_STATUS,
        "dataset_id": DATASET_ID,
        "annotation_review_status": "PENDING_INDEPENDENT_REVIEW",
        "evaluation_input_sha256": dataset.input_fingerprint,
        "dataset_sha256": dataset.canonical_sha256,
        "dataset_file_sha256": dataset.file_sha256,
        "jobs_sha256": dataset.jobs_sha256,
        "v1_dataset_sha256": V1_DATASET_SHA,
        "relevance_threshold": RELEVANCE_THRESHOLD,
        "documents": [{"reference_jd_id": j.jd_id, "text_sha256": _text_sha(j.raw_text.strip())}
                      for j in dataset.jobs],
        "case_count": len(rows), "relevant_case_count": relevant_count,
        "no_relevant_case_count": len(rows) - relevant_count, "failed_case_count": failed_count,
        "metrics_status": "FAKE_CONTROL_ONLY" if enabled else "DISABLED",
        "failure_policy": "failed_relevant_queries_count_as_zero_in_macro_recall",
        "metrics": metrics, "cases": rows, "fake_embedder_calls": fake.calls if fake else 0,
        "http_attempts": 0, "real_http_requests": 0,
        "limitations": [
            "Held-out labels are draft and pending independent review.",
            "Fake metrics only test the metric program; they are not model quality.",
            "Do not fit a similarity threshold on v1 or this held-out set.",
            "Reject-match is undefined with three jobs and no threshold.",
            "No live embedding, routing, scoring or FastAPI wiring is added.",
        ],
    }


def report_destination(output=None) -> Path:
    path = Path(output) if output is not None else REPORT_DIR / (
        "retrieval-heldout-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8] + ".json")
    if (path.resolve().parent != REPORT_DIR.resolve() or path.suffix != ".json"
            or not path.name.startswith("retrieval-heldout-")):
        raise ValueError("HELDOUT_REPORT_PATH")
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake", action="store_true", help="Run fixed artificial vectors; never a real model")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        destination = report_destination(args.output)
        if destination.exists():
            raise ValueError("HELDOUT_REPORT_EXISTS")
        dataset = load_dataset()
        report = evaluate(dataset, enabled=args.fake)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    except (OSError, ValueError):
        print("Held-out retrieval evaluation failed; check frozen data or report path.", file=sys.stderr)
        return 2
    print("Held-out %s: %d cases, %d failures; 0 real HTTP requests. Quality NOT_ESTABLISHED." %
          (report["mode"], report["case_count"], report["failed_case_count"]))
    return 1 if report["failed_case_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
