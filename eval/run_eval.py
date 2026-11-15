"""Compare single-PDF screening with the offline Mock evaluation manifest."""

import argparse
import csv
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path, PureWindowsPath
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple, Union

from app.screen import ScreenResult, screen_one


ROOT = Path(__file__).resolve().parents[1]
RESUMES = ROOT / "data" / "synthetic" / "resumes"
DEFAULT_MANIFEST = ROOT / "eval" / "manifest.csv"
DEFAULT_OUTPUT = ROOT / "eval" / "reports" / "latest.json"
SCORE_LIMITS = {"skill": 40, "project": 30, "years": 15, "education": 10, "bonus": 5}
FIELDS = (
    "case_id", "pdf_path", "scenario", "extract_status", "guard_status",
    "route_id", "score_status", "score_jd_id", "total", *SCORE_LIMITS, "reason_code",
)
SCENARIOS = {"default", "empty_jobs", "empty_project_score", "out_of_range_project_score"}
LIMITATIONS = [
    "Regression baseline for synthetic English PDFs and deterministic Mock scoring.",
    "English education and negation can differ from the Chinese text fixtures.",
    "Injected scorer failures test the pipeline, not an external model.",
]
Label = Union[int, str, None]


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    pdf: Path
    scenario: str
    expected: Dict[str, Label]


def _fixture_path(value: str) -> Path:
    # Recognize Windows drive and UNC forms even when running on Unix.
    windows = PureWindowsPath(value)
    relative = Path(value)
    if windows.anchor or relative.is_absolute() or ".." in windows.parts:
        raise ValueError("pdf_path must be relative and remain in the synthetic resume directory")
    path = (ROOT / relative).resolve()
    if RESUMES.resolve() not in path.parents or path.suffix.lower() != ".pdf":
        raise ValueError("pdf_path must point to a synthetic PDF")
    if not path.is_file():
        raise ValueError("synthetic PDF is missing")
    return path


def _number(value: str, field: str, minimum: int, maximum: int) -> int:
    if not re.fullmatch(r"-?[0-9]+", value):
        raise ValueError(f"{field} must be an integer")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{field} is out of range")
    return number


def _parse_case(row: Dict[str, str]) -> EvalCase:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", row["case_id"]):
        raise ValueError("invalid case_id")
    if row["scenario"] not in SCENARIOS:
        raise ValueError("unknown scenario")
    if row["extract_status"] not in {"OK", "EMPTY", "ERROR", "DEPENDENCY_MISSING"}:
        raise ValueError("invalid extract_status")
    if row["guard_status"] not in {"PASS", "REVIEW"}:
        raise ValueError("invalid guard_status")
    if row["score_status"] not in {"SCORED", "REVIEW"}:
        raise ValueError("invalid score_status")
    for field in ("route_id", "score_jd_id", "reason_code"):
        if not re.fullmatch(r"[A-Za-z0-9_-]*", row[field]):
            raise ValueError(f"invalid {field}")

    expected: Dict[str, Label] = {field: row[field] for field in FIELDS[3:]}
    if row["score_status"] == "REVIEW":
        expected["total"] = _number(row["total"], "total", -1, -1)
        if not row["reason_code"] or any(row[field] for field in SCORE_LIMITS):
            raise ValueError("REVIEW requires a reason_code and blank subscores")
        expected.update(dict.fromkeys(SCORE_LIMITS))
    else:
        expected["total"] = _number(row["total"], "total", 0, 100)
        if row["reason_code"] or not row["route_id"] or not row["score_jd_id"]:
            raise ValueError("SCORED requires both job IDs and a blank reason_code")
        for field, limit in SCORE_LIMITS.items():
            expected[field] = _number(row[field], field, 0, limit)
    return EvalCase(row["case_id"], _fixture_path(row["pdf_path"]), row["scenario"], expected)


def load_manifest(path: Path) -> Tuple[List[EvalCase], str]:
    """Read and validate a manifest, returning its cases and content hash."""
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


def _screen(case: EvalCase) -> ScreenResult:
    if case.scenario == "empty_jobs":
        return screen_one(case.pdf, jobs=[])
    if case.scenario == "empty_project_score":
        return screen_one(case.pdf, project_scorer=lambda *_: None)
    if case.scenario == "out_of_range_project_score":
        return screen_one(case.pdf, project_scorer=lambda *_: 31)
    return screen_one(case.pdf)


def _actual_fields(result: ScreenResult) -> Dict[str, Label]:
    score = result.score
    fields: Dict[str, Label] = {
        "extract_status": result.extract_status,
        "guard_status": result.guard_status,
        "route_id": result.route.jd_id if result.route else "",
        "score_status": score.status,
        "score_jd_id": score.jd_id,
        "total": score.total,
        "reason_code": score.reason_code,
    }
    for field in SCORE_LIMITS:
        fields[field] = getattr(score.scores, field) if score.scores is not None else None
    return fields


def evaluate(cases: Sequence[EvalCase], manifest_sha256: str) -> dict:
    """Run the real screening entry point and compare each labeled field."""
    results = []
    counts = {"SCORED": 0, "REVIEW": 0}
    passed = 0
    for case in cases:
        entry = {"case_id": case.case_id, "scenario": case.scenario, "expected": case.expected}
        try:
            actual = _actual_fields(_screen(case))
        except Exception as exc:
            entry.update(actual=None, passed=False, error_type=type(exc).__name__)
        else:
            status = actual["score_status"]
            if status in counts:
                counts[status] += 1
            mismatches = {
                field: {"expected": expected, "actual": actual[field]}
                for field, expected in case.expected.items()
                if expected != actual[field]
            }
            entry.update(actual=actual, passed=not mismatches, mismatches=mismatches)
            passed += int(not mismatches)
        results.append(entry)

    return {
        "mode": "mock",
        "manifest_sha256": manifest_sha256,
        "pdf_count": len({case.pdf for case in cases}),
        "case_count": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "score_status_counts": counts,
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
            or RESUMES.resolve() in output.parents
        ):
            print("Choose a report path outside the manifest and PDF fixtures.", file=sys.stderr)
            return 2
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        print("Cannot write the evaluation report.", file=sys.stderr)
        return 2
    print(
        f"Mock PDF evaluation: {report['passed']}/{report['case_count']} passed, "
        f"{report['failed']} failed, {report['pdf_count']} unique PDFs.",
        f"SCORED={report['score_status_counts']['SCORED']}, "
        f"REVIEW={report['score_status_counts']['REVIEW']}.",
    )
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
