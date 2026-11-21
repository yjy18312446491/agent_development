"""Fixed synthetic-vector evidence API, separate from screening and providers."""

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from app.route import JobPosting
from app import vector_index as vi


DEMO_PROFILE = vi.IndexProfile("fake-controls", 3, "advisory-demo-v1")
DOCUMENT_VECTORS = {"JD1": (1, 0, 0), "JD2": (0, 1, 0), "JD3": (0, 0, 1)}
MAX_BODY_BYTES = 4096
INVALID_REQUEST = "辅助查询不符合要求"
MODE = "fixed-fake-vectors"
INDEX_REASONS = frozenset({
    "INDEX_INVALID", "INDEX_PATH_INVALID", "INDEX_PROFILE_INVALID", "INDEX_PROFILE_MISMATCH",
    "INDEX_STALE", "INDEX_SIZE_LIMIT", "INDEX_DIMENSION_MISMATCH", "INDEX_INPUT_INVALID",
})


@dataclass(frozen=True)
class DemoCase:
    case_id: str
    title: str
    text: str
    vector: Tuple[float, ...] = field(repr=False)


# These controls demonstrate plumbing, not model semantics or relevance labels.
DEMO_CASES = (
    DemoCase("demo_backend", "合成后端样例", "合成示例：使用 Python 和 FastAPI 实现后端接口。", (1, .2, 0)),
    DemoCase("demo_junior", "合成初级样例", "合成示例：学习 Python，完成基础接口练习。", (.2, 1, 0)),
    DemoCase("demo_frontend", "合成前端样例", "合成示例：使用 TypeScript 和 React 实现页面。", (0, .2, 1)),
    DemoCase("demo_other", "合成无关样例", "合成示例：整理展览文案及活动材料，与开发岗位无关。", (1, 1, 1)),
)


def freeze_jobs(jobs) -> Tuple[JobPosting, ...]:
    """Own immutable nested skill snapshots without changing the routing model."""
    return tuple(replace(job, skills=tuple(job.skills), bonus_skills=tuple(job.bonus_skills)) for job in jobs)


def _payload(mode, case_id, status, reason_code, hits):
    return {"advisory": True, "mode": mode, "quality_conclusion": "NOT_ESTABLISHED",
            "case_id": case_id, "status": status, "reason_code": reason_code, "hits": hits}


@dataclass(frozen=True)
class DemoAdvisor:
    """One validated generation pinned at startup; requests never discover indexes."""

    index_path: Path = field(repr=False)
    jobs: Tuple[JobPosting, ...] = field(repr=False)
    profile: vi.IndexProfile = DEMO_PROFILE

    def __post_init__(self):
        if self.profile != DEMO_PROFILE:
            raise vi.VectorIndexError("INDEX_PROFILE_MISMATCH")
        jobs = freeze_jobs(self.jobs)
        # Validate the original path first so resolving cannot hide a symlink.
        vi.load_job_index(self.index_path, jobs=jobs, profile=self.profile)
        object.__setattr__(self, "index_path", Path(self.index_path).resolve())
        object.__setattr__(self, "jobs", jobs)

    def status(self):
        return {"enabled": True, "mode": MODE, "cases": [
            {"case_id": case.case_id, "title": case.title, "text": case.text} for case in DEMO_CASES
        ]}

    def evidence(self, case_id: str, top_k: int):
        case = next(case for case in DEMO_CASES if case.case_id == case_id)
        try:
            result = vi.search_saved_jobs(self.index_path, case.vector, jobs=self.jobs,
                                          profile=self.profile, enabled=True, top_k=top_k)
        except (OSError, ValueError, TypeError, RecursionError):
            return _payload(MODE, case_id, "REVIEW", "INDEX_INVALID", [])
        if result.status != "OK":
            code = result.reason_code if result.reason_code in INDEX_REASONS else "INDEX_INVALID"
            return _payload(MODE, case_id, "REVIEW", code, [])
        return _payload(MODE, case_id, "OK", "RETRIEVAL_OK", [
            {"reference_jd_id": hit.reference_jd_id, "title": hit.title,
             "similarity": hit.similarity, "evidence_excerpt": hit.text[:1000]} for hit in result.hits
        ])


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError()


async def _parse_request(request):
    if (request.query_params
            or request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json"):
        raise HTTPException(422, INVALID_REQUEST)
    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_BODY_BYTES:
                raise ValueError()
            raw.extend(chunk)
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite)
        if not isinstance(body, dict) or set(body) not in ({"case_id"}, {"case_id", "top_k"}):
            raise ValueError()
        case_id, top_k = body["case_id"], body.get("top_k", 3)
        if (not isinstance(case_id, str) or case_id not in {case.case_id for case in DEMO_CASES}
                or type(top_k) is not int or not 1 <= top_k <= 3):
            raise ValueError()
    except (ValueError, UnicodeError, RecursionError, ClientDisconnect):
        raise HTTPException(422, INVALID_REQUEST) from None
    return case_id, top_k


def advisory_router(service: Optional[DemoAdvisor] = None) -> APIRouter:
    if service is not None and type(service) is not DemoAdvisor:
        raise TypeError("Fixed fake-vector service required")
    router = APIRouter()

    @router.get("/advisory/status")
    def status():
        return service.status() if service is not None else {"enabled": False, "mode": "disabled"}

    @router.post("/advisory/evidence")
    async def evidence(request: Request):
        # Intentionally precedes Content-Type, body consumption and any disk IO.
        if service is None:
            return JSONResponse(_payload("disabled", None, "DISABLED", "RETRIEVAL_DISABLED", []), status_code=503)
        case_id, top_k = await _parse_request(request)
        result = await run_in_threadpool(service.evidence, case_id, top_k)
        return JSONResponse(result, status_code=200 if result["status"] == "OK" else 503)

    return router
