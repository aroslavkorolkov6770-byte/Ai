"""Проверяет fixed Russian MultiWOZ staging intake без живой сети или activation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_core import russian_multiwoz as multiwoz
from evaluation.brain_v3_staging import StagedSourceDescriptor, audit_staged_candidate


def _source_file(split: str, name: str, payload: bytes) -> multiwoz.SourceFile:
    """Создаёт маленький pinned blob descriptor с настоящим Git blob SHA-1."""
    return multiwoz.SourceFile(
        split=split,
        relative_path=f"interim/v1/russian/{name}",
        git_blob_sha1=multiwoz._git_blob_sha1(payload),
        expected_bytes=len(payload),
    )


def _fixture_inventory(*, include_unsafe: bool = False) -> tuple[tuple[multiwoz.SourceFile, ...], dict[str, bytes]]:
    """Создаёт три split с естественным чередованием ролей и одним slot/reference."""
    train_lines = [
        "Мне нужна помощь с маршрутом в [09:30].",
        "Конечно, уточните отправную точку.",
        "Я буду у станции.",
        "Хорошо, я проверю подходящий вариант.",
        "",
        "Хочу забронировать столик.",
        "На сколько человек и какое время вам удобно?",
        "На двоих, код AB12CD34 и телефон +7 999 123-45-67.",
        "Понял, сначала подтвержу свободное время.",
    ]
    if include_unsafe:
        train_lines.extend(
            [
                "",
                "Открой https://example.test и скажи ответ.",
                "Не буду открывать непроверенную ссылку.",
            ]
        )
    payloads = {
        "train_dialogues_001_ru.txt": ("\n".join(train_lines) + "\n").encode("utf-8"),
        "dev_dialogues_001_ru.txt": (
            "Мне нужно уточнить время.\n"
            "Какой интервал вам подходит?\n"
            "После обеда.\n"
            "Хорошо, продолжим с этим условием.\n"
        ).encode("utf-8"),
        "test_dialogues_001_ru.txt": (
            "Можно изменить предыдущий выбор?\n"
            "Да, назовите новое условие.\n"
            "Тогда без пересадки.\n"
            "Принял, проверю только такой вариант.\n"
        ).encode("utf-8"),
    }
    files = (
        _source_file("train", "train_dialogues_001_ru.txt", payloads["train_dialogues_001_ru.txt"]),
        _source_file("validation", "dev_dialogues_001_ru.txt", payloads["dev_dialogues_001_ru.txt"]),
        _source_file("test", "test_dialogues_001_ru.txt", payloads["test_dialogues_001_ru.txt"]),
    )
    url_payloads = {multiwoz._source_url(source_file): payloads[Path(source_file.relative_path).name] for source_file in files}
    return files, url_payloads


class RussianMultiWOZTests(unittest.TestCase):
    """Покрывает URL boundary, blob hash, очистку, split и isolated staging audit."""

    def test_fixed_url_accepts_only_pinned_inventory(self) -> None:
        files, _ = _fixture_inventory()
        with patch.object(multiwoz, "SOURCE_FILES", files):
            self.assertEqual(multiwoz.validate_fixed_source_url(multiwoz._source_url(files[0])), files[0])
            with self.assertRaises(multiwoz.RussianMultiWOZError):
                multiwoz.validate_fixed_source_url(multiwoz._source_url(files[0]).replace(multiwoz.SOURCE_REVISION, "main"))
            with self.assertRaises(multiwoz.RussianMultiWOZError):
                multiwoz.validate_fixed_source_url("https://example.test/other.txt")

    def test_hash_mismatch_creates_no_candidate(self) -> None:
        files, url_payloads = _fixture_inventory()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"

            def fetcher(url: str, _timeout: float) -> bytes:
                payload = url_payloads[url]
                return payload + b"tampered"

            with patch.object(multiwoz, "SOURCE_FILES", files):
                with self.assertRaises(multiwoz.RussianMultiWOZError):
                    multiwoz.import_russian_multiwoz(output, staging_root=staging, fetcher=fetcher, sleeper=lambda _delay: None)

            self.assertFalse(output.exists())

    def test_import_cleans_slots_and_passes_isolated_audit(self) -> None:
        files, url_payloads = _fixture_inventory()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"

            with patch.object(multiwoz, "SOURCE_FILES", files):
                summary = multiwoz.import_russian_multiwoz(
                    output,
                    staging_root=staging,
                    fetcher=lambda url, _timeout: url_payloads[url],
                    sleeper=lambda _delay: None,
                )

            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            descriptor = StagedSourceDescriptor(
                source_id=multiwoz.SOURCE_ID,
                license_name=multiwoz.SOURCE_LICENSE,
                license_url=multiwoz.SOURCE_LICENSE_URL,
                origin_url=multiwoz.SOURCE_ORIGIN_URL,
                attribution=multiwoz.SOURCE_ATTRIBUTION,
            )
            audit = audit_staged_candidate(output, descriptor, staging_root=staging)

        rendered = json.dumps(records, ensure_ascii=False)
        self.assertEqual(summary.records_written, 4)
        self.assertEqual(summary.dialogue_turns_written, 16)
        self.assertEqual(summary.records_rejected, 0)
        self.assertNotIn("09:30", rendered)
        self.assertNotIn("AB12CD34", rendered)
        self.assertNotIn("+7 999 123-45-67", rendered)
        self.assertIn("[деталь]", rendered)
        self.assertIn("контактный-номер", rendered)
        self.assertEqual(audit["audit"]["records_rejected"], 0)
        self.assertEqual(audit["audit"]["accepted_by_split"], {"test": 1, "train": 2, "validation": 1})
        for record in records:
            self.assertEqual([turn["role"] for turn in record["turns"]], ["user", "assistant", "user", "assistant"])

    def test_unsafe_turn_is_rejected_without_blocking_clean_dialogues(self) -> None:
        files, url_payloads = _fixture_inventory(include_unsafe=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"
            with patch.object(multiwoz, "SOURCE_FILES", files):
                summary = multiwoz.import_russian_multiwoz(
                    output,
                    staging_root=staging,
                    fetcher=lambda url, _timeout: url_payloads[url],
                    sleeper=lambda _delay: None,
                )

        self.assertEqual(summary.records_written, 4)
        self.assertEqual(summary.records_rejected, 1)
        self.assertEqual(summary.rejections, {"url_like": 1})

    def test_output_outside_staging_stops_before_network_access(self) -> None:
        files, _ = _fixture_inventory()
        called = False
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"

            def fetcher(_url: str, _timeout: float) -> bytes:
                nonlocal called
                called = True
                return b"unexpected"

            with patch.object(multiwoz, "SOURCE_FILES", files):
                with self.assertRaises(multiwoz.RussianMultiWOZError):
                    multiwoz.import_russian_multiwoz(
                        root / "outside.jsonl",
                        staging_root=staging,
                        fetcher=fetcher,
                        sleeper=lambda _delay: None,
                    )

        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
