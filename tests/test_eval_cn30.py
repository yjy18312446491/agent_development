"""Regression tests for the Chinese 30-case route-quality evaluation."""

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

from eval import run_eval_cn30


class EvalCn30Tests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.manifest = self.directory / "manifest_cn30.csv"
        self.output = self.directory / "reports" / "report.json"
        with run_eval_cn30.DEFAULT_MANIFEST.open(encoding="utf-8", newline="") as source:
            self.rows = list(csv.reader(source))
        self.header = self.rows[0]

    def run_cli(self, manifest=None, output=None):
        stderr = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(stderr):
            code = run_eval_cn30.main([
                "--manifest", str(manifest or run_eval_cn30.DEFAULT_MANIFEST),
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

    def test_real_cn30_baseline_and_quality_views(self):
        self.require_pymupdf()
        self.assertEqual(self.run_cli(), (0, ""))
        report = self.read_report()
        self.assertEqual(
            (report["mode"], report["pdf_count"], report["case_count"]),
            ("mock-cn30", 30, 30),
        )
        self.assertEqual((report["passed"], report["failed"]), (30, 0))
        self.assertEqual(
            report["manifest_sha256"],
            sha256(run_eval_cn30.DEFAULT_MANIFEST.read_bytes()).hexdigest(),
        )

        quality = report["quality"]
        self.assertEqual(quality["route_accuracy"], {"routed": 22, "correct": 19, "rate": 0.8636})
        self.assertEqual(
            quality["abnormal_interception"],
            {"expected": 4, "intercepted": 4, "rate": 1.0},
        )
        self.assertEqual(
            quality["confusion_matrix"]["counts"],
            {
                "JD1": {"JD1": 9, "JD2": 0, "UNMATCHED": 0},
                "JD2": {"JD1": 1, "JD2": 8, "UNMATCHED": 1},
                "UNMATCHED": {"JD1": 0, "JD2": 1, "UNMATCHED": 2},
            },
        )
        self.assertEqual(
            [case["case_id"] for case in quality["mismatches"]],
            ["R13", "R18", "R27"],
        )
        self.assertEqual(
            [case["case_id"] for case in quality["gated_cases"]],
            ["R23", "R24", "R25", "R26"],
        )
        groups = quality["group_accuracy"]
        self.assertEqual(groups["A"]["route_correct"], 7)
        self.assertEqual(groups["B"]["route_correct"], 7)
        self.assertEqual((groups["C"]["route_correct"], groups["C"]["routed"]), (4, 6))
        self.assertEqual((groups["D"]["intercepted"], groups["D"]["total"]), (4, 4))
        self.assertEqual((groups["E"]["route_correct"], groups["E"]["routed"], groups["E"]["gated"]), (1, 2, 4))

        by_id = {case["case_id"]: case for case in report["cases"]}
        self.assertEqual(by_id["R01"]["actual"]["route_id"], "JD1")
        self.assertEqual(by_id["R01"]["actual"]["tier"], "B")
        self.assertEqual(by_id["R19"]["actual"]["reason_code"], "EMPTY")
        mismatch = quality["mismatches"][0]
        self.assertEqual((mismatch["expected_route"], mismatch["actual_route"]), ("JD2", "JD1"))
        self.assertIn("route_reason", mismatch)

    def test_wrong_baseline_field_returns_one_and_reports_it(self):
        self.require_pymupdf()
        self.rows[1][self.header.index("baseline_total")] = "64"
        self.write_manifest(self.rows)
        self.assertEqual(self.run_cli(self.manifest), (1, ""))
        report = self.read_report()
        self.assertEqual((report["passed"], report["failed"]), (29, 1))
        case = report["cases"][0]
        self.assertEqual(case["case_id"], "R01")
        self.assertEqual(case["mismatches"], {"total": {"baseline": 64, "actual": 65}})

    def test_bad_manifest_fails_before_any_pdf_runs(self):
        header, row = self.rows[:2]
        bad_inputs = [
            ([], "header"),
            ([header], "no evaluation cases"),
            ([header, row, row], "duplicate case_id"),
            ([header, row[:-1]], "column count"),
        ]
        for field, value, message in (
            ("group", "F", "unknown group"),
            ("expected_route", "JD3", "invalid expected_route"),
            ("baseline_route", "JD3", "invalid baseline_route"),
            ("baseline_score_status", "ERROR", "invalid baseline_score_status"),
            ("baseline_total", "65.0", "baseline_total must be an integer"),
            ("baseline_total", "101", "baseline_total is out of range"),
            ("pdf_path", str(run_eval_cn30.RESUMES_CN / "R01.pdf"), "must be relative"),
            ("pdf_path", "../outside.pdf", "must be relative"),
            ("pdf_path", "data/synthetic/other.pdf", "must point to a Chinese synthetic PDF"),
            ("pdf_path", "data/synthetic/resumes_cn30/missing.pdf", "PDF is missing"),
        ):
            invalid = row[:]
            invalid[header.index(field)] = value
            bad_inputs.append(([header, invalid], message))

        review_row = self.rows[19]  # R19 is a REVIEW case.
        for field, value, message in (
            ("baseline_route", "JD1", "REVIEW requires a reason_code and a blank baseline route"),
            ("baseline_reason", "", "REVIEW requires a reason_code and a blank baseline route"),
        ):
            invalid = review_row[:]
            invalid[header.index(field)] = value
            bad_inputs.append(([header, invalid], message))

        scored_row = self.rows[1]  # R01 is a SCORED case.
        for field, value, message in (
            ("baseline_reason", "EMPTY", "SCORED requires a baseline route and a blank reason_code"),
            ("baseline_route", "", "SCORED requires a baseline route and a blank reason_code"),
        ):
            invalid = scored_row[:]
            invalid[header.index(field)] = value
            bad_inputs.append(([header, invalid], message))

        d_row = self.rows[19]
        invalid = d_row[:]
        invalid[header.index("expected_route")] = "JD1"
        bad_inputs.append(([header, invalid], "group D requires the INTERCEPT expected route"))

        e_row = self.rows[23]  # R23 is group E.
        invalid = e_row[:]
        invalid[header.index("expected_route")] = "INTERCEPT"
        bad_inputs.append(([header, invalid], "only group D may expect INTERCEPT"))

        with patch("eval.run_eval_cn30.screen_one") as screen:
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
        original = run_eval_cn30.screen_one

        def fail_first(pdf, **kwargs):
            if pdf.name == "R01.pdf":
                raise RuntimeError("private exception details")
            return original(pdf, **kwargs)

        with patch("eval.run_eval_cn30.screen_one", side_effect=fail_first):
            self.assertEqual(self.run_cli(), (1, ""))
        report = self.read_report()
        self.assertEqual(report["passed"], 29)
        self.assertEqual(report["cases"][0]["error_type"], "RuntimeError")
        self.assertTrue(report["cases"][-1]["passed"])
        self.assertNotIn("private exception details", json.dumps(report))
        quality = report["quality"]
        self.assertEqual(quality["route_accuracy"]["routed"], 21)

    def test_missing_pymupdf_is_an_input_error(self):
        with patch.dict(sys.modules, {"fitz": None}), patch("eval.run_eval_cn30.screen_one") as screen:
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


if __name__ == "__main__":
    unittest.main()
