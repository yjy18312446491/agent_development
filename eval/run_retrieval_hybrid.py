"""Retrieval quality v3: BM25 + RRF fusion over frozen datasets, offline, never HTTP."""

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys
from typing import Mapping, Optional, Sequence, Tuple
from uuid import uuid4

from app.retrieval import retrieve_job_evidence
from eval import run_retrieval_eval as v1
from eval import run_retrieval_heldout as heldout
from eval import run_retrieval_v2 as v2
from eval.run_retrieval_eval import recall_at_k


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "eval/reports"
RRF_K = 60
RELEVANCE_THRESHOLD = 2
ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> Tuple[str, ...]:
    """Deterministic mixed tokens: lowercased ASCII alnum runs plus CJK bigrams."""
    if not isinstance(text, str):
        raise ValueError("HYBRID_TOKENIZE_INPUT")
    lowered = text.lower()
    tokens = list(ASCII_TOKEN_RE.findall(lowered))
    cjk = [ch for ch in lowered if "\u4e00" <= ch <= "\u9fff"]
    tokens.extend(a + b for a, b in zip(cjk, cjk[1:]))
    return tuple(tokens)


class BM25Index:
    """Okapi BM25 over tiny in-memory job texts; stdlib only, no persistence."""

    def __init__(self, ids: Sequence[str], documents: Sequence[str],
                 k1: float = 1.5, b: float = 0.75):
        if k1 <= 0 or not 0 <= b <= 1 or len(ids) != len(documents) or len(set(ids)) != len(ids):
            raise ValueError("HYBRID_BM25_INPUT")
        if any(not isinstance(doc, str) for doc in documents):
            raise ValueError("HYBRID_BM25_INPUT")
        self.ids = tuple(ids)
        self.k1, self.b = k1, b
        token_lists = [tokenize(doc) for doc in documents]
        self.doc_lengths = [len(tokens) for tokens in token_lists]
        self.total_length = sum(self.doc_lengths)
        if self.total_length == 0 or any(length == 0 for length in self.doc_lengths):
            raise ValueError("HYBRID_BM25_EMPTY")
        self.term_counts = [Counter(tokens) for tokens in token_lists]
        document_frequency = Counter()
        for tokens in token_lists:
            document_frequency.update(set(tokens))
        self._idf = {term: math.log(1 + (len(ids) - freq + 0.5) / (freq + 0.5))
                     for term, freq in document_frequency.items()}

    def scores(self, query: str) -> Mapping[str, float]:
        terms = set(tokenize(query))
        result = {}
        for index, jd_id in enumerate(self.ids):
            length = self.doc_lengths[index]
            counts = self.term_counts[index]
            total = 0.0
            for term in terms:
                freq = counts.get(term, 0)
                if not freq:
                    continue
                weight = (freq * (self.k1 + 1)
                          / (freq + self.k1 * (1 - self.b + self.b * length / (self.total_length / len(self.ids)))))
                total += self._idf[term] * weight
            result[jd_id] = total
        return result

    def ranking(self, query: str) -> Tuple[str, ...]:
        """Descending score; ties broken by jd_id ascending, mirroring app.retrieval."""
        scored = self.scores(query)
        return tuple(sorted(self.ids, key=lambda jd_id: (-scored[jd_id], jd_id)))


def rrf_fuse(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> Tuple[str, ...]:
    """Reciprocal Rank Fusion: score(d) = sum(1 / (k + rank_i(d))); ties by id ascending."""
    if type(k) is not int or k <= 0 or not isinstance(rankings, Sequence) or isinstance(rankings, (str, bytes)):
        raise ValueError("HYBRID_RRF_INPUT")
    scores = {}
    for ranking in rankings:
        if isinstance(ranking, (str, bytes)) or not isinstance(ranking, Sequence):
            raise ValueError("HYBRID_RRF_INPUT")
        if len(set(ranking)) != len(ranking):
            raise ValueError("HYBRID_RRF_INPUT")
        for position, jd_id in enumerate(ranking, 1):
            if not isinstance(jd_id, str):
                raise ValueError("HYBRID_RRF_INPUT")
            scores[jd_id] = scores.get(jd_id, 0.0) + 1.0 / (k + position)
    return tuple(sorted(scores, key=lambda jd_id: (-scores[jd_id], jd_id)))


@dataclass(frozen=True)
class HybridCase:
    case_id: str
    coverage_group: str
    grades: Mapping[str, int]
    dense_ranking: Tuple[str, ...]
    sparse_ranking: Tuple[str, ...]
    fused_ranking: Tuple[str, ...]


def dense_ranking(jobs, query, fake_vectors) -> Tuple[str, ...]:
    """Full dense ranking via app.retrieval with the dataset's fixed control vectors."""
    result = retrieve_job_evidence(query, jobs, enabled=True, embedder=fake_vectors, top_k=len(jobs))
    if result.status != "OK":
        raise ValueError("HYBRID_DENSE_RANKING_FAILED")
    return tuple(hit.reference_jd_id for hit in result.hits)


def evaluate_case(case, jobs, fake_vectors, sparse_index) -> HybridCase:
    grades = dict(zip([j.jd_id for j in jobs], case.grades))
    dense = dense_ranking(jobs, case.query, fake_vectors)
    sparse = sparse_index.ranking(case.query)
    return HybridCase(case.case_id, case.coverage_group, grades, dense, sparse,
                      rrf_fuse([dense, sparse]))


def _metrics(cases: Sequence[HybridCase], ranking_of) -> dict:
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


def evaluate_dataset(label, jobs, cases, fake_vectors) -> dict:
    sparse_index = BM25Index([job.jd_id for job in jobs],
                             [job.raw_text.strip() for job in jobs])
    hybrid_cases = [evaluate_case(case, jobs, fake_vectors, sparse_index) for case in cases]
    dense_metrics = _metrics(hybrid_cases, lambda case: case.dense_ranking)
    sparse_metrics = _metrics(hybrid_cases, lambda case: case.sparse_ranking)
    fused_metrics = _metrics(hybrid_cases, lambda case: case.fused_ranking)
    return {
        "dataset": label,
        "job_ids": [job.jd_id for job in jobs],
        "documents": [{"reference_jd_id": job.jd_id, "text_sha256": sha256(job.raw_text.strip().encode("utf-8")).hexdigest()}
                      for job in jobs],
        "case_count": len(hybrid_cases),
        "dense_metrics": dense_metrics, "sparse_metrics": sparse_metrics,
        "fused_metrics": fused_metrics,
        "cases": [{
            "id": case.case_id, "coverage_group": case.coverage_group,
            "relevant_reference_ids": [key for key, grade in case.grades.items() if grade >= RELEVANCE_THRESHOLD],
            "dense_ranking": list(case.dense_ranking), "sparse_ranking": list(case.sparse_ranking),
            "fused_ranking": list(case.fused_ranking),
            "dense_recall_at_1": recall_at_k(case.grades, list(case.dense_ranking), 1),
            "fused_recall_at_1": recall_at_k(case.grades, list(case.fused_ranking), 1),
        } for case in hybrid_cases],
    }


def evaluate() -> dict:
    """Fixed control vectors only; this tests the fusion program, not model quality."""
    sources = (
        ("v1", v1.load_dataset(), v1.FakeVectors),
        ("heldout", heldout.load_dataset(), heldout.FakeVectors),
        ("v2", v2.load_dataset(), v2.FakeVectors),
    )
    sections = []
    for label, dataset, fake_class in sources:
        fake_vectors = fake_class(dataset)
        sections.append(evaluate_dataset(label, dataset.jobs, dataset.cases, fake_vectors))
    dense_top1 = [section["dense_metrics"]["recall_at_1"] for section in sections]
    fused_top1 = [section["fused_metrics"]["recall_at_1"] for section in sections]
    return {
        "mode": "fixed-fake-vectors", "provider": "bm25-okapi-plus-rrf-over-fixed-controls",
        "rrf_k": RRF_K, "quality_conclusion": "NOT_ESTABLISHED",
        "case_count": sum(section["case_count"] for section in sections),
        "datasets": sections,
        "summary": {"dense_recall_at_1": dense_top1, "fused_recall_at_1": fused_top1},
        "http_attempts": 0, "real_http_requests": 0,
        "limitations": [
            "Fake control vectors only test the fusion program; they are not model quality.",
            "Real live rankings are not reconstructible from persisted reports; replay is descriptive evidence only.",
            "BM25 tokenization is ASCII word runs plus CJK bigrams without a segmenter or skill dictionary.",
            "No screening wiring, threshold fitting, routing or scoring changes.",
            "No live embedding and no new HTTP; frozen dataset SHAs are revalidated on every run.",
        ],
    }


def report_destination(output=None) -> Path:
    path = Path(output) if output is not None else REPORT_DIR / (
        "retrieval-hybrid-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8] + ".json")
    if (path.resolve().parent != REPORT_DIR.resolve() or path.suffix != ".json"
            or not path.name.startswith("retrieval-hybrid-")):
        raise ValueError("HYBRID_REPORT_PATH")
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake", action="store_true", help="Run fixed artificial vectors; never a real model")
    parser.add_argument("--live", action="store_true", help="Rejected here; hybrid is offline only")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.live:
        print("Hybrid entry rejects --live; no live path exists for this experiment.", file=sys.stderr)
        return 2
    try:
        destination = report_destination(args.output)
        if destination.exists():
            raise ValueError("HYBRID_REPORT_EXISTS")
        report = evaluate()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    except (OSError, ValueError):
        print("Hybrid evaluation failed; check frozen data or report path.", file=sys.stderr)
        return 2
    print("Hybrid %s: %d cases; dense@1=%s fused@1=%s; 0 real HTTP requests." %
          (report["mode"], report["case_count"],
           [round(value, 3) if value is not None else None for value in report["summary"]["dense_recall_at_1"]],
           [round(value, 3) if value is not None else None for value in report["summary"]["fused_recall_at_1"]]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
