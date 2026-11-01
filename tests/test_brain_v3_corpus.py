"""Проверяет безопасный шлюз данных будущего генеративного корпуса Brain v3."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ai_core.brain_v3_corpus import (
    DEFAULT_MANIFEST,
    BrainV3CorpusError,
    audit_corpus,
    content_sha256,
)


PROVENANCE = {
    "source_id": "owned-seed",
    "license": "OWNED_BY_PROJECT",
    "license_url": None,
    "origin_url": None,
    "attribution": "Local test author",
}


def _record(identifier: str, split: str, user: str, assistant: str) -> dict[str, object]:
    """Строит валидную test-only запись, не являющуюся реальным train-корпусом."""
    payload: dict[str, object] = {
        "format_version": 1,
        "id": identifier,
        "record_type": "dialogue",
        "provenance": PROVENANCE,
        "language": "ru",
        "layer": "dialogue",
        "split": split,
        "topic": "everyday_dialogue",
        "quality_tier": "human_reviewed",
        "expected_response_properties": ["relevant", "natural_tone", "no_repeat"],
        "dialogue_goal": "friendly_discussion",
        "emotional_tone": "neutral",
        "turns": [
            {"role": "user", "text": user},
            {"role": "assistant", "text": assistant},
        ],
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _write_manifest(root: Path, rows: list[dict[str, object]]) -> Path:
    """Создаёт источник и соответствующий ему манифест с маленькими test gate."""
    raw_directory = root / "raw"
    raw_directory.mkdir()
    raw_path = raw_directory / "owned-seed.jsonl"
    raw_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    checksum = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest = {
        "format_version": 1,
        "corpus_name": "test-brain-v3-corpus",
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
                "source_id": "owned-seed",
                "raw_path": "raw/owned-seed.jsonl",
                "raw_sha256": checksum,
                "source_kind": "owned",
                "license": "OWNED_BY_PROJECT",
                "license_url": None,
                "origin_url": None,
                "attribution": "Local test author",
                "approved_for_training": True,
            }
        ],
    }
    manifest_path = root / "corpus_manifest.v1.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path


class BrainV3CorpusTests(unittest.TestCase):
    """Регрессии без внешнего корпуса, сети или настоящих пользовательских чатов."""

    def _clean_rows(self) -> list[dict[str, object]]:
        return [
            _record(
                "dialogue.train.001",
                "train",
                "Мне сложно решить, чем заняться вечером.",
                "Давай выберем по настроению и времени, которое у тебя есть.",
            ),
            _record(
                "dialogue.validation.001",
                "validation",
                "Почему листья осенью меняют цвет?",
                "Становится меньше света, и зелёный пигмент постепенно разрушается.",
            ),
            _record(
                "dialogue.test.001",
                "test",
                "Я уточняю: план нужен на субботу, не на пятницу.",
                "Понял, будем строить план именно на субботу.",
            ),
        ]

    def test_clean_small_fixture_can_pass_its_explicit_test_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = _write_manifest(Path(directory), self._clean_rows())
            report = audit_corpus(manifest)

        self.assertTrue(report["training_allowed"])
        self.assertEqual(report["records_accepted"], 3)
        self.assertEqual(report["records_rejected"], 0)
        self.assertEqual(report["unique_dialogue_turns"], 6)

    def test_url_like_text_is_rejected_and_blocks_training(self) -> None:
        rows = self._clean_rows()
        unsafe = _record(
            "dialogue.train.unsafe",
            "train",
            "Посмотри адрес https://example.test и скажи мнение.",
            "Я не буду использовать URL из будущего корпуса.",
        )
        rows.append(unsafe)
        with tempfile.TemporaryDirectory() as directory:
            manifest = _write_manifest(Path(directory), rows)
            report = audit_corpus(manifest)

        self.assertFalse(report["training_allowed"])
        self.assertEqual(report["rejections"]["url_like"], 1)
        self.assertIn("rejected_record_ratio_exceeded", report["training_block_reasons"])

    def test_close_cross_split_variant_is_rejected_without_printing_text(self) -> None:
        rows = self._clean_rows()
        rows[1] = _record(
            "dialogue.validation.near",
            "validation",
            "Мне сложно решить чем заняться вечером.",
            "Давай выберем по настроению и времени, которое у тебя есть.",
        )
        with tempfile.TemporaryDirectory() as directory:
            manifest = _write_manifest(Path(directory), rows)
            report = audit_corpus(manifest)

        self.assertEqual(report["rejections"]["near_duplicate_candidate"], 1)
        self.assertEqual(report["cross_split_near_duplicate_candidates"], 1)
        self.assertNotIn("Мне сложно решить", json.dumps(report, ensure_ascii=False))

    def test_tampered_content_hash_is_rejected(self) -> None:
        rows = self._clean_rows()
        rows[0]["topic"] = "tampered_topic"
        with tempfile.TemporaryDirectory() as directory:
            manifest = _write_manifest(Path(directory), rows)
            report = audit_corpus(manifest)

        self.assertEqual(report["rejections"]["content_hash_mismatch"], 1)
        self.assertFalse(report["training_allowed"])

    def test_tool_result_properties_require_tool_layer_and_result_pair(self) -> None:
        """Не позволяет обычному диалогу имитировать свежий результат route."""
        rows = self._clean_rows()
        wrong_layer = _record(
            "dialogue.train.tool-props-wrong-layer",
            "train",
            "Какая сейчас погода?",
            "Покажу результат после проверки.",
        )
        wrong_layer["expected_response_properties"] = [
            "relevant",
            "tool_result_returned",
            "no_action_promise",
        ]
        wrong_layer["content_sha256"] = content_sha256(wrong_layer)
        missing_result = _record(
            "dialogue.validation.tool-props-missing-result",
            "validation",
            "Скажи текущий результат.",
            "Проверю это позже.",
        )
        missing_result["layer"] = "tool"
        missing_result["expected_response_properties"] = [
            "relevant",
            "no_action_promise",
        ]
        missing_result["content_sha256"] = content_sha256(missing_result)
        rows.extend([wrong_layer, missing_result])

        with tempfile.TemporaryDirectory() as directory:
            manifest = _write_manifest(Path(directory), rows)
            report = audit_corpus(manifest)

        self.assertEqual(
            report["rejections"]["tool_result_properties_require_tool_layer"],
            1,
        )
        self.assertEqual(
            report["rejections"]["no_action_promise_requires_tool_result"],
            1,
        )
        self.assertFalse(report["training_allowed"])

    def test_default_manifest_with_six_clean_language_and_dialogue_sources_passes_base_data_gate(self) -> None:
        report = audit_corpus(DEFAULT_MANIFEST)

        self.assertTrue(report["training_allowed"])
        self.assertEqual(report["sources_declared"], 6)
        self.assertEqual(report["records_accepted"], 188_125)
        self.assertEqual(
            report["accepted_by_source"],
            {
                "dream-ru-open-cc-by-v1": 54_984,
                "mot-rus-golosameriki-cc-by-v1": 98_407,
                "openassistant-oasst1-apache-v1": 1_541,
                "russian-everyday-dialogues-cc-by-v1": 11,
                "russian-multiwoz-mit-v1": 10_393,
                "tatoeba-rus-cc0-v1": 22_789,
            },
        )
        self.assertEqual(report["records_rejected"], 0)
        self.assertEqual(report["unique_dialogue_turns"], 147_970)
        self.assertEqual(report["language_characters"], 241_707_400)
        self.assertEqual(report["training_block_reasons"], [])

    def test_manifest_cannot_escape_its_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _write_manifest(root, self._clean_rows())
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["sources"][0]["raw_path"] = "../outside.jsonl"
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(BrainV3CorpusError):
                audit_corpus(manifest)

    def test_manifest_cannot_treat_clean_output_as_a_raw_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _write_manifest(root, self._clean_rows())
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["sources"][0]["raw_path"] = "clean/owned-seed.jsonl"
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(BrainV3CorpusError):
                audit_corpus(manifest)


if __name__ == "__main__":
    unittest.main()
