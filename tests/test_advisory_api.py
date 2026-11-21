"""Fixed fake-vector HTTP controls; all indexes are temporary, no model calls."""

import asyncio
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json
from pathlib import Path
import socket
import urllib.request

from fastapi.testclient import TestClient
import httpx
import pytest

from app import advisory, advisory_demo, api, vector_index as vi
from app.deepseek_client import DeepSeekClient, DeepSeekConfig
from app.embedding_client import EmbeddingClient, EmbeddingConfig
from app.route import load_jobs


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Advisory demo must not load credentials, create clients or use real HTTP")

    monkeypatch.setattr(vi, "INDEX_DIR", tmp_path / "indexes")
    monkeypatch.setattr(api, "_db_path", forbidden)
    for cls in (DeepSeekClient, EmbeddingClient):
        monkeypatch.setattr(cls, "__init__", forbidden)
    for cls in (DeepSeekConfig, EmbeddingConfig):
        monkeypatch.setattr(cls, "from_env_file", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    original_open = Path.open

    def opened(path, *args, **kwargs):
        if path.name == ".env" or "reports" in path.parts:
            forbidden()
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", opened)


@pytest.fixture
def service():
    return advisory_demo.prepare_demo_advisor()


def test_default_paths_and_disabled_body_are_inert(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Disabled paths must not load jobs, index or body")
    monkeypatch.setattr(vi, "load_job_index", forbidden)
    monkeypatch.setattr(advisory_demo, "load_jobs", forbidden)
    monkeypatch.setenv("RETRIEVAL_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")

    def body():
        forbidden()
        yield b"bad"

    for application in (api.app, api.create_app()):
        with TestClient(application) as client:
            assert client.get("/health").json() == {"status": "ok"}
            assert client.get("/").status_code == 200
            assert client.get("/advisory/status?enabled=true").json() == {"enabled": False, "mode": "disabled"}
            response = client.post("/advisory/evidence?enabled=true", content=body())
            assert response.status_code == 503
            assert response.json()["reason_code"] == "RETRIEVAL_DISABLED"
            assert response.json()["hits"] == []
    assert not vi.INDEX_DIR.exists()


def test_service_injection_is_exact_and_does_not_enable_default_app(service):
    with pytest.raises(TypeError):
        api.create_app(True)
    with TestClient(api.create_app(service)) as enabled, TestClient(api.app) as default:
        assert enabled.get("/advisory/status").json()["enabled"] is True
        assert default.get("/advisory/status").json()["enabled"] is False


def test_status_does_not_read_index_and_exposes_only_synthetic_cases(service, monkeypatch):
    monkeypatch.setattr(vi, "load_job_index", lambda *a, **k: pytest.fail("Status must not read index"))
    with TestClient(api.create_app(service)) as client:
        data = client.get("/advisory/status").json()
    assert set(data) == {"enabled", "mode", "cases"}
    assert data["mode"] == "fixed-fake-vectors"
    assert [c["case_id"] for c in data["cases"]] == [c.case_id for c in advisory.DEMO_CASES]
    assert all(set(c) == {"case_id", "title", "text"} for c in data["cases"])


@pytest.mark.parametrize("case_id", [c.case_id for c in advisory.DEMO_CASES])
@pytest.mark.parametrize("top_k", [1, 2, 3])
def test_valid_evidence_is_bounded_read_only_and_not_a_scoring_result(service, case_id, top_k, monkeypatch):
    before = service.index_path.read_bytes()
    monkeypatch.setattr(vi, "save_job_index", lambda *a, **k: pytest.fail("Queries must not publish indexes"))
    with TestClient(api.create_app(service)) as client:
        response = client.post("/advisory/evidence", json={"case_id": case_id, "top_k": top_k})
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"advisory", "mode", "quality_conclusion", "case_id", "status", "reason_code", "hits"}
    assert (data["advisory"], data["quality_conclusion"], data["status"]) == (True, "NOT_ESTABLISHED", "OK")
    assert data["case_id"] == case_id and len(data["hits"]) == top_k
    for hit in data["hits"]:
        assert set(hit) == {"reference_jd_id", "title", "similarity", "evidence_excerpt"}
        assert len(hit["evidence_excerpt"]) <= 1000 and -1 <= hit["similarity"] <= 1
    assert service.index_path.read_bytes() == before
    assert len(list(vi.INDEX_DIR.iterdir())) == 1
    assert str(service.index_path) not in response.text


@pytest.mark.parametrize("content_type", ["application/json", "application/json; charset=utf-8", "Application/JSON ; charset=UTF-8"])
def test_default_top_k_and_charset_are_accepted(service, content_type):
    with TestClient(api.create_app(service)) as client:
        response = client.post("/advisory/evidence", content=b'{"case_id":"demo_backend"}',
                               headers={"Content-Type": content_type})
    assert response.status_code == 200 and len(response.json()["hits"]) == 3


@pytest.mark.parametrize("body", [
    {}, [], None, "text", 2, {"case_id": "unknown"}, {"case_id": "demo_backend", "enabled": True},
    {"case_id": "demo_backend", "text": "private"}, {"case_id": "demo_backend", "path": "private"},
    {"case_id": ["demo_backend"]}, {"case_id": 1},
] + [{"case_id": "demo_backend", "top_k": value} for value in (True, False, 0, 4, -1, 1.0, "1", None, [], {})])
def test_invalid_inputs_never_reach_index(service, body, monkeypatch):
    monkeypatch.setattr(vi, "load_job_index", lambda *a, **k: pytest.fail("Invalid input must not read index"))
    with TestClient(api.create_app(service)) as client:
        response = client.post("/advisory/evidence", content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json() == {"detail": advisory.INVALID_REQUEST}
    assert "private" not in response.text


@pytest.mark.parametrize("body", [
    b"", b"bad", b'{"case_id":"demo_backend","case_id":"demo_other"}',
    b'{"case_id":"demo_backend","top_k":1,"top_k":2}',
    b'{"case_id":"demo_backend","top_k":NaN}',
    b'{"case_id":"demo_backend","top_k":Infinity}', b'\xff',
    b"[" * 1500 + b"]" * 1500,
])
def test_invalid_json_and_encoding_are_fixed_errors(service, body):
    with TestClient(api.create_app(service)) as client:
        response = client.post("/advisory/evidence", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json() == {"detail": advisory.INVALID_REQUEST}


@pytest.mark.parametrize("content_type", ["text/plain", "", "multipart/form-data", "application/jsonx"])
def test_non_json_is_rejected(service, content_type):
    with TestClient(api.create_app(service)) as client:
        response = client.post("/advisory/evidence", content=b'{"case_id":"demo_backend"}',
                               headers={"Content-Type": content_type})
    assert response.status_code == 422


def test_query_flags_cannot_reconfigure_enabled_service(service):
    with TestClient(api.create_app(service)) as client:
        response = client.post("/advisory/evidence?model=live", json={"case_id": "demo_backend"})
    assert response.status_code == 422


def streamed_request(application, chunks, *, content_length=None, disconnect=False):
    messages = [{"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1 or disconnect}
                for i, chunk in enumerate(chunks)]
    if disconnect:
        messages.append({"type": "http.disconnect"})
    sent = []
    headers = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", content_length))

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/advisory/evidence", "raw_path": b"/advisory/evidence",
             "query_string": b"", "headers": headers, "server": ("test", 80), "client": ("test", 1), "root_path": ""}
    asyncio.run(application(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body)


@pytest.mark.parametrize("content_length", [None, b"1", b"99999"])
def test_actual_stream_limit_ignores_content_length(service, content_length):
    body = b'{"case_id":"demo_backend"}'
    padded = body + b" " * (4096 - len(body))
    application = api.create_app(service)
    assert streamed_request(application, [padded[:2000], padded[2000:]], content_length=content_length)[0] == 200
    status, result = streamed_request(application, [padded, b" "], content_length=content_length)
    assert status == 422 and result == {"detail": advisory.INVALID_REQUEST}


def test_disconnect_returns_fixed_422(service):
    status, body = streamed_request(api.create_app(service), [b'{"case_id":'], disconnect=True)
    assert status == 422 and body == {"detail": advisory.INVALID_REQUEST}


@pytest.mark.parametrize("failure", ["missing", "corrupt", "deep_json", "profile", "stale", "read_error"])
def test_unavailable_index_returns_no_partial_or_private_result(service, failure, monkeypatch):
    if failure == "missing":
        service.index_path.unlink()
    elif failure == "corrupt":
        service.index_path.write_text("bad", encoding="utf-8")
    elif failure == "deep_json":
        service.index_path.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
    elif failure in ("profile", "stale"):
        profile = vi.IndexProfile("fake-controls", 3, "other") if failure == "profile" else advisory.DEMO_PROFILE
        jobs = service.jobs if failure == "profile" else (replace(service.jobs[0], raw_text="changed"),) + service.jobs[1:]
        index = vi.build_job_index(jobs, tuple(advisory.DOCUMENT_VECTORS.values()), profile=profile)
        other = vi.save_job_index(index)
        service.index_path.write_bytes(other.read_bytes())
    else:
        original_open = Path.open

        def denied(path, *args, **kwargs):
            if path == service.index_path:
                raise PermissionError("private-credential private-path")
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", denied)
    with TestClient(api.create_app(service)) as client:
        response = client.post("/advisory/evidence", json={"case_id": "demo_backend"})
    assert response.status_code == 503
    assert response.json()["status"] == "REVIEW" and response.json()["hits"] == []
    assert response.json()["reason_code"] in advisory.INDEX_REASONS
    assert "private" not in response.text and str(service.index_path) not in response.text


def test_unknown_index_error_is_not_reflected(service, monkeypatch):
    def fail(*args, **kwargs):
        raise vi.VectorIndexError("private-credential private-path")
    monkeypatch.setattr(vi, "search_saved_jobs", fail)
    with TestClient(api.create_app(service)) as client:
        response = client.post("/advisory/evidence", json={"case_id": "demo_backend"})
    assert response.status_code == 503 and response.json()["reason_code"] == "INDEX_INVALID"
    assert "private" not in response.text


def test_startup_snapshot_is_immutable_and_query_path_is_pinned(service, monkeypatch):
    jobs = load_jobs()
    injected = advisory.DemoAdvisor(service.index_path, jobs)
    jobs.reverse()
    jobs[-1].skills.append("mutated")
    with pytest.raises((AttributeError, FrozenInstanceError)):
        injected.jobs[0].skills.append("mutated")
    other = vi.INDEX_DIR / "jobs-other.json"
    other.write_text("bad", encoding="utf-8")
    monkeypatch.setattr(Path, "glob", lambda *a, **k: pytest.fail("No latest-generation discovery"))
    monkeypatch.setattr(Path, "iterdir", lambda *a, **k: pytest.fail("No directory scan"))
    assert injected.index_path.is_absolute()
    assert injected.evidence("demo_backend", 3)["status"] == "OK"
    with pytest.raises(vi.VectorIndexError, match="INDEX_PROFILE_MISMATCH"):
        advisory.DemoAdvisor(service.index_path, service.jobs, vi.IndexProfile("fake-controls", 3, "other"))


def test_preparation_reuses_existing_generation_and_never_overwrites(service, monkeypatch):
    before = sha256(service.index_path.read_bytes()).hexdigest()
    monkeypatch.setattr(vi, "save_job_index", lambda *a, **k: pytest.fail("Must reuse exact existing generation"))
    again = advisory_demo.prepare_demo_advisor()
    assert again.index_path == service.index_path and sha256(again.index_path.read_bytes()).hexdigest() == before
    service.index_path.write_text("bad", encoding="utf-8")
    with pytest.raises(vi.VectorIndexError):
        advisory_demo.prepare_demo_advisor()
    assert service.index_path.read_text(encoding="utf-8") == "bad"


def test_launcher_is_explicit_localhost_only_and_failure_does_not_bind(service, monkeypatch):
    import uvicorn

    bindings = []
    monkeypatch.setattr(uvicorn, "run", lambda application, **kwargs: bindings.append((application, kwargs)))
    advisory_demo.main()
    assert len(bindings) == 1 and bindings[0][1] == {"host": "127.0.0.1", "port": 8000}
    with TestClient(bindings[0][0]) as client:
        assert client.get("/advisory/status").json()["enabled"] is True
    service.index_path.write_text("bad", encoding="utf-8")
    with pytest.raises(SystemExit, match="Fixed fake-vector demo unavailable"):
        advisory_demo.main()
    assert len(bindings) == 1


def test_advisory_jd3_first_does_not_change_mock_screening(service):
    from app.screen import screen_one

    assert service.evidence("demo_frontend", 1)["hits"][0]["reference_jd_id"] == "JD3"
    root = Path(__file__).resolve().parents[1] / "data/synthetic/resumes"
    normal = screen_one(root / "S001_normal_backend.pdf").finalized.as_dict()
    unmatched = screen_one(root / "live_pdf_eval/unmatched.pdf").finalized.as_dict()
    assert (normal["jd_id"], normal["total"], normal["project_source"]) == ("JD1", 60, "mock")
    assert (unmatched["jd_id"], unmatched["total"]) == ("UNMATCHED", 0)


def test_excerpt_has_character_bound_even_for_long_unicode(tmp_path):
    jobs = load_jobs()
    jobs[0] = replace(jobs[0], raw_text="示" * 1500)
    index = vi.build_job_index(jobs, tuple(advisory.DOCUMENT_VECTORS.values()), profile=advisory.DEMO_PROFILE)
    service = advisory.DemoAdvisor(vi.save_job_index(index), jobs)
    hit = service.evidence("demo_backend", 1)["hits"][0]
    assert hit["evidence_excerpt"] == "示" * 1000
