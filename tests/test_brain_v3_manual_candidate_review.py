"""Проверяет изоляцию и metadata-only очередь первого ручного Brain v3 candidate."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.brain_v3_manual_candidate_review import (
    DEFAULT_CANDIDATE_PATH,
    DEFAULT_SCOPE_PATH,
    ManualCandidateReviewError,
    PROJECT_ROOT,
    SCOPE_ID,
    build_review_queue,
    create_review_queue,
    load_review_candidate,
    load_scope,
    render_candidate_for_terminal,
    review_queue_markdown,
)


class BrainV3ManualCandidateReviewTests(unittest.TestCase):
    """Не даёт review-only материалу стать training-данными по ошибке."""

    def setUp(self) -> None:
        self.scope = load_scope()
        self.records, self.candidate_sha256 = load_review_candidate()

    def test_first_tranche_has_required_coverage_and_metadata_only_queue(self) -> None:
        report = build_review_queue(
            self.records,
            self.scope,
            candidate_path=DEFAULT_CANDIDATE_PATH,
            candidate_sha256=self.candidate_sha256,
        )
        rendered = json.dumps(report, ensure_ascii=False) + review_queue_markdown(report)

        self.assertEqual(report["candidate_records"], 24)
        self.assertEqual(report["turns_per_record"], 4)
        self.assertEqual(
            report["coverage"]["topics"],
            {
                "casual_dialogue": 4,
                "correction": 4,
                "emotional_support": 4,
                "privacy_and_memory": 4,
                "safety_boundaries": 4,
                "uncertainty_and_clarification": 4,
            },
        )
        self.assertEqual(
            report["coverage"]["human_review_scope"],
            {"full_review_required": 12, "stratified_review_pool": 12},
        )
        self.assertNotIn("Сегодня весь день бегал", rendered)
        self.assertNotIn("Мне так плохо", rendered)
        self.assertTrue(all("text" not in item and "turns" not in item for item in report["items"]))
        self.assertTrue(report["metadata_only"])
        self.assertFalse(report["candidate_is_active"])
        self.assertFalse(report["training_eligible"])
        self.assertFalse(report["training_started"])

    def test_invalid_turn_count_stops_before_queue_creation(self) -> None:
        broken_records = copy.deepcopy(self.records)
        broken_records[0]["turns"].pop()

        with self.assertRaisesRegex(ManualCandidateReviewError, "turn_count"):
            build_review_queue(
                broken_records,
                self.scope,
                candidate_path=DEFAULT_CANDIDATE_PATH,
                candidate_sha256=self.candidate_sha256,
            )

    def test_external_source_claim_stops_before_queue_creation(self) -> None:
        broken_records = copy.deepcopy(self.records)
        broken_records[0]["source"]["external_text_used"] = True

        with self.assertRaisesRegex(ManualCandidateReviewError, "source_invalid"):
            build_review_queue(
                broken_records,
                self.scope,
                candidate_path=DEFAULT_CANDIDATE_PATH,
                candidate_sha256=self.candidate_sha256,
            )

    def test_creation_writes_new_metadata_reports_without_dialogue_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results_root = Path(directory) / "results"
            report_directory = results_root / "manual-review-queue"
            report = create_review_queue(
                DEFAULT_SCOPE_PATH,
                DEFAULT_CANDIDATE_PATH,
                report_directory,
                results_root=results_root,
            )
            queue_path = report_directory / "brain_v3_manual_candidate_review_queue.json"
            markdown_path = report_directory / "BRAIN_V3_MANUAL_CANDIDATE_REVIEW_QUEUE.md"
            written = queue_path.read_text(encoding="utf-8") + markdown_path.read_text(
                encoding="utf-8"
            )

            self.assertTrue(queue_path.is_file())
            self.assertTrue(markdown_path.is_file())
            self.assertNotIn("Сегодня весь день бегал", written)
            self.assertNotIn("Мне так плохо", written)
            self.assertEqual(report["review_status"], "unreviewed")
            self.assertFalse(report["candidate_is_active"])

    def test_explicit_terminal_view_is_the_only_helper_that_returns_turn_text(self) -> None:
        rendered = render_candidate_for_terminal(
            self.records,
            self.scope,
            "brain-v3-owned-manual-v1-003",
        )

        self.assertIn("Пользователь:", rendered)
        self.assertIn("ВатэсAi:", rendered)
        self.assertIn("пустой коробке", rendered)

    def test_candidate_source_is_not_registered_in_active_manifest(self) -> None:
        manifest_path = PROJECT_ROOT / "data" / "brain_v3" / "corpus_manifest.v1.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertNotIn(SCOPE_ID, json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
