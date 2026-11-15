"""Deterministic job routing based on a candidate profile and job config."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import List, Optional, Sequence, Union

from app.extract import CandidateProfile


MIN_HIT_RATIO = 0.30

_DIRECTION_SIGNALS = {
    "backend": ("后端", "backend", "python", "fastapi", "flask", "django", "java", "spring"),
    "frontend": ("前端", "frontend", "vue", "react", "webpack", "typescript", "css"),
    "testing": ("测试用例", "selenium", "test case", "自动化测试"),
    "product": ("prd", "原型", "产品经理"),
    "operations": ("社群运营", "活动策划", "运营"),
    "data": ("数据分析", "tableau"),
    "sre": ("运维", "告警"),
}


@dataclass(frozen=True)
class JobPosting:
    """Stable job configuration used by the router."""

    jd_id: str
    title: str
    min_years: float
    skills: List[str]
    max_years: Optional[float] = None
    raw_text: str = ""
    bonus_skills: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RouteResult:
    """Deterministic routing decision for one candidate."""

    jd_id: str
    jd_title: str
    skill_ratio: float
    years_fit: float
    combined: float
    reason: str


def load_jobs(jobs_dir: Optional[Union[str, Path]] = None) -> List[JobPosting]:
    """Load stable job IDs from the repository's synthetic job config."""
    directory = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    config_path = directory / "jobs.json"
    with config_path.open("r", encoding="utf-8") as handle:
        raw_jobs = json.load(handle)

    jobs = []
    seen_ids = set()
    for raw_job in raw_jobs:
        jd_id = str(raw_job["jd_id"])
        if jd_id in seen_ids:
            raise ValueError(f"重复的岗位 ID：{jd_id}")
        seen_ids.add(jd_id)
        jobs.append(
            JobPosting(
                jd_id=jd_id,
                title=str(raw_job["title"]),
                min_years=float(raw_job["min_years"]),
                max_years=(
                    None
                    if raw_job.get("max_years") is None
                    else float(raw_job["max_years"])
                ),
                skills=[str(skill) for skill in raw_job["skills"]],
                raw_text=str(raw_job.get("raw_text", "")),
                bonus_skills=[str(skill) for skill in raw_job.get("bonus_skills") or []],
            )
        )
    return jobs


def route_profile(
    text: str,
    profile: CandidateProfile,
    jobs: Sequence[JobPosting],
) -> RouteResult:
    """Select the best compatible job without calling a model."""
    candidates = []
    for job in jobs:
        skill_ratio = _skill_ratio(profile.skills, job.skills)
        if _direction_conflict(text, job, skill_ratio):
            continue
        if job.max_years is not None and profile.years > job.max_years:
            continue
        years_fit = _years_fit(profile.years, job.min_years, job.max_years)
        combined = 0.4 * skill_ratio + 0.6 * years_fit
        candidates.append((combined, skill_ratio, years_fit, job))

    if not candidates:
        return _unmatched("没有可用的岗位匹配结果")

    combined, skill_ratio, years_fit, job = max(
        candidates,
        key=lambda item: (item[0], item[1], item[2], item[3].min_years, item[3].jd_id),
    )
    if skill_ratio < MIN_HIT_RATIO:
        return _unmatched(
            f"最佳岗位技能命中率 {skill_ratio:.2f} 低于 {MIN_HIT_RATIO:.2f}，"
            "combined 是路由指标，不是五维总分",
            skill_ratio=skill_ratio,
            years_fit=years_fit,
            combined=combined,
        )

    reason = (
        f"{job.title}：技能命中率 {skill_ratio:.2f}，"
        f"年限契合度 {years_fit:.2f}，路由综合值 {combined:.2f}（不是五维总分）"
    )
    return RouteResult(
        jd_id=job.jd_id,
        jd_title=job.title,
        skill_ratio=round(skill_ratio, 3),
        years_fit=round(years_fit, 3),
        combined=round(combined, 3),
        reason=reason,
    )


def _default_jobs_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "synthetic" / "jobs"


def _skill_ratio(profile_skills: Sequence[str], job_skills: Sequence[str]) -> float:
    required = {skill.casefold() for skill in job_skills}
    if not required:
        return 0.0
    available = {skill.casefold() for skill in profile_skills}
    return len(required & available) / len(required)


def _years_fit(years: float, minimum: float, maximum: Optional[float]) -> float:
    value = max(0.0, float(years))
    if value >= minimum and (maximum is None or value <= maximum):
        return 1.0
    if maximum is not None and value > maximum:
        return 0.7

    gap = minimum - value
    if gap <= 0.5:
        return 0.6
    if gap <= 1.0:
        return 0.4
    if value >= 1.0:
        return 0.3
    return 0.15


def _direction_conflict(text: str, job: JobPosting, skill_ratio: float) -> bool:
    if skill_ratio >= 0.5:
        return False
    job_direction = _job_direction(job)
    if not job_direction:
        return False
    lowered = (text or "").casefold()
    for direction, terms in _DIRECTION_SIGNALS.items():
        if direction == job_direction:
            continue
        if _signal_count(lowered, terms) >= 2:
            return True
    return False


def _job_direction(job: JobPosting) -> Optional[str]:
    title = job.title.casefold()
    if any(term in title for term in ("前端", "frontend")):
        return "frontend"
    if any(term in title for term in ("后端", "backend")):
        return "backend"
    return None


def _signal_count(text: str, terms: Sequence[str]) -> int:
    return sum(_contains_term(text, term) for term in terms)


def _contains_term(text: str, term: str) -> bool:
    if any("\u4e00" <= char <= "\u9fff" for char in term):
        return term.casefold() in text
    pattern = rf"(?<![a-z0-9_]){re.escape(term.casefold())}(?![a-z0-9_])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _unmatched(
    reason: str,
    skill_ratio: float = 0.0,
    years_fit: float = 0.0,
    combined: float = 0.0,
) -> RouteResult:
    return RouteResult(
        jd_id="UNMATCHED",
        jd_title="无匹配岗位",
        skill_ratio=round(skill_ratio, 3),
        years_fit=round(years_fit, 3),
        combined=round(combined, 3),
        reason=reason,
    )
