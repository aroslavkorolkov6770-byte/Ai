"""Проверяет безопасный автоматический выбор LMS-контекста."""

from __future__ import annotations

import hashlib
import unittest

from ai_core.lms_knowledge import LmsKnowledgeIndex
from ai_core.reliable_qa import normalize


def entry(context: str, questions: list[str]) -> dict:
    """Создаёт минимальную валидную запись индекса для изолированного теста."""
    context_id = hashlib.sha256(normalize(context).encode("utf-8")).hexdigest()
    return {
        "context": context,
        "context_id": context_id,
        "questions": questions,
        "task_types": ["direct_context_answer"],
    }


def index_for(entries: list[dict]) -> LmsKnowledgeIndex:
    """Собирает индекс в памяти, не читая большой пользовательский датасет."""
    return LmsKnowledgeIndex.from_payload(
        {
            "format_version": 1,
            "source": "test",
            "source_records": len(entries),
            "context_groups": len(entries),
            "entries": entries,
        }
    )


class LmsKnowledgeIndexTests(unittest.TestCase):
    def test_conflicting_contexts_request_clarification(self) -> None:
        index = index_for(
            [
                entry(
                    "Курс «Охрана труда». Для завершения нужно набрать не менее 80%.",
                    ["Какой проходной балл?"],
                ),
                entry(
                    "Курс «Охрана труда». Для завершения нужно набрать не менее 70%.",
                    ["Какой проходной балл?"],
                ),
            ]
        )

        match = index.resolve("Какой проходной балл?")

        self.assertTrue(match.needs_clarification)
        self.assertIsNone(match.context)
        self.assertEqual(match.source, "lms-knowledge-ambiguous")

    def test_natural_details_choose_one_context_without_a_form_field(self) -> None:
        index = index_for(
            [
                entry(
                    (
                        "Курс «Охрана труда». Роль: специалист. "
                        "Доступ к материалам действует 45 дней. "
                        "Для завершения нужно набрать не менее 80%."
                    ),
                    ["Какой проходной балл?"],
                ),
                entry(
                    (
                        "Курс «Охрана труда». Роль: руководитель. "
                        "Доступ к материалам действует 30 дней. "
                        "Для завершения нужно набрать не менее 70%."
                    ),
                    ["Какой проходной балл?"],
                ),
            ]
        )

        match = index.resolve(
            (
                "Я прохожу курс «Охрана труда» как специалист, "
                "доступ действует 45 дней. Какой проходной балл?"
            )
        )

        self.assertFalse(match.needs_clarification)
        self.assertIsNotNone(match.context)
        self.assertIn("80%", match.context or "")
        self.assertGreater(match.score, 0.45)

    def test_unquoted_course_and_rephrased_passing_question_choose_context(self) -> None:
        """Обычная фраза должна работать без точного текста из датасета."""
        index = index_for(
            [
                entry(
                    (
                        "Курс «Охрана труда». Роль: специалист. "
                        "Доступ к материалам действует 45 дней. "
                        "Для завершения нужно набрать не менее 80%."
                    ),
                    ["Какой проходной балл?"],
                ),
                entry(
                    (
                        "Курс «Охрана труда». Роль: руководитель. "
                        "Доступ к материалам действует 30 дней. "
                        "Для завершения нужно набрать не менее 70%."
                    ),
                    ["Какой проходной балл?"],
                ),
            ]
        )

        match = index.resolve(
            (
                "Я прохожу обучение по охране труда как специалист, "
                "доступ действует 45 дней. Какой результат нужен, "
                "чтобы зачли обучение?"
            )
        )

        self.assertFalse(match.needs_clarification)
        self.assertIn("80%", match.context or "")
        self.assertIn("passing_score", match.semantic_topics)

    def test_previous_user_message_supplies_dialogue_detail(self) -> None:
        index = index_for(
            [
                entry(
                    (
                        "Курс «Охрана труда». Роль: специалист. "
                        "Доступ к материалам действует 45 дней. "
                        "На тест даётся 3 попытки."
                    ),
                    ["Сколько попыток даётся на тест?"],
                ),
                entry(
                    (
                        "Курс «Охрана труда». Роль: руководитель. "
                        "Доступ к материалам действует 30 дней. "
                        "На тест даётся 2 попытки."
                    ),
                    ["Сколько попыток даётся на тест?"],
                ),
            ]
        )

        match = index.resolve(
            "Сколько попыток даётся на тест?",
            [
                (
                    "Я прохожу курс «Охрана труда» как специалист, "
                    "доступ действует 45 дней."
                )
            ],
        )

        self.assertFalse(match.needs_clarification)
        self.assertIn("3 попытки", match.context or "")

    def test_followup_rephrasing_uses_previous_course_details(self) -> None:
        """Уточнение нового вопроса не должно терять найденную тему беседы."""
        index = index_for(
            [
                entry(
                    (
                        "Курс «Охрана труда». Роль: специалист. "
                        "Доступ к материалам действует 45 дней. "
                        "На итоговый тест даётся 3 попытки."
                    ),
                    ["Сколько попыток даётся на тест?"],
                ),
                entry(
                    (
                        "Курс «Охрана труда». Роль: руководитель. "
                        "Доступ к материалам действует 30 дней. "
                        "На итоговый тест даётся 2 попытки."
                    ),
                    ["Сколько попыток даётся на тест?"],
                ),
            ]
        )

        match = index.resolve(
            "А пересдать итоговый тест сколько можно?",
            [
                (
                    "Я прохожу обучение по охране труда как специалист, "
                    "доступ действует 45 дней."
                )
            ],
        )

        self.assertFalse(match.needs_clarification)
        self.assertIn("3 попытки", match.context or "")
        self.assertIn("attempt_count", match.semantic_topics)

    def test_semantic_topic_is_explained_when_facts_conflict(self) -> None:
        """При конфликте индекс распознаёт тему, но не выдумывает число."""
        index = index_for(
            [
                entry(
                    "Курс «Охрана труда». Для завершения нужно набрать 80%.",
                    ["Какой проходной балл?"],
                ),
                entry(
                    "Курс «Пожарная безопасность». Для завершения нужно набрать 70%.",
                    ["Какой проходной балл?"],
                ),
            ]
        )

        match = index.resolve("Что нужно набрать, чтобы зачли обучение?")

        self.assertTrue(match.needs_clarification)
        self.assertEqual(match.source, "lms-knowledge-ambiguous")
        self.assertIn("passing_score", match.semantic_topics)


if __name__ == "__main__":
    unittest.main()
