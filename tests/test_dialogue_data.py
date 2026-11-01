"""Проверки отдельного датасета многоходового обучения."""

from pathlib import Path
import unittest

from ai_core.dialogue_data import (
    dialogue_dataset_fingerprint,
    dialogue_examples_to_pairs,
    format_dialogue_prompt,
    load_dialogue_examples,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIALOGUE_DATASET_PATH = PROJECT_ROOT / "training_data" / "dialogue_examples.jsonl"


class DialogueDataTests(unittest.TestCase):
    def test_dialogue_dataset_is_valid_and_has_expected_size(self) -> None:
        examples = load_dialogue_examples(DIALOGUE_DATASET_PATH)

        self.assertEqual(len(examples), 20)
        self.assertTrue(all(example.messages for example in examples))
        self.assertTrue(all(example.prompt for example in examples))
        self.assertTrue(all(example.completion for example in examples))

    def test_dialogue_prompt_uses_same_context_format_as_api(self) -> None:
        example = load_dialogue_examples(DIALOGUE_DATASET_PATH)[0]

        prompt = format_dialogue_prompt(example)

        self.assertIn("История диалога:", prompt)
        self.assertIn("Пользователь:", prompt)
        self.assertIn("Ассистент:", prompt)
        self.assertIn("Новый вопрос:", prompt)
        self.assertTrue(prompt.endswith(example.prompt))

    def test_dialogue_conversion_and_fingerprint_are_deterministic(self) -> None:
        examples = load_dialogue_examples(DIALOGUE_DATASET_PATH)

        pairs = dialogue_examples_to_pairs(examples)

        self.assertEqual(len(pairs), len(examples))
        self.assertEqual(
            dialogue_dataset_fingerprint(examples),
            dialogue_dataset_fingerprint(load_dialogue_examples(DIALOGUE_DATASET_PATH)),
        )
        self.assertTrue(pairs[0].completion)


if __name__ == "__main__":
    unittest.main()
