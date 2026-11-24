"""Held-out live entry uses temporary config and fake HTTP only."""

from io import BytesIO
import json
from pathlib import Path
import socket
from urllib.error import HTTPError
import urllib.request

import pytest

from app.embedding_client import EmbeddingClient
from eval import run_retrieval_heldout as heldout
from eval import run_retrieval_heldout_live as runner


KEY = "synthetic-heldout-key"


class Response(BytesIO):
    def getcode(self):
        return 200


class FakeHTTP:
    def __init__(self, dataset, outcomes):
        self.vectors = heldout.FakeVectors(dataset)
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
    monkeypatch.setattr(runner, "_source_commit", lambda: "b" * 40)

    def install(outcomes=()):
        http = FakeHTTP(heldout.load_dataset(), outcomes)
        monkeypatch.setattr(runner, "EmbeddingClient", lambda config: EmbeddingClient(config, opener=http))
        return http, directory, env

    return install


def read_report(directory):
    paths = list(directory.glob("retrieval-heldout-live-*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


def test_default_preview_reads_no_config_or_client(setup, monkeypatch):
    http, directory, _ = setup()
    monkeypatch.setattr(runner.EmbeddingConfig, "from_env_file", lambda *a: pytest.fail("credential read"))
    monkeypatch.setattr(runner, "EmbeddingClient", lambda *a: pytest.fail("client construction"))
    assert runner.main([]) == 0
    assert http.calls == [] and not (directory / runner.LOCK_NAME).exists()
    report = json.loads(next(directory.glob("retrieval-heldout-live-preview-*.json")).read_text(encoding="utf-8"))
    assert report["unique_vectors"] == 15 and report["retry_budget"] == 0
    assert report["max_http_attempts"] == 3
    assert report["real_http_requests"] == 0
    assert report["quality_conclusion"] == "NOT_ESTABLISHED"


def test_three_batches_lock_before_http_and_refuse_rerun(setup, monkeypatch):
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
    assert report["quality_conclusion"] == "NOT_ESTABLISHED"
    assert report["metrics"]["recall_at_1"] == pytest.approx(6 / 9)
    serialized = json.dumps(report)
    assert KEY not in serialized
    assert all(case.query not in serialized for case in heldout.load_dataset().cases)
    lock = (directory / runner.LOCK_NAME).read_bytes()
    assert runner.main(["--live"]) != 0 and len(http.calls) == 3
    assert (directory / runner.LOCK_NAME).read_bytes() == lock


def test_http_401_stops_without_retry(setup):
    http, directory, _ = setup([401])
    assert runner.main(["--live"]) != 0
    report = read_report(directory)
    assert len(http.calls) == 1 and report["http_attempts"] == 1
    assert report["status"] == "FAILED" and report["retry_budget"] == 0
    assert (directory / runner.LOCK_NAME).exists()
    assert runner.main(["--live"]) != 0 and len(http.calls) == 1


def test_existing_lock_blocks_even_configuration_read(setup, monkeypatch):
    http, directory, _ = setup()
    directory.mkdir()
    (directory / runner.LOCK_NAME).write_text("keep")
    monkeypatch.setattr(runner.EmbeddingConfig, "from_env_file", lambda *a: pytest.fail("credential read"))
    assert runner.main(["--live"]) != 0 and http.calls == []
    assert (directory / runner.LOCK_NAME).read_text() == "keep"


def test_old_experiment_locks_are_never_modified(setup):
    http, directory, _ = setup()
    directory.mkdir()
    old_smoke = directory / "embedding-live.lock"
    old_v1 = directory / "retrieval-relevance-v1-live.lock"
    old_smoke.write_text("previous ten-vector smoke evidence")
    old_v1.write_text("previous fifteen-vector baseline evidence")
    assert runner.main(["--live"]) == 0
    assert old_smoke.read_text() == "previous ten-vector smoke evidence"
    assert old_v1.read_text() == "previous fifteen-vector baseline evidence"
    assert len(http.calls) == 3


def test_heldout_offline_module_still_rejects_live():
    with pytest.raises(SystemExit) as error:
        heldout.main(["--live"])
    assert error.value.code == 2
