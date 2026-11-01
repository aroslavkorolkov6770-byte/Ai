"""Проверяет новый intake на временных fixtures; сеть и real corpus не меняет."""

from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from ai_core import openassistant_oasst2 as source
from ai_core.openassistant_family_guard import ExistingFamilyIndex, family_fingerprint
from evaluation.prepare_oasst2_pilot import PilotOverlapGuard


def tree(identifier: str, texts: list[str] | None = None) -> dict:
    """Создаёт маленькую цельную ветвь с явными source-review флагами."""

    texts = texts or [
        "Как спокойно подготовиться к первому разговору с новым коллегой?",
        "Начни с простой общей темы и дай собеседнику время ответить.",
        "А если я волнуюсь и постоянно перебиваю собеседника?",
        "Попробуй сделать паузу перед ответом и сначала выслушать его мысль.",
    ]
    nodes = []
    for index, text in enumerate(texts):
        nodes.append({
            "message_id": identifier if index == 0 else f"{identifier}-{index}",
            "role": "prompter" if index % 2 == 0 else "assistant",
            "lang": "ru", "text": text, "deleted": False,
            "review_result": True, "synthetic": False, "model_name": None,
            "parent_id": None if index == 0 else nodes[index - 1]["message_id"],
            "replies": [],
        })
        if index:
            nodes[index - 1]["replies"] = [nodes[-1]]
    return {"message_tree_id": identifier, "tree_state": "ready_for_export", "prompt": nodes[0]}


class OASST2SourceTests(unittest.TestCase):
    def _collect(self, rows: list[dict], old: tuple[str, ...] = ()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / source.FILE_NAME
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                for row in rows:
                    stream.write(json.dumps(row) + "\n")
            before = path.read_bytes()
            index = ExistingFamilyIndex("0" * 64, frozenset(family_fingerprint(value) for value in old))
            with patch.object(source, "EXPECTED_BYTES", len(before)), patch.object(
                source, "EXPECTED_SHA256", hashlib.sha256(before).hexdigest(),
            ):
                result = source.collect_new_records(path, index, lambda turns: None, intake_root=root)
            self.assertEqual(before, path.read_bytes())
            return result

    def test_old_families_are_excluded_and_new_candidate_is_unclassified(self) -> None:
        records, report = self._collect([tree("old"), tree("new")], old=("old",))
        self.assertEqual(len(records), 1)
        self.assertEqual(report["counts"]["existing_oasst1_family"], 1)
        record = records[0]
        self.assertEqual(len(record["turns"]), 4)
        self.assertEqual(record["quality_tier"], "license_verified")
        self.assertEqual(record["topic"], "unclassified")
        self.assertEqual(record["expected_response_properties"], ["relevant"])
        self.assertNotIn("user_id", json.dumps(record))
        self.assertEqual(report["family_items"][0]["family_sha256"], family_fingerprint("new"))

    def test_unsafe_parent_does_not_allow_safe_suffix_to_be_stitched(self) -> None:
        payload = tree("unsafe")
        payload["prompt"]["synthetic"] = True
        counts = Counter()
        self.assertEqual(list(source.safe_paths(payload, counts)), [])
        self.assertEqual(counts["synthetic"], 1)

    def test_model_origin_and_unknown_flags_and_broken_parent_are_rejected(self) -> None:
        for field, value, code in (
            ("model_name", "foreign-model", "model_origin"),
            ("review_result", None, "unreviewed"),
            ("synthetic", None, "synthetic"),
            ("parent_id", "wrong-parent", "broken_parent_link"),
        ):
            with self.subTest(field=field):
                payload = tree("flags")
                payload["prompt"][field] = value
                counts = Counter()
                self.assertEqual(list(source.safe_paths(payload, counts)), [])
                self.assertEqual(counts[code], 1)

    def test_two_turn_paths_are_counted_but_not_written_for_followup_pilot(self) -> None:
        records, report = self._collect([tree("short", ["Добрый день.", "Добрый день!"])])
        self.assertEqual(records, [])
        self.assertEqual(report["counts"]["two_turn_only_path"], 1)

    def test_duplicate_tree_and_decompression_limit_fail_closed(self) -> None:
        with self.assertRaisesRegex(source.OASST2PilotError, "duplicate_tree"):
            self._collect([tree("same"), tree("same")])
        with patch.object(source, "MAX_LINE_BYTES", 100):
            with self.assertRaisesRegex(source.OASST2PilotError, "decompression_limit"):
                self._collect([tree("bounded")])

    def test_branch_selection_keeps_one_record_and_family_split_is_stable(self) -> None:
        payload = tree("branches")
        payload["prompt"]["replies"].append({
            "message_id": "short-answer", "parent_id": "branches", "role": "assistant",
            "lang": "ru", "text": "Начни с приветствия.", "deleted": False,
            "review_result": True, "synthetic": False, "replies": [],
        })
        records, _ = self._collect([payload])
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["turns"]), 4)
        paths = list(source.safe_paths(payload, Counter()))
        self.assertEqual(source.make_record("branches", paths[0])["split"],
                         source.make_record("branches", paths[1])["split"])

    def test_redirects_are_limited_to_fixed_https_hosts(self) -> None:
        self.assertTrue(source.allowed_url("https://us.aws.cdn.hf.co/path?temporary=value"))
        for url in ("http://huggingface.co/path", "https://huggingface.co.evil.test/path",
                    "https://user:secret@huggingface.co/path", "https://huggingface.co:bad/path"):
            self.assertFalse(source.allowed_url(url))


class OASST2OverlapTests(unittest.TestCase):
    def test_active_full_dialogue_or_shared_prefix_is_excluded(self) -> None:
        guard = PilotOverlapGuard()
        paths = list(source.safe_paths(tree("example"), Counter()))
        turns = [{"role": value.role, "text": value.text} for value in paths[0]]
        guard.add_dialogue(turns)
        self.assertIn("overlap", guard.rejection(turns))
        extended = [*turns, {"role": "user", "text": "А дальше?"},
                    {"role": "assistant", "text": "Продолжи разговор."}]
        self.assertIn("overlap", guard.rejection(extended))

    def test_closed_prompt_is_blocked_without_blocking_common_greeting(self) -> None:
        guard = PilotOverlapGuard()
        prompt = "Как спокойно подготовиться к первому разговору с новым коллегой?"
        guard.add_contract_prompt(prompt)
        guard.add_contract_prompt("Привет")
        self.assertEqual(guard.rejection([
            {"role": "user", "text": prompt}, {"role": "assistant", "text": "Начни спокойно."},
        ]), "closed_contract_exact_prompt")
        self.assertIsNone(guard.rejection([
            {"role": "user", "text": "Привет"}, {"role": "assistant", "text": "Привет!"},
        ]))


if __name__ == "__main__":
    unittest.main()
