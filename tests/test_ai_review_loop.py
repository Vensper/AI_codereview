import json
import unittest
from unittest.mock import patch

from utils.ai_review import (
    chat_messages_to_responses,
    load_review_schema,
    parse_review_with_retry,
    run_review_loop,
    validate_review_text,
)
from utils.ai_executors.base import ExecutionBudget


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
    return {"choices": [{"message": {
        "role": "assistant", "content": "draft review",
    }}]}


def review_value():
    return {
        "summary": "ok",
        "change_intent": "change",
        "changes": [],
        "comments": [],
    }


def structured_response(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return {
        "status": "completed",
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": text}],
        }],
    }


class ReviewLoopTests(unittest.TestCase):
    def test_tool_transcript_is_translated_for_responses_api(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "review"},
            tool_message()["choices"][0]["message"],
            {"role": "tool", "tool_call_id": "call-0", "content": "result"},
        ]
        instructions, items = chat_messages_to_responses(messages)
        self.assertEqual(instructions, "system")
        self.assertEqual([item["type"] for item in items], [
            "message", "function_call", "function_call_output",
        ])
        self.assertEqual(items[1]["call_id"], items[2]["call_id"])

    def test_final_review_uses_responses_json_schema(self):
        payload = {"messages": [
            {"role": "system", "content": "review system prompt"},
            {"role": "user", "content": "review this diff"},
        ]}
        with patch(
            "utils.ai_review.deepseek_responses_request",
            return_value=structured_response(review_value()),
        ) as request, patch("utils.ai_review.write_text"):
            review = parse_review_with_retry(payload, "test-key", "draft review")

        self.assertEqual(review["summary"], "ok")
        request.assert_called_once()
        sent = request.call_args.args[0]
        self.assertEqual(sent["text"]["format"]["type"], "json_schema")
        self.assertEqual(sent["text"]["format"]["name"], "code_review")
        self.assertIn("comments", sent["text"]["format"]["schema"]["required"])
        self.assertNotIn("tools", sent)

    def test_invalid_structured_output_retries_once_with_validation_error(self):
        invalid = {
            "summary": "ok",
            "change_intent": "change",
            "changes": [],
        }
        payload = {"messages": [{"role": "user", "content": "review"}]}
        with patch(
            "utils.ai_review.deepseek_responses_request",
            side_effect=[structured_response(invalid), structured_response(review_value())],
        ) as request, patch("utils.ai_review.write_text"):
            review = parse_review_with_retry(payload, "test-key", "draft")

        self.assertEqual(review["comments"], [])
        self.assertEqual(request.call_count, 2)
        retry_input = request.call_args.args[0]["input"]
        self.assertIn("missing comments", retry_input[-1]["content"])

    def test_invalid_structured_output_fails_after_one_retry(self):
        invalid = {"summary": "ok"}
        payload = {"messages": [{"role": "user", "content": "review"}]}
        with patch(
            "utils.ai_review.deepseek_responses_request",
            return_value=structured_response(invalid),
        ) as request, patch("utils.ai_review.write_text"):
            with self.assertRaisesRegex(RuntimeError, "structured output failed"):
                parse_review_with_retry(payload, "test-key", "draft")
        self.assertEqual(request.call_count, 2)

    def test_incomplete_structured_response_is_retried_once(self):
        incomplete = {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [],
        }
        payload = {"messages": [{"role": "user", "content": "review"}]}
        with patch(
            "utils.ai_review.deepseek_responses_request",
            side_effect=[incomplete, structured_response(review_value())],
        ) as request, patch("utils.ai_review.write_text"):
            review = parse_review_with_retry(payload, "test-key", "draft")
        self.assertEqual(review["summary"], "ok")
        self.assertEqual(request.call_count, 2)

    def test_local_schema_rejects_invalid_enum_and_extra_fields(self):
        value = review_value()
        value["comments"] = [{
            "title": "problem",
            "path": "a.py",
            "line": 1,
            "side": "new",
            "severity": "critical",
            "problem": "bad",
            "risk": "risk",
            "suggestion": "fix",
            "extra": "not allowed",
        }]
        with self.assertRaises(ValueError):
            validate_review_text(json.dumps(value), load_review_schema())

    def test_local_validation_rejects_whitespace_only_required_text(self):
        value = review_value()
        value["change_intent"] = "   "
        with self.assertRaisesRegex(ValueError, "cannot be blank"):
            validate_review_text(json.dumps(value), load_review_schema())

    def test_model_round_fuse_stops_investigation(self):
        choices = []

        def request(payload, _key):
            choices.append(payload["tool_choice"])
            return tool_message()

        payload = {"messages": [], "tool_choice": "auto"}
        with patch("utils.ai_review.deepseek_request", side_effect=request), patch(
            "utils.ai_review.agent_tools.execute_tool_call", return_value={"status": "ok"}
        ) as execute, patch("utils.ai_review.agent_tools.append_tool_trace"):
            self.assertEqual(
                run_review_loop(
                    payload,
                    "test-key",
                    budget=ExecutionBudget(max_rounds=3),
                ),
                "",
            )
        self.assertEqual(choices, ["auto"] * 3)
        self.assertEqual(execute.call_count, 3)

    def test_excess_calls_in_last_batch_get_replies_without_execution(self):
        choices = []

        def request(payload, _key):
            choices.append(payload["tool_choice"])
            return tool_message(2)

        payload = {"messages": [], "tool_choice": "auto"}
        with patch("utils.ai_review.deepseek_request", side_effect=request), patch(
            "utils.ai_review.agent_tools.execute_tool_call", return_value={"status": "ok"}
        ) as execute, patch("utils.ai_review.agent_tools.append_tool_trace") as trace:
            self.assertEqual(
                run_review_loop(
                    payload,
                    "test-key",
                    budget=ExecutionBudget(max_tool_calls=1),
                ),
                "",
            )
        self.assertEqual(choices, ["auto"])
        execute.assert_called_once()
        self.assertEqual(trace.call_args_list[-1].args[-1]["error_code"], "tool_budget_exhausted")
        self.assertEqual(len([m for m in payload["messages"] if m.get("role") == "tool"]), 2)

    def test_draft_is_kept_in_transcript_for_structured_finalizer(self):
        payload = {"messages": [], "tool_choice": "auto"}
        with patch("utils.ai_review.deepseek_request", return_value=final_message()):
            self.assertEqual(run_review_loop(payload, "test-key"), "draft review")
        self.assertEqual(payload["messages"][-1]["role"], "assistant")


if __name__ == "__main__":
    unittest.main()
