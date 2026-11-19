"""Explicit native DashScope transport; one attempt per call, no SDK retries."""

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from time import perf_counter
from typing import Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MODEL = "text-embedding-v4"
DIMENSION = 1024
BASE_URL = "https://dashscope.aliyuncs.com"
API_PATH = "/services/embeddings/text-embedding/text-embedding"
MAX_REQUESTS = 3
MAX_BATCH_SIZE = 10
# Conservative short-fixture limit, not a claim to implement Qwen's tokenizer.
MAX_TEXT_BYTES = 2048
MAX_TOKENS = 8192
PRICE_PER_TOKEN = 0.0000005
STOP_STATUSES = (400, 401, 402, 403, 404)


class EmbeddingError(RuntimeError):
    """Only fixed public codes, never provider bodies, URLs, or credentials."""

    def __init__(self, code: str, *, status: Optional[int] = None, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.status = status
        self.retryable = retryable
        self.total_tokens = None
        self.latency_ms = 0.0


@dataclass(frozen=True)
class EmbeddingConfig:
    api_key: str = field(repr=False)
    provider: str = "dashscope"
    model: str = MODEL
    base_url: str = field(default=BASE_URL, repr=False)
    dimension: int = DIMENSION
    region: str = "cn-beijing"

    def __post_init__(self):
        if self.provider != "dashscope" or self.model != MODEL or self.region != "cn-beijing":
            raise EmbeddingError("CONFIG_MODEL_REGION")
        if type(self.dimension) is not int or self.dimension != DIMENSION:
            raise EmbeddingError("CONFIG_DIMENSION")
        if not isinstance(self.api_key, str) or not self.api_key or any(
            not 33 <= ord(char) <= 126 for char in self.api_key
        ):
            raise EmbeddingError("CONFIG_KEY")
        try:
            parsed = urlsplit(self.base_url)
            host = parsed.hostname or ""
            workspace = re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cn-beijing\.maas\.aliyuncs\.com", host)
            valid = (
                parsed.scheme == "https" and parsed.netloc == host
                and (host == "dashscope.aliyuncs.com" or workspace is not None)
                and parsed.path.rstrip("/") in ("", "/api/v1")
                and not parsed.query and not parsed.fragment
            )
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise EmbeddingError("CONFIG_ENDPOINT")

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        return base + ("" if base.endswith("/api/v1") else "/api/v1") + API_PATH

    @classmethod
    def from_env_file(cls, path: Path) -> "EmbeddingConfig":
        try:
            settings = {}
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                key, sep, value = line.strip().partition("=")
                if sep and not key.startswith("#"):
                    settings[key.strip()] = value.strip()
            if settings.get("EMBEDDING_DIMENSION") != "1024":
                raise EmbeddingError("CONFIG_DIMENSION")
            return cls(
                api_key=settings.get("EMBEDDING_API_KEY", ""),
                provider=settings.get("EMBEDDING_PROVIDER", ""),
                model=settings.get("EMBEDDING_MODEL", ""),
                base_url=settings.get("EMBEDDING_BASE_URL", ""),
                dimension=DIMENSION, region=settings.get("EMBEDDING_REGION", ""),
            )
        except (OSError, UnicodeError):
            raise EmbeddingError("CONFIG_UNREADABLE") from None


@dataclass(frozen=True)
class EmbeddingBatch:
    model: str
    text_type: str
    text_sha256: Tuple[str, ...]
    dimension: int
    vectors: Tuple[Tuple[float, ...], ...] = field(repr=False)
    total_tokens: Optional[int]
    http_status: int
    latency_ms: float
    estimated_cny: Optional[float]


def text_sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def validate_texts(texts: Sequence[str], text_type: str) -> Tuple[str, ...]:
    if text_type not in ("document", "query") or isinstance(texts, (str, bytes)):
        raise EmbeddingError("INPUT_INVALID")
    if not isinstance(texts, Sequence) or not 1 <= len(texts) <= MAX_BATCH_SIZE:
        raise EmbeddingError("INPUT_LIMIT")
    try:
        if any(not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > MAX_TEXT_BYTES for text in texts):
            raise EmbeddingError("INPUT_LIMIT")
    except UnicodeError:
        raise EmbeddingError("INPUT_INVALID") from None
    return tuple(texts)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class EmbeddingClient:
    def __init__(self, config: EmbeddingConfig, *, opener=None):
        self._config = config
        self._opener = opener if opener is not None else build_opener(_NoRedirect())
        self._requests = 0
        self._stopped = False

    @property
    def requests(self) -> int:
        return self._requests

    def embed(self, texts: Sequence[str], text_type: str) -> EmbeddingBatch:
        texts = validate_texts(texts, text_type)
        if self._stopped:
            raise EmbeddingError("PROVIDER_STOPPED")
        if self._requests >= MAX_REQUESTS:
            raise EmbeddingError("REQUEST_LIMIT")
        body = {"model": MODEL, "input": {"texts": texts}, "parameters": {
            "text_type": text_type, "dimension": DIMENSION, "output_type": "dense",
        }}
        request = Request(self._config.endpoint, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": "Bearer " + self._config.api_key, "Content-Type": "application/json"})
        started = perf_counter()
        status = None
        self._requests += 1
        try:
            try:
                with self._opener.open(request, timeout=15) as response:
                    status = response.getcode()
                    raw = response.read(1024 * 1024 + 1)
                if status != 200:
                    raise _http_error(status)
            except HTTPError as error:
                status = error.code
                error.close()
                raise _http_error(status) from None
            except (URLError, TimeoutError, OSError):
                raise EmbeddingError("TRANSPORT_ERROR", retryable=True) from None
            if len(raw) > 1024 * 1024:
                raise EmbeddingError("RESPONSE_INVALID")
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError):
                raise EmbeddingError("RESPONSE_INVALID") from None
            vectors, tokens = _parse_response(data, len(texts))
        except EmbeddingError as error:
            error.status = status
            error.latency_ms = round((perf_counter() - started) * 1000, 3)
            if not error.retryable:
                self._stopped = True
            raise
        except Exception:
            self._stopped = True
            raise EmbeddingError("TRANSPORT_INVALID", status=status) from None
        return EmbeddingBatch(MODEL, text_type, tuple(map(text_sha, texts)), DIMENSION, vectors,
                              tokens, status, round((perf_counter() - started) * 1000, 3), tokens * PRICE_PER_TOKEN)


def _http_error(status: int) -> EmbeddingError:
    retryable = status in (408, 429) or 500 <= status <= 599
    return EmbeddingError("HTTP_ERROR", status=status, retryable=retryable)


def _parse_response(data: dict, count: int):
    if not isinstance(data, dict):
        raise EmbeddingError("RESPONSE_INVALID")
    if data.get("code") not in (None, "", "Success", "success", "OK"):
        raise EmbeddingError("PROVIDER_ERROR")
    if "model" in data and data["model"] != MODEL:
        raise EmbeddingError("RESPONSE_MODEL")
    try:
        tokens = data["usage"]["total_tokens"]
        if type(tokens) is not int or not 0 <= tokens <= count * MAX_TOKENS:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise EmbeddingError("USAGE_UNKNOWN") from None
    try:
        rows = data["output"]["embeddings"]
        if not isinstance(rows, list) or len(rows) != count:
            raise ValueError()
        vectors = [None] * count
        for row in rows:
            index = row.get("text_index", row.get("index"))
            if "text_index" in row and "index" in row and row["text_index"] != row["index"]:
                raise ValueError()
            if type(index) is not int or not 0 <= index < count or vectors[index] is not None:
                raise ValueError()
            vector = row["embedding"]
            if not isinstance(vector, list) or len(vector) != DIMENSION:
                raise ValueError()
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector) or not any(vector):
                raise ValueError()
            vectors[index] = tuple(map(float, vector))
        return tuple(vectors), tokens
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        error = EmbeddingError("VECTOR_INVALID")
        error.total_tokens = tokens
        raise error from None
