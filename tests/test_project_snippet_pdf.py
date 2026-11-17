"""Verify that project-section boundaries survive PDF text extraction."""

from pathlib import Path

import pytest

from app.extract import extract_pdf
from app.project_snippet import extract_project_snippet


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"


@pytest.mark.parametrize(("name", "sections"), [
    ("pdf_single", (
        "Built a sample order lookup API with FastAPI.\n"
        "Added tests for missing orders and invalid IDs.",
    )),
    ("pdf_multi", (
        "Library search\n"
        "Built a catalog search endpoint with PostgreSQL.\n"
        "Room booking\n"
        "Added cancellation tests for the booking API.",
    )),
    ("pdf_two_headings", (
        "Built a sample inventory lookup API.",
        "Wrote interface tests for a demo booking service.",
    )),
    ("pdf_heading_only", ()),
    ("pdf_truncated", ()),
])
def test_extracts_project_sections_from_synthetic_pdf(name, sections):
    pytest.importorskip("fitz")
    fixture = RESUMES / "project_snippets" / name

    extracted = extract_pdf(fixture.with_suffix(".pdf"))

    assert extracted.ok
    # These fixtures were checked with extract_pdf after generation.
    assert extracted.text == fixture.with_suffix(".txt").read_text(encoding="utf-8").strip()
    assert extract_project_snippet(extracted.text) == "\n".join(sections)
    assert all(section in extracted.text for section in sections)


def test_s001_work_experience_is_not_a_project_section():
    pytest.importorskip("fitz")
    extracted = extract_pdf(RESUMES / "S001_normal_backend.pdf")

    assert extracted.ok
    assert any(line.startswith("Work experience: ") for line in extracted.text.splitlines())
    assert extract_project_snippet(extracted.text) == ""
