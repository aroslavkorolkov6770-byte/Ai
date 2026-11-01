"""Проверяет cross-candidate coverage audit без скрытого training пути."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.brain_v3_cross_candidate_coverage_audit import (
    CrossCandidateCoverageAuditError,
    audit_input_paths,
    build_cross_candidate_coverage_audit,
    write_audit_report,
)


class CrossCandidateCoverageAuditTests(unittest.TestCase):
    """Не даёт aggregate metadata превратиться в corpus или скрыть blocker."""

    @classmethod
    def setUpClass(cls) -> None:
        """Один проход по review metadata достаточен для проверок отчёта."""

        cls.report = build_cross_candidate_coverage_audit()

    def test_report_is_metadata_only_and_keeps_training_blocked(self) -> None:
        candidates = self.report["candidate_audit"]
        static_coverage = self.report["approved_review_only_static_coverage"]
        boundaries = self.report["boundaries"]
        rendered = json.dumps(self.report, ensure_ascii=False)

        self.assertEqual(candidates["candidates_checked"], 7)
        self.assertEqual(static_coverage["records"], 15)
        self.assertEqual(static_coverage["approved_turn_count"], 60)
        self.assertEqual(candidates["approved_static_records_excluded"], 17)
        self.assertEqual(candidates["approved_incomplete_review_records_excluded"], 16)
        self.assertEqual(candidates["approved_medical_records_held"], 1)
        self.assertEqual(
            static_coverage["turn_counts_by_semantic_category"]["topic_turns"]["safety_boundaries"],
            4,
        )
        core_dialogue = next(
            item
            for item in candidates["candidate_states"]
            if item["scope_id"] == "brain-v3-core-dialogue-candidate-v1.6"
        )
        self.assertEqual(core_dialogue["approved_records"], 6)
        self.assertEqual(core_dialogue["approved_static_records"], 5)
        self.assertEqual(core_dialogue["approved_medical_records_held"], 1)
        self.assertEqual(core_dialogue["approved_static_records_excluded"], 1)
        self.assertTrue(candidates["legacy_candidate_excluded_from_provisional_coverage"])
        legacy = candidates["candidate_states"][-1]
        self.assertEqual(
            legacy["excluded_from_provisional_coverage"],
            "legacy_mixed_outcomes_and_unreviewed_records",
        )
        self.assertEqual(candidates["state_inconsistencies"], [])
        self.assertEqual(candidates["legacy_approved_records_excluded"], 12)
        self.assertTrue(
            self.report["base_corpus"]["baseline_verification"]["manifest_matches_verified_reference"]
        )
        self.assertTrue(
            self.report["base_corpus"]["baseline_verification"]["policy_requirements_match_baseline"]
        )
        self.assertFalse(
            self.report["base_corpus"]["baseline_verification"][
                "full_raw_corpus_reaudit_performed"
            ]
        )
        self.assertFalse(boundaries["active_manifest_updated"])
        self.assertFalse(boundaries["staging_created"])
        self.assertFalse(boundaries["training_started"])
        self.assertFalse(boundaries["training_candidate_allowed"])
        self.assertFalse(boundaries["promotion_allowed"])
        priority_topics = {
            item["topic"] for item in self.report["recommended_next_static_topics"]
        }
        self.assertNotIn("tool_routing", priority_topics)
        self.assertNotIn("web_grounding", priority_topics)
        self.assertTrue(
            all(item["remaining_turn_shortfall"] > 0 for item in self.report["recommended_next_static_topics"])
        )
        self.assertNotIn('"turns"', rendered)
        self.assertNotIn('"text"', rendered)

    def test_writing_report_preserves_all_inputs_and_excludes_source_text(self) -> None:
        protected = audit_input_paths()
        hashes_before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_directory = root / "results" / "cross-candidate-v0.1"
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
                (report_directory / "brain_v3_cross_candidate_coverage_audit.json").is_file()
            )
            self.assertTrue(
                (report_directory / "BRAIN_V3_CROSS_CANDIDATE_COVERAGE_AUDIT.md").is_file()
            )
            self.assertNotIn('"turns"', rendered)
            self.assertNotIn('"text"', rendered)

        hashes_after = {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected
        }
        self.assertEqual(hashes_before, hashes_after)

    def test_report_output_outside_results_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(CrossCandidateCoverageAuditError):
                write_audit_report(
                    self.report,
                    root / "outside" / "cross-candidate-v0.1",
                    results_root=root / "results",
                )


if __name__ == "__main__":
    unittest.main()
