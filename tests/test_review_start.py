import os
import unittest
from unittest.mock import patch

from utils import review_start


class ReviewStartTests(unittest.TestCase):
    def test_codereview_comment_runs_again_for_an_already_reviewed_sha(self):
        for event in ({"WEBHOOK_EVENT_KIND": "note"}, {"gitlabActionType": "NOTE"}):
            with self.subTest(event=event), patch.dict(os.environ, {
                "EFFECTIVE_SOURCE_SHA": "a" * 40,
                "FORCE_REVIEW": "false",
                "REVIEW_BYPASS_DEDUP": "false",
                **event,
            }, clear=True), patch("utils.review_start.publishing_enabled", return_value=True), patch(
                "utils.review_start.publishing_context", return_value=("https://gitlab.example", "token", "5", "12")
            ), patch("utils.review_start.has_completed_review") as completed, patch(
                "utils.review_start.post_summary_note"
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
            "FORCE_REVIEW": "false",
            "REVIEW_BYPASS_DEDUP": "false",
            "WEBHOOK_EVENT_KIND": "merge_request",
        }, clear=True), patch("utils.review_start.publishing_enabled", return_value=True), patch(
            "utils.review_start.publishing_context", return_value=("https://gitlab.example", "token", "5", "12")
        ), patch("utils.review_start.has_completed_review", return_value=True) as completed, patch(
            "utils.review_start.post_summary_note"
        ) as post, patch("utils.review_start.write_text") as write:
            review_start.main()
            completed.assert_called_once()
            post.assert_not_called()
            write.assert_called_once_with("review-skip.flag", "already-completed\n")

    def test_manual_force_review_still_bypasses_check(self):
        with patch.dict(os.environ, {"FORCE_REVIEW": "true"}, clear=True):
            self.assertTrue(review_start.bypass_completed_review_check())
        with patch.dict(os.environ, {"REVIEW_BYPASS_DEDUP": "true"}, clear=True):
            self.assertTrue(review_start.bypass_completed_review_check())


if __name__ == "__main__":
    unittest.main()
