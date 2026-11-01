"""Проверяет локальную очистку DReaM staging до возможного promotion."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ai_core.brain_v3_corpus import content_sha256
from ai_core.korp_dream_ru_open import (
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
)
from evaluation.clean_staged_dream_against_active import (
    DReaMActiveCleanError,
    clean_staged_dream_against_active,
    summary_markdown,
)


def _record(identifier: str, source_id: str, text: str, *, license_name: str, license_url: str, origin_url: str, attribution: str) -> dict:
    """Строит минимальную проверяемую language_document запись для теста."""
    payload = {
        "format_version": 1,
        "id": identifier,
        "record_type": "language_document",
        "provenance": {
            "source_id": source_id,
            "license": license_name,
            "license_url": license_url,
            "origin_url": origin_url,
            "attribution": attribution,
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
    """Возвращает маленький valid active manifest для изолированного теста."""
    return {
        "format_version": 1,
        "corpus_name": "dream-clean-fixture",
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
                "origin_url": "https://example.test/active",
                "attribution": "Fixture active source",
                "approved_for_training": True,
            }
        ],
    }


class DreamActiveCleanTests(unittest.TestCase):
    """Проверки exact/near фильтра, staging-границы и отсутствия partial output."""

    def _prepare(self, root: Path, active_text: str, candidate_texts: list[str]) -> tuple[Path, Path, Path]:
        corpus_root = root / "brain_v3"
        raw_root = corpus_root / "raw"
        staging_root = corpus_root / "staging"
        raw_root.mkdir(parents=True)
        staging_root.mkdir()
        active_path = raw_root / "active.jsonl"
        active = _record(
            "active.fixture-001",
            "active-source-v1",
            active_text,
            license_name="CC0-1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            origin_url="https://example.test/active",
            attribution="Fixture active source",
        )
        active_path.write_text(json.dumps(active, ensure_ascii=False) + "\n", encoding="utf-8")
        (corpus_root / "corpus_manifest.v1.json").write_text(
            json.dumps(_manifest(hashlib.sha256(active_path.read_bytes()).hexdigest()), ensure_ascii=False),
            encoding="utf-8",
        )
        candidate_path = staging_root / "candidate-v0.2.jsonl"
        candidate_rows = [
            _record(
                f"{SOURCE_ID}.fixture-{index:03d}",
                SOURCE_ID,
                text,
                license_name=SOURCE_LICENSE,
                license_url=SOURCE_LICENSE_URL,
                origin_url=SOURCE_ORIGIN_URL,
                attribution=SOURCE_ATTRIBUTION,
            )
            for index, text in enumerate(candidate_texts, start=1)
        ]
        candidate_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in candidate_rows),
            encoding="utf-8",
        )
        return corpus_root, staging_root, candidate_path

    def test_removes_exact_overlap_without_touching_active_files(self) -> None:
        unique_text = "Новая проверочная строка."
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging_root, candidate = self._prepare(
                Path(directory),
                "Совпадающая строка.",
                ["Совпадающая строка.", unique_text],
            )
            manifest_before = (corpus_root / "corpus_manifest.v1.json").read_bytes()
            active_before = (corpus_root / "raw" / "active.jsonl").read_bytes()
            output = staging_root / "candidate-v0.3.jsonl"
            report = clean_staged_dream_against_active(
                candidate,
                output,
                corpus_root=corpus_root,
                staging_root=staging_root,
            )
            produced_rows = output.read_text(encoding="utf-8").splitlines()

            self.assertEqual(manifest_before, (corpus_root / "corpus_manifest.v1.json").read_bytes())
            self.assertEqual(active_before, (corpus_root / "raw" / "active.jsonl").read_bytes())

        self.assertEqual(report["records_written"], 1)
        self.assertEqual(report["rejections"], {"exact_duplicate_with_active": 1})
        self.assertFalse(report["output_is_active"])
        self.assertEqual(len(produced_rows), 1)
        self.assertNotIn(unique_text, json.dumps(report, ensure_ascii=False))
        self.assertNotIn(unique_text, summary_markdown(report))

    def test_removes_near_overlap_when_only_punctuation_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging_root, candidate = self._prepare(
                Path(directory),
                "Сегодня в городе прохладно.",
                ["Сегодня в городе прохладно!", "Самостоятельная новая фраза."],
            )
            output = staging_root / "candidate-v0.3.jsonl"
            report = clean_staged_dream_against_active(
                candidate,
                output,
                corpus_root=corpus_root,
                staging_root=staging_root,
            )

        self.assertEqual(report["records_written"], 1)
        self.assertEqual(report["rejections"], {"near_duplicate_with_active": 1})

    def test_invalid_candidate_leaves_no_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging_root, candidate = self._prepare(
                Path(directory),
                "Активная строка.",
                ["Проверочная строка."],
            )
            candidate.write_text("{}\n", encoding="utf-8")
            output = staging_root / "candidate-v0.3.jsonl"

            with self.assertRaises(DReaMActiveCleanError):
                clean_staged_dream_against_active(
                    candidate,
                    output,
                    corpus_root=corpus_root,
                    staging_root=staging_root,
                )

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
