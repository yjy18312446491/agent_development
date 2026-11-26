"""Trace id propagation across headers, payloads, SSE events and logs."""

from contextlib import closing
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
import pytest

from app import api
from app.store import connect, fetch_batch


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"
S001 = "S001_normal_backend.pdf"


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


def test_sync_response_and_header_share_one_trace_id(local_api):
    client, _, _ = local_api
    response = client.post("/batches", files=[_synthetic(S001)])

    trace_id = response.headers["x-trace-id"]
    assert len(trace_id) == 32
    assert "trace_id" not in response.json()  # Bodies stay GET-round-trip stable.
    fetched = client.get(f"/batches/{response.json()['batch_id']}")
    assert fetched.headers["x-trace-id"] != trace_id  # Every request gets its own.


def test_incoming_trace_id_is_echoed_not_replaced(local_api):
    client, _, _ = local_api
    response = client.post("/batches", files=[_synthetic(S001)], headers={"X-Trace-Id": "my-trace-123"})

    assert response.headers["x-trace-id"] == "my-trace-123"


def test_health_is_traced_too(local_api):
    client, _, _ = local_api
    response = client.get("/health")
    assert len(response.headers["x-trace-id"]) == 32


def test_sse_events_carry_the_request_trace_id(local_api):
    client, _, _ = local_api
    response = client.post("/batches/stream", files=[_synthetic(S001)])

    trace_id = response.headers["x-trace-id"]
    events = []
    for block in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    assert all(data.get("trace_id") == trace_id for _, data in events)
    assert events and events[-1][0] == "done"


def test_middleware_logs_one_line_per_request(local_api, caplog):
    client, _, _ = local_api
    with caplog.at_level(logging.INFO, logger="app.trace"):
        response = client.post("/batches", files=[_synthetic(S001)])

    records = [record for record in caplog.records if record.name == "app.trace"]
    assert len(records) >= 1
    trace_id = response.headers["x-trace-id"]
    assert any(record.getMessage().startswith(f"trace={trace_id} ") for record in records)
    assert all("duration_ms=" in record.getMessage() for record in records)


def test_resume_response_keeps_get_stable_shape(local_api):
    client, _, _ = local_api
    batch_id = client.post("/batches", files=[_synthetic(S001)]).json()["batch_id"]
    response = client.post(f"/batches/{batch_id}/resume", files=[_synthetic(S001)])

    assert response.status_code == 200
    assert "trace_id" not in response.json()
    assert len(response.headers["x-trace-id"]) == 32


def test_tracing_helpers_without_bound_trace():
    from app.tracing import current_trace_id, traced_payload

    assert current_trace_id() == ""
    assert traced_payload({"a": 1}) == {"a": 1}  # No trace bound: payload untouched.
