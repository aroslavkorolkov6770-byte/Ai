"""Регрессии надёжного режима одиночных ответов."""

import json
import unittest
from pathlib import Path

from ai_core.reliable_qa import ABSTAIN, DEFAULT_ARTIFACT, ReliableQA, normalize


ROOT = Path(__file__).resolve().parent.parent


class ReliableQATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = ReliableQA.load(DEFAULT_ARTIFACT)
        cls.allowed_texts = set(cls.model.answers.values()) | {ABSTAIN}

    def test_every_evaluation_output_is_complete_or_clarification(self) -> None:
        for filename in ("qa_acceptance.jsonl", "qa_generalization.jsonl"):
            with self.subTest(filename=filename):
                cases = [
                    json.loads(line)
                    for line in (ROOT / "evaluation" / filename)
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ]
                wrong = 0
                correct = 0
                for case in cases:
                    result = self.model.predict(case["prompt"])
                    self.assertIn(result.text, self.allowed_texts)
                    if result.accepted and result.intent == case["intent"]:
                        correct += 1
                    elif result.accepted:
                        wrong += 1
                    if case["intent"] is None:
                        self.assertFalse(result.accepted)
                supported = sum(case["intent"] is not None for case in cases)
                self.assertEqual(wrong, 0)
                self.assertGreaterEqual(correct / supported, 0.75)

    def test_held_out_questions_are_not_embedded_training_samples(self) -> None:
        import torch

        checkpoint = torch.load(DEFAULT_ARTIFACT, map_location="cpu", weights_only=True)
        training = {normalize(row["prompt"]) for row in checkpoint["training_samples"]}
        for filename in ("qa_acceptance.jsonl", "qa_generalization.jsonl"):
            held_out = {
                normalize(json.loads(line)["prompt"])
                for line in (ROOT / "evaluation" / filename)
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            }
            self.assertFalse(training & held_out)

    def test_unknown_topic_never_turns_into_generated_letters(self) -> None:
        result = self.model.predict("Какая сегодня погода на Марсе?")

        self.assertFalse(result.accepted)
        self.assertIsNone(result.intent)
        self.assertEqual(result.text, ABSTAIN)


if __name__ == "__main__":
    unittest.main()
