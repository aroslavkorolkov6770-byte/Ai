"""Проверки неразрушающего аудита качества JSONL-данных."""

import unittest

from ai_core.data_quality import (
    analyze_dataset_quality,
    analyze_topic_distribution,
    classify_topic,
    filter_pairs_by_topic,
    render_quality_report,
    render_topic_distribution,
)
from ai_core.training_data import TrainingPair


class DatasetQualityTests(unittest.TestCase):
    def test_report_separates_duplicates_from_conflicts(self) -> None:
        pairs = [
            TrainingPair("Как начать в C++?", "ответ C++"),
            TrainingPair("Как начать в C#?", "ответ C#"),
            TrainingPair("Как получить атрибут?", "первый вариант"),
            TrainingPair("Как получить атрибут?", "второй вариант"),
            TrainingPair("Как создать список?", "используй квадратные скобки"),
            TrainingPair("Как создать список?", "используй квадратные скобки"),
        ]

        report = analyze_dataset_quality(pairs)

        self.assertEqual(report.total_examples, 6)
        self.assertEqual(report.unique_normalized_prompts, 4)
        self.assertEqual(report.duplicate_prompt_groups, 2)
        self.assertEqual(report.duplicate_rows, 2)
        self.assertEqual(report.conflicting_prompt_groups, 1)
        self.assertEqual(report.conflicts[0].normalized_prompt, "как получить атрибут")

    def test_markdown_contains_manual_conflict_details(self) -> None:
        report = analyze_dataset_quality(
            [
                TrainingPair("Один вопрос", "Первый ответ"),
                TrainingPair("Один вопрос", "Второй ответ"),
            ]
        )

        markdown = render_quality_report(report)

        self.assertIn("Вопросы, требующие ручного решения", markdown)
        self.assertIn("Первый ответ", markdown)
        self.assertIn("Второй ответ", markdown)

    def test_topic_distribution_is_explicit_and_does_not_filter_pairs(self) -> None:
        pairs = [
            TrainingPair("как создать страницу HTML?", "используй тег body"),
            TrainingPair("как создать список в Python?", "используй list"),
            TrainingPair("как создать таблицу SQL?", "используй create table"),
            TrainingPair("как открыть файл в Linux?", "используй terminal"),
            TrainingPair("как найти площадь круга?", "используй формулу"),
            TrainingPair("как приготовить чай?", "нагреть воду"),
        ]

        topic_counts = analyze_topic_distribution(pairs)

        self.assertEqual(classify_topic(pairs[0]), "web")
        self.assertEqual(topic_counts["web"], 1)
        self.assertEqual(topic_counts["python"], 1)
        self.assertEqual(topic_counts["data"], 1)
        self.assertEqual(topic_counts["system"], 1)
        self.assertEqual(topic_counts["math"], 1)
        self.assertEqual(topic_counts["other"], 1)
        self.assertEqual(sum(topic_counts.values()), len(pairs))
        self.assertIn("Ориентировочная карта тем", render_topic_distribution(topic_counts))

    def test_topic_filter_is_reversible_and_validates_requested_topic(self) -> None:
        pairs = [
            TrainingPair("как создать страницу HTML?", "используй тег body"),
            TrainingPair("как создать список в Python?", "используй list"),
        ]

        web_pairs = filter_pairs_by_topic(pairs, "web")
        all_pairs = filter_pairs_by_topic(pairs, "all")

        self.assertEqual(web_pairs, [pairs[0]])
        self.assertEqual(all_pairs, pairs)
        self.assertIsNot(all_pairs, pairs)
        with self.assertRaisesRegex(ValueError, "Неизвестная тема"):
            filter_pairs_by_topic(pairs, "unknown")

    def test_topic_classifier_does_not_match_part_of_another_word(self) -> None:
        pair = TrainingPair(
            "как заменить лампочку ближнего света?",
            "отстегни пружинку и замени лампу",
        )

        self.assertEqual(classify_topic(pair), "other")

    def test_topic_classifier_does_not_treat_any_site_as_web_development(self) -> None:
        pair = TrainingPair(
            "как оформить ИП онлайн?",
            "подай заявление через сайт Госуслуг",
        )

        self.assertEqual(classify_topic(pair), "other")


if __name__ == "__main__":
    unittest.main()
