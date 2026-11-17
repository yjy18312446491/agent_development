"""Verify the local HTTP interface without a running server."""

from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from app import api
from app.store import connect, fetch_batch


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"
S001 = "S001_normal_backend.pdf"
S002 = "S002_empty.pdf"
S005 = "S005_corrupt.pdf"
SCORE_FIELDS = ("skill", "project", "years", "education", "bonus")


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


def _upload(name, content=b"", content_type="application/pdf"):
    return ("files", (name, content, content_type))


def _synthetic(name, content_type="application/pdf"):
    return _upload(name, (RESUMES / name).read_bytes(), content_type)


def _assert_no_disk_paths(response, db):
    text = response.text
    for path in (str(RESUMES), str(db.parents[1]), str(db), "SCREEN_DB_PATH",
                 "screen-upload-", "E:\\\\", "C:\\\\", "/tmp"):
        assert path not in text


def test_health_and_missing_batch_do_not_create_a_database(local_api):
    client, db, _ = local_api

    health = client.get("/health")
    missing = client.get("/batches/unknown")

    assert health.status_code == 200 and health.json() == {"status": "ok"}
    assert missing.status_code == 404 and missing.json() == {"detail": "批次不存在"}
    assert not db.exists()
    _assert_no_disk_paths(missing, db)


def test_post_and_get_return_the_same_flat_ordered_records(local_api):
    client, db, uploads = local_api

    response = client.post("/batches", files=[_synthetic(S001, "text/plain"), _synthetic(S002)])

    assert response.status_code == 200
    data = response.json()
    batch_id = data["batch_id"]
    assert UUID(batch_id, version=4).hex == batch_id
    normal, empty = data["items"]
    assert [item["source_name"] for item in data["items"]] == [S001, S002]
    assert (normal["status"], normal["jd_id"], normal["tier"], normal["total"]) == ("SCORED", "JD1", "B", 60)
    assert (normal["project"], normal["project_source"]) == (0, "mock")
    assert (empty["status"], empty["reason_code"], empty["total"], empty["tier"]) == ("REVIEW", "EMPTY", -1, None)
    assert all(empty[key] is None for key in SCORE_FIELDS)
    assert all("scores" not in item for item in data["items"])

    fetched = client.get(f"/batches/{batch_id}")
    assert fetched.status_code == 200 and fetched.json() == data
    with closing(connect(db)) as conn:
        assert fetch_batch(conn, batch_id) == data["items"]
    assert not list(uploads.iterdir())
    _assert_no_disk_paths(response, db)
    _assert_no_disk_paths(fetched, db)


def test_corrupt_pdf_returns_review_and_later_file_is_scored(local_api):
    client, db, _ = local_api
    response = client.post("/batches", files=[_synthetic(S005), _synthetic(S001)])

    assert response.status_code == 200
    corrupt, normal = response.json()["items"]
    assert (corrupt["status"], corrupt["reason_code"], corrupt["total"]) == ("REVIEW", "EXTRACT_ERROR", -1)
    assert (normal["status"], normal["total"]) == ("SCORED", 60)
    _assert_no_disk_paths(response, db)


@pytest.mark.parametrize("names", [
    (), ("../a.pdf",), ("..\\a.pdf",), ("C:\\private\\a.pdf",), ("a.pdf", "A.PDF"),
    ("=cmd.pdf",), (" +cmd.pdf",), ("-cmd.pdf",), ("@cmd.pdf",), ("a.txt",),
    ("NUL.pdf",), ("a:stream.pdf",), tuple(f"{i}.pdf" for i in range(11)),
])
def test_invalid_file_lists_are_rejected_before_storage(local_api, names):
    client, db, uploads = local_api
    response = client.post("/batches", files=[_upload(name) for name in names])

    assert response.status_code == 422
    assert response.json() == {"detail": "上传文件不符合要求"}
    assert not db.exists() and not list(uploads.iterdir())
    _assert_no_disk_paths(response, db)


def test_files_over_five_mib_are_rejected_before_storage(local_api):
    client, db, _ = local_api
    response = client.post("/batches", files=[_upload("large.pdf", b"x" * (6 * 1024 * 1024))])

    assert response.status_code == 422
    assert response.json() == {"detail": "上传文件不符合要求"}
    assert not db.exists()
    _assert_no_disk_paths(response, db)


def test_accepts_exact_file_count_and_size_limits(local_api):
    client, _, _ = local_api
    content = (RESUMES / S001).read_bytes().ljust(5 * 1024 * 1024, b" ")
    files = [_upload("size-limit.pdf", content)] + [_upload(f"{i}.pdf") for i in range(9)]
    response = client.post("/batches", files=files)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 10


def test_multipart_body_limit_counts_streamed_bytes(local_api, monkeypatch):
    client, db, _ = local_api
    monkeypatch.setattr(api, "MAX_REQUEST_BYTES", 64)
    body = iter([b'--x\r\nContent-Disposition: form-data; name="files"; ',
                 b'filename="a.pdf"\r\n\r\npayload\r\n--x--\r\n'])
    response = client.post("/batches", content=body, headers={"Content-Type": "multipart/form-data; boundary=x"})

    assert response.status_code == 422
    assert not db.exists()


def test_encoded_windows_path_is_rejected_before_parser_normalization_can_hide_it(local_api):
    client, db, _ = local_api
    body = (b"--x\r\nContent-Disposition: form-data; name=files; "
            b"filename*=UTF-8''C%3A%5Cprivate%5Ca.pdf\r\n\r\n\r\n--x--\r\n")
    response = client.post("/batches", content=body, headers={"Content-Type": "multipart/form-data; boundary=x"})

    assert response.status_code == 422
    assert not db.exists()
    _assert_no_disk_paths(response, db)


def test_save_failure_does_not_persist_batch_or_expose_paths(local_api, monkeypatch):
    client, db, uploads = local_api
    batch_ids = []

    def fail(conn, batch_id, records):
        batch_ids.append(batch_id)
        raise sqlite3.OperationalError(f"Write failed at {db}")

    monkeypatch.setattr(api, "save_batch", fail)
    response = client.post("/batches", files=[_synthetic(S001)])

    assert response.status_code == 500 and response.json() == {"detail": "批次保存失败"}
    assert len(batch_ids) == 1 and not list(uploads.iterdir())
    with closing(connect(db)) as conn:
        assert fetch_batch(conn, batch_ids[0]) == []
    assert client.get(f"/batches/{batch_ids[0]}").status_code == 404
    _assert_no_disk_paths(response, db)
