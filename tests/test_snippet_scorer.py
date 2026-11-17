"""Exercise the project-section scoring callback with local fake clients."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.extract import CandidateProfile, extract_pdf
from app.guard import GuardResult
from app.project_scorer import InvalidProjectResponse, make_injected_scorer
from app.route import RouteResult, load_jobs
from app.score import score_candidate
from app.screen import screen_one


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"
SNIPPET_PDFS = RESUMES / "project_snippets"
NORMAL_PDF = RESUMES / "S001_normal_backend.pdf"
SINGLE_SNIPPET = (
    "Built a sample order lookup API with FastAPI.\n"
    "Added tests for missing orders and invalid IDs."
)


@pytest.fixture
def jd1():
    return next(job for job in load_jobs() if job.jd_id == "JD1")


def read_pdf_text(path):
    pytest.importorskip("fitz")
    extracted = extract_pdf(path)
    assert extracted.ok
    return extracted.text


def test_single_pdf_sends_only_project_text_and_job_requirements(jd1):
    text = read_pdf_text(SNIPPET_PDFS / "pdf_single.pdf")
    client = Mock(return_value=json.dumps({
        "relevance": {"points": 7, "evidence": "order lookup API with FastAPI"},
        "contribution": {"points": 7, "evidence": "Built a sample order lookup API"},
        "result": {"points": 7, "evidence": "Added tests for missing orders and invalid IDs."},
    }))
    scorer = make_injected_scorer(client)

    assert scorer(text, CandidateProfile(), jd1) == 21
    client.assert_called_once_with(SINGLE_SNIPPET, jd1.raw_text.strip())


def test_two_project_sections_exclude_resume_name_skills_and_awards(jd1):
    pdf_text = read_pdf_text(SNIPPET_PDFS / "pdf_two_headings.pdf")
    text = "Name: Fictional Candidate\n" + pdf_text
    profile = CandidateProfile(name="Fictional Candidate", skills=["Redis"])
    client = Mock(return_value=json.dumps({
        "relevance": {"points": 7, "evidence": "inventory lookup API"},
        "contribution": {"points": 0, "evidence": ""},
        "result": {"points": 7, "evidence": "Wrote interface tests"},
    }))

    assert make_injected_scorer(client)(text, profile, jd1) == 14
    client.assert_called_once_with(
        "Built a sample inventory lookup API.\n"
        "Wrote interface tests for a demo booking service.",
        jd1.raw_text.strip(),
    )


@pytest.mark.parametrize("path", [
    SNIPPET_PDFS / "pdf_heading_only.pdf", SNIPPET_PDFS / "pdf_truncated.pdf", NORMAL_PDF,
])
def test_empty_project_section_returns_zero_without_calling_client(path, jd1):
    text = read_pdf_text(path)
    client = Mock()

    assert make_injected_scorer(client)(text, CandidateProfile(), jd1) == 0
    client.assert_not_called()


@pytest.mark.parametrize("response", [
    "invalid JSON",
    json.dumps({
        "relevance": {"points": 7, "evidence": ""},
        "contribution": {"points": 0, "evidence": ""},
        "result": {"points": 0, "evidence": ""},
    }),
])
def test_invalid_client_response_propagates_to_existing_review_path(response, jd1):
    text = read_pdf_text(SNIPPET_PDFS / "pdf_single.pdf")
    profile = CandidateProfile(skills=["Python", "FastAPI", "MySQL"], years=6, edu_score=7)
    client = Mock(return_value=response)
    scorer = make_injected_scorer(client)

    with pytest.raises(InvalidProjectResponse):
        scorer(text, profile, jd1)
    client.assert_called_once_with(SINGLE_SNIPPET, jd1.raw_text.strip())
    client.reset_mock()

    guard = GuardResult(passed=True, status="PASS", reason_code="", reason="", score=None)
    route = RouteResult(jd1.jd_id, jd1.title, 0.75, 1.0, 0.9, "Test route")
    score = score_candidate(text, profile, guard, route, [jd1], project_scorer=scorer)

    assert (score.status, score.jd_id, score.reason_code, score.total) == (
        "REVIEW", "JD1", "PROJECT_SCORER_ERROR", -1,
    )
    assert score.scores is None
    client.assert_called_once_with(SINGLE_SNIPPET, jd1.raw_text.strip())


def test_screen_one_defaults_to_mock_and_accepts_the_adapter_for_s001():
    pytest.importorskip("fitz")
    client = Mock()

    default = screen_one(NORMAL_PDF).score
    injected = screen_one(NORMAL_PDF, project_scorer=make_injected_scorer(client)).score

    for result, source in ((default, "mock"), (injected, "injected")):
        assert (result.status, result.jd_id, result.total, result.project_source) == (
            "SCORED", "JD1", 60, source,
        )
        assert result.scores is not None and result.scores.project == 0
    client.assert_not_called()
