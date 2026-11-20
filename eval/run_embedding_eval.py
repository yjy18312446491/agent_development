"""Preview a frozen pilot, or explicitly run one locked and bounded live round."""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Tuple
from uuid import uuid4

from app.embedding_cache import EmbeddingCache
from app.embedding_client import DIMENSION, MODEL, EmbeddingClient, EmbeddingConfig, EmbeddingError, text_sha, validate_texts
from app.extract import extract_pdf
from app.guard import guard_text
from app.retrieval import retrieve_job_evidence
from app.route import JobPosting, load_jobs


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "eval/live_pdf_cases.json"
FIXTURES = ROOT / "data/synthetic/resumes/live_pdf_eval"
REPORT_DIR = ROOT / "eval/reports"
ENV_FILE = ROOT / ".env"
JOBS = ROOT / "data/synthetic/jobs/jobs.json"
MANIFEST_SHA = "647d4327f0ce7c37d79a4e3ce131609747b12a917d738bdbfa423e8e9759a39a"
# The exact same committed snapshot uses LF on Linux and CRLF in this Windows worktree.
MANIFEST_GIT_SHA = "2b179f2e14e5f00cc742dad017dae4658f945a38044ee5c6792dac9da3564b7c"
JOBS_SHAS = (
    "0e1b05846f3ad13492dacbaa3a3f0ded2a1402b329b5b71c2ac94c48eb973e09",
    "b6d1be6785bdd7255a1118facaf71c51462ec1544293d875049c13cbf7bb8367",
)
DOCUMENT_SHAS = (
    "416e53ca42f9f4b9c703c4d9b9946923aa5822e70583d271e3ba74d386c06c94",
    "d980df845a13eaf5524f2f71da94160d6fbb8a3f2240794bba16ff3e0e1ca6ab",
    "3a5ae0174e8d15f3f7a3dd5dd2cc7129420298f95b8828be2591345eb93e343c",
)
QUERY_SHAS = {
    "personal_api": "0a78e24b06c5ba36d95ae9edaa2357ec24f3fa48104547c16c09fc94deb4ff75",
    "team_api": "25b4b727de440b2c5fbc9d4ad5354ebbfd759e90b3bfd739389402ba5d15d20b",
    "two_projects": "465d57cad0cbf9f967dca0b33b7dc9b41cb680d7f4f664d375459ae0fa9b3479",
    "unrelated_project": "56661f12c74e98cd9d3d0ab1597a1cb2c1173b0ef927db0429448b04d18fc8ac",
    "tool_mention": "263cf392ca29d5f193bfc0fa6a349d62a9bd2e62d2b5ce21d5dacdb8c895dbb7",
    "no_projects": "132b040261437e2ac2436234e6f076ad9a6828a10f4960fc82d57be43aacdebe",
    "unmatched": "215612a748a9d420fae3cace8a2d96e87f08f130f8948e532f6011a95d498513",
}
NO_RERUN = "不要再次运行 --live 或重建客户端换取新配额；保留锁和报告，先人工复核。"


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


def load_plan(manifest: Path = MANIFEST, fixtures: Path = FIXTURES, jobs_file: Path = JOBS) -> EmbeddingPlan:
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
                if not gate.passed or text_sha(extracted.text) != QUERY_SHAS.get(label["id"]):
                    raise ValueError()
                queries.append(Query(label["id"], extracted.text))
        if [query.case_id for query in queries] != list(QUERY_SHAS) or skipped != ["short_gate"]:
            raise ValueError()
        jobs_sha = sha256(jobs_file.read_bytes()).hexdigest()
        if jobs_file.name != "jobs.json" or jobs_sha not in JOBS_SHAS:
            raise ValueError()
        jobs = tuple(load_jobs(jobs_file.parent))
        if [job.jd_id for job in jobs] != ["JD1", "JD2", "JD3"]:
            raise ValueError()
        documents = tuple(job.raw_text.strip() for job in jobs)
        if tuple(map(text_sha, documents)) != DOCUMENT_SHAS:
            raise ValueError()
        validate_texts(documents, "document")
        validate_texts(tuple(query.text for query in queries), "query")
        if sha256(jobs_file.read_bytes()).hexdigest() != jobs_sha:
            raise ValueError()
        return EmbeddingPlan(jobs, documents, tuple(queries), tuple(skipped), jobs_sha, manifest_sha)
    except (OSError, ValueError, KeyError, TypeError, EmbeddingError):
        raise ValueError("EMBEDDING_DATASET_INVALID") from None


def evaluate_injected(plan: EmbeddingPlan, client) -> dict:
    """Exercise an injected transport without assuming it really sends HTTP."""
    return _evaluate_cached(plan, EmbeddingCache(client))


def _evaluate_cached(plan: EmbeddingPlan, cache: EmbeddingCache) -> dict:
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


def _report_destination(output=None, *, live=False) -> Path:
    destination = Path(output) if output is not None else REPORT_DIR / (
        ("embedding-live-" if live else "embedding-preview-")
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8] + ".json"
    )
    if destination.resolve().parent != REPORT_DIR.resolve() or destination.suffix.lower() != ".json":
        raise ValueError("EMBEDDING_REPORT_PATH")
    if live and not destination.name.startswith("embedding-live-"):
        raise ValueError("EMBEDDING_REPORT_PATH")
    return destination


def write_report(report: dict, output=None) -> Path:
    destination = _report_destination(output)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".embedding-report-", suffix=".tmp", dir=REPORT_DIR)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)
    return destination


def _reserve_live_report(report: dict, output=None) -> Path:
    destination = _report_destination(output, live=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    return destination


def _acquire_live_lock(report: dict, destination: Path):
    # Never delete or automatically reset this lock, on success or on failure.
    with (REPORT_DIR / "embedding-live.lock").open("x", encoding="utf-8") as handle:
        json.dump({"run_id": report["run_id"], "report": destination.name,
                   "state": "LOCKED_BEFORE_HTTP", "created_at": report["started_at"],
                   "max_http_attempts": 3, "budget_cny": 0.10}, handle)
        handle.flush()
        os.fsync(handle.fileno())


def _source_commit():
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, check=True, timeout=3)
        return result.stdout.decode("ascii").strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ValueError("EMBEDDING_SOURCE_UNKNOWN") from None


def _live_report(plan=None) -> dict:
    report = plan.preview() if plan is not None else {}
    report.update(mode="live", status="ARMED", counts_final=False, run_id=uuid4().hex,
                  started_at=datetime.now(timezone.utc).isoformat(), http_attempts=0,
                  real_http_requests=0, total_tokens=None, estimated_cny=None, note=NO_RERUN)
    return report


def _run_live(plan: EmbeddingPlan, config: EmbeddingConfig, report: dict, destination: Path) -> int:
    client = cache = None
    interrupted = unexpected = False
    try:
        client = EmbeddingClient(config)
        cache = EmbeddingCache(client)
        report.update(_evaluate_cached(plan, cache))
        success = not report["stopped"] and all(row["status"] == "OK" for row in report["rankings"])
        report["status"] = "SUCCESS" if success else "FAILED"
        if not success:
            report["failure_code"] = next(row["reason_code"] for row in report["rankings"] if row["status"] != "OK")
    except (KeyboardInterrupt, SystemExit):
        interrupted = True
        report.update(status="INTERRUPTED", failure_code="INTERRUPTED")
    except Exception:
        unexpected = True
        report.update(status="FAILED", failure_code="RUN_FAILED")
    if cache is not None:
        report.update(cache.report())
    count = client.requests if client is not None else 0
    report["unaccounted_attempts"] = max(0, count - len(report.get("attempts", [])))
    if interrupted or unexpected or count != len(report.get("attempts", [])):
        report.update(total_tokens=None, estimated_cny=None)
    report.setdefault("rankings", [{"id": query.case_id, "status": "NOT_RUN",
                                   "reason_code": report.get("failure_code", "PROVIDER_STOPPED"), "hits": []}
                                  for query in plan.queries])
    report.update(mode="live", real_http_requests=count, http_attempts=count, counts_final=True,
                  finished_at=datetime.now(timezone.utc).isoformat())
    try:
        write_report(report, destination)
    except (OSError, ValueError):
        print("Embedding live 报告保存失败。" + NO_RERUN, file=sys.stderr)
        return 1
    if report["status"] != "SUCCESS":
        print("Embedding live 未完成。" + NO_RERUN, file=sys.stderr)
        return 130 if interrupted else 1
    print(f"Embedding live completed: {count} HTTP attempts; lock retained, no automatic rerun.")
    return 0


def _preflight_failure(plan, report, destination, code, *, interrupted=False) -> int:
    failure = report if report is not None else _live_report(plan)
    failure.update(status="INTERRUPTED" if interrupted else "NOT_STARTED", failure_code=code, counts_final=True,
                   total_tokens=0, estimated_cny=0, finished_at=datetime.now(timezone.utc).isoformat())
    try:
        write_report(failure, destination or _report_destination(live=True))
    except (OSError, ValueError):
        pass  # No HTTP happened; an unwritable report directory is itself a preflight failure.
    print("Embedding live 预检失败：" + code + "。" + NO_RERUN, file=sys.stderr)
    return 130 if interrupted else 2


def _main_live(args) -> int:
    plan = destination = report = None
    try:
        plan = load_plan()
        config = EmbeddingConfig.from_env_file(ENV_FILE)
        report = _live_report(plan)
        report["source_commit"] = _source_commit()
        destination = _reserve_live_report(report, args.output)
        _acquire_live_lock(report, destination)
    except (KeyboardInterrupt, SystemExit):
        return _preflight_failure(plan, report, destination, "INTERRUPTED", interrupted=True)
    except (OSError, ValueError, EmbeddingError) as error:
        code = error.code if isinstance(error, EmbeddingError) else "PREFLIGHT_FAILED"
        if isinstance(error, FileExistsError):
            code = "LIVE_LOCK_OR_REPORT_EXISTS"
        return _preflight_failure(plan, report, destination, code)
    return _run_live(plan, config, report, destination)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.live:
        return _main_live(args)
    try:
        if args.output is not None and Path(args.output).name.startswith("embedding-live-"):
            raise ValueError("EMBEDDING_REPORT_PATH")
        plan = load_plan()
        write_report(plan.preview(), args.output)
    except (OSError, ValueError):
        print("Embedding 预览失败，请核对冻结数据或报告目录", file=sys.stderr)
        return 2
    print("Embedding preview: 3 document + 7 query vectors; 2 first attempts, at most 1 retry / 3 HTTP.")
    print("short_gate skipped locally; 0 real HTTP requests. Live requires explicit --live and a persistent lock.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
