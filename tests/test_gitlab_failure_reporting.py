import json
import ssl
import unittest
import urllib.error
from unittest.mock import patch

import utils.report_review_failure as failure_reporter
from utils.common import (
    GitLabAPIError,
    GitLabPublicationUncertainError,
    gitlab_api,
    post_failure_note,
)


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


class GitLabFailureReportingTests(unittest.TestCase):
    def test_failure_reporter_accepts_executor_configuration_stage(self):
        self.assertEqual(
            failure_reporter.normalize_failure_stage("配置 AI 执行器"),
            "配置 AI 执行器",
        )

    def test_failure_reporter_rejects_multiline_or_oversized_stage(self):
        self.assertEqual(
            failure_reporter.normalize_failure_stage("AI 审查\n伪造内容"),
            "未知阶段",
        )
        self.assertEqual(
            failure_reporter.normalize_failure_stage("x" * 41),
            "未知阶段",
        )

    def test_failure_reporter_updates_label_and_posts_sanitized_note(self):
        environment = {
            "REVIEW_FAILURE_STAGE": "配置 AI 执行器",
            "BUILD_URL": "https://jenkins.example/job/review/8/",
            "BUILD_TAG": "jenkins-review-8",
        }
        with patch.dict(failure_reporter.os.environ, environment, clear=True), patch(
            "utils.report_review_failure.publishing_enabled", return_value=True
        ), patch(
            "utils.report_review_failure.publishing_context",
            return_value=("https://gitlab.example", "token", "5", "12"),
        ), patch(
            "utils.report_review_failure.update_mr_labels"
        ) as update_labels, patch(
            "utils.report_review_failure.post_failure_note"
        ) as post_note:
            failure_reporter.main()

        update_labels.assert_called_once_with(
            "https://gitlab.example",
            "token",
            "5",
            "12",
            add=["ai_code_review_failed"],
            remove=["ai_code_reviewing", "ai_code_review_done"],
        )
        self.assertIn("失败阶段：配置 AI 执行器", post_note.call_args.args[4])
        self.assertEqual(post_note.call_args.args[5], "jenkins-review-8")

    def test_get_retries_transient_http_error(self):
        unavailable = urllib.error.HTTPError("url", 503, "unavailable", {}, None)
        with patch("utils.common.urllib.request.urlopen", side_effect=[unavailable, Response({"id": 1})]) as open_url, patch("utils.common.time.sleep") as sleep:
            self.assertEqual(gitlab_api("https://gitlab.example", "token", "GET", "/user"), {"id": 1})
        self.assertEqual(open_url.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_429_honors_retry_after(self):
        limited = urllib.error.HTTPError("url", 429, "limited", {"Retry-After": "4"}, None)
        with patch("utils.common.urllib.request.urlopen", side_effect=[limited, Response({"id": 1})]), patch("utils.common.time.sleep") as sleep:
            gitlab_api("https://gitlab.example", "token", "PUT", "/mr", {"add_labels": "failed"})
        sleep.assert_called_once_with(4.0)

    def test_certificate_failure_is_not_retried(self):
        certificate_error = urllib.error.URLError(ssl.SSLCertVerificationError("bad certificate"))
        with patch("utils.common.urllib.request.urlopen", side_effect=certificate_error) as open_url, patch("utils.common.time.sleep") as sleep:
            with self.assertRaises(GitLabAPIError):
                gitlab_api("https://gitlab.example", "token", "GET", "/user")
        open_url.assert_called_once()
        sleep.assert_not_called()

    def test_401_is_not_retried(self):
        unauthorized = urllib.error.HTTPError("url", 401, "unauthorized", {}, None)
        with patch("utils.common.urllib.request.urlopen", side_effect=unauthorized) as open_url:
            with self.assertRaises(GitLabAPIError):
                gitlab_api("https://gitlab.example", "token", "PUT", "/mr", {})
        open_url.assert_called_once()

    @patch("utils.common.current_gitlab_user_id", return_value=7)
    def test_post_timeout_checks_for_existing_note_before_retry(self, _user):
        timeout = GitLabAPIError("timeout", retryable=True, retry_delay=1)
        with patch("utils.common._find_note_by_marker", side_effect=[None, {"id": 9}]) as find, patch("utils.common.post_summary_note", side_effect=timeout) as post, patch("utils.common.time.sleep"):
            self.assertIsNone(post_failure_note("https://gitlab.example", "token", 5, 12, "failure", "jenkins-job-8"))
        self.assertEqual(find.call_count, 2)
        post.assert_called_once()
        self.assertIn("<!-- AI_CODE_REVIEW_FAILURE:jenkins-job-8 -->", post.call_args.args[-1])

    @patch("utils.common.current_gitlab_user_id", return_value=7)
    def test_post_retries_only_when_note_lookup_succeeds_and_is_absent(self, _user):
        timeout = GitLabAPIError("timeout", retryable=True, retry_delay=1)
        with patch("utils.common._find_note_by_marker", side_effect=[None, None]) as find, patch("utils.common.post_summary_note", side_effect=[timeout, {"id": 9}]) as post, patch("utils.common.time.sleep"):
            self.assertEqual(post_failure_note("https://gitlab.example", "token", 5, 12, "failure", "jenkins-job-8"), {"id": 9})
        self.assertEqual(find.call_count, 2)
        self.assertEqual(post.call_count, 2)

    @patch("utils.common.current_gitlab_user_id", return_value=7)
    def test_lookup_failure_never_posts(self, _user):
        with patch("utils.common._find_note_by_marker", side_effect=GitLabAPIError("offline", True)) as find, patch("utils.common.post_summary_note") as post:
            with self.assertRaises(GitLabPublicationUncertainError):
                post_failure_note("https://gitlab.example", "token", 5, 12, "failure", "jenkins-job-8")
        find.assert_called_once()
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
