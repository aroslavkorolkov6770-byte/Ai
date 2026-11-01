"""Проверки воспроизводимых метрик полного текста ответа."""

import unittest

from evaluation.run_evaluation import normalized_text_similarity


class EvaluationMetricTests(unittest.TestCase):
    def test_similarity_ignores_case_and_repeated_spaces(self) -> None:
        score = normalized_text_similarity(
            "  Используй   метод APPEND ",
            "используй метод append",
        )

        self.assertEqual(score, 1.0)

    def test_similarity_distinguishes_unrelated_text(self) -> None:
        score = normalized_text_similarity(
            "создай список",
            "вычисли квадратный корень",
        )

        self.assertLess(score, 0.4)


if __name__ == "__main__":
    unittest.main()
