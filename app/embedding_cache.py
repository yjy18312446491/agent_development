"""Role-aware SHA cache and a shared three-attempt, one-retry budget."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional, Sequence, Tuple

from app.embedding_client import (
    EmbeddingClient, EmbeddingError, MAX_REQUESTS, MAX_TOKENS, PRICE_PER_TOKEN,
    text_sha, validate_texts,
)


@dataclass(frozen=True)
class EmbeddingAttempt:
    text_type: str
    text_sha256: Tuple[str, ...]
    code: str
    http_status: Optional[int]
    total_tokens: Optional[int]
    latency_ms: float
    estimated_cny: Optional[float]


class EmbeddingCache:
    def __init__(self, client: EmbeddingClient):
        self._client = client
        self._vectors = {}
        self._history = []
        self._reserved_cny = Decimal("0")
        self._retry_used = False
        self._stopped = False

    @property
    def attempts(self) -> int:
        return len(self._history)

    def embed_documents(self, texts: Sequence[str]):
        return self._prefetch(texts, "document")

    def embed_queries(self, texts: Sequence[str]):
        return self._prefetch(texts, "query")

    def as_retriever_embedder(self):
        """Return a lookup-only callback: a cache miss can never make HTTP."""
        def lookup(texts):
            if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence) or not texts:
                raise EmbeddingError("CACHE_MISS")
            try:
                return tuple(self._vectors[("query" if index == 0 else "document", text_sha(text))]
                             for index, text in enumerate(texts))
            except (KeyError, AttributeError, UnicodeError, TypeError):
                raise EmbeddingError("CACHE_MISS") from None
        return lookup

    def report(self) -> dict:
        known = all(attempt.total_tokens is not None for attempt in self._history)
        return {
            "http_attempts": self.attempts, "retry_used": self._retry_used, "stopped": self._stopped,
            "reserved_cny": float(self._reserved_cny),
            "total_tokens": sum(attempt.total_tokens for attempt in self._history) if known else None,
            "estimated_cny": sum(attempt.estimated_cny for attempt in self._history) if known else None,
            "attempts": [asdict(attempt) for attempt in self._history],
        }

    def _prefetch(self, texts: Sequence[str], kind: str):
        texts = validate_texts(texts, kind)
        missing = {}
        for text in texts:
            digest = text_sha(text)
            if (kind, digest) not in self._vectors:
                missing.setdefault(digest, text)
        if not missing:
            return tuple(self._vectors[(kind, text_sha(text))] for text in texts)
        if self._stopped:
            raise EmbeddingError("PROVIDER_STOPPED")
        hashes, inputs = tuple(missing), tuple(missing.values())
        reservation = Decimal(len(inputs) * MAX_TOKENS) * Decimal("0.0000005")
        while True:
            if self.attempts >= MAX_REQUESTS or self._client.requests >= MAX_REQUESTS:
                self._stopped = True
                raise EmbeddingError("REQUEST_LIMIT")
            if self._reserved_cny + reservation > Decimal("0.10"):
                self._stopped = True
                raise EmbeddingError("BUDGET_LIMIT")
            self._reserved_cny += reservation
            try:
                batch = self._client.embed(inputs, kind)
            except EmbeddingError as error:
                cost = None if error.total_tokens is None else error.total_tokens * PRICE_PER_TOKEN
                self._history.append(EmbeddingAttempt(kind, hashes, error.code, error.status,
                                                      error.total_tokens, error.latency_ms, cost))
                if error.retryable and not self._retry_used and self.attempts < MAX_REQUESTS:
                    self._retry_used = True
                    continue
                self._stopped = True
                raise
            self._history.append(EmbeddingAttempt(kind, hashes, "OK", batch.http_status,
                                                  batch.total_tokens, batch.latency_ms, batch.estimated_cny))
            self._vectors.update(((kind, digest), vector) for digest, vector in zip(hashes, batch.vectors))
            return tuple(self._vectors[(kind, text_sha(text))] for text in texts)
