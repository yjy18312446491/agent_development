"""Explicit local job-vector snapshots, with no embedding or network provider."""

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Tuple

from app.retrieval import RetrievalResult, _job_documents, _normalize_vectors, retrieve_job_evidence


INDEX_DIR = Path(__file__).resolve().parents[1] / "out/vector-index"
MAX_BYTES = 8 * 1024 * 1024
MAX_DOCUMENTS = 128
MAX_DIMENSION = 4096


class VectorIndexError(ValueError):
    """A fixed code only; never include paths, document text or vector values."""


@dataclass(frozen=True)
class IndexProfile:
    model: str
    dimension: int
    revision: str

    def __post_init__(self):
        if (not all(isinstance(s, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", s)
                    for s in (self.model, self.revision))
                or type(self.dimension) is not int or not 1 <= self.dimension <= MAX_DIMENSION):
            raise VectorIndexError("INDEX_PROFILE_INVALID")


@dataclass(frozen=True)
class IndexedDocument:
    reference_jd_id: str
    title: str
    text: str = field(repr=False)
    vector: Tuple[float, ...] = field(repr=False)


@dataclass(frozen=True)
class JobVectorIndex:
    profile: IndexProfile
    jobs_sha256: str
    documents: Tuple[IndexedDocument, ...] = field(repr=False)
    content_sha256: str


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value):
    return sha256(_canonical(value)).hexdigest()


def _job_snapshot(jobs):
    if isinstance(jobs, (str, bytes)) or not 1 <= len(jobs) <= MAX_DOCUMENTS:
        raise VectorIndexError("INDEX_INPUT_INVALID")
    documents = _job_documents(jobs)
    if any(len(text.encode("utf-8")) > 65536 or len(jd_id) > 80 or len(title) > 500
           for jd_id, title, text in documents):
        raise VectorIndexError("INDEX_INPUT_INVALID")
    return documents, _digest([asdict(job) for job in jobs])


def _body(index):
    return {"schema_version": 1, "model": index.profile.model,
            "dimension": index.profile.dimension, "revision": index.profile.revision,
            "jobs_sha256": index.jobs_sha256,
            "documents": [asdict(document) for document in index.documents]}


def _check(index):
    if not isinstance(index, JobVectorIndex) or not isinstance(index.profile, IndexProfile):
        raise VectorIndexError("INDEX_INVALID")
    IndexProfile(index.profile.model, index.profile.dimension, index.profile.revision)
    if (not isinstance(index.documents, tuple) or not 1 <= len(index.documents) <= MAX_DOCUMENTS
            or not re.fullmatch(r"[0-9a-f]{64}", index.jobs_sha256)):
        raise VectorIndexError("INDEX_INVALID")
    ids = []
    for doc in index.documents:
        if (not isinstance(doc, IndexedDocument) or not isinstance(doc.vector, tuple)
                or not all(isinstance(s, str) and s.strip() for s in (doc.reference_jd_id, doc.title, doc.text))
                or len(doc.reference_jd_id) > 80 or len(doc.title) > 500
                or len(doc.text.encode("utf-8")) > 65536):
            raise VectorIndexError("INDEX_INVALID")
        ids.append(doc.reference_jd_id)
        if len(doc.vector) != index.profile.dimension:
            raise VectorIndexError("INDEX_DIMENSION_MISMATCH")
    if len(set(ids)) != len(ids):
        raise VectorIndexError("INDEX_INVALID")
    vectors = _normalize_vectors(tuple(d.vector for d in index.documents), len(index.documents))
    if len(vectors[0]) != index.profile.dimension or _digest(_body(index)) != index.content_sha256:
        raise VectorIndexError("INDEX_INVALID")
    if len(_canonical(_body(index))) > MAX_BYTES:
        raise VectorIndexError("INDEX_SIZE_LIMIT")


def _against(index, jobs, profile):
    _check(index)
    if not isinstance(profile, IndexProfile):
        raise VectorIndexError("INDEX_PROFILE_INVALID")
    if index.profile != profile:
        raise VectorIndexError("INDEX_PROFILE_MISMATCH")
    documents, digest = _job_snapshot(jobs)
    if (digest != index.jobs_sha256
            or documents != tuple((d.reference_jd_id, d.title, d.text) for d in index.documents)):
        raise VectorIndexError("INDEX_STALE")


def build_job_index(jobs, vectors, *, profile: IndexProfile) -> JobVectorIndex:
    """Copy caller-supplied document vectors; never generate embeddings."""
    try:
        if not isinstance(profile, IndexProfile):
            raise VectorIndexError("INDEX_PROFILE_INVALID")
        documents, digest = _job_snapshot(jobs)
        if len(vectors) != len(documents) or any(len(v) != profile.dimension for v in vectors):
            raise VectorIndexError("INDEX_DIMENSION_MISMATCH")
        normalized = _normalize_vectors(vectors, len(documents))
        if len(normalized[0]) != profile.dimension:
            raise VectorIndexError("INDEX_DIMENSION_MISMATCH")
        rows = tuple(IndexedDocument(*doc, vector) for doc, vector in zip(documents, normalized))
        index = JobVectorIndex(profile, digest, rows, "")
        index = JobVectorIndex(profile, digest, rows, _digest(_body(index)))
        _against(index, jobs, profile)
        return index
    except VectorIndexError:
        raise
    except (ValueError, TypeError, AttributeError, OverflowError, UnicodeError):
        raise VectorIndexError("INDEX_INPUT_INVALID") from None


def _path(path):
    target = Path(path)
    if (INDEX_DIR.resolve() != INDEX_DIR.absolute() or target.resolve().parent != INDEX_DIR.resolve()
            or target.is_symlink() or not re.fullmatch(r"jobs-[A-Za-z0-9_-]{1,90}\.json", target.name)):
        raise VectorIndexError("INDEX_PATH_INVALID")
    return target


def save_job_index(index: JobVectorIndex, path=None) -> Path:
    """Publish a complete immutable generation; never overwrite a prior file."""
    temporary = None
    try:
        _check(index)
        target = _path(path if path is not None else INDEX_DIR / ("jobs-" + index.content_sha256 + ".json"))
        raw = _canonical({**_body(index), "content_sha256": index.content_sha256})
        if len(raw) > MAX_BYTES:
            raise VectorIndexError("INDEX_SIZE_LIMIT")
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".jobs-", suffix=".tmp", dir=INDEX_DIR)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        # Same-directory hard link publishes atomically and refuses an existing name.
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise VectorIndexError("INDEX_EXISTS") from None
        except OSError:
            if target.exists():
                # Some filesystems refuse to link onto an existing name with a
                # generic error instead of FileExistsError; the target still
                # exists, so refusing to publish keeps the same semantics.
                raise VectorIndexError("INDEX_EXISTS") from None
            raise
        return target
    except VectorIndexError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, OverflowError, UnicodeError):
        raise VectorIndexError("INDEX_WRITE_FAILED") from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass  # An orphan temporary file cannot become a published jobs-*.json index.


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise VectorIndexError("INDEX_INVALID")
        result[key] = value
    return result


def _nonfinite(value):
    raise VectorIndexError("INDEX_INVALID")


def load_job_index(path, *, jobs, profile: IndexProfile) -> JobVectorIndex:
    """Read locally and require current jobs plus an explicit model/revision profile."""
    try:
        target = _path(path)
        with target.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise VectorIndexError("INDEX_SIZE_LIMIT")
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite)
        if (not isinstance(body, dict) or set(body) != {"schema_version", "model", "dimension", "revision",
                                                      "jobs_sha256", "documents", "content_sha256"}
                or type(body["schema_version"]) is not int or body["schema_version"] != 1
                or not isinstance(body["documents"], list) or not 1 <= len(body["documents"]) <= MAX_DOCUMENTS):
            raise VectorIndexError("INDEX_INVALID")
        stored_profile = IndexProfile(body["model"], body["dimension"], body["revision"])
        rows = []
        for doc in body["documents"]:
            if not isinstance(doc, dict) or set(doc) != {"reference_jd_id", "title", "text", "vector"}:
                raise VectorIndexError("INDEX_INVALID")
            if not isinstance(doc["vector"], list):
                raise VectorIndexError("INDEX_INVALID")
            rows.append(IndexedDocument(doc["reference_jd_id"], doc["title"], doc["text"], tuple(doc["vector"])))
        index = JobVectorIndex(stored_profile, body["jobs_sha256"], tuple(rows), body["content_sha256"])
        _against(index, jobs, profile)
        return index
    except VectorIndexError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError):
        raise VectorIndexError("INDEX_INVALID") from None


def search_job_index(index, query_vector, *, jobs, profile, enabled=False, top_k=3) -> RetrievalResult:
    if enabled is False:
        return retrieve_job_evidence("", (), enabled=False)
    if enabled is not True:
        return _review("INDEX_INPUT_INVALID")
    try:
        _against(index, jobs, profile)
        query = _normalize_vectors((query_vector,), 1)[0]
        if len(query) != profile.dimension:
            raise VectorIndexError("INDEX_DIMENSION_MISMATCH")
        expected = ("index-vector-query",) + tuple(d.text for d in index.documents)

        def lookup(texts):
            if tuple(texts) != expected:
                raise VectorIndexError("INDEX_STALE")
            return (query,) + tuple(d.vector for d in index.documents)

        result = retrieve_job_evidence("index-vector-query", jobs, enabled=True, embedder=lookup, top_k=top_k)
        _against(index, jobs, profile)
        return result
    except VectorIndexError as error:
        return _review(str(error))
    except (ValueError, TypeError, AttributeError, OverflowError, UnicodeError):
        return _review("INDEX_INPUT_INVALID")


def search_saved_jobs(path, query_vector, *, jobs, profile, enabled=False, top_k=3) -> RetrievalResult:
    """Disabled returns before path/config/data access; enabled never falls back to HTTP."""
    if enabled is False:
        return retrieve_job_evidence("", (), enabled=False)
    if enabled is not True:
        return _review("INDEX_INPUT_INVALID")
    try:
        index = load_job_index(path, jobs=jobs, profile=profile)
    except VectorIndexError as error:
        return _review(str(error))
    return search_job_index(index, query_vector, jobs=jobs, profile=profile, enabled=True, top_k=top_k)


def _review(code):
    return RetrievalResult("REVIEW", (), code, "本地辅助索引不可用，请人工复核")
