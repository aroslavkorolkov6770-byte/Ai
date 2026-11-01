"""Проверяет общий isolated audit/preflight staging-источников Brain v3."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ai_core.brain_v3_corpus import content_sha256
from evaluation.brain_v3_staging import (
    BrainV3StagingError,
    StagedSourceDescriptor,
    audit_staged_candidate,
    preflight_and_prospective_dialogue_coverage_for_staged_candidate,
    preflight_staged_candidate_against_active,
    preflight_staged_replacement_against_active,
    prospective_dialogue_coverage_for_staged_candidate,
)


CANDIDATE = StagedSourceDescriptor(
    source_id="candidate-dialogue-v1",
    license_name="CC-BY-4.0",
    license_url="https://creativecommons.org/licenses/by/4.0/",
    origin_url="https://example.test/candidate",
    attribution="Fixture candidate",
)

OWNED_CANDIDATE = StagedSourceDescriptor(
    source_id="owned-dialogue-v1",
    license_name="OWNED_BY_PROJECT",
    license_url=None,
    origin_url=None,
    attribution="Original project-owned fixture",
    source_kind="owned",
)


def _dialogue_record(identifier: str, descriptor: StagedSourceDescriptor, user: str, assistant: str) -> dict:
    """Создаёт валидную короткую Brain v3 dialogue-запись для fixture."""
    payload = {
        "format_version": 1,
        "id": identifier,
        "record_type": "dialogue",
        "provenance": {
            "source_id": descriptor.source_id,
            "license": descriptor.license_name,
            "license_url": descriptor.license_url,
            "origin_url": descriptor.origin_url,
            "attribution": descriptor.attribution,
        },
        "language": "ru",
        "layer": "dialogue",
        "split": "train",
        "topic": "fixture",
        "quality_tier": "license_verified",
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


def _active_manifest(raw_hash: str) -> dict:
    """Возвращает отдельный active manifest с одной fixture-записью."""
    return {
        "format_version": 1,
        "corpus_name": "staging-fixture",
        "language_scope": ["ru"],
        "accepted_licenses": ["CC0-1.0", "CC-BY-4.0"],
        "quality_gate": {
            "minimum_unique_dialogue_turns": 0,
            "minimum_language_characters": 0,
            "maximum_rejected_record_ratio": 0.0,
            "require_all_splits": False,
        },
        "sources": [
            {
                "source_id": "active-dialogue-v1",
                "raw_path": "raw/active.jsonl",
                "raw_sha256": raw_hash,
                "source_kind": "open_licensed",
                "license": "CC0-1.0",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "origin_url": "https://example.test/active",
                "attribution": "Fixture active",
                "approved_for_training": True,
            }
        ],
    }


class BrainV3StagingTests(unittest.TestCase):
    """Проверяет staging boundary, privacy отчёта и exact cross-source gate."""

    def _prepare(
        self,
        root: Path,
        candidate_user: str,
        candidate_assistant: str,
        active_user: str,
        active_assistant: str,
        candidate_descriptor: StagedSourceDescriptor = CANDIDATE,
    ) -> tuple[Path, Path, Path]:
        corpus_root = root / "brain_v3"
        raw_root = corpus_root / "raw"
        staging_root = corpus_root / "staging"
        raw_root.mkdir(parents=True)
        staging_root.mkdir()
        active_descriptor = StagedSourceDescriptor(
            source_id="active-dialogue-v1",
            license_name="CC0-1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            origin_url="https://example.test/active",
            attribution="Fixture active",
        )
        active_path = raw_root / "active.jsonl"
        active_path.write_text(
            json.dumps(_dialogue_record("active.dialogue.001", active_descriptor, active_user, active_assistant), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (corpus_root / "corpus_manifest.v1.json").write_text(
            json.dumps(_active_manifest(hashlib.sha256(active_path.read_bytes()).hexdigest()), ensure_ascii=False),
            encoding="utf-8",
        )
        candidate = staging_root / "candidate.jsonl"
        candidate.write_text(
            json.dumps(
                _dialogue_record(
                    "candidate.dialogue.001",
                    candidate_descriptor,
                    candidate_user,
                    candidate_assistant,
                ),
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return corpus_root, staging_root, candidate

    def test_audit_accepts_staging_candidate_without_exposing_text(self) -> None:
        source_text = "Уникальная реплика staging-кандидата."
        with tempfile.TemporaryDirectory() as directory:
            _, staging_root, candidate = self._prepare(
                Path(directory),
                source_text,
                "Уникальный ответ staging-кандидата.",
                "Активная реплика.",
                "Активный ответ.",
            )
            report = audit_staged_candidate(candidate, CANDIDATE, staging_root=staging_root)

        self.assertEqual(report["audit"]["records_accepted"], 1)
        self.assertFalse(report["candidate_is_active"])
        self.assertNotIn(source_text, json.dumps(report, ensure_ascii=False))

    def test_audit_accepts_owned_candidate_without_external_urls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, staging_root, candidate = self._prepare(
                Path(directory),
                "Нужен спокойный разговор без внешнего источника.",
                "Давай разберём ситуацию по шагам и не будем спешить.",
                "Активная реплика.",
                "Активный ответ.",
                OWNED_CANDIDATE,
            )
            report = audit_staged_candidate(
                candidate,
                OWNED_CANDIDATE,
                staging_root=staging_root,
            )

        self.assertEqual(report["source_id"], OWNED_CANDIDATE.source_id)
        self.assertEqual(report["license"], "OWNED_BY_PROJECT")
        self.assertEqual(report["audit"]["records_accepted"], 1)

    def test_preflight_blocks_cross_source_exact_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging_root, candidate = self._prepare(
                Path(directory),
                "Одинаковая реплика.",
                "Одинаковый ответ.",
                "Одинаковая реплика.",
                "Одинаковый ответ.",
            )
            report = preflight_staged_candidate_against_active(
                candidate,
                CANDIDATE,
                corpus_root=corpus_root,
                staging_root=staging_root,
            )

        self.assertFalse(report["clean_merge"])
        self.assertEqual(report["audit"]["rejections"], {"exact_duplicate": 1})

    def test_combined_preflight_and_coverage_reuses_one_verified_base_audit(self) -> None:
        source_text = "Уникальная реплика combined staging fixture."
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging_root, candidate = self._prepare(
                Path(directory),
                source_text,
                "Короткий ответ combined staging fixture.",
                "Независимая active реплика.",
                "Независимый active ответ.",
            )
            preflight, prospective = (
                preflight_and_prospective_dialogue_coverage_for_staged_candidate(
                    candidate,
                    CANDIDATE,
                    corpus_root=corpus_root,
                    staging_root=staging_root,
                )
            )
            rendered = json.dumps({"preflight": preflight, "prospective": prospective}, ensure_ascii=False)

        self.assertTrue(preflight["clean_merge"])
        self.assertTrue(prospective["coverage"]["coverage_observed"])
        self.assertFalse(preflight["candidate_is_active"])
        self.assertFalse(prospective["training_started"])
        self.assertNotIn(source_text, rendered)

    def test_replacement_preflight_excludes_only_named_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging_root, candidate = self._prepare(
                Path(directory),
                "Реплика, которая повторяет parent source.",
                "Ответ, который повторяет parent source.",
                "Реплика, которая повторяет parent source.",
                "Ответ, который повторяет parent source.",
            )
            secondary_path = corpus_root / "raw" / "secondary.jsonl"
            secondary_descriptor = StagedSourceDescriptor(
                source_id="active-dialogue-v2",
                license_name="CC-BY-4.0",
                license_url="https://creativecommons.org/licenses/by/4.0/",
                origin_url="https://example.test/secondary",
                attribution="Secondary active fixture",
            )
            secondary_path.write_text(
                json.dumps(
                    _dialogue_record(
                        "active.dialogue.002",
                        secondary_descriptor,
                        "Независимая активная реплика.",
                        "Независимый активный ответ.",
                    ),
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            manifest_path = corpus_root / "corpus_manifest.v1.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["sources"].append(
                {
                    "source_id": "active-dialogue-v2",
                    "raw_path": "raw/secondary.jsonl",
                    "raw_sha256": hashlib.sha256(secondary_path.read_bytes()).hexdigest(),
                    "source_kind": "open_licensed",
                    "license": "CC-BY-4.0",
                    "license_url": "https://creativecommons.org/licenses/by/4.0/",
                    "origin_url": "https://example.test/secondary",
                    "attribution": "Secondary active fixture",
                    "approved_for_training": True,
                }
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )

            report = preflight_staged_replacement_against_active(
                candidate,
                CANDIDATE,
                replaced_source_id="active-dialogue-v1",
                corpus_root=corpus_root,
                staging_root=staging_root,
            )

            self.assertTrue(report["clean_snapshot"])
            self.assertEqual(report["replaced_source_id"], "active-dialogue-v1")
            self.assertEqual(report["active_sources_checked"], 1)
            self.assertFalse(report["active_manifest_updated"])
            self.assertFalse(report["training_started"])
            active_source_ids = {
                source["source_id"] for source in json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )["sources"]
            }
            self.assertEqual(
                active_source_ids,
                {"active-dialogue-v1", "active-dialogue-v2"},
            )

    def test_prospective_coverage_keeps_candidate_outside_active_corpus(self) -> None:
        source_text = "Нужен отдельный контекст для prospective coverage fixture."
        with tempfile.TemporaryDirectory() as directory:
            corpus_root, staging_root, candidate = self._prepare(
                Path(directory),
                source_text,
                "Давай спокойно отделим известное от того, что надо уточнить.",
                "Активная реплика для отдельной prospective fixture.",
                "Активный ответ для отдельной prospective fixture.",
            )
            report = prospective_dialogue_coverage_for_staged_candidate(
                candidate,
                CANDIDATE,
                corpus_root=corpus_root,
                staging_root=staging_root,
            )

        self.assertTrue(report["coverage"]["coverage_observed"])
        self.assertFalse(report["coverage"]["coverage_allowed"])
        self.assertFalse(report["candidate_is_active"])
        self.assertNotIn(source_text, json.dumps(report, ensure_ascii=False))

    def test_candidate_outside_staging_is_rejected_before_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.jsonl"
            outside.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(BrainV3StagingError):
                audit_staged_candidate(outside, CANDIDATE, staging_root=root / "staging")


if __name__ == "__main__":
    unittest.main()
