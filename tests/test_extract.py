import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.extract import (
    clean_text,
    extract_pdf,
    extract_profile,
    extract_years,
    guess_name,
)


class ExtractTests(unittest.TestCase):
    def test_clean_text_removes_extra_spaces_and_empty_lines(self):
        self.assertEqual(clean_text(" 张三  \n\n Python   FastAPI "), "张三\nPython FastAPI")

    def test_guess_name_uses_text_before_filename(self):
        self.assertEqual(guess_name("张三，后端开发\n本科", "resume_李四.pdf"), "张三")

    def test_guess_name_falls_back_to_filename(self):
        self.assertEqual(guess_name("Python Developer", "resume_李四.pdf"), "李四")

    def test_profile_extracts_all_matching_fields(self):
        profile = extract_profile(
            "姓名：张三\n后端开发 5 年\n本科\nPython FastAPI MySQL",
            "resume_李四.pdf",
        )

        self.assertEqual(profile.name, "张三")
        self.assertEqual(profile.years, 5.0)
        self.assertEqual(profile.education, "本科")
        self.assertEqual(profile.edu_score, 7)
        self.assertEqual(profile.skills, ["Python", "FastAPI", "MySQL"])

    def test_explicit_years_take_priority_over_date_ranges(self):
        self.assertEqual(
            extract_years("后端开发 5 年，工作经历 2021.07—至今"),
            5.0,
        )

    def test_date_range_to_present_calculates_years(self):
        expected = round((date.today().year - 2021) + (date.today().month - 7) / 12, 1)
        self.assertEqual(extract_years("工作经历：2021.07—至今"), expected)

    def test_education_dates_are_excluded_from_years(self):
        text = (
            "教育背景：\n2019-2023 某某大学 本科\n"
            "工作经历：\n2025.03-至今 某公司 开发"
        )
        expected = round((date.today().year - 2025) + (date.today().month - 3) / 12, 1)
        self.assertEqual(extract_years(text), expected)

    def test_contiguous_work_ranges_are_unioned(self):
        text = "工作经历：\n2018.01-2020.01 公司A\n2020.01-2024.01 公司B"
        self.assertEqual(extract_years(text), 6.0)

    def test_education_years_and_work_years_are_distinguished(self):
        text = "教育背景：4年制本科\n工作经历：2年开发经验"
        self.assertEqual(extract_years(text), 2.0)

    def test_birth_year_is_not_work_experience(self):
        text = "张三，男，2001年生\n求职意向：后端"
        self.assertEqual(extract_years(text), 0.0)

    def test_education_mapping_and_duplicate_skills(self):
        profile = extract_profile("硕士\nPython python\nFastAPI FASTAPI\nRedis")

        self.assertEqual(profile.education, "硕士")
        self.assertEqual(profile.edu_score, 9)
        self.assertEqual(profile.skills, ["Python", "FastAPI", "Redis"])

    def test_skill_matching_does_not_match_substrings(self):
        profile = extract_profile("Django、Google Analytics、good communication")

        self.assertIn("Django", profile.skills)
        self.assertNotIn("Go", profile.skills)

    def test_empty_profile_returns_stable_defaults(self):
        profile = extract_profile("")

        self.assertEqual(profile.name, "")
        self.assertEqual(profile.years, 0.0)
        self.assertEqual(profile.education, "未知")
        self.assertEqual(profile.edu_score, 0)
        self.assertEqual(profile.skills, [])

    def test_titles_are_not_names(self):
        self.assertEqual(guess_name("后端开发\nPython", ""), "")
        self.assertEqual(guess_name("张三 后端开发", ""), "张三")

    def test_negated_skills_are_not_extracted(self):
        profile = extract_profile("完全没写过 Python，没用过 Docker、Nginx")

        self.assertNotIn("Python", profile.skills)
        self.assertNotIn("Docker", profile.skills)
        self.assertNotIn("Nginx", profile.skills)

    def test_profile_uses_filename_for_missing_name(self):
        profile = extract_profile("Python Developer\n本科", "resume_李四.pdf")
        self.assertEqual(profile.name, "李四")

    def test_missing_pdf_returns_structured_error(self):
        result = extract_pdf("missing-resume.pdf")
        self.assertEqual(result.status, "ERROR")
        self.assertFalse(result.ok)

    def test_extracts_text_from_pdf(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF 未安装，跳过真实 PDF 提取测试")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resume.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), "张三\nPython FastAPI")
            document.save(path)
            document.close()

            result = extract_pdf(path)

        self.assertTrue(result.ok)
        self.assertIn("Python FastAPI", result.text)


if __name__ == "__main__":
    unittest.main()
