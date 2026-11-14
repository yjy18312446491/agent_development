import tempfile
import unittest
from pathlib import Path

from app.extract import clean_text, extract_pdf, guess_name


class ExtractTests(unittest.TestCase):
    def test_clean_text_removes_extra_spaces_and_empty_lines(self):
        self.assertEqual(clean_text(" 张三  \n\n Python   FastAPI "), "张三\nPython FastAPI")

    def test_guess_name_uses_text_before_filename(self):
        self.assertEqual(guess_name("张三，后端开发\n本科", "resume_李四.pdf"), "张三")

    def test_guess_name_falls_back_to_filename(self):
        self.assertEqual(guess_name("Python Developer", "resume_李四.pdf"), "李四")

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
