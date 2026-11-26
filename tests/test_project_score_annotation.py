"""Human annotation workflow stays offline, blind, and never mutates frozen gold."""

import csv
import json
from pathlib import Path
import socket
import urllib.request

import pytest

from app.project_scorer import DIMENSIONS, POINT_LEVELS
from app.route import load_jobs
from eval import run_project_score_annotation as anno


@pytest.fixture(autouse=True)
def no_network_or_env(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Annotation workflow must not read credentials or make network requests")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    original_bytes, original_text = Path.read_bytes, Path.read_text

    def read_bytes(path, *args, **kwargs):
        if path.name == ".env":
            forbidden()
        return original_bytes(path, *args, **kwargs)

    def read_text(path, *args, **kwargs):
        if path.name == ".env":
            forbidden()
        return original_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "read_text", read_text)


@pytest.fixture(scope="module")
def gold():
    return anno._load_gold()[0]


def _fill_from_gold(tmp_path, gold, overrides=None, *, annotator="第二标注者", extra_rows=()):
    path = tmp_path / "filled.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(anno.WORKSHEET_COLUMNS)
        for case in gold:
            scores = {name: case[name] for name in DIMENSIONS}
            if overrides and case["case_id"] in overrides:
                scores.update(overrides[case["case_id"]])
            writer.writerow([case["case_id"], case["jd_id"], "要求文本", case["snippet"],
                             annotator, scores["relevance"], scores["contribution"],
                             scores["result"], ""])
        for row in extra_rows:
            writer.writerow(row)
    return path


def test_gold_loads_frozen_cases_in_order(gold):
    assert len(gold) == 15
    assert [case["case_id"] for case in gold][-3:] == ["i_joined_order_api", "team_claimed_faster",
                                                       "flask_done_no_test"]
    for case in gold:
        assert case["total"] == sum(case[name] for name in DIMENSIONS)
        assert all(case[name] in POINT_LEVELS for name in DIMENSIONS)


def test_worksheet_is_blind_and_refuses_overwrite(monkeypatch, tmp_path, gold):
    monkeypatch.setattr(anno, "REPORT_DIR", tmp_path)
    requirements = anno._job_requirements({job.jd_id: job for job in load_jobs(anno.JOBS.parent)})
    destination = anno.worksheet_destination()
    assert anno.generate_worksheet(destination, gold, requirements) == 15
    rows = list(csv.DictReader(destination.open("r", encoding="utf-8-sig")))
    assert len(rows) == 15
    assert set(rows[0]) == set(anno.WORKSHEET_COLUMNS)
    assert all(not row["annotator"] and not row["relevance"] and not row["notes"] for row in rows)
    assert all(not row.get("reason") and not row.get("total") for row in rows)
    by_id = {case["case_id"]: case for case in gold}
    assert all(row["snippet"] == by_id[row["case_id"]]["snippet"] for row in rows)
    assert all("岗位" in row["job_requirements"] for row in rows)
    with pytest.raises(ValueError, match="ANNOTATION_WORKSHEET_EXISTS"):
        anno.generate_worksheet(destination, gold, requirements)
    with pytest.raises(ValueError, match="ANNOTATION_WORKSHEET_PATH"):
        anno.worksheet_destination(tmp_path.parent / "elsewhere.csv")


def test_quadratic_weighted_kappa_known_values_and_validation():
    levels = POINT_LEVELS
    perfect = [3, 7, 10, 0, 3]
    assert anno.quadratic_weighted_kappa(perfect, perfect, levels) == 1.0
    # Hand-computed: one adjacent-category swap out of five (w=1/9, kappa = 1 - 5/53).
    kappa = anno.quadratic_weighted_kappa([3, 7, 10, 0, 7], perfect, levels)
    assert kappa == pytest.approx(48 / 53)
    constant = [3, 3, 3]
    assert anno.quadratic_weighted_kappa(constant, constant, levels) is None
    with pytest.raises(ValueError, match="ANNOTATION_KAPPA_INPUT"):
        anno.quadratic_weighted_kappa([3, 5], [3, 3], levels)
    with pytest.raises(ValueError, match="ANNOTATION_KAPPA_INPUT"):
        anno.quadratic_weighted_kappa([3], [3, 3], levels)


def test_check_full_agreement_on_gold_filled_sheet(monkeypatch, tmp_path, gold):
    monkeypatch.setattr(anno, "REPORT_DIR", tmp_path)
    path = _fill_from_gold(tmp_path, gold)
    report = anno.check_worksheet(path, anno.check_destination())
    assert report["case_count"] == 15
    assert report["all_dimensions_match_count"] == 15
    assert report["mismatches"] == []
    assert report["total_mae"] == 0
    for name in DIMENSIONS:
        assert report["dimension_agreement"][name]["exact_match"] == 15
        assert report["dimension_agreement"][name]["mae"] == 0
        assert report["dimension_agreement"][name]["quadratic_weighted_kappa"] == 1.0
    assert report["http_attempts"] == 0 and report["real_http_requests"] == 0


def test_check_reports_mismatch_exit_one_for_invalid_sheets(monkeypatch, tmp_path, gold):
    monkeypatch.setattr(anno, "REPORT_DIR", tmp_path)
    path = _fill_from_gold(tmp_path, gold, overrides={"team_we": {"contribution": 3}})
    report = anno.check_worksheet(path, anno.check_destination())
    assert report["all_dimensions_match_count"] == 14
    assert len(report["mismatches"]) == 1
    mismatch = report["mismatches"][0]
    assert mismatch["case_id"] == "team_we"
    assert mismatch["delta"]["contribution"] == 3
    assert report["dimension_agreement"]["contribution"]["exact_match"] == 14
    assert report["dimension_agreement"]["contribution"]["mae"] == pytest.approx(3 / 15)

    invalid = _fill_from_gold(tmp_path, gold, overrides={"full_30": {"relevance": 5}})
    with pytest.raises(anno._WorksheetErrors) as excinfo:
        anno._read_filled_worksheet(invalid, gold)
    assert {"case_id": "full_30", "error": "SCORE_NOT_ON_RUBRIC", "dimension": "relevance",
            "given": 5} in excinfo.value.errors

    missing_annotator = _fill_from_gold(tmp_path, gold, annotator="")
    with pytest.raises(anno._WorksheetErrors) as excinfo:
        anno._read_filled_worksheet(missing_annotator, gold)
    assert any(error["error"] == "ANNOTATOR_MISSING" for error in excinfo.value.errors)

    with_extra = _fill_from_gold(tmp_path, gold, extra_rows=[["ghost_case", "JD1", "", "", "x", 3, 3, 3, ""]])
    with pytest.raises(anno._WorksheetErrors) as excinfo:
        anno._read_filled_worksheet(with_extra, gold)
    assert {"case_id": "ghost_case", "error": "UNKNOWN_CASE_ID"} in excinfo.value.errors


def test_check_rejects_tampered_snippet_and_wrong_columns(tmp_path, gold):
    tampered = _fill_from_gold(tmp_path, gold)
    rows = list(csv.DictReader(tampered.open("r", encoding="utf-8-sig")))
    with tampered.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=anno.WORKSHEET_COLUMNS)
        writer.writeheader()
        for row in rows:
            if row["case_id"] == "tools_only":
                row["snippet"] = row["snippet"] + "改动"
            writer.writerow(row)
    with pytest.raises(anno._WorksheetErrors) as excinfo:
        anno._read_filled_worksheet(tampered, gold)
    assert {"case_id": "tools_only", "error": "SNIPPET_TAMPERED"} in excinfo.value.errors

    wrong_columns = tmp_path / "wrong-columns.csv"
    wrong_columns.write_text("case_id,relevance\nempty,0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ANNOTATION_WORKSHEET_COLUMNS"):
        anno._read_filled_worksheet(wrong_columns, gold)


def test_main_modes_preview_generate_check_and_errors(monkeypatch, tmp_path, gold):
    monkeypatch.setattr(anno, "REPORT_DIR", tmp_path)
    assert anno.main([]) == 0
    assert anno.main(["--worksheet", "--output", str(tmp_path / "annotation-worksheet-sheet.csv")]) == 0
    sheet = tmp_path / "annotation-worksheet-sheet.csv"
    assert sheet.exists()
    assert anno.main(["--worksheet", "--output", str(sheet)]) == 2
    assert anno.main(["--check", str(tmp_path / "absent.csv")]) == 2
    with pytest.raises(SystemExit):
        anno.main(["--worksheet", "--check", str(sheet)])
    filled = _fill_from_gold(tmp_path, gold)
    assert anno.main(["--check", str(filled),
                      "--output", str(tmp_path / "annotation-check-report.json")]) == 0
    disagree = _fill_from_gold(tmp_path, gold, overrides={"tools_only": {"result": 3}})
    assert anno.main(["--check", str(disagree),
                      "--output", str(tmp_path / "annotation-check-report2.json")]) == 1
