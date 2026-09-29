import unittest
from unittest.mock import Mock, patch

from utils.ai_executors.base import (
    CliExecutorBase,
    ExecutionBudget,
    ExecutionBudgetExceeded,
    ProcessResult,
    ReviewDeadline,
    ReviewDeadlineExceeded,
    terminate_process_tree,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ExampleCliExecutor(CliExecutorBase):
    name = "example"
    display_name = "Example CLI"
    command_environment = "EXAMPLE_COMMAND"
    default_command = "example"


class ExecutorLimitTests(unittest.TestCase):
    def test_deadline_reserves_at_most_120_seconds_for_finalization(self):
        clock = FakeClock()
        deadline = ReviewDeadline(clock=clock)

        self.assertEqual(deadline.investigation_remaining(), 480)
        clock.advance(470)
        self.assertEqual(deadline.begin_finalization(), 120)
        clock.advance(119)
        self.assertEqual(deadline.require_finalization_time(), 1)
        clock.advance(1)
        with self.assertRaises(ReviewDeadlineExceeded):
            deadline.require_finalization_time()

    def test_total_deadline_caps_late_finalization(self):
        clock = FakeClock()
        deadline = ReviewDeadline(clock=clock)

        clock.advance(490)
        self.assertEqual(deadline.begin_finalization(), 110)

    def test_emergency_fuses_allow_exactly_64_rounds_and_128_tools(self):
        budget = ExecutionBudget()

        budget.consume_round(64)
        budget.consume_tool_calls(128)
        self.assertEqual(budget.model_rounds, 64)
        self.assertEqual(budget.tool_calls, 128)
        with self.assertRaises(ExecutionBudgetExceeded):
            budget.consume_round()
        with self.assertRaises(ExecutionBudgetExceeded):
            budget.consume_tool_calls()

    @patch("utils.ai_executors.base.time.monotonic", return_value=12.5)
    def test_shared_cli_base_records_investigation_trace(self, _clock):
        executor = ExampleCliExecutor(environ={"PATH": r"C:\bin"})
        budget = ExecutionBudget()
        budget.consume_round()

        text = executor._complete_investigation(
            ProcessResult(0, "", ""),
            "draft",
            [],
            budget,
            started=10.0,
        )

        self.assertEqual(text, "draft")
        self.assertEqual(
            executor.get_trace(),
            [
                {
                    "executor": "example",
                    "phase": "investigation",
                    "status": "ok",
                    "return_code": 0,
                    "duration_seconds": 2.5,
                    "model_rounds": 1,
                    "tool_calls": 0,
                    "max_model_rounds": 64,
                    "max_tool_calls": 128,
                }
            ],
        )

    def test_shared_cli_base_finalization_timeout_is_traced_and_fatal(self):
        executor = ExampleCliExecutor(environ={"PATH": r"C:\bin"})
        completed = ProcessResult(-1, "", "", timed_out=True)

        with self.assertRaisesRegex(
            ReviewDeadlineExceeded, "Example CLI finalization"
        ):
            executor._complete_finalization(
                completed,
                "",
                [],
                ExecutionBudget(),
                started=0.0,
                attempt=1,
            )

        trace = executor.get_trace()[0]
        self.assertEqual(trace["phase"], "finalization")
        self.assertEqual(trace["attempt"], 2)
        self.assertEqual(trace["status"], "timeout")

    def test_shared_cli_base_redacts_finalization_failure(self):
        executor = ExampleCliExecutor(environ={"PATH": r"C:\bin"})
        completed = ProcessResult(1, "", "credential=top-secret")

        with self.assertRaisesRegex(RuntimeError, r"credential=\*\*\*"):
            executor._complete_finalization(
                completed,
                "",
                ["top-secret"],
                ExecutionBudget(),
                started=0.0,
                attempt=0,
            )

    @patch("utils.ai_executors.base.subprocess.run")
    @patch("utils.ai_executors.base.os.name", "nt")
    def test_windows_timeout_kills_the_exact_process_tree(self, run):
        process = Mock()
        process.pid = 4321
        process.poll.return_value = None

        terminate_process_tree(process)

        self.assertEqual(
            run.call_args.args[0],
            ["taskkill", "/PID", "4321", "/T", "/F"],
        )
        process.wait.assert_called_once_with(timeout=5)


if __name__ == "__main__":
    unittest.main()
