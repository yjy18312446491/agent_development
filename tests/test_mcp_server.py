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


def test_tools_list_exposes_two_documented_tools():
    responses = exchange([{"jsonrpc": "2.0", "id": 7, "method": "tools/list"}])
    tools = {tool["name"]: tool for tool in responses[0]["result"]["tools"]}
    assert set(tools) == {"screen_resume", "list_jobs"}
    assert tools["screen_resume"]["inputSchema"]["required"] == ["pdf_paths"]


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
