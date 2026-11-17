"""Shape a verified screening result for display and serialization."""

from dataclasses import asdict, dataclass
from typing import Optional

from app.route import RouteResult
from app.score import ScoreBreakdown, ScoreResult


@dataclass(frozen=True)
class FinalizedResult:
    """Display and storage fields derived from one verified score."""

    extract_status: str
    guard_status: str
    status: str
    jd_id: str
    jd_title: str
    tier: Optional[str]
    total: int
    scores: Optional[ScoreBreakdown]
    project_source: str
    reason_code: str
    reason: str

    def as_dict(self) -> dict:
        """Return a JSON-ready record."""
        return asdict(self)


def finalize_result(
    score: ScoreResult,
    route: Optional[RouteResult] = None,
    *,
    extract_status: str,
    guard_status: str,
) -> FinalizedResult:
    """Format the result of verify_score and assign a tier to scored results."""
    tier = None
    if score.status == "SCORED":
        if score.total >= 80:
            tier = "A"
        elif score.total >= 55:
            tier = "B"
        else:
            tier = "C"

    return FinalizedResult(
        extract_status=extract_status,
        guard_status=guard_status,
        status=score.status,
        jd_id=score.jd_id,
        jd_title=route.jd_title if route is not None and score.jd_id and score.jd_id == route.jd_id else "",
        tier=tier,
        total=score.total,
        scores=score.scores,
        project_source=score.project_source,
        reason_code=score.reason_code,
        reason=score.reason,
    )
