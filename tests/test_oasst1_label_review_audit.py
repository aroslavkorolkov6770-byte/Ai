"""Проверяет завершённый semantic pilot без изменения корпуса или ledger."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from evaluation import oasst1_human_review as quality
from evaluation.oasst1_label_review import DEFAULT_PATHS, LabelReviewPaths
from evaluation.oasst1_label_review_audit import (
    OASST1LabelReviewAuditError,
    audit_markdown,
    build_label_review_audit,
    write_audit,
)


class OASST1LabelReviewAuditTests(unittest.TestCase):
    """Проверяет реальный завершённый pilot и fail-closed границы."""

    @classmethod
    def setUpClass(cls) -> None:
        """Читает реальные решения без их записи или изменения."""

        cls.report = build_label_review_audit()

    def test_completed_pilot_has_exact_review_counts_and_train_only_ids(self) -> None:
        """Пять correction не следует объявлять качеством всего корпуса."""

        pilot = self.report["pilot"]
        self.assertEqual(pilot["reviewed_records"], 24)
        self.assertEqual(pilot["verdict_counts"], {"confirmed": 19, "corrected": 5, "rejected": 0})
        self.assertEqual(pilot["approved_train_records"], 75)
        self.assertEqual(pilot["approved_train_records_outside_pilot"], 51)
        self.assertEqual(len(pilot["items"]), 24)
        self.assertEqual(len({item["id"] for item in pilot["items"]}), 24)
        self.assertTrue(all(item["split"] == "train" for item in pilot["items"]))
        self.assertTrue(pilot["not_a_population_quality_estimate"])
        self.assertFalse(self.report["boundaries"]["training_started"])
        self.assertFalse(self.report["boundaries"]["promotion_allowed"])
        rendered = json.dumps(self.report, ensure_ascii=False)
        self.assertNotIn('"turns"', rendered)
        self.assertNotIn('"text"', rendered)
        self.assertIn("19", audit_markdown(self.report))

    def test_report_is_immutable_and_input_hashes_unchanged(self) -> None:
        """Отчёт пишет лишь новую папку и не трогает raw source/ledgers."""

        inputs = [
            quality.DEFAULT_SOURCE_PATH, quality.DEFAULT_QUEUE_PATH,
            quality.DEFAULT_DECISIONS_PATH, DEFAULT_PATHS.inventory_path,
            DEFAULT_PATHS.decisions_path,
        ]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            destination = root / "label-review-audit-v0.1"
            write_audit(self.report, destination, results_root=root)
            self.assertTrue((destination / "OASST1_LABEL_REVIEW_AUDIT.md").is_file())
            with self.assertRaisesRegex(OASST1LabelReviewAuditError, "report_exists"):
                write_audit(self.report, destination, results_root=root)
            with self.assertRaisesRegex(OASST1LabelReviewAuditError, "outside_results"):
                write_audit(self.report, Path(directory) / "outside", results_root=root)
        self.assertEqual(
            before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        )

    def test_incomplete_or_tampered_ledger_is_rejected(self) -> None:
        """Отсутствие одного решения или чужая метка не даёт completed audit."""

        original_lines = DEFAULT_PATHS.decisions_path.read_text(encoding="utf-8").splitlines()
        with tempfile.TemporaryDirectory() as directory:
            reviews_root = Path(directory) / "reviews"
            reviews_root.mkdir()
            ledger_path = reviews_root / "topic-decisions.jsonl"
            paths = LabelReviewPaths(decisions_path=ledger_path, reviews_root=reviews_root)
            ledger_path.write_text("\n".join(original_lines[:-1]) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(OASST1LabelReviewAuditError, "incomplete"):
                build_label_review_audit(paths)
            changed = json.loads(original_lines[0])
            changed["content_sha256"] = "0" * 64
            ledger_path.write_text(
                json.dumps(changed) + "\n" + "\n".join(original_lines[1:]) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(OASST1LabelReviewAuditError, "review_invalid"):
                build_label_review_audit(paths)


if __name__ == "__main__":
    unittest.main()
