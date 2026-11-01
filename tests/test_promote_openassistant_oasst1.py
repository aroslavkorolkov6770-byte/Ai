"""Проверяет local-only OASST1 promotion на малом изолированном fixture corpus."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_core.brain_v3_corpus import content_sha256
from ai_core.openassistant_oasst1 import (
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
)
from evaluation.promote_openassistant_oasst1 import (
    BACKUP_FILE_NAME,
    MANIFEST_FILE_NAME,
    RAW_FILE_NAME,
    OpenAssistantOASST1PromotionError,
    promote_openassistant_oasst1,
)


def _record(text: str) -> dict:
    """Строит одну минимальную проверяемую dialogue-запись для fixture promotion."""
    payload = {
        "format_version": 1,
        "id": "openassistant.oasst1.fixture.001",
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
        "topic": "tool_routing",
        "quality_tier": "license_verified",
        "expected_response_properties": [
            "relevant",
            "context_retained",
            "ask_when_missing",
            "no_fake_certainty",
            "clear_next_step",
        ],
        "dialogue_goal": "follow_up_with_context",
        "emotional_tone": "calm",
        "turns": [
            {"role": "user", "text": text},
            {"role": "assistant", "text": "Понял, уточню это условие."},
        ],
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _empty_manifest() -> dict:
    """Строит валидный чистый active manifest до добавления fixture-source."""
    return {
        "format_version": 1,
        "corpus_name": "openassistant-oasst1-promotion-fixture",
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


class OpenAssistantOASST1PromotionTests(unittest.TestCase):
    """Покрывает proof-gate, backup, atomic raw copy и запрет overwrite."""

    def _prepare(self, root: Path, *, clean_merge: bool = True) -> tuple[dict[str, Path], str, int, bytes]:
        corpus_root = root / "brain_v3"
        staging_root = corpus_root / "staging"
        raw_root = corpus_root / "raw"
        staging_root.mkdir(parents=True)
        raw_root.mkdir()
        fixture_text = "Уникальная строка для OASST1 promotion fixture."
        candidate = staging_root / "openassistant-oasst1-apache-v0.2.jsonl"
        candidate_bytes = (json.dumps(_record(fixture_text), ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        candidate.write_bytes(candidate_bytes)
        candidate_sha256 = hashlib.sha256(candidate_bytes).hexdigest()
        manifest = corpus_root / MANIFEST_FILE_NAME
        manifest_bytes = (json.dumps(_empty_manifest(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        manifest.write_bytes(manifest_bytes)
        audit_proof = root / "audit.json"
        audit_proof.write_text(
            json.dumps(
                {
                    "mode": "staging_candidate_audit",
                    "candidate_sha256": candidate_sha256,
                    "source_id": SOURCE_ID,
                    "audit": {
                        "records_total": 1,
                        "records_accepted": 1,
                        "records_rejected": 0,
                        "cross_split_exact_duplicates": 0,
                        "cross_split_near_duplicate_candidates": 0,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        preflight_proof = root / "preflight.json"
        preflight_proof.write_text(
            json.dumps(
                {
                    "mode": "active_corpus_preflight",
                    "candidate_sha256": candidate_sha256,
                    "candidate_source_id": SOURCE_ID,
                    "active_sources_checked": 0,
                    "clean_merge": clean_merge,
                    "audit": {
                        "records_rejected": 0,
                        "cross_split_exact_duplicates": 0,
                        "cross_split_near_duplicate_candidates": 0,
                        "accepted_by_source": {SOURCE_ID: 1},
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        paths = {
            "corpus_root": corpus_root,
            "staging_root": staging_root,
            "candidate": candidate,
            "raw": raw_root / RAW_FILE_NAME,
            "manifest": manifest,
            "backup": corpus_root / "manifest_backups" / BACKUP_FILE_NAME,
            "audit": audit_proof,
            "preflight": preflight_proof,
            "report": root / "promotion-report",
        }
        return paths, candidate_sha256, len(candidate_bytes), manifest_bytes

    def _promotion_with_fixture_constants(
        self,
        paths: dict[str, Path],
        candidate_sha256: str,
        candidate_bytes: int,
    ) -> dict:
        with patch.multiple(
            "evaluation.promote_openassistant_oasst1",
            EXPECTED_CANDIDATE_SHA256=candidate_sha256,
            EXPECTED_CANDIDATE_BYTES=candidate_bytes,
            EXPECTED_CANDIDATE_RECORDS=1,
        ):
            return promote_openassistant_oasst1(
                candidate=paths["candidate"],
                raw_path=paths["raw"],
                manifest_path=paths["manifest"],
                backup_path=paths["backup"],
                audit_proof=paths["audit"],
                preflight_proof=paths["preflight"],
                report_directory=paths["report"],
                corpus_root=paths["corpus_root"],
                staging_root=paths["staging_root"],
            )

    def test_promotes_verified_candidate_with_backup_and_no_text_in_report(self) -> None:
        fixture_text = "Уникальная строка для OASST1 promotion fixture."
        with tempfile.TemporaryDirectory() as directory:
            paths, candidate_sha256, candidate_bytes, manifest_before = self._prepare(Path(directory))
            report = self._promotion_with_fixture_constants(paths, candidate_sha256, candidate_bytes)
            manifest_after = json.loads(paths["manifest"].read_text(encoding="utf-8"))
            report_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in [
                    paths["report"] / "openassistant_oasst1_promotion.json",
                    paths["report"] / "OPENASSISTANT_OASST1_PROMOTION_REPORT.md",
                ]
            )

            self.assertEqual(paths["raw"].read_bytes(), paths["candidate"].read_bytes())
            self.assertEqual(paths["backup"].read_bytes(), manifest_before)
            self.assertEqual(manifest_after["sources"][-1]["source_id"], SOURCE_ID)
            self.assertEqual(manifest_after["sources"][-1]["raw_sha256"], candidate_sha256)

        self.assertTrue(report["active_manifest_updated"])
        self.assertFalse(report["training_started"])
        self.assertEqual(report["active_audit_after"]["records_accepted"], 1)
        self.assertNotIn(fixture_text, report_text)

    def test_unclean_preflight_prevents_raw_copy_and_manifest_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths, candidate_sha256, candidate_bytes, manifest_before = self._prepare(Path(directory), clean_merge=False)

            with self.assertRaises(OpenAssistantOASST1PromotionError):
                self._promotion_with_fixture_constants(paths, candidate_sha256, candidate_bytes)

            self.assertFalse(paths["raw"].exists())
            self.assertFalse(paths["backup"].exists())
            self.assertEqual(paths["manifest"].read_bytes(), manifest_before)

    def test_existing_raw_target_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths, candidate_sha256, candidate_bytes, manifest_before = self._prepare(Path(directory))
            paths["raw"].write_text("existing", encoding="utf-8")

            with self.assertRaises(OpenAssistantOASST1PromotionError):
                self._promotion_with_fixture_constants(paths, candidate_sha256, candidate_bytes)

            self.assertEqual(paths["raw"].read_text(encoding="utf-8"), "existing")
            self.assertFalse(paths["backup"].exists())
            self.assertEqual(paths["manifest"].read_bytes(), manifest_before)


if __name__ == "__main__":
    unittest.main()
