"""Run the screening pipeline for one or more PDF resumes."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Union

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from typing_extensions import TypedDict

from app.extract import CandidateProfile, ExtractResult, extract_pdf, extract_profile
from app.finalize import FinalizedResult, finalize_result
from app.guard import GuardResult, guard_text
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
    """Run the local graph and return the established screening result."""
    try:
        with tracing_context(enabled=False):
            state = _SCREEN_GRAPH.invoke({
                "pdf_path": pdf_path, "jobs": jobs, "project_scorer": project_scorer,
            })
        return state["result"]
    except Exception:
        # This fallback also covers a failure in the verification node itself.
        review = ScoreResult(
            status="REVIEW", jd_id="", total=-1, scores=None,
            reason_code="BATCH_ITEM_ERROR", reason="单份筛选失败，请人工复核",
        )
        return ScreenResult("ERROR", "REVIEW", None, None, review)


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


class _ScreenState(TypedDict, total=False):
    pdf_path: Union[str, Path]
    jobs: Optional[Sequence[JobPosting]]
    project_scorer: Optional[ProjectScorer]
    extract: ExtractResult
    guard: GuardResult
    profile: CandidateProfile
    route: RouteResult
    score: ScoreResult
    result: ScreenResult


def _extract_node(state: _ScreenState) -> dict:
    return {"extract": extract_pdf(state["pdf_path"])}


def _guard_node(state: _ScreenState) -> dict:
    return {"guard": guard_text(state["extract"])}


def _after_guard(state: _ScreenState) -> str:
    return "load_jobs" if state["guard"].passed else "review"


def _review_node(state: _ScreenState) -> dict:
    guard = state["guard"]
    reason = "PDF 读取失败，请人工复核" if guard.reason_code == "EXTRACT_ERROR" else guard.reason
    return {"result": _early_review(state["extract"].status, guard.status, guard.reason_code, reason)}


def _jobs_node(state: _ScreenState) -> dict:
    if state["jobs"] is not None:
        return {"jobs": state["jobs"]}
    try:
        return {"jobs": load_jobs()}
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return {"result": _early_review(
            state["extract"].status, state["guard"].status, "JOB_CONFIG_ERROR", "岗位配置加载失败，请人工复核"
        )}


def _after_jobs(state: _ScreenState) -> str:
    return END if "result" in state else "extract_profile"


def _profile_node(state: _ScreenState) -> dict:
    return {"profile": extract_profile(state["extract"].text, file_name=Path(state["pdf_path"]).name)}


def _route_node(state: _ScreenState) -> dict:
    return {"route": route_profile(state["extract"].text, state["profile"], state["jobs"])}


def _score_node(state: _ScreenState) -> dict:
    return {"score": score_candidate(
        state["extract"].text, state["profile"], state["guard"], state["route"], state["jobs"],
        project_scorer=state["project_scorer"],
    )}


def _verify_node(state: _ScreenState) -> dict:
    score = verify_score(state["score"], state["route"])
    return {"result": ScreenResult(
        state["extract"].status, state["guard"].status, state["profile"], state["route"], score
    )}


def _build_screen_graph():
    graph = StateGraph(_ScreenState)
    for name, node in (
        ("extract_pdf", _extract_node), ("guard_text", _guard_node), ("review", _review_node),
        ("load_jobs", _jobs_node), ("extract_profile", _profile_node),
        ("route_profile", _route_node), ("score_candidate", _score_node), ("verify_score", _verify_node),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "extract_pdf")
    graph.add_edge("extract_pdf", "guard_text")
    graph.add_conditional_edges("guard_text", _after_guard, ["load_jobs", "review"])
    graph.add_edge("review", END)
    graph.add_conditional_edges("load_jobs", _after_jobs, ["extract_profile", END])
    graph.add_edge("extract_profile", "route_profile")
    graph.add_edge("route_profile", "score_candidate")
    graph.add_edge("score_candidate", "verify_score")
    graph.add_edge("verify_score", END)
    return graph.compile()


_SCREEN_GRAPH = _build_screen_graph()
