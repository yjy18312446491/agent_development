"""Route-quality evaluation over the synthetic Chinese resume set.

Runs the default Mock screening entry point on 30 synthetic Chinese
resumes, locks the observed baseline per case, and derives quality views
for presentation: a route confusion matrix, per-group accuracy, and
mismatch attribution. Quality metrics are reported, not asserted; the
baseline fields are the regression contract.
"""

from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
import argparse
import csv
import json
from pathlib import Path, PureWindowsPath
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple, Union

from app.screen import screen_one


ROOT = Path(__file__).resolve().parents[1]
RESUMES_CN = ROOT / "data" / "synthetic" / "resumes_cn30"
DEFAULT_MANIFEST = ROOT / "eval" / "manifest_cn30.csv"
DEFAULT_OUTPUT = ROOT / "eval" / "reports" / "latest-cn30.json"
GROUPS = ("A", "B", "C", "D", "E")
EXPECTED_ROUTES = ("JD1", "JD2", "UNMATCHED", "INTERCEPT")
ROUTED_ROUTES = ("JD1", "JD2", "UNMATCHED")
BASELINE_ROUTES = ("JD1", "JD2", "UNMATCHED", "")
FIELDS = (
    "case_id", "pdf_path", "group", "expected_route",
    "baseline_route", "baseline_score_status", "baseline_total",
    "baseline_reason", "note",
)
LIMITATIONS = [
    "Route quality metrics come from the deterministic router and Mock "
    "scoring on 30 synthetic Chinese resumes; they do not measure a "
    "real LLM screening model.",
    "Totals use the Mock project score of 0, so legacy score-range and "
    "tier annotations are not comparable and are not asserted here.",
    "Non-abnormal resumes intercepted by the text gate are counted "
    "separately and excluded from the route accuracy denominator.",
    "The English 13-case PDF baseline and this Chinese baseline are "
    "independent regressions with separate manifests.",
]
Label = Union[int, str, None]


@dataclass(frozen=True)
class QualityCase:
    case_id: str
    pdf: Path
    group: str
    expected_route: str
    baseline: Dict[str, Label]
    note: str


def _fixture_path(value: str) -> Path:
    windows = PureWindowsPath(value)
    relative = Path(value)
    if windows.anchor or relative.is_absolute() or ".." in windows.parts:
        raise ValueError("pdf_path must be relative and remain in the Chinese resume directory")
    path = (ROOT / relative).resolve()
    if RESUMES_CN.resolve() not in path.parents or path.suffix.lower() != ".pdf":
        raise ValueError("pdf_path must point to a Chinese synthetic PDF")
    if not path.is_file():
        raise ValueError("Chinese synthetic PDF is missing")
    return path


def _number(value: str, field: str, minimum: int, maximum: int) -> int:
    if not re.fullmatch(r"-?[0-9]+", value):
        raise ValueError(f"{field} must be an integer")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{field} is out of range")
    return number


def _parse_case(row: Dict[str, str]) -> QualityCase:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", row["case_id"]):
        raise ValueError("invalid case_id")
    if row["group"] not in GROUPS:
        raise ValueError("unknown group")
    if row["expected_route"] not in EXPECTED_ROUTES:
        raise ValueError("invalid expected_route")
    if row["baseline_route"] not in BASELINE_ROUTES:
        raise ValueError("invalid baseline_route")
    if row["baseline_score_status"] not in {"SCORED", "REVIEW"}:
        raise ValueError("invalid baseline_score_status")
    if not re.fullmatch(r"[A-Za-z0-9_-]*", row["baseline_reason"]):
        raise ValueError("invalid baseline_reason")

    baseline: Dict[str, Label] = {
        "route_id": row["baseline_route"],
        "score_status": row["baseline_score_status"],
        "reason_code": row["baseline_reason"],
    }
    if row["baseline_score_status"] == "REVIEW":
        baseline["total"] = _number(row["baseline_total"], "baseline_total", -1, -1)
        if not row["baseline_reason"] or row["baseline_route"]:
            raise ValueError("REVIEW requires a reason_code and a blank baseline route")
    else:
        baseline["total"] = _number(row["baseline_total"], "baseline_total", 0, 100)
        if row["baseline_reason"] or not row["baseline_route"]:
            raise ValueError("SCORED requires a baseline route and a blank reason_code")

    if row["group"] == "D" and row["expected_route"] != "INTERCEPT":
        raise ValueError("group D requires the INTERCEPT expected route")
    if row["group"] != "D" and row["expected_route"] == "INTERCEPT":
        raise ValueError("only group D may expect INTERCEPT")
    return QualityCase(
        row["case_id"], _fixture_path(row["pdf_path"]), row["group"],
        row["expected_route"], baseline, row["note"],
    )


def load_manifest(path: Path) -> Tuple[List[QualityCase], str]:
    """Read and validate the Chinese manifest, returning cases and hash."""
    content = path.read_bytes()
    reader = csv.reader(StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
    if next(reader, None) != list(FIELDS):
        raise ValueError("manifest header does not match the required columns")
    cases = []
    seen = set()
    for record in reader:
        if not record:
            continue
        try:
            if len(record) != len(FIELDS):
                raise ValueError("incorrect column count")
            case = _parse_case(dict(zip(FIELDS, record)))
            if case.case_id in seen:
                raise ValueError("duplicate case_id")
        except ValueError as exc:
            raise ValueError(f"line {reader.line_num}: {exc}") from None
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError("manifest has no evaluation cases")
    return cases, sha256(content).hexdigest()


def _actual_fields(result) -> Dict[str, Label]:
    score = result.score
    tier: Label = None
    if score.status == "SCORED":
        tier = "A" if score.total >= 80 else ("B" if score.total >= 55 else "C")
    route = result.route
    return {
        "extract_status": result.extract_status,
        "guard_status": result.guard_status,
        "route_id": route.jd_id if route else "",
        "skill_ratio": route.skill_ratio if route else None,
        "years_fit": route.years_fit if route else None,
        "combined": route.combined if route else None,
        "route_reason": route.reason if route else "",
        "score_status": score.status,
        "total": score.total,
        "tier": tier,
        "reason_code": score.reason_code,
    }


def _route_view(case: QualityCase, actual: Dict[str, Label]) -> str:
    """Classify one case for the confusion matrix and accuracy views."""
    if actual["score_status"] != "SCORED":
        return "INTERCEPTED" if case.expected_route == "INTERCEPT" else "GATED"
    return str(actual["route_id"])


def _quality_views(cases: Sequence[QualityCase], actuals: Sequence[Optional[Dict[str, Label]]]) -> dict:
    matrix = {expected: {actual: 0 for actual in ROUTED_ROUTES} for expected in ROUTED_ROUTES}
    routed = 0
    correct = 0
    gated = []
    intercepted_expected = 0
    intercepted = 0
    groups = {
        group: {"total": 0, "route_correct": 0, "routed": 0, "gated": 0, "intercepted": 0}
        for group in GROUPS
    }
    mismatches = []
    for case, actual in zip(cases, actuals):
        if actual is None:
            continue
        view = _route_view(case, actual)
        group_stats = groups[case.group]
        group_stats["total"] += 1
        if view == "GATED":
            gated.append({"case_id": case.case_id, "reason_code": actual["reason_code"]})
            group_stats["gated"] += 1
            continue
        if view == "INTERCEPTED":
            intercepted_expected += 1
            intercepted += 1
            group_stats["intercepted"] += 1
            continue
        routed += 1
        group_stats["routed"] += 1
        matrix[case.expected_route][view] += 1
        if case.expected_route == view:
            correct += 1
            group_stats["route_correct"] += 1
        else:
            mismatches.append({
                "case_id": case.case_id,
                "group": case.group,
                "expected_route": case.expected_route,
                "actual_route": view,
                "route_id": actual["route_id"],
                "skill_ratio": actual["skill_ratio"],
                "years_fit": actual["years_fit"],
                "combined": actual["combined"],
                "route_reason": actual["route_reason"],
            })
    return {
        "confusion_matrix": {
            "rows_expected": ROUTED_ROUTES,
            "columns_actual": list(ROUTED_ROUTES),
            "counts": matrix,
        },
        "route_accuracy": {
            "routed": routed,
            "correct": correct,
            "rate": round(correct / routed, 4) if routed else None,
        },
        "abnormal_interception": {
            "expected": intercepted_expected,
            "intercepted": intercepted,
            "rate": round(intercepted / intercepted_expected, 4) if intercepted_expected else None,
        },
        "gated_cases": gated,
        "group_accuracy": groups,
        "mismatches": mismatches,
    }


def evaluate(cases: Sequence[QualityCase], manifest_sha256: str) -> dict:
    """Run real screening per case, lock baseline fields, derive quality views."""
    results = []
    actuals: List[Optional[Dict[str, Label]]] = []
    passed = 0
    for case in cases:
        entry = {"case_id": case.case_id, "group": case.group, "expected_route": case.expected_route}
        try:
            actual = _actual_fields(screen_one(case.pdf))
        except Exception as exc:
            entry.update(actual=None, passed=False, error_type=type(exc).__name__)
            actuals.append(None)
        else:
            mismatches = {
                field: {"baseline": case.baseline[field], "actual": actual[field]}
                for field in case.baseline
                if case.baseline[field] != actual[field]
            }
            entry.update(actual=actual, passed=not mismatches, mismatches=mismatches)
            actuals.append(actual)
            passed += int(not mismatches)
        results.append(entry)

    return {
        "mode": "mock-cn30",
        "manifest_sha256": manifest_sha256,
        "pdf_count": len({case.pdf for case in cases}),
        "case_count": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "quality": _quality_views(cases, actuals),
        "cases": results,
        "limitations": LIMITATIONS,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        cases, manifest_sha256 = load_manifest(args.manifest)
    except OSError:
        print("Cannot read the evaluation manifest.", file=sys.stderr)
        return 2
    except (ValueError, csv.Error) as exc:
        print(f"Invalid evaluation manifest: {exc}", file=sys.stderr)
        return 2
    try:
        import fitz  # PyMuPDF is already a project dependency.
    except ImportError:
        print("PyMuPDF is required for the PDF evaluation.", file=sys.stderr)
        return 2

    report = evaluate(cases, manifest_sha256)
    try:
        output = args.output.resolve()
        if (
            output in (args.manifest.resolve(), DEFAULT_MANIFEST.resolve())
            or RESUMES_CN.resolve() in output.parents
        ):
            print("Choose a report path outside the manifest and PDF fixtures.", file=sys.stderr)
            return 2
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        print("Cannot write the evaluation report.", file=sys.stderr)
        return 2

    quality = report["quality"]
    accuracy = quality["route_accuracy"]
    interception = quality["abnormal_interception"]
    rate = accuracy["rate"]
    print(
        f"Chinese PDF evaluation: {report['passed']}/{report['case_count']} baseline fields "
        f"locked, {report['failed']} failed, {report['pdf_count']} unique PDFs.",
        f"Route accuracy {accuracy['correct']}/{accuracy['routed']}"
        + (f" ({rate:.1%})" if rate is not None else ""),
        f"abnormal interception {interception['intercepted']}/{interception['expected']},",
        f"gated before routing {len(quality['gated_cases'])}.",
    )
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
