"""Проверяет metadata-only очередь ручной проверки Brain v3 candidate."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluation.brain_v3_owned_dialogue_review import (
    create_review_queue,
    review_queue_markdown,
    select_review_queue,
)


def _record(
    identifier: str,
    *,
    split: str,
    topic: str,
    goal: str,
    tone: str,
    property_name: str,
    turn_count: int,
) -> dict[str, object]:
    """Создаёт маленькую тестовую запись с текстом, который нельзя раскрыть в queue."""
    turns: list[dict[str, str]] = []
    for index in range(turn_count):
        role = "user" if index % 2 == 0 else "assistant"
        turns.append(
            {
                "role": role,
                "text": f"private review probe {identifier} turn {index}",
            }
        )
    return {
        "id": identifier,
        "content_sha256": f"hash-{identifier}",
        "split": split,
        "topic": topic,
        "dialogue_goal": goal,
        "emotional_tone": tone,
        "expected_response_properties": [property_name],
        "turns": turns,
    }


class BrainV3OwnedDialogueReviewTests(unittest.TestCase):
    """Проверяет стратификацию и запрет сохранения реплик в review-артефакте."""

    def setUp(self) -> None:
        self.records = [
            _record(
                "review.train.alpha",
                split="train",
                topic="casual_dialogue",
                goal="casual_connection",
                tone="warm",
                property_name="natural_tone",
                turn_count=2,
            ),
            _record(
                "review.validation.beta",
                split="validation",
                topic="emotional_support",
                goal="emotional_support",
                tone="supportive",
                property_name="appropriate_empathy",
                turn_count=4,
            ),
            _record(
                "review.test.gamma",
                split="test",
                topic="web_grounding",
                goal="route_verified_fact",
                tone="concise",
                property_name="fact_grounded",
                turn_count=6,
            ),
        ]

    def test_queue_covers_every_non_empty_stratum_and_never_exposes_turn_text(self) -> None:
        report = select_review_queue(self.records, fraction=0.05)
        rendered = json.dumps(report, ensure_ascii=False) + review_queue_markdown(report)
        self.assertEqual(report["selected_records"], 3)
        self.assertEqual(report["coverage"]["splits"], {"test": 1, "train": 1, "validation": 1})
        self.assertEqual(report["coverage"]["turn_counts"], {"2": 1, "4": 1, "6": 1})
        self.assertNotIn("private review probe", rendered)
        self.assertTrue(
            all("turns" not in item and "text" not in item for item in report["items"])
        )
        self.assertFalse(report["candidate_is_active"])
        self.assertFalse(report["training_started"])

    def test_creation_writes_only_new_metadata_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.jsonl"
            candidate.write_text(
                "\n".join(
                    json.dumps(record, ensure_ascii=False) for record in self.records
                )
                + "\n",
                encoding="utf-8",
            )
            results_root = root / "results"
            report_directory = results_root / "review-queue"
            report = create_review_queue(
                candidate,
                report_directory,
                fraction=0.05,
                results_root=results_root,
            )
            self.assertTrue(
                (report_directory / "brain_v3_owned_dialogue_review_queue.json").is_file()
            )
            self.assertTrue(
                (report_directory / "BRAIN_V3_OWNED_DIALOGUE_REVIEW_QUEUE.md").is_file()
            )
            self.assertEqual(report["review_status"], "unreviewed")
            self.assertFalse(report["candidate_is_active"])
            self.assertFalse(report["training_started"])


if __name__ == "__main__":
    unittest.main()
