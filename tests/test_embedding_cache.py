"""Check per-round budgets and that seven rankings reuse prefetched vectors."""

from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from app.embedding_cache import EmbeddingCache
from app.embedding_client import EmbeddingClient, EmbeddingConfig, EmbeddingError
from app.retrieval import retrieve_job_evidence
from app.route import load_jobs


class Response(BytesIO):
    def getcode(self):
        return 200


class FakeHTTP:
    def __init__(self, failures=()):
        self.failures = list(failures)
        self.calls = []

    def open(self, request, timeout):
        body = json.loads(request.data)
        self.calls.append(body)
        failure = self.failures.pop(0) if self.failures else None
        if isinstance(failure, int):
            raise HTTPError("https://example.invalid", failure, "private-key", {}, BytesIO(b"private-key"))
        if isinstance(failure, Exception):
            raise failure
        texts = body["input"]["texts"]
        data = {"output": {"embeddings": [
            {"text_index": index, "embedding": [1.0] + [0.0] * 1023} for index in range(len(texts))
        ]}, "usage": {"total_tokens": len(texts) * 10}}
        return Response(json.dumps(data).encode())


def cache_with(failures=()):
    http = FakeHTTP(failures)
    return EmbeddingCache(EmbeddingClient(EmbeddingConfig("synthetic-key"), opener=http)), http


def test_prefetch_three_documents_seven_queries_then_seven_rankings_make_no_more_http():
    cache, http = cache_with()
    jobs = load_jobs()
    queries = ["synthetic query " + str(index) for index in range(7)]
    cache.embed_documents([job.raw_text.strip() for job in jobs])
    cache.embed_queries(queries)
    for query in queries:
        assert retrieve_job_evidence(query, jobs, enabled=True, embedder=cache.as_retriever_embedder()).status == "OK"
    assert [len(body["input"]["texts"]) for body in http.calls] == [3, 7]
    assert [body["parameters"]["text_type"] for body in http.calls] == ["document", "query"]
    assert cache.report()["http_attempts"] == 2


def test_duplicates_are_cached_and_roles_do_not_share_vectors():
    cache, http = cache_with()
    cache.embed_documents(["same", "same"])
    cache.embed_documents(["same"])
    cache.embed_queries(["same"])
    cache.embed_queries(["same"])
    assert len(http.calls) == 2 and all(len(body["input"]["texts"]) == 1 for body in http.calls)


def test_disabled_retrieval_and_cache_miss_cannot_make_http():
    cache, http = cache_with()
    assert retrieve_job_evidence("query", load_jobs(), embedder=cache.as_retriever_embedder()).status == "DISABLED"
    with pytest.raises(EmbeddingError, match="CACHE_MISS"):
        cache.as_retriever_embedder()(["missing"])
    assert http.calls == []


@pytest.mark.parametrize("failure", [TimeoutError("private-key"), 408, 429, 500, 503])
def test_only_one_global_retry_and_unknown_failed_usage(failure):
    cache, http = cache_with([failure, None, failure])
    cache.embed_documents(["document"])
    with pytest.raises(EmbeddingError):
        cache.embed_queries(["query"])
    with pytest.raises(EmbeddingError, match="PROVIDER_STOPPED"):
        cache.embed_queries(["query two"])
    assert len(http.calls) == 3 and cache.report()["retry_used"]
    assert cache.report()["total_tokens"] is None and cache.report()["estimated_cny"] is None
    assert "private-key" not in json.dumps(cache.report())


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
def test_account_errors_stop_entire_round(status):
    cache, http = cache_with([status])
    with pytest.raises(EmbeddingError):
        cache.embed_documents(["document"])
    with pytest.raises(EmbeddingError, match="PROVIDER_STOPPED"):
        cache.embed_queries(["query"])
    assert len(http.calls) == 1


def test_fourth_attempt_is_rejected_without_http():
    cache, http = cache_with()
    for index in range(3):
        cache.embed_documents([str(index)])
    with pytest.raises(EmbeddingError, match="REQUEST_LIMIT"):
        cache.embed_queries(["fourth"])
    assert len(http.calls) == 3


def test_money_reservation_blocks_an_attempt_before_exceeding_ten_cents():
    cache, http = cache_with()
    for prefix in ("a", "b"):
        cache.embed_documents([prefix + str(index) for index in range(10)])
    with pytest.raises(EmbeddingError, match="BUDGET_LIMIT"):
        cache.embed_queries(["q" + str(index) for index in range(10)])
    assert len(http.calls) == 2 and cache.report()["reserved_cny"] <= 0.10
