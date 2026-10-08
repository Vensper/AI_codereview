import pathlib
import re
import unittest


JENKINSFILE = pathlib.Path(__file__).resolve().parents[1] / "Jenkinsfile"


class JenkinsAiParameterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = JENKINSFILE.read_text(encoding="utf-8")

    def test_manual_build_exposes_only_six_parameters(self):
        block = self.text.split("    parameters {", 1)[1].split("\n    options {", 1)[0]
        self.assertEqual(
            re.findall(r"name:\s*'([^']+)'", block),
            [
                "ai_excutor",
                "PROJECT_ID",
                "dev_repository",
                "SOURCE_BRANCH",
                "MR_IID",
                "TARGET_BRANCH",
            ],
        )
        self.assertIn("name: 'ai_excutor',\n            defaultValue: 'cc'", block)
        self.assertIn("name: 'TARGET_BRANCH',\n            defaultValue: 'master'", block)

    def test_gateway_configuration_is_not_a_build_parameter(self):
        block = self.text.split("    parameters {", 1)[1].split("\n    options {", 1)[0]
        for name in (
            "AI_EXECUTOR",
            "AI_GATEWAY_RESPONSES_URL",
            "AI_GATEWAY_ANTHROPIC_URL",
            "AI_GATEWAY_CHAT_URL",
            "AI_OPENCODE_PROTOCOL",
            "AI_MODEL",
            "AI_AUTH_STYLE",
            "AI_CREDENTIAL_ID",
        ):
            self.assertNotIn(f"name: '{name}'", block)

    def test_gateway_route_is_resolved_before_credentials(self):
        resolver = self.text.index("python -u -m utils.resolve_ai_gateway")
        credentials = self.text.index("credentialsId: credentialId", resolver)
        self.assertLess(resolver, credentials)
        self.assertIn("gatewayRoute.size() != 5", self.text)

    def test_short_executor_codes_are_mapped_and_injected(self):
        self.assertIn("def executorNames = [cc: 'claude', cx: 'codex', oc: 'opencode']", self.text)
        self.assertIn("params.ai_excutor", self.text)
        self.assertIn('"AI_EXECUTOR=${executor}"', self.text)
        self.assertIn('"AI_API_PROTOCOL=${protocol}"', self.text)

    def test_cli_paths_and_versions_are_logged_before_credentials(self):
        checkout = self.text.index("stage('checkout')")
        diagnostics = self.text.index("echo AI CLI diagnostics:")
        checkout_logic = self.text.index("catchError", checkout)
        self.assertLess(diagnostics, checkout_logic)
        block = self.text[diagnostics:checkout_logic]
        self.assertIn("where codex", block)
        self.assertIn("codex --version", block)
        self.assertIn("where opencode", block)
        self.assertIn("opencode --version", block)
        self.assertIn("exit /b 0", block)


if __name__ == "__main__":
    unittest.main()
