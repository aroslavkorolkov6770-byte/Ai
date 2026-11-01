"""Проверяет новую review-only партию незакрытых разговорных навыков Brain v3."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from evaluation.brain_v3_scoped_review import build_queue, load_candidate, load_scope


ROOT = Path(__file__).resolve().parents[1]
SCOPE_PATH = ROOT / "data" / "brain_v3" / "reviews" / "brain-v3-semantic-gap-candidate-v1.2.scope.json"
CANDIDATE_PATH = ROOT / "data" / "brain_v3" / "reviews" / "brain-v3-semantic-gap-candidate-v1.2.review.jsonl"
BASELINE_REPORT_PATH = (
    ROOT
    / "evaluation"
    / "results"
    / "brain-v3-owned-reviewed-dialogues-audit-v0.1"
    / "brain_v3_reviewed_staging_audit.json"
)


class BrainV3SemanticGapCandidateTests(unittest.TestCase):
    """Не даёт review-only semantic-gap материалу стать training-данными."""

    def setUp(self) -> None:
        self.scope = load_scope(SCOPE_PATH)
        self.records, self.candidate_sha256 = load_candidate(CANDIDATE_PATH)

    def test_candidate_covers_only_the_targeted_missing_semantic_cells(self) -> None:
        queue = build_queue(
            self.records,
            self.scope,
            candidate_path=CANDIDATE_PATH,
            candidate_sha256=self.candidate_sha256,
        )

        self.assertEqual(queue["scope_id"], "brain-v3-semantic-gap-candidate-v1.2")
        self.assertEqual(queue["candidate_records"], 14)
        self.assertEqual(
            queue["coverage"]["topics"],
            {
                "context_and_memory": 2,
                "explanation_and_instruction": 2,
                "opinion_and_choice": 2,
                "style_and_length": 2,
                "tool_routing": 2,
                "uncertainty_and_clarification": 2,
                "web_grounding": 2,
            },
        )
        self.assertEqual(
            queue["coverage"]["human_review_scope"],
            {"full_review_required": 6, "stratified_review_pool": 8},
        )
        self.assertTrue(queue["metadata_only"])
        self.assertFalse(queue["candidate_is_active"])
        self.assertFalse(queue["training_eligible"])
        self.assertFalse(queue["training_started"])

    def test_queue_excludes_candidate_turns_and_preserves_review_only_boundary(self) -> None:
        queue = build_queue(
            self.records,
            self.scope,
            candidate_path=CANDIDATE_PATH,
            candidate_sha256=self.candidate_sha256,
        )
        rendered = json.dumps(queue, ensure_ascii=False)

        self.assertNotIn("Во сколько сегодня закат", rendered)
        self.assertNotIn("Черновик позволяет", rendered)
        self.assertEqual(self.scope["status"], "human_review_in_progress")
        self.assertFalse(self.scope["candidate_is_active"])
        self.assertFalse(self.scope["training_eligible"])

    def test_scope_binds_to_the_verified_baseline_coverage_report(self) -> None:
        expected_hash = hashlib.sha256(BASELINE_REPORT_PATH.read_bytes()).hexdigest()

        self.assertEqual(
            self.scope["coverage_basis"]["coverage_report_sha256"],
            expected_hash,
        )


if __name__ == "__main__":
    unittest.main()
