"""Проверяет безопасный metadata-only workflow ручной проверки OASST1."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from evaluation.oasst1_human_review import (
    APPROVAL_REASON_CODES,
    DECISION_MODE,
    OASST1HumanReviewError,
    QUEUE_MODE,
    append_review_decision,
    build_review_summary,
    create_review_decision,
    load_review_decisions,
    load_review_queue,
    load_source_records_for_queue,
    render_candidate_for_terminal,
    review_summary_markdown,
    write_review_summary,
)
from evaluation.oasst1_semantic_review import SOURCE_ID


def _sha256(value: str) -> str:
    """Создаёт безопасный детерминированный fixture-хэш без настоящих данных."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _queue_item(identifier: str, content_sha256: str, split: str) -> dict[str, object]:
    """Создаёт минимальный metadata-only item исходной review queue."""

    return {
        "id": identifier,
        "content_sha256": content_sha256,
        "split": split,
        "turn_count": 4,
        "primary_candidate_topic": "casual_dialogue",
        "candidate_topic_labels": ["casual_dialogue"],
        "candidate_reason_codes": {"casual_dialogue": ["casual_talk"]},
        "review_status": "unreviewed",
    }


def _source_record(item: dict[str, object]) -> dict[str, object]:
    """Создаёт fixture-диалог, который никогда не попадает в decision ledger."""

    return {
        "id": item["id"],
        "record_type": "dialogue",
        "language": "ru",
        "split": item["split"],
        "content_sha256": item["content_sha256"],
        "turns": [
            {"role": "user", "text": "Привет, как спокойно разобрать новую тему?"},
            {"role": "assistant", "text": "Давай начнём с небольшого шага и примера."},
            {"role": "user", "text": "А если я запутаюсь, можно спросить ещё раз?"},
            {"role": "assistant", "text": "Конечно, уточни место, и разберём его проще."},
        ],
    }


class OASST1HumanReviewTests(unittest.TestCase):
    """Проверяет просмотр, verdict, сводку и запрет утечки source text."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.raw_root = self.root / "raw"
        self.results_root = self.root / "results"
        self.reviews_root = self.root / "reviews"
        self.raw_root.mkdir()
        self.results_root.mkdir()
        self.items = [
            _queue_item("oasst1.fixture.001", _sha256("fixture-001"), "train"),
            _queue_item("oasst1.fixture.002", _sha256("fixture-002"), "validation"),
        ]
        self.queue_path = self.results_root / "queue.json"
        self.queue_path.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "mode": QUEUE_MODE,
                    "source_id": SOURCE_ID,
                    "review_status": "unreviewed",
                    "selected_records": len(self.items),
                    "items": self.items,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.source_path = self.raw_root / "openassistant-oasst1-apache-v1.jsonl"
        self.source_path.write_text(
            "\n".join(
                json.dumps(_source_record(item), ensure_ascii=False)
                for item in self.items
            ),
            encoding="utf-8",
        )
        self.decisions_path = self.reviews_root / "decisions.jsonl"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_terminal_view_contains_source_text_but_decision_never_does(self) -> None:
        queue = load_review_queue(self.queue_path, results_root=self.results_root)
        source_records = load_source_records_for_queue(
            queue,
            self.source_path,
            raw_root=self.raw_root,
        )
        terminal_view = render_candidate_for_terminal(
            self.items[0],
            source_records[self.items[0]["id"]],
        )
        self.assertIn("Привет, как спокойно разобрать новую тему?", terminal_view)
        decision = create_review_decision(
            self.items[0],
            decision="approved",
            reason_codes=APPROVAL_REASON_CODES,
            reviewer_role="local_reviewer",
            reviewed_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        )
        serialized = json.dumps(decision, ensure_ascii=False)
        self.assertEqual(decision["mode"], DECISION_MODE)
        self.assertNotIn("Привет, как спокойно разобрать новую тему?", serialized)
        self.assertNotIn("Давай начнём с небольшого шага и примера.", serialized)

    def test_append_decision_rejects_duplicate_and_summary_stays_non_training(self) -> None:
        added = append_review_decision(
            self.queue_path,
            self.source_path,
            self.decisions_path,
            candidate_id=self.items[0]["id"],
            decision="approved",
            reason_codes=APPROVAL_REASON_CODES,
            reviewer_role="local_reviewer",
            results_root=self.results_root,
            raw_root=self.raw_root,
            reviews_root=self.reviews_root,
            reviewed_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        )
        self.assertEqual(added["candidate_id"], self.items[0]["id"])
        queue = load_review_queue(self.queue_path, results_root=self.results_root)
        decisions = load_review_decisions(
            self.decisions_path,
            queue,
            reviews_root=self.reviews_root,
        )
        self.assertEqual(len(decisions), 1)
        with self.assertRaisesRegex(OASST1HumanReviewError, "duplicate_decision"):
            append_review_decision(
                self.queue_path,
                self.source_path,
                self.decisions_path,
                candidate_id=self.items[0]["id"],
                decision="approved",
                reason_codes=APPROVAL_REASON_CODES,
                reviewer_role="local_reviewer",
                results_root=self.results_root,
                raw_root=self.raw_root,
                reviews_root=self.reviews_root,
            )
        summary = build_review_summary(queue, decisions)
        self.assertEqual(summary["review_status"], "in_progress")
        self.assertFalse(summary["training_candidate_allowed"])
        self.assertFalse(summary["promotion_allowed"])
        rendered = review_summary_markdown(summary)
        self.assertNotIn("Привет, как спокойно разобрать новую тему?", rendered)
        summary_directory = self.results_root / "summary-v0.1"
        write_review_summary(summary_directory, summary, results_root=self.results_root)
        self.assertTrue((summary_directory / "OASST1_HUMAN_REVIEW_SUMMARY.md").is_file())
        with self.assertRaisesRegex(OASST1HumanReviewError, "summary_exists"):
            write_review_summary(summary_directory, summary, results_root=self.results_root)

    def test_approval_requires_all_quality_criteria(self) -> None:
        with self.assertRaisesRegex(
            OASST1HumanReviewError,
            "approval_criteria_missing",
        ):
            create_review_decision(
                self.items[0],
                decision="approved",
                reason_codes=["natural_russian"],
                reviewer_role="local_reviewer",
            )

    def test_queue_and_decision_paths_cannot_escape_controlled_roots(self) -> None:
        outside_queue = self.root / "outside-queue.json"
        outside_queue.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(OASST1HumanReviewError, "queue_outside_results"):
            load_review_queue(outside_queue, results_root=self.results_root)
        queue = load_review_queue(self.queue_path, results_root=self.results_root)
        with self.assertRaisesRegex(OASST1HumanReviewError, "decisions_outside_reviews"):
            load_review_decisions(
                self.root / "outside-decisions.jsonl",
                queue,
                reviews_root=self.reviews_root,
            )


if __name__ == "__main__":
    unittest.main()
