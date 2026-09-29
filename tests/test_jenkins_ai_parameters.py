import pathlib
import re
import unittest


JENKINSFILE = pathlib.Path(__file__).resolve().parents[1] / "Jenkinsfile"


class JenkinsAiParameterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = JENKINSFILE.read_text(encoding="utf-8")

    def executor_profile(self, name):
        match = re.search(
            rf"{name}: \[(.*?)\n {{28}}\](?:,|\n)",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(match, f"missing Jenkins executor profile: {name}")
        return match.group(1)

    def test_claude_is_first_executor_choice(self):
        self.assertIn(
            "name: 'AI_EXECUTOR',\n            choices: ['claude', 'codex', 'opencode']",
            self.text,
        )

    def test_executor_profiles_centralize_protocol_and_auth_matrix(self):
        self.assertIn("def executorProfiles = [", self.text)
        claude = self.executor_profile("claude")
        codex = self.executor_profile("codex")
        opencode = self.executor_profile("opencode")
        self.assertIn(
            "deepseekBaseUrl: 'https://api.deepseek.com/anthropic'",
            claude,
        )
        self.assertIn("deepseekProtocol: 'anthropic'", claude)
        self.assertIn("customDefaultProtocol: 'anthropic'", claude)
        self.assertIn("protocols: ['anthropic']", claude)
        self.assertIn("authStyles: ['bearer', 'api_key']", claude)
        self.assertIn("deepseekProtocol: 'openai_responses'", codex)
        self.assertIn("customDefaultProtocol: 'openai_responses'", codex)
        self.assertIn("protocols: ['openai_responses']", codex)
        self.assertIn("authStyles: ['bearer', 'api_key']", codex)
        self.assertIn("deepseekProtocol: 'openai_chat'", opencode)
        self.assertIn("customDefaultProtocol: 'openai_chat'", opencode)
        self.assertIn(
            "protocols: ['openai_chat', 'openai_responses']",
            opencode,
        )
        self.assertIn("authStyles: ['bearer']", opencode)

    def test_executor_profile_drives_defaults_and_validation(self):
        self.assertIn("baseUrl = profile.deepseekBaseUrl", self.text)
        self.assertIn("protocol = profile.deepseekProtocol", self.text)
        self.assertIn("protocol = profile.customDefaultProtocol", self.text)
        self.assertIn("protocol in profile.protocols", self.text)
        self.assertIn("authStyle in profile.authStyles", self.text)
        self.assertNotIn("def supportedProtocols = [", self.text)

    def test_selected_executor_and_protocol_are_injected(self):
        self.assertIn('"AI_EXECUTOR=${executor}"', self.text)
        self.assertIn('"AI_API_PROTOCOL=${protocol}"', self.text)
        self.assertNotIn("'AI_EXECUTOR=claude'", self.text)


if __name__ == "__main__":
    unittest.main()
