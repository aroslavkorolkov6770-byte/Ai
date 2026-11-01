"""Проверяет объединённый preflight DReaM и active corpus без активации."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ai_core.brain_v3_corpus import content_sha256
from ai_core.korp_dream_ru_open import KORP_CORPUS_ID, KORP_SHOW_STRUCT, preview_dream_ru_open
from evaluation.preflight_dream_against_active_corpus import (
    DReaMActivePreflightError,
    preflight_dream_against_active,
    summary_markdown,
)


def _active_record(text: str) -> dict:
    """Строит маленькую искусственную активную language_document запись."""
    payload = {
        "format_version": 1,
        "id": "active.source.fixture-001",
        "record_type": "language_document",
        "provenance": {
            "source_id": "active-source-v1",
            "license": "CC0-1.0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "origin_url": "https://example.test/active-source",
            "attribution": "Fixture active source",
        },
        "language": "ru",
        "layer": "language",
        "split": "train",
        "topic": "fixture",
        "quality_tier": "license_verified",
        "expected_response_properties": ["natural_tone"],
        "document_kind": "fixture_sentence",
        "text": text,
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _manifest(raw_hash: str) -> dict:
    """Создаёт изолированный active manifest только для unit test."""
    return {
        "format_version": 1,
        "corpus_name": "preflight-fixture",
        "language_scope": ["ru"],
        "accepted_licenses": ["CC0-1.0", "CC-BY-4.0"],
        "quality_gate": {
            "minimum_unique_dialogue_turns": 0,
            "minimum_language_characters": 1,
            "maximum_rejected_record_ratio": 0.0,
            "require_all_splits": False,
        },
        "sources": [
            {
                "source_id": "active-source-v1",
                "raw_path": "raw/active.jsonl",
                "raw_sha256": raw_hash,
                "source_kind": "open_licensed",
                "license": "CC0-1.0",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "origin_url": "https://example.test/active-source",
                "attribution": "Fixture active source",
                "approved_for_training": True,
            }
        ],
    }


def _candidate_payload(words: list[str]) -> bytes:
    """Возвращает искусственный Korp JSON с одним sentence hit для preview."""
    return json.dumps(
        {
            "hits": 1,
            "kwic": [
                {
                    "corpus": KORP_CORPUS_ID,
                    "structs": {KORP_SHOW_STRUCT: "fixture-dream-1"},
                    "tokens": [{"word": word} for word in words],
                }
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")


class DreamActivePreflightTests(unittest.TestCase):
    """Проверки cross-source дублей, report privacy и запрета activation."""

    def _prepare(self, root: Path, active_text: str, candidate_words: list[str]) -> tuple[Path, Path, Path]:
        corpus_root = root / "brain_v3"
        raw = corpus_root / "raw"
        staging = corpus_root / "staging"
        raw.mkdir(parents=True)
        active_path = raw / "active.jsonl"
        active_path.write_text(json.dumps(_active_record(active_text), ensure_ascii=False) + "\n", encoding="utf-8")
        raw_hash = hashlib.sha256(active_path.read_bytes()).hexdigest()
        (corpus_root / "corpus_manifest.v1.json").write_text(
            json.dumps(_manifest(raw_hash), ensure_ascii=False),
            encoding="utf-8",
        )
        candidate = staging / "candidate.jsonl"
        preview_dream_ru_open(
            candidate,
            max_pages=1,
            matches_per_page=1,
            staging_root=staging,
            fetcher=lambda _url, _timeout: _candidate_payload(candidate_words),
            sleeper=lambda _: None,
        )
        return corpus_root, staging, candidate

    def test_clean_candidate_can_pass_cross_source_preflight_without_activation(self) -> None:
        source_text = "Свежий кандидат"
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging, candidate = self._prepare(
                Path(directory),
                "Активное предложение.",
                ["Свежий", "кандидат", "."],
            )
            report = preflight_dream_against_active(
                candidate,
                corpus_root=corpus_root,
                staging_root=staging,
            )
            report_json = json.dumps(report, ensure_ascii=False)
            markdown = summary_markdown(report)

        self.assertTrue(report["clean_merge"])
        self.assertEqual(report["audit"]["records_rejected"], 0)
        self.assertFalse(report["candidate_is_active"])
        self.assertFalse(report["active_manifest_updated"])
        self.assertFalse(report["training_started"])
        self.assertNotIn(source_text, report_json)
        self.assertNotIn(source_text, markdown)

    def test_cross_source_duplicate_blocks_clean_merge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging, candidate = self._prepare(
                Path(directory),
                "Одинаковый текст.",
                ["Одинаковый", "текст", "."],
            )
            report = preflight_dream_against_active(
                candidate,
                corpus_root=corpus_root,
                staging_root=staging,
            )

        self.assertFalse(report["clean_merge"])
        self.assertGreater(report["audit"]["records_rejected"], 0)

    def test_candidate_outside_staging_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.jsonl"
            outside.write_text("{}\n", encoding="utf-8")

            with self.assertRaises(DReaMActivePreflightError):
                preflight_dream_against_active(
                    outside,
                    corpus_root=root / "brain_v3",
                    staging_root=root / "brain_v3" / "staging",
                )


if __name__ == "__main__":
    unittest.main()
