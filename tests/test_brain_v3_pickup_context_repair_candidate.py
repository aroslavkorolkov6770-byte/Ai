"""Проверяет отдельный review-only candidate для уточнения ПВЗ."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from evaluation.brain_v3_scoped_review import (
    APPROVAL_REASON_CODES,
    ScopedReviewError,
    build_queue,
    build_summary,
    create_decision,
    load_candidate,
    load_scope,
)


ROOT = Path(__file__).resolve().parents[1]
SCOPE_PATH = (
    ROOT
    / "data"
    / "brain_v3"
    / "reviews"
    / "brain-v3-pickup-context-repair-v1.4.scope.json"
)
CANDIDATE_PATH = (
    ROOT
    / "data"
    / "brain_v3"
    / "reviews"
    / "brain-v3-pickup-context-repair-v1.4.review.jsonl"
)


class BrainV3PickupContextRepairCandidateTests(unittest.TestCase):
    """Не даёт repair-сценам стать неявным training material."""

    def setUp(self) -> None:
        self.scope = load_scope(SCOPE_PATH)
        self.records, self.candidate_sha256 = load_candidate(CANDIDATE_PATH)

    def _queue(
        self,
        records: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return build_queue(
            self.records if records is None else records,
            self.scope,
            candidate_path=CANDIDATE_PATH,
            candidate_sha256=self.candidate_sha256,
        )

    def test_candidate_is_metadata_only_and_requires_full_review(self) -> None:
        queue = self._queue()
        rendered = json.dumps(queue, ensure_ascii=False)

        self.assertEqual(queue["candidate_records"], 2)
        self.assertEqual(queue["coverage"]["topics"], {"web_grounding": 2})
        self.assertEqual(queue["coverage"]["human_review_scope"], {"full_review_required": 2})
        self.assertEqual(queue["coverage"]["response_properties"]["ask_when_missing"], 2)
        self.assertEqual(queue["coverage"]["response_properties"]["tool_result_returned"], 2)
        self.assertEqual(queue["coverage"]["response_properties"]["no_action_promise"], 2)
        self.assertTrue(queue["metadata_only"])
        self.assertFalse(queue["candidate_is_active"])
        self.assertFalse(queue["training_eligible"])
        self.assertNotIn("[[web.", rendered)

    def test_no_action_promise_requires_a_final_tool_result_property(self) -> None:
        broken_records = copy.deepcopy(self.records)
        properties = broken_records[0]["scenario"]["expected_response_properties"]
        assert isinstance(properties, list)
        properties.remove("tool_result_returned")

        with self.assertRaisesRegex(
            ScopedReviewError,
            "no_action_promise_requires_tool_result",
        ):
            self._queue(records=broken_records)

    def test_completed_human_review_still_cannot_enable_training(self) -> None:
        queue = self._queue()
        checked_at = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)
        decisions = [
            create_decision(
                item,
                self.scope,
                decision="approved",
                reason_codes=APPROVAL_REASON_CODES,
                reviewer_role="project_owner",
                reviewed_at=checked_at,
            )
            for item in queue["items"]
        ]
        summary = build_summary(queue, decisions, self.scope)

        self.assertTrue(summary["required_human_review_coverage_complete"])
        self.assertFalse(summary["training_candidate_allowed"])
        self.assertFalse(summary["promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
