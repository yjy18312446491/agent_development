"""Per-file task persistence and resume for interrupted batches."""

from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
import pytest

from app import api, screen
from app.store import connect, create_task, fetch_record, fetch_tasks, mark_task_done


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


def test_task_rows_reject_blank_and_path_like_names():
    import sqlite3

    with closing(connect(":memory:")) as conn:
        assert create_task(conn, "b1", ["a.pdf"]) == 1
        with pytest.raises(ValueError):
            create_task(conn, "b1", [""])
        with pytest.raises(ValueError):
            create_task(conn, "b1", ["dir/a.pdf"])
        with pytest.raises(ValueError):
            create_task(conn, "b1", ["C:\\a.pdf"])
        mark_task_done(conn, "b1", "a.pdf")
        with pytest.raises(ValueError):
            mark_task_done(conn, "b1", "missing.pdf")
        assert fetch_record(conn, "b1", "a.pdf") is None
        assert [task["status"] for task in fetch_tasks(conn, "b1")] == ["done"]


def test_sync_batch_registers_and_completes_tasks(local_api):
    client, _, _ = local_api
    batch_id = client.post("/batches", files=[_synthetic(S001), _synthetic(S002)]).json()["batch_id"]

    state = client.get(f"/batches/{batch_id}/tasks").json()
    assert (state["total"], state["done"], state["complete"]) == (2, 2, True)
    assert [task["source_name"] for task in state["tasks"]] == [S001, S002]
    assert [task["status"] for task in state["tasks"]] == ["done", "done"]


def test_resume_screens_only_pending_files(local_api, monkeypatch):
    client, db, _ = local_api
    real_save = api.save_batch
    failed_once = {"hit": False}

    def flaky_save(conn, batch_id, records):
        if records[0].source_name == S005 and not failed_once["hit"]:
            failed_once["hit"] = True
            raise sqlite3.OperationalError("simulated crash")
        return real_save(conn, batch_id, records)

    monkeypatch.setattr(api, "save_batch", flaky_save)
    flaky = TestClient(api.app, raise_server_exceptions=False)
    crashed = flaky.post("/batches", files=[_synthetic(S001), _synthetic(S005)])
    assert crashed.status_code == 500
    with closing(connect(db)) as conn:
        rows = conn.execute("SELECT DISTINCT batch_id FROM batch_tasks").fetchall()
        batch_id = rows[-1][0]

    state = client.get(f"/batches/{batch_id}/tasks").json()
    assert dict((t["source_name"], t["status"]) for t in state["tasks"]) == {S001: "done", S005: "pending"}
    partial = client.get(f"/batches/{batch_id}")
    assert [item["source_name"] for item in partial.json()["items"]] == [S001]

    finished = client.post(f"/batches/{batch_id}/resume", files=[_synthetic(S005)])
    assert finished.status_code == 200
    payload = finished.json()
    assert payload["resumed"] == [S005] and payload["reused"] == []
    assert [item["source_name"] for item in payload["items"]] == [S001, S005]
    assert (payload["items"][0]["total"], payload["items"][1]["status"]) == (60, "REVIEW")

    state = client.get(f"/batches/{batch_id}/tasks").json()
    assert state["complete"] is True

    again = client.post(f"/batches/{batch_id}/resume", files=[_synthetic(S001), _synthetic(S005)])
    assert again.status_code == 200
    assert again.json()["resumed"] == [] and again.json()["reused"] == [S001, S005]
    assert again.json()["items"] == payload["items"]


def test_resume_rejects_unknown_batch_and_foreign_names(local_api):
    client, _, _ = local_api
    batch_id = client.post("/batches", files=[_synthetic(S001)]).json()["batch_id"]

    missing = client.post("/batches/unknown-batch/resume", files=[_synthetic(S001)])
    foreign = client.post(f"/batches/{batch_id}/resume", files=[_synthetic(S002)])

    assert missing.status_code == 404
    assert foreign.status_code == 422 and foreign.json() == {"detail": "批次任务不含该文件名"}
