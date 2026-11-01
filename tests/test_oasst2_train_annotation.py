"""Разметка агента не может выдаваться за проверенные training-данные."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evaluation import oasst2_train_annotation as annotation
from evaluation import oasst2_quality_review as review


class AnnotationTests(unittest.TestCase):
    def test_exact_scope_and_policy_vocabulary(self):
        report = annotation.build_proposals()
        self.assertEqual(report["records"], 24)
        self.assertEqual(len({item["id"] for item in report["items"]}), 24)
        self.assertTrue(all(item["split"] == "train" for item in report["items"]))
        self.assertTrue(all(item["human_annotation_review"] == "pending" and not item["training_eligible"]
                            for item in report["items"]))
        self.assertEqual(report["verified_semantic_labels"], 0)

    def test_known_bad_behavior_is_flagged_not_overwritten(self):
        report = annotation.build_proposals()
        by_id = {item["id"]: item for item in report["items"]}
        self.assertIn("coercive_advice", by_id["oasst2.ru.1a4b7e6d574666ee59b8c42b56e4"]["concern_codes"])
        self.assertIn("legacy_capability_claim", by_id["oasst2.ru.ce843e2db3153182189854be1677"]["concern_codes"])
        self.assertFalse(report["human_decisions_changed"])

    def test_outside_label_and_scope_rejected(self):
        specs = copy.deepcopy(annotation.SPECS)
        first = next(iter(specs))
        specs[first] = ("unknown_topic", *specs[first][1:])
        with patch.object(annotation, "SPECS", specs):
            with self.assertRaisesRegex(review.ReviewError, "outside_policy"):
                annotation.build_proposals()
        specs.pop(first)
        with patch.object(annotation, "SPECS", specs):
            with self.assertRaisesRegex(review.ReviewError, "scope"):
                annotation.build_proposals()

    def test_outputs_are_immutable_and_metadata_only(self):
        report = annotation.build_proposals()
        with tempfile.TemporaryDirectory(dir=annotation.audit.RESULTS_ROOT) as directory:
            path = Path(directory) / "output"
            annotation.write_proposals(report, path)
            before = (path / "train_annotation_proposals.json").read_bytes()
            with self.assertRaisesRegex(review.ReviewError, "exists"):
                annotation.write_proposals(report, path)
            self.assertEqual(before, (path / "train_annotation_proposals.json").read_bytes())
            self.assertNotIn(b'"turns"', before)
            self.assertNotIn(b'"text"', before)

    def test_raw_and_outside_output_rejected(self):
        report = annotation.build_proposals()
        with tempfile.TemporaryDirectory(dir=annotation.audit.RESULTS_ROOT) as directory:
            path = Path(directory) / "output"
            with self.assertRaisesRegex(review.ReviewError, "raw_content"):
                annotation.write_proposals({**report, "text": "forbidden"}, path)
            self.assertFalse(path.exists())
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(review.ReviewError, "outside"):
                annotation.write_proposals(report, Path(directory) / "output")


if __name__ == "__main__":
    unittest.main()
