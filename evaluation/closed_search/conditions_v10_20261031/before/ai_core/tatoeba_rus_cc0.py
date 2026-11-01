"""Импортирует только официальный русский CC0-экспорт Tatoeba в Brain v3.

Источник — это данные, а не команды. Модуль читает BZip2 TSV потоком,
пропускает неподходящие строки и создаёт неизменяемый JSONL-кандидат. Он не
обучает модель, не запускает код из архива и не добавляет результат в активный
чат. Лицензия и хэши фиксируются в отчёте вызывающего скрипта.
"""

from __future__ import annotations

import bz2
import hashlib
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .brain_v3_corpus import (
    MAX_DOCUMENT_LENGTH,
    NearDuplicateIndex,
    content_sha256,
    duplicate_signature,
    file_sha256,
    text_risk_code,
)


TATOEBA_RUS_CC0_URL = (
    "https://downloads.tatoeba.org/exports/per_language/rus/"
    "rus_sentences_CC0.tsv.bz2"
)
TATOEBA_ORIGIN_URL = "https://tatoeba.org/"
CC0_LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
SOURCE_ID = "tatoeba-rus-cc0-v1"
SOURCE_LICENSE = "CC0-1.0"
SOURCE_ATTRIBUTION = "Tatoeba Project, Russian CC0 sentence export"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_RECORD_BYTES = 128 * 1024
MAX_RECORDS = 1_000_000


class TatoebaImportError(ValueError):
    """Останавливает импорт при повреждении архива или небезопасном выводе."""


@dataclass(frozen=True)
class TatoebaImportSummary:
    """Содержит только метаданные импорта, без предложений исходного набора."""

    source_id: str
    input_path: str
    input_sha256: str
    output_path: str
    output_sha256: str
    records_read: int
    records_written: int
    records_rejected: int
    split_counts: dict[str, int]
    rejections: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        """Возвращает сериализуемую структуру для Markdown/JSON отчёта."""
        return {
            "format_version": 1,
            "source_id": self.source_id,
            "source_url": TATOEBA_RUS_CC0_URL,
            "origin_url": TATOEBA_ORIGIN_URL,
            "license": SOURCE_LICENSE,
            "license_url": CC0_LICENSE_URL,
            "attribution": SOURCE_ATTRIBUTION,
            "input_path": self.input_path,
            "input_sha256": self.input_sha256,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "records_read": self.records_read,
            "records_written": self.records_written,
            "records_rejected": self.records_rejected,
            "split_counts": self.split_counts,
            "rejections": self.rejections,
        }


def _normalised_fingerprint(text: str) -> str:
    """Строит ключ точного дубля, не сохраняя оригинальную строку в отчёте."""
    normalised = " ".join(text.casefold().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _deterministic_split(sentence_id: str) -> str:
    """Даёт стабильные train/validation/test без зависимости от порядка файла."""
    bucket = hashlib.sha256(f"{SOURCE_ID}:{sentence_id}".encode("utf-8")).digest()[0] % 100
    if bucket < 90:
        return "train"
    if bucket < 95:
        return "validation"
    return "test"


def _record(sentence_id: str, text: str) -> dict[str, Any]:
    """Строит одну language_document-запись с проверяемым content hash."""
    payload: dict[str, Any] = {
        "format_version": 1,
        "id": f"tatoeba.rus.cc0.{sentence_id}",
        "record_type": "language_document",
        "provenance": {
            "source_id": SOURCE_ID,
            "license": SOURCE_LICENSE,
            "license_url": CC0_LICENSE_URL,
            "origin_url": TATOEBA_ORIGIN_URL,
            "attribution": SOURCE_ATTRIBUTION,
        },
        "language": "ru",
        "layer": "language",
        "split": _deterministic_split(sentence_id),
        "topic": "open_sentence_corpus",
        "quality_tier": "license_verified",
        "expected_response_properties": ["natural_tone", "relevant"],
        "document_kind": "isolated_sentence",
        "text": text.strip(),
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _safe_archive(path: Path) -> Path:
    """Проверяет путь и размер до распаковки BZip2-потока."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Не найден Tatoeba-архив: {resolved}")
    if resolved.suffix.lower() != ".bz2":
        raise TatoebaImportError("Ожидается официальный BZip2 TSV-экспорт Tatoeba.")
    if resolved.stat().st_size < 1 or resolved.stat().st_size > MAX_ARCHIVE_BYTES:
        raise TatoebaImportError("Размер Tatoeba-архива выходит за безопасный лимит.")
    return resolved


def _prepare_destination(path: Path) -> Path:
    """Не разрешает перезапись ранее зафиксированного raw JSONL."""
    destination = path.expanduser().resolve()
    if destination.suffix.lower() != ".jsonl":
        raise TatoebaImportError("Вывод Tatoeba должен быть JSONL-файлом.")
    if destination.exists():
        raise TatoebaImportError(f"Выходной файл уже существует: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def import_tatoeba_rus_cc0(archive: Path, destination: Path) -> TatoebaImportSummary:
    """Конвертирует официальный CC0 TSV в отдельный raw JSONL без перезаписи.

    Базовый формат Tatoeba: ``sentence_id<TAB>language<TAB>text``. Некоторые
    актуальные экспортные файлы добавляют четвёртое мета-поле; оно никогда не
    выполняется и не переносится в корпус. Любая строка с неожиданной
    структурой, не-русской меткой, URL, PII-подобным фрагментом или точным
    дублем пропускается и учитывается только кодом причины.
    """
    source_path = _safe_archive(archive)
    output_path = _prepare_destination(destination)
    rejections: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    seen_fingerprints: set[str] = set()
    near_duplicates = NearDuplicateIndex()
    records_read = 0
    records_written = 0
    expanded_bytes = 0
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=".tatoeba-rus-cc0-",
            suffix=".jsonl.tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            with bz2.open(source_path, mode="rb") as input_stream:
                for line_number, raw_line in enumerate(input_stream, start=1):
                    if line_number > MAX_RECORDS:
                        raise TatoebaImportError("Tatoeba-архив превышает безопасный лимит строк.")
                    expanded_bytes += len(raw_line)
                    if expanded_bytes > MAX_EXPANDED_BYTES:
                        raise TatoebaImportError("Tatoeba-архив превышает безопасный лимит распаковки.")
                    if len(raw_line) > MAX_RECORD_BYTES:
                        rejections["line_too_long"] += 1
                        continue
                    records_read += 1
                    try:
                        line = raw_line.decode("utf-8").rstrip("\r\n")
                    except UnicodeDecodeError:
                        rejections["invalid_utf8"] += 1
                        continue
                    parts = line.split("\t")
                    if len(parts) not in {3, 4}:
                        rejections["invalid_tsv"] += 1
                        continue
                    sentence_id, language, text = parts[:3]
                    if not sentence_id.isdecimal():
                        rejections["invalid_sentence_id"] += 1
                        continue
                    if language != "rus":
                        rejections["unexpected_language"] += 1
                        continue
                    risk = text_risk_code(text, maximum=MAX_DOCUMENT_LENGTH)
                    if risk is not None:
                        rejections[risk] += 1
                        continue
                    fingerprint = _normalised_fingerprint(text)
                    if fingerprint in seen_fingerprints:
                        rejections["exact_duplicate"] += 1
                        continue
                    signature = duplicate_signature(text)
                    if near_duplicates.find(signature) is not None:
                        rejections["near_duplicate_candidate"] += 1
                        continue
                    seen_fingerprints.add(fingerprint)
                    record = _record(sentence_id, text)
                    near_duplicates.add(sentence_id, signature)
                    temporary.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                    temporary.write("\n")
                    split_counts[record["split"]] += 1
                    records_written += 1

        if records_written == 0:
            raise TatoebaImportError("Импорт Tatoeba не получил ни одной безопасной русской строки.")
        if output_path.exists():
            raise TatoebaImportError("Выходной файл появился во время импорта; перезапись запрещена.")
        assert temporary_path is not None
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return TatoebaImportSummary(
        source_id=SOURCE_ID,
        input_path=str(source_path),
        input_sha256=file_sha256(source_path),
        output_path=str(output_path),
        output_sha256=file_sha256(output_path),
        records_read=records_read,
        records_written=records_written,
        records_rejected=sum(rejections.values()),
        split_counts=dict(sorted(split_counts.items())),
        rejections=dict(sorted(rejections.items())),
    )


def summary_markdown(summary: TatoebaImportSummary) -> str:
    """Формирует отчёт о происхождении и числах без текста предложений."""
    return "\n".join(
        [
            "# Импорт русского Tatoeba CC0",
            "",
            f"Источник: [{SOURCE_ID}]({TATOEBA_RUS_CC0_URL}).",
            f"Лицензия: [{SOURCE_LICENSE}]({CC0_LICENSE_URL}).",
            f"Строк прочитано: {summary.records_read}; записано: {summary.records_written}; отклонено: {summary.records_rejected}.",
            f"Split: {summary.split_counts}.",
            f"SHA-256 исходного архива: `{summary.input_sha256}`.",
            f"SHA-256 сформированного JSONL: `{summary.output_sha256}`.",
            "",
            "Импорт не запускает обучение. Тексты предложений в отчёт не выводятся; перед использованием JSONL проходит независимый аудит корпуса.",
            "",
        ]
    )
