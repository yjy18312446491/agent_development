"""Verify persistence and CSV output using temporary files."""

from contextlib import closing
import csv
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from app.screen import screen_many
from app.store import connect, export_csv, fetch_batch, save_batch


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"
NORMAL = RESUMES / "S001_normal_backend.pdf"
EMPTY = RESUMES / "S002_empty.pdf"
SCORE_COLUMNS = ("skill", "project", "years", "education", "bonus")


@pytest.fixture
def db(tmp_path):
    with closing(connect(tmp_path / "screen-results.db")) as conn:
        yield conn


@pytest.fixture
def batch():
    pytest.importorskip("fitz")
    return screen_many([NORMAL, EMPTY])


def test_screening_batch_persists_in_order_across_connections(tmp_path, batch):
    path = tmp_path / "reopen.sqlite"
    with closing(connect(path)) as conn:
        assert save_batch(conn, "t1", batch) == 2

    with closing(connect(path)) as conn:
        assert fetch_batch(conn, "other-batch") == []
        normal, empty = fetch_batch(conn, "t1")

    for row, record in zip((normal, empty), batch):
        expected = record.as_dict()
        scores = expected.pop("scores")
        expected.update(scores if scores is not None else dict.fromkeys(SCORE_COLUMNS))
        assert row == {"batch_id": "t1", **expected}
    assert (normal["status"], normal["tier"], normal["total"], normal["project"], normal["project_source"]) == (
        "SCORED", "B", 60, 0, "mock",
    )
    assert (empty["status"], empty["reason_code"], empty["total"], empty["tier"]) == (
        "REVIEW", "EMPTY", -1, None,
    )
    assert all(empty[column] is None for column in SCORE_COLUMNS)


def test_csv_round_trip_preserves_header_paths_and_blank_review_fields(db, batch, tmp_path):
    assert save_batch(db, "t1", batch) == 2
    path = tmp_path / "screen-results.csv"

    export_csv(fetch_batch(db, "t1"), path)

    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        normal, empty = list(reader)
        assert reader.fieldnames == [
            "batch_id", "source_name", "extract_status", "guard_status", "status",
            "jd_id", "jd_title", "tier", "total", *SCORE_COLUMNS,
            "project_source", "reason_code", "reason",
        ]
    assert (normal["source_name"], normal["tier"], normal["total"], normal["project"]) == (
        NORMAL.name, "B", "60", "0",
    )
    assert normal["jd_title"] == "后端开发工程师（中级）"
    assert (empty["source_name"], empty["reason_code"], empty["total"], empty["tier"]) == (
        EMPTY.name, "EMPTY", "-1", "",
    )
    assert all(empty[column] == "" for column in SCORE_COLUMNS)
    assert str(RESUMES) not in path.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("batch_id", ["", " \t\n"])
def test_blank_batch_id_is_rejected(db, batch, batch_id):
    with pytest.raises(ValueError, match="batch_id"):
        save_batch(db, batch_id, batch)
    assert db.execute("SELECT count(*) FROM screen_results").fetchone()[0] == 0


def test_duplicate_rejects_the_entire_write_without_overwriting(db, batch):
    normal, empty = batch
    assert save_batch(db, "t1", [normal]) == 1
    original = fetch_batch(db, "t1")

    with pytest.raises(sqlite3.IntegrityError):
        save_batch(db, "t1", [empty, normal])

    assert fetch_batch(db, "t1") == original
    assert save_batch(db, "t1", [empty]) == 1
    assert [row["source_name"] for row in fetch_batch(db, "t1")] == [NORMAL.name, EMPTY.name]


def test_batch_id_is_treated_as_data_and_batches_are_isolated(db, batch):
    unusual = "team's batch;2026"
    assert save_batch(db, unusual, batch[:1]) == 1
    assert save_batch(db, "t1", batch[:1]) == 1

    assert [row["batch_id"] for row in fetch_batch(db, unusual)] == [unusual]
    assert [row["batch_id"] for row in fetch_batch(db, "t1")] == ["t1"]


@pytest.mark.parametrize("source_name", ["/other/resume.pdf", "C:\\private\\resume.pdf"])
def test_manually_constructed_record_cannot_persist_a_path(db, batch, source_name):
    with pytest.raises(ValueError, match="source_name"):
        save_batch(db, "t1", [replace(batch[0], source_name=source_name)])
    assert fetch_batch(db, "t1") == []


def test_empty_batch_and_csv_keep_the_fixed_shape(db, tmp_path):
    path = tmp_path / "empty.csv"

    assert save_batch(db, "empty", []) == 0
    export_csv(fetch_batch(db, "empty"), path)

    with path.open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.reader(stream)) == [[
            "batch_id", "source_name", "extract_status", "guard_status", "status",
            "jd_id", "jd_title", "tier", "total", *SCORE_COLUMNS,
            "project_source", "reason_code", "reason",
        ]]
