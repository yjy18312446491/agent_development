"""Exercise the native embedding wire contract with fake HTTP only."""

from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from app.embedding_client import EmbeddingClient, EmbeddingConfig, EmbeddingError, _NoRedirect


KEY = "synthetic-embedding-test-key"


def payload(count=1, **changes):
    data = {"output": {"embeddings": [
        {"text_index": index, "embedding": [float(index + 1)] + [0.0] * 1023}
        for index in range(count)
    ]}, "usage": {"total_tokens": count * 10}}
    data.update(changes)
    return data


class Response(BytesIO):
    status = 200

    def getcode(self):
        return self.status


class FakeHTTP:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return Response(json.dumps(outcome).encode("utf-8"))


def test_native_request_and_out_of_order_vectors():
    data = payload(2)
    data["output"]["embeddings"].reverse()
    http = FakeHTTP([data])
    result = EmbeddingClient(EmbeddingConfig(KEY), opener=http).embed(["one", "two"], "document")
    request, timeout = http.calls[0]
    assert request.full_url.endswith("/api/v1/services/embeddings/text-embedding/text-embedding")
    assert request.get_header("Authorization") == "Bearer " + KEY
    assert timeout == 15
    assert json.loads(request.data) == {
        "model": "text-embedding-v4", "input": {"texts": ["one", "two"]},
        "parameters": {"text_type": "document", "dimension": 1024, "output_type": "dense"},
    }
    assert [v[0] for v in result.vectors] == [1, 2]
    assert result.total_tokens == 20 and result.estimated_cny == pytest.approx(0.00001)
    assert result.http_status == 200 and result.latency_ms >= 0
    assert KEY not in repr(result) and KEY not in repr(EmbeddingConfig(KEY))


@pytest.mark.parametrize("change", [
    {"provider": "deepseek"}, {"model": "other"}, {"dimension": 512},
    {"dimension": True}, {"region": "ap-southeast-1"},
    {"api_key": ""}, {"api_key": "key with spaces"}, {"api_key": "非ASCII"},
    {"api_key": "key\x00"},
    {"base_url": "http://dashscope.aliyuncs.com"},
    {"base_url": "https://dashscope.aliyuncs.com.evil.example"},
    {"base_url": "https://a.ap-southeast-1.maas.aliyuncs.com"},
    {"base_url": "https://user@dashscope.aliyuncs.com"},
    {"base_url": "https://dashscope.aliyuncs.com:443"},
    {"base_url": "https://dashscope.aliyuncs.com/?key=private"},
    {"base_url": "https://dashscope.aliyuncs.com/other"},
])
def test_invalid_config_is_rejected_before_http(change):
    options = {"api_key": KEY}
    options.update(change)
    with pytest.raises(EmbeddingError) as error:
        EmbeddingConfig(**options)
    assert KEY not in str(error.value)


def test_workspace_url_is_accepted_but_not_exposed():
    config = EmbeddingConfig(KEY, base_url="https://synthetic-workspace.cn-beijing.maas.aliyuncs.com/api/v1")
    http = FakeHTTP([payload()])
    EmbeddingClient(config, opener=http).embed(["query"], "query")
    assert "/api/v1/api/v1/" not in http.calls[0][0].full_url
    assert "synthetic-workspace" not in repr(config)


def test_env_config_never_falls_back_to_llm_key(tmp_path):
    path = tmp_path / "config"
    path.write_text("LLM_API_KEY=" + KEY, encoding="utf-8")
    with pytest.raises(EmbeddingError):
        EmbeddingConfig.from_env_file(path)
    path.write_text("\n".join((
        "EMBEDDING_PROVIDER=dashscope", "EMBEDDING_MODEL=text-embedding-v4",
        "EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com", "EMBEDDING_REGION=cn-beijing",
        "EMBEDDING_DIMENSION=1024", "EMBEDDING_API_KEY=" + KEY,
    )), encoding="utf-8")
    assert EmbeddingConfig.from_env_file(path).api_key == KEY


@pytest.mark.parametrize("texts,kind", [([], "query"), (["x"] * 11, "query"),
    ([""], "query"), (["x" * 8193], "query"), (["中" * 700], "query"), (["x"], "invalid")])
def test_input_limit_and_type_reject_locally(texts, kind):
    http = FakeHTTP([])
    client = EmbeddingClient(EmbeddingConfig(KEY), opener=http)
    with pytest.raises(EmbeddingError):
        client.embed(texts, kind)
    assert http.calls == [] and client.requests == 0


@pytest.mark.parametrize("corruption", ["dimension", "count", "nan", "inf", "bool", "zero", "index", "duplicate", "usage"])
def test_malformed_success_stops_subsequent_calls(corruption):
    data = payload(2)
    rows = data["output"]["embeddings"]
    if corruption == "dimension":
        rows[0]["embedding"].pop()
    elif corruption == "count":
        rows.pop()
    elif corruption in ("nan", "inf", "bool"):
        rows[0]["embedding"][0] = {"nan": float("nan"), "inf": float("inf"), "bool": True}[corruption]
    elif corruption == "zero":
        rows[0]["embedding"] = [0] * 1024
    elif corruption == "index":
        rows[0]["text_index"] = 2
    elif corruption == "duplicate":
        rows[1]["text_index"] = 0
    else:
        del data["usage"]
    http = FakeHTTP([data])
    client = EmbeddingClient(EmbeddingConfig(KEY), opener=http)
    with pytest.raises(EmbeddingError) as error:
        client.embed(["one", "two"], "query")
    assert error.value.total_tokens == (None if corruption == "usage" else 20)
    assert not error.value.retryable
    with pytest.raises(EmbeddingError, match="PROVIDER_STOPPED"):
        client.embed(["other"], "query")
    assert len(http.calls) == 1


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 302])
def test_http_terminal_errors_are_sanitized_and_sticky(status):
    http = FakeHTTP([HTTPError("https://synthetic-workspace.example/" + KEY, status, KEY, {}, BytesIO(KEY.encode()))])
    client = EmbeddingClient(EmbeddingConfig(KEY), opener=http)
    with pytest.raises(EmbeddingError) as error:
        client.embed(["one"], "document")
    assert not error.value.retryable and error.value.status == status
    assert KEY not in str(error.value) and "workspace" not in str(error.value)
    with pytest.raises(EmbeddingError):
        client.embed(["two"], "document")
    assert len(http.calls) == 1


@pytest.mark.parametrize("code", ["InvalidApiKey", "Arrearage", "ModelNotFound", KEY])
def test_http_200_provider_error_codes_are_terminal(code):
    client = EmbeddingClient(EmbeddingConfig(KEY), opener=FakeHTTP([{"code": code, "message": KEY}]))
    with pytest.raises(EmbeddingError, match="PROVIDER_ERROR") as error:
        client.embed(["one"], "query")
    assert not error.value.retryable and KEY not in str(error.value)


def test_transport_does_not_retry_and_fourth_http_is_refused():
    http = FakeHTTP([TimeoutError(KEY), payload(), payload()])
    client = EmbeddingClient(EmbeddingConfig(KEY), opener=http)
    with pytest.raises(EmbeddingError) as error:
        client.embed(["one"], "query")
    assert error.value.retryable and len(http.calls) == 1 and KEY not in str(error.value)
    client.embed(["two"], "query")
    client.embed(["three"], "query")
    with pytest.raises(EmbeddingError, match="REQUEST_LIMIT"):
        client.embed(["four"], "query")
    assert len(http.calls) == 3


@pytest.mark.parametrize("tokens", [None, -1, True, "10", 8193])
def test_invalid_usage_remains_unknown(tokens):
    client = EmbeddingClient(EmbeddingConfig(KEY), opener=FakeHTTP([payload(usage={"total_tokens": tokens})]))
    with pytest.raises(EmbeddingError, match="USAGE_UNKNOWN") as error:
        client.embed(["one"], "query")
    assert error.value.total_tokens is None


def test_new_native_index_alias_is_supported_without_guessing_row_order():
    data = payload()
    row = data["output"]["embeddings"][0]
    row["index"] = row.pop("text_index")
    assert EmbeddingClient(EmbeddingConfig(KEY), opener=FakeHTTP([data])).embed(["one"], "query").vectors[0][0] == 1


def test_provider_model_mismatch_is_not_reported_as_the_expected_model():
    client = EmbeddingClient(EmbeddingConfig(KEY), opener=FakeHTTP([payload(model="other-model")]))
    with pytest.raises(EmbeddingError, match="RESPONSE_MODEL"):
        client.embed(["one"], "query")


def test_real_opener_installs_no_redirect_handler_without_making_http():
    client = EmbeddingClient(EmbeddingConfig(KEY))
    handler = next(handler for handler in client._opener.handlers if isinstance(handler, _NoRedirect))
    assert handler.redirect_request(None, None, 302, "redirect", {}, "https://example.invalid") is None
