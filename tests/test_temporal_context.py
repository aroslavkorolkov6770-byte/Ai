"""Проверки мгновенных локальных ответов о времени и календаре."""

from datetime import datetime, timedelta, timezone
import unittest

from ai_core.temporal_context import describe_current_temporal_context


class TemporalContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixed_now = datetime(
            2026,
            9,
            10,
            17,
            30,
            tzinfo=timezone(timedelta(hours=3), name="Europe/Minsk"),
        )

    def test_combined_current_time_and_calendar_answer_is_deterministic(self) -> None:
        answer = describe_current_temporal_context(
            "Сколько времени, какой сегодня день, месяц и время года?",
            now=self.fixed_now,
        )

        self.assertEqual(
            answer,
            "Сейчас 17:30 по минскому времени. Сегодня четверг. Сейчас сентябрь. Сейчас осень.",
        )

    def test_date_question_uses_local_calendar_without_network(self) -> None:
        answer = describe_current_temporal_context(
            "Какая сегодня дата?",
            now=self.fixed_now,
        )

        self.assertEqual(answer, "Сегодня 10 сентября 2026 года.")

    def test_non_temporal_question_is_not_intercepted(self) -> None:
        self.assertIsNone(
            describe_current_temporal_context("Как сделать кнопку в HTML?", now=self.fixed_now)
        )


if __name__ == "__main__":
    unittest.main()
