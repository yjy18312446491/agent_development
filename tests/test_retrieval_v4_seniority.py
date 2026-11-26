"""v4 seniority rerank stays offline, deterministic, and never touches screening."""

import hashlib
import json
from pathlib import Path
import socket
import urllib.request

import pytest

from app.route import JobPosting, load_jobs
from eval import run_retrieval_eval as v1
from eval import run_retrieval_heldout as heldout
from eval import run_retrieval_v2 as v2
from eval import run_retrieval_v4_seniority as v4


@pytest.fixture(autouse=True)
def no_network_or_env(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("v4 evaluation must not read credentials or make network requests")

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
def screen_jobs():
    return tuple(load_jobs(v1.JOBS.parent))


FROZEN_YEARS = {
    "RV001": 4.0, "RV002": 3.5, "RV003": 5.0, "RV004": 0.0, "RV005": 1.5,
    "RV006": 0.5, "RV007": 2.0, "RV008": 1.5, "RV009": 3.0,
    "RV010": None, "RV011": None, "RV012": None,
    "HV001": 4.0, "HV002": 3.5, "HV003": 6.0, "HV004": 0.0, "HV005": 1.0,
    "HV006": 2.0, "HV007": 1.5, "HV008": 2.0, "HV009": 4.0,
    "HV010": None, "HV011": None, "HV012": None,
    "QV001": 5.0, "QV002": 3.5, "QV003": 0.0, "QV004": 1.0, "QV005": 2.0,
    "QV006": 1.5, "QV007": 4.0, "QV008": 3.5, "QV009": 3.0, "QV010": 2.0,
    "QV011": 3.0, "QV012": 2.0,
    "QV013": None, "QV014": None, "QV015": None, "QV016": None,
    "QV017": None, "QV018": None,
}


def test_extract_years_reads_half_patterns_chinese_numerals_and_fresh_grad():
    assert v4.extract_experience_years("一年问卷系统后端经验") == 1.0
    assert v4.extract_experience_years("一年半后端经验") == 1.5
    assert v4.extract_experience_years("半年 Python 开发实习") == 0.5
    assert v4.extract_experience_years("两年订单宽表 ETL") == 2.0
    assert v4.extract_experience_years("两年半项目") == 2.5
    assert v4.extract_experience_years("三年半服务端经验") == 3.5
    assert v4.extract_experience_years("5年后端") == 5.0
    assert v4.extract_experience_years("2.5年实习") == 2.5
    assert v4.extract_experience_years("十年经验") == 10.0
    assert v4.extract_experience_years("十二年经验") == 12.0
    assert v4.extract_experience_years("应届毕业生，无全职年限") == 0.0
    assert v4.extract_experience_years("应届生，没有全职开发年限") == 0.0
    assert v4.extract_experience_years("中学化学教师，负责备课和批改作业") is None
    with pytest.raises(ValueError, match="V4_YEARS_INPUT"):
        v4.extract_experience_years(7)
    with pytest.raises(ValueError, match="V4_YEARS_INPUT"):
        v4.extract_experience_years("   ")
    with pytest.raises(ValueError, match="V4_YEARS_TOKEN"):
        v4.extract_experience_years("零三年经验")


def test_years_feasible_uses_min_max_window_and_validates_input(screen_jobs):
    by_id = {job.jd_id: job for job in screen_jobs}
    assert v4.years_feasible(0.0, by_id["JD2"])
    assert v4.years_feasible(2.5, by_id["JD2"])
    assert not v4.years_feasible(2.6, by_id["JD2"])
    assert v4.years_feasible(3.0, by_id["JD1"])
    assert not v4.years_feasible(2.9, by_id["JD1"])
    with pytest.raises(ValueError, match="V4_FEASIBLE_INPUT"):
        v4.years_feasible(1.0, {"jd_id": "JD2"})
    with pytest.raises(ValueError, match="V4_FEASIBLE_INPUT"):
        v4.years_feasible(True, by_id["JD2"])


def test_rerank_demotes_infeasible_jobs_and_keeps_order_neutral_on_no_years(screen_jobs):
    by_id = {job.jd_id: job for job in screen_jobs}
    junior_query = "一年问卷系统后端经验，使用 Python、Flask 与 MySQL。"
    assert v4.seniority_rerank(("JD1", "JD2", "JD3"), screen_jobs, junior_query) == ("JD2", "JD3", "JD1")
    neutral_query = "负责线下读书会的报名登记和活动运营"
    assert v4.seniority_rerank(("JD1", "JD2", "JD3"), screen_jobs, neutral_query) == ("JD1", "JD2", "JD3")
    senior_query = "四年后端开发，使用 Python 和 FastAPI。"
    assert v4.seniority_rerank(("JD1", "JD2", "JD3"), screen_jobs, senior_query) == ("JD1", "JD3", "JD2")
    assert by_id["JD1"].jd_id == "JD1"
    with pytest.raises(ValueError, match="V4_RERANK_INPUT"):
        v4.seniority_rerank(("JD1", "JD1"), screen_jobs, junior_query)
    with pytest.raises(ValueError, match="V4_RERANK_INPUT"):
        v4.seniority_rerank(("JD9",), screen_jobs, junior_query)
    with pytest.raises(ValueError, match="V4_RERANK_INPUT"):
        v4.seniority_rerank(("JD1",), (by_id["JD1"], by_id["JD1"]), junior_query)
    with pytest.raises(ValueError, match="V4_RERANK_INPUT"):
        v4.seniority_rerank("JD1", screen_jobs, junior_query)


def test_extraction_matches_frozen_queries_exactly():
    for dataset in (v1.load_dataset(), heldout.load_dataset(), v2.load_dataset()):
        for case in dataset.cases:
            assert v4.extract_experience_years(case.query) == FROZEN_YEARS[case.case_id], case.case_id


def test_control_rerank_fixes_years_type_errors_without_breaking_any_case():
    report = v4.evaluate()
    by_label = {section["dataset"]: section for section in report["datasets"]}
    assert by_label["v1"]["dense_metrics"]["recall_at_1"] == pytest.approx(6 / 9)
    assert by_label["v1"]["reranked_metrics"]["recall_at_1"] == pytest.approx(8 / 9)
    assert by_label["heldout"]["dense_metrics"]["recall_at_1"] == pytest.approx(6 / 9)
    assert by_label["heldout"]["reranked_metrics"]["recall_at_1"] == pytest.approx(8 / 9)
    assert by_label["v2"]["dense_metrics"]["recall_at_1"] == pytest.approx(9 / 12)
    assert by_label["v2"]["reranked_metrics"]["recall_at_1"] == pytest.approx(12 / 12)
    for section in report["datasets"]:
        assert section["reranked_metrics"]["recall_at_3"] == pytest.approx(1.0)
        assert section["guards"] == {"relevant_demoted_from_top1": 0, "relevant_lost_from_top3": 0}
        assert section["cases"] == sorted(section["cases"], key=lambda row: row["id"])
        for row in section["cases"]:
            assert not row["broken_top1"]
    fixed = {row["id"] for section in report["datasets"] for row in section["cases"] if row["fixed_top1"]}
    assert fixed == {"RV002", "RV005", "HV002", "HV005", "QV002", "QV004", "QV010"}
    assert report["http_attempts"] == 0 and report["real_http_requests"] == 0


def _write_live_report(path: Path, dataset, override_top1=None):
    jobs = dataset.jobs
    rows = []
    for case in dataset.cases:
        grades = dict(zip([j.jd_id for j in jobs], case.grades))
        ranking = ["JD1", "JD2", "JD3"][:len(jobs)]
        if override_top1 and case.case_id in override_top1:
            first = override_top1[case.case_id]
            ranking = [first] + [jd for jd in ranking if jd != first]
        rows.append({
            "id": case.case_id,
            "query_sha256": hashlib.sha256(case.query.encode("utf-8")).hexdigest(),
            "status": "OK",
            "rankings": [{"reference_jd_id": jd_id, "similarity": 0.5} for jd_id in ranking],
        })
    report = {"dataset_sha256": dataset.canonical_sha256, "metrics_status": "DESCRIPTIVE_BASELINE",
              "cases": rows}
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return report


def test_replay_reorders_persisted_rankings_and_skips_missing_reports(tmp_path, screen_jobs):
    dataset = v1.load_dataset()
    path = tmp_path / "retrieval-baseline-live-round.json"
    _write_live_report(path, dataset, override_top1={"RV005": "JD1"})
    section = v4.replay_dataset("v1", dataset, path)
    assert section["status"] == "replayed"
    # With JD1 first everywhere, RV004/RV006 (wrong JD1 top-1, feasible JD2) are also repaired.
    assert section["fixed_case_ids"] == ["RV004", "RV005", "RV006"]
    assert section["broken_case_ids"] == []
    assert section["guards"] == {"relevant_demoted_from_top1": 0, "relevant_lost_from_top3": 0}
    rv005 = next(row for row in section["cases"] if row["id"] == "RV005")
    assert rv005["live_top3"] == ["JD1", "JD2", "JD3"] and rv005["reranked_top3"][0] == "JD2"
    missing = v4.replay_dataset("v1", dataset, tmp_path / "absent.json")
    assert missing == {"dataset": "v1", "status": "skipped_missing_report",
                       "report_path": "absent.json"}


def test_replay_rejects_reports_from_another_dataset_or_tampered_queries(tmp_path):
    dataset = v1.load_dataset()
    path = tmp_path / "wrong-dataset.json"
    _write_live_report(path, dataset)
    with pytest.raises(ValueError, match="V4_REPLAY_REPORT_INVALID"):
        v4.replay_dataset("heldout", heldout.load_dataset(), path)
    heldout_path = tmp_path / "heldout.json"
    heldout_dataset = heldout.load_dataset()
    _write_live_report(heldout_path, heldout_dataset)
    raw = json.loads(heldout_path.read_text(encoding="utf-8"))
    raw["cases"][0]["query_sha256"] = "0" * 64
    heldout_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="V4_REPLAY_REPORT_INVALID"):
        v4.replay_dataset("heldout", heldout_dataset, heldout_path)
    raw = json.loads(heldout_path.read_text(encoding="utf-8"))
    raw["dataset_sha256"] = "0" * 64
    heldout_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="V4_REPLAY_REPORT_INVALID"):
        v4.replay_dataset("heldout", heldout_dataset, heldout_path)


def test_replay_of_all_three_live_reports_fixes_only_the_known_misrankings():
    sections = v4.replay({"v1": v4.LIVE_REPORTS["v1"], "heldout": v4.LIVE_REPORTS["heldout"],
                          "v2": v4.LIVE_REPORTS["v2"]})
    by_label = {section["dataset"]: section for section in sections}
    assert by_label["v1"]["live_recall_at_1"] == pytest.approx(8 / 9)
    assert by_label["v1"]["reranked_recall_at_1"] == pytest.approx(9 / 9)
    assert by_label["heldout"]["live_recall_at_1"] == pytest.approx(8 / 9)
    assert by_label["heldout"]["reranked_recall_at_1"] == pytest.approx(9 / 9)
    assert by_label["v2"]["live_recall_at_1"] == pytest.approx(11 / 12)
    assert by_label["v2"]["reranked_recall_at_1"] == pytest.approx(12 / 12)
    for section in sections:
        assert section["broken_case_ids"] == []
        assert section["guards"] == {"relevant_demoted_from_top1": 0, "relevant_lost_from_top3": 0}
    assert by_label["v1"]["fixed_case_ids"] == ["RV005"]
    assert by_label["heldout"]["fixed_case_ids"] == ["HV005"]
    assert by_label["v2"]["fixed_case_ids"] == ["QV004"]


def test_report_destination_prefix_and_rejections(monkeypatch, tmp_path):
    monkeypatch.setattr(v4, "REPORT_DIR", tmp_path)
    destination = v4.report_destination()
    assert destination.name.startswith("retrieval-v4-seniority-") and destination.suffix == ".json"
    with pytest.raises(ValueError, match="V4_REPORT_PATH"):
        v4.report_destination(tmp_path.parent / "other.json")
    assert v4.main(["--live"]) == 2
    assert v4.main([]) == 2
