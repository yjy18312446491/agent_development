"""Run the screening pipeline for one PDF resume."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Union

from app.extract import CandidateProfile, extract_pdf, extract_profile
from app.guard import guard_text
from app.route import JobPosting, RouteResult, load_jobs, route_profile
from app.score import ProjectScorer, ScoreResult, score_candidate
from app.verify import verify_score


@dataclass(frozen=True)
class ScreenResult:
    extract_status: str
    guard_status: str
    profile: Optional[CandidateProfile]
    route: Optional[RouteResult]
    score: ScoreResult


def screen_one(
    pdf_path: Union[str, Path],
    jobs: Optional[Sequence[JobPosting]] = None,
    *,
    project_scorer: Optional[ProjectScorer] = None,
) -> ScreenResult:
    """Screen a single PDF using the default jobs or a supplied job sequence."""
    extract = extract_pdf(pdf_path)
    guard = guard_text(extract)

    if not guard.passed:
        reason = guard.reason
        if guard.reason_code == "EXTRACT_ERROR":
            reason = "PDF 读取失败，请人工复核"
        return _early_review(extract.status, guard.status, guard.reason_code, reason)

    if jobs is None:
        try:
            jobs = load_jobs()
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            return _early_review(
                extract.status, guard.status, "JOB_CONFIG_ERROR", "岗位配置加载失败，请人工复核"
            )

    profile = extract_profile(extract.text, file_name=Path(pdf_path).name)
    route = route_profile(extract.text, profile, jobs)
    scored = score_candidate(
        extract.text, profile, guard, route, jobs, project_scorer=project_scorer
    )
    return ScreenResult(extract.status, guard.status, profile, route, verify_score(scored, route))


def _early_review(extract_status: str, guard_status: str, code: str, reason: str) -> ScreenResult:
    review = ScoreResult(
        status="REVIEW", jd_id="", total=-1, scores=None, reason_code=code, reason=reason
    )
    return ScreenResult(extract_status, guard_status, None, None, verify_score(review))
