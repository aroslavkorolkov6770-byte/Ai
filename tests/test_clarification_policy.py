"""Проверяет единый ранний слой уточнения без модели и сети."""

from __future__ import annotations

import unittest

from ai_core.clarification_policy import (
    MISSING_EXTERNAL_SUBJECT,
    MISSING_MATCH,
    MISSING_NEWS,
    MISSING_OPTIONS,
    clarification_followup_web_query,
    clarification_for_prompt,
)


class ClarificationPolicyTests(unittest.TestCase):
    """Проверяет только запросы, где без детали нельзя ответить точно."""

    def test_obviously_incomplete_requests_receive_one_specific_question(self) -> None:
        cases = (
            ("Кто выиграл вчерашний матч?", MISSING_MATCH),
            ("Какие новости?", MISSING_NEWS),
            ("Какие новости сегодня?", MISSING_NEWS),
            ("Сколько стоит?", MISSING_EXTERNAL_SUBJECT),
            ("Какой курс валют сейчас?", MISSING_EXTERNAL_SUBJECT),
            ("Что выбрать?", MISSING_OPTIONS),
        )

        for prompt, expected in cases:
            with self.subTest(prompt=prompt):
                decision = clarification_for_prompt(prompt, [])
                self.assertEqual(decision, expected)

    def test_clear_questions_and_normal_conversation_are_not_intercepted(self) -> None:
        prompts = (
            "Что такое HTTP?",
            "Какие правила возврата у этого сервиса?",
            "Кто выиграл матч «Север» — «Маяк»?",
            "Привет",
            "Мне тревожно после сложного разговора.",
        )

        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertIsNone(clarification_for_prompt(prompt, []))

    def test_short_reference_can_use_current_dialogue_context(self) -> None:
        history = [
            {
                "role": "user",
                "content": "Я выбираю между электричкой и автомобилем для поездки на работу.",
            },
            {"role": "assistant", "content": "Давай сравним варианты по времени и расходам."},
        ]

        self.assertIsNone(clarification_for_prompt("Что выбрать?", history))

    def test_web_followup_combines_only_the_immediate_clarified_question(self) -> None:
        initial_prompt = "Кто выиграл вчерашний матч?"
        history = [
            {"role": "user", "content": initial_prompt},
            {"role": "assistant", "content": MISSING_MATCH.text},
        ]

        self.assertEqual(
            clarification_followup_web_query(
                "Матч «Север» — «Маяк» в финале Кубка.",
                history,
            ),
            "Кто выиграл вчерашний матч? Матч «Север» — «Маяк» в финале Кубка.",
        )
        self.assertIsNone(clarification_followup_web_query("Не знаю.", history))
        self.assertIsNone(clarification_followup_web_query("Да.", history))
        self.assertIsNone(
            clarification_followup_web_query("А какие ещё новости?", history)
        )
        self.assertEqual(
            clarification_for_prompt("Не знаю.", history),
            MISSING_MATCH,
        )


if __name__ == "__main__":
    unittest.main()
