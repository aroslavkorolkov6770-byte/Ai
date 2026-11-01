"""Проверяет metadata-only coverage gate будущего dialogue-корпуса Brain v3."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_core.brain_v3_corpus import audit_corpus, content_sha256
from ai_core.brain_v3_dialogue_coverage import (
    DEFAULT_POLICY,
    DialogueCoverageError,
    audit_dialogue_coverage,
    load_dialogue_coverage_policy,
    summary_markdown,
)


PROVENANCE = {
    "source_id": "owned-coverage-v1",
    "license": "OWNED_BY_PROJECT",
    "license_url": None,
    "origin_url": None,
    "attribution": "Local coverage fixture",
}


def _record(
    identifier: str,
    split: str,
    *,
    topic: str,
    dialogue_goal: str,
    emotional_tone: str,
    properties: list[str],
    turns: list[dict[str, str]],
) -> dict[str, object]:
    """Строит fixture dialogue-запись с валидным corpus hash без реального train-data."""
    payload: dict[str, object] = {
        "format_version": 1,
        "id": identifier,
        "record_type": "dialogue",
        "provenance": PROVENANCE,
        "language": "ru",
        "layer": "dialogue",
        "split": split,
        "topic": topic,
        "quality_tier": "human_reviewed",
        "expected_response_properties": properties,
        "dialogue_goal": dialogue_goal,
        "emotional_tone": emotional_tone,
        "turns": turns,
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _write_manifest(root: Path, rows: list[dict[str, object]]) -> Path:
    """Создаёт маленький immutable corpus для изолированного coverage-test."""
    raw_directory = root / "raw"
    raw_directory.mkdir()
    raw_path = raw_directory / "owned-coverage-v1.jsonl"
    raw_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    manifest = {
        "format_version": 1,
        "corpus_name": "coverage-fixture",
        "language_scope": ["ru"],
        "accepted_licenses": ["OWNED_BY_PROJECT"],
        "quality_gate": {
            "minimum_unique_dialogue_turns": 8,
            "minimum_language_characters": 0,
            "maximum_rejected_record_ratio": 0.0,
            "require_all_splits": True,
        },
        "sources": [
            {
                "source_id": "owned-coverage-v1",
                "raw_path": "raw/owned-coverage-v1.jsonl",
                "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                "source_kind": "owned",
                "license": "OWNED_BY_PROJECT",
                "license_url": None,
                "origin_url": None,
                "attribution": "Local coverage fixture",
                "approved_for_training": True,
            }
        ],
    }
    manifest_path = root / "corpus_manifest.v1.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def _small_policy() -> dict[str, object]:
    """Возвращает намеренно маленькую policy, достижимую тремя fixture-диалогами."""
    return {
        "format_version": 1,
        "policy_id": "coverage_fixture_v1",
        "scope": {"language": "ru", "record_type": "dialogue", "layer": "dialogue"},
        "minimums": {
            "minimum_unique_dialogue_turns": 8,
            "minimum_dialogue_records": 3,
            "minimum_multi_turn_records": 1,
            "minimum_multi_turn_ratio": 0.3,
            "minimum_turns_by_split": {"train": 4, "validation": 2, "test": 2},
        },
        "required_topics": {"casual_dialogue": 6, "emotional_support": 2},
        "required_dialogue_goals": {"casual_connection": 6, "emotional_support": 2},
        "required_emotional_tones": {"warm": 6, "supportive": 2},
        "required_response_properties": {
            "relevant": 8,
            "natural_tone": 6,
            "appropriate_empathy": 2,
        },
    }


def _write_policy(root: Path, payload: dict[str, object]) -> Path:
    """Сохраняет отдельную test-only coverage policy."""
    path = root / "dialogue_coverage_policy.v1.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class BrainV3DialogueCoverageTests(unittest.TestCase):
    """Покрывает policy, защищённый audit и отсутствие утечки raw metadata/text."""

    def _diverse_rows(self) -> list[dict[str, object]]:
        return [
            _record(
                "coverage.train.001",
                "train",
                topic="casual_dialogue",
                dialogue_goal="casual_connection",
                emotional_tone="warm",
                properties=["relevant", "natural_tone"],
                turns=[
                    {"role": "user", "text": "Хочу спокойно поговорить после длинного дня."},
                    {"role": "assistant", "text": "Конечно, можешь рассказать, что сегодня больше всего запомнилось."},
                    {"role": "user", "text": "Я закончил важную часть проекта и теперь немного выдохнул."},
                    {"role": "assistant", "text": "Звучит как хороший момент заметить результат и дать себе короткий отдых."},
                ],
            ),
            _record(
                "coverage.validation.001",
                "validation",
                topic="emotional_support",
                dialogue_goal="emotional_support",
                emotional_tone="supportive",
                properties=["relevant", "appropriate_empathy"],
                turns=[
                    {"role": "user", "text": "Перед разговором я немного волнуюсь."},
                    {"role": "assistant", "text": "Это понятно: можно заранее выбрать одну главную мысль, которую хочешь сказать."},
                ],
            ),
            _record(
                "coverage.test.001",
                "test",
                topic="casual_dialogue",
                dialogue_goal="casual_connection",
                emotional_tone="warm",
                properties=["relevant", "natural_tone"],
                turns=[
                    {"role": "user", "text": "Хочу закончить вечер без спешки."},
                    {"role": "assistant", "text": "Тогда выбери одно простое приятное занятие и не пытайся вместить всё сразу."},
                ],
            ),
        ]

    def test_small_diverse_fixture_passes_its_explicit_coverage_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = _write_manifest(root, self._diverse_rows())
            policy_path = _write_policy(root, _small_policy())
            report = audit_dialogue_coverage(manifest_path, policy_path)

        self.assertTrue(report["coverage_observed"])
        self.assertTrue(report["coverage_allowed"])
        self.assertTrue(report["generator_training_ready"])
        self.assertEqual(report["observed"]["dialogue_turns"], 8)
        self.assertEqual(report["observed"]["multi_turn_dialogue_records"], 1)
        self.assertEqual(report["observed"]["dialogue_turns_by_split"], {"test": 2, "train": 4, "validation": 2})
        self.assertFalse(report["training_started"])

    def test_verified_base_audit_reuses_same_clean_fixture_without_second_full_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = _write_manifest(root, self._diverse_rows())
            policy_path = _write_policy(root, _small_policy())
            base_audit = audit_corpus(manifest_path)
            with patch(
                "ai_core.brain_v3_dialogue_coverage.audit_corpus",
                side_effect=AssertionError("second corpus audit must not run"),
            ):
                report = audit_dialogue_coverage(
                    manifest_path,
                    policy_path,
                    verified_base_audit=base_audit,
                )

        self.assertTrue(report["coverage_observed"])
        self.assertTrue(report["generator_training_ready"])
        self.assertEqual(report["base_corpus_audit"]["records_accepted"], 3)

    def test_malformed_reused_base_audit_is_rejected_before_metadata_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = _write_manifest(root, self._diverse_rows())
            policy_path = _write_policy(root, _small_policy())
            malformed_audit = audit_corpus(manifest_path)
            malformed_audit["records_total"] = -1
            with self.assertRaisesRegex(DialogueCoverageError, "invalid_reused_base_audit"):
                audit_dialogue_coverage(
                    manifest_path,
                    policy_path,
                    verified_base_audit=malformed_audit,
                )

    def test_default_policy_blocks_unclassified_dialogue_diversity_without_leaking_source_tags(self) -> None:
        report = audit_dialogue_coverage()
        rendered = json.dumps(report, ensure_ascii=False)

        self.assertTrue(report["base_corpus_audit"]["training_allowed"])
        self.assertEqual(report["observed"]["dialogue_records"], 11_945)
        self.assertEqual(report["observed"]["dialogue_turns"], 147_970)
        self.assertEqual(report["observed"]["multi_turn_dialogue_records"], 11_354)
        self.assertEqual(report["observed"]["topic_turns"]["tool_routing"], 142_250)
        self.assertEqual(report["observed"]["dialogue_goal_turns"]["follow_up_with_context"], 142_250)
        self.assertEqual(report["observed"]["response_property_turns"]["natural_tone"], 5_720)
        self.assertEqual(report["observed"]["unmapped_topic_turns"], 5_720)
        self.assertFalse(report["coverage_allowed"])
        self.assertFalse(report["generator_training_ready"])
        self.assertIn("insufficient_topic_coverage:casual_dialogue", report["coverage_block_reasons"])
        self.assertNotIn("everyday_cafe", rendered)

    def test_unmapped_source_metadata_is_counted_without_being_rendered(self) -> None:
        unique_text = "Уникальная реплика для проверки отсутствия утечки coverage отчёта."
        rows = [
            _record(
                "coverage.train.unmapped",
                "train",
                topic="private_source_marker",
                dialogue_goal="private_goal_marker",
                emotional_tone="private_tone_marker",
                properties=["relevant"],
                turns=[
                    {"role": "user", "text": unique_text},
                    {"role": "assistant", "text": "Ответ fixture без публикации исходной реплики."},
                ],
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = _write_manifest(root, rows)
            policy_path = _write_policy(root, _small_policy())
            report = audit_dialogue_coverage(manifest_path, policy_path)
            rendered = json.dumps(report, ensure_ascii=False) + summary_markdown(report)

        self.assertTrue(report["coverage_observed"])
        self.assertEqual(report["observed"]["unmapped_topic_turns"], 2)
        self.assertEqual(report["observed"]["unmapped_dialogue_goal_turns"], 2)
        self.assertEqual(report["observed"]["unmapped_emotional_tone_turns"], 2)
        self.assertNotIn("private_source_marker", rendered)
        self.assertNotIn("private_goal_marker", rendered)
        self.assertNotIn("private_tone_marker", rendered)
        self.assertNotIn(unique_text, rendered)

    def test_unclean_base_corpus_blocks_metadata_collection_before_report(self) -> None:
        unsafe_text = "Проверка адреса https://example.test не должна попасть в coverage report."
        rows = [
            _record(
                "coverage.train.unsafe",
                "train",
                topic="casual_dialogue",
                dialogue_goal="casual_connection",
                emotional_tone="warm",
                properties=["relevant"],
                turns=[
                    {"role": "user", "text": unsafe_text},
                    {"role": "assistant", "text": "Fixture ответ."},
                ],
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = _write_manifest(root, rows)
            policy_path = _write_policy(root, _small_policy())
            report = audit_dialogue_coverage(manifest_path, policy_path)
            rendered = json.dumps(report, ensure_ascii=False) + summary_markdown(report)

        self.assertFalse(report["coverage_observed"])
        self.assertEqual(report["coverage_block_reasons"], ["base_corpus_not_clean"])
        self.assertNotIn(unsafe_text, rendered)
        self.assertNotIn("https://example.test", rendered)

    def test_policy_rejects_unknown_response_property(self) -> None:
        payload = _small_policy()
        payload["required_response_properties"] = {"unknown_property": 1}
        with tempfile.TemporaryDirectory() as directory:
            policy_path = _write_policy(Path(directory), payload)
            with self.assertRaises(DialogueCoverageError):
                load_dialogue_coverage_policy(policy_path)

    def test_default_policy_requires_every_supported_response_property(self) -> None:
        policy = load_dialogue_coverage_policy(DEFAULT_POLICY)

        self.assertEqual(
            {tag for tag, _ in policy.required_response_properties},
            {
                "relevant",
                "natural_tone",
                "brief",
                "context_retained",
                "no_repeat",
                "ask_when_missing",
                "fact_grounded",
                "no_url_without_request",
                "no_unfounded_memory",
                "privacy_respected",
                "acknowledges_correction",
                "appropriate_empathy",
                "clear_next_step",
                "no_fake_certainty",
                "safe_boundary",
                "source_on_request",
                "uses_requested_style",
            },
        )


if __name__ == "__main__":
    unittest.main()
