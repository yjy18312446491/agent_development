"""Server-sent events streaming endpoint for batch screening progress."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
import pytest

from app import api
from app.store import connect, fetch_batch


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"
S001 = "S001_normal_backend.pdf"
S002 = "S002_empty.pdf"
S005 = "S005_corrupt.pdf"


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


def _synthetic(name):
    return ("files", (name, (RESUMES / name).read_bytes(), "application/pdf"))


def _events(text):
    parsed = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def test_stream_emits_progress_then_done_and_persists(local_api):
    client, db, _ = local_api
    response = client.post("/batches/stream", files=[_synthetic(S001), _synthetic(S002)])

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    progress = [data for name, data in events if name == "progress"]
    done = [data for name, data in events if name == "done"]
    assert [data["code"] for name, data in events if name == "error"] == []
    assert [item["index"] for item in progress] == [0, 1]
    assert [item["source_name"] for item in progress] == [S001, S002]
    assert [item["total"] for item in progress] == [2, 2]
    assert [(item["status"], item["jd_id"], item["score"]) for item in progress] == [
        ("SCORED", "JD1", 60), ("REVIEW", "", -1)]
    assert len(done) == 1
    batch_id = done[0]["batch_id"]
    items = done[0]["items"]
    assert [item["source_name"] for item in items] == [S001, S002]
    fetched = client.get(f"/batches/{batch_id}")
    assert fetched.status_code == 200 and fetched.json()["items"] == items
    with closing(connect(db)) as conn:
        assert fetch_batch(conn, batch_id) == items
    for name, data in events:
        dumped = json.dumps(data, ensure_ascii=False)
        assert "E:\\" not in dumped and "C:\\" not in dumped and "screen-upload-" not in dumped


def test_stream_keeps_screening_after_corrupt_file(local_api):
    client, _, _ = local_api
    response = client.post("/batches/stream", files=[_synthetic(S005), _synthetic(S001)])
    progress = [data for name, data in _events(response.text) if name == "progress"]
    assert [(item["status"], item["score"]) for item in progress] == [("REVIEW", -1), ("SCORED", 60)]


def test_stream_reports_save_failure_as_error_event(local_api, monkeypatch):
    client, db, _ = local_api
    monkeypatch.setattr(api, "save_batch",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    response = client.post("/batches/stream", files=[_synthetic(S001)])

    events = _events(response.text)
    errors = [data for name, data in events if name == "error"]
    assert errors == [{"code": "BATCH_SAVE_ERROR", "message": "批次保存失败"}]
    assert [name for name, _ in events if name == "done"] == []
    assert "locked" not in response.text
    if db.exists():
        with closing(connect(db)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM screen_results").fetchone()[0] == 0


def test_stream_rejects_invalid_params(local_api):
    client, db, _ = local_api

    missing = client.post("/batches/stream")
    bad_scorer = client.post("/batches/stream", params={"project_scorer": "gpt"}, files=[_synthetic(S001)])
    advisory = client.post("/batches/stream", params={"advisory": "embedding"}, files=[_synthetic(S001)])

    assert missing.status_code == 422 and bad_scorer.status_code == 422
    assert advisory.status_code == 422 and advisory.json() == {"detail": "流式端点不支持辅助检索"}
    assert not db.exists()
