"""Regression tests for evaluation data, reporting, and CLI exit codes."""

from contextlib import redirect_stderr, redirect_stdout
import csv
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from eval import run_eval


class EvalTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.manifest = self.directory / "manifest.csv"
        self.output = self.directory / "reports" / "report.json"
        with run_eval.DEFAULT_MANIFEST.open(encoding="utf-8", newline="") as source:
            self.rows = list(csv.reader(source))

    def run_cli(self, manifest=None, output=None):
        stderr = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(stderr):
            code = run_eval.main([
                "--manifest", str(manifest or run_eval.DEFAULT_MANIFEST),
                "--output", str(output or self.output),
            ])
        return code, stderr.getvalue()

    def write_manifest(self, rows):
        with self.manifest.open("w", encoding="utf-8", newline="") as target:
            csv.writer(target).writerows(rows)

    def read_report(self):
        return json.loads(self.output.read_text(encoding="utf-8"))

    def require_pymupdf(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is not installed")

    def test_real_pdf_baseline_matches_all_labels(self):
        self.require_pymupdf()
        self.assertEqual(self.run_cli(), (0, ""))
        report = self.read_report()
        self.assertEqual((report["mode"], report["pdf_count"], report["case_count"]), ("mock", 10, 13))
        self.assertEqual((report["passed"], report["failed"]), (13, 0))
        self.assertEqual(report["score_status_counts"], {"SCORED": 6, "REVIEW": 7})
        self.assertEqual(report["manifest_sha256"], sha256(run_eval.DEFAULT_MANIFEST.read_bytes()).hexdigest())
        by_id = {case["case_id"]: case for case in report["cases"]}
        self.assertEqual(by_id["S001"]["actual"]["total"], 60)
        for case_id, status, job_id, total in (
            ("S001-empty-jobs", "SCORED", "UNMATCHED", 0),
            ("S001-project-none", "REVIEW", "JD1", -1),
            ("S001-project-31", "REVIEW", "JD1", -1),
            ("S005", "REVIEW", "", -1),
        ):
            with self.subTest(case_id=case_id):
                case = by_id[case_id]
                actual = case["actual"]
                self.assertTrue(case["passed"])
                self.assertEqual((actual["score_status"], actual["score_jd_id"], actual["total"]),
                                 (status, job_id, total))

    def test_wrong_label_returns_one_and_reports_the_field(self):
        self.require_pymupdf()
        self.rows[1][self.rows[0].index("total")] = "59"
        self.write_manifest(self.rows)
        self.assertEqual(self.run_cli(self.manifest), (1, ""))
        report = self.read_report()
        self.assertEqual((report["passed"], report["failed"]), (12, 1))
        case = report["cases"][0]
        self.assertEqual(case["case_id"], "S001")
        self.assertEqual(case["mismatches"], {"total": {"expected": 59, "actual": 60}})

    def test_bad_manifest_fails_before_any_pdf_runs(self):
        header, row = self.rows[:2]
        bad_inputs = [
            ([], "header"),
            ([header], "no evaluation cases"),
            ([header, row, row], "duplicate case_id"),
            ([header, row[:-1]], "column count"),
        ]
        for field, value, message in (
            ("scenario", "unexpected", "unknown scenario"),
            ("extract_status", "unexpected", "invalid extract_status"),
            ("total", "60.0", "total must be an integer"),
            ("bonus", "6", "bonus is out of range"),
            ("pdf_path", str(run_eval.RESUMES / "S001_normal_backend.pdf"), "must be relative"),
            ("pdf_path", "../outside.pdf", "must be relative"),
            ("pdf_path", "data/synthetic/other.pdf", "must point to a synthetic PDF"),
            ("pdf_path", "data/synthetic/resumes/missing.pdf", "PDF is missing"),
        ):
            invalid = row[:]
            invalid[header.index(field)] = value
            bad_inputs.append(([header, invalid], message))

        with patch("eval.run_eval.screen_one") as screen:
            for contents, message in bad_inputs:
                with self.subTest(message=message):
                    self.write_manifest(contents)
                    code, stderr = self.run_cli(self.manifest)
                    self.assertEqual(code, 2)
                    self.assertIn(message, stderr)
                    self.assertFalse(self.output.exists())
            screen.assert_not_called()

    def test_unexpected_case_exception_does_not_stop_later_cases(self):
        self.require_pymupdf()
        original = run_eval.screen_one

        def fail_first(pdf, **kwargs):
            if pdf.name == "S001_normal_backend.pdf" and not kwargs:
                raise RuntimeError("private exception details")
            return original(pdf, **kwargs)

        with patch("eval.run_eval.screen_one", side_effect=fail_first):
            self.assertEqual(self.run_cli(), (1, ""))
        report = self.read_report()
        self.assertEqual(report["passed"], 12)
        self.assertEqual(report["cases"][0]["error_type"], "RuntimeError")
        self.assertTrue(report["cases"][-1]["passed"])
        self.assertNotIn("private exception details", json.dumps(report))

    def test_missing_pymupdf_is_an_input_error(self):
        with patch.dict(sys.modules, {"fitz": None}), patch("eval.run_eval.screen_one") as screen:
            code, stderr = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("PyMuPDF is required", stderr)
        self.assertFalse(self.output.exists())
        screen.assert_not_called()

    def test_report_write_error_returns_two(self):
        self.require_pymupdf()
        code, stderr = self.run_cli(output=self.directory)
        self.assertEqual(code, 2)
        self.assertIn("Cannot write the evaluation report", stderr)
