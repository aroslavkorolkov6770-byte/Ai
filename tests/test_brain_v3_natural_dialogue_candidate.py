"""Проверяет границы review-only партии естественного диалога v1.5."""

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
    / "brain-v3-natural-dialogue-candidate-v1.5.scope.json"
)
CANDIDATE_PATH = (
    ROOT
    / "data"
    / "brain_v3"
    / "reviews"
    / "brain-v3-natural-dialogue-candidate-v1.5.review.jsonl"
)


class BrainV3NaturalDialogueCandidateTests(unittest.TestCase):
    """Не даёт статичным review-сценам неявно стать материалом для обучения."""

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

    def test_candidate_is_static_metadata_only_and_requires_full_review(self) -> None:
        queue = self._queue()
        rendered_queue = json.dumps(queue, ensure_ascii=False)

        self.assertEqual(queue["candidate_records"], 6)
        self.assertEqual(
            queue["coverage"]["topics"],
            {
                "casual_dialogue": 1,
                "correction": 1,
                "emotional_support": 1,
                "opinion_and_choice": 1,
                "style_and_length": 1,
                "uncertainty_and_clarification": 1,
            },
        )
        self.assertEqual(
            queue["coverage"]["human_review_scope"],
            {"full_review_required": 6},
        )
        self.assertTrue(queue["metadata_only"])
        self.assertFalse(queue["candidate_is_active"])
        self.assertFalse(queue["training_eligible"])
        self.assertNotIn("Не хочется решать", rendered_queue)
        self.assertNotIn("[[", rendered_queue)

    def test_dialogue_text_has_no_dynamic_web_or_url_content(self) -> None:
        dialogue_text = "\n".join(
            turn["text"]
            for record in self.records
            for turn in record["turns"]
        ).lower()

        self.assertNotIn("http", dialogue_text)
        self.assertNotIn("www.", dialogue_text)
        self.assertNotIn("[[", dialogue_text)
        self.assertNotIn("погода", dialogue_text)

    def test_tool_result_property_is_invalid_for_an_ordinary_dialogue_topic(self) -> None:
        broken_records = copy.deepcopy(self.records)
        properties = broken_records[0]["scenario"]["expected_response_properties"]
        assert isinstance(properties, list)
        properties.append("tool_result_returned")

        with self.assertRaisesRegex(
            ScopedReviewError,
            "tool_result_properties_require_tool_topic",
        ):
            self._queue(records=broken_records)

    def test_completed_human_review_still_cannot_enable_training(self) -> None:
        queue = self._queue()
        checked_at = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)
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
