"""Проверяет итоговый audit v1.6 и отдельный медицинский training gate."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation import brain_v3_scoped_review as review
from evaluation.brain_v3_core_dialogue_review_audit import (
    CoreDialogueReviewAuditError,
    audit_input_paths,
    build_audit,
    write_audit_report,
)


class CoreDialogueReviewAuditTests(unittest.TestCase):
    """Не даёт завершённому human review незаметно открыть обучение."""

    @classmethod
    def setUpClass(cls) -> None:
        """Строит один metadata-only отчёт для всех read-only проверок."""

        cls.report = build_audit()

    def test_six_approvals_still_leave_medical_and_training_gates_closed(self) -> None:
        """Все verdict подтверждены, но medical-domain review ещё нет."""

        audit = self.report["audit"]
        self.assertEqual(audit["records_checked"], 6)
        self.assertEqual(audit["approved_records"], 6)
        self.assertTrue(audit["full_human_review_coverage_complete"])
        self.assertEqual(audit["medical_domain_review_status"], "pending")
        self.assertFalse(audit["training_allowed"])
        self.assertFalse(audit["promotion_allowed"])
        self.assertFalse(self.report["staging_created"])
        self.assertFalse(self.report["active_manifest_updated"])
        self.assertFalse(self.report["training_started"])
        rendered = json.dumps(self.report, ensure_ascii=False)
        self.assertNotIn('"turns"', rendered)
        self.assertNotIn('"text"', rendered)

    def test_report_write_preserves_inputs_and_has_no_dialogue(self) -> None:
        """Итоговая папка не переписывает source/ledger и не копирует реплики."""

        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in audit_input_paths()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            destination = root / "audit-v0.1"
            write_audit_report(self.report, destination, results_root=root)
            rendered = "".join(
                path.read_text(encoding="utf-8") for path in sorted(destination.iterdir())
            )
            self.assertNotIn('"turns"', rendered)
            self.assertNotIn('"text"', rendered)
            with self.assertRaisesRegex(CoreDialogueReviewAuditError, "report_exists"):
                write_audit_report(self.report, destination, results_root=root)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in audit_input_paths()}
        self.assertEqual(before, after)

    def test_unsafe_output_and_raw_content_are_rejected(self) -> None:
        """Отчёт нельзя записать мимо результатов или с текстом диалога."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            with self.assertRaisesRegex(CoreDialogueReviewAuditError, "outside_results"):
                write_audit_report(self.report, Path(directory) / "outside", results_root=root)
            unsafe = copy.deepcopy(self.report)
            unsafe["text"] = "Нельзя переносить реплики в audit"
            with self.assertRaisesRegex(CoreDialogueReviewAuditError, "not_metadata_only"):
                write_audit_report(unsafe, root / "unsafe", results_root=root)

    def test_unverified_domain_status_cannot_open_gate(self) -> None:
        """Замена pending на неподтверждённое complete не проходит audit."""

        from evaluation.brain_v3_core_dialogue_review_audit import SCOPE_PATH

        original = review.load_scope(SCOPE_PATH)
        changed = copy.deepcopy(original)
        changed["medical_domain_review"]["status"] = "complete"
        with patch(
            "evaluation.brain_v3_core_dialogue_review_audit.review.load_scope",
            return_value=changed,
        ):
            with self.assertRaisesRegex(CoreDialogueReviewAuditError, "domain_gate_invalid"):
                build_audit()


if __name__ == "__main__":
    unittest.main()
