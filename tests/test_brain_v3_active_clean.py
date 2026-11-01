"""Проверяет reusable cross-source clean staging-кандидатов Brain v3."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ai_core.brain_v3_corpus import content_sha256
from ai_core.openassistant_oasst1 import (
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
)
from evaluation.brain_v3_active_clean import (
    BrainV3ActiveCleanError,
    clean_staged_candidate_against_active,
    summary_markdown,
)
from evaluation.brain_v3_staging import StagedSourceDescriptor


DESCRIPTOR = StagedSourceDescriptor(
    source_id=SOURCE_ID,
    license_name=SOURCE_LICENSE,
    license_url=SOURCE_LICENSE_URL,
    origin_url=SOURCE_ORIGIN_URL,
    attribution=SOURCE_ATTRIBUTION,
)


def _dialogue_record(
    identifier: str,
    source_id: str,
    user: str,
    assistant: str,
    *,
    license_name: str,
    license_url: str,
    origin_url: str,
    attribution: str,
) -> dict:
    """Строит minimal valid dialogue record для isolated overlap fixture."""
    payload = {
        "format_version": 1,
        "id": identifier,
        "record_type": "dialogue",
        "provenance": {
            "source_id": source_id,
            "license": license_name,
            "license_url": license_url,
            "origin_url": origin_url,
            "attribution": attribution,
        },
        "language": "ru",
        "layer": "dialogue",
        "split": "train",
        "topic": "fixture",
        "quality_tier": "human_reviewed",
        "expected_response_properties": ["relevant", "natural_tone"],
        "dialogue_goal": "fixture_exchange",
        "emotional_tone": "neutral",
        "turns": [
            {"role": "user", "text": user},
            {"role": "assistant", "text": assistant},
        ],
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _manifest(raw_hash: str) -> dict:
    """Возвращает малый чистый active manifest для cross-source fixture."""
    return {
        "format_version": 1,
        "corpus_name": "active-clean-fixture",
        "language_scope": ["ru"],
        "accepted_licenses": ["CC0-1.0", SOURCE_LICENSE],
        "quality_gate": {
            "minimum_unique_dialogue_turns": 0,
            "minimum_language_characters": 0,
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


class BrainV3ActiveCleanTests(unittest.TestCase):
    """Проверяет exact/near filter, atomic output и границу active corpus."""

    def _prepare(
        self,
        root: Path,
        active_pair: tuple[str, str],
        candidate_pairs: list[tuple[str, str]],
    ) -> tuple[Path, Path, Path]:
        corpus_root = root / "brain_v3"
        raw_root = corpus_root / "raw"
        staging_root = corpus_root / "staging"
        raw_root.mkdir(parents=True)
        staging_root.mkdir()
        active_path = raw_root / "active.jsonl"
        active = _dialogue_record(
            "active.fixture-001",
            "active-source-v1",
            *active_pair,
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
        candidate_path = staging_root / "candidate-v0.1.jsonl"
        candidate_rows = [
            _dialogue_record(
                f"oasst1.fixture-{index:03d}",
                SOURCE_ID,
                *pair,
                license_name=SOURCE_LICENSE,
                license_url=SOURCE_LICENSE_URL,
                origin_url=SOURCE_ORIGIN_URL,
                attribution=SOURCE_ATTRIBUTION,
            )
            for index, pair in enumerate(candidate_pairs, start=1)
        ]
        candidate_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in candidate_rows),
            encoding="utf-8",
        )
        return corpus_root, staging_root, candidate_path

    def test_removes_exact_overlap_without_touching_active_files(self) -> None:
        unique_user = "Новый независимый вопрос."
        unique_assistant = "Новый независимый ответ."
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging_root, candidate = self._prepare(
                Path(directory),
                ("Совпадающий вопрос.", "Совпадающий ответ."),
                [
                    ("Совпадающий вопрос.", "Совпадающий ответ."),
                    (unique_user, unique_assistant),
                ],
            )
            manifest_before = (corpus_root / "corpus_manifest.v1.json").read_bytes()
            active_before = (corpus_root / "raw" / "active.jsonl").read_bytes()
            output = staging_root / "candidate-v0.2.jsonl"
            report = clean_staged_candidate_against_active(
                candidate,
                output,
                DESCRIPTOR,
                corpus_root=corpus_root,
                staging_root=staging_root,
            )

            self.assertEqual(manifest_before, (corpus_root / "corpus_manifest.v1.json").read_bytes())
            self.assertEqual(active_before, (corpus_root / "raw" / "active.jsonl").read_bytes())

        rendered = json.dumps(report, ensure_ascii=False) + summary_markdown(report)
        self.assertEqual(report["records_written"], 1)
        self.assertEqual(report["rejections"], {"exact_duplicate_with_active": 1})
        self.assertFalse(report["output_is_active"])
        self.assertNotIn(unique_user, rendered)
        self.assertNotIn(unique_assistant, rendered)

    def test_removes_near_overlap_when_only_punctuation_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging_root, candidate = self._prepare(
                Path(directory),
                ("Сегодня в городе прохладно.", "Лучше взять тёплую куртку."),
                [
                    ("Сегодня в городе прохладно!", "Лучше взять тёплую куртку!"),
                    ("Другая самостоятельная реплика.", "Другая самостоятельная помощь."),
                ],
            )
            output = staging_root / "candidate-v0.2.jsonl"
            report = clean_staged_candidate_against_active(
                candidate,
                output,
                DESCRIPTOR,
                corpus_root=corpus_root,
                staging_root=staging_root,
            )

        self.assertEqual(report["records_written"], 1)
        self.assertEqual(report["rejections"], {"near_duplicate_with_active": 1})

    def test_invalid_candidate_leaves_no_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging_root, candidate = self._prepare(
                Path(directory),
                ("Активный вопрос.", "Активный ответ."),
                [("Проверочный вопрос.", "Проверочный ответ.")],
            )
            candidate.write_text("{}\n", encoding="utf-8")
            output = staging_root / "candidate-v0.2.jsonl"

            with self.assertRaises(BrainV3ActiveCleanError):
                clean_staged_candidate_against_active(
                    candidate,
                    output,
                    DESCRIPTOR,
                    corpus_root=corpus_root,
                    staging_root=staging_root,
                )

            self.assertFalse(output.exists())

    def test_existing_output_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging_root, candidate = self._prepare(
                Path(directory),
                ("Активный вопрос.", "Активный ответ."),
                [("Новый вопрос.", "Новый ответ.")],
            )
            output = staging_root / "candidate-v0.2.jsonl"
            output.write_text("existing", encoding="utf-8")

            with self.assertRaises(BrainV3ActiveCleanError):
                clean_staged_candidate_against_active(
                    candidate,
                    output,
                    DESCRIPTOR,
                    corpus_root=corpus_root,
                    staging_root=staging_root,
                )

            self.assertEqual(output.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
