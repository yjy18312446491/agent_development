"""Regression tests for rule scoring and its Guard and route contracts."""

from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock

from app.extract import CandidateProfile, ExtractResult, extract_profile
from app.guard import GuardResult, guard_text
from app.route import JobPosting, RouteResult, load_jobs, route_profile
from app.score import ScoreBreakdown, score_candidate


RESUMES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "resumes"


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.jobs = load_jobs()
        self.guard = GuardResult(True, "PASS", "OK", "", None)
        self.route = RouteResult("JD1", self.jobs[0].title, 1.0, 1.0, 1.0, "")

    def score(self, profile=None, *, jobs=None, route=None, project_scorer=None):
        return score_candidate(
            "简历正文",
            CandidateProfile() if profile is None else profile,
            self.guard,
            self.route if route is None else route,
            self.jobs if jobs is None else jobs,
            project_scorer=project_scorer,
        )

    def assert_review(self, result, reason_code):
        self.assertEqual((result.status, result.total, result.scores), ("REVIEW", -1, None))
        self.assertEqual(result.reason_code, reason_code)
        self.assertTrue(result.reason)
        self.assertEqual((result.skill_hits, result.skill_missing, result.project_source), ([], [], ""))

    def test_required_skills_zero_partial_and_full(self):
        job = replace(self.jobs[0], skills=["Python", "FastAPI", "python", "MySQL"])
        for skills, points, hits, missing in (
            (["IPython", "MySQL8"], 0, [], ["Python", "FastAPI", "MySQL"]),
            (["mysql", "MYSQL", "python"], 27, ["Python", "MySQL"], ["FastAPI"]),
            (["python", "fastapi", "mysql"], 40, ["Python", "FastAPI", "MySQL"], []),
        ):
            with self.subTest(skills=skills):
                result = self.score(CandidateProfile(skills=skills), jobs=[job])
                self.assertEqual(result.status, "SCORED")
                self.assertEqual(result.scores.skill, points)
                self.assertEqual(result.skill_hits, hits)
                self.assertEqual(result.skill_missing, missing)

    def test_experience_tiers_and_boundaries(self):
        for years, points in (
            (4, 15), (3, 15), (2.99, 12), (2.5, 12), (2.49, 9),
            (2, 9), (1.99, 6), (1, 6), (0.99, 3), (0, 3),
        ):
            with self.subTest(years=years):
                self.assertEqual(self.score(CandidateProfile(years=years)).scores.years, points)

    def test_zero_year_minimum_awards_full_experience_points(self):
        route = replace(self.route, jd_id="JD2")
        self.assertEqual(self.score(CandidateProfile(years=0), route=route).scores.years, 15)

    def test_unknown_education_and_default_mock_are_zero(self):
        result = self.score()
        self.assertEqual(result.status, "SCORED")
        self.assertEqual(result.scores.education, 0)
        self.assertEqual((result.scores.project, result.project_source), (0, "mock"))

    def test_bonus_skills_are_optional_deduplicated_and_capped(self):
        original = JobPosting("JD1", "岗位", 3, ["Python"], None, "原岗位说明")
        profile = CandidateProfile(skills=["python", "Docker", "docker", "linux", "Git"])
        self.assertEqual(original.bonus_skills, [])
        self.assertEqual(self.score(profile, jobs=[original]).scores.bonus, 0)
        for skills, points in (
            (["PYTHON"], 0),
            (["Python", "Docker", "DOCKER"], 3),
            (["Docker", "Linux"], 5),
            (["Docker", "Linux", "Git"], 5),
        ):
            with self.subTest(skills=skills):
                job = replace(original, bonus_skills=skills)
                result = self.score(profile, jobs=[job])
                self.assertEqual(result.scores.bonus, points)
                self.assertEqual(result.scores.skill, 40)

    def test_synthetic_jobs_provide_configured_bonus_skills(self):
        self.assertEqual(
            {job.jd_id: job.bonus_skills for job in self.jobs},
            {"JD1": ["Docker", "Linux"], "JD2": ["Git", "Docker"], "JD3": ["Git"]},
        )

    def test_doctorate_with_full_jd1_match_and_injected_project_score_gets_100(self):
        profile = CandidateProfile(
            years=3, education="博士", edu_score=10, skills=self.jobs[0].skills + ["Docker", "Linux"]
        )
        scorer = Mock(return_value=30)
        result = self.score(profile, project_scorer=scorer)
        scorer.assert_called_once_with("简历正文", profile, self.jobs[0])
        self.assertEqual((result.status, result.jd_id, result.total), ("SCORED", "JD1", 100))
        self.assertEqual(result.scores, ScoreBreakdown(40, 30, 15, 10, 5))
        self.assertEqual(result.project_source, "injected")

    def test_injected_zero_is_a_valid_project_score(self):
        result = self.score(project_scorer=lambda text, profile, job: 0)
        self.assertEqual((result.status, result.scores.project, result.project_source),
                         ("SCORED", 0, "injected"))

    def test_unmatched_keeps_only_education_score(self):
        route = replace(self.route, jd_id="UNMATCHED")
        scorer = Mock(return_value=30)
        for education, points in (("本科", 7), ("未知", 0)):
            with self.subTest(education=education):
                profile = CandidateProfile(years=4, education=education, edu_score=points,
                                           skills=["Python", "Docker"])
                result = self.score(profile, route=route, jobs=[], project_scorer=scorer)
                self.assertEqual((result.status, result.jd_id, result.total), ("SCORED", "UNMATCHED", points))
                self.assertEqual(result.scores, ScoreBreakdown(0, 0, 0, points, 0))
                self.assertEqual((result.skill_hits, result.skill_missing), ([], []))
        scorer.assert_not_called()

    def test_guard_blocks_before_profile_and_preserves_original_reason(self):
        self.guard = GuardResult(False, "REVIEW", "TOO_SHORT", "内容过短", -1)
        scorer = Mock(return_value=30)
        result = self.score(CandidateProfile(years=-1), project_scorer=scorer)
        self.assert_review(result, "TOO_SHORT")
        self.assertEqual(result.reason, self.guard.reason)
        scorer.assert_not_called()

    def test_missing_guard_skips_project_scoring(self):
        self.guard = None
        scorer = Mock(return_value=30)
        self.assert_review(self.score(project_scorer=scorer), "GUARD_MISSING")
        scorer.assert_not_called()

    def test_invalid_job_configuration_skips_project_scoring(self):
        scorer = Mock(return_value=30)
        for jobs, code in (
            ([], "JOB_NOT_FOUND"),
            ([replace(self.jobs[0], skills=[])], "EMPTY_REQUIRED_SKILLS"),
            ([replace(self.jobs[0], min_years=float("nan"))], "INVALID_JOB_MIN_YEARS"),
        ):
            with self.subTest(code=code):
                result = self.score(jobs=jobs, project_scorer=scorer)
                self.assert_review(result, code)
                self.assertEqual(result.jd_id, "JD1")
        scorer.assert_not_called()

    def test_invalid_profile_is_reviewed_even_if_unmatched(self):
        scorer = Mock(return_value=30)
        route = replace(self.route, jd_id="UNMATCHED")
        for field, invalid_values, code in (
            ("years", (-1, float("nan"), float("inf"), True, "2"), "INVALID_YEARS"),
            ("edu_score", (-1, 11, 7.0, True), "INVALID_EDUCATION_SCORE"),
        ):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    profile = CandidateProfile(**{field: value})
                    self.assert_review(self.score(profile, route=route, project_scorer=scorer), code)
        scorer.assert_not_called()

    def test_invalid_project_scores_and_exception_trigger_review(self):
        for value in (False, 12.0, -1, 31, None):
            with self.subTest(value=value):
                self.assert_review(self.score(project_scorer=Mock(return_value=value)), "INVALID_PROJECT_SCORE")
        self.assert_review(
            self.score(project_scorer=Mock(side_effect=RuntimeError("internal detail"))),
            "PROJECT_SCORER_ERROR",
        )

    def test_normal_chinese_resume_from_extract_through_score(self):
        text = (RESUMES / "S001_normal_backend.txt").read_text(encoding="utf-8")
        profile = extract_profile(text)
        guard = guard_text(ExtractResult(text, "OK"))
        route = route_profile(text, profile, self.jobs)
        result = score_candidate(text, profile, guard, route, self.jobs)
        self.assertEqual((guard.status, route.jd_id, result.status), ("PASS", "JD2", "SCORED"))
        scores = result.scores
        self.assertEqual(result.total, scores.skill + scores.project + scores.years + scores.education + scores.bonus)
        self.assertEqual(result.project_source, "mock")

    def test_short_chinese_resume_is_reviewed(self):
        text = (RESUMES / "S007_education_only.txt").read_text(encoding="utf-8")
        profile = extract_profile(text)
        guard = guard_text(ExtractResult(text, "OK"))
        route = route_profile(text, profile, self.jobs)
        scorer = Mock(return_value=30)
        result = score_candidate(text, profile, guard, route, self.jobs, project_scorer=scorer)
        self.assert_review(result, "TOO_SHORT")
        scorer.assert_not_called()
