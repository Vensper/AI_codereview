import base64
import unittest
import urllib.parse
from unittest.mock import patch

from utils.common import publishing_enabled
from utils.mr_context import resolve_mr_identity, webhook_context
from utils.webhook_receiver import trigger_jenkins


SHA = "a" * 40


class MrContextTests(unittest.TestCase):
    def test_note_event_preserves_mr_identity(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {"noteable_type": "MergeRequest", "note": "/codereview"},
            "project": {"id": 5},
            "merge_request": {
                "iid": 12, "target_project_id": 5, "source_project_id": 9,
                "source_branch": "feature", "target_branch": "main",
                "last_commit": {"id": SHA},
                "source": {"git_http_url": "https://gitlab.example/group/fork.git"},
                "target": {"git_http_url": "https://gitlab.example/group/repo.git"},
            },
        }
        values = webhook_context(payload)
        self.assertEqual(values["WEBHOOK_MR_IID"], "12")
        self.assertEqual(values["WEBHOOK_TARGET_PROJECT_ID"], "5")
        self.assertEqual(values["WEBHOOK_SOURCE_PROJECT_ID"], "9")
        self.assertEqual(values["WEBHOOK_SOURCE_SHA"], SHA)
        payload["object_attributes"]["note"] = "ordinary comment"
        self.assertIsNone(webhook_context(payload))

    def test_label_only_mr_update_does_not_retrigger_review(self):
        payload = {
            "object_kind": "merge_request",
            "object_attributes": {"state": "opened", "action": "update", "iid": 12},
        }
        self.assertIsNone(webhook_context(payload))
        payload["object_attributes"]["oldrev"] = "b" * 40
        self.assertEqual(webhook_context(payload)["WEBHOOK_MR_IID"], "12")

    def test_fork_without_source_url_never_uses_target_repository(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {"noteable_type": "MergeRequest", "note": "/codereview"},
            "project": {"id": 5, "git_http_url": "https://gitlab.example/group/target.git"},
            "merge_request": {
                "iid": 12, "source_project_id": 9, "target_project_id": 5,
                "source_branch": "feature", "target_branch": "main",
                "last_commit": {"id": SHA},
            },
        }
        self.assertEqual(webhook_context(payload)["WEBHOOK_SOURCE_REPO_HTTP_URL"], "")

    def test_unique_lookup_requires_project_branch_source_project_and_sha(self):
        environment = {
            "EFFECTIVE_PROJECT_ID": "5", "EFFECTIVE_MR_IID": "",
            "EFFECTIVE_REPOSITORY_URL": "https://gitlab.example/group/fork.git",
            "EFFECTIVE_TARGET_REPOSITORY_URL": "https://gitlab.example/group/repo.git",
            "EFFECTIVE_SOURCE_BRANCH": "feature", "EFFECTIVE_TARGET_BRANCH": "main",
            "EFFECTIVE_SOURCE_SHA": SHA, "GITLAB_API_TOKEN": "secret",
        }
        candidate = {
            "iid": 12, "state": "opened", "source_branch": "feature",
            "target_branch": "main", "source_project_id": 9,
            "target_project_id": 5, "sha": SHA,
        }

        def api(_base, _token, method, path):
            self.assertEqual(method, "GET")
            if path == "/projects/group%2Ffork":
                return {"id": 9}
            self.assertIn("source_branch=feature", path)
            return [candidate]

        self.assertEqual(resolve_mr_identity(environment, api), ("5", "12"))
        environment["EFFECTIVE_SOURCE_SHA"] = "b" * 40
        with self.assertRaisesRegex(RuntimeError, "0 exact matches"):
            resolve_mr_identity(environment, api)
        environment["EFFECTIVE_SOURCE_SHA"] = SHA

        def ambiguous(_base, _token, _method, path):
            return {"id": 9} if path == "/projects/group%2Ffork" else [candidate, dict(candidate, iid=13)]

        with self.assertRaisesRegex(RuntimeError, "2 exact matches"):
            resolve_mr_identity(environment, ambiguous)
        wrong_project = dict(candidate, source_project_id=10)

        def cross_project(_base, _token, _method, path):
            return {"id": 9} if path == "/projects/group%2Ffork" else [wrong_project]

        with self.assertRaisesRegex(RuntimeError, "0 exact matches"):
            resolve_mr_identity(environment, cross_project)

    def test_no_lookup_without_sha(self):
        with self.assertRaisesRegex(RuntimeError, "source SHA"):
            resolve_mr_identity({
                "EFFECTIVE_PROJECT_ID": "5", "EFFECTIVE_MR_IID": "",
                "EFFECTIVE_REPOSITORY_URL": "https://gitlab.example/group/fork.git",
                "EFFECTIVE_SOURCE_BRANCH": "feature", "EFFECTIVE_TARGET_BRANCH": "main",
                "GITLAB_API_TOKEN": "secret",
            }, lambda *_: self.fail("GitLab API should not be called"))

    def test_missing_project_id_uses_target_repository_path(self):
        environment = {
            "EFFECTIVE_PROJECT_ID": "", "EFFECTIVE_MR_IID": "12",
            "EFFECTIVE_REPOSITORY_URL": "https://gitlab.example/group/fork.git",
            "EFFECTIVE_TARGET_REPOSITORY_URL": "https://gitlab.example/group/repo.git",
            "GITLAB_API_TOKEN": "secret",
        }
        calls = []

        def api(_base, _token, _method, path):
            calls.append(path)
            return {"id": 5}

        self.assertEqual(resolve_mr_identity(environment, api), ("5", "12"))
        self.assertEqual(calls, ["/projects/group%2Frepo"])

    def test_manual_build_can_disable_gitlab_publishing(self):
        with patch.dict("os.environ", {"PUBLISH_COMMENT": "true", "REVIEW_PUBLISH_COMMENT": "false"}):
            self.assertFalse(publishing_enabled())

    def test_webhook_trigger_passes_context_as_build_parameters(self):
        calls = []

        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def opener(request, timeout):
            calls.append((request, timeout))
            return Response()

        trigger_jenkins(
            {
                "WEBHOOK_MR_IID": "12",
                "WEBHOOK_TARGET_PROJECT_ID": "5",
                "WEBHOOK_SOURCE_REPO_HTTP_URL": "https://gitlab.example/group/source.git",
                "WEBHOOK_SOURCE_BRANCH": "feature/review",
                "WEBHOOK_TARGET_BRANCH": "master",
            },
            {"JENKINS_JOB_URL": "https://jenkins.example/job/review",
             "JENKINS_API_USER": "bot", "JENKINS_API_TOKEN": "token"},
            opener,
        )
        request, timeout = calls[0]
        self.assertEqual(timeout, 5)
        self.assertEqual(request.full_url, "https://jenkins.example/job/review/buildWithParameters")
        form = urllib.parse.parse_qs(request.data.decode("utf-8"))
        self.assertEqual(form["MR_IID"], ["12"])
        self.assertEqual(form["PROJECT_ID"], ["5"])
        self.assertEqual(
            form["dev_repository"],
            ["https://gitlab.example/group/source.git"],
        )
        self.assertEqual(form["SOURCE_BRANCH"], ["feature/review"])
        self.assertEqual(form["TARGET_BRANCH"], ["master"])
        self.assertNotIn("PUBLISH_COMMENT", form)
        authorization = request.get_header("Authorization").split(" ", 1)[1]
        self.assertEqual(base64.b64decode(authorization).decode(), "bot:token")


if __name__ == "__main__":
    unittest.main()
