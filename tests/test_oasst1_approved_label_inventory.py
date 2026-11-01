"""Проверяет pilot-инвентаризацию меток без изменения OASST1 данных."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from evaluation.oasst1_approved_label_inventory import (
    OASST1LabelInventoryError,
    audit_input_paths,
    build_label_inventory,
    write_inventory,
)


class OASST1ApprovedLabelInventoryTests(unittest.TestCase):
    """Не позволяет эвристическим меткам стать подтверждёнными автоматически."""

    @classmethod
    def setUpClass(cls) -> None:
        """Валидирует source/queue/ledger один раз для всего тестового набора."""

        cls.report = build_label_inventory()

    def test_pilot_uses_only_previously_approved_train_ids(self) -> None:
        """Review качества сохранён, а test/validation не смешиваются с train."""

        human = self.report["existing_human_review"]
        pilot = self.report["label_review_pilot"]
        self.assertEqual(human["reviewed_records"], 104)
        self.assertEqual(human["approved_records"], 97)
        self.assertEqual(human["rejected_records"], 7)
        self.assertEqual(human["approved_by_split"], {"test": 15, "train": 75, "validation": 7})
        self.assertFalse(human["topic_labels_human_confirmed"])
        self.assertEqual(pilot["selected_records"], 24)
        self.assertEqual(len({item["id"] for item in pilot["items"]}), 24)
        self.assertTrue(all(item["split"] == "train" for item in pilot["items"]))
        self.assertTrue(
            all(item["human_topic_verdict"] == "pending" for item in pilot["items"])
        )
        topic_counts = Counter(item["proposed_primary_topic"] for item in pilot["items"])
        self.assertEqual(len(topic_counts), 11)
        self.assertEqual(topic_counts["style_and_length"], 1)
        self.assertEqual(topic_counts["explanation_and_instruction"], 3)
        self.assertEqual(topic_counts["context_and_memory"], 3)
        self.assertEqual(topic_counts["casual_dialogue"], 3)
        self.assertFalse(self.report["boundaries"]["training_started"])
        self.assertFalse(self.report["boundaries"]["promotion_allowed"])
        rendered = json.dumps(self.report, ensure_ascii=False)
        self.assertNotIn('"turns"', rendered)
        self.assertNotIn('"text"', rendered)

    def test_versioned_report_does_not_modify_source_or_ledger(self) -> None:
        """Отчёт содержит только ID/hashes/метки и не перезаписывает входы."""

        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in audit_input_paths()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            destination = root / "label-inventory-v0.1"
            write_inventory(self.report, destination, results_root=root)
            rendered = "".join(
                path.read_text(encoding="utf-8") for path in sorted(destination.iterdir())
            )
            self.assertNotIn('"turns"', rendered)
            self.assertNotIn('"text"', rendered)
            with self.assertRaisesRegex(OASST1LabelInventoryError, "report_exists"):
                write_inventory(self.report, destination, results_root=root)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in audit_input_paths()}
        self.assertEqual(before, after)

    def test_output_outside_results_is_rejected(self) -> None:
        """Нельзя случайно записать pilot в source или посторонний каталог."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(OASST1LabelInventoryError, "outside_results"):
                write_inventory(
                    self.report,
                    root / "outside",
                    results_root=root / "results",
                )


if __name__ == "__main__":
    unittest.main()
