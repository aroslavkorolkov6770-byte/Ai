"""Проверяет итог очереди и отказ при изменённых metadata; ledger только временный."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation.oasst1_material_review import MaterialReviewPaths, load_review_snapshot
from evaluation.oasst1_material_review_audit import (
    OASST1MaterialReviewAuditError,
    build_material_review_audit,
    write_audit,
)


class OASST1MaterialReviewAuditTests(unittest.TestCase):
    """Имитирует решения в fixtures, не записывает решения за пользователя."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = MaterialReviewPaths(
            decisions_path=self.root / "reviews" / "decisions.jsonl",
            reviews_root=self.root / "reviews",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _decisions_fixture(self) -> list[dict]:
        """Создаёт тестовые наблюдения по существующим metadata, без текста."""

        report, _ = load_review_snapshot(self.paths)
        triage = report["triage"]
        by_id = {item["id"]: item for item in triage["all_items"]}
        decisions = []
        for index, identifier in enumerate(triage["suggested_review_sample_ids"]):
            item = by_id[identifier]
            verdict = (
                "needs_source_check" if index == 0 else
                "exclude_candidate" if index == 1 else "no_obvious_excerpt"
            )
            decisions.append({
                "format_version": 1,
                "mode": "oasst1_approved_train_material_human_review_decision",
                "candidate_id": identifier,
                "content_sha256": item["content_sha256"],
                "split": "train",
                "triage_group": item["triage_group"],
                "verdict": verdict,
                "reason_code": "none_obvious" if verdict == "no_obvious_excerpt" else "quoted_span",
                "reviewer_role": "local_human_reviewer",
                "reviewed_at": "2026-10-02T00:00:00Z",
            })
        return decisions

    def _write_fixture(self, rows: list[dict]) -> None:
        self.paths.reviews_root.mkdir(exist_ok=True)
        self.paths.decisions_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
        )

    def test_missing_ledger_is_pending_and_complete_mode_rejects_it(self) -> None:
        report = build_material_review_audit(self.paths)
        review = report["review"]
        self.assertEqual(review["state"], "incomplete")
        self.assertEqual(review["reviewed_records"], 0)
        self.assertEqual(review["pending_records"], 24)
        self.assertEqual(review["records_outside_sample"], 51)
        self.assertIsNone(report["inputs"]["material_decisions_sha256"])
        self.assertFalse(self.paths.decisions_path.exists())
        with self.assertRaisesRegex(OASST1MaterialReviewAuditError, "incomplete"):
            build_material_review_audit(self.paths, require_complete=True)

    def test_partial_and_complete_observations_keep_findings_and_boundaries(self) -> None:
        fixture = self._decisions_fixture()
        self._write_fixture(fixture[:3])
        partial = build_material_review_audit(self.paths)
        self.assertEqual(partial["review"]["pending_records"], 21)
        self.assertEqual(len(partial["review"]["needs_source_check_ids"]), 1)
        self.assertEqual(len(partial["review"]["exclude_candidate_ids"]), 1)
        self._write_fixture(fixture)
        complete = build_material_review_audit(self.paths, require_complete=True)
        self.assertEqual(complete["review"]["state"], "sample_complete_with_findings")
        self.assertEqual(complete["review"]["verdict_counts"], {
            "exclude_candidate": 1, "needs_source_check": 1, "no_obvious_excerpt": 22,
        })
        self.assertTrue(complete["review"]["sample_complete"])
        self.assertFalse(complete["boundaries"]["training_candidate_allowed"])
        self.assertFalse(complete["boundaries"]["record_level_rights_confirmed"])
        self.assertFalse(complete["boundaries"]["unsampled_records_automatically_accepted"])
        self.assertNotIn('"turns"', json.dumps(complete))

    def test_tampered_content_hash_and_duplicate_are_rejected(self) -> None:
        fixture = self._decisions_fixture()
        damaged = copy.deepcopy(fixture[:1])
        damaged[0]["content_sha256"] = "0" * 64
        self._write_fixture(damaged)
        with self.assertRaisesRegex(OASST1MaterialReviewAuditError, "review_invalid"):
            build_material_review_audit(self.paths)
        self._write_fixture([fixture[0], fixture[0]])
        with self.assertRaisesRegex(OASST1MaterialReviewAuditError, "review_invalid"):
            build_material_review_audit(self.paths)

    def test_change_during_read_is_rejected(self) -> None:
        from evaluation import oasst1_material_review_audit as audit

        stable = audit._input_hashes(self.paths)
        changed = {**stable, "material_triage_sha256": "0" * 64}
        with patch.object(audit, "_input_hashes", side_effect=[stable, changed]):
            with self.assertRaisesRegex(OASST1MaterialReviewAuditError, "input_drift"):
                build_material_review_audit(self.paths)

    def test_snapshot_is_immutable_and_rejects_raw_content(self) -> None:
        report = build_material_review_audit(self.paths)
        output_root = self.root / "results"
        destination = output_root / "pending"
        write_audit(report, destination, results_root=output_root)
        self.assertTrue((destination / "OASST1_MATERIAL_REVIEW_AUDIT.md").is_file())
        with self.assertRaisesRegex(OASST1MaterialReviewAuditError, "report_exists"):
            write_audit(report, destination, results_root=output_root)
        with self.assertRaisesRegex(OASST1MaterialReviewAuditError, "outside_results"):
            write_audit(report, self.root / "outside", results_root=output_root)
        with self.assertRaisesRegex(OASST1MaterialReviewAuditError, "not_metadata_only"):
            write_audit({**report, "turns": []}, output_root / "raw", results_root=output_root)


if __name__ == "__main__":
    unittest.main()
