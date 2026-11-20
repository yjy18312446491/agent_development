"""Check the frozen dataset, zero-network CLI, and the two-batch pilot."""

from io import BytesIO
import json
from pathlib import Path
import shutil

import pytest

from app.embedding_client import EmbeddingClient, EmbeddingConfig
from app.screen import screen_one
from eval import run_embedding_eval as runner


class Response(BytesIO):
    def getcode(self):
        return 200


class FakeHTTP:
    def __init__(self, invalid=False):
        self.calls = []
        self.invalid = invalid

    def open(self, request, timeout):
        body = json.loads(request.data)
        self.calls.append(body)
        count = len(body["input"]["texts"])
        result = {"output": {"embeddings": [
            {"text_index": index, "embedding": [1.0] + [0.0] * 1023} for index in range(count)
        ]}, "usage": {"total_tokens": count * 10}}
        if self.invalid:
            del result["usage"]
        return Response(json.dumps(result).encode())


def test_frozen_plan_has_ten_vectors_and_no_project_labels():
    report = runner.load_plan().preview()
    assert report["unique_vectors"] == 10 and report["skipped"] == ["short_gate"]
    assert report["real_http_requests"] == 0
    assert "project_dimensions" not in json.dumps(report) and "rule_total" not in json.dumps(report)


def test_two_fake_batches_generate_seven_rankings_without_changing_screening():
    plan = runner.load_plan()
    http = FakeHTTP()
    result = runner.evaluate_injected(plan, EmbeddingClient(EmbeddingConfig("synthetic-key"), opener=http))
    assert [len(call["input"]["texts"]) for call in http.calls] == [3, 7]
    assert result["http_attempts"] == 2 and all(row["status"] == "OK" for row in result["rankings"])
    assert len(result["rankings"]) == 7 and result["total_tokens"] == 100
    assert result["real_http_requests"] is None  # Injected transports are not assumed to be real or fake.
    assert "synthetic-key" not in json.dumps(result)
    baseline = screen_one(runner.ROOT / "data/synthetic/resumes/S001_normal_backend.pdf")
    assert baseline.score.jd_id == "JD1" and baseline.score.total == 60


def test_missing_usage_stops_queries_and_keeps_usage_unknown():
    http = FakeHTTP(invalid=True)
    report = runner.evaluate_injected(runner.load_plan(), EmbeddingClient(EmbeddingConfig("synthetic-key"), opener=http))
    assert len(http.calls) == 1 and report["total_tokens"] is None and report["estimated_cny"] is None
    assert all(row["status"] == "NOT_RUN" for row in report["rankings"])


def test_changed_manifest_is_rejected(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_bytes(runner.MANIFEST.read_bytes() + b" ")
    with pytest.raises(ValueError, match="DATASET_INVALID"):
        runner.load_plan(path)


def test_exact_git_lf_snapshot_is_accepted_and_actual_byte_hash_is_reported(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_bytes(runner.MANIFEST.read_bytes().replace(b"\r\n", b"\n"))
    report = runner.load_plan(path).preview()
    assert report["manifest_sha256"] == runner.MANIFEST_GIT_SHA
    assert report["frozen_manifest_sha256"] == runner.MANIFEST_SHA


def test_changed_pdf_is_rejected_before_any_request(tmp_path):
    fixtures = tmp_path / "pdfs"
    shutil.copytree(runner.FIXTURES, fixtures)
    (fixtures / "personal_api.pdf").write_bytes(b"changed")
    with pytest.raises(ValueError, match="DATASET_INVALID"):
        runner.load_plan(fixtures=fixtures)


def test_preview_never_reads_credentials_or_creates_http_client(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Preview must not create a client or read credentials")

    monkeypatch.setattr(EmbeddingConfig, "from_env_file", forbidden)
    monkeypatch.setattr(EmbeddingClient, "__init__", forbidden)
    monkeypatch.setattr(runner, "REPORT_DIR", tmp_path)
    assert runner.main([]) == 0
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert report["http_attempts"] == report["real_http_requests"] == 0


def test_live_dataset_failure_precedes_credentials(monkeypatch, capsys, tmp_path):
    def invalid():
        raise ValueError("invalid frozen data")

    def forbidden(*args):
        pytest.fail("Invalid data must be rejected before credentials")

    monkeypatch.setattr(runner, "load_plan", invalid)
    monkeypatch.setattr(EmbeddingConfig, "from_env_file", forbidden)
    monkeypatch.setattr(runner, "REPORT_DIR", tmp_path)
    assert runner.main(["--live"]) == 2
    assert "预检失败" in capsys.readouterr().err


def test_report_must_stay_under_ignored_reports_directory(tmp_path):
    with pytest.raises(ValueError, match="REPORT_PATH"):
        runner.write_report({}, tmp_path / "report.json")
