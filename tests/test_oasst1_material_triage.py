"""Проверяет triage обеих ролей без вывода диалогов и правовых verdict."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation import oasst1_human_review as quality
from evaluation import oasst1_material_triage as triage


class OASST1MaterialTriageTests(unittest.TestCase):
    """Сигнал — только повод для чтения, а не разрешение на обучение."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = triage.build_material_triage()

    def test_all_75_train_ids_partition_and_review_sample_are_stable(self) -> None:
        """Новая проверка охватывает user и assistant и не смешивает split."""

        data = self.report["triage"]
        self.assertEqual(data["train_records"], 75)
        self.assertEqual(data["group_counts"], {
            "priority_signal": 14, "length_only": 31, "no_signal": 30
        })
        self.assertEqual(data["suggested_review_sample_count"], 24)
        self.assertEqual(len(set(data["suggested_review_sample_ids"])), 24)
        self.assertEqual(len({item["id"] for item in data["all_items"]}), 75)
        self.assertTrue(all(item["split"] == "train" for item in data["all_items"]))
        self.assertTrue(all(item["human_material_verdict"] == "pending" for item in data["all_items"]))
        self.assertTrue(data["heuristic_only"])
        self.assertTrue(data["not_a_copyright_or_license_verdict"])
        self.assertFalse(self.report["boundaries"]["training_started"])
        self.assertFalse(self.report["primary_source_references"]["record_level_rights_confirmed"])

    def test_user_signals_are_detected_without_copying_text(self) -> None:
        """URL/цитаты у пользователя учитываются отдельно от assistant."""

        record = {"turns": [
            {"role": "user", "text": "Согласно источнику: https://example.invalid " + "«" + "а" * 130 + "»"},
            {"role": "assistant", "text": "Ответ без цитаты."},
        ]}
        codes = triage.material_attention_codes(record)
        self.assertIn("user_contains_url", codes)
        self.assertIn("user_attribution_word", codes)
        self.assertIn("user_long_quoted_span", codes)
        self.assertNotIn("assistant_contains_url", codes)
        rendered = json.dumps(self.report, ensure_ascii=False)
        self.assertNotIn('"turns"', rendered)
        self.assertNotIn('"text"', rendered)
        self.assertNotIn("https://example.invalid", rendered)

    def test_immutable_output_and_full_audit_drift_gate(self) -> None:
        """Не переписывает source или старый audit и отвергает его drift."""

        inputs = (quality.DEFAULT_SOURCE_PATH, quality.DEFAULT_QUEUE_PATH, triage.FULL_AUDIT_PATH)
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            destination = root / "material-v0.3"
            triage.write_triage(self.report, destination, results_root=root)
            with self.assertRaisesRegex(triage.OASST1MaterialTriageError, "report_exists"):
                triage.write_triage(self.report, destination, results_root=root)
            with self.assertRaisesRegex(triage.OASST1MaterialTriageError, "outside_results"):
                triage.write_triage(self.report, Path(directory) / "outside", results_root=root)
            changed = json.loads(triage.FULL_AUDIT_PATH.read_text(encoding="utf-8"))
            changed["review"]["reviewed_primary_topics"] = 74
            fake = Path(directory) / "changed-full-audit.json"
            fake.write_text(json.dumps(changed), encoding="utf-8")
            with patch.object(triage, "FULL_AUDIT_PATH", fake):
                with self.assertRaisesRegex(triage.OASST1MaterialTriageError, "full_audit_drift"):
                    triage.build_material_triage()
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs})


if __name__ == "__main__":
    unittest.main()
