"""Calculate an explainable five-part score for a routed candidate."""

from dataclasses import dataclass, field
import math
from typing import Callable, List, Optional, Sequence

from app.extract import CandidateProfile
from app.guard import GuardResult
from app.route import JobPosting, RouteResult


ProjectScorer = Callable[[str, CandidateProfile, JobPosting], int]


@dataclass(frozen=True)
class ScoreBreakdown:
    skill: int
    project: int
    years: int
    education: int
    bonus: int

    @property
    def total(self) -> int:
        return self.skill + self.project + self.years + self.education + self.bonus


@dataclass(frozen=True)
class ScoreResult:
    status: str
    jd_id: str
    total: int
    scores: Optional[ScoreBreakdown]
    skill_hits: List[str] = field(default_factory=list)
    skill_missing: List[str] = field(default_factory=list)
    project_source: str = ""
    reason: str = ""
    reason_code: str = ""


def score_candidate(
    text: str,
    profile: CandidateProfile,
    guard: Optional[GuardResult],
    route: RouteResult,
    jobs: Sequence[JobPosting],
    *,
    project_scorer: Optional[ProjectScorer] = None,
) -> ScoreResult:
    """Score an eligible profile; return a structured review on failure."""
    if guard is None:
        return _review("", "GUARD_MISSING", "未收到 Guard 结果")
    if guard.status != "PASS":
        return _review("", guard.reason_code or "GUARD_BLOCKED", guard.reason or "Guard 拦截")
    if not _finite_number(profile.years) or profile.years < 0:
        return _review("", "INVALID_YEARS", "候选人工龄必须为有限非负数")
    if not _valid_integer_score(profile.edu_score, 10):
        return _review("", "INVALID_EDUCATION_SCORE", "学历分必须为 0–10 的整数")

    jd_id = route.jd_id
    if jd_id == "UNMATCHED":
        scores = ScoreBreakdown(skill=0, project=0, years=0, education=profile.edu_score, bonus=0)
        return _scored(jd_id, scores, "mock", "无匹配岗位，仅保留学历分")

    job = next((job for job in jobs if job.jd_id == jd_id), None)
    if job is None:
        return _review(jd_id, "JOB_NOT_FOUND", "找不到路由选中的岗位配置")
    if not job.skills:
        return _review(jd_id, "EMPTY_REQUIRED_SKILLS", "岗位的必需技能为空")
    if not _finite_number(job.min_years):
        return _review(jd_id, "INVALID_JOB_MIN_YEARS", "岗位最低年限必须为有限数")

    required = {}
    for skill in job.skills:
        required.setdefault(skill.casefold(), skill)
    available = {skill.casefold() for skill in profile.skills}
    hits = [name for key, name in required.items() if key in available]
    missing = [name for key, name in required.items() if key not in available]
    bonus_only = {skill.casefold() for skill in job.bonus_skills} - required.keys()
    bonus_count = len(available & bonus_only)

    project, source = 0, "mock"
    if project_scorer is not None:
        try:
            project = project_scorer(text, profile, job)
        except Exception:
            return _review(jd_id, "PROJECT_SCORER_ERROR", "项目打分器执行失败")
        if not _valid_integer_score(project, 30):
            return _review(jd_id, "INVALID_PROJECT_SCORE", "项目分必须为 0–30 的整数")
        source = "injected"

    scores = ScoreBreakdown(
        skill=round(40 * len(hits) / len(required)),
        project=project,
        years=_score_years(profile.years, job.min_years),
        education=profile.edu_score,
        bonus=(0, 3, 5)[min(bonus_count, 2)],
    )
    return _scored(jd_id, scores, source, "五维评分完成", hits, missing)


def _score_years(years: float, minimum: float) -> int:
    if years >= minimum:
        return 15
    gap = minimum - years
    if gap <= 0.5:
        return 12
    if gap <= 1:
        return 9
    return 6 if years >= 1 else 3


def _finite_number(value: object) -> bool:
    return type(value) is int or (isinstance(value, float) and math.isfinite(value))


def _valid_integer_score(value: object, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


def _review(jd_id: str, reason_code: str, reason: str) -> ScoreResult:
    return ScoreResult("REVIEW", jd_id, -1, None, reason=reason, reason_code=reason_code)


def _scored(
    jd_id: str,
    scores: ScoreBreakdown,
    project_source: str,
    reason: str,
    hits: Optional[List[str]] = None,
    missing: Optional[List[str]] = None,
) -> ScoreResult:
    return ScoreResult(
        "SCORED", jd_id, scores.total, scores, hits or [], missing or [], project_source, reason
    )
