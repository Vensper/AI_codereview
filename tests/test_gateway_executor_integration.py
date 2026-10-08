import json
import unittest
from unittest.mock import patch

from utils.ai_executors.base import ExecutionBudget, ProcessResult
from utils.ai_executors.claude_code import ClaudeCodeExecutor
from utils.ai_executors.codex_cli import CodexCliExecutor
from utils.ai_executors.opencode_cli import OpenCodeCliExecutor
from utils.resolve_ai_gateway import resolve_gateway_route


SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


class GatewayExecutorIntegrationTests(unittest.TestCase):
    def gateway_environment(self, executor, opencode_protocol="openai_responses"):
        return {
            "AI_EXECUTOR": executor,
            "AI_GATEWAY_RESPONSES_URL": "https://gateway.example/v1",
            "AI_GATEWAY_ANTHROPIC_URL": "https://gateway.example/anthropic",
            "AI_GATEWAY_CHAT_URL": "https://gateway.example/v1",
            "AI_OPENCODE_PROTOCOL": opencode_protocol,
            "AI_MODEL": "shared-model-route",
            "AI_AUTH_STYLE": "bearer",
            "AI_CREDENTIAL_ID": "ai-model-gateway-key",
        }

    def executor_environment(self, executor, opencode_protocol="openai_responses"):
        route = resolve_gateway_route(
            self.gateway_environment(executor, opencode_protocol)
        )
        return route, {
            "PATH": r"C:\bin",
            "AI_API_KEY": "gateway-secret",
            "AI_BASE_URL": route.base_url,
            "AI_MODEL_OVERRIDE": route.model,
            "AI_API_PROTOCOL": route.protocol,
            "AI_AUTH_STYLE": route.auth_style,
        }

    def test_codex_route_reaches_codex_provider_configuration(self):
        route, environ = self.executor_environment("codex")

        def completed(*_args, **kwargs):
            kwargs["stdout_line_callback"](json.dumps({"type": "turn.started"}))
            kwargs["stdout_line_callback"](
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "answer",
                            "type": "agent_message",
                            "text": '{"summary":"ok"}',
                        },
                    }
                )
            )
            return ProcessResult(0, "", "")

        with patch(
            "utils.ai_executors.codex_cli.shutil.which",
            return_value=r"C:\bin\codex.cmd",
        ), patch(
            "utils.ai_executors.codex_cli.run_process", side_effect=completed
        ) as run_process:
            raw = CodexCliExecutor(environ=environ).finalize(
                "prompt", r"C:\worktree", SCHEMA, 120, ExecutionBudget()
            )

        self.assertEqual(json.loads(raw), {"summary": "ok"})
        command = run_process.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], route.model)
        self.assertIn(
            f'model_providers.jenkins.base_url="{route.base_url}"', command
        )

    def test_claude_route_reaches_anthropic_environment(self):
        route, environ = self.executor_environment("claude")
        output = json.dumps(
            {"structured_output": {"summary": "ok"}}, ensure_ascii=False
        )
        with patch(
            "utils.ai_executors.claude_code.shutil.which",
            return_value=r"C:\bin\claude.cmd",
        ), patch(
            "utils.ai_executors.claude_code.run_process",
            return_value=ProcessResult(0, output, ""),
        ) as run_process:
            raw = ClaudeCodeExecutor(environ=environ).finalize(
                "prompt", r"C:\worktree", SCHEMA, 120, ExecutionBudget()
            )

        self.assertEqual(json.loads(raw), {"summary": "ok"})
        child_env = run_process.call_args.kwargs["env"]
        self.assertEqual(child_env["ANTHROPIC_BASE_URL"], route.base_url)
        self.assertEqual(child_env["ANTHROPIC_AUTH_TOKEN"], "gateway-secret")

    def test_opencode_route_supports_both_gateway_protocols(self):
        for protocol in ("openai_responses", "openai_chat"):
            with self.subTest(protocol=protocol):
                route, environ = self.executor_environment("opencode", protocol)

                def completed(*_args, **kwargs):
                    kwargs["stdout_line_callback"](
                        json.dumps(
                            {
                                "type": "text",
                                "part": {
                                    "id": "answer",
                                    "type": "text",
                                    "text": '{"summary":"ok"}',
                                },
                            }
                        )
                    )
                    return ProcessResult(0, "", "")

                with patch(
                    "utils.ai_executors.opencode_cli.shutil.which",
                    return_value=r"C:\bin\opencode.cmd",
                ), patch(
                    "utils.ai_executors.opencode_cli.run_process",
                    side_effect=completed,
                ) as run_process:
                    raw = OpenCodeCliExecutor(environ=environ).finalize(
                        "prompt", r"C:\worktree", SCHEMA, 120, ExecutionBudget()
                    )

                self.assertEqual(json.loads(raw), {"summary": "ok"})
                child_env = run_process.call_args.kwargs["env"]
                config = json.loads(child_env["OPENCODE_CONFIG_CONTENT"])
                expected_package = (
                    "@ai-sdk/openai"
                    if protocol == "openai_responses"
                    else "@ai-sdk/openai-compatible"
                )
                self.assertEqual(
                    config["provider"]["jenkins"]["npm"], expected_package
                )
                self.assertEqual(
                    config["provider"]["jenkins"]["options"]["baseURL"],
                    route.base_url,
                )


if __name__ == "__main__":
    unittest.main()
