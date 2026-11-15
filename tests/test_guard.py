import json
import unittest
from pathlib import Path

from app.extract import ExtractResult, extract_pdf, extract_profile
from app.guard import guard_text


class GuardTests(unittest.TestCase):
    def test_empty_extract_is_manual_review(self):
        result = guard_text(ExtractResult("", "EMPTY", "没有文字层"))

        self.assertFalse(result.passed)
        self.assertEqual(result.status, "REVIEW")
        self.assertEqual(result.reason_code, "EMPTY")
        self.assertEqual(result.score, -1)

    def test_extract_error_is_manual_review(self):
        result = guard_text(ExtractResult("", "ERROR", "文件不存在：x"))

        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, "EXTRACT_ERROR")
        self.assertEqual(result.score, -1)
        self.assertIn("文件不存在", result.reason)

    def test_missing_dependency_is_manual_review(self):
        result = guard_text(ExtractResult("", "DEPENDENCY_MISSING", "fitz"))

        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, "DEPENDENCY_MISSING")
        self.assertEqual(result.score, -1)

    def test_empty_object_is_structured_review(self):
        result = guard_text(None)

        self.assertFalse(result.passed)
        self.assertEqual(result.status, "REVIEW")
        self.assertEqual(result.reason_code, "EXTRACT_ERROR")
        self.assertEqual(result.score, -1)

    def test_low_information_fragment_is_suspect_scan_or_corrupt(self):
        fragment = ExtractResult("\ufffd" * 70 + "@@@###***", "OK")

        result = guard_text(fragment)

        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, "SUSPECT_SCAN_OR_CORRUPT")
        self.assertNotEqual(result.reason_code, "TOO_SHORT")
        self.assertEqual(result.score, -1)

    def test_short_realistic_text_is_too_short(self):
        short_text = "张三有后端开发经验，熟悉接口设计、数据库和服务维护，能够完成日常开发与问题排查。"
        result = guard_text(ExtractResult(short_text, "OK"))

        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, "TOO_SHORT")
        self.assertIn(str(len(short_text)), result.reason)
        self.assertIn("200", result.reason)
        self.assertEqual(result.score, -1)

    def test_normal_resume_passes_without_a_score(self):
        text = _normal_resume_text()
        result = guard_text(ExtractResult(text, "OK"))

        self.assertTrue(result.passed)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.reason_code, "")
        self.assertIsNone(result.score)
        self.assertGreaterEqual(result.text_len, 200)
        self.assertGreaterEqual(result.han_count, 50)

    def test_long_entry_level_resume_with_zero_years_passes(self):
        text = (
            "姓名：林澄\n教育背景：2020-2024 星河大学 本科\n"
            "项目经历：校园服务平台，使用 Python、FastAPI 和 MySQL 完成课程项目。\n"
            "负责需求整理、接口设计、数据表设计、测试编写和部署说明。\n"
        ) * 3

        result = guard_text(ExtractResult(text, "OK"))

        self.assertTrue(result.passed)
        self.assertIsNone(result.score)

    def test_four_bad_categories_are_represented(self):
        results = [
            guard_text(ExtractResult("", "EMPTY")),
            guard_text(ExtractResult("\ufffd" * 70 + "@@@", "OK")),
            guard_text(ExtractResult("", "ERROR", "损坏")),
            guard_text(ExtractResult("张三熟悉后端开发和数据库。", "OK")),
        ]
        codes = {result.reason_code for result in results}

        self.assertEqual(
            codes,
            {"EMPTY", "SUSPECT_SCAN_OR_CORRUPT", "EXTRACT_ERROR", "TOO_SHORT"},
        )
        self.assertTrue(all(result.score == -1 for result in results))

    def test_synthetic_pdf_manifest_matches_guard(self):
        root = Path(__file__).resolve().parents[1] / "data" / "synthetic"
        manifest_path = root / "manifest.jsonl"
        records = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()]

        self.assertGreaterEqual(len(records), 8)
        for record in records:
            result = extract_pdf(root / record["file"])
            guarded = guard_text(result)
            self.assertEqual(result.status, record["extract_status"], record["id"])
            self.assertEqual(guarded.status, record["guard_status"], record["id"])
            self.assertEqual(guarded.reason_code, record["reason_code"], record["id"])

    def test_synthetic_profile_sources_match_manifest(self):
        root = Path(__file__).resolve().parents[1] / "data" / "synthetic"
        manifest_path = root / "manifest.jsonl"
        records = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()]

        profile_records = [record for record in records if "profile_text_file" in record]
        self.assertGreater(len(profile_records), 0)
        for record in profile_records:
            profile_text = (root / record["profile_text_file"]).read_text(encoding="utf-8")
            profile = extract_profile(profile_text)
            self.assertEqual(profile.name, record["name"], record["id"])
            self.assertEqual(profile.years, record["years"], record["id"])
            self.assertEqual(profile.education, record["education"], record["id"])
            self.assertEqual(profile.skills, record["skills"], record["id"])

    def test_chinese_scan_prompt_is_currently_too_short(self):
        result = guard_text(ExtractResult("请使用OCR识别该扫描件", "OK"))

        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, "TOO_SHORT")
        self.assertIn("200", result.reason)


def _normal_resume_text():
    return (
        "姓名：林澄\n求职意向：后端开发工程师\n"
        "工作经历：2021.01-2023.01 星河科技 后端开发工程师\n"
        "负责用户服务、订单服务和权限模块的设计与开发，使用 Python、FastAPI、MySQL、Redis、"
        "Docker 和 Linux 完成接口开发、数据处理、日志监控和线上问题排查。参与需求评审、技术方案设计、"
        "代码审查、自动化测试和发布流程建设，能够独立定位接口错误并编写清晰的开发文档。\n"
        "教育背景：2017-2021 星河大学 本科。"
    )


if __name__ == "__main__":
    unittest.main()
