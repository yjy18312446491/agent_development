"""Blind human-annotation workflow for project scores: worksheet generation and agreement check.

The frozen JSONL labels (12 original + 3 v2 contrast cases) are the existing gold.
This entrypoint produces a blind worksheet (snippet plus job requirements only; no
gold scores, no rubric reasons) for a second human annotator, then checks a filled
worksheet against the frozen gold with per-dimension exact match, MAE and quadratic
weighted Cohen's kappa. Agreement is descriptive and never gates anything; zero
HTTP, zero key reads, frozen SHAs revalidated on every run.
"""

import argparse
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Mapping, Optional, Sequence
from uuid import uuid4

from app.project_scorer import DIMENSIONS, POINT_LEVELS
from app.route import load_jobs
from eval.run_deepseek_pilot import LABELS, V2_EXTRA_LABELS, _read_labels


ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "data/synthetic/jobs/jobs.json"
REPORT_DIR = ROOT / "eval/reports"
WORKSHEET_PREFIX = "annotation-worksheet-"
CHECK_PREFIX = "annotation-check-"
WORKSHEET_COLUMNS = ("case_id", "jd_id", "job_requirements", "snippet", "annotator",
                     "relevance", "contribution", "result", "notes")


def _load_gold() -> tuple:
    """Frozen labels in file order: 12 original cases, then the 3 v2 contrast cases."""
    original_content = LABELS.read_bytes()
    original = _read_labels(original_content)
    extra_content = V2_EXTRA_LABELS.read_bytes()
    extra = _read_labels(extra_content)
    if len(original) != 12 or len(extra) != 3:
        raise ValueError("ANNOTATION_GOLD_INVALID")
    if set(original) & set(extra):
        raise ValueError("ANNOTATION_GOLD_INVALID")
    return (list(original.values()) + list(extra.values()),
            sha256(original_content).hexdigest(), sha256(extra_content).hexdigest())


def _job_requirements(jobs_by_id) -> dict:
    requirements = {}
    for jd_id, job in jobs_by_id.items():
        text = job.raw_text.strip()
        requirements[jd_id] = text if text else job.title
    return requirements


def worksheet_destination(output=None) -> Path:
    path = Path(output) if output is not None else REPORT_DIR / (
        WORKSHEET_PREFIX + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + "-" + uuid4().hex[:8] + ".csv")
    if (path.resolve().parent != REPORT_DIR.resolve() or path.suffix != ".csv"
            or not path.name.startswith(WORKSHEET_PREFIX)):
        raise ValueError("ANNOTATION_WORKSHEET_PATH")
    return path


def check_destination(output=None) -> Path:
    path = Path(output) if output is not None else REPORT_DIR / (
        CHECK_PREFIX + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + "-" + uuid4().hex[:8] + ".json")
    if (path.resolve().parent != REPORT_DIR.resolve() or path.suffix != ".json"
            or not path.name.startswith(CHECK_PREFIX)):
        raise ValueError("ANNOTATION_CHECK_PATH")
    return path


def generate_worksheet(destination: Path, gold: Sequence[dict], requirements: Mapping) -> int:
    """Blind worksheet: evidence only. Gold scores and rubric reasons are excluded."""
    if destination.exists():
        raise ValueError("ANNOTATION_WORKSHEET_EXISTS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(WORKSHEET_COLUMNS)
        for case in gold:
            writer.writerow([
                case["case_id"], case["jd_id"], requirements[case["jd_id"]], case["snippet"],
                "", "", "", "",
            ])
    return len(gold)


def quadratic_weighted_kappa(rater_a: Sequence, rater_b: Sequence,
                             levels: Sequence = POINT_LEVELS) -> Optional[float]:
    """Quadratic weighted Cohen's kappa over a fixed ordinal category list.

    None when both raters are constant on the same category (weighted sums are zero
    and kappa is undefined). Both inputs must use only the fixed categories.
    """
    if (len(rater_a) != len(rater_b) or not rater_a
            or any(value not in levels for value in rater_a)
            or any(value not in levels for value in rater_b)
            or len(set(levels)) != len(levels)):
        raise ValueError("ANNOTATION_KAPPA_INPUT")
    count = len(rater_a)
    observed = {a: {b: 0 for b in levels} for a in levels}
    for a, b in zip(rater_a, rater_b):
        observed[a][b] += 1
    row_margins = {a: sum(observed[a].values()) for a in levels}
    column_margins = {b: sum(observed[a][b] for a in levels) for b in levels}
    weighted_observed = 0.0
    weighted_expected = 0.0
    for index_a, a in enumerate(levels):
        for index_b, b in enumerate(levels):
            weight = (index_a - index_b) ** 2 / (len(levels) - 1) ** 2
            weighted_observed += weight * observed[a][b] / count
            weighted_expected += weight * row_margins[a] * column_margins[b] / count ** 2
    if weighted_expected == 0:
        return None
    return 1.0 - weighted_observed / weighted_expected


def _read_filled_worksheet(path: Path, gold: Sequence[dict]) -> dict:
    """Parse a filled worksheet and validate it against the frozen case list."""
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(WORKSHEET_COLUMNS):
                raise ValueError("ANNOTATION_WORKSHEET_COLUMNS")
            rows = list(reader)
    except OSError:
        raise
    except (csv.Error, UnicodeError):
        raise ValueError("ANNOTATION_WORKSHEET_COLUMNS") from None
    gold_by_id = {case["case_id"]: case for case in gold}
    filled = {}
    errors = []
    for row in rows:
        case_id = (row.get("case_id") or "").strip()
        if case_id not in gold_by_id:
            errors.append({"case_id": case_id or "(blank)", "error": "UNKNOWN_CASE_ID"})
            continue
        if case_id in filled:
            errors.append({"case_id": case_id, "error": "DUPLICATE_ROW"})
            continue
        if (row.get("snippet") or "") != gold_by_id[case_id]["snippet"]:
            errors.append({"case_id": case_id, "error": "SNIPPET_TAMPERED"})
            continue
        scores = {}
        for name in DIMENSIONS:
            raw = (row.get(name) or "").strip()
            try:
                value = int(raw)
            except ValueError:
                errors.append({"case_id": case_id, "error": "MISSING_OR_NON_INTEGER", "dimension": name})
                continue
            if value not in POINT_LEVELS:
                errors.append({"case_id": case_id, "error": "SCORE_NOT_ON_RUBRIC", "dimension": name,
                               "given": value})
                continue
            scores[name] = value
        if not (row.get("annotator") or "").strip():
            errors.append({"case_id": case_id, "error": "ANNOTATOR_MISSING"})
        filled[case_id] = scores
    missing = [case["case_id"] for case in gold if case["case_id"] not in filled]
    if missing:
        errors.append({"case_id": ",".join(missing), "error": "MISSING_ROWS"})
    if errors:
        raise _WorksheetErrors(errors)
    return filled


class _WorksheetErrors(Exception):
    def __init__(self, errors):
        super().__init__("Invalid filled worksheet")
        self.errors = errors


def check_agreement(filled: Mapping, gold: Sequence[dict]) -> dict:
    """Descriptive agreement only; mismatches go to human adjudication."""
    by_id = {case["case_id"]: case for case in gold}
    dimension_agreement = {}
    for name in DIMENSIONS:
        gold_scores = [by_id[case_id][name] for case_id in by_id]
        given_scores = [filled[case_id][name] for case_id in by_id]
        dimension_agreement[name] = {
            "exact_match": sum(a == b for a, b in zip(gold_scores, given_scores)),
            "mae": round(sum(abs(a - b) for a, b in zip(gold_scores, given_scores)) / len(gold_scores), 4),
            "quadratic_weighted_kappa": quadratic_weighted_kappa(given_scores, gold_scores),
        }
    mismatches = []
    for case in gold:
        case_id = case["case_id"]
        deltas = {name: filled[case_id][name] - case[name] for name in DIMENSIONS}
        if any(deltas.values()):
            mismatches.append({
                "case_id": case_id,
                "gold": {name: case[name] for name in DIMENSIONS},
                "given": {name: filled[case_id][name] for name in DIMENSIONS},
                "delta": deltas,
            })
    gold_totals = [by_id[case_id]["total"] for case_id in by_id]
    given_totals = [sum(filled[case_id][name] for name in DIMENSIONS) for case_id in by_id]
    return {
        "case_count": len(gold),
        "dimension_agreement": dimension_agreement,
        "all_dimensions_match_count": sum(1 for case in gold
                                          if all(filled[case["case_id"]][name] == case[name]
                                                 for name in DIMENSIONS)),
        "total_mae": round(sum(abs(a - b) for a, b in zip(given_totals, gold_totals)) / len(gold_totals), 4),
        "mismatches": mismatches,
        "quality_conclusion": "DESCRIPTIVE_NOT_GATED",
        "http_attempts": 0, "real_http_requests": 0,
        "limitations": [
            "One annotator versus the frozen gold; single-rater agreement is descriptive, not accuracy.",
            "Mismatches are adjudicated by humans against the rubric; this tool never retunes gold labels.",
            "The frozen labels stay byte-identical; report files are local and not committed.",
        ],
    }


def check_worksheet(path: Path, destination: Path) -> dict:
    gold, labels_sha, extra_sha = _load_gold()
    filled = _read_filled_worksheet(path, gold)
    report = {
        "mode": "annotation-check", "worksheet": path.name,
        "worksheet_sha256": sha256(path.read_bytes()).hexdigest(),
        "labels_sha256": labels_sha, "extra_labels_sha256": extra_sha,
    }
    report.update(check_agreement(filled, gold))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return report


def _preview() -> int:
    gold, labels_sha, extra_sha = _load_gold()
    print("Blind annotation preview: %d cases (12 original + 3 v2 contrast); "
          "labels %s…, extras %s…; rubric %s; 0 real HTTP requests." %
          (len(gold), labels_sha[:8], extra_sha[:8], list(POINT_LEVELS)))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worksheet", action="store_true",
                        help="generate a blind CSV worksheet under eval/reports")
    parser.add_argument("--check", type=Path,
                        help="validate a filled worksheet and compute agreement with the frozen gold")
    parser.add_argument("--output", help="override the generated worksheet/check report path")
    args = parser.parse_args(argv)
    if args.worksheet and args.check:
        parser.error("--worksheet and --check are mutually exclusive")
    if not args.worksheet and not args.check:
        return _preview()
    try:
        gold, _labels_sha, _extra_sha = _load_gold()
        jobs_by_id = {job.jd_id: job for job in load_jobs(JOBS.parent)}
        requirements = _job_requirements(jobs_by_id)
        if args.worksheet:
            destination = worksheet_destination(args.output)
            count = generate_worksheet(destination, gold, requirements)
            print("Blind worksheet written: %s (%d rows); fill annotator/relevance/contribution/result, "
                  "then run --check." % (destination.name, count))
            return 0
        destination = check_destination(args.output)
        report = check_worksheet(args.check, destination)
        print("Annotation check: %d cases; all-dimension match %d/%d; total MAE %.4f; %d mismatches; "
              "0 real HTTP requests." % (report["case_count"], report["all_dimensions_match_count"],
                                         report["case_count"], report["total_mae"],
                                         len(report["mismatches"])))
        return 1 if report["mismatches"] else 0
    except _WorksheetErrors as exc:
        for error in exc.errors[:10]:
            print("Worksheet error: %s" % json.dumps(error, ensure_ascii=False), file=sys.stderr)
        print("Filled worksheet is invalid; fix the listed rows and rerun.", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("Annotation workflow failed; check frozen labels or report paths.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
