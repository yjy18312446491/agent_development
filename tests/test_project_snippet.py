"""Regression cases for extracting project-section text."""

from pathlib import Path

import pytest

from app.extract import clean_text, extract_pdf
from app.project_snippet import extract_project_snippet


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"


@pytest.mark.parametrize(("filename", "sections"), [
    ("zh_single.txt", ("青禾仓储：使用 Flask 开发库存查询接口。\n编写入库、出库和库存查询的接口测试。",)),
    ("zh_multi.txt", ("星桥预约系统\n实现预约查询和取消接口。\n小岸图书工具\n编写图书库存同步程序。",)),
    ("en_projects.txt", ("Built a sample order lookup API with FastAPI.",)),
    ("en_two_headings.txt", ("Built a library search endpoint.", "Wrote tests for booking cancellations.")),
    ("no_heading.txt", ()),
    ("heading_only.txt", ()),
    ("truncated.txt", ()),
    ("work_not_project.txt", ()),
])
def test_extracts_expected_fixture_sections(filename, sections):
    text = clean_text((RESUMES / "project_snippets" / filename).read_text(encoding="utf-8"))

    assert extract_project_snippet(text) == "\n".join(sections)
    assert all(section in text for section in sections)


@pytest.mark.parametrize("heading", [
    "1. 项目经历", "2、项目经验：", "项目：", "3、项目", "(3) pRoJeCtS:", "Project Experience",
    "PROJECT HIGHLIGHTS :",
])
def test_accepts_numbered_titles_optional_colons_and_english_capitalization(heading):
    assert extract_project_snippet(f"{heading}\nOriginal project body.\n(4) Skills:\nPython") == (
        "Original project body."
    )


@pytest.mark.parametrize("heading", [
    "教育背景", "教育经历", "EDUCATION", "专业技能", "技能", "Skills", "工作经历",
    "实习经历", "Work Experience", "自我评价", "获奖情况", "证书", "Awards",
])
def test_stops_before_each_known_next_section(heading):
    assert extract_project_snippet(f"项目经历\n保留正文。\n2、{heading}：\n其他内容") == "保留正文。"


@pytest.mark.parametrize("text", [
    None, "", " \n\t", "Projects\n\n ", "Project experience: Inline project text.",
    "Professional Experience\nBuilt an API.", "Experience\nBuilt an API.",
    "实习经历\n编写了接口测试。", "我的项目经历如下\n开发库存接口。",
])
def test_returns_empty_without_a_standalone_project_heading_and_body(text):
    assert extract_project_snippet(text) == ""


def test_keeps_adjacent_nonempty_sections_and_skips_empty_ones():
    text = "Projects\nProject Experience\nFirst.\n项目经验\nSecond.\nProject Highlights"
    assert extract_project_snippet(text) == "First.\nSecond."


def test_preserves_inner_whitespace_and_unbounded_body():
    # The extractor consumes the original characters between section boundaries.
    body = "First,  with double spaces.\r\n\r\n" + "跨页续行" * 3000
    text = f"Projects\r\n{body}\r\nEducation\r\nCourse"
    assert extract_project_snippet(text) == body


@pytest.mark.parametrize("filename", ["S007_education_only.pdf", "S008_project_only.pdf"])
def test_existing_pdf_with_inline_project_description_returns_empty(filename):
    pytest.importorskip("fitz")
    extracted = extract_pdf(RESUMES / filename)

    assert extracted.ok
    # Both PDFs have an inline label. Neither has a standalone project heading.
    assert any(line.startswith("Project experience: ") for line in extracted.text.splitlines())
    assert extract_project_snippet(extracted.text) == ""


def test_cn30_resume_with_short_project_heading_yields_snippet():
    pytest.importorskip("fitz")
    extracted = extract_pdf(RESUMES.parent / "resumes_cn30" / "R09.pdf")

    assert extracted.ok
    # The inline "技能：..." tail line is not a standalone heading, so the section
    # body extends to the end of the document (same semantics as R07/R11/R16).
    assert extract_project_snippet(extracted.text) == (
        "个人博客系统：Flask+MySQL实现文章发布、评论功能\n"
        "技能：Python、Flask、MySQL、Excel处理、HTML"
    )
