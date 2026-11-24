"""V2 retrieval labels stay disjoint from v1/held-out and never go live here."""

import json
from pathlib import Path
import socket
import urllib.request

import pytest

from app.embedding_client import EmbeddingClient, EmbeddingConfig
from app.screen import screen_one
from eval import run_retrieval_eval as v1
from eval import run_retrieval_heldout as heldout
from eval import run_retrieval_v2 as v2


@pytest.fixture(autouse=True)
def no_credentials_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("V2 retrieval must not read credentials or make network requests")

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


def test_v2_has_new_jobs_and_queries_without_moving_gold():
    dataset = v2.load_dataset()
    v1_dataset = v1.load_dataset()
    held = heldout.load_dataset()
    assert [job.jd_id for job in dataset.jobs] == list(v2.JOB_IDS)
    assert len(dataset.cases) == 18
    assert dataset.canonical_sha256 == v2.DATASET_SHA
    assert v1_dataset.canonical_sha256 == v2.V1_DATASET_SHA
    assert held.canonical_sha256 == v2.HELDOUT_DATASET_SHA
    queries = {c.query for c in dataset.cases}
    assert queries.isdisjoint({c.query for c in v1_dataset.cases})
    assert queries.isdisjoint({c.query for c in held.cases})
    assert all(c.case_id.startswith("QV") for c in dataset.cases)
    groups = [sum(c.coverage_group == group for c in dataset.cases) for group in (*v2.JOB_IDS, "none")]
    assert groups == [2, 2, 2, 2, 2, 2, 6]


def test_v2_labels_remain_pending_and_quality_is_not_established():
    report = v2.evaluate(v2.load_dataset(), enabled=True)
    assert report["annotation_review_status"] == "PENDING_INDEPENDENT_REVIEW"
    assert report["quality_conclusion"] == "NOT_ESTABLISHED"
    assert report["real_http_requests"] == 0
    assert report["metrics"]["recall_at_1"] == pytest.approx(9 / 12)
    assert report["metrics"]["recall_at_3"] == pytest.approx(12 / 12)
    assert report["relevant_case_count"] == 12
    wrong_first = {"QV002", "QV004", "QV010"}
    for row in report["cases"]:
        if row["id"] in wrong_first:
            assert row["rankings"][0]["reference_jd_id"] != row["relevant_reference_ids"][0]


def test_default_v2_preview_is_disabled():
    report = v2.evaluate(v2.load_dataset())
    assert report["mode"] == "disabled-preview"
    assert report["fake_embedder_calls"] == 0
    assert report["metrics"]["recall_at_1"] is None


def test_changed_v2_snapshot_is_rejected(tmp_path):
    raw = json.loads(v2.DATASET.read_text(encoding="utf-8"))
    raw["cases"][0]["query"] = "changed v2 query that still looks like backend work"
    path = tmp_path / "v2.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="V2_DATASET_INVALID"):
        v2.load_dataset(path=path)


def test_cli_rejects_live_and_does_not_load_data(monkeypatch):
    monkeypatch.setattr(v2, "load_dataset", lambda: pytest.fail("No live path may load data"))
    assert v2.main(["--live"]) == 2


def test_cli_writes_v2_report_without_raw_queries(tmp_path, monkeypatch):
    monkeypatch.setattr(v2, "REPORT_DIR", tmp_path)
    assert v2.main(["--fake"]) == 0
    report_path = next(tmp_path.glob("retrieval-v2-*.json"))
    payload = report_path.read_text(encoding="utf-8")
    report = json.loads(payload)
    assert report["mode"] == "fixed-fake-vectors"
    assert all(case.query not in payload for case in v2.load_dataset().cases)


def test_v2_fake_eval_does_not_change_screening():
    resume = v2.ROOT / "data/synthetic/resumes/S001_normal_backend.pdf"
    before = screen_one(resume)
    v2.evaluate(v2.load_dataset(), enabled=True)
    after = screen_one(resume)
    assert after == before
    assert before.score.jd_id == "JD1" and before.score.total == 60 and before.score.scores.project == 0
