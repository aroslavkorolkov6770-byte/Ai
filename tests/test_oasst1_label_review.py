"""Проверяет отдельный human topic review без решений за пользователя."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from evaluation import oasst1_human_review as quality
from evaluation.oasst1_label_review import (
    INVENTORY_PATH,
    LabelReviewPaths,
    OASST1LabelReviewError,
    next_dialogue,
    record_topic_decision,
    review_status,
)
from evaluation.oasst1_label_review_web import create_label_review_app


class OASST1LabelReviewTests(unittest.TestCase):
    """Изолирует новый ledger и сохраняет прежние данные нетронутыми."""

    def setUp(self) -> None:
        """Для записи verdict использует только временный каталог."""

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = LabelReviewPaths(
            decisions_path=self.root / "reviews" / "topic-decisions.jsonl",
            reviews_root=self.root / "reviews",
        )

    def tearDown(self) -> None:
        """Удаляет исключительно собственный временный тестовый каталог."""

        self.temporary.cleanup()

    def test_next_shows_full_train_dialogue_and_status_has_no_text(self) -> None:
        """Экран получает один диалог; прогресс не раскрывает реплики."""

        first = next_dialogue(self.paths)
        self.assertIsNotNone(first)
        self.assertEqual(first["split"], "train")
        self.assertGreaterEqual(len(first["turns"]), 4)
        self.assertTrue(all(turn["text"] for turn in first["turns"]))
        status = review_status(self.paths)
        self.assertEqual(status["pending_records"], 24)
        self.assertFalse(status["training_candidate_allowed"])
        self.assertNotIn("turns", json.dumps(status))
        self.assertFalse(self.paths.decisions_path.exists())

    def test_explicit_topic_verdict_is_metadata_only_and_duplicate_fails(self) -> None:
        """Решение сохраняет ID и тему, но не копирует исходный разговор."""

        first = next_dialogue(self.paths)
        before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (INVENTORY_PATH, quality.DEFAULT_QUEUE_PATH,
                         quality.DEFAULT_DECISIONS_PATH, quality.DEFAULT_SOURCE_PATH)
        }
        with self.assertRaisesRegex(OASST1LabelReviewError, "full_read_required"):
            record_topic_decision(first["id"], "confirmed", paths=self.paths)
        self.assertFalse(self.paths.decisions_path.exists())
        result = record_topic_decision(
            first["id"], "confirmed", read_confirmed=True, paths=self.paths
        )
        self.assertEqual(result["topic_verdict"], "confirmed")
        self.assertEqual(review_status(self.paths)["pending_records"], 23)
        ledger = self.paths.decisions_path.read_text(encoding="utf-8")
        self.assertNotIn(first["turns"][0]["text"], ledger)
        self.assertNotIn('"turns"', ledger)
        with self.assertRaisesRegex(OASST1LabelReviewError, "duplicate_decision"):
            record_topic_decision(first["id"], "rejected", read_confirmed=True, paths=self.paths)
        second = next_dialogue(self.paths)
        self.assertNotEqual(second["id"], first["id"])
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}
        self.assertEqual(before, after)

    def test_correction_is_restricted_to_known_different_topic(self) -> None:
        """Произвольный текст не превращается в semantic label."""

        first = next_dialogue(self.paths)
        for invalid in (None, first["proposed_primary_topic"], "unknown_topic"):
            with self.assertRaisesRegex(OASST1LabelReviewError, "correction_invalid"):
                record_topic_decision(
                    first["id"], "corrected", corrected_topic=invalid,
                    read_confirmed=True, paths=self.paths,
                )
        alternative = next(
            topic for topic in first["available_topics"]
            if topic != first["proposed_primary_topic"]
        )
        record_topic_decision(
            first["id"], "corrected", corrected_topic=alternative,
            read_confirmed=True, paths=self.paths,
        )
        self.assertEqual(review_status(self.paths)["verdict_counts"]["corrected"], 1)

    def test_unknown_id_tampered_inventory_and_malformed_ledger_fail_closed(self) -> None:
        """Нельзя добавить чужой ID или использовать изменённый отчёт."""

        with self.assertRaisesRegex(OASST1LabelReviewError, "unknown_candidate"):
            record_topic_decision("not-in-pilot", "confirmed", read_confirmed=True, paths=self.paths)
        with self.assertRaisesRegex(OASST1LabelReviewError, "unknown_candidate"):
            next_dialogue(self.paths, after_id="not-in-pilot")
        self.paths.decisions_path.parent.mkdir(parents=True)
        self.paths.decisions_path.write_text('{"candidate_id": []}\n', encoding="utf-8")
        with self.assertRaisesRegex(OASST1LabelReviewError, "decision_invalid"):
            review_status(self.paths)

        saved = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        saved["label_review_pilot"]["items"][0]["split"] = "test"
        # Проверяем drift на временном results-root, не создавая файл в проекте.
        temp_results = self.root / "results"
        temp_results.mkdir()
        tampered_path = temp_results / "tampered.json"
        tampered_path.write_text(json.dumps(saved), encoding="utf-8")
        tampered_paths = LabelReviewPaths(
            inventory_path=tampered_path, inventory_root=temp_results,
            decisions_path=self.paths.decisions_path, reviews_root=self.paths.reviews_root,
        )
        with self.assertRaisesRegex(OASST1LabelReviewError, "inventory_drift"):
            review_status(tampered_paths)

    def test_browser_routes_are_separate_from_chat_and_quality_review(self) -> None:
        """Новый экран имеет только свои локальные endpoints."""

        paths = {route.path for route in create_label_review_app(self.paths).routes}
        self.assertEqual(paths, {"/", "/health", "/api/status", "/api/next", "/api/decisions"})


if __name__ == "__main__":
    unittest.main()
