"""Проверяет MOT Russian verifier на малом локальном staging-fixture."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_core.brain_v3_corpus import content_sha256
from ai_core.mot_russian import (
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
)
from evaluation.brain_v3_staging import BrainV3StagingError
from evaluation.verify_mot_russian import create_reports


def _language_record(text: str) -> dict:
    """Создаёт валидную fixture language_document с MOT provenance."""
    payload = {
        "format_version": 1,
        "id": "mot.fixture.001",
        "record_type": "language_document",
        "provenance": {
            "source_id": SOURCE_ID,
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "origin_url": SOURCE_ORIGIN_URL,
            "attribution": SOURCE_ATTRIBUTION,
        },
        "language": "ru",
        "layer": "language",
        "split": "train",
        "topic": "fixture",
        "quality_tier": "license_verified",
        "expected_response_properties": ["relevant"],
        "document_kind": "public_domain_news_article",
        "text": text,
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _active_manifest() -> dict:
    """Строит пустой active corpus для изолированного preflight fixture."""
    return {
        "format_version": 1,
        "corpus_name": "mot-verifier-fixture",
        "language_scope": ["ru"],
        "accepted_licenses": [SOURCE_LICENSE],
        "quality_gate": {
            "minimum_unique_dialogue_turns": 0,
            "minimum_language_characters": 1,
            "maximum_rejected_record_ratio": 0.0,
            "require_all_splits": False,
        },
        "sources": [],
    }


class VerifyMOTRussianTests(unittest.TestCase):
    """Проверяет candidate-only отчёты и запрет их overwrite."""

    def test_reports_are_isolated_and_do_not_expose_candidate_text(self) -> None:
        fixture_text = "Уникальная проверочная фраза для MOT audit."
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus_root = root / "brain_v3"
            staging_root = corpus_root / "staging"
            staging_root.mkdir(parents=True)
            candidate = staging_root / "candidate.jsonl"
            candidate.write_text(
                json.dumps(_language_record(fixture_text), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (corpus_root / "corpus_manifest.v1.json").write_text(
                json.dumps(_active_manifest(), ensure_ascii=False),
                encoding="utf-8",
            )
            audit_directory = root / "audit-report"
            preflight_directory = root / "preflight-report"

            audit, preflight = create_reports(
                candidate,
                audit_directory,
                preflight_directory,
                corpus_root=corpus_root,
                staging_root=staging_root,
            )
            report_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in [
                    audit_directory / "mot_russian_audit.json",
                    audit_directory / "MOT_RUSSIAN_AUDIT_REPORT.md",
                    preflight_directory / "mot_russian_preflight.json",
                    preflight_directory / "MOT_RUSSIAN_PREFLIGHT_REPORT.md",
                ]
            )

        self.assertEqual(audit["audit"]["records_accepted"], 1)
        self.assertFalse(audit["candidate_is_active"])
        self.assertTrue(preflight["clean_merge"])
        self.assertEqual(preflight["active_sources_checked"], 0)
        self.assertNotIn(fixture_text, report_text)

    def test_existing_report_directory_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus_root = root / "brain_v3"
            staging_root = corpus_root / "staging"
            staging_root.mkdir(parents=True)
            candidate = staging_root / "candidate.jsonl"
            candidate.write_text(
                json.dumps(_language_record("Безопасный fixture текст."), ensure_ascii=False) + "\n",
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
                create_reports(
                    candidate,
                    audit_directory,
                    preflight_directory,
                    corpus_root=corpus_root,
                    staging_root=staging_root,
                )


if __name__ == "__main__":
    unittest.main()
