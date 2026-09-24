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


if __name__ == "__main__":
    unittest.main()
