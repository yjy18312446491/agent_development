"""Retrieval quality v2: extra eval-only jobs, offline fake vectors, never HTTP."""

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
from eval.run_retrieval_eval import JOB_FILE_SHAS, JOB_TEXT_SHAS as SCREEN_JOB_TEXT_SHAS, recall_at_k


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/synthetic/retrieval/relevance_v2.json"
SCREEN_JOBS = ROOT / "data/synthetic/jobs/jobs.json"
REPORT_DIR = ROOT / "eval/reports"
DATASET_SHA = "b190eca60d6ded1ab0862df4bd30dc55134c9ad15fabdc40aec59e50ecb3dda5"
DATASET_ID = "retrieval-relevance-v2"
ANNOTATION_STATUS = "draft_pending_independent_review"
V1_DATASET = ROOT / "data/synthetic/retrieval/relevance_v1.json"
V1_DATASET_SHA = "704ff394272ce4b9b3b4cf25b59440d82ea44ffec3d738d965a918377cdfc04b"
HELDOUT_DATASET = ROOT / "data/synthetic/retrieval/relevance_heldout_v1.json"
HELDOUT_DATASET_SHA = "e1d0b0beb43ee08daafe5721f2de1546839e3b6cddb531847d20590378fc9529"
JOB_IDS = ("JD1", "JD2", "JD3", "JD4", "JD5", "JD6")
JOB_TEXT_SHAS = (
    "416e53ca42f9f4b9c703c4d9b9946923aa5822e70583d271e3ba74d386c06c94",
    "d980df845a13eaf5524f2f71da94160d6fbb8a3f2240794bba16ff3e0e1ca6ab",
    "3a5ae0174e8d15f3f7a3dd5dd2cc7129420298f95b8828be2591345eb93e343c",
    "0edce0e1da7e4b951c4408d472888846f50f4d0356ba072ebfee2c57d90c075b",
    "8842dd336266d5072f305925f39b47f6709fa750e2e102d5225db293cb3a6a64",
    "8fa4d88876109daf72000f9cc77758c51cf3a72d4d282956bd0c12b315c3493d",
)
RELEVANCE_THRESHOLD = 2
CASE_COUNT = 18
RELEVANT_COUNT = 12


@dataclass(frozen=True)
class QualityCase:
    case_id: str
    coverage_group: str
    query: str = field(repr=False)
    grades: Tuple[int, ...]
    rationales: Tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class QualityDataset:
    jobs: Tuple[JobPosting, ...]
    cases: Tuple[QualityCase, ...]
    canonical_sha256: str
    file_sha256: str
    screen_jobs_sha256: str
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


def _canonical_queries(path: Path, expected_sha: str) -> set:
    raw = json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_unique_object,
                     parse_constant=_invalid_constant)
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if sha256(canonical).hexdigest() != expected_sha:
        raise ValueError()
    return {row["query"] for row in raw["cases"]}


def _parse_jobs(raw_jobs) -> Tuple[JobPosting, ...]:
    if not isinstance(raw_jobs, list) or len(raw_jobs) != 6:
        raise ValueError()
    jobs = []
    for index, row in enumerate(raw_jobs):
        if not isinstance(row, dict) or set(row) != {
            "jd_id", "title", "min_years", "max_years", "skills", "bonus_skills", "raw_text",
        }:
            raise ValueError()
        if row["jd_id"] != JOB_IDS[index] or not _nonempty(row["title"]) or not _nonempty(row["raw_text"]):
            raise ValueError()
        if type(row["min_years"]) is not float:
            raise ValueError()
        if row["max_years"] is not None and type(row["max_years"]) is not float:
            raise ValueError()
        if not isinstance(row["skills"], list) or not row["skills"] or not all(map(_nonempty, row["skills"])):
            raise ValueError()
        if not isinstance(row["bonus_skills"], list) or not all(map(_nonempty, row["bonus_skills"])):
            raise ValueError()
        text = row["raw_text"].strip()
        if _text_sha(text) != JOB_TEXT_SHAS[index]:
            raise ValueError()
        jobs.append(JobPosting(
            row["jd_id"], row["title"], row["min_years"], list(row["skills"]),
            row["max_years"], text, list(row["bonus_skills"]),
        ))
    return tuple(jobs)


def _parse_cases(raw: dict) -> Tuple[QualityCase, ...]:
    expected = {"schema_version", "dataset_id", "annotation_status", "annotation_source",
                "relevance_threshold", "rubric", "jobs", "cases"}
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
    rows = raw["cases"]
    if not isinstance(rows, list) or len(rows) != CASE_COUNT:
        raise ValueError()
    cases, seen_queries = [], set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != {"id", "coverage_group", "query", "relevance", "rationale"}:
            raise ValueError()
        if row["id"] != "QV%03d" % index or row["coverage_group"] not in (*JOB_IDS, "none"):
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
        cases.append(QualityCase(row["id"], group, text, tuple(grades[j] for j in JOB_IDS),
                                 tuple(reasons[j] for j in JOB_IDS)))
    expected_groups = Counter({job: 2 for job in JOB_IDS})
    expected_groups["none"] = 6
    if Counter(c.coverage_group for c in cases) != expected_groups:
        raise ValueError()
    return tuple(cases)


def load_dataset(path: Path = DATASET, jobs_file: Path = SCREEN_JOBS,
                 v1_path: Path = V1_DATASET, heldout_path: Path = HELDOUT_DATASET) -> QualityDataset:
    """Load v2. Refuse if screening jobs, v1, or held-out gold moved, or queries overlap."""
    try:
        contents = path.read_bytes()
        raw = json.loads(contents.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        jobs = _parse_jobs(raw["jobs"])
        cases = _parse_cases(raw)
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = sha256(canonical).hexdigest()
        if digest != DATASET_SHA:
            raise ValueError()
        frozen_queries = _canonical_queries(v1_path, V1_DATASET_SHA) | _canonical_queries(
            heldout_path, HELDOUT_DATASET_SHA)
        if any(case.query in frozen_queries for case in cases):
            raise ValueError()
        job_bytes = jobs_file.read_bytes()
        job_sha = sha256(job_bytes).hexdigest()
        if jobs_file.name != "jobs.json" or job_sha not in JOB_FILE_SHAS:
            raise ValueError()
        screen_jobs = tuple(load_jobs(jobs_file.parent))
        if tuple(j.jd_id for j in screen_jobs) != ("JD1", "JD2", "JD3"):
            raise ValueError()
        if tuple(_text_sha(j.raw_text.strip()) for j in screen_jobs) != SCREEN_JOB_TEXT_SHAS:
            raise ValueError()
        if tuple(_text_sha(j.raw_text.strip()) for j in jobs[:3]) != SCREEN_JOB_TEXT_SHAS:
            raise ValueError()
        if jobs_file.read_bytes() != job_bytes:
            raise ValueError()
        if any(c.query == j.raw_text.strip() for c in cases for j in jobs):
            raise ValueError()
        return QualityDataset(jobs, cases, digest, sha256(contents).hexdigest(), job_sha,
                              _input_fingerprint(jobs, cases))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise ValueError("V2_DATASET_INVALID") from None


# 6-D controls, not a model and not derived from relevance labels.
# QV002/QV004/QV010 deliberately put a different job first.
CONTROL_JOB_VECTORS = {
    "JD1": (1, 0, 0, 0, 0, 0), "JD2": (0, 1, 0, 0, 0, 0), "JD3": (0, 0, 1, 0, 0, 0),
    "JD4": (0, 0, 0, 1, 0, 0), "JD5": (0, 0, 0, 0, 1, 0), "JD6": (0, 0, 0, 0, 0, 1),
}
CONTROL_QUERY_VECTORS = {
    "QV001": (3, 2, 0, 0, 1, 0), "QV002": (2, 3, 0, 0, 1, 0), "QV003": (1, 3, 0, 0, 1, 0),
    "QV004": (3, 2, 0, 0, 1, 0), "QV005": (0, 0, 3, 0, 0, 0), "QV006": (0, 0, 3, 0, 0, 1),
    "QV007": (0, 0, 0, 3, 0, 1), "QV008": (0, 0, 0, 3, 0, 2), "QV009": (1, 1, 0, 0, 3, 0),
    "QV010": (1, 0, 0, 3, 2, 0), "QV011": (0, 0, 0, 1, 0, 3), "QV012": (0, 0, 0, 1, 0, 3),
    "QV013": (1, 1, 1, 1, 1, 1), "QV014": (-1, -2, -3, -1, -2, -3),
    "QV015": (0, 1, 0, 0, 0, 0), "QV016": (0, 0, 0, 0, 0, 1),
    "QV017": (1, 0, 0, 0, 0, 0), "QV018": (0, 0, 1, 0, 0, 0),
}


class FakeVectors:
    def __init__(self, dataset: QualityDataset):
        self.calls = 0
        self._vectors = {j.raw_text.strip(): CONTROL_JOB_VECTORS[j.jd_id] for j in dataset.jobs}
        self._vectors.update({c.query: CONTROL_QUERY_VECTORS[c.case_id] for c in dataset.cases})

    def __call__(self, texts):
        self.calls += 1
        try:
            return tuple(self._vectors[text] for text in texts)
        except KeyError:
            raise ValueError("FAKE_VECTOR_MISSING") from None


def evaluate(dataset: QualityDataset, *, enabled: bool = False) -> dict:
    if type(enabled) is not bool:
        raise ValueError("V2_EVAL_FLAG")
    if (dataset.canonical_sha256 != DATASET_SHA
            or _input_fingerprint(dataset.jobs, dataset.cases) != dataset.input_fingerprint):
        raise ValueError("V2_SNAPSHOT_CHANGED")
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
    if relevant_count != RELEVANT_COUNT:
        raise ValueError("V2_RELEVANT_COUNT")
    metrics = {"recall_at_%d" % k: recall_sums[k] / relevant_count if enabled and relevant_count else None
               for k in (1, 3)}
    return {
        "mode": "fixed-fake-vectors" if enabled else "disabled-preview",
        "provider": "fixed-6d-controls" if enabled else None,
        "quality_conclusion": "NOT_ESTABLISHED",
        "annotation_status": ANNOTATION_STATUS,
        "dataset_id": DATASET_ID,
        "annotation_review_status": "PENDING_INDEPENDENT_REVIEW",
        "evaluation_input_sha256": dataset.input_fingerprint,
        "dataset_sha256": dataset.canonical_sha256,
        "dataset_file_sha256": dataset.file_sha256,
        "screen_jobs_sha256": dataset.screen_jobs_sha256,
        "v1_dataset_sha256": V1_DATASET_SHA,
        "heldout_dataset_sha256": HELDOUT_DATASET_SHA,
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
            "V2 labels are draft and pending independent review.",
            "Fake metrics only test the metric program; they are not model quality.",
            "Do not fit a similarity threshold on v1, held-out, or this set.",
            "Six eval-only jobs are not loaded by screen_one or Docker.",
            "No live embedding until an explicit later command.",
        ],
    }


def report_destination(output=None) -> Path:
    path = Path(output) if output is not None else REPORT_DIR / (
        "retrieval-v2-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8] + ".json")
    if (path.resolve().parent != REPORT_DIR.resolve() or path.suffix != ".json"
            or not path.name.startswith("retrieval-v2-")):
        raise ValueError("V2_REPORT_PATH")
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake", action="store_true", help="Run fixed artificial vectors; never a real model")
    parser.add_argument("--live", action="store_true", help="Rejected here; use the separate live entry after approval")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.live:
        print("V2 offline entry rejects --live; do not reuse old locks.", file=sys.stderr)
        return 2
    try:
        destination = report_destination(args.output)
        if destination.exists():
            raise ValueError("V2_REPORT_EXISTS")
        dataset = load_dataset()
        report = evaluate(dataset, enabled=args.fake)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    except (OSError, ValueError):
        print("V2 retrieval evaluation failed; check frozen data or report path.", file=sys.stderr)
        return 2
    print("V2 %s: %d cases, %d failures; 0 real HTTP requests. Quality NOT_ESTABLISHED." %
          (report["mode"], report["case_count"], report["failed_case_count"]))
    return 1 if report["failed_case_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
