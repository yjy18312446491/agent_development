"""Check graph migration against the previous sequential screening contract."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import langsmith.client
import pytest

from app import screen


RESUMES = Path(__file__).resolve().parents[1] / "data/synthetic/resumes"
PDFS = sorted(RESUMES.glob("S*.pdf"))


def _sequential_screen(path, jobs=None, project_scorer=None):
    """Reference the pre-graph order while using the established stage functions."""
    extract = screen.extract_pdf(path)
    guard = screen.guard_text(extract)
    if not guard.passed:
        reason = "PDF 读取失败，请人工复核" if guard.reason_code == "EXTRACT_ERROR" else guard.reason
        return screen._early_review(extract.status, guard.status, guard.reason_code, reason)
    if jobs is None:
        try:
            jobs = screen.load_jobs()
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            return screen._early_review(
                extract.status, guard.status, "JOB_CONFIG_ERROR", "岗位配置加载失败，请人工复核"
            )
    profile = screen.extract_profile(extract.text, file_name=Path(path).name)
    route = screen.route_profile(extract.text, profile, jobs)
    score = screen.score_candidate(extract.text, profile, guard, route, jobs, project_scorer=project_scorer)
    return screen.ScreenResult(extract.status, guard.status, profile, route, screen.verify_score(score, route))


@pytest.mark.parametrize("pdf", PDFS, ids=lambda pdf: pdf.stem)
@pytest.mark.parametrize("scoring", ["mock", "injected", "failed"])
def test_graph_matches_previous_sequential_results(pdf, scoring):
    def scorer(text, profile, job):
        if scoring == "failed":
            raise RuntimeError("private model error")
        return 10

    callback = None if scoring == "mock" else scorer
    expected = _sequential_screen(pdf, project_scorer=callback)
    actual = screen.screen_one(pdf, project_scorer=callback)
    assert actual == expected
    assert actual.finalized.as_dict() == expected.finalized.as_dict()


def test_graph_short_circuits_guard_and_uses_all_normal_nodes():
    short = list(screen._SCREEN_GRAPH.stream({"pdf_path": RESUMES / "S002_empty.pdf", "jobs": None,
                                             "project_scorer": None}, stream_mode="updates"))
    assert [next(iter(step)) for step in short] == ["extract_pdf", "guard_text", "review"]
    normal = list(screen._SCREEN_GRAPH.stream({"pdf_path": RESUMES / "S001_normal_backend.pdf", "jobs": None,
                                              "project_scorer": None}, stream_mode="updates"))
    assert [next(iter(step)) for step in normal] == [
        "extract_pdf", "guard_text", "load_jobs", "extract_profile", "route_profile",
        "score_candidate", "verify_score",
    ]


@pytest.mark.parametrize("stage", [
    "extract_pdf", "guard_text", "load_jobs", "extract_profile", "route_profile", "score_candidate", "verify_score",
])
def test_unexpected_node_error_returns_review_without_private_detail(monkeypatch, stage):
    def fail(*args, **kwargs):
        raise RuntimeError("private node failure")

    monkeypatch.setattr(screen, stage, fail)
    result = screen.screen_one(RESUMES / "S001_normal_backend.pdf")
    assert result.score.status == "REVIEW" and result.score.total == -1
    assert result.score.scores is None and result.finalized.tier is None
    assert result.score.reason_code == "BATCH_ITEM_ERROR"
    assert "private" not in result.score.reason


def test_shared_graph_keeps_concurrent_invocations_separate():
    paths = [RESUMES / "S001_normal_backend.pdf", RESUMES / "S002_empty.pdf"] * 3
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(screen.screen_one, paths))
    assert [result.score.total for result in results] == [60, -1] * 3
    assert [result.profile is None for result in results] == [False, True] * 3


def test_graph_invocation_disables_external_tracing(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Screening graph must not send tracing data")

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(langsmith.client.Client, "request_with_retries", forbidden)
    result = screen.screen_one(RESUMES / "S001_normal_backend.pdf")
    assert result.score.total == 60
