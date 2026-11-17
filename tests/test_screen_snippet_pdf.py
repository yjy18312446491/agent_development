"""Test the complete PDF screening path with an injected project scorer."""

from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.extract import extract_pdf
from app.guard import guard_text
from app.project_scorer import make_injected_scorer
from app.project_snippet import extract_project_snippet
from app.route import load_jobs
from app.screen import screen_one


PDF = (
    Path(__file__).resolve().parents[1]
    / "data" / "synthetic" / "resumes" / "project_snippets" / "pdf_screen_jd1.pdf"
)
PROJECT_TEXT = (
    "Built a sample order lookup API with FastAPI.\n"
    "Added tests for missing orders and invalid IDs."
)


def test_long_synthetic_pdf_passes_real_guard_and_yields_project_text():
    pytest.importorskip("fitz")
    extracted = extract_pdf(PDF)

    assert extracted.ok
    assert extracted.text == PDF.with_suffix(".txt").read_text(encoding="utf-8").strip()
    guard = guard_text(extracted)
    assert guard.passed and guard.status == "PASS"
    assert guard.han_count == 0 and guard.letter_count >= 400
    assert extract_project_snippet(extracted.text) == PROJECT_TEXT
    assert PROJECT_TEXT in extracted.text


def test_screen_one_adds_injected_project_score_to_the_mock_baseline():
    pytest.importorskip("fitz")
    jd1 = next(job for job in load_jobs() if job.jd_id == "JD1")
    client = Mock(return_value=json.dumps({
        "relevance": {"points": 7, "evidence": "order lookup API with FastAPI"},
        "contribution": {"points": 7, "evidence": "Built a sample order lookup API"},
        "result": {"points": 7, "evidence": "Added tests for missing orders and invalid IDs."},
    }))

    default = screen_one(PDF)
    injected = screen_one(PDF, project_scorer=make_injected_scorer(client))

    for result in (default, injected):
        assert (result.extract_status, result.guard_status) == ("OK", "PASS")
        assert result.route is not None and result.route.jd_id == "JD1"
        assert (result.score.status, result.score.jd_id) == ("SCORED", "JD1")
    assert default.score.scores is not None and injected.score.scores is not None
    assert (default.score.project_source, default.score.scores.project) == ("mock", 0)
    assert (injected.score.project_source, injected.score.scores.project) == ("injected", 21)
    assert replace(injected.score.scores, project=0) == default.score.scores
    assert injected.score.total == default.score.total + 21
    client.assert_called_once_with(PROJECT_TEXT, jd1.raw_text.strip())
