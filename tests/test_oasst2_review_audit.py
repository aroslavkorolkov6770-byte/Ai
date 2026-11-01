"""Completed-аудит не одобряет остаток и не изменяет человеческие решения."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from evaluation import oasst2_quality_review as review
from evaluation import oasst2_review_audit as audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.paths = replace(review.ReviewPaths(), queue=root / "queue.json", ledger=root / "ledger.json")
        self.queue = review.prepare(self.paths)
        _, self.records = review.context(self.paths)
        self.rows = [{"candidate_id": item["id"], "verdict": "acceptable", "reason_code": "none_obvious"}
                     for item in self.queue["items"]]

    def tearDown(self):
        self.temporary.cleanup()

    def test_complete_partition_and_pending(self):
        report = audit.summarise(self.queue, self.records, self.rows)
        self.assertEqual(report["unreviewed_records"], 278)
        self.assertEqual(len(report["quality_observed_train_ids"]), 24)
        self.assertFalse(report["training_candidate_allowed"])
        self.assertFalse(report["unreviewed_records_automatically_approved"])
        self.assertEqual(sum(sum(counts.values()) for counts in report["split_quality_counts"].values()), 314)

    def test_incomplete_and_duplicate_rejected(self):
        for rows in (self.rows[:-1], self.rows[:-1] + [self.rows[0]]):
            with self.assertRaisesRegex(review.ReviewError, "completed"):
                audit.summarise(self.queue, self.records, rows)

    def test_exclusion_is_family_bound_and_not_reverted(self):
        self.rows[0] = {**self.rows[0], "verdict": "exclude", "reason_code": "third_party_material"}
        report = audit.summarise(self.queue, self.records, self.rows)
        item = self.queue["items"][0]
        self.assertEqual(report["excluded_ids"], [item["id"]])
        self.assertEqual(report["excluded_family_hashes"], [item["family_sha256"]])
        self.assertNotIn(item["id"], report["quality_observed_train_ids"])
        self.assertEqual(sum(report["material_triage"]["group_counts"].values()), 313)

    def test_hold_does_not_become_approval(self):
        self.rows[0] = {**self.rows[0], "verdict": "needs_check", "reason_code": "factual_uncertainty"}
        report = audit.summarise(self.queue, self.records, self.rows)
        identifier = self.rows[0]["candidate_id"]
        self.assertEqual(report["held_ids"], [identifier])
        self.assertNotIn(identifier, report["acceptable_observation_ids"])

    def test_metadata_has_no_dialogue(self):
        report = audit.summarise(self.queue, self.records, self.rows)
        encoded = json.dumps(report)
        for key in ('"text"', '"turns"', '"conversation"'):
            self.assertNotIn(key, encoded)
        self.assertTrue(all(item["training_eligible"] is False for item in report["material_triage"]["all_items"]))

    def test_output_outside_results_refused(self):
        report = audit.summarise(self.queue, self.records, self.rows)
        with self.assertRaisesRegex(review.ReviewError, "outside"):
            audit.write_audit(report, Path(self.temporary.name) / "output")

    def test_saved_report_is_immutable(self):
        report = audit.summarise(self.queue, self.records, self.rows)
        with tempfile.TemporaryDirectory(dir=audit.RESULTS_ROOT) as directory:
            destination = Path(directory) / "report"
            audit.write_audit(report, destination)
            path = destination / "oasst2_quality_audit.json"
            before = path.read_bytes()
            with self.assertRaisesRegex(review.ReviewError, "exists"):
                audit.write_audit(report, destination)
            self.assertEqual(path.read_bytes(), before)

    def test_raw_dialogue_cannot_be_written(self):
        report = {**audit.summarise(self.queue, self.records, self.rows), "text": "forbidden"}
        with tempfile.TemporaryDirectory(dir=audit.RESULTS_ROOT) as directory:
            destination = Path(directory) / "report"
            with self.assertRaisesRegex(review.ReviewError, "raw_content"):
                audit.write_audit(report, destination)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
