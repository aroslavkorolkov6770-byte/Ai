"""Проверяет metadata-only staging/audit путь для v1.4 без training JSONL."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.brain_v3_pickup_context_runtime_staging import (
    ACTIVE_MANIFEST_PATH,
    DECISIONS_PATH,
    EXPECTED_ORIGIN_IDS,
    QUEUE_PATH,
    REVIEW_CANDIDATE_PATH,
    SCOPE_PATH,
    PickupContextRuntimeStagingError,
    audit_runtime_contract_staging_candidate,
    build_runtime_contract_snapshot,
    create_runtime_contract_staging_candidate,
    write_audit_report,
)


class PickupContextRuntimeStagingTests(unittest.TestCase):
    """Не даёт web-marker contract стать corpus-строкой или скрытым training input."""

    def setUp(self) -> None:
        self.snapshot = build_runtime_contract_snapshot()

    def test_snapshot_contains_only_metadata_for_the_two_approved_contracts(self) -> None:
        rendered = json.dumps(self.snapshot, ensure_ascii=False)
        selection = self.snapshot["selection"]

        self.assertEqual(
            [item["origin_candidate_id"] for item in selection],
            list(EXPECTED_ORIGIN_IDS),
        )
        self.assertEqual(self.snapshot["review_proof"]["approved_records"], 2)
        self.assertTrue(
            self.snapshot["review_proof"]["required_human_review_coverage_complete"]
        )
        self.assertTrue(self.snapshot["dynamic_runtime_content_excluded"])
        self.assertFalse(self.snapshot["contains_raw_dialogue_text"])
        self.assertFalse(self.snapshot["training_eligible"])
        self.assertFalse(self.snapshot["promotion_allowed"])
        self.assertNotIn("[[", rendered)
        self.assertNotIn('"turns"', rendered)
        self.assertNotIn('"text"', rendered)

    def test_creation_is_immutable_and_does_not_change_review_or_active_inputs(self) -> None:
        protected_paths = (
            SCOPE_PATH,
            REVIEW_CANDIDATE_PATH,
            QUEUE_PATH,
            DECISIONS_PATH,
            ACTIVE_MANIFEST_PATH,
        )
        hashes_before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected_paths
        }
        source_records = [
            json.loads(line)
            for line in REVIEW_CANDIDATE_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        source_text = source_records[0]["turns"][0]["text"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging_root = root / "staging"
            results_root = root / "results"
            candidate_path = staging_root / "runtime-contract.json"
            report_directory = results_root / "creation"
            summary = create_runtime_contract_staging_candidate(
                candidate_path,
                report_directory,
                staging_root=staging_root,
                results_root=results_root,
            )
            candidate_text = candidate_path.read_text(encoding="utf-8")
            report_text = "".join(
                path.read_text(encoding="utf-8")
                for path in sorted(report_directory.iterdir())
            )

            self.assertTrue(candidate_path.is_file())
            self.assertTrue((report_directory / "brain_v3_runtime_contract_staging_candidate.json").is_file())
            self.assertFalse(summary["candidate_is_active"])
            self.assertFalse(summary["training_started"])
            self.assertFalse(summary["promotion_allowed"])
            self.assertNotIn(source_text, candidate_text)
            self.assertNotIn(source_text, report_text)
            self.assertNotIn("[[", candidate_text)

        hashes_after = {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected_paths
        }
        self.assertEqual(hashes_before, hashes_after)

    def test_audit_confirms_boundary_without_creating_training_rights(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging_root = root / "staging"
            results_root = root / "results"
            candidate_path = staging_root / "runtime-contract.json"
            create_runtime_contract_staging_candidate(
                candidate_path,
                results_root / "creation",
                staging_root=staging_root,
                results_root=results_root,
            )
            report = audit_runtime_contract_staging_candidate(
                candidate_path,
                staging_root=staging_root,
            )
            audit_directory = results_root / "audit"
            write_audit_report(report, audit_directory, results_root=results_root)
            rendered = json.dumps(report, ensure_ascii=False)

            self.assertEqual(report["audit"]["records_checked"], 2)
            self.assertTrue(report["audit"]["all_origin_verdicts_approved"])
            self.assertTrue(report["audit"]["dynamic_runtime_content_excluded"])
            self.assertFalse(report["audit"]["training_allowed"])
            self.assertFalse(report["candidate_is_active"])
            self.assertFalse(report["training_started"])
            self.assertTrue((audit_directory / "brain_v3_runtime_contract_staging_audit.json").is_file())
            self.assertNotIn("[[", rendered)
            self.assertNotIn('"turns"', rendered)

    def test_creation_rejects_output_outside_the_staging_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(PickupContextRuntimeStagingError):
                create_runtime_contract_staging_candidate(
                    root / "outside.json",
                    root / "results" / "creation",
                    staging_root=root / "staging",
                    results_root=root / "results",
                )


if __name__ == "__main__":
    unittest.main()
