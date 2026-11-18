"""Run the screening pipeline for one or more PDF resumes."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Union

from app.extract import CandidateProfile, extract_pdf, extract_profile
from app.finalize import FinalizedResult, finalize_result
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

    @property
    def finalized(self) -> FinalizedResult:
        """Expose the verified result as a display and storage record."""
        return finalize_result(
            self.score, self.route, extract_status=self.extract_status, guard_status=self.guard_status
        )


@dataclass(frozen=True)
class BatchRecord:
    source_name: str
    finalized: FinalizedResult

    def as_dict(self) -> dict:
        """Return a JSON-ready record with the source basename."""
        return {"source_name": self.source_name, **self.finalized.as_dict()}


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


def screen_many(
    pdf_paths: Sequence[Union[str, Path]],
    jobs: Optional[Sequence[JobPosting]] = None,
    *,
    project_scorer: Optional[ProjectScorer] = None,
) -> List[BatchRecord]:
    """Screen each PDF independently and keep one record per path."""
    records = []
    for path in pdf_paths:
        try:
            result = screen_one(path, jobs, project_scorer=project_scorer)
        except Exception:
            # Ordinary file and scorer errors are already handled by screen_one.
            result = _early_review("ERROR", "REVIEW", "BATCH_ITEM_ERROR", "单份筛选失败，请人工复核")
        records.append(BatchRecord(Path(path).name, result.finalized))
    return records


def _early_review(extract_status: str, guard_status: str, code: str, reason: str) -> ScreenResult:
    review = ScoreResult(
        status="REVIEW", jd_id="", total=-1, scores=None, reason_code=code, reason=reason
    )
    return ScreenResult(extract_status, guard_status, None, None, verify_score(review))
