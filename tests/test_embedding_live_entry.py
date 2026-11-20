"""Exercise the live control path only with temporary keys and fake HTTP."""

from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from app.embedding_client import EmbeddingClient, EmbeddingConfig
from eval import run_embedding_eval as runner


KEY = "synthetic-live-test-key"


class Response(BytesIO):
    def getcode(self):
        return 200


class FakeHTTP:
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = []

    def open(self, request, timeout):
        body = json.loads(request.data)
        self.calls.append(body)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        if type(outcome) is int:
            raise HTTPError("https://synthetic-workspace.example/" + KEY, outcome, KEY, {}, BytesIO(KEY.encode()))
        count = len(body["input"]["texts"])
        result = {"output": {"embeddings": [
            {"text_index": index, "embedding": [1.0] + [0.0] * 1023} for index in range(count)
        ]}, "usage": {"total_tokens": count * 10}}
        if outcome == "unknown_usage":
            del result["usage"]
        return Response(json.dumps(result).encode())


@pytest.fixture
def setup_live(monkeypatch, tmp_path):
    env = tmp_path / "fake.env"
    env.write_text("\n".join((
        "EMBEDDING_PROVIDER=dashscope", "EMBEDDING_API_KEY=" + KEY,
        "EMBEDDING_MODEL=text-embedding-v4", "EMBEDDING_DIMENSION=1024",
        "EMBEDDING_REGION=cn-beijing", "EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com",
    )), encoding="utf-8")
    reports = tmp_path / "reports"
    monkeypatch.setattr(runner, "ENV_FILE", env)
    monkeypatch.setattr(runner, "REPORT_DIR", reports)

    def install(outcomes=()):
        http = FakeHTTP(outcomes)
        monkeypatch.setattr(runner, "EmbeddingClient", lambda config: EmbeddingClient(config, opener=http))
        return http, env, reports
    return install


def reports_in(directory):
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("embedding-live-*.json"))]


def test_live_fake_transport_prefetches_two_batches_and_keeps_lock(setup_live):
    http, _, directory = setup_live()
    assert runner.main(["--live"]) == 0
    report = reports_in(directory)[0]
    assert [len(call["input"]["texts"]) for call in http.calls] == [3, 7]
    assert report["mode"] == "live" and report["status"] == "SUCCESS"
    assert report["real_http_requests"] == report["http_attempts"] == 2
    assert report["counts_final"] is True and report["total_tokens"] == 100
    assert len(report["rankings"]) == 7 and all(row["status"] == "OK" for row in report["rankings"])
    assert (directory / "embedding-live.lock").exists()
    assert KEY not in json.dumps(report)


def test_existing_lock_blocks_repeat_without_replacing_lock(setup_live):
    http, _, directory = setup_live()
    directory.mkdir()
    lock = directory / "embedding-live.lock"
    lock.write_text("prior run", encoding="utf-8")
    assert runner.main(["--live"]) != 0
    assert http.calls == [] and lock.read_text(encoding="utf-8") == "prior run"


@pytest.mark.parametrize("outcomes", [[TimeoutError(KEY), None, None], [None, 429, None]])
def test_only_one_retry_with_three_total_attempts(setup_live, outcomes):
    http, _, directory = setup_live(outcomes)
    assert runner.main(["--live"]) == 0
    report = reports_in(directory)[0]
    assert report["real_http_requests"] == 3 and report["retry_used"]
    assert report["total_tokens"] is None and report["estimated_cny"] is None
    assert report["reserved_cny"] <= 0.10
    assert runner.main(["--live"]) != 0
    assert len(http.calls) == 3


def test_exhausted_retry_never_permits_fourth_http_or_fresh_round(setup_live, capsys):
    http, _, directory = setup_live([TimeoutError(KEY), None, 429])
    assert runner.main(["--live"]) != 0
    report = reports_in(directory)[0]
    assert report["status"] == "FAILED" and report["real_http_requests"] == 3
    assert runner.main(["--live"]) != 0 and len(http.calls) == 3
    output = capsys.readouterr()
    assert KEY not in output.out + output.err and "不要" in output.err


@pytest.mark.parametrize("failure", [400, 401, 402, 403, 404, "unknown_usage"])
def test_terminal_failure_stops_query_batch_and_saves_unknown_usage(setup_live, failure):
    http, _, directory = setup_live([failure])
    assert runner.main(["--live"]) != 0
    report = reports_in(directory)[0]
    assert len(http.calls) == report["real_http_requests"] == 1
    assert report["status"] == "FAILED" and report["total_tokens"] is None
    assert report["estimated_cny"] is None and all(row["status"] == "NOT_RUN" for row in report["rankings"])
    assert (directory / "embedding-live.lock").exists()


def test_keyboard_interrupt_keeps_lock_and_does_not_claim_zero_usage(setup_live):
    http, _, directory = setup_live([KeyboardInterrupt()])
    assert runner.main(["--live"]) != 0
    report = reports_in(directory)[0]
    assert report["status"] == "INTERRUPTED" and report["real_http_requests"] == 1
    assert report["unaccounted_attempts"] == 1
    assert report["total_tokens"] is None and report["estimated_cny"] is None
    assert (directory / "embedding-live.lock").exists()
    assert runner.main(["--live"]) != 0 and len(http.calls) == 1


@pytest.mark.parametrize("misconfiguration", ["missing_key", "llm_only", "singapore"])
def test_bad_configuration_never_makes_http(setup_live, misconfiguration, capsys):
    http, env, _ = setup_live()
    text = env.read_text(encoding="utf-8")
    if misconfiguration == "missing_key":
        text = text.replace(KEY, "")
    elif misconfiguration == "llm_only":
        text = "LLM_API_KEY=" + KEY
    else:
        text = text.replace("https://dashscope.aliyuncs.com", "https://a.ap-southeast-1.maas.aliyuncs.com")
    env.write_text(text, encoding="utf-8")
    assert runner.main(["--live"]) != 0 and http.calls == []
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err


@pytest.mark.parametrize("name", ["outside.json", "embedding-preview-old.json"])
def test_bad_output_path_is_rejected_before_http(setup_live, tmp_path, name):
    http, _, directory = setup_live()
    output = tmp_path / name if name == "outside.json" else directory / name
    assert runner.main(["--live", "--output", str(output)]) != 0
    assert http.calls == [] and not output.exists() and not (directory / "embedding-live.lock").exists()


def test_unwritable_report_target_is_rejected_before_http(setup_live, monkeypatch):
    http, _, directory = setup_live()
    original = Path.open

    def denied(path, mode="r", *args, **kwargs):
        if path.parent == directory and mode == "x":
            raise PermissionError(KEY)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    assert runner.main(["--live"]) != 0
    assert http.calls == [] and not (directory / "embedding-live.lock").exists()


def test_changed_jobs_hash_fails_before_credentials_or_http(setup_live, monkeypatch, tmp_path):
    http, _, _ = setup_live()
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_bytes(runner.JOBS.read_bytes() + b" ")
    original = runner.load_plan
    monkeypatch.setattr(runner, "load_plan", lambda: original(jobs_file=jobs_file))
    monkeypatch.setattr(EmbeddingConfig, "from_env_file", lambda *args: pytest.fail("Must reject data first"))
    assert runner.main(["--live"]) != 0 and http.calls == []


def test_changed_extracted_query_hash_fails_before_http(setup_live, monkeypatch):
    http, _, _ = setup_live()
    original = runner.extract_pdf

    def changed(path):
        extracted = original(path)
        return replace(extracted, text=extracted.text + " changed") if path.stem == "personal_api" else extracted

    monkeypatch.setattr(runner, "extract_pdf", changed)
    assert runner.main(["--live"]) != 0 and http.calls == []


def test_changed_jd_text_is_rejected_even_if_job_loader_is_substituted(setup_live, monkeypatch):
    http, _, _ = setup_live()
    jobs = runner.load_jobs()
    jobs[0] = replace(jobs[0], raw_text="changed")
    monkeypatch.setattr(runner, "load_jobs", lambda *args: jobs)
    assert runner.main(["--live"]) != 0 and http.calls == []


def test_preview_cannot_overwrite_a_live_report(setup_live):
    http, _, directory = setup_live()
    directory.mkdir()
    output = directory / "embedding-live-old.json"
    output.write_text("keep prior evidence", encoding="utf-8")
    assert runner.main(["--output", str(output)]) != 0
    assert output.read_text(encoding="utf-8") == "keep prior evidence" and http.calls == []


def test_lock_and_initial_report_exist_before_any_fake_http(setup_live, monkeypatch):
    http, _, directory = setup_live()
    original = http.open

    def checked(request, timeout):
        lock = json.loads((directory / "embedding-live.lock").read_text(encoding="utf-8"))
        initial = json.loads((directory / lock["report"]).read_text(encoding="utf-8"))
        assert initial["run_id"] == lock["run_id"]
        assert initial["status"] == "ARMED" and initial["counts_final"] is False
        return original(request, timeout)

    monkeypatch.setattr(http, "open", checked)
    assert runner.main(["--live"]) == 0 and len(http.calls) == 2


def test_lock_flush_failure_keeps_same_run_identity_and_makes_no_http(setup_live, monkeypatch):
    http, _, directory = setup_live()
    original = runner.os.fsync
    calls = []

    def fail_second(descriptor):
        calls.append(descriptor)
        if len(calls) == 2:
            raise OSError(KEY)
        return original(descriptor)

    monkeypatch.setattr(runner.os, "fsync", fail_second)
    assert runner.main(["--live"]) != 0 and http.calls == []
    lock = json.loads((directory / "embedding-live.lock").read_text(encoding="utf-8"))
    report = reports_in(directory)[0]
    assert report["run_id"] == lock["run_id"] and report["status"] == "NOT_STARTED"


def test_git_lf_jobs_snapshot_is_accepted(tmp_path):
    jobs = tmp_path / "jobs.json"
    jobs.write_bytes(runner.JOBS.read_bytes().replace(b"\r\n", b"\n"))
    plan = runner.load_plan(jobs_file=jobs)
    assert plan.jobs_sha256 == runner.JOBS_SHAS[1]
    assert tuple(row.case_id for row in plan.queries) == tuple(runner.QUERY_SHAS)


def test_final_report_failure_retains_lock_and_does_not_allow_another_round(setup_live, monkeypatch, capsys):
    http, _, directory = setup_live()

    def cannot_replace(*args):
        raise OSError(KEY)

    monkeypatch.setattr(runner.os, "replace", cannot_replace)
    assert runner.main(["--live"]) != 0
    assert len(http.calls) == 2 and (directory / "embedding-live.lock").exists()
    assert reports_in(directory)[0]["counts_final"] is False
    assert runner.main(["--live"]) != 0 and len(http.calls) == 2
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err


def test_interrupt_after_lock_creation_but_before_http_is_recorded(setup_live, monkeypatch):
    http, _, directory = setup_live()
    original = runner._acquire_live_lock

    def interrupted(report, destination):
        original(report, destination)
        raise KeyboardInterrupt()

    monkeypatch.setattr(runner, "_acquire_live_lock", interrupted)
    assert runner.main(["--live"]) != 0 and http.calls == []
    report = reports_in(directory)[0]
    assert report["status"] == "INTERRUPTED" and report["real_http_requests"] == 0
    assert report["counts_final"] is True and (directory / "embedding-live.lock").exists()
