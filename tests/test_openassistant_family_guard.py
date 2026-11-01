"""Проверяет сохранение старых семейств без внешних данных или импорта."""

from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_core import openassistant_oasst1 as oasst1
from ai_core.openassistant_family_guard import (
    OpenAssistantFamilyGuardError,
    family_fingerprint,
    load_existing_oasst1_families,
)


class OpenAssistantFamilyGuardTests(unittest.TestCase):
    """Использует маленькие временные gzip fixtures со своей проверкой hashes."""

    def _index(self, rows: list[dict]):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / oasst1.SOURCE_FILE_NAME
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                for row in rows:
                    stream.write(json.dumps(row) + "\n")
            before = path.read_bytes()
            with patch.object(oasst1, "EXPECTED_SOURCE_BYTES", len(before)), patch.object(
                oasst1, "EXPECTED_SOURCE_SHA256", hashlib.sha256(before).hexdigest(),
            ):
                result = load_existing_oasst1_families(path, intake_root=root)
            self.assertEqual(path.read_bytes(), before)
            return result

    def test_old_family_is_recognized_without_using_branch_or_release(self) -> None:
        index = self._index([
            {"message_tree_id": "old-tree", "prompt": {"text": "fixture only"}},
            {"message_tree_id": "previously-filtered-tree", "tree_state": "aborted"},
        ])
        self.assertTrue(index.contains("old-tree"))
        self.assertTrue(index.contains("previously-filtered-tree"))
        self.assertFalse(index.contains("new-tree"))
        metadata = index.metadata()
        self.assertEqual(metadata["families_indexed"], 2)
        self.assertNotIn("old-tree", json.dumps(metadata))
        self.assertNotIn("fixture only", json.dumps(metadata))

    def test_index_is_order_independent_and_ids_are_distinct(self) -> None:
        first = self._index([{"message_tree_id": "alpha"}, {"message_tree_id": "beta"}])
        second = self._index([{"message_tree_id": "beta"}, {"message_tree_id": "alpha"}])
        self.assertEqual(first.family_hashes, second.family_hashes)
        self.assertEqual(first.metadata()["family_index_sha256"], second.metadata()["family_index_sha256"])
        self.assertNotEqual(family_fingerprint("alpha"), family_fingerprint("beta"))

    def test_invalid_or_missing_tree_ids_are_rejected(self) -> None:
        for identifier in (None, [], "", " padded ", "x\n", "x" * 129):
            with self.subTest(identifier=identifier):
                with self.assertRaisesRegex(OpenAssistantFamilyGuardError, "invalid_tree_id"):
                    family_fingerprint(identifier)
        with self.assertRaisesRegex(OpenAssistantFamilyGuardError, "invalid_tree_id"):
            self._index([{}])

    def test_duplicate_or_empty_index_is_rejected(self) -> None:
        with self.assertRaisesRegex(OpenAssistantFamilyGuardError, "duplicate_tree"):
            self._index([{"message_tree_id": "same"}, {"message_tree_id": "same"}])
        with self.assertRaisesRegex(OpenAssistantFamilyGuardError, "empty_index"):
            self._index([])

    def test_unverified_archive_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / oasst1.SOURCE_FILE_NAME
            path.write_bytes(b"not the pinned archive")
            with self.assertRaisesRegex(OpenAssistantFamilyGuardError, "intake_invalid"):
                load_existing_oasst1_families(path, intake_root=root)

    def test_change_during_indexing_is_rejected(self) -> None:
        with patch("ai_core.openassistant_family_guard.file_sha256", side_effect=[
            "a" * 64, "b" * 64,
        ]):
            with self.assertRaisesRegex(OpenAssistantFamilyGuardError, "intake_drift"):
                self._index([{"message_tree_id": "changed-source"}])


if __name__ == "__main__":
    unittest.main()
