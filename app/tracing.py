"""Request-scoped trace identifiers for correlating logs, events and payloads.

A trace_id is one hex uuid per HTTP request (or an incoming ``X-Trace-Id``);
context variables carry it into threadpool work, SSE events and JSON payloads.
Data-level correlation stays with ``batch_id``; trace_id is request scope.
"""

from contextvars import ContextVar
import json
from logging import getLogger
from time import perf_counter
from uuid import uuid4

from typing import Any, Dict

_TRACE: ContextVar = ContextVar("trace_id", default="")
logger = getLogger("app.trace")


def new_trace_id() -> str:
    return uuid4().hex


def bind_trace(trace_id: str):
    return _TRACE.set(trace_id)


def current_trace_id() -> str:
    return _TRACE.get()


def log_request(trace_id: str, method: str, path: str, status: int, started: float) -> None:
    logger.info("trace=%s method=%s path=%s status=%s duration_ms=%.1f",
                trace_id, method, path, status, (perf_counter() - started) * 1000)


def traced_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the current trace id to an outbound payload when one is bound."""
    trace_id = current_trace_id()
    if trace_id:
        payload = dict(payload)
        payload["trace_id"] = trace_id
    return payload


def traced_json(data: Any) -> str:
    return json.dumps(traced_payload(data), ensure_ascii=False)
