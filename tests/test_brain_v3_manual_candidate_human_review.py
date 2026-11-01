"""Проверяет metadata-only human-review ledger первого manual Brain v3 candidate."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from evaluation.brain_v3_manual_candidate_human_review import (
    APPROVAL_REASON_CODES,
    DEFAULT_CANDIDATE_PATH,
    DEFAULT_QUEUE_PATH,
    DEFAULT_SCOPE_PATH,
    ManualCandidateHumanReviewError,
    append_review_decision,
    build_review_summary,
    create_review_decision,
    load_review_decisions,
    load_review_queue,
    write_review_summary,
)
from evaluation.brain_v3_manual_candidate_review import create_review_queue


class BrainV3ManualCandidateHumanReviewTests(unittest.TestCase):
    """Гарантирует, что технический код не может подменить verdict человека."""

    def setUp(self) -> None:
        self.queue = load_review_queue()
        self.fixed_time = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

    def _approved_decision(self, item: dict[str, object]) -> dict[str, object]:
        return create_review_decision(
            item,
            decision="approved",
            reason_codes=APPROVAL_REASON_CODES,
            reviewer_role="project_owner",
            reviewed_at=self.fixed_time,
        )

    def test_queue_is_bound_to_review_only_source_and_stays_non_training(self) -> None:
        self.assertEqual(self.queue["candidate_records"], 24)
        self.assertTrue(self.queue["metadata_only"])
        self.assertFalse(self.queue["candidate_is_active"])
        self.assertFalse(self.queue["training_eligible"])
        self.assertFalse(self.queue["training_started"])

    def test_approval_requires_all_four_human_checks(self) -> None:
        item = self.queue["items"][0]

        with self.assertRaisesRegex(
            ManualCandidateHumanReviewError,
            "approval_criteria_missing",
        ):
            create_review_decision(
                item,
                decision="approved",
                reason_codes=["provenance_checked"],
                reviewer_role="project_owner",
                reviewed_at=self.fixed_time,
            )

    def test_required_coverage_needs_all_high_risk_and_general_sample(self) -> None:
        high_risk_items = [
            item for item in self.queue["items"] if item["requires_full_human_review"]
        ]
        general_items = [
            item for item in self.queue["items"] if not item["requires_full_human_review"]
        ]
        decisions = [
            self._approved_decision(item)
            for item in [*high_risk_items, *general_items[:2]]
        ]
        summary = build_review_summary(
            self.queue,
            decisions,
            general_review_ratio=0.1,
        )
        rendered = json.dumps(summary, ensure_ascii=False)

        self.assertEqual(summary["reviewed_records"], 14)
        self.assertEqual(summary["high_risk_reviewed_records"], 12)
        self.assertEqual(summary["general_reviewed_records"], 2)
        self.assertTrue(summary["required_human_review_coverage_complete"])
        self.assertFalse(summary["all_items_reviewed"])
        self.assertFalse(summary["training_candidate_allowed"])
        self.assertFalse(summary["promotion_allowed"])
        self.assertNotIn("Сегодня весь день бегал", rendered)
        self.assertNotIn("Мне так плохо", rendered)

    def test_append_writes_only_metadata_and_rejects_duplicate_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reviews_root = root / "reviews"
            results_root = root / "results"
            reviews_root.mkdir(parents=True)
            scope_path = reviews_root / DEFAULT_SCOPE_PATH.name
            candidate_path = reviews_root / DEFAULT_CANDIDATE_PATH.name
            scope_path.write_bytes(DEFAULT_SCOPE_PATH.read_bytes())
            candidate_path.write_bytes(DEFAULT_CANDIDATE_PATH.read_bytes())
            queue_directory = results_root / "queue"
            create_review_queue(
                scope_path,
                candidate_path,
                queue_directory,
                review_root=reviews_root,
                results_root=results_root,
            )
            queue_path = queue_directory / "brain_v3_manual_candidate_review_queue.json"
            decisions_path = reviews_root / "decisions.jsonl"
            identifier = "brain-v3-owned-manual-v1-001"
            decision = append_review_decision(
                queue_path,
                decisions_path,
                candidate_id=identifier,
                decision="approved",
                reason_codes=APPROVAL_REASON_CODES,
                reviewer_role="project_owner",
                scope_path=scope_path,
                candidate_path=candidate_path,
                results_root=results_root,
                review_root=reviews_root,
                reviewed_at=self.fixed_time,
            )
            written = decisions_path.read_text(encoding="utf-8")
            loaded = load_review_decisions(
                decisions_path,
                load_review_queue(
                    queue_path,
                    scope_path=scope_path,
                    candidate_path=candidate_path,
                    results_root=results_root,
                    review_root=reviews_root,
                ),
                review_root=reviews_root,
            )

            self.assertEqual(decision["candidate_id"], identifier)
            self.assertEqual(len(loaded), 1)
            self.assertNotIn("Сегодня весь день бегал", written)
            self.assertNotIn("Мне так плохо", written)
            with self.assertRaisesRegex(
                ManualCandidateHumanReviewError,
                "duplicate_decision",
            ):
                append_review_decision(
                    queue_path,
                    decisions_path,
                    candidate_id=identifier,
                    decision="approved",
                    reason_codes=APPROVAL_REASON_CODES,
                    reviewer_role="project_owner",
                    scope_path=scope_path,
                    candidate_path=candidate_path,
                    results_root=results_root,
                    review_root=reviews_root,
                    reviewed_at=self.fixed_time,
                )

    def test_summary_write_is_metadata_only(self) -> None:
        summary = build_review_summary(
            self.queue,
            [],
            general_review_ratio=0.1,
        )
        with tempfile.TemporaryDirectory() as directory:
            results_root = Path(directory) / "results"
            destination = results_root / "summary"
            write_review_summary(destination, summary, results_root=results_root)
            written = (
                (destination / "brain_v3_manual_candidate_human_review_summary.json")
                .read_text(encoding="utf-8")
                + (destination / "BRAIN_V3_MANUAL_CANDIDATE_HUMAN_REVIEW_SUMMARY.md")
                .read_text(encoding="utf-8")
            )

            self.assertIn("not_started", written)
            self.assertNotIn("Сегодня весь день бегал", written)
            self.assertNotIn("Мне так плохо", written)


if __name__ == "__main__":
    unittest.main()
