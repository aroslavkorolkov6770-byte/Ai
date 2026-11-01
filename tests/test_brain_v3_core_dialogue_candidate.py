"""Проверяет, что партия v1.6 остаётся исходным материалом только для review."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from ai_core.brain_v3_corpus import NearDuplicateIndex, duplicate_signature
from evaluation.brain_v3_scoped_review import (
    build_queue,
    build_summary,
    load_candidate,
    load_scope,
)


ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = ROOT / "data" / "brain_v3" / "reviews"
SCOPE_PATH = REVIEW_ROOT / "brain-v3-core-dialogue-candidate-v1.6.scope.json"
CANDIDATE_PATH = REVIEW_ROOT / "brain-v3-core-dialogue-candidate-v1.6.review.jsonl"


def _dialogue_signature(record: dict[str, object]):
    """Собирает одинаковый отпечаток для текущей и прежних партий."""

    turns = record["turns"]
    assert isinstance(turns, list)
    text = "\n".join(str(turn["text"]) for turn in turns)
    return duplicate_signature(text)


class BrainV3CoreDialogueCandidateTests(unittest.TestCase):
    """Проверяет смысловые ячейки, чистоту и границу human review."""

    @classmethod
    def setUpClass(cls) -> None:
        """Читает шесть сцен один раз, не меняя кандидат или рабочий корпус."""

        cls.scope = load_scope(SCOPE_PATH)
        cls.records, cls.candidate_sha256 = load_candidate(CANDIDATE_PATH)
        cls.queue = build_queue(
            cls.records,
            cls.scope,
            candidate_path=CANDIDATE_PATH,
            candidate_sha256=cls.candidate_sha256,
        )

    def test_queue_covers_three_gaps_without_training_rights(self) -> None:
        """В queue попадают только metadata и все шесть требуют review."""

        self.assertEqual(self.queue["candidate_records"], 6)
        self.assertEqual(
            self.queue["coverage"]["topics"],
            {
                "context_and_memory": 2,
                "explanation_and_instruction": 2,
                "safety_boundaries": 2,
            },
        )
        self.assertEqual(
            self.queue["coverage"]["human_review_scope"],
            {"full_review_required": 6},
        )
        self.assertTrue(self.queue["metadata_only"])
        self.assertFalse(self.queue["candidate_is_active"])
        self.assertFalse(self.queue["training_eligible"])
        self.assertFalse(self.queue["training_started"])
        rendered = json.dumps(self.queue, ensure_ascii=False)
        self.assertNotIn('"turns"', rendered)
        self.assertNotIn('"text"', rendered)

    def test_unreviewed_records_cannot_be_promoted(self) -> None:
        """Техническая проверка не подменяет решение владельца."""

        summary = build_summary(self.queue, [], self.scope)
        self.assertEqual(summary["approved_records"], 0)
        self.assertEqual(summary["unreviewed_records"], 6)
        self.assertFalse(summary["required_human_review_coverage_complete"])
        self.assertFalse(summary["training_candidate_allowed"])
        self.assertFalse(summary["promotion_allowed"])

    def test_no_web_marker_or_personal_chat_text(self) -> None:
        """Сцены не содержат результаты инструментов или чужие переписки."""

        rendered = "\n".join(
            str(turn["text"])
            for record in self.records
            for turn in record["turns"]
        ).casefold()
        for marker in ("http", "www.", "[[", "погода", "lms"):
            self.assertNotIn(marker, rendered)
        self.assertEqual(
            sorted(record["review_status"] for record in self.records),
            ["unreviewed"] * 6,
        )

    def test_no_near_duplicate_of_earlier_review_candidate(self) -> None:
        """Сверяет всю сцену с предыдущими draft-партиями без смены их verdict."""

        earlier = NearDuplicateIndex()
        for path in sorted(REVIEW_ROOT.glob("*.review.jsonl")):
            if path == CANDIDATE_PATH:
                continue
            records, _ = load_candidate(path)
            for record in records:
                earlier.add(record["id"], _dialogue_signature(record))
        for record in self.records:
            self.assertIsNone(
                earlier.find(_dialogue_signature(record)),
                msg=f"Близкий дубль: {record['id']}",
            )


if __name__ == "__main__":
    unittest.main()
