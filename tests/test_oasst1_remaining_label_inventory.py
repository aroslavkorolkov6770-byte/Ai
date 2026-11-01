"""Проверяет 51-ID очередь без обучения и без повторного использования pilot ID."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation import oasst1_human_review as quality
from evaluation import oasst1_remaining_label_inventory as remaining
from evaluation.oasst1_label_review import (
    DEFAULT_PATHS,
    REMAINING_PATHS,
    LabelReviewPaths,
    OASST1LabelReviewError,
    next_dialogue,
    record_topic_decision,
    review_status,
)
from evaluation.oasst1_label_review_web import create_label_review_app


class OASST1RemainingLabelInventoryTests(unittest.TestCase):
    """Сохраняет old pilot и изолирует решения второй партии."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = remaining.build_remaining_inventory()

    def test_queue_is_exact_disjoint_approved_train_remainder(self) -> None:
        """Ни один из 24 уже просмотренных ID не попадает в новую очередь."""

        pilot_items = json.loads(remaining.PILOT_AUDIT_PATH.read_text(encoding="utf-8"))["pilot"]["items"]
        old_ids = {item["id"] for item in pilot_items}
        new_items = self.report["label_review_pilot"]["items"]
        new_ids = {item["id"] for item in new_items}
        self.assertEqual(len(old_ids), 24)
        self.assertEqual(len(new_items), 51)
        self.assertEqual(len(new_ids), 51)
        self.assertFalse(old_ids & new_ids)
        self.assertTrue(all(item["split"] == "train" for item in new_items))
        self.assertTrue(all(item["human_topic_verdict"] == "pending" for item in new_items))
        self.assertEqual(self.report["existing_human_review"]["approved_by_split"]["train"], 75)
        self.assertEqual(len(old_ids | new_ids), 75)
        self.assertFalse(self.report["boundaries"]["training_started"])

    def test_attention_is_metadata_only_not_a_license_verdict(self) -> None:
        """Признаки ссылок/цитат не копируют текст и не выносят правового вывода."""

        attention = self.report["quote_attention"]
        self.assertTrue(attention["heuristic_only"])
        self.assertTrue(attention["not_a_copyright_or_license_verdict"])
        self.assertEqual(attention["records_with_any_signal"], 32)
        self.assertEqual(attention["signal_code_counts"]["long_assistant_turn"], 30)
        rendered = json.dumps(self.report, ensure_ascii=False)
        self.assertNotIn('"turns"', rendered)
        self.assertNotIn('"text"', rendered)
        codes = remaining.quote_attention_codes({"turns": [
            {"role": "user", "text": "https://example.invalid"},
            {"role": "assistant", "text": "Согласно источнику, см. https://example.invalid"},
        ]})
        self.assertEqual(codes, ["assistant_contains_link", "attribution_word"])

    def test_output_is_immutable_and_pilot_audit_drift_fails_closed(self) -> None:
        """Отчёт не перезаписывает исходные данные и зависит от pilot audit."""

        inputs = (
            quality.DEFAULT_SOURCE_PATH, quality.DEFAULT_QUEUE_PATH,
            quality.DEFAULT_DECISIONS_PATH, remaining.PILOT_AUDIT_PATH,
            DEFAULT_PATHS.decisions_path,
        )
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            destination = root / "remaining-v0.2"
            remaining.write_inventory(self.report, destination, results_root=root)
            with self.assertRaisesRegex(remaining.OASST1RemainingInventoryError, "report_exists"):
                remaining.write_inventory(self.report, destination, results_root=root)
            with self.assertRaisesRegex(remaining.OASST1RemainingInventoryError, "outside_results"):
                remaining.write_inventory(self.report, Path(directory) / "outside", results_root=root)
            old = json.loads(remaining.PILOT_AUDIT_PATH.read_text(encoding="utf-8"))
            old["pilot"]["reviewed_records"] = 23
            fake = Path(directory) / "tampered.json"
            fake.write_text(json.dumps(old), encoding="utf-8")
            with patch.object(remaining, "PILOT_AUDIT_PATH", fake):
                with self.assertRaisesRegex(remaining.OASST1RemainingInventoryError, "pilot_audit_drift"):
                    remaining.build_remaining_inventory()
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs})

    def test_second_review_ledger_is_separate_and_requires_human_read(self) -> None:
        """Новая партия не меняет 24 решений первой партии."""

        with tempfile.TemporaryDirectory() as directory:
            reviews_root = Path(directory) / "reviews"
            paths = LabelReviewPaths(
                inventory_path=REMAINING_PATHS.inventory_path,
                decisions_path=reviews_root / "decisions-v0.2.jsonl",
                reviews_root=reviews_root,
                batch_id="remaining_train_v0.2",
            )
            before = hashlib.sha256(DEFAULT_PATHS.decisions_path.read_bytes()).hexdigest()
            self.assertEqual(review_status(paths)["pending_records"], 51)
            candidate = next_dialogue(paths)
            self.assertEqual(candidate["split"], "train")
            self.assertIn("quote_attention_codes", candidate)
            with self.assertRaisesRegex(OASST1LabelReviewError, "full_read_required"):
                record_topic_decision(candidate["id"], "confirmed", paths=paths)
            self.assertFalse(paths.decisions_path.exists())
            record_topic_decision(candidate["id"], "confirmed", read_confirmed=True, paths=paths)
            self.assertEqual(review_status(paths)["pending_records"], 50)
            self.assertEqual(hashlib.sha256(DEFAULT_PATHS.decisions_path.read_bytes()).hexdigest(), before)
            self.assertEqual(
                {route.path for route in create_label_review_app(paths).routes},
                {"/", "/health", "/api/status", "/api/next", "/api/decisions"},
            )


if __name__ == "__main__":
    unittest.main()
