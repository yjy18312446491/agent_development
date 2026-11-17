"""Check the output contract and the tiers exposed by screen_one."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.finalize import finalize_result
from app.route import RouteResult
from app.score import ScoreBreakdown, ScoreResult
from app.screen import screen_one


NORMAL_PDF = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes" / "S001_normal_backend.pdf"
JD1 = RouteResult("JD1", "后端开发工程师（中级）", 1.0, 1.0, 1.0, "")


@pytest.mark.parametrize(("scores", "tier"), [
    (ScoreBreakdown(0, 0, 0, 0, 0), "C"),
    (ScoreBreakdown(40, 0, 14, 0, 0), "C"),
    (ScoreBreakdown(40, 0, 15, 0, 0), "B"),
    (ScoreBreakdown(40, 24, 15, 0, 0), "B"),
    (ScoreBreakdown(40, 25, 15, 0, 0), "A"),
    (ScoreBreakdown(40, 30, 15, 10, 5), "A"),
])
def test_scored_tier_boundaries(scores, tier):
    score = ScoreResult("SCORED", "JD1", scores.total, scores, project_source="injected")

    result = finalize_result(score, JD1, extract_status="OK", guard_status="PASS")

    assert result.tier == tier
    assert (result.total, result.jd_id, result.jd_title) == (scores.total, JD1.jd_id, JD1.jd_title)
    assert result.scores == scores and result.project_source == "injected"


def test_missing_pdf_is_review_without_tier_or_scores(tmp_path):
    result = screen_one(tmp_path / "missing.pdf").finalized

    assert (result.status, result.total, result.reason_code) == ("REVIEW", -1, "EXTRACT_ERROR")
    assert result.tier is None and result.scores is None
    assert result.jd_id == result.jd_title == ""
    assert str(tmp_path) not in json.dumps(result.as_dict())


def test_screen_one_mock_result_serializes():
    pytest.importorskip("fitz")

    payload = json.loads(json.dumps(screen_one(NORMAL_PDF).finalized.as_dict()))

    assert payload == {
        "extract_status": "OK", "guard_status": "PASS", "status": "SCORED",
        "jd_id": "JD1", "jd_title": JD1.jd_title, "tier": "B", "total": 60,
        "scores": {"skill": 40, "project": 0, "years": 15, "education": 0, "bonus": 5},
        "project_source": "mock", "reason_code": "", "reason": "五维评分完成",
    }


def test_unmatched_zero_remains_scored_with_tier_c():
    pytest.importorskip("fitz")

    result = screen_one(NORMAL_PDF, jobs=[]).finalized

    assert (result.status, result.jd_id, result.tier, result.total) == ("SCORED", "UNMATCHED", "C", 0)
    assert result.scores == ScoreBreakdown(0, 0, 0, 0, 0)


def test_scorer_failure_keeps_review_job_without_creating_a_tier():
    pytest.importorskip("fitz")

    def fail(*args):
        raise ValueError("test scorer failure")

    result = screen_one(NORMAL_PDF, project_scorer=fail).finalized

    assert (result.status, result.jd_id, result.jd_title) == ("REVIEW", "JD1", JD1.jd_title)
    assert (result.total, result.reason_code) == (-1, "PROJECT_SCORER_ERROR")
    assert result.tier is None and result.scores is None


def test_finalize_uses_verified_result_when_score_and_route_disagree():
    pytest.importorskip("fitz")
    bad_score = ScoreResult("SCORED", "JD2", 60, ScoreBreakdown(40, 0, 15, 0, 5))
    with patch("app.screen.score_candidate", return_value=bad_score):
        screened = screen_one(NORMAL_PDF)

    assert screened.route is not None and screened.route.jd_id == "JD1"
    result = screened.finalized
    assert (result.status, result.total, result.reason_code) == ("REVIEW", -1, "SCORE_ROUTE_MISMATCH")
    assert result.jd_id == result.jd_title == ""
    assert result.tier is None and result.scores is None
