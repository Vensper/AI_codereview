import pathlib
import unittest


JENKINSFILE = pathlib.Path(__file__).resolve().parents[1] / "Jenkinsfile"


class JenkinsAiParameterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = JENKINSFILE.read_text(encoding="utf-8")

    def test_claude_is_first_executor_choice(self):
        self.assertIn(
            "name: 'AI_EXECUTOR',\n            choices: ['claude', 'codex', 'opencode']",
            self.text,
        )

    def test_deepseek_protocol_matrix_is_explicit(self):
        self.assertIn("baseUrl = 'https://api.deepseek.com/anthropic'", self.text)
        self.assertIn("protocol = 'anthropic'", self.text)
        self.assertIn("protocol = 'openai_responses'", self.text)
        self.assertIn("protocol = 'openai_chat'", self.text)

    def test_selected_executor_and_protocol_are_injected(self):
        self.assertIn('"AI_EXECUTOR=${executor}"', self.text)
        self.assertIn('"AI_API_PROTOCOL=${protocol}"', self.text)
        self.assertNotIn("'AI_EXECUTOR=claude'", self.text)

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
