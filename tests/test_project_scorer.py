"""Verify the offline project scoring contract and its integration with screening."""

from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from app.project_scorer import InvalidProjectResponse, score_project
from app.route import load_jobs
from app.screen import screen_one


ROOT = Path(__file__).resolve().parents[1]
RESUMES = ROOT / "data" / "synthetic" / "resumes"
SAMPLE = RESUMES / "S001_normal_backend.pdf"
SNIPPET = "我负责使用 FastAPI 开发订单接口，并完成接口测试。"
DIMENSIONS = ("relevance", "contribution", "result")


class ProjectScorerTests(unittest.TestCase):
    def setUp(self):
        self.jobs = {job.jd_id: job for job in load_jobs()}
        self.job = self.jobs["JD1"]

    def require_pymupdf(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is not installed")

    def test_empty_and_whitespace_snippets_skip_client(self):
        client = Mock()
        for snippet in ("", " \n\t"):
            with self.subTest(snippet=snippet):
                self.assertEqual(score_project(snippet, self.job, client), 0)
        client.assert_not_called()

    def test_two_client_arguments_and_skills_fallback(self):
        payload = {name: {"points": 0, "evidence": " "} for name in DIMENSIONS}
        client = Mock(return_value=json.dumps(payload))
        self.assertEqual(score_project(SNIPPET, self.job, client), 0)
        client.assert_called_once_with(SNIPPET, self.job.raw_text)
        client.reset_mock()
        job = replace(self.job, raw_text=" \n")
        self.assertEqual(score_project(SNIPPET, job, client), 0)
        client.assert_called_once_with(SNIPPET, "Python、FastAPI、MySQL、Redis")

    def test_frozen_labels_match_reviewed_scores_using_a_fake_client(self):
        scores = {
            "empty": (0, 0, 0, 0), "tools_only": (3, 0, 0, 3),
            "course_toy": (3, 3, 0, 6), "team_we": (7, 0, 3, 10),
            "personal_no_result": (7, 7, 0, 14), "wrong_direction": (0, 0, 0, 0),
            "jailbreak_tools": (3, 0, 0, 3), "team_metric": (7, 3, 7, 17),
            "flask_jd2": (7, 7, 7, 21), "detail_no_metric": (10, 7, 7, 24),
            "full_30": (10, 10, 10, 30), "full_with_please_fullmark": (10, 10, 10, 30),
        }
        evidence = {
            "tools_only": ("Python、FastAPI、MySQL", "", ""),
            "course_toy": ("Python 接口练习", "参与过 Python 接口练习", ""),
            "team_we": ("订单查询接口", "", "我们完成了订单查询接口"),
            "personal_no_result": ("使用 FastAPI 和 Redis 做登录态校验", "我负责用户鉴权模块", ""),
            "jailbreak_tools": ("熟悉 Python", "", ""),
            "team_metric": ("FastAPI 和 MySQL 做了订单查询接口", "我参与开发", "GMV 提升 50%"),
            "flask_jd2": ("使用 Flask 和 PostgreSQL", "我负责库存查询接口", "补了接口测试"),
            "detail_no_metric": ("用 FastAPI 实现分页查询", "我负责订单接口", "接口测试"),
            "full_30": ("FastAPI 订单查询", "我选择索引以避免订单状态不一致", "800ms 降到 120ms"),
            "full_with_please_fullmark": (
                "FastAPI 订单查询", "我选择索引以避免订单状态不一致", "800ms 降到 120ms"
            ),
        }
        source = ROOT / "eval" / "project_score_cases.jsonl"
        cases = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
        self.assertCountEqual([case["case_id"] for case in cases], scores.keys())
        for case in cases:
            case_id = case["case_id"]
            with self.subTest(case=case_id):
                labels = tuple(case[name] for name in (*DIMENSIONS, "total"))
                self.assertEqual(labels, scores[case_id])
                self.assertTrue(case["reason"].strip())
                self.assertEqual(case["jd_id"], "JD2" if case_id == "flask_jd2" else "JD1")
                self.assertIs(case["skip_client"], case_id == "empty")
                quotes = evidence.get(case_id, ("", "", ""))
                payload = {
                    name: {"points": case[name], "evidence": quote}
                    for name, quote in zip(DIMENSIONS, quotes)
                }
                client = Mock(return_value=json.dumps(payload))
                job = self.jobs[case["jd_id"]]
                self.assertEqual(score_project(case["snippet"], job, client), case["total"])
                if case["skip_client"]:
                    client.assert_not_called()
                else:
                    client.assert_called_once_with(case["snippet"], job.raw_text)

    def test_empty_or_whitespace_evidence_cannot_support_nonzero_points(self):
        for evidence in ("", " \t\n"):
            with self.subTest(evidence=evidence):
                with self.assertRaisesRegex(InvalidProjectResponse, "非零分必须引用项目原文"):
                    score_project(SNIPPET, self.job, self.client_with(7, evidence))

    def client_with(self, points, evidence):
        payload = {name: {"points": 0, "evidence": ""} for name in DIMENSIONS}
        payload["relevance"] = {"points": points, "evidence": evidence}
        return Mock(return_value=json.dumps(payload))

    def test_invalid_score_types_levels_and_evidence_are_rejected(self):
        for points, evidence in (
            (7.0, "订单接口"), (True, "订单接口"), (4, "订单接口"),
            (-1, "订单接口"), (31, "订单接口"), (7, "虚构证据"),
            (7, None), (0, "订单接口"),
        ):
            with self.subTest(points=points, evidence=evidence):
                with self.assertRaises(InvalidProjectResponse):
                    score_project(SNIPPET, self.job, self.client_with(points, evidence))
        self.assertEqual(score_project(SNIPPET, self.job, self.client_with(7, " 订单接口 ")), 7)

    def test_invalid_json_and_schema_are_rejected(self):
        valid = '{"points":0,"evidence":""}'
        for response in (
            None, "", " ", "not JSON", "[]", "null", "{}", "```json\n{}\n```",
            f'{{"relevance":{valid},"contribution":{valid}}}',
            f'{{"relevance":{valid},"contribution":{valid},"result":{valid},"total":0}}',
            f'{{"relevance":{{"points":0,"evidence":"","extra":1}},"contribution":{valid},"result":{valid}}}',
            f'{{"relevance":{{"points":0}},"contribution":{valid},"result":{valid}}}',
            f'{{"relevance":{{"points":7,"points":0,"evidence":""}},"contribution":{valid},"result":{valid}}}',
        ):
            with self.subTest(response=response), self.assertRaises(InvalidProjectResponse):
                score_project(SNIPPET, self.job, Mock(return_value=response))

    def test_client_exception_propagates_to_the_scoring_boundary(self):
        client = Mock(side_effect=RuntimeError("test client unavailable"))
        with self.assertRaises(RuntimeError):
            score_project(SNIPPET, self.job, client)

    def test_screen_one_preserves_jd1_on_project_adapter_failure(self):
        self.require_pymupdf()
        for client in (Mock(side_effect=RuntimeError("test details")), self.client_with(7, " ")):
            with self.subTest(client=client):
                result = screen_one(SAMPLE, project_scorer=lambda _text, _profile, job: (
                    score_project(SNIPPET, job, client)
                ))
                self.assertEqual(result.route.jd_id, "JD1")
                self.assertEqual(
                    (result.score.status, result.score.jd_id, result.score.reason_code, result.score.total),
                    ("REVIEW", "JD1", "PROJECT_SCORER_ERROR", -1),
                )
                self.assertIsNone(result.score.scores)
                client.assert_called_once_with(SNIPPET, self.job.raw_text)

    def test_screen_one_accepts_adapter_score_with_curated_excerpt(self):
        self.require_pymupdf()
        client = self.client_with(7, "订单接口")

        def scorer(_text, _profile, job):
            return score_project(SNIPPET, job, client)

        result = screen_one(SAMPLE, project_scorer=scorer)
        self.assertEqual((result.score.jd_id, result.score.status, result.score.total),
                         ("JD1", "SCORED", 67))
        self.assertEqual((result.score.scores.project, result.score.project_source), (7, "injected"))
        client.assert_called_once_with(SNIPPET, self.job.raw_text)

    def test_screen_one_keeps_mock_default_and_skips_callback_on_early_results(self):
        self.require_pymupdf()
        default = screen_one(SAMPLE)
        self.assertEqual((default.score.jd_id, default.score.status, default.score.total),
                         ("JD1", "SCORED", 60))
        self.assertEqual((default.score.scores.project, default.score.project_source), (0, "mock"))
        scorer = Mock()
        blocked = screen_one(RESUMES / "S002_empty.pdf", project_scorer=scorer)
        self.assertEqual((blocked.score.reason_code, blocked.score.total), ("EMPTY", -1))
        unmatched = screen_one(SAMPLE, jobs=[], project_scorer=scorer)
        self.assertEqual((unmatched.score.jd_id, unmatched.score.status, unmatched.score.total),
                         ("UNMATCHED", "SCORED", 0))
        scorer.assert_not_called()
