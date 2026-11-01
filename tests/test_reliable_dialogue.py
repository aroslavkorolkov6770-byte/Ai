"""Регрессии состояния и декодирования follow-up."""

import json
import unittest
from pathlib import Path

from ai_core.reliable_dialogue import DEFAULT_DIALOGUE_ARTIFACT, ReliableDialogue
from ai_core.reliable_qa import ABSTAIN, DEFAULT_ARTIFACT, ReliableQA, normalize


ROOT = Path(__file__).resolve().parent.parent


class ReliableDialogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qa = ReliableQA.load(DEFAULT_ARTIFACT)
        cls.model = ReliableDialogue.load(
            cls.qa,
            DEFAULT_DIALOGUE_ARTIFACT,
            DEFAULT_ARTIFACT,
        )
        cls.allowed_texts = set(cls.qa.answers.values()) | {ABSTAIN}

    def messages_for(self, intent: str) -> list[dict[str, str]]:
        return [{"role": "assistant", "content": self.qa.answers[intent]}]

    def test_context_changes_the_meaning_of_the_same_color_request(self) -> None:
        prompt = "Как сделать его зелёным?"

        background = self.model.predict(prompt, self.messages_for("html_button"))
        text_color = self.model.predict(prompt, self.messages_for("css_font_size"))

        self.assertEqual(background.intent, "css_background")
        self.assertEqual(text_color.intent, "css_text_color")
        self.assertTrue(background.context_used)
        self.assertTrue(text_color.context_used)

    def test_followup_without_verified_context_requests_clarification(self) -> None:
        result = self.model.predict("А как сделать её синей?", [])

        self.assertFalse(result.accepted)
        self.assertEqual(result.text, ABSTAIN)

    def test_explicit_new_question_does_not_inherit_old_topic(self) -> None:
        result = self.model.predict(
            "Как добавить изображение в HTML?",
            self.messages_for("html_button"),
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.intent, "html_image")
        self.assertFalse(result.context_used)

    def test_active_dialogue_checkpoint_is_the_verified_release(self) -> None:
        self.assertEqual(self.model.version, "web-dialogue-intent-v2.10")

    def test_held_out_suites_keep_precision_coverage_and_rejection(self) -> None:
        filenames = (
            "dialogue_acceptance.jsonl",
            "dialogue_generalization.jsonl",
            "dialogue_final_holdout.jsonl",
            "dialogue_release_holdout.jsonl",
        )
        for filename in filenames:
            with self.subTest(filename=filename):
                cases = [
                    json.loads(line)
                    for line in (ROOT / "evaluation" / filename)
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ]
                supported = correct = wrong = 0
                for case in cases:
                    result = self.model.predict(
                        case["prompt"],
                        self.messages_for(case["previous"]),
                    )
                    self.assertIn(result.text, self.allowed_texts)
                    expected = case["intent"]
                    if expected is None:
                        self.assertFalse(result.accepted)
                        continue
                    supported += 1
                    if result.accepted and result.intent == expected:
                        correct += 1
                    elif result.accepted:
                        wrong += 1
                self.assertEqual(wrong, 0)
                self.assertGreaterEqual(correct / supported, 0.95)

    def test_dialogue_test_prompts_do_not_overlap_training(self) -> None:
        filenames = (
            "dialogue_acceptance.jsonl",
            "dialogue_generalization.jsonl",
            "dialogue_final_holdout.jsonl",
            "dialogue_release_holdout.jsonl",
        )
        for filename in filenames:
            held_out = {
                normalize(json.loads(line)["prompt"])
                for line in (ROOT / "evaluation" / filename)
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            }
            self.assertFalse(self.model.training_prompts & held_out)


if __name__ == "__main__":
    unittest.main()
