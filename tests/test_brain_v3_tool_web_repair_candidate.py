"""Проверяет изолированный tool/web repair candidate без запуска обучения."""

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
    / "brain-v3-tool-web-result-repair-v1.3.scope.json"
)
CANDIDATE_PATH = (
    ROOT
    / "data"
    / "brain_v3"
    / "reviews"
    / "brain-v3-tool-web-result-repair-v1.3.review.jsonl"
)


class BrainV3ToolWebRepairCandidateTests(unittest.TestCase):
    """Не даёт marker-based repair-партии стать неявным training material."""

    def setUp(self) -> None:
        self.scope = load_scope(SCOPE_PATH)
        self.records, self.candidate_sha256 = load_candidate(CANDIDATE_PATH)

    def _queue(self, records: list[dict[str, object]] | None = None, scope: dict[str, object] | None = None) -> dict[str, object]:
        return build_queue(
            self.records if records is None else records,
            self.scope if scope is None else scope,
            candidate_path=CANDIDATE_PATH,
            candidate_sha256=self.candidate_sha256,
        )

    def test_candidate_is_metadata_only_and_requires_full_review(self) -> None:
        queue = self._queue()
        rendered = json.dumps(queue, ensure_ascii=False)

        self.assertEqual(queue["candidate_records"], 6)
        self.assertEqual(queue["coverage"]["topics"], {"tool_routing": 3, "web_grounding": 3})
        self.assertEqual(queue["coverage"]["human_review_scope"], {"full_review_required": 6})
        self.assertEqual(queue["coverage"]["response_properties"]["tool_result_returned"], 6)
        self.assertEqual(queue["coverage"]["response_properties"]["no_action_promise"], 6)
        self.assertTrue(queue["metadata_only"])
        self.assertFalse(queue["candidate_is_active"])
        self.assertFalse(queue["training_eligible"])
        self.assertNotIn("[[weather.", rendered)
        self.assertNotIn("[[web.", rendered)

    def test_no_action_promise_requires_a_final_tool_result_property(self) -> None:
        broken_records = copy.deepcopy(self.records)
        properties = broken_records[0]["scenario"]["expected_response_properties"]
        assert isinstance(properties, list)
        properties.remove("tool_result_returned")

        with self.assertRaisesRegex(ScopedReviewError, "no_action_promise_requires_tool_result"):
            self._queue(records=broken_records)

    def test_tool_result_metadata_is_rejected_for_a_non_tool_topic(self) -> None:
        broken_records = copy.deepcopy(self.records)
        broken_scope = copy.deepcopy(self.scope)
        tranche = broken_scope["first_review_tranche"]
        review = broken_scope["review"]
        assert isinstance(tranche, dict)
        assert isinstance(review, dict)
        tranche["topics"] = {
            "tool_routing": 2,
            "web_grounding": 3,
            "explanation_and_instruction": 1,
        }
        tranche["topic_goal_map"] = {
            "tool_routing": "route_verified_fact",
            "web_grounding": "route_verified_fact",
            "explanation_and_instruction": "explain_or_plan",
        }
        tranche["required_dialogue_goals"] = [
            "route_verified_fact",
            "explain_or_plan",
        ]
        review["full_review_topics"] = [
            "tool_routing",
            "web_grounding",
            "explanation_and_instruction",
        ]
        scenario = broken_records[0]["scenario"]
        assert isinstance(scenario, dict)
        scenario["topic"] = "explanation_and_instruction"
        scenario["dialogue_goal"] = "explain_or_plan"

        with self.assertRaisesRegex(ScopedReviewError, "tool_result_properties_require_tool_topic"):
            self._queue(records=broken_records, scope=broken_scope)

    def test_completed_human_review_still_cannot_enable_training(self) -> None:
        queue = self._queue()
        checked_at = datetime(2026, 9, 30, 6, 42, tzinfo=timezone.utc)
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
