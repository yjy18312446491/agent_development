"""Verify the local HTTP interface without a running server."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from app import api, deepseek_client
from app.deepseek_client import Completion, DeepSeekClient, DeepSeekConfig, DeepSeekError, PROMPT_VERSION_V2
from app.store import connect, fetch_batch


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"
S001 = "S001_normal_backend.pdf"
S002 = "S002_empty.pdf"
S005 = "S005_corrupt.pdf"
SCORE_FIELDS = ("skill", "project", "years", "education", "bonus")


@pytest.fixture
def local_api(tmp_path, monkeypatch):
    db = tmp_path / "store" / "results.sqlite"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setenv("SCREEN_DB_PATH", str(db))
    monkeypatch.setattr(api, "TemporaryDirectory", lambda **kwargs: TemporaryDirectory(dir=uploads, **kwargs))
    with TestClient(api.app) as client:
        yield client, db, uploads
    assert list(uploads.iterdir()) == []


def _upload(name, content=b"", content_type="application/pdf"):
    return ("files", (name, content, content_type))


def _synthetic(name, content_type="application/pdf"):
    return _upload(name, (RESUMES / name).read_bytes(), content_type)


def _assert_no_disk_paths(response, db):
    text = response.text
    for path in (str(RESUMES), str(db.parents[1]), str(db), "SCREEN_DB_PATH",
                 "screen-upload-", "E:\\\\", "C:\\\\", "/tmp"):
        assert path not in text


def test_health_and_missing_batch_do_not_create_a_database(local_api):
    client, db, _ = local_api

    health = client.get("/health")
    missing = client.get("/batches/unknown")

    assert health.status_code == 200 and health.json() == {"status": "ok"}
    assert missing.status_code == 404 and missing.json() == {"detail": "批次不存在"}
    assert not db.exists()
    _assert_no_disk_paths(missing, db)


def test_post_and_get_return_the_same_flat_ordered_records(local_api):
    client, db, uploads = local_api

    response = client.post("/batches", files=[_synthetic(S001, "text/plain"), _synthetic(S002)])

    assert response.status_code == 200
    data = response.json()
    batch_id = data["batch_id"]
    assert UUID(batch_id, version=4).hex == batch_id
    normal, empty = data["items"]
    assert [item["source_name"] for item in data["items"]] == [S001, S002]
    assert (normal["status"], normal["jd_id"], normal["tier"], normal["total"]) == ("SCORED", "JD1", "B", 60)
    assert (normal["project"], normal["project_source"]) == (0, "mock")
    assert (empty["status"], empty["reason_code"], empty["total"], empty["tier"]) == ("REVIEW", "EMPTY", -1, None)
    assert all(empty[key] is None for key in SCORE_FIELDS)
    assert all("scores" not in item for item in data["items"])

    fetched = client.get(f"/batches/{batch_id}")
    assert fetched.status_code == 200 and fetched.json() == data
    with closing(connect(db)) as conn:
        assert fetch_batch(conn, batch_id) == data["items"]
    assert not list(uploads.iterdir())
    _assert_no_disk_paths(response, db)
    _assert_no_disk_paths(fetched, db)


def test_corrupt_pdf_returns_review_and_later_file_is_scored(local_api):
    client, db, _ = local_api
    response = client.post("/batches", files=[_synthetic(S005), _synthetic(S001)])

    assert response.status_code == 200
    corrupt, normal = response.json()["items"]
    assert (corrupt["status"], corrupt["reason_code"], corrupt["total"]) == ("REVIEW", "EXTRACT_ERROR", -1)
    assert (normal["status"], normal["total"]) == ("SCORED", 60)
    _assert_no_disk_paths(response, db)


@pytest.mark.parametrize("names", [
    (), ("../a.pdf",), ("..\\a.pdf",), ("C:\\private\\a.pdf",), ("a.pdf", "A.PDF"),
    ("=cmd.pdf",), (" +cmd.pdf",), ("-cmd.pdf",), ("@cmd.pdf",), ("a.txt",),
    ("NUL.pdf",), ("a:stream.pdf",), tuple(f"{i}.pdf" for i in range(11)),
])
def test_invalid_file_lists_are_rejected_before_storage(local_api, names):
    client, db, uploads = local_api
    response = client.post("/batches", files=[_upload(name) for name in names])

    assert response.status_code == 422
    assert response.json() == {"detail": "上传文件不符合要求"}
    assert not db.exists() and not list(uploads.iterdir())
    _assert_no_disk_paths(response, db)


def test_files_over_five_mib_are_rejected_before_storage(local_api):
    client, db, _ = local_api
    response = client.post("/batches", files=[_upload("large.pdf", b"x" * (6 * 1024 * 1024))])

    assert response.status_code == 422
    assert response.json() == {"detail": "上传文件不符合要求"}
    assert not db.exists()
    _assert_no_disk_paths(response, db)


def test_accepts_exact_file_count_and_size_limits(local_api):
    client, _, _ = local_api
    content = (RESUMES / S001).read_bytes().ljust(5 * 1024 * 1024, b" ")
    files = [_upload("size-limit.pdf", content)] + [_upload(f"{i}.pdf") for i in range(9)]
    response = client.post("/batches", files=files)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 10


def test_multipart_body_limit_counts_streamed_bytes(local_api, monkeypatch):
    client, db, _ = local_api
    monkeypatch.setattr(api, "MAX_REQUEST_BYTES", 64)
    body = iter([b'--x\r\nContent-Disposition: form-data; name="files"; ',
                 b'filename="a.pdf"\r\n\r\npayload\r\n--x--\r\n'])
    response = client.post("/batches", content=body, headers={"Content-Type": "multipart/form-data; boundary=x"})

    assert response.status_code == 422
    assert not db.exists()


def test_encoded_windows_path_is_rejected_before_parser_normalization_can_hide_it(local_api):
    client, db, _ = local_api
    body = (b"--x\r\nContent-Disposition: form-data; name=files; "
            b"filename*=UTF-8''C%3A%5Cprivate%5Ca.pdf\r\n\r\n\r\n--x--\r\n")
    response = client.post("/batches", content=body, headers={"Content-Type": "multipart/form-data; boundary=x"})

    assert response.status_code == 422
    assert not db.exists()
    _assert_no_disk_paths(response, db)


def test_save_failure_does_not_persist_batch_or_expose_paths(local_api, monkeypatch):
    client, db, uploads = local_api
    batch_ids = []

    def fail(conn, batch_id, records):
        batch_ids.append(batch_id)
        raise sqlite3.OperationalError(f"Write failed at {db}")

    monkeypatch.setattr(api, "save_batch", fail)
    response = client.post("/batches", files=[_synthetic(S001)])

    assert response.status_code == 500 and response.json() == {"detail": "批次保存失败"}
    assert len(batch_ids) == 1 and not list(uploads.iterdir())
    with closing(connect(db)) as conn:
        assert fetch_batch(conn, batch_ids[0]) == []
    assert client.get(f"/batches/{batch_ids[0]}").status_code == 404
    _assert_no_disk_paths(response, db)


def _live_pdf(case_id, *, name=None):
    content = (RESUMES / "live_pdf_eval" / (case_id + ".pdf")).read_bytes()
    return _upload(name or case_id + ".pdf", content)


def _completion(request):
    body = json.loads(request.data)
    assert body["messages"][0]["content"] == deepseek_client.PROJECT_SCORER_PROMPT_V2
    payload = json.loads(body["messages"][1]["content"])
    assert set(payload) == {"snippet", "job_requirements"}
    assert "Work experience" not in payload["snippet"] and "Fictional Linden" not in payload["snippet"]
    quote = payload["snippet"].splitlines()[0]
    text = json.dumps({field: {"points": 7, "evidence": quote}
                       for field in ("relevance", "contribution", "result")})
    return Completion(text, "test-model", None, 200, 50)


def _inject_provider(monkeypatch, transport):
    clients, inputs, attempts, limits = [], [], [], []

    def configured(path):
        assert path.name == ".env"
        return DeepSeekConfig("test-credential")

    def create(config, *, max_requests, prompt_version):
        assert prompt_version == PROMPT_VERSION_V2
        client = DeepSeekClient(config, max_requests=max_requests, prompt_version=prompt_version)
        original_complete = client.complete

        def complete(snippet, requirements):
            inputs.append((snippet, requirements))
            return original_complete(snippet, requirements)

        def request(request):
            attempts.append(request)
            return transport(request)

        monkeypatch.setattr(client, "complete", complete)
        monkeypatch.setattr(client, "_request", request)
        clients.append(client)
        limits.append(max_requests)
        return client

    monkeypatch.setattr(api.DeepSeekConfig, "from_env_file", configured)
    monkeypatch.setattr(api, "DeepSeekClient", create)
    monkeypatch.setattr(deepseek_client, "sleep", lambda seconds: None)
    return clients, inputs, attempts, limits


def test_default_http_paths_do_not_load_model_configuration(local_api, monkeypatch):
    client, _, _ = local_api

    def forbidden(*args, **kwargs):
        pytest.fail("Default HTTP paths must not load model configuration or construct a client")

    monkeypatch.setattr(api.DeepSeekConfig, "from_env_file", forbidden)
    monkeypatch.setattr(api, "DeepSeekClient", forbidden)
    assert client.get("/").status_code == client.get("/health").status_code == 200
    result = client.post("/batches", files=[_synthetic(S001)]).json()
    assert result["items"][0]["total"] == 60 and result["items"][0]["project_source"] == "mock"
    assert client.get(f"/batches/{result['batch_id']}").json() == result


def test_explicit_deepseek_scores_projects_and_preserves_rules(local_api, monkeypatch):
    client, db, _ = local_api
    clients, inputs, attempts, limits = _inject_provider(monkeypatch, _completion)
    response = client.post("/batches?project_scorer=deepseek", files=[_live_pdf("personal_api")])
    assert response.status_code == 200
    row = response.json()["items"][0]
    assert (row["jd_id"], row["tier"], row["project"], row["total"]) == ("JD1", "A", 21, 81)
    assert [row[field] for field in ("skill", "years", "education", "bonus")] == [40, 15, 0, 5]
    assert row["project_source"] == "injected"
    assert len(clients) == len(inputs) == len(attempts) == 1 and limits == [3]
    assert client.get(f"/batches/{response.json()['batch_id']}").json() == response.json()
    assert "test-credential" not in response.text
    _assert_no_disk_paths(response, db)


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, None])
@pytest.mark.parametrize("stop_at", [1, 2])
def test_provider_stop_preserves_previous_results_and_local_skips(local_api, monkeypatch, status, stop_at):
    client, db, _ = local_api
    sent = []

    def transport(request):
        sent.append(request)
        if len(sent) == stop_at:
            raise DeepSeekError("HTTP_ERROR" if status is not None else "REQUEST_LIMIT", status=status)
        return _completion(request)

    clients, inputs, attempts, limits = _inject_provider(monkeypatch, transport)
    files = [_live_pdf("personal_api", name=f"scored-{i}.pdf") for i in range(3)]
    files.extend([_synthetic(S002), _live_pdf("short_gate"), _live_pdf("no_projects"), _live_pdf("unmatched")])
    response = client.post("/batches?project_scorer=deepseek", files=files)
    assert response.status_code == 200
    rows = response.json()["items"]
    assert len(clients) == 1 and len(inputs) == len(attempts) == stop_at and limits == [len(files) + 2]
    for row in rows[:stop_at - 1]:
        assert row["status"] == "SCORED" and row["total"] == 81
    assert rows[stop_at - 1]["reason_code"] == "PROJECT_SCORER_ERROR"
    for row in rows[stop_at:3]:
        assert row["reason_code"] == "PROVIDER_STOPPED"
    for row in rows[stop_at - 1:3]:
        assert row["status"] == "REVIEW" and row["total"] == -1 and row["tier"] is None
        assert row["jd_id"] == "JD1" and all(row[field] is None for field in SCORE_FIELDS)
    assert [(row["reason_code"], row["total"]) for row in rows[3:]] == [
        ("EMPTY", -1), ("TOO_SHORT", -1), ("", 60), ("", 0),
    ]
    assert client.get(f"/batches/{response.json()['batch_id']}").json() == response.json()
    with closing(connect(db)) as conn:
        assert "test-credential" not in json.dumps(fetch_batch(conn, response.json()["batch_id"]))
    _assert_no_disk_paths(response, db)


@pytest.mark.parametrize("file_count", [5, 10])
def test_provider_network_retries_reserve_later_first_attempts(local_api, monkeypatch, file_count):
    client, _, _ = local_api
    sent = []

    def transport(request):
        sent.append(request)
        if len(sent) <= 4:
            raise DeepSeekError("NETWORK_ERROR")
        return _completion(request)

    clients, inputs, attempts, limits = _inject_provider(monkeypatch, transport)
    files = [_live_pdf("personal_api", name=f"{i}.pdf") for i in range(file_count)]
    response = client.post("/batches?project_scorer=deepseek", files=files)
    assert response.status_code == 200
    rows = response.json()["items"]
    assert len(inputs) == file_count
    assert len(attempts) == clients[0].requests == file_count + 2 and limits == [file_count + 2]
    assert [row["reason_code"] for row in rows] == ["PROJECT_SCORER_ERROR"] * 2 + [""] * (file_count - 2)
    assert [row["total"] for row in rows] == [-1, -1] + [81] * (file_count - 2)


def test_invalid_model_output_does_not_stop_later_files(local_api, monkeypatch):
    client, _, _ = local_api
    sent = []

    def transport(request):
        sent.append(request)
        completion = _completion(request)
        if len(sent) == 1:
            return Completion("invalid JSON", "test-model", None, 1, 1)
        return completion

    _, inputs, attempts, _ = _inject_provider(monkeypatch, transport)
    result = client.post("/batches?project_scorer=deepseek", files=[
        _live_pdf("personal_api"), _live_pdf("team_api"),
    ])
    assert result.status_code == 200 and len(inputs) == len(attempts) == 2
    assert [(row["reason_code"], row["total"]) for row in result.json()["items"]] == [
        ("PROJECT_SCORER_ERROR", -1), ("", 81),
    ]


@pytest.mark.parametrize("query", ["other", "", "DeepSeek", "mock"])
def test_invalid_scorer_query_does_not_load_config_or_create_a_batch(local_api, monkeypatch, query):
    client, db, _ = local_api

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid query must be rejected before provider setup")

    monkeypatch.setattr(api.DeepSeekConfig, "from_env_file", forbidden)
    result = client.post("/batches", params={"project_scorer": query}, files=[_synthetic(S001)])
    assert result.status_code == 422 and result.json() == {"detail": "项目评分方式不符合要求"}
    assert not db.exists()


def test_provider_setup_failure_is_safe_and_does_not_persist(local_api, monkeypatch):
    client, db, uploads = local_api

    def fail(path):
        raise DeepSeekError(f"cannot read {db} test-credential")

    monkeypatch.setattr(api.DeepSeekConfig, "from_env_file", fail)
    result = client.post("/batches?project_scorer=deepseek", files=[_live_pdf("personal_api")])
    assert result.status_code == 503 and result.json() == {"detail": "模型未配置"}
    assert not db.exists() and list(uploads.iterdir()) == []
    _assert_no_disk_paths(result, db)


@pytest.mark.parametrize("names", [("a.txt",), ("a.pdf", "A.PDF"), tuple(f"{i}.pdf" for i in range(11))])
def test_invalid_live_upload_is_rejected_before_model_configuration(local_api, monkeypatch, names):
    client, db, uploads = local_api

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid uploads must be rejected before provider configuration")

    monkeypatch.setattr(api.DeepSeekConfig, "from_env_file", forbidden)
    result = client.post("/batches?project_scorer=deepseek", files=[_upload(name) for name in names])
    assert result.status_code == 422 and result.json() == {"detail": api.INVALID_UPLOAD}
    assert not db.exists() and list(uploads.iterdir()) == []


def test_provider_state_and_budget_are_separate_for_each_batch(local_api, monkeypatch):
    client, _, _ = local_api
    sent = []

    def transport(request):
        sent.append(request)
        if len(sent) == 1:
            raise DeepSeekError("HTTP_ERROR", status=401)
        return _completion(request)

    clients, inputs, attempts, limits = _inject_provider(monkeypatch, transport)
    first = client.post("/batches?project_scorer=deepseek", files=[_live_pdf("personal_api")])
    second = client.post("/batches?project_scorer=deepseek", files=[_live_pdf("personal_api")])
    assert first.status_code == second.status_code == 200
    assert first.json()["items"][0]["reason_code"] == "PROJECT_SCORER_ERROR"
    assert second.json()["items"][0]["total"] == 81
    assert len(clients) == len(inputs) == len(attempts) == 2 and limits == [3, 3]
    assert first.json()["batch_id"] != second.json()["batch_id"]
