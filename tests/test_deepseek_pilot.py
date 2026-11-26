"""Exercise the client boundary and pilot without remote requests."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from app.deepseek_client import (
    DeepSeekClient, DeepSeekConfig, DeepSeekError, PROJECT_SCORER_PROMPT_V2, _INSTRUCTIONS,
)
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

    def test_rate_limit_backoff_is_exponential_and_other_transients_stay_fixed(self):
        sleeps = []
        client = DeepSeekClient(DeepSeekConfig("local-stub"), max_requests=2)
        with patch.object(client._opener, "open", side_effect=[http_error(429), reply("{}")]), patch(
            "app.deepseek_client.sleep", side_effect=sleeps.append
        ):
            client.complete("片段", "岗位")
            self.assertEqual(sleeps, [1.0])

        sleeps.clear()
        client = DeepSeekClient(DeepSeekConfig("local-stub"), max_requests=2)
        with patch.object(client._opener, "open", side_effect=[http_error(500), reply("{}")]), patch(
            "app.deepseek_client.sleep", side_effect=sleeps.append
        ):
            client.complete("片段", "岗位")
            self.assertEqual(sleeps, [0.5])

        sleeps.clear()
        client = DeepSeekClient(DeepSeekConfig("local-stub"), max_requests=3)
        with patch.object(client._opener, "open", side_effect=[http_error(429), http_error(429)]), patch(
            "app.deepseek_client.sleep", side_effect=sleeps.append
        ):
            with self.assertRaisesRegex(DeepSeekError, "HTTP_ERROR"):
                client.complete("片段", "岗位")
            self.assertEqual(sleeps, [1.0])

    def test_retry_delay_schedule_is_exponential_and_capped(self):
        from app.deepseek_client import _retry_delay

        self.assertEqual([_retry_delay(attempt, 429) for attempt in range(4)], [1.0, 2.0, 4.0, 8.0])
        self.assertEqual(_retry_delay(5, 429), 8.0)
        self.assertEqual(_retry_delay(0, 500), 0.5)
        self.assertEqual(_retry_delay(0, None), 0.5)

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
            for argv in ([], ["--all"]):
                self.assertEqual(pilot.main(argv), 0)
        config.assert_not_called()
        client.assert_not_called()

    def test_v2_preview_lists_all_cases_without_reading_config_or_opening_http(self):
        with patch.object(pilot.DeepSeekConfig, "from_env_file") as config, patch.object(
            pilot, "DeepSeekClient"
        ) as client, patch("builtins.print") as printed:
            self.assertEqual(pilot.main(["--all", "--prompt-version", "v2"]), 0)
        config.assert_not_called()
        client.assert_not_called()
        output = printed.call_args.args[0]
        self.assertIn("zero network requests", output)
        ids = output.split("Full suite: ")[1].split(", ")
        self.assertEqual(len(ids), 15)
        self.assertIn("empty", ids)
        self.assertEqual(ids[-3:], list(pilot.V2_EXTRA_IDS))

    def test_original_labels_are_frozen(self):
        content = pilot.LABELS.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(sha256(content).hexdigest(),
                         "b9c59b5cf05224a3a70d05520f112daaeb77d7dca2de3c6c88e3fd96f63fc56c")

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

    def run_fake_cli(self, argv, send):
        """Run the CLI against a fake transport and temporary reports."""
        with TemporaryDirectory() as directory:
            smoke = Path(directory) / "deepseek-smoke.json"
            full = Path(directory) / "deepseek-all.json"
            v2 = Path(directory) / "deepseek-v2.json"
            smoke.write_text("original smoke", encoding="utf-8")
            if "v2" in argv:
                full.write_text("original full", encoding="utf-8")
            with patch.multiple(pilot, REPORT=smoke, FULL_REPORT=full, V2_REPORT=v2), patch.object(
                pilot.DeepSeekConfig, "from_env_file", return_value=DeepSeekConfig("local-stub")
            ), patch("app.deepseek_client.build_opener", return_value=Mock(open=send)), patch(
                "app.deepseek_client.sleep"
            ):
                code = pilot.main(argv)
            if "v2" in argv:
                self.assertEqual(smoke.read_text(encoding="utf-8"), "original smoke")
                self.assertEqual(full.read_text(encoding="utf-8"), "original full")
                output = v2
            elif "--all" in argv:
                self.assertEqual(smoke.read_text(encoding="utf-8"), "original smoke")
                self.assertFalse(v2.exists())
                output = full
            else:
                self.assertFalse(full.exists())
                output = smoke
            return code, json.loads(output.read_text(encoding="utf-8"))

    def test_full_cli_skips_empty_and_reports_drift_and_invalid_response(self):
        zero = json.dumps({name: {"points": 0, "evidence": ""} for name in pilot.DIMENSIONS})
        send = Mock(side_effect=[reply("invalid")] + [reply(zero) for _ in range(10)])
        code, report = self.run_fake_cli(["--live", "--all"], send)
        self.assertEqual(code, 0)
        self.assertEqual(report["prompt_version"], "project-score-v1")
        self.assertEqual(len(report["requested_cases"]), 12)
        self.assertNotIn("v2_comparison", report)
        self.assertEqual(json.loads(send.call_args_list[0].args[0].data)["messages"][0]["content"],
                         _INSTRUCTIONS)
        self.assertFalse(report["aborted"])
        self.assertEqual((report["requests"], report["requested_nonempty"], send.call_count), (11, 11, 11))
        self.assertEqual((report["skipped_local"], report["valid_responses"],
                          report["invalid_responses"], report["metrics_denominator"]), (1, 10, 1, 10))
        skipped, invalid, drift = report["cases"][:3]
        self.assertEqual((skipped["status"], skipped["actual_total"], skipped["requests"]),
                         ("SKIPPED", 0, 0))
        self.assertTrue(skipped["skip_client"])
        self.assertEqual((invalid["status"], invalid["failure"]), ("REVIEW", "INVALID_PROJECT_RESPONSE"))
        self.assertEqual((drift["status"], drift["matched"]), ("SCORED", False))
        self.assertEqual(report["project_mae"], 15.5)
        self.assertEqual(report["exact_matches"], 1)
        self.assertEqual(report["dimension_matches"], {"relevance": 1, "contribution": 3, "result": 4})
        self.assertEqual(report["cases"][8]["job_id"], "JD2")
        self.assertIsNone(report["instruction_pairs"][0]["delta"])
        self.assertEqual(report["instruction_pairs"][1]["delta"],
                         {"relevance": 0, "contribution": 0, "result": 0, "total": 0})
        payload = json.dumps(report)
        self.assertNotIn("FastAPI", payload)
        self.assertNotIn("local-stub", payload)
        self.assertNotIn("evidence", payload)

    def test_v2_sends_extended_prompt_and_reports_original_and_contrast_scores(self):
        zero = json.dumps({name: {"points": 0, "evidence": ""} for name in pilot.DIMENSIONS})
        send = Mock(side_effect=[reply(zero) for _ in range(14)])
        code, report = self.run_fake_cli(["--live", "--all", "--prompt-version", "v2"], send)
        self.assertEqual(code, 0)
        self.assertEqual(report["prompt_version"], "project-score-v2")
        self.assertEqual(len(report["requested_cases"]), 15)
        self.assertEqual((report["valid_responses"], report["skipped_local"], send.call_count), (14, 1, 14))
        self.assertEqual(report["cases"][0]["case_id"], "empty")
        self.assertEqual(report["cases"][0]["requests"], 0)
        self.assertEqual(report["extra_labels_sha256"], sha256(pilot.V2_EXTRA_LABELS.read_bytes()).hexdigest())
        system = json.loads(send.call_args_list[0].args[0].data)["messages"][0]["content"]
        self.assertEqual(system, PROJECT_SCORER_PROMPT_V2)
        self.assertTrue(system.startswith(_INSTRUCTIONS))
        for rule in ("我 / 本人 / 负责", "可描述的团队指标", "补了接口测试"):
            self.assertIn(rule, system[len(_INSTRUCTIONS):])
        comparison = report["v2_comparison"]
        self.assertEqual(comparison["original_cases"],
                         {"metrics_denominator": 11, "exact_matches": 1, "project_mae": 14.364})
        self.assertEqual({key: item["delta"]["total"] for key, item in comparison["extra_cases"].items()},
                         {"i_joined_order_api": -10, "team_claimed_faster": -13, "flask_done_no_test": -17})
        self.assertEqual(set(comparison["v1_disagreements"]), set(pilot.V1_DISAGREEMENT_IDS))
        self.assertEqual(comparison["v1_disagreements"]["team_we"]["actual"],
                         dict.fromkeys(pilot.DIMENSIONS, 0))

    def test_v2_retries_leave_a_first_request_for_each_later_case(self):
        zero = json.dumps({name: {"points": 0, "evidence": ""} for name in pilot.DIMENSIONS})
        send = Mock(side_effect=[
            http_error(429), reply(zero), http_error(429), reply(zero), http_error(429),
            *[reply(zero) for _ in range(11)],
        ])
        code, report = self.run_fake_cli(["--live", "--all", "--prompt-version", "v2"], send)
        self.assertEqual(code, 0)
        self.assertEqual((report["request_limit"], report["requests"], report["retries"]), (16, 16, 2))
        self.assertEqual([entry["requests"] for entry in report["cases"]], [0, 2, 2] + [1] * 12)
        self.assertEqual(report["not_run"], [])
        failed = report["v2_comparison"]["v1_disagreements"]["team_we"]
        self.assertEqual(failed["status"], "REVIEW")
        self.assertIsNone(failed["delta"])
        self.assertEqual(report["v2_comparison"]["original_cases"]["metrics_denominator"], 10)

    def test_v2_auth_failure_preserves_not_run_comparisons(self):
        send = Mock(side_effect=http_error(401))
        code, report = self.run_fake_cli(["--live", "--all", "--prompt-version", "v2"], send)
        self.assertEqual(code, 1)
        self.assertTrue(report["aborted"])
        self.assertEqual(report["v2_comparison"]["original_cases"]["metrics_denominator"], 0)
        self.assertIsNone(report["v2_comparison"]["original_cases"]["project_mae"])
        extra = report["v2_comparison"]["extra_cases"]["flask_done_no_test"]
        self.assertEqual((extra["status"], extra["expected_total"]), ("NOT_RUN", 17))
        self.assertIsNone(extra["actual_total"])
        self.assertIsNone(extra["delta"])
        send.assert_called_once()

    def test_full_cli_reserves_all_first_attempts_after_two_retries(self):
        zero = json.dumps({name: {"points": 0, "evidence": ""} for name in pilot.DIMENSIONS})
        send = Mock(side_effect=[
            http_error(429), reply(zero), http_error(429), reply(zero), http_error(429),
            *[reply(zero) for _ in range(8)],
        ])
        code, report = self.run_fake_cli(["--live", "--all"], send)
        self.assertEqual(code, 0)
        self.assertEqual((report["requests"], report["retries"], send.call_count), (13, 2, 13))
        self.assertEqual([entry["requests"] for entry in report["cases"]], [0, 2, 2] + [1] * 9)
        self.assertEqual(report["not_run"], [])
        self.assertEqual(report["cases"][3]["http_status"], 429)
        self.assertEqual(report["cases"][-1]["status"], "SCORED")
        self.assertEqual(len(report["instruction_pairs"]), 2)
        self.assertTrue(all(pair["delta"] is not None for pair in report["instruction_pairs"]))

    def test_full_cli_returns_one_on_auth_failure_and_preserves_partial_report(self):
        send = Mock(side_effect=http_error(401))
        code, report = self.run_fake_cli(["--live", "--all"], send)
        self.assertEqual(code, 1)
        self.assertTrue(report["aborted"])
        self.assertEqual([entry["status"] for entry in report["cases"]], ["SKIPPED", "REVIEW"])
        self.assertEqual(report["not_run"], report["requested_cases"][2:])
        send.assert_called_once()

    def test_smoke_cli_keeps_three_case_order_and_four_request_limit(self):
        zero = json.dumps({name: {"points": 0, "evidence": ""} for name in pilot.DIMENSIONS})
        send = Mock(side_effect=[reply(zero) for _ in range(3)])
        code, report = self.run_fake_cli(["--live"], send)
        self.assertEqual(code, 1)
        self.assertEqual(report["requested_cases"], list(pilot.SMOKE_IDS))
        self.assertEqual((report["request_limit"], report["requests"], send.call_count), (4, 3, 3))

    def test_instruction_delta_is_instructed_minus_plain_by_dimension(self):
        plain = {"case_id": "tools_only", "actual": {"relevance": 3, "contribution": 0, "result": 0},
                 "actual_total": 3}
        instructed = {"case_id": "jailbreak_tools",
                      "actual": {"relevance": 0, "contribution": 7, "result": 0}, "actual_total": 7}
        pairs = pilot._instruction_deltas([plain, instructed])
        self.assertEqual(pairs[0]["delta"], {"relevance": -3, "contribution": 7, "result": 0, "total": 4})
        self.assertIsNone(pairs[1]["delta"])
