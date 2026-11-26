"""Retrieval quality v4: deterministic seniority rerank over frozen rankings, offline, never HTTP.

The v1/held-out/v2 live replays share one top-1 error type: the relevant junior
job (JD2, 0-2.5 years) is ranked behind an experience-infeasible senior job by
both dense and lexical rankers. This experiment reorders candidates by a
deterministic years-feasibility signal extracted from the query text: jobs whose
min/max years conflict with the query's stated experience are demoted, order
inside each group is preserved. There is no tuned score weight, no threshold
fitting, no screening wiring and no HTTP; control vectors only exercise the
program and the live replay only reorders already-persisted top-3 rankings.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from typing import Mapping, Optional, Sequence, Tuple
from uuid import uuid4

from app.retrieval import retrieve_job_evidence
from app.route import JobPosting
from eval import run_retrieval_eval as v1
from eval import run_retrieval_heldout as heldout
from eval import run_retrieval_v2 as v2
from eval.run_retrieval_eval import recall_at_k


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "eval/reports"
RELEVANCE_THRESHOLD = 2
LIVE_REPORTS = {
    "v1": ROOT / "eval/reports/retrieval-baseline-live-20261121-round01.json",
    "heldout": ROOT / "eval/reports/retrieval-heldout-live-59dc7cfde9964edfae12361b37728e4f.json",
    "v2": ROOT / "eval/reports/retrieval-v2-live-26e43ac6fc1847cb9c13cd3fc292bdee.json",
}
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
YEARS_RE = re.compile(r"(半|[零一二两三四五六七八九十]+|\d+(?:\.\d+)?)年(半?)")
FRESH_GRAD_RE = re.compile(r"应届")


def _chinese_token_value(token: str) -> float:
    if token in _CN_DIGITS:
        return float(_CN_DIGITS[token])
    if token == "十":
        return 10.0
    if len(token) == 2 and token[0] == "十" and token[1] in _CN_DIGITS:
        return 10.0 + _CN_DIGITS[token[1]]
    if len(token) == 2 and token[0] in _CN_DIGITS and token[0] != "零" and token[1] == "十":
        return float(_CN_DIGITS[token[0]] * 10)
    if (len(token) == 3 and token[0] in _CN_DIGITS and token[0] != "零"
            and token[1] == "十" and token[2] in _CN_DIGITS):
        return float(_CN_DIGITS[token[0]] * 10 + _CN_DIGITS[token[2]])
    raise ValueError("V4_YEARS_TOKEN")


def extract_experience_years(text: str) -> Optional[float]:
    """Deterministic junior/senior signal: stated experience years, or None.

    应届 marks a fresh graduate (0.0). 半年 is 0.5; 一年半 is 1.5. Text without a
    numeral+年 pattern and without 应届 yields None (neutral, never reordered).
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("V4_YEARS_INPUT")
    if FRESH_GRAD_RE.search(text):
        return 0.0
    match = YEARS_RE.search(text)
    if match is None:
        return None
    token, half = match.group(1), match.group(2)
    if token == "半":
        return 0.5
    if set(token) <= set("0123456789."):
        value = float(token)
    else:
        value = _chinese_token_value(token)
    if not 0 <= value <= 99:
        raise ValueError("V4_YEARS_RANGE")
    return value + (0.5 if half == "半" else 0.0)


def years_feasible(years: float, job: JobPosting) -> bool:
    """True when stated years fit the job's configured min/max years window."""
    if not isinstance(job, JobPosting):
        raise ValueError("V4_FEASIBLE_INPUT")
    if isinstance(years, bool) or not isinstance(years, (int, float)):
        raise ValueError("V4_FEASIBLE_INPUT")
    minimum, maximum = job.min_years, job.max_years
    for bound in (minimum, maximum):
        if bound is not None and (isinstance(bound, bool) or not isinstance(bound, (int, float))):
            raise ValueError("V4_FEASIBLE_INPUT")
    return minimum <= years and (maximum is None or years <= maximum)


def seniority_rerank(ranking: Sequence[str], jobs: Sequence[JobPosting],
                     query: str) -> Tuple[str, ...]:
    """Feasible jobs first (original order kept), infeasible jobs after; None is neutral."""
    if (isinstance(ranking, (str, bytes)) or not isinstance(ranking, Sequence)
            or any(not isinstance(jd_id, str) for jd_id in ranking)
            or len(set(ranking)) != len(ranking)):
        raise ValueError("V4_RERANK_INPUT")
    if not isinstance(jobs, Sequence) or isinstance(jobs, (str, bytes)):
        raise ValueError("V4_RERANK_INPUT")
    by_id = {job.jd_id: job for job in jobs}
    if any(jd_id not in by_id for jd_id in ranking) or len(by_id) != len(jobs):
        raise ValueError("V4_RERANK_INPUT")
    years = extract_experience_years(query)
    if years is None:
        return tuple(ranking)
    feasible = [jd_id for jd_id in ranking if years_feasible(years, by_id[jd_id])]
    infeasible = [jd_id for jd_id in ranking if not years_feasible(years, by_id[jd_id])]
    return tuple(feasible + infeasible)


@dataclass(frozen=True)
class SeniorityCase:
    case_id: str
    coverage_group: str
    grades: Mapping[str, int]
    dense_ranking: Tuple[str, ...]
    reranked_ranking: Tuple[str, ...]
    years: Optional[float]


def dense_ranking(jobs, query, fake_vectors) -> Tuple[str, ...]:
    """Full dense ranking via app.retrieval with the dataset's fixed control vectors."""
    result = retrieve_job_evidence(query, jobs, enabled=True, embedder=fake_vectors, top_k=len(jobs))
    if result.status != "OK":
        raise ValueError("V4_DENSE_RANKING_FAILED")
    return tuple(hit.reference_jd_id for hit in result.hits)


def _row(case: SeniorityCase, grades: Mapping[str, int], jobs: Sequence[JobPosting]) -> dict:
    relevant = [key for key, grade in grades.items() if grade >= RELEVANCE_THRESHOLD]
    before_top1 = case.dense_ranking[0] if case.dense_ranking else None
    after_top1 = case.reranked_ranking[0] if case.reranked_ranking else None
    return {
        "id": case.case_id, "coverage_group": case.coverage_group,
        "experience_years": case.years,
        "feasible_reference_ids": ([job.jd_id for job in jobs
                                    if case.years is not None and years_feasible(case.years, job)]
                                   if case.years is not None else None),
        "relevant_reference_ids": relevant,
        "dense_ranking": list(case.dense_ranking), "reranked_ranking": list(case.reranked_ranking),
        "dense_recall_at_1": recall_at_k(grades, list(case.dense_ranking), 1),
        "reranked_recall_at_1": recall_at_k(grades, list(case.reranked_ranking), 1),
        "dense_recall_at_3": recall_at_k(grades, list(case.dense_ranking), 3),
        "reranked_recall_at_3": recall_at_k(grades, list(case.reranked_ranking), 3),
        "fixed_top1": bool(before_top1 != after_top1
                           and recall_at_k(grades, list(case.reranked_ranking), 1)
                           and not recall_at_k(grades, list(case.dense_ranking), 1)),
        "broken_top1": bool(before_top1 != after_top1
                            and recall_at_k(grades, list(case.dense_ranking), 1)
                            and not recall_at_k(grades, list(case.reranked_ranking), 1)),
    }


def _guards(rows: Sequence[dict]) -> dict:
    """Relevant jobs must never lose a top-1 or a top-3 slot through reranking."""
    demoted_top1 = sum(1 for row in rows if row["broken_top1"])
    lost_top3 = sum(1 for row in rows
                    if (row.get("dense_recall_at_3") or row.get("live_recall_at_3"))
                    and not (row.get("reranked_recall_at_3")))
    return {"relevant_demoted_from_top1": demoted_top1, "relevant_lost_from_top3": lost_top3}


def _metrics(cases: Sequence[SeniorityCase], ranking_of) -> dict:
    sums = {1: 0.0, 3: 0.0}
    relevant_count = 0
    for case in cases:
        relevant = {key for key, grade in case.grades.items() if grade >= RELEVANCE_THRESHOLD}
        if not relevant:
            continue
        relevant_count += 1
        ranking = ranking_of(case)
        for k in sums:
            sums[k] += recall_at_k(case.grades, list(ranking), k) or 0.0
    return {"relevant_case_count": relevant_count,
            "recall_at_1": sums[1] / relevant_count if relevant_count else None,
            "recall_at_3": sums[3] / relevant_count if relevant_count else None}


def evaluate_dataset(label: str, jobs, cases, fake_class) -> dict:
    fake_vectors = fake_class(_FakeAdapterDataset(jobs, cases))
    seniority_cases = []
    for case in cases:
        grades = dict(zip([j.jd_id for j in jobs], case.grades))
        dense = dense_ranking(jobs, case.query, fake_vectors)
        reranked = seniority_rerank(dense, jobs, case.query)
        seniority_cases.append(SeniorityCase(case.case_id, case.coverage_group, grades,
                                             dense, reranked, extract_experience_years(case.query)))
    rows = [_row(case, case.grades, jobs) for case in seniority_cases]
    return {
        "dataset": label,
        "job_ids": [job.jd_id for job in jobs],
        "documents": [{"reference_jd_id": job.jd_id,
                       "text_sha256": sha256(job.raw_text.strip().encode("utf-8")).hexdigest()}
                      for job in jobs],
        "case_count": len(rows),
        "dense_metrics": _metrics(seniority_cases, lambda case: case.dense_ranking),
        "reranked_metrics": _metrics(seniority_cases, lambda case: case.reranked_ranking),
        "guards": _guards(rows),
        "cases": rows,
    }


class _FakeAdapterDataset:
    """Minimal adapter so the frozen loaders' FakeVectors classes keep working."""

    def __init__(self, jobs, cases):
        self.jobs = jobs
        self.cases = cases


def evaluate() -> dict:
    """Fixed control vectors only; this tests the rerank program, not model quality."""
    sources = (
        ("v1", v1.load_dataset(), v1.FakeVectors),
        ("heldout", heldout.load_dataset(), heldout.FakeVectors),
        ("v2", v2.load_dataset(), v2.FakeVectors),
    )
    sections = []
    for label, dataset, fake_class in sources:
        sections.append(evaluate_dataset(label, dataset.jobs, dataset.cases, fake_class))
    dense_top1 = [section["dense_metrics"]["recall_at_1"] for section in sections]
    reranked_top1 = [section["reranked_metrics"]["recall_at_1"] for section in sections]
    return {
        "mode": "fixed-fake-vectors", "provider": "deterministic-years-feasibility-rerank",
        "quality_conclusion": "NOT_ESTABLISHED",
        "case_count": sum(section["case_count"] for section in sections),
        "datasets": sections,
        "summary": {"dense_recall_at_1": dense_top1, "reranked_recall_at_1": reranked_top1},
        "http_attempts": 0, "real_http_requests": 0,
        "limitations": [
            "Fake control vectors only test the rerank program; they are not model quality.",
            "Experience extraction is a regex over frozen synthetic text; 半年-like phrases outside the frozen sets can false-positive.",
            "No screening wiring, threshold fitting, routing or scoring changes; jobs.json untouched.",
            "No live embedding and no new HTTP; frozen dataset SHAs are revalidated on every run.",
        ],
    }


def _load_live_report(path: Path, dataset) -> dict:
    """Verify a persisted live report against the frozen dataset before replay."""
    try:
        raw = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise ValueError("V4_REPLAY_REPORT_INVALID") from None
    if not isinstance(raw, dict) or raw.get("dataset_sha256") != dataset.canonical_sha256:
        raise ValueError("V4_REPLAY_REPORT_INVALID")
    if raw.get("metrics_status") not in ("DESCRIPTIVE_BASELINE", "DESCRIPTIVE_HELDOUT", "DESCRIPTIVE_V2"):
        raise ValueError("V4_REPLAY_REPORT_INVALID")
    cases = {case.case_id: case for case in dataset.cases}
    rankings = {}
    for row in raw.get("cases", []):
        case = cases.get(row.get("id"))
        if case is None or row.get("status") != "OK":
            raise ValueError("V4_REPLAY_REPORT_INVALID")
        if row.get("query_sha256") != sha256(case.query.encode("utf-8")).hexdigest():
            raise ValueError("V4_REPLAY_REPORT_INVALID")
        ids = tuple(hit["reference_jd_id"] for hit in row.get("rankings", []))
        if len(ids) != len(row.get("rankings", [])) or len(set(ids)) != len(ids):
            raise ValueError("V4_REPLAY_REPORT_INVALID")
        rankings[case.case_id] = ids
    if set(rankings) != set(cases):
        raise ValueError("V4_REPLAY_REPORT_INVALID")
    return rankings


def replay_dataset(label: str, dataset, report_path: Path) -> dict:
    """Descriptive replay: reorder persisted live top-3 rankings, no new HTTP."""
    if not report_path.exists():
        return {"dataset": label, "status": "skipped_missing_report", "report_path": str(report_path.name)}
    jobs = dataset.jobs
    grades_by_case = {case.case_id: dict(zip([j.jd_id for j in jobs], case.grades))
                      for case in dataset.cases}
    rankings = _load_live_report(report_path, dataset)
    rows = []
    for case_id, dense in rankings.items():
        grades = grades_by_case[case_id]
        reranked = seniority_rerank(dense, jobs, next(c.query for c in dataset.cases if c.case_id == case_id))
        rows.append({
            "id": case_id,
            "experience_years": extract_experience_years(next(c.query for c in dataset.cases if c.case_id == case_id)),
            "relevant_reference_ids": [key for key, grade in grades.items() if grade >= RELEVANCE_THRESHOLD],
            "live_top3": list(dense), "reranked_top3": list(reranked),
            "live_recall_at_1": recall_at_k(grades, list(dense), 1),
            "reranked_recall_at_1": recall_at_k(grades, list(reranked), 1),
            "live_recall_at_3": recall_at_k(grades, list(dense), 3),
            "reranked_recall_at_3": recall_at_k(grades, list(reranked), 3),
            "fixed_top1": bool(reranked[0] != dense[0]
                               and recall_at_k(grades, list(reranked), 1)
                               and not recall_at_k(grades, list(dense), 1)),
            "broken_top1": bool(reranked[0] != dense[0]
                                and recall_at_k(grades, list(dense), 1)
                                and not recall_at_k(grades, list(reranked), 1)),
        })
    live_top1 = [row["live_recall_at_1"] for row in rows if row["live_recall_at_1"] is not None]
    reranked_top1 = [row["reranked_recall_at_1"] for row in rows if row["reranked_recall_at_1"] is not None]
    relevant_rows = [row for row in rows if row["live_recall_at_1"] is not None]
    return {
        "dataset": label, "status": "replayed", "report_path": report_path.name,
        "relevant_case_count": len(relevant_rows),
        "live_recall_at_1": sum(live_top1) / len(relevant_rows) if relevant_rows else None,
        "reranked_recall_at_1": sum(reranked_top1) / len(relevant_rows) if relevant_rows else None,
        "guards": _guards(rows),
        "fixed_case_ids": [row["id"] for row in rows if row["fixed_top1"]],
        "broken_case_ids": [row["id"] for row in rows if row["broken_top1"]],
        "cases": rows,
    }


def replay(selected: Mapping[str, Path]) -> list:
    sources = (
        ("v1", v1.load_dataset()),
        ("heldout", heldout.load_dataset()),
        ("v2", v2.load_dataset()),
    )
    return [replay_dataset(label, dataset, selected[label]) for label, dataset in sources]


def report_destination(output=None) -> Path:
    path = Path(output) if output is not None else REPORT_DIR / (
        "retrieval-v4-seniority-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + "-" + uuid4().hex[:8] + ".json")
    if (path.resolve().parent != REPORT_DIR.resolve() or path.suffix != ".json"
            or not path.name.startswith("retrieval-v4-seniority-")):
        raise ValueError("V4_REPORT_PATH")
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake", action="store_true", help="Run fixed artificial vectors; never a real model")
    parser.add_argument("--replay", action="store_true",
                        help="Also replay persisted live top-3 rankings through the same rerank")
    parser.add_argument("--replay-v1", type=Path, default=LIVE_REPORTS["v1"])
    parser.add_argument("--replay-heldout", type=Path, default=LIVE_REPORTS["heldout"])
    parser.add_argument("--replay-v2", type=Path, default=LIVE_REPORTS["v2"])
    parser.add_argument("--live", action="store_true", help="Rejected here; no live embedding exists in v4")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.live:
        print("V4 offline entry rejects --live; there is no live embedding path here.", file=sys.stderr)
        return 2
    if not args.fake:
        print("Pass --fake to run the fixed control vectors; v4 has no other mode.", file=sys.stderr)
        return 2
    try:
        destination = report_destination(args.output)
        if destination.exists():
            raise ValueError("V4_REPORT_EXISTS")
        report = evaluate()
        if args.replay:
            report["live_replay"] = replay({"v1": args.replay_v1, "heldout": args.replay_heldout,
                                            "v2": args.replay_v2})
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    except (OSError, ValueError):
        print("Retrieval v4 seniority evaluation failed; check frozen data or report path.", file=sys.stderr)
        return 2
    print("Retrieval v4 seniority %s: %d cases; dense@1=%s reranked@1=%s; 0 real HTTP requests." %
          (report["mode"], report["case_count"],
           [round(value, 3) if value is not None else None for value in report["summary"]["dense_recall_at_1"]],
           [round(value, 3) if value is not None else None for value in report["summary"]["reranked_recall_at_1"]]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
