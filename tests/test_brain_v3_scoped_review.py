"""Проверяет reusable review-only workflow repair candidate без обучения."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from evaluation.brain_v3_scoped_review import (
    APPROVAL_REASON_CODES,
    DEFAULT_CANDIDATE_PATH,
    DEFAULT_SCOPE_PATH,
    ScopedReviewError,
    build_queue,
    build_summary,
    create_decision,
    create_queue,
    load_candidate,
    load_scope,
    queue_markdown,
    render_candidate_for_terminal,
)


class BrainV3ScopedReviewTests(unittest.TestCase):
    """Не даёт repair-only материалу попасть в training по технической ошибке."""

    def setUp(self) -> None:
        self.scope = load_scope()
        self.records, self.candidate_sha256 = load_candidate()

    def _queue(self) -> dict[str, object]:
        return build_queue(
            self.records,
            self.scope,
            candidate_path=DEFAULT_CANDIDATE_PATH,
            candidate_sha256=self.candidate_sha256,
        )

    def test_repair_candidate_has_complete_metadata_only_queue(self) -> None:
        queue = self._queue()
        rendered = json.dumps(queue, ensure_ascii=False) + queue_markdown(queue)

        self.assertEqual(queue["scope_id"], "brain-v3-memory-repair-candidate-v1.1")
        self.assertEqual(queue["candidate_records"], 4)
        self.assertEqual(queue["turns_per_record"], 4)
        self.assertEqual(queue["coverage"]["topics"], {"privacy_and_memory": 4})
        self.assertEqual(
            queue["coverage"]["human_review_scope"],
            {"full_review_required": 4},
        )
        self.assertNotIn("мне удобны короткие ответы", rendered)
        self.assertNotIn("северный вход в парк", rendered)
        self.assertTrue(queue["metadata_only"])
        self.assertFalse(queue["candidate_is_active"])
        self.assertFalse(queue["training_eligible"])
        self.assertFalse(queue["training_started"])

    def test_invalid_turn_count_stops_before_queue_creation(self) -> None:
        broken_records = copy.deepcopy(self.records)
        broken_records[0]["turns"].pop()

        with self.assertRaisesRegex(ScopedReviewError, "turn_count"):
            build_queue(
                broken_records,
                self.scope,
                candidate_path=DEFAULT_CANDIDATE_PATH,
                candidate_sha256=self.candidate_sha256,
            )

    def test_explicit_view_works_even_when_records_are_an_iterator(self) -> None:
        rendered = render_candidate_for_terminal(
            iter(self.records),
            self.scope,
            "brain-v3-memory-repair-v1.1-003",
        )

        self.assertIn("Пользователь:", rendered)
        self.assertIn("ВатэсAi:", rendered)
        self.assertIn("северный вход", rendered)

    def test_full_review_can_finish_without_automatic_training_rights(self) -> None:
        queue = self._queue()
        checked_at = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
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
        self.assertEqual(summary["approved_records"], 4)
        self.assertEqual(summary["rejected_records"], 0)
        self.assertFalse(summary["training_candidate_allowed"])
        self.assertFalse(summary["promotion_allowed"])

    def test_queue_creation_writes_only_metadata_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results_root = Path(directory) / "results"
            report_directory = results_root / "repair-review-queue"
            queue = create_queue(
                DEFAULT_SCOPE_PATH,
                DEFAULT_CANDIDATE_PATH,
                report_directory,
                results_root=results_root,
            )
            written = "".join(
                path.read_text(encoding="utf-8")
                for path in sorted(report_directory.iterdir())
            )

            self.assertTrue((report_directory / "brain_v3_scoped_review_queue.json").is_file())
            self.assertTrue((report_directory / "BRAIN_V3_SCOPED_REVIEW_QUEUE.md").is_file())
            self.assertNotIn("работаю по ночам", written)
            self.assertNotIn("северный вход в парк", written)
            self.assertFalse(queue["training_started"])


if __name__ == "__main__":
    unittest.main()
