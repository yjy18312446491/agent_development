"""Local MCP server over stdio: exposes Mock screening and job listing as tools.

Newline-delimited JSON-RPC 2.0 (MCP stdio transport), Python 3.9 stdlib only.
No network, no credentials, default Mock scorer; results match app.screen.
"""

import json
import sys
from io import BufferedIOBase
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from app.route import load_jobs
from app.screen import screen_many


ROOT = Path(__file__).resolve().parents[1]
JOBS_FILE = ROOT / "data/synthetic/jobs/jobs.json"
PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "resume-screening-agent", "version": "0.1.0"}

_TOOLS = [
    {
        "name": "screen_resume",
        "description": "对一份或多份本地 PDF 简历运行默认 Mock 筛选，返回每份的档位、岗位、总分与复核原因码。"
                       "不调用真实模型、不读取凭据；结果与 FastAPI 默认路径一致。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pdf_paths": {
                    "type": "array", "items": {"type": "string"}, "minItems": 1,
                    "description": "本地 PDF 文件路径列表（相对当前目录或绝对路径）",
                },
            },
            "required": ["pdf_paths"],
        },
    },
    {
        "name": "list_jobs",
        "description": "列出当前筛选使用的岗位（JD1–JD3）及年限与技能要求，只读。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _tool_screen_resume(arguments: Dict[str, Any]) -> Dict[str, Any]:
    paths = arguments.get("pdf_paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p.strip() for p in paths):
        raise ValueError("pdf_paths 必须是非空字符串数组")
    records = screen_many([Path(p) for p in paths])
    return {"records": [record.as_dict() for record in records]}


def _tool_list_jobs(arguments: Dict[str, Any]) -> Dict[str, Any]:
    if arguments:
        raise ValueError("list_jobs 不接受参数")
    jobs = load_jobs(JOBS_FILE.parent)
    return {"jobs": [{"jd_id": job.jd_id, "title": job.title, "min_years": job.min_years,
                      "max_years": job.max_years, "skills": list(job.skills),
                      "bonus_skills": list(job.bonus_skills)} for job in jobs]}


_TOOL_HANDLERS = {"screen_resume": _tool_screen_resume, "list_jobs": _tool_list_jobs}


def _result(request_id: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_text_result(payload: Any, *, is_error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "isError": is_error}


def _dispatch(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC message; None means no response (notification)."""
    request_id = request.get("id") if isinstance(request, dict) else None
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or "method" not in request:
        if request_id is None:  # Unrecoverable or notification-shaped: stay silent.
            return None
        return _error(request_id, -32600, "INVALID_REQUEST")
    method = request["method"]
    if request_id is None:  # JSON-RPC 2.0 notification: never answered.
        return None
    try:
        if method == "initialize":
            return _result(request_id, {"protocolVersion": PROTOCOL_VERSION,
                                        "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO})
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": _TOOLS})
        if method == "tools/call":
            params = request.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return _result(request_id, _tool_text_result({"error": "params.name 必须是工具名"}, is_error=True))
            handler = _TOOL_HANDLERS.get(params["name"])
            if handler is None:
                return _result(request_id, _tool_text_result({"error": "未知工具: " + params["name"]}, is_error=True))
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                return _result(request_id, _tool_text_result({"error": "arguments 必须是对象"}, is_error=True))
            try:
                return _result(request_id, _tool_text_result(handler(arguments)))
            except (ValueError, TypeError) as error:
                return _result(request_id, _tool_text_result({"error": str(error)}, is_error=True))
        return _error(request_id, -32601, "METHOD_NOT_FOUND")
    except Exception:
        return _error(request_id, -32603, "INTERNAL_ERROR")


def serve(read_stream, write_stream) -> int:
    """Serve newline-delimited JSON-RPC until EOF; returns the process exit code."""
    while True:
        line = read_stream.readline()
        if not line:
            return 0
        text = line.decode("utf-8", errors="strict") if isinstance(line, bytes) else line
        if not text.strip():
            continue
        try:
            request = json.loads(text)
        except (ValueError, UnicodeError):
            response = _error(None, -32700, "PARSE_ERROR")
        else:
            response = _dispatch(request)
        if response is not None:
            write_stream.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
            write_stream.flush()


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv:
        print("MCP server takes no arguments; it reads stdio JSON-RPC.", file=sys.stderr)
        return 2
    return serve(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
