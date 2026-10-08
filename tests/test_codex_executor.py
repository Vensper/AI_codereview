import json
import os
import tempfile
import unittest
from unittest.mock import patch

from utils.ai_executors.base import ExecutionBudget, ProcessResult
from utils.ai_executors.codex_cli import CodexCliExecutor


SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


class CodexCliExecutorTests(unittest.TestCase):
    def environment(self, **overrides):
        value = {
            "PATH": r"C:\bin",
            "AI_API_KEY": "top-secret",
            "AI_BASE_URL": "https://gateway.example/v1",
            "AI_MODEL_OVERRIDE": "review-model",
            "AI_API_PROTOCOL": "openai_responses",
            "AI_AUTH_STYLE": "bearer",
            "DEEPSEEK_API_KEY": "must-not-leak",
        }
        value.update(overrides)
        return value

    @patch("utils.ai_executors.codex_cli.run_process")
    @patch(
        "utils.ai_executors.codex_cli.shutil.which",
        return_value=r"C:\bin\codex.cmd",
    )
    def test_investigation_is_ephemeral_read_only_and_counts_events(
        self, _which, run_process
    ):
        def completed(*_args, **kwargs):
            callback = kwargs["stdout_line_callback"]
            callback(json.dumps({"type": "turn.started"}))
            callback(json.dumps({
                "type": "item.completed",
                "item": {"id": "tool-1", "type": "command_execution"},
            }))
            callback(json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "message-1",
                    "type": "agent_message",
                    "text": "draft",
                },
            }))
            return ProcessResult(0, "", "")

        run_process.side_effect = completed
        executor = CodexCliExecutor(environ=self.environment())
        budget = ExecutionBudget()

        draft = executor.investigate("prompt", r"C:\worktree", 480, budget)

        self.assertEqual(draft, "draft")
        self.assertEqual(budget.model_rounds, 1)
        self.assertEqual(budget.tool_calls, 1)
        command = run_process.call_args.args[0]
        self.assertIn("--sandbox", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--strict-config", command)
        joined_command = " ".join(command)
        self.assertNotIn("top-secret", joined_command)
        self.assertNotIn("model_supports_reasoning_summaries", joined_command)
        self.assertIn('model_reasoning_summary="none"', command)
        kwargs = run_process.call_args.kwargs
        self.assertEqual(kwargs["env"]["AI_API_KEY"], "top-secret")
        self.assertNotIn("DEEPSEEK_API_KEY", kwargs["env"])
        runtime_home = kwargs["env"]["CODEX_HOME"]
        self.assertEqual(
            os.path.commonpath([runtime_home, os.getcwd()]),
            os.getcwd(),
        )
        self.assertNotEqual(
            os.path.commonpath([runtime_home, tempfile.gettempdir()]),
            tempfile.gettempdir(),
        )

    @patch("utils.ai_executors.codex_cli.run_process")
    @patch(
        "utils.ai_executors.codex_cli.shutil.which",
        return_value=r"C:\bin\codex.cmd",
    )
    def test_finalization_uses_native_output_schema(self, _which, run_process):
        def completed(*_args, **kwargs):
            kwargs["stdout_line_callback"](json.dumps({"type": "turn.started"}))
            kwargs["stdout_line_callback"](json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "message-1",
                    "type": "agent_message",
                    "text": '{"summary":"ok"}',
                },
            }))
            return ProcessResult(0, "", "")

        run_process.side_effect = completed
        executor = CodexCliExecutor(environ=self.environment())
        raw = executor.finalize(
            "prompt", r"C:\worktree", SCHEMA, 120, ExecutionBudget()
        )

        self.assertEqual(json.loads(raw), {"summary": "ok"})
        command = run_process.call_args.args[0]
        self.assertIn("--output-schema", command)
        schema_path = command[command.index("--output-schema") + 1]
        self.assertTrue(schema_path.endswith("review.schema.json"))

    @patch(
        "utils.ai_executors.codex_cli.shutil.which",
        return_value=r"C:\bin\codex.cmd",
    )
    def test_api_key_auth_uses_header_environment_reference(self, _which):
        executor = CodexCliExecutor(
            environ=self.environment(AI_AUTH_STYLE="api_key")
        )
        settings = executor._settings()
        values = executor._provider_config(settings)
        self.assertIn(
            'model_providers.jenkins.env_http_headers={"x-api-key"="AI_API_KEY"}',
            values,
        )
        self.assertNotIn("top-secret", " ".join(values))

    def test_shell_environment_excludes_api_key(self):
        executor = CodexCliExecutor(environ=self.environment())
        values = executor._provider_config(executor._settings())
        self.assertIn(
            "shell_environment_policy.ignore_default_excludes=false",
            values,
        )
        self.assertIn(
            'shell_environment_policy.filters.AI_API_KEY="exclude"',
            values,
        )

    def test_rejects_non_responses_protocol(self):
        executor = CodexCliExecutor(
            environ=self.environment(AI_API_PROTOCOL="openai_chat")
        )
        with self.assertRaisesRegex(RuntimeError, "openai_responses"):
            executor._settings()

    @patch("utils.ai_executors.codex_cli.run_process")
    @patch(
        "utils.ai_executors.codex_cli.shutil.which",
        return_value=r"C:\bin\codex.cmd",
    )
    def test_nonzero_exit_redacts_credential(self, _which, run_process):
        run_process.return_value = ProcessResult(
            1, "", "authentication failed for top-secret"
        )
        executor = CodexCliExecutor(environ=self.environment())
        with self.assertRaisesRegex(RuntimeError, r"authentication failed for \*\*\*"):
            executor.finalize(
                "prompt", r"C:\worktree", SCHEMA, 120, ExecutionBudget()
            )

    @patch("utils.ai_executors.codex_cli.run_process")
    @patch(
        "utils.ai_executors.codex_cli.shutil.which",
        return_value=r"C:\bin\codex.cmd",
    )
    def test_nonzero_exit_reports_structured_stdout_error(
        self, _which, run_process
    ):
        def completed(*_args, **kwargs):
            kwargs["stdout_line_callback"](json.dumps({
                "type": "error",
                "message": "request rejected for top-secret",
            }))
            return ProcessResult(1, "", "temporary helper warning")

        run_process.side_effect = completed
        executor = CodexCliExecutor(environ=self.environment())
        with self.assertRaisesRegex(
            RuntimeError,
            r"request rejected for \*\*\*",
        ) as raised:
            executor.investigate(
                "prompt", r"C:\worktree", 120, ExecutionBudget()
            )
        self.assertIn("temporary helper warning", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
