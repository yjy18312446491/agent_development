"""Local HTTP entry points for offline batch screening."""

from contextlib import closing
from email.message import Message
from os import getenv
from pathlib import Path, PureWindowsPath
from shutil import copyfileobj
import sqlite3
from tempfile import TemporaryDirectory
from typing import AsyncIterator, List, Optional
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect
from starlette.responses import FileResponse

from app.advisory import DemoAdvisor, advisory_router
from app.deepseek_client import DeepSeekClient, DeepSeekConfig, DeepSeekError, PROMPT_VERSION_V2
from app.project_scorer import make_injected_scorer
from app.score import ProjectScorer, ProviderStoppedError
from app.screen import screen_many
from app.store import connect, fetch_batch, save_batch


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


def _screen_and_save(uploads: List[UploadFile], project_scorer: Optional[str] = None) -> dict:
    scorer = _make_deepseek_scorer(len(uploads)) if project_scorer == "deepseek" else None
    batch_id = uuid4().hex
    try:
        with TemporaryDirectory(prefix="screen-upload-") as directory:
            paths = []
            for upload in uploads:
                path = Path(directory) / upload.filename
                with path.open("xb") as target:
                    copyfileobj(upload.file, target)
                paths.append(path)
            records = screen_many(paths, project_scorer=scorer)

        db_path = _db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(db_path)) as conn:
            save_batch(conn, batch_id, records)
            items = fetch_batch(conn, batch_id)
    except (OSError, sqlite3.Error):
        raise HTTPException(500, "批次保存失败") from None
    return {"batch_id": batch_id, "items": items}


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/", response_class=FileResponse, include_in_schema=False)
def demo() -> FileResponse:
    return FileResponse(Path(__file__).with_name("static") / "demo.html", media_type="text/html")


@router.post(
    "/batches",
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
        "schema": {"type": "object", "required": ["files"], "properties": {
            "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
        }},
    }}}},
)
async def create_batch(request: Request, project_scorer: Optional[str] = None) -> dict:
    if project_scorer not in (None, "deepseek"):
        raise HTTPException(422, "项目评分方式不符合要求")
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
        uploads = _validated_uploads(form)
        return await run_in_threadpool(_screen_and_save, uploads, project_scorer)
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


def create_app(advisory_service: Optional[DemoAdvisor] = None) -> FastAPI:
    application = FastAPI(title="简历筛选")
    application.include_router(router)
    application.include_router(advisory_router(advisory_service))
    return application


# Default and Docker entry points always start without an advisory service.
app = create_app()
