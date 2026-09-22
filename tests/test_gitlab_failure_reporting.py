import json
import ssl
import unittest
import urllib.error
from unittest.mock import patch

from utils.common import GitLabAPIError, gitlab_api, post_failure_note


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
        with patch("utils.common._failure_note_exists", side_effect=[False, True]) as exists, patch("utils.common.post_summary_note", side_effect=timeout) as post, patch("utils.common.time.sleep"):
            self.assertIsNone(post_failure_note("https://gitlab.example", "token", 5, 12, "failure", "jenkins-job-8"))
        self.assertEqual(exists.call_count, 2)
        post.assert_called_once()
        self.assertIn("<!-- AI_CODE_REVIEW_FAILURE:jenkins-job-8 -->", post.call_args.args[-1])

    @patch("utils.common.current_gitlab_user_id", return_value=7)
    def test_post_retries_only_when_note_lookup_succeeds_and_is_absent(self, _user):
        timeout = GitLabAPIError("timeout", retryable=True, retry_delay=1)
        with patch("utils.common._failure_note_exists", side_effect=[False, False]) as exists, patch("utils.common.post_summary_note", side_effect=[timeout, {"id": 9}]) as post, patch("utils.common.time.sleep"):
            self.assertEqual(post_failure_note("https://gitlab.example", "token", 5, 12, "failure", "jenkins-job-8"), {"id": 9})
        self.assertEqual(exists.call_count, 2)
        self.assertEqual(post.call_count, 2)

    @patch("utils.common.current_gitlab_user_id", return_value=7)
    def test_lookup_failure_never_posts(self, _user):
        with patch("utils.common._failure_note_exists", side_effect=GitLabAPIError("offline", True)) as exists, patch("utils.common.post_summary_note") as post:
            with self.assertRaises(GitLabAPIError):
                post_failure_note("https://gitlab.example", "token", 5, 12, "failure", "jenkins-job-8")
        exists.assert_called_once()
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
