import json
import unittest

from utils.review_contract import generate_review_with_retry, load_review_schema


class ReviewContractTests(unittest.TestCase):
    def test_all_executors_share_the_same_validation_retry(self):
        calls = []
        writes = {}
        valid = {
            "summary": "ok",
            "change_intent": "change",
            "changes": [],
            "comments": [],
        }

        def generate(attempt, previous_output, validation_error):
            calls.append((attempt, previous_output, validation_error))
            if attempt == 0:
                return '{"summary":"missing required fields"}'
            return json.dumps(valid)

        result = generate_review_with_retry(
            generate,
            load_review_schema(),
            write_text_fn=lambda path, value: writes.__setitem__(path, value),
        )

        self.assertEqual(result, valid)
        self.assertEqual(len(calls), 2)
        self.assertIn("missing change_intent", calls[1][2])
        self.assertIn("review-raw-structured-initial.txt", writes)
        self.assertEqual(writes["review-validation.txt"], "valid\n")


if __name__ == "__main__":
    unittest.main()
