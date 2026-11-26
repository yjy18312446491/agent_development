"""MCP stdio server: Mock-only screening tools, no network, no credentials."""

import io
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import urllib.request

import pytest

from app import mcp_server as mcp


@pytest.fixture(autouse=True)
def no_network_or_env(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("MCP server must not read credentials or make network requests")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def exchange(messages):
    incoming = io.BytesIO("\n".join(json.dumps(m, ensure_ascii=False) for m in messages).encode("utf-8"))
    outgoing = io.BytesIO()
    assert mcp.serve(incoming, outgoing) == 0
    return [json.loads(line) for line in outgoing.getvalue().decode("utf-8").splitlines()]


def call_tool(name, arguments=None):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": arguments or {}}}
    responses = exchange([request])
    assert len(responses) == 1
    payload = responses[0]["result"]
    return json.loads(payload["content"][0]["text"]), payload["isError"]


def test_initialize_handshake_and_notifications_get_no_reply():
    responses = exchange([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ])
    assert [r["id"] for r in responses] == [1, 2]
    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    assert responses[0]["result"]["capabilities"] == {"tools": {}}
    assert responses[0]["result"]["serverInfo"]["name"] == "resume-screening-agent"
    assert responses[1]["result"] == {}


def test_tools_list_exposes_documented_tools():
    responses = exchange([{"jsonrpc": "2.0", "id": 7, "method": "tools/list"}])
    tools = {tool["name"]: tool for tool in responses[0]["result"]["tools"]}
    assert set(tools) == {"screen_resume", "list_jobs", "get_batch", "get_batch_tasks", "export_batch_csv"}
    assert tools["screen_resume"]["inputSchema"]["required"] == ["pdf_paths"]
    assert tools["export_batch_csv"]["inputSchema"]["required"] == ["batch_id", "csv_path"]


def test_screen_resume_tool_scores_synthetic_pdf_with_default_mock():
    pdf = mcp.ROOT / "data/synthetic/resumes/S001_normal_backend.pdf"
    payload, is_error = call_tool("screen_resume", {"pdf_paths": [str(pdf)]})
    assert is_error is False
    record = payload["records"][0]
    assert record["source_name"] == "S001_normal_backend.pdf"
    assert record["status"] == "SCORED" and record["jd_id"] == "JD1"
    assert record["total"] == 60 and record["tier"] == "B" and record["project_source"] == "mock"


def test_screen_resume_tool_reports_missing_file_as_review_record():
    payload, is_error = call_tool("screen_resume", {"pdf_paths": ["tmp/definitely-missing.pdf"]})
    assert is_error is False
    record = payload["records"][0]
    assert record["status"] == "REVIEW" and record["total"] == -1
    assert record["reason_code"] == "EXTRACT_ERROR"


def test_tool_errors_never_crash_the_server():
    payload, is_error = call_tool("screen_resume", {"pdf_paths": []})
    assert is_error is True and "pdf_paths" in payload["error"]
    payload, is_error = call_tool("screen_resume", {"pdf_paths": [42]})
    assert is_error is True
    payload, is_error = call_tool("screen_resume", None)
    assert is_error is True
    payload, is_error = call_tool("no_such_tool")
    assert is_error is True and "no_such_tool" in payload["error"]
    payload, is_error = call_tool("list_jobs", {"unexpected": 1})
    assert is_error is True


def test_list_jobs_returns_frozen_three_jobs():
    payload, is_error = call_tool("list_jobs")
    assert is_error is False
    assert [job["jd_id"] for job in payload["jobs"]] == ["JD1", "JD2", "JD3"]
    assert all(job["skills"] for job in payload["jobs"])


@pytest.fixture
def seeded_batch(tmp_path, monkeypatch):
    from contextlib import closing as _closing

    from app.screen import BatchRecord, screen_one
    from app.store import connect, create_task, mark_task_done, save_batch

    db = tmp_path / "results.sqlite"
    monkeypatch.setenv("SCREEN_DB_PATH", str(db))
    batch_id = "b" * 32
    pdf = mcp.ROOT / "data/synthetic/resumes/S001_normal_backend.pdf"
    with _closing(connect(db)) as conn:
        create_task(conn, batch_id, ["S001_normal_backend.pdf"])
        save_batch(conn, batch_id, [
            BatchRecord("S001_normal_backend.pdf", screen_one(pdf).finalized)])
        mark_task_done(conn, batch_id, "S001_normal_backend.pdf")
    return batch_id, tmp_path


def test_get_batch_reads_stored_results(seeded_batch):
    batch_id, _ = seeded_batch
    payload, is_error = call_tool("get_batch", {"batch_id": batch_id})
    assert is_error is False
    assert payload["items"][0]["total"] == 60 and payload["items"][0]["tier"] == "B"

    missing, is_error = call_tool("get_batch", {"batch_id": "unknown"})
    assert is_error is True and missing["error"] == "批次不存在"


def test_get_batch_tasks_reports_progress(seeded_batch):
    batch_id, _ = seeded_batch
    payload, is_error = call_tool("get_batch_tasks", {"batch_id": batch_id})
    assert is_error is False
    assert (payload["total"], payload["done"], payload["complete"]) == (1, 1, True)
    assert payload["tasks"][0]["status"] == "done"


def test_export_batch_csv_writes_new_file_only(seeded_batch):
    batch_id, tmp_path = seeded_batch
    target = tmp_path / "export.csv"
    payload, is_error = call_tool("export_batch_csv", {"batch_id": batch_id, "csv_path": str(target)})
    assert is_error is False
    assert payload["rows"] == 1 and target.exists()
    header = target.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.startswith("batch_id,source_name")

    again, is_error = call_tool("export_batch_csv", {"batch_id": batch_id, "csv_path": str(target)})
    assert is_error is True and "新 .csv" in again["error"]

    bad_suffix, is_error = call_tool("export_batch_csv", {"batch_id": batch_id, "csv_path": str(tmp_path / "x.txt")})
    assert is_error is True
    missing_dir, is_error = call_tool(
        "export_batch_csv", {"batch_id": batch_id, "csv_path": str(tmp_path / "nope" / "x.csv")})
    assert is_error is True


def test_batch_tools_report_missing_database(monkeypatch, tmp_path):
    monkeypatch.setenv("SCREEN_DB_PATH", str(tmp_path / "absent.sqlite"))
    payload, is_error = call_tool("get_batch", {"batch_id": "whatever"})
    assert is_error is True and "尚未创建" in payload["error"]


def test_protocol_errors_and_garbage_lines_are_isolated():
    responses = exchange([
        {"jsonrpc": "2.0", "id": 3, "method": "no/such-method"},
        {"jsonrpc": "1.0", "id": 4, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},  # notification: silent
    ])
    assert [r["id"] for r in responses] == [3, 4]
    assert responses[0]["error"]["code"] == -32601
    assert responses[1]["error"]["code"] == -32600
    incoming = io.BytesIO(b"this is not json\n" + json.dumps(
        {"jsonrpc": "2.0", "id": 9, "method": "ping"}).encode() + b"\n")
    outgoing = io.BytesIO()
    assert mcp.serve(incoming, outgoing) == 0
    replies = [json.loads(line) for line in outgoing.getvalue().decode("utf-8").splitlines()]
    assert [r["id"] for r in replies] == [None, 9]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["result"] == {}


def test_main_rejects_arguments_and_serve_stops_at_eof(monkeypatch):
    assert mcp.main(["--help"]) == 2
    monkeypatch.setattr(mcp.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"")))
    monkeypatch.setattr(mcp.sys, "stdout", SimpleNamespace(buffer=io.BytesIO()))
    assert mcp.main() == 0
    assert mcp.serve(io.BytesIO(b""), io.BytesIO()) == 0


def test_server_screening_matches_fastapi_default_record_shape():
    from app.screen import screen_one

    pdf = mcp.ROOT / "data/synthetic/resumes/S001_normal_backend.pdf"
    payload, _ = call_tool("screen_resume", {"pdf_paths": [str(pdf)]})
    direct = screen_one(pdf).finalized.as_dict()
    record = {key: value for key, value in payload["records"][0].items() if key != "source_name"}
    assert record == direct
