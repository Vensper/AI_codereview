import unittest
from unittest.mock import patch

from utils.ai_review import run_review_loop


def tool_message(count=1):
    return {"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [
            {"id": f"call-{i}", "type": "function", "function": {
                "name": "search_repository", "arguments": "{}",
            }} for i in range(count)
        ],
    }}]}


def final_message():
    return {"choices": [{"message": {"role": "assistant", "content": '{"summary":"ok"}'}}]}


class ReviewLoopTests(unittest.TestCase):
    def test_tool_round_limit_requests_final_answer_without_tools(self):
        choices = []

        def request(payload, _key):
            choices.append(payload["tool_choice"])
            return tool_message() if len(choices) <= 8 else final_message()

        payload = {"messages": [], "tool_choice": "auto"}
        with patch("utils.ai_review.deepseek_request", side_effect=request), patch(
            "utils.ai_review.agent_tools.execute_tool_call", return_value={"status": "ok"}
        ) as execute, patch("utils.ai_review.agent_tools.append_tool_trace"):
            self.assertEqual(run_review_loop(payload, "test-key"), '{"summary":"ok"}')
        self.assertEqual(choices, ["auto"] * 8 + ["none"])
        self.assertEqual(execute.call_count, 8)

    def test_excess_calls_in_last_batch_get_replies_without_execution(self):
        choices = []

        def request(payload, _key):
            choices.append(payload["tool_choice"])
            return tool_message(2) if len(choices) == 1 else final_message()

        payload = {"messages": [], "tool_choice": "auto"}
        with patch("utils.ai_review.MAX_TOOL_CALLS", 1), patch(
            "utils.ai_review.deepseek_request", side_effect=request
        ), patch(
            "utils.ai_review.agent_tools.execute_tool_call", return_value={"status": "ok"}
        ) as execute, patch("utils.ai_review.agent_tools.append_tool_trace") as trace:
            self.assertEqual(run_review_loop(payload, "test-key"), '{"summary":"ok"}')
        self.assertEqual(choices, ["auto", "none"])
        execute.assert_called_once()
        self.assertEqual(trace.call_args_list[-1].args[-1]["error_code"], "tool_budget_exhausted")
        self.assertEqual(len([m for m in payload["messages"] if m.get("role") == "tool"]), 2)


if __name__ == "__main__":
    unittest.main()
