"""Проверяет аудит всех 75 train-меток без изменения данных или обучения."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from evaluation import oasst1_full_train_label_audit as audit
from evaluation import oasst1_human_review as quality
from evaluation.oasst1_label_review import DEFAULT_PATHS, REMAINING_PATHS


class OASST1FullTrainLabelAuditTests(unittest.TestCase):
    """Не допускает ложного статуса completed и утечки исходных реплик."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = audit.build_full_train_label_audit()

    def test_all_approved_train_ids_have_disjoint_human_verdicts(self) -> None:
        """Обе партии дают 75 ID и не затрагивают validation/test."""

        review = self.report["review"]
        self.assertEqual(review["reviewed_primary_topics"], 75)
        self.assertEqual(review["approved_train_records"], 75)
        self.assertEqual(review["pilot_records"], 24)
        self.assertEqual(review["remaining_batch_records"], 51)
        self.assertEqual(review["verdict_counts"], {"confirmed": 51, "corrected": 24, "rejected": 0})
        self.assertEqual(len({item["id"] for item in review["items"]}), 75)
        self.assertTrue(all(item["split"] == "train" for item in review["items"]))
        self.assertTrue(all(item["human_reviewed_primary_topic"] for item in review["items"]))
        self.assertFalse(self.report["boundaries"]["training_started"])
        self.assertFalse(self.report["boundaries"]["promotion_allowed"])

    def test_material_attention_is_only_a_metadata_review_queue(self) -> None:
        """Сигналы не объявляются авторско-правовым или quality verdict."""

        attention = self.report["material_attention"]
        self.assertEqual(attention["records_with_any_signal"], 44)
        self.assertEqual(attention["code_counts"]["long_assistant_turn"], 42)
        self.assertTrue(attention["heuristic_only"])
        self.assertTrue(attention["not_a_copyright_or_license_verdict"])
        self.assertTrue(all(item["human_material_review_status"] == "pending" for item in attention["items"]))
        rendered = json.dumps(self.report, ensure_ascii=False)
        self.assertNotIn('"turns"', rendered)
        self.assertNotIn('"text"', rendered)
        self.assertNotIn("https://", rendered)

    def test_immutable_report_does_not_change_inputs(self) -> None:
        """Связывает все входы по hash и пишет только в новую папку."""

        inputs = (
            quality.DEFAULT_SOURCE_PATH, quality.DEFAULT_QUEUE_PATH,
            quality.DEFAULT_DECISIONS_PATH, DEFAULT_PATHS.inventory_path,
            DEFAULT_PATHS.decisions_path, audit.PILOT_AUDIT_PATH,
            REMAINING_PATHS.inventory_path, REMAINING_PATHS.decisions_path,
        )
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        self.assertEqual(set(before.values()), set(self.report["inputs"].values()))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            destination = root / "audit-v0.2"
            audit.write_audit(self.report, destination, results_root=root)
            self.assertTrue((destination / "OASST1_FULL_TRAIN_LABEL_AUDIT.md").is_file())
            with self.assertRaisesRegex(audit.OASST1FullTrainLabelAuditError, "report_exists"):
                audit.write_audit(self.report, destination, results_root=root)
            with self.assertRaisesRegex(audit.OASST1FullTrainLabelAuditError, "outside_results"):
                audit.write_audit(self.report, Path(directory) / "outside", results_root=root)
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs})

    def test_incomplete_second_batch_or_modified_pilot_audit_fails_closed(self) -> None:
        """Один пропущенный verdict или изменённый старый аудит останавливает сборку."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            short_ledger = root / "reviews" / "short.jsonl"
            short_ledger.parent.mkdir()
            lines = REMAINING_PATHS.decisions_path.read_text(encoding="utf-8").splitlines()
            short_ledger.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
            temp_paths = replace(
                REMAINING_PATHS, decisions_path=short_ledger, reviews_root=short_ledger.parent
            )
            with patch.object(audit, "REMAINING_PATHS", temp_paths):
                with self.assertRaisesRegex(audit.OASST1FullTrainLabelAuditError, "incomplete"):
                    audit.build_full_train_label_audit()
            old = json.loads(audit.PILOT_AUDIT_PATH.read_text(encoding="utf-8"))
            old["pilot"]["reviewed_records"] = 23
            fake = root / "changed-pilot.json"
            fake.write_text(json.dumps(old), encoding="utf-8")
            with patch.object(audit, "PILOT_AUDIT_PATH", fake):
                with self.assertRaisesRegex(audit.OASST1FullTrainLabelAuditError, "pilot_drift"):
                    audit.build_full_train_label_audit()


if __name__ == "__main__":
    unittest.main()
