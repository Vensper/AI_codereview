import pathlib
import re
import unittest

from utils.report_review_failure import normalize_failure_stage


JENKINSFILE = pathlib.Path(__file__).resolve().parents[1] / "Jenkinsfile"


class JenkinsFailureReportingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = JENKINSFILE.read_text(encoding="utf-8")

    def test_every_configured_failure_stage_is_accepted_by_python_reporter(self):
        stages = set(
            re.findall(r"env\.REVIEW_FAILURE_STAGE = '([^']+)'", self.text)
        )
        self.assertIn("配置 AI 执行器", stages)
        for stage in stages:
            with self.subTest(stage=stage):
                self.assertEqual(normalize_failure_stage(stage), stage)

    def test_python_reporter_is_primary_and_bootstrap_fallback_remains(self):
        self.assertIn(
            "fileExists('review-tool/utils/report_review_failure.py')", self.text
        )
        self.assertIn("python -u -m utils.report_review_failure", self.text)
        self.assertIn("启动兜底", self.text)
        self.assertNotIn("def reportableStages", self.text)
        self.assertIn("add_labels=ai_code_review_failed", self.text)
        self.assertIn("AI_CODE_REVIEW_FAILURE:", self.text)


if __name__ == "__main__":
    unittest.main()
