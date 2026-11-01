"""Проверки локальной базы известных обучающих примеров."""

from pathlib import Path
import unittest

from ai_core.knowledge_base import LocalKnowledgeBase
from ai_core.training_data import TrainingPair


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = PROJECT_ROOT / "training_data" / "dataset.jsonl"


class KnowledgeBaseTests(unittest.TestCase):
    def test_exact_question_returns_dataset_answer(self) -> None:
        knowledge_base = LocalKnowledgeBase.from_jsonl(DATASET_PATH)
        answer = knowledge_base.lookup(" Как СЛОЖИТЬ два числа! ")
        self.assertEqual(answer, "нужно сложить их значения: a + b")

    def test_unknown_question_does_not_fake_an_answer(self) -> None:
        knowledge_base = LocalKnowledgeBase.from_jsonl(DATASET_PATH)
        self.assertIsNone(knowledge_base.lookup("вопроса нет в датасете"))

    def test_similar_question_returns_high_confidence_match(self) -> None:
        knowledge_base = LocalKnowledgeBase.from_jsonl(DATASET_PATH)
        result = knowledge_base.lookup_similar("как складывать два числа?")
        self.assertIsNotNone(result)
        answer, score = result
        self.assertEqual(answer, "нужно сложить их значения: a + b")
        self.assertGreaterEqual(score, 0.70)

    def test_distant_question_is_not_matched(self) -> None:
        knowledge_base = LocalKnowledgeBase.from_jsonl(DATASET_PATH)
        self.assertIsNone(knowledge_base.lookup_similar("как написать роман?"))

    def test_c_plus_plus_and_c_sharp_are_not_combined(self) -> None:
        knowledge_base = LocalKnowledgeBase(
            [
                TrainingPair("как начать в C++?", "ответ про C++"),
                TrainingPair("как начать в C#?", "ответ про C#"),
            ]
        )

        self.assertEqual(knowledge_base.lookup("как начать в C++?"), "ответ про C++")
        self.assertEqual(knowledge_base.lookup("как начать в C#?"), "ответ про C#")
        self.assertEqual(knowledge_base.conflict_count, 0)

    def test_conflicted_prompt_does_not_return_arbitrary_answer(self) -> None:
        knowledge_base = LocalKnowledgeBase(
            [
                TrainingPair("как получить атрибут?", "первый вариант"),
                TrainingPair("как получить атрибут?", "второй вариант"),
            ]
        )

        self.assertIsNone(knowledge_base.lookup("как получить атрибут?"))
        self.assertTrue(knowledge_base.is_conflicted_prompt("как получить атрибут?"))
        self.assertEqual(knowledge_base.conflict_count, 1)


if __name__ == "__main__":
    unittest.main()
