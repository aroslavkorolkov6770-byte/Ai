"""Проверяет staging/audit snapshot из одобренных Brain v3 review-сцен."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ai_core.brain_v3_corpus import content_sha256
from evaluation.brain_v3_reviewed_staging import (
    DEFAULT_CANDIDATE_PATH,
    EXPECTED_RECORDS,
    EXPECTED_TURNS,
    MANUAL_REJECTED_IDS,
    ReviewedStagingError,
    audit_reviewed_staging_candidate,
    build_staged_records,
    create_reviewed_staging_candidate,
    creation_summary_markdown,
    source_descriptor,
)
from evaluation.brain_v3_staging import audit_staged_candidate


class BrainV3ReviewedStagingTests(unittest.TestCase):
    """Не даёт одобренному review material тихо стать active/training corpus."""

    def setUp(self) -> None:
        self.records, self.metadata = build_staged_records()

    def test_fixed_selection_has_expected_splits_and_review_proofs(self) -> None:
        coverage = self.metadata["coverage"]
        selected_ids = {
            item["origin_candidate_id"] for item in self.metadata["selection"]
        }

        self.assertEqual(len(self.records), EXPECTED_RECORDS)
        self.assertEqual(coverage["dialogue_turns"], EXPECTED_TURNS)
        self.assertEqual(coverage["records_by_split"], {"test": 6, "train": 6, "validation": 4})
        self.assertTrue(selected_ids.isdisjoint(MANUAL_REJECTED_IDS))
        self.assertEqual(
            {proof["source_key"] for proof in self.metadata["review_proofs"]},
            {"manual", "repair"},
        )
        self.assertTrue(
            all(proof["required_coverage_complete"] for proof in self.metadata["review_proofs"])
        )

    def test_staged_records_are_canonical_human_reviewed_dialogues(self) -> None:
        descriptor = source_descriptor()
        expected_provenance = {
            "source_id": descriptor.source_id,
            "license": descriptor.license_name,
            "license_url": descriptor.license_url,
            "origin_url": descriptor.origin_url,
            "attribution": descriptor.attribution,
        }

        self.assertTrue(all(record["record_type"] == "dialogue" for record in self.records))
        self.assertTrue(all(record["quality_tier"] == "human_reviewed" for record in self.records))
        self.assertTrue(all(record["provenance"] == expected_provenance for record in self.records))
        self.assertTrue(all(record["content_sha256"] == content_sha256(record) for record in self.records))
        self.assertTrue(all(record["turns"][-1]["role"] == "assistant" for record in self.records))

    def test_creation_writes_candidate_and_metadata_without_changing_inputs(self) -> None:
        input_paths = [
            Path(proof["candidate_path"])
            for proof in self.metadata["review_proofs"]
        ]
        hashes_before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in input_paths}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging_root = root / "staging"
            results_root = root / "results"
            candidate = staging_root / "reviewed.jsonl"
            report_directory = results_root / "reviewed-report"
            summary = create_reviewed_staging_candidate(
                candidate,
                report_directory,
                staging_root=staging_root,
                results_root=results_root,
            )
            written_report = "".join(
                path.read_text(encoding="utf-8")
                for path in sorted(report_directory.iterdir())
            )

            self.assertTrue(candidate.is_file())
            self.assertTrue((report_directory / "brain_v3_reviewed_staging_candidate.json").is_file())
            self.assertFalse(summary["candidate_is_active"])
            self.assertFalse(summary["training_started"])
            self.assertNotIn(self.records[0]["turns"][0]["text"], written_report)
            self.assertNotIn(self.records[0]["turns"][1]["text"], written_report)
        self.assertEqual(
            {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in input_paths},
            hashes_before,
        )

    def test_isolated_audit_accepts_records_but_blocks_training_for_small_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging_root = root / "staging"
            results_root = root / "results"
            candidate = staging_root / "reviewed.jsonl"
            create_reviewed_staging_candidate(
                candidate,
                results_root / "reviewed-report",
                staging_root=staging_root,
                results_root=results_root,
            )
            report = audit_staged_candidate(
                candidate,
                source_descriptor(),
                staging_root=staging_root,
            )

        self.assertEqual(report["audit"]["records_accepted"], EXPECTED_RECORDS)
        self.assertEqual(report["audit"]["records_rejected"], 0)
        self.assertFalse(report["audit"]["training_allowed"])
        self.assertIn("insufficient_unique_dialogue_turns", report["audit"]["training_block_reasons"])
        self.assertFalse(report["candidate_is_active"])
        self.assertFalse(report["training_started"])

    def test_output_outside_staging_is_rejected_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ReviewedStagingError):
                create_reviewed_staging_candidate(
                    root / "outside.jsonl",
                    root / "results" / "report",
                    staging_root=root / "staging",
                    results_root=root / "results",
                )

    def test_creation_summary_contains_no_raw_dialogue_text(self) -> None:
        summary = {
            "coverage": self.metadata["coverage"],
        }
        rendered = json.dumps(self.metadata, ensure_ascii=False) + creation_summary_markdown(summary)

        self.assertNotIn(self.records[0]["turns"][0]["text"], rendered)
        self.assertNotIn(self.records[0]["turns"][1]["text"], rendered)


if __name__ == "__main__":
    unittest.main()
