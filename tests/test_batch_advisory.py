"""Opt-in embedding sidecar stays off the scoring path and never uses real HTTP."""

from io import BytesIO
import json
from pathlib import Path
import urllib.request

import pytest

from app import api
from app.embedding_client import EmbeddingClient, EmbeddingConfig, EmbeddingError
from app.store import connect, fetch_batch


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"
S001 = "S001_normal_backend.pdf"
S002 = "S002_empty.pdf"
KEY = "synthetic-advisory-key"


class Response(BytesIO):
    def getcode(self):
        return 200


class FakeHTTP:
    def __init__(self):
        self.calls = []

    def open(self, request, timeout):
        body = json.loads(request.data)
        self.calls.append(body)
        texts = body["input"]["texts"]
        kind = body["parameters"]["text_type"]
        if kind == "document":
            bases = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
            embeddings = [list(bases[index % 3]) + [0.0] * 1021 for index in range(len(texts))]
        else:
            embeddings = [[0.0, 0.0, 1.0] + [0.0] * 1021 for _ in texts]
        payload = {"output": {"embeddings": [{"text_index": i, "embedding": vector}
                                            for i, vector in enumerate(embeddings)]},
                   "usage": {"total_tokens": len(texts)}}
        return Response(json.dumps(payload).encode())


@pytest.fixture(autouse=True)
def no_real_network_or_env(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Advisory tests must not read .env or open a real network")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    monkeypatch.setattr("app.embedding_client.build_opener", forbidden)
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        if path.name == ".env":
            forbidden()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)


@pytest.fixture
def client(local_api, monkeypatch):
    http = FakeHTTP()
    monkeypatch.setattr(api.EmbeddingConfig, "from_env_file", lambda path: EmbeddingConfig(
        api_key=KEY, provider="dashscope", model="text-embedding-v4",
        base_url="https://dashscope.aliyuncs.com", dimension=1024, region="cn-beijing",
    ))
    monkeypatch.setattr(api, "EmbeddingClient", lambda config: EmbeddingClient(config, opener=http))
    return local_api[0], local_api[1], http


@pytest.fixture
def local_api(tmp_path, monkeypatch):
    from tempfile import TemporaryDirectory
    from fastapi.testclient import TestClient

    db = tmp_path / "store" / "results.sqlite"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setenv("SCREEN_DB_PATH", str(db))
    monkeypatch.setattr(api, "TemporaryDirectory", lambda **kwargs: TemporaryDirectory(dir=uploads, **kwargs))
    with TestClient(api.app) as test_client:
        yield test_client, db


def _file(name):
    return ("files", (name, (RESUMES / name).read_bytes(), "application/pdf"))


def test_default_batch_has_no_advisory_and_does_not_construct_client(local_api, monkeypatch):
    test_client, db = local_api
    monkeypatch.setattr(api, "EmbeddingClient", lambda *a, **k: pytest.fail("client"))
    monkeypatch.setattr(api.EmbeddingConfig, "from_env_file", lambda *a, **k: pytest.fail("config"))
    response = test_client.post("/batches", files=[_file(S001), _file(S002)])
    assert response.status_code == 200
    data = response.json()
    assert "advisory" not in data
    assert (data["items"][0]["jd_id"], data["items"][0]["total"]) == ("JD1", 60)


def test_favicon_is_empty_and_does_not_create_a_database(local_api):
    test_client, db = local_api
    response = test_client.get("/favicon.ico")
    assert response.status_code == 204 and response.content == b""
    assert not db.exists()


def test_invalid_advisory_query_does_not_read_config(local_api, monkeypatch):
    test_client, db = local_api
    monkeypatch.setattr(api.EmbeddingConfig, "from_env_file", lambda *a, **k: pytest.fail("config"))
    response = test_client.post("/batches", params={"advisory": "on"}, files=[_file(S001)])
    assert response.status_code == 422 and response.json() == {"detail": "辅助检索方式不符合要求"}
    assert not db.exists()


def test_missing_embedding_config_does_not_persist(local_api, monkeypatch):
    test_client, db = local_api

    def fail(path):
        raise EmbeddingError("CONFIG_UNREADABLE")

    monkeypatch.setattr(api.EmbeddingConfig, "from_env_file", fail)
    response = test_client.post("/batches?advisory=embedding", files=[_file(S001)])
    assert response.status_code == 503 and response.json() == {"detail": "辅助检索未配置"}
    assert not db.exists()


def test_embedding_advisory_does_not_change_rule_scores(client):
    test_client, db, http = client
    response = test_client.post("/batches?advisory=embedding", files=[_file(S001), _file(S002)])
    assert response.status_code == 200
    data = response.json()
    normal, empty = data["items"]
    assert (normal["jd_id"], normal["tier"], normal["total"], normal["project_source"]) == ("JD1", "B", 60, "mock")
    assert (empty["status"], empty["reason_code"], empty["total"]) == ("REVIEW", "EMPTY", -1)
    advisory = data["advisory"]
    assert advisory["mode"] == "embedding" and advisory["quality_conclusion"] == "NOT_ESTABLISHED"
    first = advisory["items"][0]
    assert first["source_name"] == S001 and first["hits"][0]["reference_jd_id"] == "JD3"
    second = advisory["items"][1]
    assert second["source_name"] == S002 and second["hits"] == []
    fetched = test_client.get("/batches/" + data["batch_id"]).json()
    assert "advisory" not in fetched
    assert fetched["items"] == data["items"]
    with connect(db) as conn:
        stored = fetch_batch(conn, data["batch_id"])
    assert stored == data["items"]
    assert KEY not in response.text
    assert [call["parameters"]["text_type"] for call in http.calls] == ["document", "query"]
