import json
import tempfile
import unittest
from pathlib import Path

from app.extract import CandidateProfile, extract_profile
from app.route import load_jobs, route_profile


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = load_jobs()

    def test_four_year_backend_routes_to_intermediate_job(self):
        profile = extract_profile(
            "后端开发 4 年\nPython FastAPI MySQL Redis Docker"
        )

        result = route_profile("后端开发工程师", profile, self.jobs)

        self.assertEqual(result.jd_id, "JD1")
        self.assertNotEqual(result.jd_id, "JD2")
        self.assertGreater(result.combined, 0.8)

    def test_entry_level_backend_routes_to_junior_job(self):
        profile = extract_profile(
            "项目经历：校园平台\nPython Flask MySQL\n应届生，工作年限 0 年"
        )

        result = route_profile("后端开发工程师", profile, self.jobs)

        self.assertEqual(profile.years, 0.0)
        self.assertEqual(result.jd_id, "JD2")

    def test_four_year_flask_profile_stays_out_of_junior_job(self):
        profile = extract_profile("后端开发 4 年\nPython Flask MySQL")

        result = route_profile("后端开发工程师", profile, self.jobs)

        self.assertEqual(result.jd_id, "JD1")
        self.assertNotEqual(result.jd_id, "JD2")

    def test_frontend_profile_does_not_route_to_backend(self):
        profile = extract_profile(
            "前端开发工程师\n前端开发 1 年\n"
            "JavaScript TypeScript Vue React CSS"
        )

        result = route_profile("前端开发工程师", profile, self.jobs)

        self.assertEqual(result.jd_id, "JD3")
        self.assertNotIn(result.jd_id, {"JD1", "JD2"})

    def test_low_skill_hit_ratio_is_unmatched(self):
        profile = CandidateProfile(years=4.0, skills=["Java"])

        result = route_profile("通用工程师", profile, self.jobs)

        self.assertEqual(result.jd_id, "UNMATCHED")
        self.assertEqual(result.skill_ratio, 0.0)
        self.assertEqual(result.combined, 0.6)

    def test_unmatched_keeps_best_route_metrics(self):
        profile = CandidateProfile(years=4.0, skills=["Python"])

        result = route_profile("后端开发工程师", profile, self.jobs)

        self.assertEqual(result.jd_id, "UNMATCHED")
        self.assertEqual(result.skill_ratio, 0.25)
        self.assertEqual(result.years_fit, 1.0)
        self.assertEqual(result.combined, 0.7)
        self.assertIn("不是五维总分", result.reason)

    def test_backend_support_terms_do_not_create_direction_conflict(self):
        profile = CandidateProfile(years=0.0, skills=["Python"])
        text = "后端开发，负责自动化测试、Kubernetes 和监控告警"

        result = route_profile(text, profile, self.jobs)

        self.assertEqual(result.jd_id, "JD2")

    def test_route_id_is_from_config_or_unmatched(self):
        profile = extract_profile("后端开发 2 年\nPython Flask MySQL")

        result = route_profile("后端开发工程师", profile, self.jobs)

        valid_ids = {job.jd_id for job in self.jobs} | {"UNMATCHED"}
        self.assertIn(result.jd_id, valid_ids)

    def test_load_jobs_uses_stable_relative_config(self):
        jobs = load_jobs()

        self.assertEqual([job.jd_id for job in jobs], ["JD1", "JD2", "JD3"])
        self.assertEqual(jobs[0].min_years, 3.0)
        self.assertIsNone(jobs[0].max_years)
        self.assertEqual(jobs[1].max_years, 2.5)

    def test_boundary_years_route_to_expected_levels(self):
        junior_profile = extract_profile("后端开发 2.5 年\nPython Flask MySQL")
        intermediate_profile = extract_profile(
            "后端开发 3 年\nPython FastAPI MySQL Redis"
        )

        self.assertEqual(
            route_profile("后端开发", junior_profile, self.jobs).jd_id,
            "JD2",
        )
        self.assertEqual(
            route_profile("后端开发", intermediate_profile, self.jobs).jd_id,
            "JD1",
        )

    def test_empty_job_list_is_unmatched(self):
        profile = extract_profile("后端开发 2 年\nPython Flask MySQL")

        result = route_profile("后端开发", profile, [])

        self.assertEqual(result.jd_id, "UNMATCHED")
        self.assertEqual(result.skill_ratio, 0.0)

    def test_duplicate_job_ids_fail_to_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            path.write_text(
                json.dumps(
                    [
                        {"jd_id": "JD1", "title": "A", "min_years": 0, "skills": ["Python"]},
                        {"jd_id": "JD1", "title": "B", "min_years": 0, "skills": ["Flask"]},
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "重复的岗位 ID"):
                load_jobs(Path(directory))

    def test_route_does_not_use_profile_name_as_job_id(self):
        profile = extract_profile("姓名：JD1\n后端开发 0 年\nPython Flask MySQL")

        result = route_profile("项目经历", profile, self.jobs)

        self.assertEqual(result.jd_id, "JD2")


if __name__ == "__main__":
    unittest.main()
