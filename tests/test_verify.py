"""Regression tests for score verification."""

from dataclasses import replace
from pathlib import Path
import unittest

from app.extract import ExtractResult, extract_profile
from app.guard import guard_text
from app.route import RouteResult, load_jobs, route_profile
from app.score import ScoreBreakdown, ScoreResult, score_candidate
from app.verify import verify_score


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.route = RouteResult("JD2", "后端开发", 0.67, 1.0, 0.87, "")
        self.points = ScoreBreakdown(27, 0, 15, 7, 3)
        self.scored = ScoreResult(
            "SCORED", "JD2", 52, self.points,
            skill_hits=["Python"], skill_missing=["Flask"], project_source="mock",
        )
        self.unmatched = replace(self.route, jd_id="UNMATCHED")
        self.upstream_review = ScoreResult(
            "REVIEW", "JD1", -1, None, reason="找不到岗位", reason_code="JOB_NOT_FOUND"
        )

    def assert_verification_review(self, result, code):
        self.assertEqual((result.status, result.total, result.scores, result.jd_id),
                         ("REVIEW", -1, None, ""))
        self.assertEqual(result.reason_code, code)
        self.assertTrue(result.reason)
        self.assertEqual((result.skill_hits, result.skill_missing, result.project_source),
                         ([], [], ""))

    def test_s001_passes_with_jd2_and_total_52(self):
        text = (RESUMES / "S001_normal_backend.txt").read_text(encoding="utf-8")
        profile = extract_profile(text)
        guard = guard_text(ExtractResult(text, "OK"))
        jobs = load_jobs()
        route = route_profile(text, profile, jobs)
        scored = score_candidate(text, profile, guard, route, jobs)

        self.assertEqual((guard.status, scored.status, scored.jd_id, scored.total),
                         ("PASS", "SCORED", "JD2", 52))
        self.assertEqual(scored.scores, self.points)
        self.assertIs(verify_score(scored, route), scored)

    def test_unmatched_keeps_education_including_zero(self):
        for education in (7, 0):
            with self.subTest(education=education):
                scored = ScoreResult(
                    "SCORED", "UNMATCHED", education, ScoreBreakdown(0, 0, 0, education, 0)
                )
                self.assertIs(verify_score(scored, self.unmatched), scored)

    def test_guard_review_passes_without_route(self):
        review = ScoreResult(
            "REVIEW", "", -1, None, reason="简历内容过短", reason_code="TOO_SHORT"
        )
        self.assertIs(verify_score(review), review)

    def test_job_not_found_review_keeps_the_original_job_and_reason(self):
        for route in (None, self.route):
            with self.subTest(route=route):
                verified = verify_score(self.upstream_review, route)
                self.assertIs(verified, self.upstream_review)
                self.assertEqual((verified.jd_id, verified.reason_code), ("JD1", "JOB_NOT_FOUND"))

    def test_invalid_upstream_review_gets_a_new_review(self):
        for changes in (
            {"scores": self.points},
            {"total": 0},
            {"total": -1.0},
            {"total": True},
            {"reason_code": ""},
            {"reason_code": " "},
            {"reason_code": None},
        ):
            with self.subTest(changes=changes):
                review = replace(self.upstream_review, **changes)
                self.assert_verification_review(verify_score(review), "INVALID_SCORE_RESULT")

    def test_missing_result_and_unknown_status_are_invalid(self):
        for invalid in (None, replace(self.scored, status="FOO")):
            with self.subTest(result=invalid):
                self.assert_verification_review(
                    verify_score(invalid, self.route), "INVALID_SCORE_RESULT"
                )

    def test_invalid_breakdown_and_total_are_rejected(self):
        for changes in (
            {"scores": None},
            {"scores": (27, 0, 15, 7, 3)},
            {"total": 51},
            {"total": -1},
            {"total": 52.0},
            {"total": True},
        ):
            with self.subTest(changes=changes):
                scored = replace(self.scored, **changes)
                self.assert_verification_review(
                    verify_score(scored, self.route), "INVALID_SCORE_RESULT"
                )

    def test_each_subscore_checks_its_type_and_range_before_the_sum(self):
        for field, value in (
            ("skill", 41), ("project", 31), ("years", 16), ("years", -1),
            ("education", 11), ("bonus", 6), ("project", True), ("years", 12.0),
        ):
            with self.subTest(field=field, value=value):
                scores = replace(self.points, **{field: value})
                scored = replace(self.scored, scores=scores, total=int(scores.total))
                self.assert_verification_review(
                    verify_score(scored, self.route), "INVALID_SCORE_RESULT"
                )

    def test_full_score_is_valid(self):
        scored = replace(self.scored, total=100, scores=ScoreBreakdown(40, 30, 15, 10, 5))
        self.assertIs(verify_score(scored, self.route), scored)

    def test_scored_result_requires_matching_nonempty_job_ids(self):
        for result_id, route in (
            ("JD2", None), ("JD1", self.route), ("", self.route),
            ("JD2", replace(self.route, jd_id="")),
            (" ", replace(self.route, jd_id=" ")),
        ):
            with self.subTest(result_id=result_id, route=route):
                scored = replace(self.scored, jd_id=result_id)
                self.assert_verification_review(
                    verify_score(scored, route), "SCORE_ROUTE_MISMATCH"
                )

    def test_unmatched_rejects_nonzero_non_education_scores(self):
        for field in ("skill", "project", "years", "bonus"):
            with self.subTest(field=field):
                scores = replace(ScoreBreakdown(0, 0, 0, 7, 0), **{field: 5})
                scored = ScoreResult("SCORED", "UNMATCHED", 12, scores)
                self.assert_verification_review(
                    verify_score(scored, self.unmatched), "INVALID_UNMATCHED_SCORE"
                )

    def test_validation_checks_structure_then_route_then_unmatched(self):
        wrong_unmatched = ScoreResult("SCORED", "UNMATCHED", 12, ScoreBreakdown(5, 0, 0, 7, 0))
        self.assert_verification_review(
            verify_score(replace(wrong_unmatched, total=11)), "INVALID_SCORE_RESULT"
        )
        self.assert_verification_review(verify_score(wrong_unmatched), "SCORE_ROUTE_MISMATCH")
