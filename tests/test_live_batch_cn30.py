"""Offline acceptance tests for the whole-resume Chinese batch evaluation."""

from dataclasses import replace
import json

import pytest

from app import deepseek_client
from app.deepseek_client import Completion, DeepSeekClient, DeepSeekConfig, DeepSeekError, PROMPT_VERSION_V2
from app.project_scorer import DIMENSIONS
from eval import run_live_batch_cn30 as evaluator


@pytest.fixture
def plan():
    return evaluator.load_cases()


def _fake_completion(request):
    messages = json.loads(request.data)["messages"]
    model_input = json.loads(messages[1]["content"])
    evidence = model_input["snippet"].splitlines()[0]
    payload = {name: {"points": 7, "evidence": evidence} for name in DIMENSIONS}
    return Completion(json.dumps(payload), "returned-test-model", "test-fingerprint", 180, 40)


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
    assert "Cases=16, first attempts=3, retries<=3, requests<=6" in output
    assert "Dry run: 0 network requests." in output
    assert "R19: REVIEW / - / Mock -1, skip." in output


def test_preflight_rejects_fixture_checksum_mismatch(plan):
    cases, _ = plan
    with pytest.raises(ValueError, match="frozen SHA-256"):
        evaluator._prepare({**cases[0].label, "sha256": "0" * 64})


def test_live_report_scores_only_resumes_with_project_snippets(monkeypatch, plan):
    cases, manifest_sha = plan
    sent = []

    def respond(request):
        body = json.loads(request.data)
        assert body["messages"][0]["content"] == deepseek_client.PROJECT_SCORER_PROMPT_V2
        model_input = json.loads(body["messages"][1]["content"])
        assert set(model_input) == {"snippet", "job_requirements"}
        sent.append(model_input)
        return _fake_completion(request)

    report = evaluator.run_live(cases, manifest_sha, _client(monkeypatch, respond), "test-model")
    assert report["manifest_sha256_normalized"] == manifest_sha
    assert report["valid"] == report["success_denominator"] == 3
    assert report["requests"] == len(sent) == 3
    assert report["expected_skips"] == report["skipped"] == 13
    assert report["failed"] == report["not_run"] == 0
    assert report["tier_drift"] == 3
    assert report["project_totals"] == [21, 21, 21]
    assert [row["case_id"] for row in report["cases"] if row["outcome"] == "valid"] == ["R07", "R11", "R16"]
    scored = report["cases"][3]
    assert scored["mock"]["total"] == 65 and scored["live"]["total"] == 86 and scored["live"]["tier"] == "A"
    assert scored["end_to_end_total_delta"] == 21 and scored["tier_changed"] is True
    assert scored["transport"]["model"] == "returned-test-model"
    assert scored["transport"]["input_tokens"] == 180
    skipped = report["cases"][0]
    assert skipped["outcome"] == "skipped" and skipped["requests"] == 0
    assert skipped["live"]["scores"] == skipped["mock"]["scores"]
    unmatched = report["cases"][-2]
    assert unmatched["case_id"] == "R27" and unmatched["live"]["jd_id"] == "UNMATCHED"


def test_permanent_failure_stops_later_completions_but_preserves_skips(monkeypatch, plan):
    cases, manifest_sha = plan
    attempts = []

    def respond(request):
        attempts.append(request)
        if len(attempts) == 2:
            raise DeepSeekError("HTTP_ERROR", status=401)
        return _fake_completion(request)

    report = evaluator.run_live(cases, manifest_sha, _client(monkeypatch, respond), "test-model")
    assert len(attempts) == report["requests"] == 2
    assert report["retries"] == 0 and report["failed"] == 1 and report["not_run"] == 1
    assert report["valid"] == 1 and report["skipped"] == report["expected_skips"] == 13
    failed = report["cases"][5]
    assert failed["case_id"] == "R11" and failed["failure"] == {"code": "HTTP_ERROR", "http_status": 401}
    assert failed["live"]["status"] == "REVIEW" and failed["live"]["reason_code"] == "PROJECT_SCORER_ERROR"
    assert failed["live"]["total"] == -1 and failed["live"]["scores"] is None
    not_run = report["cases"][9]
    assert not_run["case_id"] == "R16" and not_run["outcome"] == "not_run"
    assert not_run["requests"] == 0 and not_run["live"] is None


def test_changed_project_input_is_rejected_before_a_request(monkeypatch, plan):
    cases, _ = plan

    def forbidden(request):
        pytest.fail("Changed project input must not be sent")

    case = replace(cases[3], model_input=("过期的项目摘录", "过期的岗位要求"))
    result = evaluator._run_case(case, _client(monkeypatch, forbidden), allow_retry=True)
    assert result["requests"] == 0
    assert result["failure"]["code"] == "UNEXPECTED_MODEL_INPUT"


def test_live_cli_keeps_a_new_report_per_invocation(monkeypatch, tmp_path):
    monkeypatch.setattr(DeepSeekConfig, "from_env_file", lambda path: DeepSeekConfig("test-only"))
    monkeypatch.setattr(DeepSeekClient, "_request", lambda self, request: _fake_completion(request))
    monkeypatch.setattr(evaluator, "REPORT_DIR", tmp_path)

    assert evaluator.main(["--live"]) == 0
    assert evaluator.main(["--live"]) == 0
    reports = sorted(tmp_path.glob("live-batch-cn30-*.json"))
    assert len(reports) == 2
    for report_path in reports:
        contents = report_path.read_text(encoding="utf-8")
        assert "test-only" not in contents
        report = json.loads(contents)
        assert report["valid"] == 3 and report["request_limit"] == 6
