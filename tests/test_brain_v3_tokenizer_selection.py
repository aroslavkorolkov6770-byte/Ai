"""Проверяет выбор tokenizer без утечки validation/test в словарь."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ai_core.brain_v3_corpus import content_sha256
from ai_core.brain_v3_tokenizer import load_brain_v3_tokenizer
from evaluation.select_brain_v3_tokenizer import select_tokenizer


PROVENANCE = {
    "source_id": "owned-tokenizer-seed",
    "license": "OWNED_BY_PROJECT",
    "license_url": None,
    "origin_url": None,
    "attribution": "Local tokenizer test author",
}


def _dialogue(identifier: str, split: str, user: str, assistant: str) -> dict[str, object]:
    """Создаёт маленький валидный диалог, который не является реальным корпусом."""
    payload: dict[str, object] = {
        "format_version": 1,
        "id": identifier,
        "record_type": "dialogue",
        "provenance": PROVENANCE,
        "language": "ru",
        "layer": "dialogue",
        "split": split,
        "topic": "tokenizer_test",
        "quality_tier": "human_reviewed",
        "expected_response_properties": ["relevant"],
        "dialogue_goal": "tokenizer_validation",
        "emotional_tone": "neutral",
        "turns": [
            {"role": "user", "text": user},
            {"role": "assistant", "text": assistant},
        ],
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _manifest_with_fixture(root: Path) -> Path:
    """Пишет isolated fixture с разными символами в каждом split."""
    raw_directory = root / "raw"
    raw_directory.mkdir()
    raw_path = raw_directory / "owned-tokenizer-seed.jsonl"
    rows = [
        _dialogue("tokenizer.train.001", "train", "ааа", "ббб"),
        _dialogue("tokenizer.validation.001", "validation", "Жук", "проверка"),
        _dialogue("tokenizer.test.001", "test", "Ёж", "измерение"),
    ]
    raw_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    checksum = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest = {
        "format_version": 1,
        "corpus_name": "tokenizer-test-corpus",
        "language_scope": ["ru"],
        "accepted_licenses": ["OWNED_BY_PROJECT"],
        "quality_gate": {
            "minimum_unique_dialogue_turns": 6,
            "minimum_language_characters": 0,
            "maximum_rejected_record_ratio": 0.0,
            "require_all_splits": True,
        },
        "sources": [
            {
                "source_id": "owned-tokenizer-seed",
                "raw_path": "raw/owned-tokenizer-seed.jsonl",
                "raw_sha256": checksum,
                "source_kind": "owned",
                "license": "OWNED_BY_PROJECT",
                "license_url": None,
                "origin_url": None,
                "attribution": "Local tokenizer test author",
                "approved_for_training": True,
            }
        ],
    }
    manifest_path = root / "corpus_manifest.v1.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path


class BrainV3TokenizerSelectionTests(unittest.TestCase):
    """Проверяет split-дисциплину и отсутствие сырых фраз в отчёте."""

    def test_selection_builds_on_train_only_and_keeps_test_out_of_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = _manifest_with_fixture(root)
            artifact_dir = root / "artifact"
            report_dir = root / "report"

            payload = select_tokenizer(
                manifest_path,
                artifact_dir,
                report_dir,
                bpe_vocabulary_size=512,
                unigram_vocabulary_size=512,
            )

            tokenizer = load_brain_v3_tokenizer(artifact_dir / "tokenizer.json")
            report_markdown = (report_dir / "BRAIN_V3_TOKENIZER_REPORT.md").read_text(
                encoding="utf-8"
            )
            report_json = (report_dir / "brain_v3_tokenizer_selection.json").read_text(
                encoding="utf-8"
            )

        self.assertFalse(payload["training_started"])
        self.assertEqual(payload["selection_scope"]["build_split"], "train")
        self.assertEqual(payload["selection_scope"]["selection_split"], "validation")
        self.assertEqual(payload["selection_scope"]["final_measurement_split"], "test")
        self.assertNotIn("Ж", tokenizer.token_to_id)
        self.assertNotIn("Ё", tokenizer.token_to_id)
        self.assertEqual(tokenizer.decode(tokenizer.encode("Жук и Ёж")), "Жук и Ёж")
        self.assertNotIn("Жук", report_markdown)
        self.assertNotIn("Ёж", report_markdown)
        self.assertNotIn("Жук", report_json)
        self.assertNotIn("Ёж", report_json)


if __name__ == "__main__":
    unittest.main()
