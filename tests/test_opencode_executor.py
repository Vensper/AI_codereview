import json
import os
import tempfile
import unittest
from unittest.mock import patch

from utils.ai_executors.base import ExecutionBudget, ProcessResult
from utils.ai_executors.opencode_cli import OpenCodeCliExecutor


SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


class OpenCodeCliExecutorTests(unittest.TestCase):
    def environment(self, **overrides):
        value = {
            "PATH": r"C:\bin",
            "USERPROFILE": r"C:\Users\agent",
            "AI_API_KEY": "top-secret",
            "AI_BASE_URL": "https://gateway.example/v1",
            "AI_MODEL_OVERRIDE": "review-model",
            "AI_API_PROTOCOL": "openai_chat",
            "AI_AUTH_STYLE": "bearer",
            "OPENCODE_NPM_REGISTRY": "https://npm.example",
            "OPENCODE_CACHE_HOME": r"C:\jenkins-cache\opencode",
            "DEEPSEEK_API_KEY": "must-not-leak",
        }
        value.update(overrides)
        return value

    @patch("utils.ai_executors.opencode_cli.run_process")
    @patch(
        "utils.ai_executors.opencode_cli.shutil.which",
        return_value=r"C:\bin\opencode.cmd",
    )
    def test_investigation_is_isolated_and_read_only(self, _which, run_process):
        def completed(*_args, **kwargs):
            callback = kwargs["stdout_line_callback"]
            callback(json.dumps({
                "type": "step_start",
                "part": {"id": "step-1", "type": "step-start"},
            }))
            callback(json.dumps({
                "type": "tool",
                "part": {"id": "tool-1", "type": "tool"},
            }))
            callback(json.dumps({
                "type": "text",
                "part": {"id": "text-1", "type": "text", "text": "draft"},
            }))
            return ProcessResult(0, "", "")

        run_process.side_effect = completed
        executor = OpenCodeCliExecutor(environ=self.environment())
        budget = ExecutionBudget()
        draft = executor.investigate("prompt", r"C:\worktree", 480, budget)

        self.assertEqual(draft, "draft")
        self.assertEqual(budget.model_rounds, 1)
        self.assertEqual(budget.tool_calls, 1)
        command = run_process.call_args.args[0]
        self.assertIn("--pure", command)
        self.assertIn("--format", command)
        self.assertEqual(command[command.index("--format") + 1], "json")
        kwargs = run_process.call_args.kwargs
        environment = kwargs["env"]
        self.assertEqual(environment["OPENCODE_DISABLE_PROJECT_CONFIG"], "1")
        self.assertEqual(environment["OPENCODE_PURE"], "1")
        self.assertEqual(environment["XDG_CACHE_HOME"], r"C:\jenkins-cache\opencode")
        self.assertEqual(environment["NPM_CONFIG_REGISTRY"], "https://npm.example")
        self.assertIn("ai-review-opencode-", environment["TEMP"])
        self.assertEqual(environment["TEMP"], environment["TMP"])
        self.assertNotIn("DEEPSEEK_API_KEY", environment)
        config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(config["permission"]["*"], "deny")
        self.assertEqual(config["permission"]["read"], "allow")
        self.assertEqual(config["permission"]["glob"], "allow")
        self.assertEqual(config["permission"]["grep"], "allow")
        self.assertEqual(config["permission"]["bash"], "deny")
        self.assertEqual(
            config["provider"]["jenkins"]["npm"],
            "@ai-sdk/openai-compatible",
        )
        self.assertNotIn("top-secret", environment["OPENCODE_CONFIG_CONTENT"])

    @patch("utils.ai_executors.opencode_cli.run_process")
    @patch(
        "utils.ai_executors.opencode_cli.shutil.which",
        return_value=r"C:\bin\opencode.cmd",
    )
    def test_finalization_disables_all_tools_and_supports_responses(
        self, _which, run_process
    ):
        def completed(*_args, **kwargs):
            callback = kwargs["stdout_line_callback"]
            callback(json.dumps({
                "type": "step_start",
                "part": {"id": "step-1", "type": "step-start"},
            }))
            callback(json.dumps({
                "type": "text",
                "part": {
                    "id": "text-1",
                    "type": "text",
                    "text": '{"summary":"ok"}',
                },
            }))
            return ProcessResult(0, "", "")

        run_process.side_effect = completed
        executor = OpenCodeCliExecutor(
            environ=self.environment(AI_API_PROTOCOL="openai_responses")
        )
        raw = executor.finalize(
            "prompt", r"C:\worktree", SCHEMA, 120, ExecutionBudget()
        )

        self.assertEqual(json.loads(raw), {"summary": "ok"})
        environment = run_process.call_args.kwargs["env"]
        config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(config["permission"]["read"], "deny")
        self.assertEqual(config["permission"]["glob"], "deny")
        self.assertEqual(config["permission"]["grep"], "deny")
        self.assertEqual(
            config["provider"]["jenkins"]["npm"], "@ai-sdk/openai"
        )

    def test_rejects_repository_controlled_opencode_config(self):
        executor = OpenCodeCliExecutor(environ=self.environment())
        with tempfile.TemporaryDirectory() as workspace:
            with open(os.path.join(workspace, "opencode.json"), "w") as handle:
                handle.write("{}")
            with self.assertRaisesRegex(RuntimeError, "repository-controlled"):
                executor._reject_executable_project_config(workspace)

    def test_rejects_api_key_header_mode(self):
        executor = OpenCodeCliExecutor(
            environ=self.environment(AI_AUTH_STYLE="api_key")
        )
        with self.assertRaisesRegex(RuntimeError, "bearer"):
            executor._settings()

    def test_temporary_provider_cache_is_reused_across_phases(self):
        executor = OpenCodeCliExecutor(
            environ={
                key: value
                for key, value in self.environment().items()
                if key != "OPENCODE_CACHE_HOME"
            }
        )
        settings = executor._settings()
        first, _ = executor._environment(settings, "one", True)
        second, _ = executor._environment(settings, "two", False)
        self.assertEqual(first["XDG_CACHE_HOME"], second["XDG_CACHE_HOME"])
        executor.close()

    @patch("utils.ai_executors.opencode_cli.run_process")
    @patch(
        "utils.ai_executors.opencode_cli.shutil.which",
        return_value=r"C:\bin\opencode.cmd",
    )
    def test_nonzero_exit_redacts_credential(self, _which, run_process):
        run_process.return_value = ProcessResult(
            1, "", "authentication failed for top-secret"
        )
        executor = OpenCodeCliExecutor(environ=self.environment())
        with self.assertRaisesRegex(RuntimeError, r"authentication failed for \*\*\*"):
            executor.finalize(
                "prompt", r"C:\worktree", SCHEMA, 120, ExecutionBudget()
            )

    @patch("utils.ai_executors.opencode_cli.run_process")
    @patch(
        "utils.ai_executors.opencode_cli.shutil.which",
        return_value=r"C:\bin\opencode.cmd",
    )
    def test_nonzero_exit_reports_structured_stdout_error(
        self, _which, run_process
    ):
        def completed(*_args, **kwargs):
            kwargs["stdout_line_callback"](json.dumps({
                "type": "error",
                "error": {
                    "name": "ProviderAuthError",
                    "data": {
                        "message": "request rejected for top-secret",
                        "request": {"prompt": "must not be logged"},
                    },
                },
            }))
            return ProcessResult(1, "", "")

        run_process.side_effect = completed
        executor = OpenCodeCliExecutor(environ=self.environment())
        with self.assertRaisesRegex(
            RuntimeError,
            r"ProviderAuthError: request rejected for \*\*\*",
        ) as raised:
            executor.investigate(
                "prompt", r"C:\worktree", 120, ExecutionBudget()
            )
        self.assertNotIn("must not be logged", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
