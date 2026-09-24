import os
import unittest
from unittest.mock import patch

from utils import review_start


class ReviewStartTests(unittest.TestCase):
    def test_codereview_comment_runs_again_for_an_already_reviewed_sha(self):
        for event in (
            {"WEBHOOK_EVENT_KIND": "note"},
            {"gitlabActionType": "NOTE"},
            {"gitlabTriggerPhrase": " /codereview "},
        ):
            with self.subTest(event=event), patch.dict(os.environ, {
                "EFFECTIVE_SOURCE_SHA": "a" * 40,
                "BUILD_TAG": "jenkins-review-8",
                "FORCE_REVIEW": "false",
                "REVIEW_BYPASS_DEDUP": "false",
                **event,
            }, clear=True), patch("utils.review_start.publishing_enabled", return_value=True), patch(
                "utils.review_start.publishing_context", return_value=("https://gitlab.example", "token", "5", "12")
            ), patch("utils.review_start.has_completed_review") as completed, patch(
                "utils.review_start.post_review_start_note"
            ) as post, patch("utils.review_start.update_mr_labels") as labels, patch(
                "utils.review_start.write_text"
            ) as write:
                review_start.main()
                completed.assert_not_called()
                post.assert_called_once()
                labels.assert_called_once()
                write.assert_not_called()

    def test_mr_update_still_skips_completed_review(self):
        with patch.dict(os.environ, {
            "EFFECTIVE_SOURCE_SHA": "a" * 40,
            "BUILD_TAG": "jenkins-review-8",
            "FORCE_REVIEW": "false",
            "REVIEW_BYPASS_DEDUP": "false",
            "WEBHOOK_EVENT_KIND": "merge_request",
        }, clear=True), patch("utils.review_start.publishing_enabled", return_value=True), patch(
            "utils.review_start.publishing_context", return_value=("https://gitlab.example", "token", "5", "12")
        ), patch("utils.review_start.has_completed_review", return_value=True) as completed, patch(
            "utils.review_start.post_review_start_note"
        ) as post, patch("utils.review_start.update_mr_labels") as labels, patch(
            "utils.review_start.write_text"
        ) as write:
            review_start.main()
            completed.assert_called_once()
            post.assert_not_called()
            labels.assert_called_once_with(
                "https://gitlab.example",
                "token",
                "5",
                "12",
                add=["ai_code_review_done"],
                remove=["ai_code_reviewing", "ai_code_review_failed"],
            )
            write.assert_called_once_with("review-skip.flag", "already-completed\n")

    def test_completed_review_label_repair_failure_does_not_skip(self):
        environment = {
            "EFFECTIVE_SOURCE_SHA": "a" * 40,
            "BUILD_TAG": "jenkins-review-8",
            "WEBHOOK_EVENT_KIND": "merge_request",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "utils.review_start.publishing_enabled", return_value=True
        ), patch(
            "utils.review_start.publishing_context",
            return_value=("https://gitlab.example", "token", "5", "12"),
        ), patch(
            "utils.review_start.has_completed_review", return_value=True
        ), patch(
            "utils.review_start.update_mr_labels",
            side_effect=RuntimeError("offline"),
        ), patch("utils.review_start.write_text") as write:
            with self.assertRaisesRegex(RuntimeError, "offline"):
                review_start.main()

        write.assert_not_called()

    def test_manual_force_review_still_bypasses_check(self):
        with patch.dict(os.environ, {"FORCE_REVIEW": "true"}, clear=True):
            self.assertTrue(review_start.bypass_completed_review_check())
        with patch.dict(os.environ, {"REVIEW_BYPASS_DEDUP": "true"}, clear=True):
            self.assertTrue(review_start.bypass_completed_review_check())

    def test_other_comment_does_not_bypass_check(self):
        with patch.dict(os.environ, {"gitlabTriggerPhrase": "ordinary comment"}, clear=True):
            self.assertFalse(review_start.bypass_completed_review_check())

    def test_start_note_and_label_failures_do_not_block_forced_review(self):
        environment = {
            "EFFECTIVE_SOURCE_SHA": "a" * 40,
            "BUILD_TAG": "jenkins-review-8",
            "gitlabTriggerPhrase": "/codereview",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "utils.review_start.publishing_enabled", return_value=True
        ), patch(
            "utils.review_start.publishing_context",
            return_value=("https://gitlab.example", "token", "5", "12"),
        ), patch(
            "utils.review_start.has_completed_review"
        ) as completed, patch(
            "utils.review_start.post_review_start_note",
            side_effect=RuntimeError("timeout"),
        ) as post, patch(
            "utils.review_start.update_mr_labels",
            side_effect=RuntimeError("offline"),
        ) as labels, patch("builtins.print") as output:
            review_start.main()

        completed.assert_not_called()
        post.assert_called_once()
        labels.assert_called_once()
        self.assertIn(
            "AI review will continue",
            " ".join(str(call.args[0]) for call in output.call_args_list),
        )

    def test_dedup_lookup_failure_still_stops_normal_review(self):
        environment = {
            "EFFECTIVE_SOURCE_SHA": "a" * 40,
            "BUILD_TAG": "jenkins-review-8",
            "WEBHOOK_EVENT_KIND": "merge_request",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "utils.review_start.publishing_enabled", return_value=True
        ), patch(
            "utils.review_start.publishing_context",
            return_value=("https://gitlab.example", "token", "5", "12"),
        ), patch(
            "utils.review_start.has_completed_review",
            side_effect=RuntimeError("lookup unavailable"),
        ), patch(
            "utils.review_start.post_review_start_note"
        ) as post:
            with self.assertRaisesRegex(RuntimeError, "lookup unavailable"):
                review_start.main()
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
