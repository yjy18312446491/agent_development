"""Opt-in, in-memory job evidence retrieval, independent of routing and scoring."""

from dataclasses import dataclass
import math
from numbers import Real
from typing import Callable, Optional, Sequence, Tuple

from app.route import JobPosting


Embedder = Callable[[Sequence[str]], Sequence[Sequence[float]]]


@dataclass(frozen=True)
class JobEvidence:
    """A reference to existing job text, never a selected routing decision."""

    reference_jd_id: str
    title: str
    text: str
    similarity: float


@dataclass(frozen=True)
class RetrievalResult:
    status: str
    hits: Tuple[JobEvidence, ...]
    reason_code: str
    reason: str


def retrieve_job_evidence(
    text: str,
    jobs: Sequence[JobPosting],
    *,
    enabled: bool = False,
    embedder: Optional[Embedder] = None,
    top_k: int = 3,
) -> RetrievalResult:
    """Rank reference job texts using one injected batch embedding call.

    Disabled calls do no work. Enabled calls require an explicit provider; this
    module creates no client and reads no configuration. Results are advisory
    and do not feed back into screen_one, deterministic routing, or scoring.
    """
    if enabled is False:
        return RetrievalResult("DISABLED", (), "RETRIEVAL_DISABLED", "辅助检索未启用")
    if enabled is not True or type(top_k) is not int or top_k <= 0:
        return _review("RETRIEVAL_INPUT_ERROR", "辅助检索输入无效，请人工复核")
    if not isinstance(text, str) or not text.strip():
        return _review("RETRIEVAL_INPUT_ERROR", "辅助检索输入无效，请人工复核")
    try:
        documents = _job_documents(jobs)
    except (TypeError, ValueError, AttributeError):
        return _review("RETRIEVAL_INPUT_ERROR", "辅助检索输入无效，请人工复核")
    if not callable(embedder):
        return _review("EMBEDDER_MISSING", "辅助检索未配置向量提供器，请人工复核")

    # Immutable text snapshots are the only data passed to the provider.
    texts = (text,) + tuple(document[2] for document in documents)
    try:
        vectors = embedder(texts)
    except Exception:
        return _review("EMBEDDING_ERROR", "辅助向量生成失败，请人工复核")
    try:
        normalized = _normalize_vectors(vectors, len(texts))
    except (TypeError, ValueError, OverflowError):
        return _review("EMBEDDING_INVALID", "辅助向量结果无效，请人工复核")

    query = normalized[0]
    hits = []
    for (jd_id, title, evidence), vector in zip(documents, normalized[1:]):
        similarity = math.fsum(left * right for left, right in zip(query, vector))
        hits.append(JobEvidence(jd_id, title, evidence, max(-1.0, min(1.0, similarity))))
    hits.sort(key=lambda hit: (-hit.similarity, hit.reference_jd_id))
    return RetrievalResult("OK", tuple(hits[:top_k]), "RETRIEVAL_OK", "辅助证据已排序，岗位与评分仍以规则结果为准")


def _job_documents(jobs: Sequence[JobPosting]) -> Tuple[Tuple[str, str, str], ...]:
    if not isinstance(jobs, Sequence) or not jobs:
        raise ValueError("Missing job documents")
    documents = []
    seen = set()
    for job in jobs:
        if not isinstance(job, JobPosting) or not isinstance(job.jd_id, str) or not job.jd_id.strip():
            raise ValueError("Invalid job reference")
        if job.jd_id in seen or not isinstance(job.title, str) or not job.title.strip():
            raise ValueError("Duplicate or invalid job reference")
        seen.add(job.jd_id)
        if not isinstance(job.raw_text, str):
            raise ValueError("Invalid job text")
        evidence = job.raw_text.strip()
        if not evidence:
            evidence = "\n".join((
                job.title,
                "Required skills: " + ", ".join(job.skills),
                "Bonus skills: " + ", ".join(job.bonus_skills),
                f"Years: {job.min_years} to {job.max_years if job.max_years is not None else 'unbounded'}",
            ))
        documents.append((job.jd_id, job.title, evidence))
    return tuple(documents)


def _normalize_vectors(vectors: Sequence[Sequence[float]], count: int) -> Tuple[Tuple[float, ...], ...]:
    if not isinstance(vectors, Sequence) or len(vectors) != count:
        raise ValueError("Unexpected vector count")
    normalized = []
    dimension = None
    for vector in vectors:
        if isinstance(vector, (str, bytes, bytearray)) or not isinstance(vector, Sequence) or not vector:
            raise ValueError("Missing vector")
        if dimension is None:
            dimension = len(vector)
        if len(vector) != dimension:
            raise ValueError("Inconsistent vector dimensions")
        if any(isinstance(value, bool) or not isinstance(value, Real) for value in vector):
            raise ValueError("Non-numeric vector")
        values = tuple(float(value) for value in vector)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Non-finite vector")
        scale = max(abs(value) for value in values)
        if scale == 0:
            raise ValueError("Zero vector")
        scaled = tuple(value / scale for value in values)
        norm = math.sqrt(math.fsum(value * value for value in scaled))
        normalized.append(tuple(value / norm for value in scaled))
    return tuple(normalized)


def _review(code: str, reason: str) -> RetrievalResult:
    return RetrievalResult("REVIEW", (), code, reason)
