"""Opt-in batch sidecar for embedding evidence; never writes scores or jd_id."""

from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from app.embedding_client import EmbeddingClient, EmbeddingError, MAX_TEXT_BYTES, text_sha
from app.extract import extract_pdf
from app.retrieval import retrieve_job_evidence
from app.route import JobPosting, load_jobs


def _lookup(vectors: Dict[Tuple[str, str], Sequence[float]]):
    def embedder(texts):
        return tuple(vectors[("query" if index == 0 else "document", text_sha(text))]
                     for index, text in enumerate(texts))
    return embedder


def _hits(result) -> List[dict]:
    return [{"reference_jd_id": hit.reference_jd_id, "title": hit.title,
             "similarity": hit.similarity, "evidence_excerpt": hit.text[:1000]}
            for hit in result.hits]


def attach_embedding_advisory(
    paths: Sequence[Path],
    client: EmbeddingClient,
    *,
    jobs: Optional[Sequence[JobPosting]] = None,
    load_jobs_fn: Callable = load_jobs,
) -> dict:
    """Rank job evidence after screening. Failures stay in the sidecar."""
    payload = {
        "advisory": True,
        "mode": "embedding",
        "quality_conclusion": "NOT_ESTABLISHED",
        "items": [],
    }
    try:
        job_list = tuple(jobs if jobs is not None else load_jobs_fn())
        documents = tuple(job.raw_text.strip() for job in job_list)
        queries = []
        texts = []
        oversized = set()
        for index, path in enumerate(paths):
            extracted = extract_pdf(path)
            text = extracted.text if extracted.ok else ""
            texts.append(text)
            if not text.strip():
                continue
            try:
                too_long = len(text.encode("utf-8")) > MAX_TEXT_BYTES
            except UnicodeError:
                too_long = True
            if too_long:
                oversized.add(index)
                continue
            queries.append(text)
        vectors = {}
        document_batch = client.embed(documents, "document")
        vectors.update((("document", digest), vector)
                       for digest, vector in zip(document_batch.text_sha256, document_batch.vectors))
        if queries:
            query_batch = client.embed(tuple(queries), "query")
            vectors.update((("query", digest), vector)
                           for digest, vector in zip(query_batch.text_sha256, query_batch.vectors))
        embedder = _lookup(vectors)
        items = []
        for index, (path, text) in enumerate(zip(paths, texts)):
            if index in oversized:
                items.append({"source_name": path.name, "status": "REVIEW",
                              "reason_code": "INPUT_LIMIT", "hits": []})
                continue
            result = retrieve_job_evidence(text, job_list, enabled=True, embedder=embedder, top_k=3)
            items.append({"source_name": path.name, "status": result.status,
                          "reason_code": result.reason_code, "hits": _hits(result)})
        payload["items"] = items
        return payload
    except EmbeddingError as error:
        return {
            "advisory": True,
            "mode": "embedding",
            "quality_conclusion": "NOT_ESTABLISHED",
            "items": [{"source_name": path.name, "status": "REVIEW",
                       "reason_code": error.code, "hits": []} for path in paths],
        }
    except (OSError, ValueError, TypeError, RecursionError):
        return {
            "advisory": True,
            "mode": "embedding",
            "quality_conclusion": "NOT_ESTABLISHED",
            "items": [{"source_name": path.name, "status": "REVIEW",
                       "reason_code": "EMBEDDING_ERROR", "hits": []} for path in paths],
        }
