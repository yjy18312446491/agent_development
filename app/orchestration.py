"""Risk-aware orchestration on top of the established screening graph.

Adds three nodes after the existing chain without changing its contract:

- ``risk_critique``: deterministic rule-based risk review of the verified result.
- ``human_review``: explicit LangGraph interrupt for high-risk results, or an
  automatic keep-current-behaviour decision in the default mode.
- ``export_report``: optional Markdown assessment report with derived fields only.

The default ``screen_one`` / ``screen_many`` entry points stay untouched; the
upgraded flow is opt-in through ``screen_one_risk`` / ``screen_many_risk``.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
import re
from typing import Any, Mapping, Optional, Sequence, Tuple, Union
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langsmith import tracing_context

from app.screen import (
    ScreenResult,
    _after_guard,
    _extract_node,
    _guard_node,
    _jobs_node,
    _profile_node,
    _review_node,
    _route_node,
    _score_node,
    _ScreenState,
    _verify_node,
    _early_review,
)
from app.score import ProjectScorer, ScoreResult
from app.verify import verify_score


REVIEW_MODES = ("auto", "interrupt")
DECISIONS = ("approve", "reject")
_INJECTION_PATTERNS = (
    r"ignore (all )?previous", r"disregard (all )?previous",
    r"忽略(上面|以上|之前|先前|前面的)", r"无视(上面|以上|之前|先前)",
    r"system prompt", r"系统提示词", r"角色扮演", r"你现在是",
)
_SEVERITIES = ("info", "review", "block")


@dataclass(frozen=True)
class RiskFinding:
    """One deterministic risk observation with a fixed severity vocabulary."""

    code: str
    severity: str
    detail: str

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


@dataclass(frozen=True)
class RiskReport:
    """Aggregate risk level over ordered findings."""

    level: str
    needs_human_review: bool
    findings: Tuple[RiskFinding, ...]

    def as_dict(self) -> dict:
        return {"level": self.level, "needs_human_review": self.needs_human_review,
                "findings": [finding.as_dict() for finding in self.findings]}


@dataclass(frozen=True)
class RiskScreenOutcome:
    """Screening result plus risk review, human decision and report location."""

    source_name: str
    screen: ScreenResult
    risk: RiskReport
    review_decision: Optional[dict]
    report_path: Optional[str]
    interrupted: bool = False

    @property
    def as_dict(self) -> dict:
        """JSON-ready record: finalized screening fields plus review context."""
        payload = {"source_name": self.source_name, **self.screen.finalized.as_dict()}
        payload["risk"] = self.risk.as_dict()
        payload["review_decision"] = self.review_decision
        payload["report_path"] = self.report_path
        payload["interrupted"] = self.interrupted
        return payload


def _risk_node(state: dict) -> dict:
    findings = []
    guard = state.get("guard")
    if guard is None or not guard.passed:
        code = guard.reason_code if guard is not None else "EXTRACT_ERROR"
        findings.append(RiskFinding("GATE_INTERCEPT", "review", str(code)))
    score = state.get("score")
    if score is not None and score.status == "REVIEW":
        findings.append(RiskFinding("SCORE_REVIEW", "review", str(score.reason_code)))
    route = state.get("route")
    if route is not None and route.jd_id == "UNMATCHED":
        findings.append(RiskFinding("ROUTE_UNMATCHED", "info", "无匹配岗位"))
    extract = state.get("extract")
    text = (extract.text if extract is not None else "") or ""
    hits = [pattern for pattern in _INJECTION_PATTERNS if re.search(pattern, text)]
    if hits:
        findings.append(RiskFinding("INJECTION_SUSPECT", "review", "|".join(hits)))
    level = "review" if any(finding.severity in ("review", "block") for finding in findings) else "ok"
    return {"risk": RiskReport(level, level == "review", tuple(findings))}


def _validated_decision(raw: Any) -> dict:
    if not isinstance(raw, Mapping) or raw.get("decision") not in DECISIONS:
        raise ValueError("REVIEW_DECISION_INVALID")
    note = raw.get("note")
    return {"decision": raw["decision"], "note": None if note is None else str(note)}


def _human_review_node(state: dict) -> dict:
    risk = state["risk"]
    if not risk.needs_human_review:
        decision = {"decision": "auto_ok", "note": "无需人工复核"}
    elif state.get("review_mode") == "interrupt":
        decision = _validated_decision(interrupt({
            "risk": risk.as_dict(),
            "screening": state["result"].finalized.as_dict(),
        }))
    else:
        decision = {"decision": "auto_kept", "note": "自动保留人工复核标记"}
    result = state["result"]
    if decision["decision"] == "reject":
        note = decision.get("note") or "无补充说明"
        rejected = ScoreResult(
            status="REVIEW", jd_id="", total=-1, scores=None,
            reason_code="HUMAN_REVIEW_REJECTED", reason=f"人工复核驳回：{note}",
        )
        result = ScreenResult(
            result.extract_status, result.guard_status, result.profile, result.route,
            verify_score(rejected, state.get("route")),
        )
    return {"result": result, "review_decision": decision}


def _report_filename(pdf_path: Union[str, Path]) -> str:
    stem = Path(str(pdf_path)).stem
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", stem)[:80] or "resume"
    if PureWindowsPath(safe).is_reserved():
        safe = "_" + safe
    return safe + "-screen-report.md"


def _render_report(state: dict) -> str:
    result = state["result"]
    finalized = result.finalized.as_dict()
    scores = finalized["scores"]
    breakdown = ("无" if scores is None else
                 f"技能 {scores['skill']}、项目 {scores['project']}、年限 {scores['years']}"
                 f"、学历 {scores['education']}、加分 {scores['bonus']}")
    route = result.route
    routing = ("未执行路由" if route is None else
               f"{route.jd_id} {route.jd_title}（技能命中 {route.skill_ratio:.2f}、"
               f"年限契合 {route.years_fit:.2f}、综合 {route.combined:.2f}）")
    lines = [
        "# 简历筛选评估报告",
        "",
        f"- 生成时间（UTC）：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- 来源文件：{Path(str(state['pdf_path'])).name}",
        f"- 抽取状态：{result.extract_status}；闸门状态：{result.guard_status}",
        f"- 判岗：{routing}",
        f"- 结论：{finalized['status']}，档位 {finalized['tier']}，总分 {finalized['total']}",
        f"- 分项：{breakdown}",
        f"- 原因码：{finalized['reason_code']}；说明：{finalized['reason']}",
        "",
        "## 风险审查",
        "",
        f"- 风险等级：{state['risk'].level}",
    ]
    lines.extend(
        f"- [{finding.severity}] {finding.code}：{finding.detail}"
        for finding in state["risk"].findings
    )
    if not state["risk"].findings:
        lines.append("- 无风险发现")
    lines.extend(["", "## 人工复核", ""])
    decision = state.get("review_decision")
    if decision is None:
        lines.append("- 未触发决策（等待人工处理）")
    else:
        lines.append(f"- 决策：{decision['decision']}；说明：{decision.get('note') or '无'}")
    lines.extend(["", "> 本报告只含派生字段，不含简历原文。", ""])
    return "\n".join(lines)


def _export_report_node(state: dict) -> dict:
    report_dir = state.get("report_dir")
    if not report_dir:
        return {"report_path": None}
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _report_filename(state["pdf_path"])
    path.write_text(_render_report(state), encoding="utf-8")
    return {"report_path": str(path)}


class _RiskState(_ScreenState, total=False):
    review_mode: str
    report_dir: Optional[str]
    risk: RiskReport
    review_decision: Optional[dict]
    report_path: Optional[str]


def _after_jobs_risk(state: dict) -> str:
    return "risk_critique" if "result" in state else "extract_profile"


def _after_review(state: dict) -> str:
    return "export_report" if state.get("report_dir") else END


def _build_risk_graph(checkpointer: Optional[BaseCheckpointSaver] = None):
    graph = StateGraph(_RiskState)
    for name, node in (
        ("extract_pdf", _extract_node), ("guard_text", _guard_node), ("review", _review_node),
        ("load_jobs", _jobs_node), ("extract_profile", _profile_node),
        ("route_profile", _route_node), ("score_candidate", _score_node),
        ("verify_score", _verify_node), ("risk_critique", _risk_node),
        ("human_review", _human_review_node), ("export_report", _export_report_node),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "extract_pdf")
    graph.add_edge("extract_pdf", "guard_text")
    graph.add_conditional_edges("guard_text", _after_guard, ["load_jobs", "review"])
    graph.add_conditional_edges("load_jobs", _after_jobs_risk, ["extract_profile", "risk_critique"])
    graph.add_edge("review", "risk_critique")
    graph.add_edge("extract_profile", "route_profile")
    graph.add_edge("route_profile", "score_candidate")
    graph.add_edge("score_candidate", "verify_score")
    graph.add_edge("verify_score", "risk_critique")
    graph.add_edge("risk_critique", "human_review")
    graph.add_conditional_edges("human_review", _after_review, ["export_report", END])
    graph.add_edge("export_report", END)
    return graph.compile(checkpointer=checkpointer)


def _fallback_outcome(source_name: str) -> RiskScreenOutcome:
    screen = _early_review("ERROR", "REVIEW", "BATCH_ITEM_ERROR", "单份筛选失败，请人工复核")
    risk = RiskReport("review", True, (RiskFinding("PIPELINE_ERROR", "review", "BATCH_ITEM_ERROR"),))
    return RiskScreenOutcome(source_name, screen, risk, None, None)


def _validate_common(review_mode: str, report_dir: Optional[Union[str, Path]]) -> Optional[str]:
    if review_mode not in REVIEW_MODES:
        raise ValueError("REVIEW_MODE_INVALID")
    return None if report_dir is None else str(report_dir)


def screen_one_risk(
    pdf_path: Union[str, Path],
    jobs: Optional[Sequence[Any]] = None,
    project_scorer: Optional[ProjectScorer] = None,
    *,
    review_mode: str = "auto",
    report_dir: Optional[Union[str, Path]] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None,
    thread_id: Optional[str] = None,
    resume: Optional[Mapping] = None,
) -> RiskScreenOutcome:
    """Run the risk-aware graph; interrupt mode pauses for a human decision.

    When the graph interrupts, the returned outcome has ``interrupted=True``;
    resume the same ``thread_id`` by passing ``resume={"decision": ...}`` with
    the same ``checkpointer``. Reports contain derived fields only.
    """
    if resume is not None:
        _validated_decision(resume)
    if review_mode == "interrupt" and checkpointer is None:
        raise ValueError("CHECKPOINTER_REQUIRED")
    resolved_dir = _validate_common(review_mode, report_dir)
    source_name = Path(str(pdf_path)).name
    graph = _build_risk_graph(checkpointer)
    if resume is not None:
        payload: Any = Command(resume=dict(resume))
        config = {"configurable": {"thread_id": thread_id or uuid4().hex}}
    else:
        payload = {
            "pdf_path": pdf_path, "jobs": jobs, "project_scorer": project_scorer,
            "review_mode": review_mode, "report_dir": resolved_dir,
        }
        config = {"configurable": {"thread_id": thread_id or uuid4().hex}} if checkpointer else None
    try:
        with tracing_context(enabled=False):
            state = graph.invoke(payload, config)
    except GraphInterrupt:
        # Legacy LangGraph behaviour; the installed version returns the key below.
        return _fallback_outcome(source_name)
    except Exception:
        # Mirror screen_one: any pipeline failure becomes a REVIEW outcome.
        return _fallback_outcome(source_name)
    if isinstance(state, dict) and "__interrupt__" in state:
        return RiskScreenOutcome(source_name, state["result"], state["risk"], None, None, interrupted=True)
    return RiskScreenOutcome(source_name, state["result"], state["risk"],
                             state.get("review_decision"), state.get("report_path"))


def screen_many_risk(
    pdf_paths: Sequence[Union[str, Path]],
    jobs: Optional[Sequence[Any]] = None,
    project_scorer: Optional[ProjectScorer] = None,
    *,
    review_mode: str = "auto",
    report_dir: Optional[Union[str, Path]] = None,
) -> list:
    """Screen each PDF through the risk-aware graph, keeping per-file records."""
    outcomes = []
    for path in pdf_paths:
        try:
            outcome = screen_one_risk(
                path, jobs, project_scorer=project_scorer,
                review_mode=review_mode, report_dir=report_dir,
            )
        except ValueError:
            raise
        except Exception:
            outcome = _fallback_outcome(Path(str(path)).name)
        outcomes.append(outcome)
    return outcomes
