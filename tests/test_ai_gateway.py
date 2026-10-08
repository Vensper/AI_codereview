import os
import tempfile
import unittest
from unittest.mock import patch

from utils.resolve_ai_gateway import main, resolve_gateway_route


class AiGatewayRouteTests(unittest.TestCase):
    def environment(self, **overrides):
        value = {
            "AI_EXECUTOR": "codex",
            "AI_GATEWAY_RESPONSES_URL": "https://gateway.example/v1/",
            "AI_GATEWAY_ANTHROPIC_URL": "https://gateway.example/anthropic/",
            "AI_GATEWAY_CHAT_URL": "https://gateway.example/v1/",
            "AI_OPENCODE_PROTOCOL": "openai_responses",
            "AI_MODEL": "qwen-coder",
            "AI_AUTH_STYLE": "bearer",
            "AI_CREDENTIAL_ID": "ai-model-gateway-key",
        }
        value.update(overrides)
        return value

    def test_codex_always_uses_responses_route(self):
        route = resolve_gateway_route(self.environment(AI_EXECUTOR="codex"))
        self.assertEqual(route.protocol, "openai_responses")
        self.assertEqual(route.base_url, "https://gateway.example/v1")
        self.assertEqual(route.model, "qwen-coder")
        self.assertEqual(route.auth_style, "bearer")
        self.assertEqual(route.credential_id, "ai-model-gateway-key")

    def test_claude_always_uses_anthropic_route(self):
        route = resolve_gateway_route(self.environment(AI_EXECUTOR="claude"))
        self.assertEqual(route.protocol, "anthropic")
        self.assertEqual(route.base_url, "https://gateway.example/anthropic")

    def test_opencode_can_use_responses_or_chat(self):
        responses = resolve_gateway_route(self.environment(AI_EXECUTOR="opencode"))
        chat = resolve_gateway_route(
            self.environment(
                AI_EXECUTOR="opencode",
                AI_OPENCODE_PROTOCOL="openai_chat",
                AI_GATEWAY_CHAT_URL="https://chat-gateway.example/v1",
            )
        )
        self.assertEqual(responses.protocol, "openai_responses")
        self.assertEqual(chat.protocol, "openai_chat")
        self.assertEqual(chat.base_url, "https://chat-gateway.example/v1")

    def test_only_selected_protocol_url_can_be_overridden(self):
        route = resolve_gateway_route(
            {
                "AI_EXECUTOR": "codex",
                "AI_GATEWAY_RESPONSES_URL": "https://gateway.example/v1",
                "AI_MODEL": "model-alias",
            }
        )
        self.assertEqual(route.model, "model-alias")

    def test_blank_hidden_configuration_uses_safe_current_defaults(self):
        expected = {
            "claude": ("anthropic", "https://api.deepseek.com/anthropic", "deepseek-flash[1m]"),
            "codex": ("openai_responses", "https://api.deepseek.com", "deepseek-flash"),
            "opencode": ("openai_chat", "https://api.deepseek.com", "deepseek-flash"),
        }
        for executor, values in expected.items():
            with self.subTest(executor=executor):
                route = resolve_gateway_route({"AI_EXECUTOR": executor})
                self.assertEqual(
                    (route.protocol, route.base_url, route.model), values
                )
                self.assertEqual(route.auth_style, "bearer")
                self.assertEqual(route.credential_id, "deepseek-api-key")

    def test_executor_specific_hidden_values_override_common_values(self):
        route = resolve_gateway_route(
            self.environment(
                AI_CODEX_BASE_URL="https://codex-gateway.example/responses",
                AI_CODEX_MODEL="codex-route",
                AI_CODEX_AUTH_STYLE="api_key",
                AI_CODEX_CREDENTIAL_ID="ai-model-codex-key",
            )
        )
        self.assertEqual(
            route.base_url, "https://codex-gateway.example/responses"
        )
        self.assertEqual(route.model, "codex-route")
        self.assertEqual(route.auth_style, "api_key")
        self.assertEqual(route.credential_id, "ai-model-codex-key")

    def test_each_executor_can_use_an_independent_compatible_api(self):
        cases = [
            (
                "claude",
                "AI_CLAUDE_BASE_URL",
                "https://models.example/anthropic",
                "AI_CLAUDE_MODEL",
                "qwen-coder-anthropic",
                "anthropic",
            ),
            (
                "codex",
                "AI_CODEX_BASE_URL",
                "https://models.example/v1",
                "AI_CODEX_MODEL",
                "gpt-coding-route",
                "openai_responses",
            ),
            (
                "opencode",
                "AI_OPENCODE_BASE_URL",
                "https://models.example/openai/v1",
                "AI_OPENCODE_MODEL",
                "kimi-coding-route",
                "openai_chat",
            ),
        ]
        for executor, url_name, url, model_name, model, protocol in cases:
            with self.subTest(executor=executor):
                route = resolve_gateway_route(
                    {
                        "AI_EXECUTOR": executor,
                        url_name: url,
                        model_name: model,
                    }
                )
                self.assertEqual(route.base_url, url)
                self.assertEqual(route.model, model)
                self.assertEqual(route.protocol, protocol)

    def test_rejects_http_credentials_query_and_control_characters(self):
        invalid_urls = [
            "http://gateway.example/v1",
            "https://user:secret@gateway.example/v1",
            "https://gateway.example/v1?key=secret",
            "https://gateway.example/v1#fragment",
            "https://gateway.example/v1 bad",
        ]
        for value in invalid_urls:
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    resolve_gateway_route(
                        self.environment(AI_GATEWAY_RESPONSES_URL=value)
                    )

    def test_rejects_unknown_executor_and_opencode_protocol(self):
        with self.assertRaisesRegex(RuntimeError, "Unsupported AI_EXECUTOR"):
            resolve_gateway_route(self.environment(AI_EXECUTOR="unknown"))
        with self.assertRaisesRegex(RuntimeError, "AI_OPENCODE_PROTOCOL"):
            resolve_gateway_route(
                self.environment(
                    AI_EXECUTOR="opencode",
                    AI_OPENCODE_PROTOCOL="anthropic",
                )
            )

    def test_rejects_invalid_auth_style_and_credential_namespace(self):
        with self.assertRaisesRegex(RuntimeError, "AI_AUTH_STYLE"):
            resolve_gateway_route(self.environment(AI_AUTH_STYLE="token"))
        with self.assertRaisesRegex(RuntimeError, "AI_CREDENTIAL_ID"):
            resolve_gateway_route(
                self.environment(AI_CREDENTIAL_ID="gitlab-review-api")
            )
        with self.assertRaisesRegex(RuntimeError, "OpenCode"):
            resolve_gateway_route(
                self.environment(AI_EXECUTOR="opencode", AI_AUTH_STYLE="api_key")
            )

    def test_cli_writes_non_secret_route_file(self):
        environ = self.environment()
        environ["AI_API_KEY"] = "must-not-be-written"
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "route.txt")
            with patch.dict(os.environ, environ, clear=True), patch(
                "sys.argv", ["resolve_ai_gateway", output]
            ):
                main()
            with open(output, encoding="utf-8") as handle:
                text = handle.read()
        self.assertEqual(
            text,
            "openai_responses\nhttps://gateway.example/v1\nqwen-coder\n"
            "bearer\nai-model-gateway-key\n",
        )
        self.assertNotIn("must-not-be-written", text)


if __name__ == "__main__":
    unittest.main()
