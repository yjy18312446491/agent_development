"""Verify the explicit screening smoke CLI with a fake HTTP transport."""

from io import BytesIO
import json
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import pytest

from app.deepseek_client import DeepSeekConfig, PROJECT_SCORER_PROMPT_V2
from app.route import load_jobs
from eval import run_screen_deepseek as demo


PROJECT_TEXT = (
    "Built a sample order lookup API with FastAPI.\n"
    "Added tests for missing orders and invalid IDs."
)
SCORES = {
    "relevance": {"points": 7, "evidence": "order lookup API with FastAPI"},
    "contribution": {"points": 0, "evidence": ""},
    "result": {"points": 7, "evidence": "Added tests for missing orders and invalid IDs."},
}


def reply(content):
    return BytesIO(json.dumps({
        "model": "deepseek-flash", "system_fingerprint": "fake-model-version",
        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        "usage": {"prompt_tokens": 250, "completion_tokens": 50},
    }).encode("utf-8"))


def http_error(status):
    return HTTPError("https://api.deepseek.com/chat/completions", status, "", {}, None)


@pytest.fixture(autouse=True)
def transport(monkeypatch, tmp_path):
    pytest.importorskip("fitz")
    send = Mock(side_effect=AssertionError("Unexpected HTTP request"))
    config = Mock(return_value=DeepSeekConfig("local-test-key"))
    output = tmp_path / "screen-deepseek.json"
    monkeypatch.setattr(demo.DeepSeekConfig, "from_env_file", config)
    monkeypatch.setattr(demo, "REPORT", output)
    with patch("app.deepseek_client.build_opener", return_value=Mock(open=send)), patch(
        "app.deepseek_client.sleep"
    ):
        yield send, config, output


def test_preview_skips_configuration_http_and_report(transport):
    send, config, output = transport

    assert demo.main([]) == 0

    config.assert_not_called()
    send.assert_not_called()
    assert not output.exists()


def test_live_sends_only_project_and_job_and_reports_observed_scores(transport):
    send, config, output = transport
    send.side_effect = [reply(json.dumps(SCORES))]

    assert demo.main(["--live"]) == 0

    config.assert_called_once_with(demo.ROOT / ".env")
    send.assert_called_once()
    body = json.loads(send.call_args.args[0].data)
    assert body["messages"][0]["content"] == PROJECT_SCORER_PROMPT_V2
    jd1 = next(job for job in load_jobs() if job.jd_id == "JD1")
    assert json.loads(body["messages"][1]["content"]) == {
        "snippet": PROJECT_TEXT, "job_requirements": jd1.raw_text.strip(),
    }
    serialized = output.read_text(encoding="utf-8")
    report = json.loads(serialized)
    assert (report["passed"], report["requests"], report["prompt_version"]) == (
        True, 1, "project-score-v2",
    )
    baseline, live = report["baseline"], report["live"]
    assert (baseline["project_source"], baseline["scores"]["project"]) == ("mock", 0)
    assert (live["guard_status"], live["jd_id"], live["project_source"]) == ("PASS", "JD1", "injected")
    assert live["scores"]["project"] == 14 and live["total"] == baseline["total"] + 14
    assert report["project_dimensions"] == {"relevance": 7, "contribution": 0, "result": 7}
    for private_value in ("local-test-key", "Fictional Cedar", "job_requirements", "evidence",
                          *PROJECT_TEXT.splitlines()):
        assert private_value not in serialized


@pytest.mark.parametrize(("status", "attempts"), [(401, 1), (429, 2)])
def test_http_failure_is_reported_and_attempts_are_bounded(transport, status, attempts):
    send, _, output = transport
    send.side_effect = [http_error(status) for _ in range(attempts)]

    assert demo.main(["--live"]) == 1

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is False and report["requests"] == send.call_count == attempts
    assert report["failure"] == {"code": "HTTP_ERROR", "http_status": status}
    live = report["live"]
    assert (live["status"], live["jd_id"], live["reason_code"], live["total"]) == (
        "REVIEW", "JD1", "PROJECT_SCORER_ERROR", -1,
    )
    assert "project_dimensions" not in report


def test_malformed_project_response_keeps_review_and_never_adds_scores(transport):
    send, _, output = transport
    send.side_effect = [reply("invalid score JSON")]

    assert demo.main(["--live"]) == 1

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["live"]["reason_code"] == "PROJECT_SCORER_ERROR"
    assert report["live"]["jd_id"] == "JD1"
    assert "project_dimensions" not in report
    send.assert_called_once()
