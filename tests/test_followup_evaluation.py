"""Проверяет, что follow-up benchmark отделён от обучающего web-корпуса."""

import unittest

from ai_core.training_corpus import load_training_pairs_for_topic
from evaluation.run_followup_evaluation import load_followup_cases


class FollowupEvaluationTests(unittest.TestCase):
    """Защищает честность отдельной проверки памяти диалога."""

    def test_followup_cases_are_valid_and_not_in_web_training(self) -> None:
        cases = load_followup_cases()
        training_prompts = {
            pair.prompt.casefold().strip()
            for pair in load_training_pairs_for_topic("web")
        }
        evaluation_questions = {
            str(case["messages"][0]["content"]).casefold().strip()
            for case in cases
        }
        evaluation_questions.update(
            str(case["prompt"]).casefold().strip()
            for case in cases
        )

        self.assertEqual(len(cases), 12)
        self.assertEqual(len(evaluation_questions), 24)
        self.assertTrue(evaluation_questions.isdisjoint(training_prompts))
        self.assertTrue(all(case["completion"].strip() for case in cases))


if __name__ == "__main__":
    unittest.main()
