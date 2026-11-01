"""Адресный разбор не превращает замечание или внешний источник в approval."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evaluation import oasst2_targeted_review as targeted
from evaluation import oasst2_quality_review as review


class TargetedReviewTests(unittest.TestCase):
    def test_partition_preserves_all_ids(self):
        report = targeted.build_review()
        self.assertEqual(report["records"], 24)
        self.assertEqual(sorted(report["action_counts"].values()), [2, 5, 8, 9])
        self.assertEqual(len({item["id"] for item in report["items"]}), 24)
        self.assertTrue(all(item["split"] == "train" and not item["training_eligible"] for item in report["items"]))
        self.assertFalse(report["human_ledger_changed"])
        self.assertEqual(report["verified_semantic_labels"], 0)

    def test_source_check_is_not_full_fact_or_rights_clearance(self):
        report = targeted.build_review()
        checked = [item for item in report["items"] if item["source_check"]]
        self.assertEqual(len(checked), 3)
        self.assertTrue(all(not item["source_check"]["whole_dialogue_fact_verified"] for item in checked))
        self.assertFalse(report["all_facts_verified"])
        self.assertFalse(report["all_material_rights_verified"])
        self.assertTrue(all(item["source_check"]["source_key"] in report["sources"] for item in checked))

    def test_unknown_scope_and_overlapping_groups_fail_closed(self):
        with patch.object(targeted, "DO_NOT_USE", targeted.DO_NOT_USE | {"unknown"}):
            with self.assertRaisesRegex(review.ReviewError, "scope"):
                targeted.build_review()
        with patch.object(targeted, "EDITORIAL", targeted.EDITORIAL | targeted.DO_NOT_USE):
            with self.assertRaisesRegex(review.ReviewError, "scope"):
                targeted.build_review()

    def test_immutable_metadata_output(self):
        report = targeted.build_review()
        with tempfile.TemporaryDirectory(dir=targeted.annotation.audit.RESULTS_ROOT) as directory:
            path = Path(directory) / "output"
            targeted.write_review(report, path)
            before = (path / "targeted_review.json").read_bytes()
            with self.assertRaisesRegex(review.ReviewError, "exists"):
                targeted.write_review(report, path)
            self.assertEqual(before, (path / "targeted_review.json").read_bytes())
            self.assertNotIn(b'"turns"', before)

    def test_raw_and_outside_output_rejected(self):
        report = targeted.build_review()
        with tempfile.TemporaryDirectory(dir=targeted.annotation.audit.RESULTS_ROOT) as directory:
            path = Path(directory) / "output"
            with self.assertRaisesRegex(review.ReviewError, "raw_content"):
                targeted.write_review({**report, "text": "forbidden"}, path)
            self.assertFalse(path.exists())
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(review.ReviewError, "outside"):
                targeted.write_review(report, Path(directory) / "output")


if __name__ == "__main__":
    unittest.main()
