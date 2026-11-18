"""Exercise the single-PDF entry point with real fixtures and failure cases."""

from pathlib import Path
import unittest
from unittest.mock import DEFAULT, Mock, patch

from app.extract import ExtractResult, extract_pdf
from app.guard import guard_text
from app.route import load_jobs
from app.score import ScoreBreakdown, ScoreResult
from app.screen import screen_one


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"
NORMAL_PDF = RESUMES / "S001_normal_backend.pdf"


class ScreenTests(unittest.TestCase):
    def require_fitz(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is not installed")

    def assert_early_review(self, result, code):
        self.assertIsNone(result.profile)
        self.assertIsNone(result.route)
        self.assertEqual(
            (result.score.status, result.score.total, result.score.jd_id, result.score.scores),
            ("REVIEW", -1, "", None),
        )
        self.assertEqual(result.score.reason_code, code)

    def test_normal_pdf_uses_default_jobs_and_scores_60(self):
        self.require_fitz()
        result = screen_one(str(NORMAL_PDF))
        self.assertEqual((result.extract_status, result.guard_status), ("OK", "PASS"))
        self.assertEqual((result.profile.years, result.profile.edu_score), (6.0, 0))
        self.assertEqual(result.route.jd_id, "JD1")
        self.assertEqual((result.score.jd_id, result.score.status, result.score.total),
                         ("JD1", "SCORED", 60))
        self.assertEqual(result.score.scores, ScoreBreakdown(40, 0, 15, 0, 5))
        self.assertEqual(result.score.project_source, "mock")

    def test_real_guard_rejections_skip_all_later_stages(self):
        self.require_fitz()
        for name, extract_status, code in (
            ("S002_empty.pdf", "EMPTY", "EMPTY"),
            ("S003_image_only.pdf", "EMPTY", "EMPTY"),
            ("S004_scan_fragment.pdf", "OK", "SUSPECT_SCAN_OR_CORRUPT"),
            ("S005_corrupt.pdf", "ERROR", "EXTRACT_ERROR"),
            ("S006_short_text.pdf", "OK", "TOO_SHORT"),
            ("missing_fixture.pdf", "ERROR", "EXTRACT_ERROR"),
        ):
            with self.subTest(name=name), patch.multiple(
                "app.screen", extract_profile=DEFAULT, load_jobs=DEFAULT,
                route_profile=DEFAULT, score_candidate=DEFAULT,
            ) as later:
                later["load_jobs"].side_effect = OSError("default configuration unavailable")
                scorer = Mock()
                result = screen_one(RESUMES / name, project_scorer=scorer)
                self.assertEqual((result.extract_status, result.guard_status),
                                 (extract_status, "REVIEW"))
                self.assert_early_review(result, code)
                for stage in later.values():
                    stage.assert_not_called()
                scorer.assert_not_called()
                if code == "EXTRACT_ERROR":
                    self.assertEqual(result.score.reason, "PDF 读取失败，请人工复核")

    def test_extract_error_does_not_expose_local_path_or_error_details(self):
        location = "/private-folder/synthetic.pdf"
        extraction = ExtractResult("", "ERROR", f"could not open {location}")
        with patch("app.screen.extract_pdf", return_value=extraction):
            result = screen_one(NORMAL_PDF)
        self.assert_early_review(result, "EXTRACT_ERROR")
        self.assertEqual(result.score.reason, "PDF 读取失败，请人工复核")
        self.assertNotIn(location, result.score.reason)

    def test_missing_dependency_preserves_guard_reason(self):
        extraction = ExtractResult("", "DEPENDENCY_MISSING", "fitz")
        with patch("app.screen.extract_pdf", return_value=extraction), patch(
            "app.screen.load_jobs"
        ) as loader:
            result = screen_one(NORMAL_PDF)
        self.assertEqual((result.extract_status, result.guard_status),
                         ("DEPENDENCY_MISSING", "REVIEW"))
        self.assert_early_review(result, "DEPENDENCY_MISSING")
        self.assertEqual(result.score.reason, guard_text(extraction).reason)
        loader.assert_not_called()

    def test_empty_jobs_keeps_education_points_with_chinese_text(self):
        source = RESUMES / "S001_normal_backend.txt"
        extraction = ExtractResult(source.read_text(encoding="utf-8"), "OK")
        with patch("app.screen.extract_pdf", return_value=extraction), patch(
            "app.screen.load_jobs"
        ) as loader:
            scorer = Mock()
            result = screen_one(NORMAL_PDF, [], project_scorer=scorer)
        self.assert_unmatched(result, 7)
        loader.assert_not_called()
        scorer.assert_not_called()

    def test_empty_jobs_allows_zero_for_real_pdf(self):
        self.require_fitz()
        with patch("app.screen.load_jobs") as loader:
            scorer = Mock()
            result = screen_one(NORMAL_PDF, [], project_scorer=scorer)
        self.assert_unmatched(result, 0)
        loader.assert_not_called()
        scorer.assert_not_called()

    def assert_unmatched(self, result, education):
        self.assertEqual(result.guard_status, "PASS")
        self.assertEqual(result.route.jd_id, "UNMATCHED")
        self.assertEqual((result.score.jd_id, result.score.status, result.score.total),
                         ("UNMATCHED", "SCORED", education))
        self.assertEqual(result.score.scores, ScoreBreakdown(0, 0, 0, education, 0))

    def test_default_job_loading_failures_return_public_review(self):
        self.require_fitz()
        for error in (OSError("private path"), ValueError("internal JSON details")):
            with self.subTest(error=type(error).__name__), patch.multiple(
                "app.screen", load_jobs=DEFAULT, extract_profile=DEFAULT,
                route_profile=DEFAULT, score_candidate=DEFAULT,
            ) as stages:
                stages["load_jobs"].side_effect = error
                scorer = Mock()
                result = screen_one(NORMAL_PDF, project_scorer=scorer)
                self.assertEqual((result.extract_status, result.guard_status), ("OK", "PASS"))
                self.assert_early_review(result, "JOB_CONFIG_ERROR")
                self.assertEqual(result.score.reason, "岗位配置加载失败，请人工复核")
                stages["load_jobs"].assert_called_once_with()
                stages["extract_profile"].assert_not_called()
                stages["route_profile"].assert_not_called()
                stages["score_candidate"].assert_not_called()
                scorer.assert_not_called()

    def test_project_scorer_gets_extracted_text_profile_and_matched_job(self):
        self.require_fitz()
        scorer = Mock(return_value=10)
        jobs = load_jobs()
        result = screen_one(NORMAL_PDF, jobs, project_scorer=scorer)
        self.assertEqual((result.score.status, result.score.total, result.score.project_source),
                         ("SCORED", 70, "injected"))
        self.assertEqual(result.score.scores.project, 10)
        jd1 = next(job for job in jobs if job.jd_id == "JD1")
        scorer.assert_called_once_with(extract_pdf(NORMAL_PDF).text, result.profile, jd1)
        self.assertIs(scorer.call_args.args[1], result.profile)
        self.assertIs(scorer.call_args.args[2], jd1)

    def test_project_scorer_exception_preserves_job_and_review_reason(self):
        self.require_fitz()
        scorer = Mock(side_effect=RuntimeError("private scorer details"))
        result = screen_one(NORMAL_PDF, project_scorer=scorer)
        self.assertIsNotNone(result.profile)
        self.assertEqual(result.route.jd_id, "JD1")
        self.assertEqual(
            (result.score.status, result.score.jd_id, result.score.reason_code, result.score.total),
            ("REVIEW", "JD1", "PROJECT_SCORER_ERROR", -1),
        )
        self.assertIsNone(result.score.scores)
        self.assertNotIn("private scorer details", result.score.reason)
        scorer.assert_called_once()

    def test_entry_uses_verified_score_and_keeps_real_route(self):
        self.require_fitz()
        wrong_score = ScoreResult("SCORED", "JD2", 60, ScoreBreakdown(40, 0, 15, 0, 5))
        with patch("app.screen.score_candidate", return_value=wrong_score):
            result = screen_one(NORMAL_PDF)
        self.assertIsNotNone(result.profile)
        self.assertEqual(result.route.jd_id, "JD1")
        self.assertEqual(
            (result.score.status, result.score.total, result.score.jd_id, result.score.reason_code),
            ("REVIEW", -1, "", "SCORE_ROUTE_MISMATCH"),
        )
