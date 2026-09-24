import json
import unittest
from unittest.mock import patch

from utils.ai_executors import DEFAULT_EXECUTOR, executor_name_from_env
from utils.ai_executors.base import (
    ExecutionBudget,
    ExecutionBudgetExceeded,
    ProcessResult,
    ReviewDeadlineExceeded,
)
from utils.ai_executors.claude_code import ClaudeCodeExecutor


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "change_intent": {"type": "string"},
        "changes": {"type": "array"},
        "comments": {"type": "array"},
    },
    "required": ["summary", "change_intent", "changes", "comments"],
}


class ClaudeCodeExecutorTests(unittest.TestCase):
    def test_claude_is_the_default_executor(self):
        self.assertEqual(DEFAULT_EXECUTOR, "claude")
        self.assertEqual(executor_name_from_env({}), "claude")
        self.assertEqual(executor_name_from_env({"AI_EXECUTOR": "deepseek"}), "deepseek")

    @patch("utils.ai_executors.claude_code.run_process")
    @patch("utils.ai_executors.claude_code.shutil.which", return_value=r"C:\bin\claude.cmd")
    def test_investigation_is_read_only_and_counts_observable_usage(
        self, _which, run_process
    ):
        def completed(*_args, **kwargs):
            callback = kwargs["stdout_line_callback"]
            callback(json.dumps({
                "type": "assistant",
                "message": {
                    "id": "message-1",
                    "content": [
                        {"type": "tool_use", "id": "tool-1"},
                        {"type": "text", "text": "draft"},
                    ],
                },
            }))
            callback(json.dumps({"type": "tool_use", "id": "tool-1"}))
            return ProcessResult(0, "", "")

        run_process.side_effect = completed
        executor = ClaudeCodeExecutor(
            environ={"PATH": r"C:\bin", "AI_API_KEY": "key"}
        )
        budget = ExecutionBudget()

        draft = executor.investigate(
            "investigate", r"C:\worktree", timeout_seconds=480, budget=budget
        )

        self.assertEqual(draft, "draft")
        self.assertEqual(budget.model_rounds, 1)
        self.assertEqual(budget.tool_calls, 1)
        command = run_process.call_args.args[0]
        self.assertIn("stream-json", command)
        tools_index = command.index("--tools")
        self.assertEqual(
            command[tools_index + 1:tools_index + 4], ["Read", "Glob", "Grep"]
        )
        self.assertIn("Read(.git/**)", command)

    @patch("utils.ai_executors.claude_code.run_process")
    @patch("utils.ai_executors.claude_code.shutil.which", return_value=r"C:\bin\claude.cmd")
    def test_finalization_disables_tools_and_uses_schema_without_secret_leak(
        self, _which, run_process
    ):
        review = {
            "summary": "ok",
            "change_intent": "change",
            "changes": [],
            "comments": [],
        }
        run_process.return_value = ProcessResult(
            0, json.dumps({"structured_output": review}), ""
        )
        executor = ClaudeCodeExecutor(
            environ={
                "PATH": r"C:\bin",
                "AI_API_KEY": "top-secret",
                "AI_AUTH_STYLE": "bearer",
                "AI_BASE_URL": "https://gateway.example",
                "AI_MODEL_OVERRIDE": "review-model",
                "DEEPSEEK_API_KEY": "must-not-leak",
            }
        )

        raw = executor.finalize(
            "final prompt",
            r"C:\worktree",
            SCHEMA,
            timeout_seconds=120,
            budget=ExecutionBudget(),
        )

        self.assertEqual(json.loads(raw), review)
        command = run_process.call_args.args[0]
        tools_index = command.index("--tools")
        self.assertEqual(command[tools_index + 1], "")
        self.assertIn("--json-schema", command)
        self.assertEqual(command[-2:], ["--model", "review-model"])
        kwargs = run_process.call_args.kwargs
        self.assertEqual(kwargs["input_text"], "final prompt")
        self.assertEqual(kwargs["cwd"], r"C:\worktree")
        self.assertEqual(kwargs["env"]["ANTHROPIC_AUTH_TOKEN"], "top-secret")
        self.assertEqual(
            kwargs["env"]["ANTHROPIC_BASE_URL"], "https://gateway.example"
        )
        self.assertNotIn("DEEPSEEK_API_KEY", kwargs["env"])

    def test_generic_key_defaults_to_anthropic_api_key(self):
        executor = ClaudeCodeExecutor(environ={"AI_API_KEY": "key"})
        environment, _secrets = executor._environment()
        self.assertEqual(environment["ANTHROPIC_API_KEY"], "key")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", environment)

    @patch("utils.ai_executors.claude_code.run_process")
    @patch("utils.ai_executors.claude_code.shutil.which", return_value=r"C:\bin\claude.cmd")
    def test_nonzero_final_exit_redacts_credential(self, _which, run_process):
        run_process.return_value = ProcessResult(
            1, "", "authentication failed for top-secret"
        )
        executor = ClaudeCodeExecutor(
            environ={"PATH": r"C:\bin", "AI_API_KEY": "top-secret"}
        )

        with self.assertRaisesRegex(RuntimeError, r"authentication failed for \*\*\*"):
            executor.finalize(
                "prompt", r"C:\worktree", SCHEMA, 120, ExecutionBudget()
            )

    @patch("utils.ai_executors.claude_code.run_process")
    @patch("utils.ai_executors.claude_code.shutil.which", return_value=r"C:\bin\claude.cmd")
    def test_investigation_timeout_continues_with_partial_draft(
        self, _which, run_process
    ):
        def completed(*_args, **kwargs):
            kwargs["stdout_line_callback"](json.dumps({
                "type": "assistant",
                "message": {
                    "id": "message-1",
                    "content": [{"type": "text", "text": "partial"}],
                },
            }))
            return ProcessResult(1, "", "", timed_out=True)

        run_process.side_effect = completed
        executor = ClaudeCodeExecutor(
            environ={"PATH": r"C:\bin", "AI_API_KEY": "key"}
        )
        draft = executor.investigate(
            "prompt", r"C:\worktree", 480, ExecutionBudget()
        )
        self.assertEqual(draft, "partial")
        self.assertEqual(
            executor.get_trace()[0]["status"], "investigation_timeout"
        )

    @patch("utils.ai_executors.claude_code.run_process")
    @patch("utils.ai_executors.claude_code.shutil.which", return_value=r"C:\bin\claude.cmd")
    def test_finalization_timeout_is_fatal(self, _which, run_process):
        run_process.return_value = ProcessResult(1, "", "", timed_out=True)
        executor = ClaudeCodeExecutor(
            environ={"PATH": r"C:\bin", "AI_API_KEY": "key"}
        )

        with self.assertRaises(ReviewDeadlineExceeded):
            executor.finalize(
                "prompt", r"C:\worktree", SCHEMA, 120, ExecutionBudget()
            )

    @patch("utils.ai_executors.claude_code.run_process")
    @patch("utils.ai_executors.claude_code.shutil.which", return_value=r"C:\bin\claude.cmd")
    def test_observer_fuse_ends_investigation(self, _which, run_process):
        budget = ExecutionBudget(max_rounds=1, max_tool_calls=128)

        def completed(*_args, **kwargs):
            callback = kwargs["stdout_line_callback"]
            callback(json.dumps({
                "type": "assistant",
                "message": {"id": "one", "content": []},
            }))
            try:
                callback(json.dumps({
                    "type": "assistant",
                    "message": {"id": "two", "content": []},
                }))
            except ExecutionBudgetExceeded as exc:
                return ProcessResult(1, "", "", monitor_error=exc)
            self.fail("round fuse did not trigger")

        run_process.side_effect = completed
        executor = ClaudeCodeExecutor(
            environ={"PATH": r"C:\bin", "AI_API_KEY": "key"}
        )
        self.assertEqual(
            executor.investigate("prompt", r"C:\worktree", 480, budget), ""
        )
        self.assertEqual(executor.get_trace()[0]["status"], "investigation_fuse")


if __name__ == "__main__":
    unittest.main()
