import unittest
from unittest.mock import Mock, patch

from utils.ai_executors.base import (
    ExecutionBudget,
    ExecutionBudgetExceeded,
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
