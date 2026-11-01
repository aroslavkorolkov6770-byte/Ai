"""Проверяет независимый audit DReaM staging-кандидата без сети и activation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_core.korp_dream_ru_open import KORP_CORPUS_ID, KORP_SHOW_STRUCT, preview_dream_ru_open
from evaluation.audit_staged_dream_ru_open import (
    StagedDReaMAuditError,
    audit_staged_dream_ru_open,
    summary_markdown,
)


def _payload() -> bytes:
    """Возвращает искусственный безопасный Korp JSON без обращения к серверу."""
    return json.dumps(
        {
            "hits": 2,
            "kwic": [
                {
                    "corpus": KORP_CORPUS_ID,
                    "structs": {KORP_SHOW_STRUCT: "fixture-sentence-1"},
                    "tokens": [{"word": "Первый"}, {"word": "тест"}, {"word": "."}],
                },
                {
                    "corpus": KORP_CORPUS_ID,
                    "structs": {KORP_SHOW_STRUCT: "fixture-sentence-2"},
                    "tokens": [{"word": "Второй"}, {"word": "тест"}, {"word": "."}],
                },
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")


class DreamStagingAuditTests(unittest.TestCase):
    """Проверки staging-only path, отчёта без текста и отсутствия activation."""

    def test_audit_uses_hard_linked_candidate_without_exposing_text(self) -> None:
        source_text = "Первый тест"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            candidate = staging / "candidate.jsonl"
            preview_dream_ru_open(
                candidate,
                max_pages=1,
                matches_per_page=2,
                staging_root=staging,
                fetcher=lambda _url, _timeout: _payload(),
                sleeper=lambda _: None,
            )

            report = audit_staged_dream_ru_open(candidate, staging_root=staging)
            markdown = summary_markdown(report)
            report_json = json.dumps(report, ensure_ascii=False)

        self.assertEqual(report["audit"]["records_accepted"], 2)
        self.assertFalse(report["candidate_is_active"])
        self.assertFalse(report["active_manifest_updated"])
        self.assertFalse(report["training_started"])
        self.assertFalse(report["audit"]["training_allowed"])
        self.assertNotIn(source_text, markdown)
        self.assertNotIn(source_text, report_json)

    def test_audit_rejects_candidate_outside_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            outside = root / "outside.jsonl"
            outside.write_text("{}\n", encoding="utf-8")

            with self.assertRaises(StagedDReaMAuditError):
                audit_staged_dream_ru_open(outside, staging_root=staging)


if __name__ == "__main__":
    unittest.main()
