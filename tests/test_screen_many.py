"""Exercise order, serialization and failure isolation in batch screening."""

import json
from pathlib import Path
from unittest.mock import Mock, call, patch

import pytest

from app.route import load_jobs
from app.screen import screen_many, screen_one


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"
NORMAL = RESUMES / "S001_normal_backend.pdf"
EMPTY = RESUMES / "S002_empty.pdf"
CORRUPT = RESUMES / "S005_corrupt.pdf"


def test_batch_screens_all_ten_synthetic_pdfs_in_requested_order():
    pytest.importorskip("fitz")
    paths = sorted(RESUMES.glob("S*.pdf"), reverse=True)

    records = screen_many(paths)

    assert len(records) == len(paths) == 10
    assert [record.source_name for record in records] == [path.name for path in paths]
    assert [record.finalized for record in records] == [screen_one(path).finalized for path in paths]
    assert records[-1].finalized.status == "SCORED"


def test_mixed_batch_preserves_review_reasons_and_only_serializes_basenames(tmp_path):
    pytest.importorskip("fitz")
    missing = tmp_path / "private_folder" / "missing.pdf"

    records = screen_many([str(NORMAL), EMPTY, missing, CORRUPT])
    serialized = json.dumps([record.as_dict() for record in records], ensure_ascii=False)
    normal, empty, absent, corrupt = json.loads(serialized)

    assert [item["source_name"] for item in (normal, empty, absent, corrupt)] == [
        NORMAL.name, EMPTY.name, missing.name, CORRUPT.name,
    ]
    assert (normal["status"], normal["jd_id"], normal["tier"], normal["total"], normal["project_source"]) == (
        "SCORED", "JD1", "B", 60, "mock",
    )
    assert normal["scores"]["project"] == 0
    assert records[0].as_dict() == {"source_name": NORMAL.name, **records[0].finalized.as_dict()}
    for item, reason in ((empty, "EMPTY"), (absent, "EXTRACT_ERROR"), (corrupt, "EXTRACT_ERROR")):
        assert (item["status"], item["reason_code"], item["total"]) == ("REVIEW", reason, -1)
        assert item["tier"] is None and item["scores"] is None
    assert "private_folder" not in serialized
    assert str(RESUMES) not in serialized


def test_batch_passes_explicitly_empty_jobs_to_screen_one():
    pytest.importorskip("fitz")

    result = screen_many([NORMAL], jobs=[])[0].finalized

    assert (result.status, result.jd_id, result.tier, result.total) == ("SCORED", "UNMATCHED", "C", 0)


def test_injected_scorer_is_used_only_after_the_guard_passes():
    pytest.importorskip("fitz")
    scorer = Mock(return_value=21)

    normal, empty = screen_many([NORMAL, EMPTY], project_scorer=scorer)

    assert (normal.finalized.tier, normal.finalized.total, normal.finalized.project_source) == (
        "A", 81, "injected",
    )
    assert normal.finalized.scores.project == 21
    assert empty.finalized.reason_code == "EMPTY"
    scorer.assert_called_once()


def test_unexpected_exception_is_private_and_does_not_stop_later_items(tmp_path):
    pytest.importorskip("fitz")
    failing = tmp_path / "private_folder" / "fail.pdf"
    jobs = load_jobs()
    scorer = Mock(return_value=0)
    succeeded = screen_one(NORMAL, jobs)
    with patch("app.screen.screen_one", side_effect=[RuntimeError(f"Internal detail: {failing}"), succeeded]) as run:
        failed, later = screen_many([failing, NORMAL], jobs, project_scorer=scorer)

    assert run.call_args_list == [
        call(failing, jobs, project_scorer=scorer), call(NORMAL, jobs, project_scorer=scorer),
    ]
    assert failed.source_name == "fail.pdf"
    assert (failed.finalized.status, failed.finalized.total, failed.finalized.reason_code) == (
        "REVIEW", -1, "BATCH_ITEM_ERROR",
    )
    assert failed.finalized.reason == "单份筛选失败，请人工复核"
    assert failed.finalized.tier is None and failed.finalized.scores is None
    assert "private_folder" not in json.dumps(failed.as_dict())
    assert later.finalized == succeeded.finalized


def test_empty_input_does_not_call_screen_one():
    with patch("app.screen.screen_one") as run:
        assert screen_many([]) == []
    run.assert_not_called()
