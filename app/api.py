"""Local HTTP entry points for offline batch screening."""

from contextlib import closing
from email.message import Message
from os import getenv
from pathlib import Path, PureWindowsPath
from shutil import copyfileobj
import sqlite3
from tempfile import TemporaryDirectory
from typing import AsyncIterator, List
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect
from starlette.responses import FileResponse

from app.screen import screen_many
from app.store import connect, fetch_batch, save_batch


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "tmp" / "screen-results.sqlite"
MAX_FILES = 10
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_FILES * MAX_FILE_BYTES + 1024 * 1024  # Multipart framing overhead.
INVALID_UPLOAD = "上传文件不符合要求"

app = FastAPI(title="简历筛选")


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


def _screen_and_save(uploads: List[UploadFile]) -> dict:
    batch_id = uuid4().hex
    try:
        with TemporaryDirectory(prefix="screen-upload-") as directory:
            paths = []
            for upload in uploads:
                path = Path(directory) / upload.filename
                with path.open("xb") as target:
                    copyfileobj(upload.file, target)
                paths.append(path)
            records = screen_many(paths)

        db_path = _db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(db_path)) as conn:
            save_batch(conn, batch_id, records)
            items = fetch_batch(conn, batch_id)
    except (OSError, sqlite3.Error):
        raise HTTPException(500, "批次保存失败") from None
    return {"batch_id": batch_id, "items": items}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=FileResponse, include_in_schema=False)
def demo() -> FileResponse:
    return FileResponse(Path(__file__).with_name("static") / "demo.html", media_type="text/html")


@app.post(
    "/batches",
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
        "schema": {"type": "object", "required": ["files"], "properties": {
            "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
        }},
    }}}},
)
async def create_batch(request: Request) -> dict:
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
        return await run_in_threadpool(_screen_and_save, uploads)
    finally:
        await form.close()


@app.get("/batches/{batch_id}")
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
