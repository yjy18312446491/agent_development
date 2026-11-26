"""Local HTTP entry points for offline batch screening."""

from contextlib import closing
from email.message import Message
from os import getenv
from pathlib import Path, PureWindowsPath
from shutil import copyfileobj
from time import perf_counter
import json
import sqlite3
from tempfile import TemporaryDirectory
from typing import AsyncIterator, List, Optional
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect
from starlette.responses import FileResponse, Response, StreamingResponse

from app.advisory import DemoAdvisor, advisory_router
from app.batch_advisory import attach_embedding_advisory
from app.deepseek_client import DeepSeekClient, DeepSeekConfig, DeepSeekError, PROMPT_VERSION_V2
from app.embedding_client import EmbeddingClient, EmbeddingConfig, EmbeddingError
from app.project_scorer import make_injected_scorer
from app.score import ProjectScorer, ProviderStoppedError
from app.screen import BatchRecord, screen_many, screen_one
from app.store import connect, create_task, fetch_batch, fetch_record, fetch_tasks, mark_task_done, save_batch
from app.tracing import bind_trace, current_trace_id, log_request, new_trace_id, traced_payload


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "tmp" / "screen-results.sqlite"
MAX_FILES = 10
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_FILES * MAX_FILE_BYTES + 1024 * 1024  # Multipart framing overhead.
INVALID_UPLOAD = "上传文件不符合要求"
# Match the evaluator's terminal HTTP statuses within the app-only runtime image.
STOP_STATUSES = (400, 401, 402, 403, 404)

router = APIRouter()


def _db_path() -> Path:
    return Path(getenv("SCREEN_DB_PATH") or DEFAULT_DB_PATH)


async def _bounded_body(request: Request) -> AsyncIterator[bytes]:
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > MAX_REQUEST_BYTES:
                raise MultiPartException(INVALID_UPLOAD)
            yield chunk
    except ClientDisconnect:
        raise MultiPartException(INVALID_UPLOAD) from None


def _validated_uploads(form: FormData) -> List[UploadFile]:
    items = form.multi_items()
    if not 1 <= len(items) <= MAX_FILES:
        raise HTTPException(422, INVALID_UPLOAD)

    uploads = []
    seen = set()
    for field, upload in items:
        if field != "files" or not isinstance(upload, UploadFile):
            raise HTTPException(422, INVALID_UPLOAD)

        name = upload.filename or ""
        folded = name.casefold()
        # The multipart parser can strip Windows paths, including encoded ones.
        disposition = Message()
        disposition["Content-Disposition"] = upload.headers.get("content-disposition", "")
        original_name = disposition.get_filename() or ""
        if (
            not name.isprintable()
            or any(char in name for char in '/\\<>:"|?*')
            or "/" in original_name
            or "\\" in original_name
            or name != Path(name).name
            or Path(name).suffix.casefold() != ".pdf"
            or PureWindowsPath(name).is_reserved()
            or name.strip().startswith(("=", "+", "-", "@"))
            or folded in seen
            or upload.size is None
            or upload.size > MAX_FILE_BYTES
        ):
            raise HTTPException(422, INVALID_UPLOAD)
        seen.add(folded)
        uploads.append(upload)
    return uploads


async def _parse_upload_form(request: Request) -> tuple:
    """Shared multipart gate; returns (uploads, form) so callers close form late."""
    if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "multipart/form-data":
        raise HTTPException(422, INVALID_UPLOAD)
    parser = MultiPartParser(request.headers, _bounded_body(request), max_files=MAX_FILES, max_fields=0)
    try:
        form = await parser.parse()
    except (MultiPartException, ValueError):
        raise HTTPException(422, INVALID_UPLOAD) from None
    except OSError:
        raise HTTPException(500, "批次保存失败") from None
    try:
        return _validated_uploads(form), form
    except BaseException:
        await form.close()
        raise


def _make_deepseek_scorer(file_count: int) -> ProjectScorer:
    request_limit = file_count + 2
    try:
        config = DeepSeekConfig.from_env_file(ROOT / ".env")
        client = DeepSeekClient(config, max_requests=request_limit, prompt_version=PROMPT_VERSION_V2)
    except DeepSeekError:
        raise HTTPException(503, "模型未配置") from None

    stopped = False
    retries_left = 2

    def request(snippet: str, requirements: str) -> str:
        nonlocal stopped, retries_left
        if stopped:
            raise ProviderStoppedError()
        before = client.requests
        client.max_requests = min(request_limit, before + 1 + int(retries_left > 0))
        try:
            return client.complete(snippet, requirements).text
        except DeepSeekError as error:
            if error.status in STOP_STATUSES or error.code == "REQUEST_LIMIT":
                stopped = True
            raise
        finally:
            retries_left -= max(0, client.requests - before - 1)

    return make_injected_scorer(request)


def _screen_and_save(
    uploads: List[UploadFile],
    project_scorer: Optional[str] = None,
    advisory: Optional[str] = None,
) -> dict:
    scorer = _make_deepseek_scorer(len(uploads)) if project_scorer == "deepseek" else None
    embed_config = None
    if advisory == "embedding":
        try:
            embed_config = EmbeddingConfig.from_env_file(ROOT / ".env")
        except EmbeddingError:
            raise HTTPException(503, "辅助检索未配置") from None
    batch_id = uuid4().hex
    advisory_payload = None
    try:
        db_path = _db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(db_path)) as conn:
            create_task(conn, batch_id, [upload.filename for upload in uploads])
        with TemporaryDirectory(prefix="screen-upload-") as directory:
            paths = []
            for upload in uploads:
                path = Path(directory) / upload.filename
                with path.open("xb") as target:
                    copyfileobj(upload.file, target)
                paths.append(path)
            # Real scorers keep budget counters sequential; Mock screens concurrently.
            workers = 1 if scorer is not None else 4
            records = screen_many(paths, project_scorer=scorer, max_workers=workers)
            for record in records:
                with closing(connect(db_path)) as conn:
                    save_batch(conn, batch_id, [record])
                    mark_task_done(conn, batch_id, record.source_name)
            if embed_config is not None:
                advisory_payload = attach_embedding_advisory(paths, EmbeddingClient(embed_config))

        with closing(connect(db_path)) as conn:
            items = fetch_batch(conn, batch_id)
    except (OSError, sqlite3.Error):
        raise HTTPException(500, "批次保存失败") from None
    # trace_id stays in headers/logs/SSE only; bodies must equal GET round-trips.
    payload = {"batch_id": batch_id, "items": items}
    if advisory_payload is not None:
        payload["advisory"] = advisory_payload
    return payload


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/", response_class=FileResponse, include_in_schema=False)
def demo() -> FileResponse:
    return FileResponse(Path(__file__).with_name("static") / "demo.html", media_type="text/html")


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@router.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
def chrome_devtools_probe() -> Response:
    return Response(status_code=204)


@router.post(
    "/batches",
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
        "schema": {"type": "object", "required": ["files"], "properties": {
            "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
        }},
    }}}},
)
async def create_batch(
    request: Request,
    project_scorer: Optional[str] = None,
    advisory: Optional[str] = None,
) -> dict:
    if project_scorer not in (None, "deepseek"):
        raise HTTPException(422, "项目评分方式不符合要求")
    if advisory not in (None, "embedding"):
        raise HTTPException(422, "辅助检索方式不符合要求")
    uploads, form = await _parse_upload_form(request)
    try:
        return await run_in_threadpool(_screen_and_save, uploads, project_scorer, advisory)
    finally:
        await form.close()


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str) -> dict:
    db_path = _db_path()
    if not db_path.is_file():
        raise HTTPException(404, "批次不存在")
    try:
        with closing(connect(db_path)) as conn:
            items = fetch_batch(conn, batch_id)
    except sqlite3.Error:
        raise HTTPException(500, "批次读取失败") from None
    if not items:
        raise HTTPException(404, "批次不存在")
    return {"batch_id": batch_id, "items": items}


@router.get("/batches/{batch_id}/tasks")
def get_batch_tasks(batch_id: str) -> dict:
    db_path = _db_path()
    if not db_path.is_file():
        raise HTTPException(404, "批次不存在")
    try:
        with closing(connect(db_path)) as conn:
            tasks = fetch_tasks(conn, batch_id)
    except sqlite3.Error:
        raise HTTPException(500, "批次读取失败") from None
    if not tasks:
        raise HTTPException(404, "批次不存在")
    done = [task for task in tasks if task["status"] == "done"]
    return {
        "batch_id": batch_id, "total": len(tasks), "done": len(done),
        "complete": len(done) == len(tasks), "tasks": tasks,
    }


def _resume_and_save(batch_id: str, uploads: List[UploadFile], project_scorer: Optional[str]) -> dict:
    db_path = _db_path()
    try:
        with closing(connect(db_path)) as conn:
            tasks = fetch_tasks(conn, batch_id)
    except sqlite3.Error:
        raise HTTPException(500, "批次读取失败") from None
    if not tasks:
        raise HTTPException(404, "批次不存在")
    known = {task["source_name"] for task in tasks}
    done = {task["source_name"] for task in tasks if task["status"] == "done"}
    uploaded = [upload.filename for upload in uploads]
    if any(name not in known for name in uploaded):
        raise HTTPException(422, "批次任务不含该文件名")

    resumed, reused = [], []
    try:
        pending_names = []
        for name in uploaded:
            if name in done:
                with closing(connect(db_path)) as conn:
                    if fetch_record(conn, batch_id, name) is not None:
                        reused.append(name)
                        continue
            pending_names.append(name)
        scorer = _make_deepseek_scorer(len(pending_names)) if project_scorer == "deepseek" else None
        with TemporaryDirectory(prefix="screen-upload-") as directory:
            paths = []
            for upload in uploads:
                if upload.filename not in pending_names:
                    continue
                path = Path(directory) / upload.filename
                with path.open("xb") as target:
                    copyfileobj(upload.file, target)
                paths.append(path)
            for path in paths:
                result = screen_one(path, project_scorer=scorer)
                record = BatchRecord(Path(path).name, result.finalized)
                with closing(connect(db_path)) as conn:
                    save_batch(conn, batch_id, [record])
                    mark_task_done(conn, batch_id, record.source_name)
                resumed.append(record.source_name)
        with closing(connect(db_path)) as conn:
            items = fetch_batch(conn, batch_id)
    except (OSError, sqlite3.Error):
        raise HTTPException(500, "批次保存失败") from None
    return {"batch_id": batch_id, "items": items, "resumed": resumed, "reused": reused}


@router.post(
    "/batches/{batch_id}/resume",
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
        "schema": {"type": "object", "required": ["files"], "properties": {
            "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
        }},
    }}}},
)
async def resume_batch(
    batch_id: str,
    request: Request,
    project_scorer: Optional[str] = None,
) -> dict:
    """Finish an interrupted batch: only task rows not yet done are re-screened."""
    if project_scorer not in (None, "deepseek"):
        raise HTTPException(422, "项目评分方式不符合要求")
    uploads, form = await _parse_upload_form(request)
    try:
        return await run_in_threadpool(_resume_and_save, batch_id, uploads, project_scorer)
    finally:
        await form.close()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(traced_payload(data), ensure_ascii=False)}\n\n"


@router.post(
    "/batches/stream",
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
        "schema": {"type": "object", "required": ["files"], "properties": {
            "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
        }},
    }}}},
)
async def create_batch_stream(
    request: Request,
    project_scorer: Optional[str] = None,
    advisory: Optional[str] = None,
) -> StreamingResponse:
    """Screen uploaded PDFs and push per-file progress as server-sent events.

    Emits ``progress`` after each file, then ``done`` with the persisted batch,
    or ``error`` with a fixed code and no private detail.
    """
    if project_scorer not in (None, "deepseek"):
        raise HTTPException(422, "项目评分方式不符合要求")
    if advisory is not None:
        raise HTTPException(422, "流式端点不支持辅助检索")
    uploads, form = await _parse_upload_form(request)
    try:
        scorer = _make_deepseek_scorer(len(uploads)) if project_scorer == "deepseek" else None

        async def _stream() -> AsyncIterator[str]:
            try:
                records: List[BatchRecord] = []
                try:
                    with TemporaryDirectory(prefix="screen-upload-") as directory:
                        paths = []
                        for upload in uploads:
                            path = Path(directory) / upload.filename
                            with path.open("xb") as target:
                                copyfileobj(upload.file, target)
                            paths.append(path)
                        total = len(paths)
                        for index, path in enumerate(paths):
                            result = await run_in_threadpool(screen_one, path, None, project_scorer=scorer)
                            records.append(BatchRecord(Path(path).name, result.finalized))
                            yield _sse("progress", {
                                "index": index, "total": total, "source_name": Path(path).name,
                                "status": result.score.status, "jd_id": result.score.jd_id,
                                "score": result.score.total,
                            })
                except OSError:
                    yield _sse("error", {"code": "UPLOAD_WRITE_ERROR", "message": INVALID_UPLOAD})
                    return
                except Exception:
                    yield _sse("error", {"code": "BATCH_STREAM_ERROR", "message": "流式筛选失败"})
                    return
                batch_id = uuid4().hex
                db_path = _db_path()

                def _save() -> List[dict]:
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    with closing(connect(db_path)) as conn:
                        save_batch(conn, batch_id, records)
                        return fetch_batch(conn, batch_id)

                try:
                    items = await run_in_threadpool(_save)
                except (OSError, sqlite3.Error):
                    yield _sse("error", {"code": "BATCH_SAVE_ERROR", "message": "批次保存失败"})
                    return
                yield _sse("done", {"batch_id": batch_id, "items": items})
            finally:
                await form.close()

        return StreamingResponse(_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except BaseException:
        await form.close()
        raise


def create_app(advisory_service: Optional[DemoAdvisor] = None) -> FastAPI:
    application = FastAPI(title="简历筛选")
    application.include_router(router)
    application.include_router(advisory_router(advisory_service))

    @application.middleware("http")
    async def trace_middleware(request: Request, call_next):
        trace_id = request.headers.get("x-trace-id", "").strip() or new_trace_id()
        token = bind_trace(trace_id)
        started = perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            log_request(trace_id, request.method, request.url.path, status, started)
        response.headers["X-Trace-Id"] = trace_id
        return response

    return application


# Default and Docker entry points always start without an advisory service.
app = create_app()
