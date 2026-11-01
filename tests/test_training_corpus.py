"""Проверки объединения исходных и вручную составленных источников."""

import unittest

from ai_core.data_quality import analyze_dataset_quality, filter_pairs_by_topic
from ai_core.dialogue_data import dialogue_examples_to_pairs, load_dialogue_examples
from ai_core.training_corpus import (
    WEB_DIALOGUE_DATASET_PATH,
    WEB_DIALOGUE_EXTENDED_DATASET_PATH,
    load_primary_training_pairs,
    load_training_corpus,
    load_training_pairs_for_topic,
    load_web_foundation_pairs,
)


class TrainingCorpusTests(unittest.TestCase):
    """Защищает исходный JSONL от неявной замены дополнительным набором."""

    def test_web_topic_contains_complete_manual_web_foundation_dataset(self) -> None:
        primary_pairs = load_primary_training_pairs()
        foundation_pairs = load_web_foundation_pairs()
        web_pairs = load_training_pairs_for_topic("web")

        self.assertGreater(len(foundation_pairs), 0)
        self.assertEqual(
            web_pairs,
            [*filter_pairs_by_topic(primary_pairs, "web"), *foundation_pairs],
        )

    def test_all_topic_combines_sources_without_changing_primary_order(self) -> None:
        primary_pairs = load_primary_training_pairs()
        foundation_pairs = load_web_foundation_pairs()

        self.assertEqual(load_training_corpus(), [*primary_pairs, *foundation_pairs])
        self.assertEqual(
            load_training_pairs_for_topic("all"),
            [*primary_pairs, *foundation_pairs],
        )

    def test_non_web_topics_do_not_receive_manual_web_examples(self) -> None:
        primary_pairs = load_primary_training_pairs()

        self.assertEqual(
            load_training_pairs_for_topic("python"),
            filter_pairs_by_topic(primary_pairs, "python"),
        )

    def test_manual_web_examples_do_not_add_new_answer_conflicts(self) -> None:
        report = analyze_dataset_quality(load_training_corpus())

        self.assertEqual(report.conflicting_prompt_groups, 1)
        self.assertEqual(
            report.conflicts[0].normalized_prompt,
            "как получить атрибут элемента",
        )

    def test_web_dialogue_dataset_is_valid_and_non_empty(self) -> None:
        examples = load_dialogue_examples(WEB_DIALOGUE_DATASET_PATH)
        pairs = dialogue_examples_to_pairs(examples)

        self.assertGreaterEqual(len(examples), 20)
        self.assertEqual(len(examples), len(pairs))
        self.assertTrue(all("История диалога:" in pair.prompt for pair in pairs))

    def test_extended_web_dialogue_dataset_is_valid_and_non_empty(self) -> None:
        examples = load_dialogue_examples(WEB_DIALOGUE_EXTENDED_DATASET_PATH)
        pairs = dialogue_examples_to_pairs(examples)

        self.assertGreaterEqual(len(examples), 40)
        self.assertEqual(len(examples), len(pairs))
        self.assertTrue(all("История диалога:" in pair.prompt for pair in pairs))


if __name__ == "__main__":
    unittest.main()
