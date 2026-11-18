"""Offline acceptance tests for multi-PDF DeepSeek evaluation."""

from dataclasses import replace
import json

import pytest

from app import deepseek_client
from app.deepseek_client import Completion, DeepSeekClient, DeepSeekConfig, DeepSeekError, PROMPT_VERSION_V2
from app.project_scorer import DIMENSIONS
from eval import run_live_pdf_eval as evaluator


@pytest.fixture
def plan():
    return evaluator.load_cases()


def _fake_completion(request):
    messages = json.loads(request.data)["messages"]
    model_input = json.loads(messages[1]["content"])
    evidence = model_input["snippet"].splitlines()[0]
    payload = {name: {"points": 7, "evidence": evidence} for name in DIMENSIONS}
    return Completion(json.dumps(payload), "returned-test-model", "test-fingerprint", 200, 50)


def _client(monkeypatch, transport):
    monkeypatch.setattr(DeepSeekClient, "_request", lambda self, request: transport(request))
    monkeypatch.setattr(deepseek_client, "sleep", lambda seconds: None)
    return DeepSeekClient(DeepSeekConfig("test-only"), prompt_version=PROMPT_VERSION_V2)


def test_preview_checks_the_frozen_cases_without_config_or_client(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Preview must not load provider configuration or construct a client")

    monkeypatch.setattr(DeepSeekConfig, "from_env_file", forbidden)
    monkeypatch.setattr(evaluator, "DeepSeekClient", forbidden)
    assert evaluator.main([]) == 0
    output = capsys.readouterr().out
    assert "Cases=8, first attempts=5, retries<=2, requests<=7" in output
    assert "Dry run: 0 network requests." in output
    assert "short_gate: REVIEW / - / Mock -1, skip." in output


def test_preflight_rejects_fixture_checksum_mismatch(plan):
    cases, _ = plan
    with pytest.raises(ValueError, match="frozen SHA-256"):
        evaluator._prepare({**cases[0].label, "sha256": "0" * 64})


def test_live_report_uses_only_frozen_project_text_and_jobs(monkeypatch, plan):
    cases, manifest_sha = plan
    sent = []

    def respond(request):
        body = json.loads(request.data)
        assert body["messages"][0]["content"] == deepseek_client.PROJECT_SCORER_PROMPT_V2
        model_input = json.loads(body["messages"][1]["content"])
        assert set(model_input) == {"snippet", "job_requirements"}
        assert "Work experience" not in model_input["snippet"]
        assert "Fictional Linden" not in model_input["snippet"]
        assert "Fictional workshop demonstration" not in model_input["snippet"]
        assert "FastAPI" in model_input["job_requirements"]
        sent.append(model_input)
        return _fake_completion(request)

    report = evaluator.run_live(cases, manifest_sha, _client(monkeypatch, respond), "test-model")
    assert report["manifest_sha256_normalized"] == manifest_sha
    assert report["valid"] == report["success_denominator"] == report["project_metrics_denominator"] == 5
    assert report["requests"] == len(sent) == 5
    assert report["expected_skips"] == report["skipped"] == 3
    assert report["full_matches"] == 1
    assert report["project_mae"] == pytest.approx(7.6)
    assert report["failed"] == report["not_run"] == 0
    normal = report["cases"][0]
    assert normal["mock"]["total"] == 60 and normal["live"]["tier"] == "A"
    assert normal["live"]["total"] == 81
    assert normal["transport"]["model"] == "returned-test-model"
    assert normal["transport"]["input_tokens"] == 200
    assert report["cases"][-1]["live"]["reason_code"] == "TOO_SHORT"
    assert report["cases"][-1]["live"]["total"] == -1


def test_retry_budget_reserves_first_attempts_and_continues(monkeypatch, plan):
    cases, manifest_sha = plan
    attempts = []

    def respond(request):
        attempts.append(request)
        if len(attempts) <= 4:
            raise DeepSeekError("NETWORK_ERROR")
        return _fake_completion(request)

    report = evaluator.run_live(cases, manifest_sha, _client(monkeypatch, respond), "test-model")
    assert report["requests"] == report["request_limit"] == len(attempts) == 7
    assert report["retries"] == 2 and report["not_run"] == 0
    assert report["failed"] == 2 and report["valid"] == 3
    assert report["success_denominator"] == 5 and report["project_metrics_denominator"] == 3
    assert [record["requests"] for record in report["cases"]] == [2, 2, 1, 1, 1, 0, 0, 0]
    for row in report["cases"][:2]:
        assert row["failure"] == {"code": "NETWORK_ERROR", "http_status": None}
        assert row["live"]["status"] == "REVIEW"
        assert row["live"]["reason_code"] == "PROJECT_SCORER_ERROR"
        assert row["live"]["total"] == -1
        assert row["live"]["tier"] is None and row["live"]["scores"] is None


def test_invalid_completion_is_excluded_and_records_review(monkeypatch, plan):
    cases, manifest_sha = plan
    attempts = []

    def respond(request):
        attempts.append(request)
        answer = _fake_completion(request)
        return replace(answer, text='{"relevance": 99}') if len(attempts) == 1 else answer

    report = evaluator.run_live(cases, manifest_sha, _client(monkeypatch, respond), "test-model")
    assert report["requests"] == 5 and report["failed"] == 1 and report["valid"] == 4
    first = report["cases"][0]
    assert first["outcome"] == "failed"
    assert first["failure"]["code"] == "PROJECT_SCORER_ERROR"
    assert first["live"]["total"] == -1 and first["project_absolute_error"] is None


def test_changed_project_input_is_rejected_before_a_request(monkeypatch, plan):
    cases, _ = plan

    def forbidden(request):
        pytest.fail("Changed project input must not be sent")

    case = replace(cases[0], model_input=("outdated excerpt", "outdated requirements"))
    result = evaluator._run_case(case, _client(monkeypatch, forbidden), allow_retry=True)
    assert result["requests"] == 0
    assert result["failure"]["code"] == "UNEXPECTED_MODEL_INPUT"


def test_live_cli_keeps_a_new_report_per_invocation(monkeypatch, tmp_path):
    monkeypatch.setattr(DeepSeekConfig, "from_env_file", lambda path: DeepSeekConfig("test-only"))
    monkeypatch.setattr(DeepSeekClient, "_request", lambda self, request: _fake_completion(request))
    monkeypatch.setattr(evaluator, "REPORT_DIR", tmp_path)

    assert evaluator.main(["--live"]) == 0
    assert evaluator.main(["--live"]) == 0
    reports = sorted(tmp_path.glob("live-pdf-*.json"))
    assert len(reports) == 2
    for report_path in reports:
        contents = report_path.read_text(encoding="utf-8")
        assert "test-only" not in contents and "Fictional Linden" not in contents
        assert json.loads(contents)["valid"] == 5
