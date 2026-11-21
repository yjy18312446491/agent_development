"""Controlled relevance baseline, using temporary configuration and fake HTTP only."""

from io import BytesIO
import json
from pathlib import Path
import socket
from urllib.error import HTTPError
import urllib.request

import pytest

from app.embedding_client import EmbeddingClient
from eval import run_retrieval_baseline as runner
from eval.run_retrieval_eval import FakeVectors


KEY = "synthetic-baseline-key"


class Response(BytesIO):
    def getcode(self):
        return 200


class FakeHTTP:
    def __init__(self, dataset, outcomes):
        self.vectors = FakeVectors(dataset)
        self.outcomes = list(outcomes)
        self.calls = []

    def open(self, request, timeout):
        body = json.loads(request.data)
        self.calls.append(body)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        if type(outcome) is int:
            raise HTTPError("https://example.invalid/" + KEY, outcome, KEY, {}, BytesIO(KEY.encode()))
        texts = body["input"]["texts"]
        embeddings = [list(vector) + [0.0] * 1021 for vector in self.vectors(texts)]
        result = {"output": {"embeddings": [{"text_index": i, "embedding": vector}
                                           for i, vector in enumerate(embeddings)]},
                  "usage": {"total_tokens": len(texts) * 10}}
        if outcome == "unknown_usage":
            del result["usage"]
        if outcome == "bad_dimension":
            result["output"]["embeddings"][0]["embedding"] = [1.0]
        return Response(json.dumps(result).encode())


@pytest.fixture(autouse=True)
def no_real_network_or_env(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Real network or repository credentials are forbidden")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    monkeypatch.setattr("app.embedding_client.build_opener", forbidden)
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        if path.name == ".env":
            forbidden()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read_text)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    directory = tmp_path / "reports"
    env = tmp_path / "synthetic.env"
    env.write_text("\n".join(("EMBEDDING_PROVIDER=dashscope", "EMBEDDING_API_KEY=" + KEY,
                             "EMBEDDING_MODEL=text-embedding-v4", "EMBEDDING_DIMENSION=1024",
                             "EMBEDDING_REGION=cn-beijing", "EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com")),
                   encoding="utf-8")
    monkeypatch.setattr(runner, "REPORT_DIR", directory)
    monkeypatch.setattr(runner, "ENV_FILE", env)
    monkeypatch.setattr(runner, "_source_commit", lambda: "a" * 40)

    def install(outcomes=()):
        http = FakeHTTP(runner.load_dataset(), outcomes)
        monkeypatch.setattr(runner, "EmbeddingClient", lambda config: EmbeddingClient(config, opener=http))
        return http, directory, env
    return install


def read_report(directory):
    paths = list(directory.glob("retrieval-baseline-live-*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


def test_default_preview_reads_no_config_or_client(setup, monkeypatch):
    http, directory, _ = setup()
    monkeypatch.setattr(runner.EmbeddingConfig, "from_env_file", lambda *a: pytest.fail("credential read"))
    monkeypatch.setattr(runner, "EmbeddingClient", lambda *a: pytest.fail("client construction"))
    assert runner.main([]) == 0
    assert http.calls == [] and not (directory / runner.LOCK_NAME).exists()
    report = json.loads(next(directory.glob("retrieval-baseline-preview-*.json")).read_text(encoding="utf-8"))
    assert report["unique_vectors"] == 15 and report["retry_budget"] == 0
    assert report["planned_reservation_cny"] == 0.06144
    assert report["metrics"]["recall_at_1"] is None and report["real_http_requests"] == 0


def test_three_batches_then_lookup_only_metrics_and_durable_lock(setup, monkeypatch):
    http, directory, _ = setup()
    original = http.open

    def check_lock(request, timeout):
        lock = json.loads((directory / runner.LOCK_NAME).read_text(encoding="utf-8"))
        initial = json.loads((directory / lock["report"]).read_text(encoding="utf-8"))
        assert initial["run_id"] == lock["run_id"] and not initial["counts_final"]
        assert initial["attempts"][-1]["code"] == "IN_FLIGHT"
        return original(request, timeout)
    monkeypatch.setattr(http, "open", check_lock)
    assert runner.main(["--live"]) == 0
    assert [len(c["input"]["texts"]) for c in http.calls] == [3, 10, 2]
    assert [c["parameters"]["text_type"] for c in http.calls] == ["document", "query", "query"]
    report = read_report(directory)
    assert report["status"] == "SUCCESS" and report["http_attempts"] == 3
    assert report["total_tokens"] == 150 and report["estimated_cny"] == pytest.approx(0.000075)
    assert report["reserved_cny"] == 0.06144 and report["failed_case_count"] == 0
    assert report["metrics"]["recall_at_1"] == pytest.approx(6 / 9)
    assert report["metrics"]["recall_at_3"] == 1
    assert report["quality_conclusion"] == "NOT_ESTABLISHED"
    assert report["no_relevant_case_count"] == 3
    assert all(c["recall_at_1"] is None for c in report["cases"][-3:])
    serialized = json.dumps(report)
    assert KEY not in serialized and "embedding\"" not in serialized
    assert all(case.query not in serialized for case in runner.load_dataset().cases)
    lock = (directory / runner.LOCK_NAME).read_bytes()
    assert runner.main(["--live"]) != 0 and len(http.calls) == 3
    assert (directory / runner.LOCK_NAME).read_bytes() == lock


@pytest.mark.parametrize("batch_index", [0, 1, 2])
@pytest.mark.parametrize("failure", [400, 401, 402, 403, 404, 408, 429, 500,
                                     "unknown_usage", "bad_dimension"])
def test_every_failure_stops_without_retry(setup, batch_index, failure, capsys):
    http, directory, _ = setup([None] * batch_index + [failure])
    assert runner.main(["--live"]) != 0
    report = read_report(directory)
    assert len(http.calls) == report["http_attempts"] == batch_index + 1
    assert report["status"] == "FAILED" and report["retry_budget"] == 0
    assert report["total_tokens"] is None if failure != "bad_dimension" else report["total_tokens"] is not None
    assert report["failed_case_count"] == 12
    assert report["metrics"]["recall_at_1"] == 0
    assert all(c["status"] == "NOT_RUN" for c in report["cases"])
    assert (directory / runner.LOCK_NAME).exists()
    assert runner.main(["--live"]) != 0 and len(http.calls) == batch_index + 1
    captured = capsys.readouterr()
    assert KEY not in json.dumps(report) + captured.out + captured.err


@pytest.mark.parametrize("failure", [TimeoutError(KEY), RuntimeError(KEY), KeyboardInterrupt(), SystemExit()])
def test_interrupt_and_unexpected_failure_are_unknown_and_never_rerun(setup, failure):
    http, directory, _ = setup([None, failure])
    assert runner.main(["--live"]) != 0
    report = read_report(directory)
    assert len(http.calls) == 2 and report["http_attempts"] == 2
    assert report["total_tokens"] is None and report["estimated_cny"] is None
    assert report["counts_final"] and (directory / runner.LOCK_NAME).exists()
    assert runner.main(["--live"]) != 0 and len(http.calls) == 2


def test_budget_reservation_precedes_http_and_stops_over_budget(setup, monkeypatch):
    http, directory, _ = setup()
    monkeypatch.setattr(runner, "BUDGET", runner.Decimal("0.02"))
    assert runner.main(["--live"]) != 0
    report = read_report(directory)
    assert len(http.calls) == 1 and report["http_attempts"] == 1
    assert report["reserved_cny"] <= 0.02


def test_existing_lock_blocks_even_configuration_read(setup, monkeypatch):
    http, directory, _ = setup()
    directory.mkdir()
    (directory / runner.LOCK_NAME).write_text("keep")
    monkeypatch.setattr(runner.EmbeddingConfig, "from_env_file", lambda *a: pytest.fail("credential read"))
    assert runner.main(["--live"]) != 0 and http.calls == []
    assert (directory / runner.LOCK_NAME).read_text() == "keep"


def test_old_experiment_lock_is_never_modified(setup):
    http, directory, _ = setup()
    directory.mkdir()
    old = directory / "embedding-live.lock"
    old.write_text("previous ten-vector smoke evidence")
    assert runner.main(["--live"]) == 0
    assert old.read_text() == "previous ten-vector smoke evidence"
    assert len(http.calls) == 3  # This test only uses the injected fake opener.


@pytest.mark.parametrize("relative", ["../outside.json", "embedding-live-old.json", "retrieval-baseline-preview-old.json"])
def test_invalid_destination_never_sends(setup, relative):
    http, directory, _ = setup()
    assert runner.main(["--live", "--output", str(directory / relative)]) != 0
    assert http.calls == [] and not directory.exists()


def test_existing_report_never_overwritten(setup):
    http, directory, _ = setup()
    directory.mkdir()
    path = directory / "retrieval-baseline-live-old.json"
    path.write_text("keep")
    assert runner.main(["--live", "--output", str(path)]) != 0
    assert path.read_text() == "keep" and http.calls == []


def test_invalid_config_never_sends(setup):
    http, directory, env = setup()
    env.write_text("LLM_API_KEY=" + KEY, encoding="utf-8")
    assert runner.main(["--live"]) != 0 and http.calls == []
    assert not directory.exists()


def test_changed_snapshot_rejected_before_any_http(setup, monkeypatch):
    http, directory, _ = setup()
    dataset = runner.load_dataset()
    dataset.jobs[0].skills.append("changed after review")
    monkeypatch.setattr(runner, "load_dataset", lambda: dataset)
    assert runner.main(["--live"]) != 0 and http.calls == []
    assert not directory.exists()


def test_snapshot_change_after_first_http_stops_next_batch(setup, monkeypatch):
    http, directory, _ = setup()
    dataset = runner.load_dataset()
    monkeypatch.setattr(runner, "load_dataset", lambda: dataset)
    original = http.open

    def changed(request, timeout):
        response = original(request, timeout)
        dataset.jobs[0].skills.append("changed")
        return response
    monkeypatch.setattr(http, "open", changed)
    assert runner.main(["--live"]) != 0 and len(http.calls) == 1
    report = read_report(directory)
    assert report["metrics_status"] == "INVALID_SNAPSHOT"
    assert report["metrics"]["recall_at_1"] is None


@pytest.mark.parametrize("save_number", [1, 2, 3])
def test_report_write_failure_never_resets_lock_or_restarts_http(setup, monkeypatch, save_number):
    http, directory, _ = setup()
    original = runner._save
    count = [0]

    def fail(report, path, **kwargs):
        count[0] += 1
        if count[0] >= save_number:
            raise OSError(KEY)
        return original(report, path, **kwargs)
    monkeypatch.setattr(runner, "_save", fail)
    assert runner.main(["--live"]) != 0
    assert len(http.calls) <= 1
    count_before = len(http.calls)
    if save_number > 1:
        assert (directory / runner.LOCK_NAME).exists()
        assert runner.main(["--live"]) != 0 and len(http.calls) == count_before


def test_lock_fsync_failure_sends_nothing_and_preserves_lock(setup, monkeypatch):
    http, directory, _ = setup()
    original = runner.os.fsync
    calls = [0]

    def fail(descriptor):
        calls[0] += 1
        if calls[0] == 2:
            raise OSError(KEY)
        return original(descriptor)
    monkeypatch.setattr(runner.os, "fsync", fail)
    assert runner.main(["--live"]) != 0 and http.calls == []
    assert (directory / runner.LOCK_NAME).exists()


def test_lower_request_limit_blocks_the_next_batch(setup, monkeypatch):
    http, directory, _ = setup()
    monkeypatch.setattr(runner, "MAX_HTTP", 2)
    assert runner.main(["--live"]) != 0 and len(http.calls) == 2
    assert read_report(directory)["http_attempts"] == 2


def test_even_an_extra_planned_batch_cannot_send_fourth_request(setup, monkeypatch):
    http, directory, _ = setup()
    original = runner.batches
    monkeypatch.setattr(runner, "batches", lambda dataset: original(dataset) + (original(dataset)[0],))
    assert runner.main(["--live"]) != 0 and len(http.calls) == 3
    assert read_report(directory)["http_attempts"] == 3


def test_lost_lock_race_sends_nothing_and_preserves_other_identity(setup, monkeypatch):
    http, directory, _ = setup()
    original = runner._lock

    def another_process(report, path):
        (directory / runner.LOCK_NAME).write_text("other-process")
        return original(report, path)
    monkeypatch.setattr(runner, "_lock", another_process)
    assert runner.main(["--live"]) != 0 and http.calls == []
    assert (directory / runner.LOCK_NAME).read_text() == "other-process"


def test_unknown_source_refuses_before_lock_or_http(setup, monkeypatch):
    http, directory, _ = setup()

    def unknown():
        raise ValueError(KEY)
    monkeypatch.setattr(runner, "_source_commit", unknown)
    assert runner.main(["--live"]) != 0 and http.calls == []
    assert not directory.exists()


def test_source_evidence_requires_committed_runner_and_clean_relevant_inputs(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return runner.subprocess.CompletedProcess(command, 0, stdout=b"a" * 40 + b"\n")
    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner._source_commit() == "a" * 40
    assert commands[0] == ["git", "cat-file", "-e", "HEAD:eval/run_retrieval_baseline.py"]
    assert commands[1][:5] == ["git", "diff", "--quiet", "HEAD", "--"]
    assert commands[2] == ["git", "rev-parse", "HEAD"]
