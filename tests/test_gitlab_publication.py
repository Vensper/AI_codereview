import json
import os
import tempfile
import unittest
from unittest.mock import patch

from utils.common import (
    GitLabAPIError,
    GitLabPublicationUncertainError,
    _find_discussion_by_marker,
    inline_comment_marker,
    post_marked_discussion,
    post_review_start_note,
    post_review_summary_note,
    review_done_marker,
    start_note_marker,
    summary_note_marker,
)
from utils.publish_review import main as publish_review


SHA = "a" * 40


class GitLabPublicationTests(unittest.TestCase):
    def _publishing_environment(self):
        return {
            "EFFECTIVE_REPOSITORY_URL": "https://gitlab.example/group/project.git",
            "EFFECTIVE_SOURCE_SHA": SHA,
            "EFFECTIVE_PROJECT_ID": "5",
            "EFFECTIVE_MR_IID": "12",
            "GITLAB_API_TOKEN": "token",
            "REVIEW_PUBLISH_COMMENT": "true",
            "BUILD_TAG": "jenkins-review-8",
            "BUILD_NUMBER": "8",
        }

    def _write_review_files(self, directory):
        comment = {
            "path": "src/app.py",
            "side": "new",
            "line": 12,
            "severity": "high",
            "title": "Missing validation",
            "problem": "Input is trusted.",
            "risk": "Invalid data is stored.",
            "suggestion": "Validate it.",
        }
        with open(os.path.join(directory, "mr.diff"), "w", encoding="utf-8") as handle:
            handle.write("diff")
        with open(
            os.path.join(directory, "review-result.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump(
                {
                    "summary": "Review summary",
                    "change_intent": "Validate input",
                    "changes": [],
                    "comments": [comment],
                },
                handle,
            )
        return comment

    def test_markers_are_stable_per_build_and_finding(self):
        comment = {
            "path": "src/app.py",
            "side": "new",
            "line": 12,
            "severity": "high",
            "title": "Missing validation",
            "problem": "Input is trusted.",
            "risk": "Invalid data is stored.",
            "suggestion": "Validate it.",
        }
        self.assertEqual(
            summary_note_marker("jenkins-review-8", SHA),
            summary_note_marker("jenkins-review-8", SHA),
        )
        self.assertEqual(
            inline_comment_marker("jenkins-review-8", SHA, comment),
            inline_comment_marker(
                "jenkins-review-8", SHA, dict(reversed(list(comment.items())))
            ),
        )
        self.assertEqual(
            inline_comment_marker("jenkins-review-8", SHA, comment),
            inline_comment_marker("jenkins-review-9", SHA, comment),
        )
        self.assertNotEqual(
            inline_comment_marker(
                "jenkins-review-8", SHA, comment, run_scoped=True
            ),
            inline_comment_marker(
                "jenkins-review-9", SHA, comment, run_scoped=True
            ),
        )
        self.assertIn(
            "AI_CODE_REVIEW_START",
            start_note_marker("jenkins-review-8", SHA),
        )

    @patch("utils.common.current_gitlab_user_id", return_value=7)
    def test_start_note_timeout_is_confirmed_without_duplicate_post(self, _user):
        timeout = GitLabAPIError("timeout", retryable=True, retry_delay=1)
        with patch(
            "utils.common._find_note_by_marker",
            side_effect=[None, {"id": 22, "body": "marked"}],
        ) as find, patch(
            "utils.common.post_summary_note", side_effect=timeout
        ) as post, patch("utils.common.time.sleep"):
            result = post_review_start_note(
                "https://gitlab.example",
                "token",
                5,
                12,
                "start",
                "jenkins-review-8",
                SHA,
            )
        self.assertEqual(result["id"], 22)
        self.assertEqual(find.call_count, 2)
        post.assert_called_once()
        self.assertIn("AI_CODE_REVIEW_START", post.call_args.args[-1])

    @patch("utils.common.current_gitlab_user_id", return_value=7)
    def test_summary_timeout_is_confirmed_without_duplicate_post(self, _user):
        timeout = GitLabAPIError("timeout", retryable=True, retry_delay=1)
        completed_body = (
            summary_note_marker("jenkins-review-8", SHA)
            + "\n"
            + review_done_marker(SHA)
        )
        with patch(
            "utils.common._find_note_by_marker",
            side_effect=[None, {"id": 23, "body": completed_body}],
        ) as find, patch(
            "utils.common.post_summary_note", side_effect=timeout
        ) as post, patch("utils.common.time.sleep"):
            result = post_review_summary_note(
                "https://gitlab.example",
                "token",
                5,
                12,
                "summary",
                "jenkins-review-8",
                SHA,
            )
        self.assertEqual(result["id"], 23)
        self.assertEqual(find.call_count, 2)
        post.assert_called_once()
        self.assertIn("AI_CODE_REVIEW_SUMMARY", post.call_args.args[-1])
        self.assertIn("AI_CODE_REVIEW_DONE", post.call_args.args[-1])

    @patch("utils.common.current_gitlab_user_id", return_value=7)
    def test_incomplete_existing_summary_stops_without_reposting(self, _user):
        incomplete = {
            "id": 23,
            "body": summary_note_marker("jenkins-review-8", SHA),
        }
        with patch(
            "utils.common._find_note_by_marker", return_value=incomplete
        ), patch("utils.common.post_summary_note") as post:
            with self.assertRaises(GitLabPublicationUncertainError):
                post_review_summary_note(
                    "https://gitlab.example",
                    "token",
                    5,
                    12,
                    "summary",
                    "jenkins-review-8",
                    SHA,
                )

        post.assert_not_called()

    @patch("utils.common.current_gitlab_user_id", return_value=7)
    def test_summary_retries_only_after_confirmed_absence(self, _user):
        timeout = GitLabAPIError("timeout", retryable=True, retry_delay=1)
        with patch(
            "utils.common._find_note_by_marker", side_effect=[None, None]
        ), patch(
            "utils.common.post_summary_note", side_effect=[timeout, {"id": 24}]
        ) as post, patch("utils.common.time.sleep"):
            result = post_review_summary_note(
                "https://gitlab.example",
                "token",
                5,
                12,
                "summary",
                "jenkins-review-8",
                SHA,
            )
        self.assertEqual(result["id"], 24)
        self.assertEqual(post.call_count, 2)

    @patch("utils.common.current_gitlab_user_id", return_value=7)
    def test_summary_lookup_failure_stops_before_post(self, _user):
        with patch(
            "utils.common._find_note_by_marker",
            side_effect=GitLabAPIError("offline", retryable=True),
        ), patch("utils.common.post_summary_note") as post:
            with self.assertRaises(GitLabPublicationUncertainError):
                post_review_summary_note(
                    "https://gitlab.example",
                    "token",
                    5,
                    12,
                    "summary",
                    "jenkins-review-8",
                    SHA,
                )
        post.assert_not_called()

    def test_discussion_timeout_is_confirmed_by_marker(self):
        marker = inline_comment_marker(
            "jenkins-review-8",
            SHA,
            {"path": "a.py", "side": "new", "line": 1},
        )
        timeout = GitLabAPIError("timeout", retryable=True, retry_delay=1)
        form = {"body": "review", "position[new_line]": 1}
        with patch(
            "utils.common._find_discussion_by_marker",
            side_effect=[None, {"id": "discussion-1"}],
        ), patch(
            "utils.common.gitlab_api", side_effect=timeout
        ) as api, patch("utils.common.time.sleep"):
            result = post_marked_discussion(
                "https://gitlab.example", "token", 5, 12, form, marker, 7
            )
        self.assertEqual(result["id"], "discussion-1")
        api.assert_called_once()
        self.assertIn(marker, api.call_args.args[-1]["body"])

    def test_discussion_lookup_checks_bot_author(self):
        marker = "<!-- marker -->"
        discussions = [
            {
                "id": "wrong-author",
                "notes": [{"author": {"id": 3}, "body": marker}],
            },
            {
                "id": "expected",
                "notes": [{"author": {"id": 7}, "body": "text " + marker}],
            },
        ]
        with patch("utils.common.gitlab_api", return_value=discussions):
            result = _find_discussion_by_marker(
                "https://gitlab.example", "token", 5, 12, marker, 7
            )
        self.assertEqual(result["id"], "expected")

    def test_uncertain_inline_post_is_not_downgraded_to_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            comment = self._write_review_files(directory)
            previous = os.getcwd()
            os.chdir(directory)
            try:
                with patch.dict(os.environ, self._publishing_environment(), clear=True), patch(
                    "utils.publish_review.parse_changed_lines",
                    return_value={(comment["path"], "new", 12): {
                        "old_path": comment["path"],
                        "new_path": comment["path"],
                        "new_line": 12,
                    }},
                ), patch(
                    "utils.publish_review.latest_mr_version",
                    return_value={
                        "base_sha": "b" * 40,
                        "start_sha": "c" * 40,
                        "head_sha": SHA,
                    },
                ), patch(
                    "utils.publish_review.current_gitlab_user_id", return_value=7
                ), patch(
                    "utils.publish_review.post_inline_comment",
                    side_effect=GitLabPublicationUncertainError("uncertain"),
                ), patch(
                    "utils.publish_review.post_review_summary_note"
                ) as post_summary:
                    with self.assertRaises(GitLabPublicationUncertainError):
                        publish_review()
                post_summary.assert_not_called()
            finally:
                os.chdir(previous)

    def test_completed_summary_is_posted_once_before_done_label(self):
        with tempfile.TemporaryDirectory() as directory:
            comment = self._write_review_files(directory)
            previous = os.getcwd()
            os.chdir(directory)
            try:
                with patch.dict(os.environ, self._publishing_environment(), clear=True), patch(
                    "utils.publish_review.parse_changed_lines",
                    return_value={(comment["path"], "new", 12): {
                        "old_path": comment["path"],
                        "new_path": comment["path"],
                        "new_line": 12,
                    }},
                ), patch(
                    "utils.publish_review.latest_mr_version",
                    return_value={
                        "base_sha": "b" * 40,
                        "start_sha": "c" * 40,
                        "head_sha": SHA,
                    },
                ), patch(
                    "utils.publish_review.current_gitlab_user_id", return_value=7
                ), patch(
                    "utils.publish_review.post_inline_comment"
                ) as post_inline, patch(
                    "utils.publish_review.post_review_summary_note",
                    return_value={"id": 25},
                ) as post_summary, patch(
                    "utils.publish_review.update_mr_labels"
                ) as labels:
                    publish_review()
                post_inline.assert_called_once()
                post_summary.assert_called_once()
                labels.assert_called_once_with(
                    "https://gitlab.example",
                    "token",
                    "5",
                    "12",
                    add=["ai_code_review_done"],
                    remove=["ai_code_reviewing", "ai_code_review_failed"],
                )
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
