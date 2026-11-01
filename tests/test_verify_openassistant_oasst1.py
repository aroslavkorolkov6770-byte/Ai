"""Проверяет OASST1 verifier на малом изолированном staging-fixture."""

from __future__ import annotations

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
from evaluation.brain_v3_staging import BrainV3StagingError
from evaluation.verify_openassistant_oasst1 import create_verification_reports


def _dialogue_record(text: str) -> dict:
    """Строит валидную OASST1-like dialogue запись без реальных source messages."""
    payload = {
        "format_version": 1,
        "id": "oasst1.fixture.001",
        "record_type": "dialogue",
        "provenance": {
            "source_id": SOURCE_ID,
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "origin_url": SOURCE_ORIGIN_URL,
            "attribution": SOURCE_ATTRIBUTION,
        },
        "language": "ru",
        "layer": "dialogue",
        "split": "train",
        "topic": "open_assistant_general",
        "quality_tier": "human_reviewed",
        "expected_response_properties": ["relevant", "natural_tone"],
        "dialogue_goal": "general_assistant_exchange",
        "emotional_tone": "unclassified",
        "turns": [
            {"role": "user", "text": text},
            {"role": "assistant", "text": "Понял, отвечу по существу."},
        ],
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _active_manifest() -> dict:
    """Создаёт пустой active corpus для isolated preflight fixture."""
    return {
        "format_version": 1,
        "corpus_name": "oasst1-verifier-fixture",
        "language_scope": ["ru"],
        "accepted_licenses": [SOURCE_LICENSE],
        "quality_gate": {
            "minimum_unique_dialogue_turns": 0,
            "minimum_language_characters": 0,
            "maximum_rejected_record_ratio": 0.0,
            "require_all_splits": False,
        },
        "sources": [],
    }


class VerifyOpenAssistantOASST1Tests(unittest.TestCase):
    """Проверяет isolated reports и запрет overwrite их каталогов."""

    def test_reports_are_isolated_and_do_not_expose_candidate_text(self) -> None:
        fixture_text = "Уникальная проверочная фраза для OASST1 audit."
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus_root = root / "brain_v3"
            staging_root = corpus_root / "staging"
            staging_root.mkdir(parents=True)
            candidate = staging_root / "candidate.jsonl"
            candidate.write_text(
                json.dumps(_dialogue_record(fixture_text), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (corpus_root / "corpus_manifest.v1.json").write_text(
                json.dumps(_active_manifest(), ensure_ascii=False),
                encoding="utf-8",
            )
            audit_directory = root / "audit-report"
            preflight_directory = root / "preflight-report"

            reports = create_verification_reports(
                candidate,
                audit_directory,
                preflight_directory,
                corpus_root=corpus_root,
                staging_root=staging_root,
            )
            report_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in [
                    audit_directory / "openassistant_oasst1_audit.json",
                    audit_directory / "OPENASSISTANT_OASST1_AUDIT_REPORT.md",
                    preflight_directory / "openassistant_oasst1_preflight.json",
                    preflight_directory / "OPENASSISTANT_OASST1_PREFLIGHT_REPORT.md",
                ]
            )

        self.assertEqual(reports["audit"]["audit"]["records_accepted"], 1)
        self.assertFalse(reports["audit"]["candidate_is_active"])
        self.assertTrue(reports["preflight"]["clean_merge"])
        self.assertEqual(reports["preflight"]["active_sources_checked"], 0)
        self.assertNotIn(fixture_text, report_text)

    def test_existing_report_directory_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus_root = root / "brain_v3"
            staging_root = corpus_root / "staging"
            staging_root.mkdir(parents=True)
            candidate = staging_root / "candidate.jsonl"
            candidate.write_text(
                json.dumps(_dialogue_record("Безопасный fixture текст."), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (corpus_root / "corpus_manifest.v1.json").write_text(
                json.dumps(_active_manifest(), ensure_ascii=False),
                encoding="utf-8",
            )
            audit_directory = root / "audit-report"
            preflight_directory = root / "preflight-report"
            audit_directory.mkdir()

            with self.assertRaises(BrainV3StagingError):
                create_verification_reports(
                    candidate,
                    audit_directory,
                    preflight_directory,
                    corpus_root=corpus_root,
                    staging_root=staging_root,
                )


if __name__ == "__main__":
    unittest.main()
