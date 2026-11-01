"""Проверяет metadata-only audit завершённого review candidate v1.5."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.brain_v3_natural_dialogue_review_audit import (
    DECISIONS_PATH,
    QUEUE_PATH,
    REVIEW_CANDIDATE_PATH,
    SCOPE_PATH,
    NaturalDialogueReviewAuditError,
    build_completed_review_audit,
    write_audit_report,
)


class NaturalDialogueReviewAuditTests(unittest.TestCase):
    """Не даёт полному review ошибочно открыть training или утечь репликам."""

    def setUp(self) -> None:
        self.report = build_completed_review_audit()

    def test_completed_review_is_confirmed_but_training_stays_blocked(self) -> None:
        audit = self.report["audit"]
        rendered = json.dumps(self.report, ensure_ascii=False)

        self.assertEqual(audit["records_checked"], 6)
        self.assertTrue(audit["all_candidate_ids_reviewed"])
        self.assertTrue(audit["all_verdicts_approved"])
        self.assertTrue(audit["full_human_review_coverage_complete"])
        self.assertTrue(audit["static_dialogue_boundary_confirmed"])
        self.assertTrue(audit["queue_metadata_only"])
        self.assertFalse(audit["training_allowed"])
        self.assertFalse(audit["promotion_allowed"])
        self.assertFalse(self.report["candidate_is_active"])
        self.assertFalse(self.report["training_started"])
        self.assertNotIn('"turns"', rendered)
        self.assertNotIn('"text"', rendered)

    def test_writing_audit_preserves_review_inputs_and_excludes_dialogue_text(self) -> None:
        protected_paths = (SCOPE_PATH, REVIEW_CANDIDATE_PATH, QUEUE_PATH, DECISIONS_PATH)
        hashes_before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected_paths
        }
        source_text = json.loads(
            REVIEW_CANDIDATE_PATH.read_text(encoding="utf-8").splitlines()[0]
        )["turns"][0]["text"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_directory = root / "results" / "audit-v0.1"
            write_audit_report(
                self.report,
                report_directory,
                results_root=root / "results",
            )
            rendered = "".join(
                path.read_text(encoding="utf-8")
                for path in sorted(report_directory.iterdir())
            )

            self.assertTrue(
                (report_directory / "brain_v3_natural_dialogue_completed_review_audit.json").is_file()
            )
            self.assertTrue(
                (report_directory / "BRAIN_V3_NATURAL_DIALOGUE_COMPLETED_REVIEW_AUDIT.md").is_file()
            )
            self.assertNotIn(source_text, rendered)
            self.assertNotIn('"turns"', rendered)
            self.assertNotIn('"text"', rendered)

        hashes_after = {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected_paths
        }
        self.assertEqual(hashes_before, hashes_after)

    def test_output_outside_results_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(NaturalDialogueReviewAuditError):
                write_audit_report(
                    self.report,
                    root / "outside" / "audit-v0.1",
                    results_root=root / "results",
                )


if __name__ == "__main__":
    unittest.main()
