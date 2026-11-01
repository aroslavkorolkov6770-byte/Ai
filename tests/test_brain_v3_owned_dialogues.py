"""Проверяет project-owned target candidate до его записи в staging Brain v3."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from ai_core.brain_v3_corpus import content_sha256
from ai_core.brain_v3_owned_dialogues import (
    DEFAULT_BLUEPRINT_PATH,
    EXPECTED_DIALOGUE_RECORDS,
    EXPECTED_DIALOGUE_TURNS,
    EXPECTED_STRUCTURE,
    MAX_ASSISTANT_CONTEXT_ECHO_RATIO,
    MAX_ASSISTANT_OVERLONG_RATIO,
    MAX_SINGLE_ASSISTANT_REPEAT_RATIO,
    MIN_ASSISTANT_UNIQUE_RATIO,
    OwnedDialogueError,
    PROHIBITED_AUTHORING_ARTIFACTS,
    SOURCE_ID,
    assess_owned_dialogue_records,
    build_owned_dialogue_records,
    create_owned_dialogue_candidate,
    creation_summary_markdown,
    source_descriptor,
)


ROOT = Path(__file__).resolve().parent.parent


def _blueprint() -> dict[str, object]:
    """Читает только versioned metadata blueprint, а не raw corpus."""
    return json.loads(DEFAULT_BLUEPRINT_PATH.read_text(encoding="utf-8"))


class BrainV3OwnedDialogueTests(unittest.TestCase):
    """Проверяет quotas, повторяемость и локальную staging boundary кандидата."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.blueprint = _blueprint()
        cls.records = build_owned_dialogue_records(cls.blueprint)
        cls.summary = assess_owned_dialogue_records(cls.records, cls.blueprint)

    def test_current_blueprint_becomes_complete_owned_candidate_in_memory(self) -> None:
        self.assertEqual(len(self.records), EXPECTED_DIALOGUE_RECORDS)
        self.assertEqual(self.summary["dialogue_turns"], EXPECTED_DIALOGUE_TURNS)
        self.assertEqual(self.summary["turn_structure"], EXPECTED_STRUCTURE)
        self.assertEqual(self.summary["unique_first_user_turns"], EXPECTED_DIALOGUE_RECORDS)
        self.assertGreaterEqual(
            self.summary["assistant_unique_ratio"],
            MIN_ASSISTANT_UNIQUE_RATIO,
        )
        self.assertLessEqual(
            self.summary["max_assistant_repeat_ratio"],
            MAX_SINGLE_ASSISTANT_REPEAT_RATIO,
        )
        self.assertLessEqual(
            self.summary["assistant_context_echo_ratio"],
            MAX_ASSISTANT_CONTEXT_ECHO_RATIO,
        )
        self.assertLessEqual(
            self.summary["assistant_overlong_ratio"],
            MAX_ASSISTANT_OVERLONG_RATIO,
        )
        self.assertFalse(self.summary["automatically_human_reviewed"])
        self.assertFalse(self.summary["candidate_is_active"])
        self.assertFalse(self.summary["training_started"])

    def test_records_have_only_owned_provenance_and_end_with_assistant(self) -> None:
        descriptor = source_descriptor()
        expected_provenance = {
            "source_id": descriptor.source_id,
            "license": descriptor.license_name,
            "license_url": descriptor.license_url,
            "origin_url": descriptor.origin_url,
            "attribution": descriptor.attribution,
        }
        self.assertEqual(descriptor.source_kind, "owned")
        self.assertEqual(descriptor.source_id, SOURCE_ID)
        self.assertTrue(
            all(record["provenance"] == expected_provenance for record in self.records)
        )
        self.assertTrue(
            all(record["turns"][-1]["role"] == "assistant" for record in self.records)
        )

    def test_summary_never_contains_candidate_text(self) -> None:
        first_user = self.records[0]["turns"][0]["text"]
        first_assistant = self.records[0]["turns"][1]["text"]
        rendered = json.dumps(self.summary, ensure_ascii=False) + creation_summary_markdown(
            {
                "candidate_path": "C:/safe/staging/candidate.jsonl",
                "candidate_sha256": "0" * 64,
                "candidate_bytes": 1,
                "quality": self.summary,
            }
        )
        self.assertNotIn(first_user, rendered)
        self.assertNotIn(first_assistant, rendered)

    def test_records_exclude_known_robotic_authoring_artifacts(self) -> None:
        all_turns = "\n".join(
            turn["text"].casefold()
            for record in self.records
            for turn in record["turns"]
        )
        for artifact in PROHIBITED_AUTHORING_ARTIFACTS:
            self.assertNotIn(artifact, all_turns)
        self.assertIsNone(re.search(r"[а-яё][a-z]|[a-z][а-яё]", all_turns))

    def test_assessment_rejects_known_robotic_authoring_artifact(self) -> None:
        records = deepcopy(self.records)
        records[0]["turns"][0]["text"] += " Это особенно уместно в черновом примере."
        records[0]["content_sha256"] = content_sha256(records[0])
        with self.assertRaises(OwnedDialogueError):
            assess_owned_dialogue_records(records, self.blueprint)

    def test_assessment_rejects_mixed_script_typo(self) -> None:
        records = deepcopy(self.records)
        records[0]["turns"][0]["text"] += " Идеe требует исправления."
        records[0]["content_sha256"] = content_sha256(records[0])
        with self.assertRaises(OwnedDialogueError):
            assess_owned_dialogue_records(records, self.blueprint)

    def test_assessment_rejects_invalid_infinitive_case(self) -> None:
        for fragment in (
            " Начни с дать нейтральный ответ.",
            " Важно сохранить отделить старую версию от новой.",
        ):
            with self.subTest(fragment=fragment):
                records = deepcopy(self.records)
                records[0]["turns"][0]["text"] += fragment
                records[0]["content_sha256"] = content_sha256(records[0])
                with self.assertRaises(OwnedDialogueError):
                    assess_owned_dialogue_records(records, self.blueprint)

    def test_creation_writes_only_new_staging_candidate_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results_root = root / "results"
            blueprint_directory = results_root / "blueprint"
            blueprint_directory.mkdir(parents=True)
            blueprint_path = blueprint_directory / "dialogue_authoring_blueprint.json"
            blueprint_path.write_text(
                json.dumps(self.blueprint, ensure_ascii=False),
                encoding="utf-8",
            )
            staging_root = root / "staging"
            staging_root.mkdir()
            candidate_path = staging_root / "owned-candidate.jsonl"
            report_directory = results_root / "owned-report"

            summary = create_owned_dialogue_candidate(
                blueprint_path,
                candidate_path,
                report_directory,
                staging_root=staging_root,
                results_root=results_root,
            )

            self.assertTrue(candidate_path.is_file())
            self.assertTrue((report_directory / "owned_dialogue_creation.json").is_file())
            self.assertEqual(summary["source_id"], SOURCE_ID)
            self.assertFalse(summary["candidate_is_active"])
            self.assertFalse(summary["active_manifest_updated"])
            self.assertFalse(summary["training_started"])


if __name__ == "__main__":
    unittest.main()
