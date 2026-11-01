"""Проверяет закреплённый staging-импорт маленького CC-BY dialogue-пилота."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_core import russian_everyday_dialogues as dialogues
from ai_core.brain_v3_corpus import audit_corpus, file_sha256


_UNIQUE_PHRASES = (
    "северная лодка обогнула каменный маяк",
    "янтарная птица нашла высокую сосну",
    "бумажный змей поднялся над полем",
    "тихий фонарь осветил старый мост",
    "лесной ручей наполнил круглое озеро",
    "быстрый поезд миновал дальний пейзаж",
    "морская раковина лежала возле пирса",
    "горный ветер качнул белую палатку",
    "осенний лист накрыл мокрую скамью",
    "зимний след пересёк пустую тропу",
    "солнечный луч согрел тёмный камень",
    "ночной трамвай проехал мимо площади",
    "красная книга стояла на полке",
    "полевой цветок вырос у дороги",
    "серебряный ключ открыл старую дверь",
    "дальний самолёт исчез за облаком",
    "летняя гроза омыла зелёную крышу",
    "медный колокол зазвучал вдали",
    "школьный глобус показал синее море",
    "мягкий плед укрыл широкое кресло",
)


def _raw_row(identifier: int, situation: str, *, tag: str = "вежливость", assistant: str | None = None) -> dict:
    """Создаёт минимальную JSONL-строку в опубликованной source-схеме для теста."""
    phrase = _UNIQUE_PHRASES[identifier - 1]
    return {
        "id": identifier,
        "situation": situation,
        "user": f"Подскажите, где {phrase}?",
        "assistant": assistant or f"Спокойно объясняю: {phrase}.",
        "register": "нейтральный" if identifier % 2 else "разговорный",
        "tags": [tag],
    }


def _raw_payload() -> bytes:
    """Формирует 20 строк с тем же coverage, что закреплённый источник."""
    situations = ["магазин"] * 7 + ["транспорт"] * 6 + ["кафе"] * 7
    rows = [_raw_row(index, situation) for index, situation in enumerate(situations, start=1)]
    rows[1] = _raw_row(2, "магазин", tag="цена", assistant="Цена сегодня указана в рублях.")
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")


def _manifest(raw_hash: str) -> dict:
    """Строит отдельный manifest для проверки сгенерированного JSONL через audit."""
    return {
        "format_version": 1,
        "corpus_name": "russian-everyday-dialogues-fixture",
        "language_scope": ["ru"],
        "accepted_licenses": ["CC-BY-4.0"],
        "quality_gate": {
            "minimum_unique_dialogue_turns": 0,
            "minimum_language_characters": 0,
            "maximum_rejected_record_ratio": 0.0,
            "require_all_splits": True,
        },
        "sources": [
            {
                "source_id": dialogues.SOURCE_ID,
                "raw_path": "raw/candidate.jsonl",
                "raw_sha256": raw_hash,
                "source_kind": "open_licensed",
                "license": dialogues.SOURCE_LICENSE,
                "license_url": dialogues.SOURCE_LICENSE_URL,
                "origin_url": dialogues.SOURCE_ORIGIN_URL,
                "attribution": dialogues.SOURCE_ATTRIBUTION,
                "approved_for_training": True,
            }
        ],
    }


class RussianEverydayDialoguesTests(unittest.TestCase):
    """Проверки URL-гранницы, hash-pin, смыслового фильтра и staging-only записи."""

    def test_fixed_source_url_rejects_main_and_other_hosts(self) -> None:
        with self.assertRaises(dialogues.RussianEverydayDialoguesError):
            dialogues.validate_fixed_source_url(dialogues.SOURCE_URL.replace(dialogues.SOURCE_REVISION, "main"))
        with self.assertRaises(dialogues.RussianEverydayDialoguesError):
            dialogues.validate_fixed_source_url("https://example.test/dialogues.jsonl")

    def test_hash_mismatch_creates_no_output(self) -> None:
        payload = _raw_payload()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"
            with patch.object(dialogues, "EXPECTED_RAW_SHA256", "0" * 64):
                with self.assertRaises(dialogues.RussianEverydayDialoguesError):
                    dialogues.import_russian_everyday_dialogues(
                        output,
                        staging_root=staging,
                        fetcher=lambda _url, _timeout: payload,
                        sleeper=lambda _: None,
                    )
            self.assertFalse(output.exists())

    def test_import_filters_context_bound_fact_and_passes_independent_audit(self) -> None:
        payload = _raw_payload()
        expected_hash = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"
            with patch.object(dialogues, "EXPECTED_RAW_SHA256", expected_hash):
                summary = dialogues.import_russian_everyday_dialogues(
                    output,
                    staging_root=staging,
                    fetcher=lambda _url, _timeout: payload,
                    sleeper=lambda _: None,
                )
            report_json = json.dumps(summary.as_dict(), ensure_ascii=False)

            corpus_root = root / "brain_v3"
            raw_root = corpus_root / "raw"
            raw_root.mkdir(parents=True)
            linked_candidate = raw_root / "candidate.jsonl"
            os.link(output, linked_candidate)
            (corpus_root / "corpus_manifest.v1.json").write_text(
                json.dumps(_manifest(file_sha256(output)), ensure_ascii=False),
                encoding="utf-8",
            )
            audit = audit_corpus(corpus_root / "corpus_manifest.v1.json")

        self.assertEqual(summary.records_written, 19)
        self.assertEqual(summary.rejections, {"context_bound_local_fact": 1})
        self.assertEqual(summary.records_rejected, 1)
        self.assertTrue(output.is_relative_to(staging) if hasattr(output, "is_relative_to") else True)
        self.assertEqual(audit["records_rejected"], 0)
        self.assertTrue(audit["training_allowed"])
        self.assertEqual(audit["unique_dialogue_turns"], 38)
        self.assertNotIn("Проверочный вопрос", report_json)

    def test_output_outside_staging_is_rejected_before_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            called = False

            def fetcher(_url: str, _timeout: float) -> bytes:
                nonlocal called
                called = True
                return _raw_payload()

            with self.assertRaises(dialogues.RussianEverydayDialoguesError):
                dialogues.import_russian_everyday_dialogues(
                    root / "outside.jsonl",
                    staging_root=root / "staging",
                    fetcher=fetcher,
                    sleeper=lambda _: None,
                )
            self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
