"""Preview the frozen embedding pilot; this revision forbids live execution."""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Tuple

from app.embedding_cache import EmbeddingCache
from app.embedding_client import DIMENSION, MODEL, EmbeddingError, text_sha, validate_texts
from app.extract import extract_pdf
from app.guard import guard_text
from app.retrieval import retrieve_job_evidence
from app.route import JobPosting, load_jobs


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "eval/live_pdf_cases.json"
FIXTURES = ROOT / "data/synthetic/resumes/live_pdf_eval"
REPORT_DIR = ROOT / "eval/reports"
MANIFEST_SHA = "647d4327f0ce7c37d79a4e3ce131609747b12a917d738bdbfa423e8e9759a39a"
# The exact same committed snapshot uses LF on Linux and CRLF in this Windows worktree.
MANIFEST_GIT_SHA = "2b179f2e14e5f00cc742dad017dae4658f945a38044ee5c6792dac9da3564b7c"
LIVE_FORBIDDEN = "本交接禁止真实请求，等 Cursor 复审且用户确认本机已配百炼 Key"


@dataclass(frozen=True)
class Query:
    case_id: str
    text: str = field(repr=False)


@dataclass(frozen=True)
class EmbeddingPlan:
    jobs: Tuple[JobPosting, ...]
    documents: Tuple[str, ...] = field(repr=False)
    queries: Tuple[Query, ...]
    skipped: Tuple[str, ...]
    jobs_sha256: str
    manifest_sha256: str

    def preview(self) -> dict:
        return {
            "mode": "preview", "model": MODEL, "region": "cn-beijing", "dimension": DIMENSION,
            "manifest_sha256": self.manifest_sha256, "frozen_manifest_sha256": MANIFEST_SHA,
            "jobs_sha256": self.jobs_sha256,
            "first_attempts": 2, "retry_budget": 1, "max_http_attempts": 3,
            "unique_vectors": len(self.documents) + len(self.queries), "budget_cny": 0.10,
            "http_attempts": 0, "real_http_requests": 0,
            "documents": [{"reference_jd_id": job.jd_id, "text_sha256": text_sha(text)}
                          for job, text in zip(self.jobs, self.documents)],
            "queries": [{"id": query.case_id, "text_sha256": text_sha(query.text),
                         "utf8_bytes": len(query.text.encode("utf-8"))} for query in self.queries],
            "skipped": list(self.skipped),
        }


def load_plan(manifest: Path = MANIFEST, fixtures: Path = FIXTURES) -> EmbeddingPlan:
    """Verify immutable source files without treating route labels as relevance gold."""
    try:
        contents = manifest.read_bytes()
        manifest_sha = sha256(contents).hexdigest()
        if manifest_sha not in (MANIFEST_SHA, MANIFEST_GIT_SHA):
            raise ValueError()
        labels = json.loads(contents)
        queries, skipped = [], []
        for label in labels:
            path = fixtures / label["pdf"]
            if path.resolve().parent != fixtures.resolve() or sha256(path.read_bytes()).hexdigest() != label["sha256"]:
                raise ValueError()
            extracted = extract_pdf(path)
            gate = guard_text(extracted)
            if label["id"] == "short_gate":
                if gate.passed:
                    raise ValueError()
                skipped.append(label["id"])
            else:
                if not gate.passed:
                    raise ValueError()
                queries.append(Query(label["id"], extracted.text))
        if len(queries) != 7 or skipped != ["short_gate"]:
            raise ValueError()
        jobs = tuple(load_jobs())
        if [job.jd_id for job in jobs] != ["JD1", "JD2", "JD3"]:
            raise ValueError()
        documents = tuple(job.raw_text.strip() for job in jobs)
        validate_texts(documents, "document")
        validate_texts(tuple(query.text for query in queries), "query")
        jobs_sha = sha256((ROOT / "data/synthetic/jobs/jobs.json").read_bytes()).hexdigest()
        return EmbeddingPlan(jobs, documents, tuple(queries), tuple(skipped), jobs_sha, manifest_sha)
    except (OSError, ValueError, KeyError, TypeError, EmbeddingError):
        raise ValueError("EMBEDDING_DATASET_INVALID") from None


def evaluate_injected(plan: EmbeddingPlan, client) -> dict:
    """Exercise a caller-injected transport; the CLI never creates a real client."""
    cache = EmbeddingCache(client)
    report = plan.preview()
    report.update(mode="injected-transport", real_http_requests=None)
    error_code = None
    try:
        cache.embed_documents(plan.documents)
        cache.embed_queries(tuple(query.text for query in plan.queries))
    except EmbeddingError as error:
        error_code = error.code
    rankings = []
    for query in plan.queries:
        if error_code:
            rankings.append({"id": query.case_id, "status": "NOT_RUN", "reason_code": error_code, "hits": []})
            continue
        result = retrieve_job_evidence(query.text, plan.jobs, enabled=True, embedder=cache.as_retriever_embedder())
        rankings.append({"id": query.case_id, "status": result.status, "reason_code": result.reason_code,
                         "hits": [{"reference_jd_id": hit.reference_jd_id, "similarity": hit.similarity}
                                  for hit in result.hits]})
    report.update(cache.report())
    report["rankings"] = rankings
    # No routed jd_id or project-score label is used to compute retrieval accuracy.
    return report


def write_report(report: dict, output=None) -> Path:
    destination = Path(output) if output is not None else REPORT_DIR / (
        "embedding-preview-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    )
    if destination.resolve().parent != REPORT_DIR.resolve() or destination.suffix.lower() != ".json":
        raise ValueError("EMBEDDING_REPORT_PATH")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return destination


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.live:
        parser.error(LIVE_FORBIDDEN)
    try:
        plan = load_plan()
        write_report(plan.preview(), args.output)
    except (OSError, ValueError):
        parser.error("Embedding 预览失败，请核对冻结数据或报告目录")
    print("Embedding preview: 3 document + 7 query vectors; 2 first attempts, at most 1 retry / 3 HTTP.")
    print("short_gate skipped locally; 0 real HTTP requests. Live execution is disabled in this revision.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
