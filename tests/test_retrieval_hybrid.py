"""Hybrid BM25+RRF stays offline, fuses deterministically, and never touches screening."""

import json
from pathlib import Path
import socket
import urllib.request

import pytest

from app.screen import screen_one
from eval import run_retrieval_hybrid as hy
from eval import run_retrieval_eval as v1
from eval import run_retrieval_heldout as heldout
from eval import run_retrieval_v2 as v2


@pytest.fixture(autouse=True)
def no_network_or_env(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Hybrid evaluation must not read credentials or make network requests")

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


def test_tokenize_mixed_ascii_and_cjk_bigrams():
    assert hy.tokenize("Python, Flask!") == ("python", "flask")
    assert hy.tokenize("会议室预定") == ("会议", "议室", "室预", "预定")
    assert hy.tokenize("FastAPI接口") == ("fastapi", "接口")
    with pytest.raises(ValueError, match="HYBRID_TOKENIZE_INPUT"):
        hy.tokenize(7)


def test_bm25_ranks_matching_document_first_and_breaks_ties_by_id():
    index = hy.BM25Index(["JD1", "JD2"], ["flask mysql 接口开发", "会议室预定与排班"])
    assert index.ranking("flask mysql 接口")[0] == "JD1"
    tie = hy.BM25Index(["JD1", "JD2"], ["完全相同文本", "完全相同文本"])
    assert tie.ranking("完全相同") == ("JD1", "JD2")
    assert index.scores("完全无关") == {"JD1": 0.0, "JD2": 0.0}


def test_bm25_rejects_empty_duplicate_or_nonstring_documents():
    with pytest.raises(ValueError, match="HYBRID_BM25_INPUT"):
        hy.BM25Index(["JD1", "JD1"], ["a", "b"])
    with pytest.raises(ValueError, match="HYBRID_BM25_EMPTY"):
        hy.BM25Index(["JD1", "JD2"], ["内容", "  "])
    with pytest.raises(ValueError, match="HYBRID_BM25_INPUT"):
        hy.BM25Index(["JD1"], [None])


def test_rrf_fuses_with_known_scores_and_validates_input():
    fused = hy.rrf_fuse([("JD1", "JD2", "JD3"), ("JD2", "JD1", "JD3")], k=60)
    assert fused == ("JD1", "JD2", "JD3")
    scores = {1 / 61 + 1 / 62, 1 / 62 + 1 / 61}
    assert scores == {1 / 61 + 1 / 62}
    # Equal reciprocal scores tie-break by jd_id ascending.
    assert hy.rrf_fuse([("JD9",), ("JD8",)], k=1) == ("JD8", "JD9")
    with pytest.raises(ValueError, match="HYBRID_RRF_INPUT"):
        hy.rrf_fuse([("JD1", "JD1")])
    with pytest.raises(ValueError, match="HYBRID_RRF_INPUT"):
        hy.rrf_fuse([("JD1",)], k=0)
    with pytest.raises(ValueError, match="HYBRID_RRF_INPUT"):
        hy.rrf_fuse("JD1 JD2")
    with pytest.raises(ValueError, match="HYBRID_RRF_INPUT"):
        hy.rrf_fuse([("JD1",), (1, 2)])


def test_hybrid_evaluate_covers_three_frozen_datasets_with_improved_fused_recall():
    report = hy.evaluate()
    assert report["case_count"] == 42 and report["real_http_requests"] == 0
    assert report["quality_conclusion"] == "NOT_ESTABLISHED"
    assert report["rrf_k"] == 60
    observed = {section["dataset"]: section for section in report["datasets"]}
    assert observed["v1"]["dense_metrics"]["recall_at_1"] == pytest.approx(6 / 9)
    assert observed["v1"]["fused_metrics"]["recall_at_1"] == pytest.approx(7 / 9)
    assert observed["heldout"]["dense_metrics"]["recall_at_1"] == pytest.approx(6 / 9)
    assert observed["heldout"]["fused_metrics"]["recall_at_1"] == pytest.approx(7 / 9)
    assert observed["v2"]["dense_metrics"]["recall_at_1"] == pytest.approx(9 / 12)
    assert observed["v2"]["fused_metrics"]["recall_at_1"] == pytest.approx(11 / 12)
    for section in report["datasets"]:
        assert section["fused_metrics"]["recall_at_3"] == pytest.approx(1.0)
    for dataset in report["datasets"]:
        for case in dataset["cases"]:
            assert len(set(case["fused_ranking"])) == len(case["fused_ranking"])


def test_hybrid_report_contains_no_raw_queries_or_credentials():
    payload = json.dumps(hy.evaluate(), ensure_ascii=False)
    for module in (v1, heldout, v2):
        dataset = module.load_dataset()
        assert all(case.query not in payload for case in dataset.cases)
        assert all(job.raw_text.strip() not in payload for job in dataset.jobs)


def test_hybrid_fusion_does_not_change_screening():
    resume = hy.ROOT / "data/synthetic/resumes/S001_normal_backend.pdf"
    before = screen_one(resume)
    hy.evaluate()
    after = screen_one(resume)
    assert after == before
    assert before.score.jd_id == "JD1" and before.score.total == 60 and before.score.scores.project == 0


def test_cli_rejects_live_and_writes_report_without_queries(tmp_path, monkeypatch):
    monkeypatch.setattr(hy, "REPORT_DIR", tmp_path)
    assert hy.main(["--live"]) == 2
    assert not list(tmp_path.iterdir())
    assert hy.main(["--fake"]) == 0
    report_path = next(tmp_path.glob("retrieval-hybrid-*.json"))
    payload = report_path.read_text(encoding="utf-8")
    report = json.loads(payload)
    assert report["mode"] == "fixed-fake-vectors" and report["http_attempts"] == 0
    assert all(case.query not in payload for case in v2.load_dataset().cases)
    assert hy.main(["--fake", "--output", report_path.name]) == 2
