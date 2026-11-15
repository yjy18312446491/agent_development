"""Validate a score result before handing it to the next screening stage."""

from typing import Optional

from app.route import RouteResult
from app.score import ScoreBreakdown, ScoreResult


_SCORE_LIMITS = (
    ("skill", 40),
    ("project", 30),
    ("years", 15),
    ("education", 10),
    ("bonus", 5),
)


def verify_score(
    result: Optional[ScoreResult], route: Optional[RouteResult] = None
) -> ScoreResult:
    """Return valid results as received and send invalid results for review."""
    if not isinstance(result, ScoreResult) or result.status not in ("SCORED", "REVIEW"):
        return _invalid_score()

    if result.status == "REVIEW":
        if (
            _is_integer(result.total)
            and result.total == -1
            and result.scores is None
            and _has_text(result.reason_code)
        ):
            return result
        return _invalid_score()

    scores = result.scores
    if not isinstance(scores, ScoreBreakdown) or not _is_integer(result.total):
        return _invalid_score()
    for field, maximum in _SCORE_LIMITS:
        points = getattr(scores, field)
        if not _is_integer(points) or not 0 <= points <= maximum:
            return _invalid_score()
    if not 0 <= result.total <= 100 or result.total != scores.total:
        return _invalid_score()

    if (
        not isinstance(route, RouteResult)
        or not _has_text(route.jd_id)
        or not _has_text(result.jd_id)
        or result.jd_id != route.jd_id
    ):
        return _review("SCORE_ROUTE_MISMATCH", "评分岗位与路由结果不一致")

    # With a verified sum and nonnegative subscores, this enforces four zero scores.
    if result.jd_id == "UNMATCHED" and result.total != scores.education:
        return _review("INVALID_UNMATCHED_SCORE", "无匹配岗位的分项应只保留学历分")
    return result


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _invalid_score() -> ScoreResult:
    return _review("INVALID_SCORE_RESULT", "评分结果结构或分数不合法")


def _review(code: str, reason: str) -> ScoreResult:
    return ScoreResult(
        status="REVIEW", jd_id="", total=-1, scores=None, reason_code=code, reason=reason
    )
