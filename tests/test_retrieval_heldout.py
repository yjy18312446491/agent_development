"""Held-out relevance labels stay disjoint from v1 and never go live."""

import json
from pathlib import Path
import socket
import urllib.request

import pytest

from app.embedding_client import EmbeddingClient, EmbeddingConfig
from app.screen import screen_one
from eval import run_retrieval_eval as v1
from eval import run_retrieval_heldout as heldout


@pytest.fixture(autouse=True)
def no_credentials_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Held-out retrieval must not read credentials or make network requests")

    monkeypatch.setattr(EmbeddingConfig, "from_env_file", forbidden)
    monkeypatch.setattr(EmbeddingClient, "__init__", forbidden)
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


def test_heldout_has_twelve_new_queries_and_does_not_move_v1_gold():
    dataset = heldout.load_dataset()
    v1_dataset = v1.load_dataset()
    assert len(dataset.cases) == 12
    assert [sum(c.coverage_group == group for c in dataset.cases) for group in (*heldout.JOB_IDS, "none")] == [3] * 4
    assert dataset.canonical_sha256 == heldout.DATASET_SHA
    assert v1_dataset.canonical_sha256 == heldout.V1_DATASET_SHA
    heldout_queries = {c.query for c in dataset.cases}
    v1_queries = {c.query for c in v1_dataset.cases}
    assert heldout_queries.isdisjoint(v1_queries)
    assert all(c.case_id.startswith("HV") for c in dataset.cases)
    assert all(c.case_id.startswith("RV") for c in v1_dataset.cases)


def test_heldout_labels_remain_pending_and_quality_is_not_established():
    report = heldout.evaluate(heldout.load_dataset(), enabled=True)
    assert report["annotation_review_status"] == "PENDING_INDEPENDENT_REVIEW"
    assert report["quality_conclusion"] == "NOT_ESTABLISHED"
    assert report["real_http_requests"] == 0
    assert report["http_attempts"] == 0
    assert report["metrics"]["recall_at_1"] == pytest.approx(6 / 9)
    assert report["metrics"]["recall_at_3"] == pytest.approx(9 / 9)
    assert report["relevant_case_count"] == 9
    wrong_first = {"HV002", "HV005", "HV008"}
    for row in report["cases"]:
        if row["id"] in wrong_first:
            assert row["rankings"][0]["reference_jd_id"] != row["relevant_reference_ids"][0]


def test_default_heldout_preview_is_disabled():
    report = heldout.evaluate(heldout.load_dataset())
    assert report["mode"] == "disabled-preview"
    assert report["fake_embedder_calls"] == 0
    assert report["metrics"]["recall_at_1"] is None


def test_changed_heldout_or_v1_snapshot_is_rejected(tmp_path):
    raw = json.loads(heldout.DATASET.read_text(encoding="utf-8"))
    raw["cases"][0]["query"] = "changed held-out query that still looks like backend work"
    path = tmp_path / "heldout.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="HELDOUT_DATASET_INVALID"):
        heldout.load_dataset(path=path)


def test_cli_rejects_live_and_does_not_load_data(monkeypatch):
    monkeypatch.setattr(heldout, "load_dataset", lambda: pytest.fail("No live path may load data"))
    with pytest.raises(SystemExit) as error:
        heldout.main(["--live"])
    assert error.value.code == 2


def test_cli_writes_heldout_report_without_raw_queries(tmp_path, monkeypatch):
    monkeypatch.setattr(heldout, "REPORT_DIR", tmp_path)
    assert heldout.main(["--fake"]) == 0
    report_path = next(tmp_path.glob("retrieval-heldout-*.json"))
    payload = report_path.read_text(encoding="utf-8")
    report = json.loads(payload)
    assert report["mode"] == "fixed-fake-vectors"
    assert all(case.query not in payload for case in heldout.load_dataset().cases)
    assert not (tmp_path / "embedding-live.lock").exists()


def test_heldout_fake_eval_does_not_change_screening():
    resume = heldout.ROOT / "data/synthetic/resumes/S001_normal_backend.pdf"
    before = screen_one(resume)
    heldout.evaluate(heldout.load_dataset(), enabled=True)
    after = screen_one(resume)
    assert after == before
    assert before.score.jd_id == "JD1" and before.score.total == 60 and before.score.scores.project == 0


def test_report_path_must_use_heldout_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(heldout, "REPORT_DIR", tmp_path)
    target = tmp_path / "retrieval-offline-wrong.json"
    target.write_bytes(b"keep")
    assert heldout.main(["--fake", "--output", str(target)]) == 2
    assert target.read_bytes() == b"keep"
