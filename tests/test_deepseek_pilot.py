"""Exercise the client boundary and pilot without remote requests."""

from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from app.deepseek_client import DeepSeekClient, DeepSeekConfig, DeepSeekError
from app.project_scorer import score_project
from app.route import load_jobs
from eval import run_deepseek_pilot as pilot


def reply(text, *, finish="stop"):
    payload = {
        "model": "deepseek-flash", "system_fingerprint": "test-model-version",
        "choices": [{"finish_reason": finish, "message": {"content": text}}],
        "usage": {"prompt_tokens": 201, "completion_tokens": 23},
    }
    return BytesIO(json.dumps(payload).encode())


def http_error(status):
    return HTTPError("https://api.deepseek.com/chat/completions", status, "", {}, None)


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.client = DeepSeekClient(DeepSeekConfig("local-stub"), max_requests=4)
        self.job = load_jobs()[0]

    def test_client_sends_json_with_curated_fields_and_returns_usage(self):
        example = {"relevance": {"points": 3, "evidence": "FastAPI"},
                   "contribution": {"points": 0, "evidence": ""},
                   "result": {"points": 0, "evidence": ""}}
        with patch.object(self.client._opener, "open", return_value=reply(json.dumps(example))) as send:
            self.assertEqual(score_project("熟悉 FastAPI", self.job, self.client), 3)
        request = send.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer local-stub")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(json.loads(body["messages"][1]["content"]),
                         {"snippet": "熟悉 FastAPI", "job_requirements": self.job.raw_text})
        self.assertNotIn("tools", body)
        self.assertEqual(self.client.requests, 1)

    def test_transient_error_retries_once_and_respects_global_cap(self):
        client = DeepSeekClient(DeepSeekConfig("local-stub"), max_requests=2)
        with patch.object(client._opener, "open", side_effect=[http_error(429), reply("{}")]) as send, patch(
            "app.deepseek_client.sleep"
        ):
            response = client.complete("片段", "岗位")
            self.assertEqual((response.input_tokens, response.output_tokens), (201, 23))
            with self.assertRaisesRegex(DeepSeekError, "REQUEST_LIMIT"):
                client.complete("片段", "岗位")
            self.assertEqual(send.call_count, 2)

    def test_auth_and_redirect_errors_never_retry(self):
        for status in (302, 401, 402):
            with self.subTest(status=status), patch.object(
                self.client._opener, "open", side_effect=http_error(status)
            ) as send:
                with self.assertRaises(DeepSeekError) as error:
                    self.client.complete("片段", "岗位")
                self.assertEqual(error.exception.status, status)
                send.assert_called_once()

    def test_empty_or_truncated_reply_fails_without_retry(self):
        for raw, code in ((reply("  "), "EMPTY_RESPONSE"),
                          (reply("{}", finish="length"), "INCOMPLETE_RESPONSE"),
                          (BytesIO(b"not-json"), "INVALID_ENVELOPE")):
            with self.subTest(code=code), patch.object(self.client._opener, "open", return_value=raw) as send:
                with self.assertRaisesRegex(DeepSeekError, code):
                    self.client.complete("片段", "岗位")
                send.assert_called_once()

    def test_config_rejects_other_hosts_and_does_not_repr_key(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / ".env"
            body = ("LLM_PROVIDER=deepseek\nLLM_MODEL=deepseek-flash\n"
                    "LLM_API_KEY=local-stub\nLLM_BASE_URL=")
            config.write_text(body + "https://api.deepseek.com.example.org", encoding="utf-8")
            with self.assertRaisesRegex(DeepSeekError, "CONFIG_ENDPOINT"):
                DeepSeekConfig.from_env_file(config)
            config.write_text(body + "https://api.deepseek.com", encoding="utf-8")
            self.assertNotIn("local-stub", repr(DeepSeekConfig.from_env_file(config)))

    def test_default_cli_does_not_read_credentials_or_create_a_client(self):
        with patch.object(pilot.DeepSeekConfig, "from_env_file") as config, patch.object(
            pilot, "DeepSeekClient"
        ) as client:
            self.assertEqual(pilot.main([]), 0)
        config.assert_not_called()
        client.assert_not_called()

    def test_pilot_records_label_drift_without_persisting_raw_response(self):
        case = {"case_id": "example", "jd_id": self.job.jd_id, "snippet": "熟悉 FastAPI",
                "relevance": 3, "contribution": 0, "result": 0, "total": 3}
        response = {name: {"points": 0, "evidence": ""} for name in pilot.DIMENSIONS}
        with patch.object(self.client._opener, "open", return_value=reply(json.dumps(response))):
            report = pilot.run_pilot([case], {self.job.jd_id: self.job}, self.client, "deepseek-flash", "sha")
        self.assertEqual((report["valid_responses"], report["exact_matches"], report["project_mae"]), (1, 0, 3))
        self.assertEqual(report["observed_usage"]["input_tokens"], 201)
        self.assertNotIn("FastAPI", json.dumps(report))

    def test_pilot_stops_after_auth_failure_and_lists_remaining_cases(self):
        cases = [{"case_id": case_id, "jd_id": self.job.jd_id, "snippet": "片段",
                  "relevance": 0, "contribution": 0, "result": 0, "total": 0}
                 for case_id in pilot.SMOKE_IDS]
        with patch.object(self.client._opener, "open", side_effect=http_error(401)):
            report = pilot.run_pilot(cases, {self.job.jd_id: self.job}, self.client, "deepseek-flash", "sha")
        self.assertEqual(report["requests"], 1)
        self.assertEqual(report["cases"][0]["status"], "REVIEW")
        self.assertEqual(report["not_run"], list(pilot.SMOKE_IDS[1:]))
