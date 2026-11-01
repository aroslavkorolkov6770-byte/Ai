"""Безопасный аудит и извлечение policy-разметки web-диалогового seed-набора.

Архив пользователя содержит документацию, JSON Schema и служебный
``validate_package.py``. Этот модуль никогда не запускает код из архива и не
считает текст документации инструкцией. Он самостоятельно проверяет ZIP,
контрольные суммы, JSON Schema и связи между данными, после чего извлекает
только реплики пользователя и метки решения о поиске.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DATASET_FORMAT_VERSION = 1
SPLITS = ("train", "validation", "test")
REQUIRED_ENTRIES = frozenset(
    {
        "schema.json",
        "sources.csv",
        "coverage_matrix.csv",
        "checksums.sha256",
        *(f"{split}.jsonl" for split in SPLITS),
    }
)
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_ENTRY_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 128
MAX_TOTAL_UNCOMPRESSED_BYTES = 24 * 1024 * 1024
MAX_RECORD_BYTES = 128 * 1024
MAX_RECORDS_PER_SPLIT = 10_000
NEAR_DUPLICATE_RATIO = 0.96
BROWSE_LABELS = (
    "insufficient_context",
    "may_browse",
    "must_browse",
    "must_not_browse",
    "should_browse",
)

_SPACE = re.compile(r"\s+")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d()\-\s]{8,}\d)(?!\w)")
_SECRET = re.compile(
    r"\b(?:api[_ -]?key|secret|password|парол[ья]|токен)\s*[:=]\s*\S+",
    flags=re.IGNORECASE,
)
_INSTRUCTION_LIKE = (
    re.compile(r"ignore\s+(?:all\s+)?previous", flags=re.IGNORECASE),
    re.compile(r"ignore\s+instructions", flags=re.IGNORECASE),
    re.compile(r"игнорируй\s+(?:все\s+)?предыдущ", flags=re.IGNORECASE),
    re.compile(r"system\s+prompt", flags=re.IGNORECASE),
)


class WebDialogueDatasetError(ValueError):
    """Останавливает импорт при повреждённом или неоднозначном наборе."""


@dataclass(frozen=True)
class WebDialoguePolicyExample:
    """Одна policy-запись без system, assistant, tool, evidence и ответа.

    В реальном чате доступны только текущая реплика и предыдущие реплики
    пользователя. Поэтому модель не получает готовый текст ideal_response,
    поисковые запросы, цитаты, внутренние подсказки и реплики ассистента.
    """

    identifier: str
    split: str
    user_messages: tuple[str, ...]
    browse_label: str
    expected_action: str
    clarification_needed: bool
    risk_level: str
    temporal_sensitivity: str

    @property
    def prompt(self) -> str:
        """Собирает пользовательский контекст без ролей, недоступных в чате."""
        return "\n".join(self.user_messages)


def file_sha256(path: Path) -> str:
    """Вычисляет SHA-256 потоком и не загружает архив целиком в память."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise(value: str) -> str:
    """Строит ключ сравнения без сохранения исходной реплики в отчёте."""
    return _SPACE.sub(" ", value.casefold()).strip()


def _fingerprint(value: str) -> str:
    """Создаёт необратимый ключ для дублей и межсплитовых пересечений."""
    return hashlib.sha256(_normalise(value).encode("utf-8")).hexdigest()


def _safe_entry(zip_file: zipfile.ZipFile, entry_name: str) -> zipfile.ZipInfo:
    """Возвращает разрешённую запись после проверки размера и пути."""
    try:
        entry = zip_file.getinfo(entry_name)
    except KeyError as error:
        raise WebDialogueDatasetError(f"В архиве нет обязательного файла {entry_name!r}.") from error
    if entry.is_dir() or entry.file_size < 1:
        raise WebDialogueDatasetError(f"Файл {entry_name!r} пуст или является папкой.")
    if entry.file_size > MAX_ENTRY_BYTES:
        raise WebDialogueDatasetError(f"Файл {entry_name!r} превышает безопасный лимит.")
    return entry


def _entry_lines(
    archive_path: Path,
    entry_name: str,
) -> Iterator[tuple[int, bytes]]:
    """Читает JSONL построчно без извлечения файлов на диск."""
    with zipfile.ZipFile(archive_path) as zip_file:
        entry = _safe_entry(zip_file, entry_name)
        with zip_file.open(entry, "r") as source:
            for line_number, raw_line in enumerate(source, start=1):
                if line_number > MAX_RECORDS_PER_SPLIT:
                    raise WebDialogueDatasetError(
                        f"{entry_name}: превышен безопасный лимит числа записей."
                    )
                if len(raw_line) > MAX_RECORD_BYTES:
                    raise WebDialogueDatasetError(
                        f"{entry_name}:{line_number}: JSONL-строка слишком велика."
                    )
                if raw_line.strip():
                    yield line_number, raw_line


def _read_json_record(raw_line: bytes, entry_name: str, line_number: int) -> dict[str, Any]:
    """Читает JSON как данные и требует объект верхнего уровня."""
    try:
        value = json.loads(raw_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WebDialogueDatasetError(
            f"{entry_name}:{line_number}: некорректный UTF-8 или JSON."
        ) from error
    if not isinstance(value, dict):
        raise WebDialogueDatasetError(
            f"{entry_name}:{line_number}: ожидается JSON-объект."
        )
    return value


def _matches_type(value: Any, expected: str) -> bool:
    """Проверяет JSON-типы, не смешивая bool и integer."""
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, int | float) and not isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _validate_format(value: str, format_name: str, path: str) -> None:
    """Поддерживает форматы JSON Schema, реально использованные этим архивом."""
    if format_name == "date":
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise WebDialogueDatasetError(f"{path}: ожидается дата ISO-8601.") from error
    elif format_name == "uri":
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise WebDialogueDatasetError(f"{path}: ожидается абсолютный HTTP(S) URL.")


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    """Проверяет используемое подмножество JSON Schema самостоятельно.

    Вложенные текстовые поля никогда не выполняются. Ошибка сообщает только
    путь и правило схемы, но не печатает содержимое пользовательской записи.
    """
    if "const" in schema and value != schema["const"]:
        raise WebDialogueDatasetError(f"{path}: значение не совпадает с const схемы.")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise WebDialogueDatasetError(f"{path}: значение отсутствует в enum схемы.")
    expected_types = schema.get("type")
    if expected_types is not None:
        expected = expected_types if isinstance(expected_types, list) else [expected_types]
        if not all(isinstance(item, str) for item in expected) or not any(
            _matches_type(value, item) for item in expected
        ):
            raise WebDialogueDatasetError(f"{path}: тип не соответствует JSON Schema.")

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise WebDialogueDatasetError(f"{path}: строка короче минимальной длины.")
        if isinstance(maximum, int) and len(value) > maximum:
            raise WebDialogueDatasetError(f"{path}: строка длиннее максимальной длины.")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise WebDialogueDatasetError(f"{path}: строка не проходит pattern схемы.")
        format_name = schema.get("format")
        if isinstance(format_name, str):
            _validate_format(value, format_name, path)

    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int | float) and value < minimum:
            raise WebDialogueDatasetError(f"{path}: число меньше minimum схемы.")
        if isinstance(maximum, int | float) and value > maximum:
            raise WebDialogueDatasetError(f"{path}: число больше maximum схемы.")

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise WebDialogueDatasetError(f"{path}: массив короче minItems схемы.")
        if isinstance(maximum, int) and len(value) > maximum:
            raise WebDialogueDatasetError(f"{path}: массив длиннее maxItems схемы.")
        if schema.get("uniqueItems"):
            fingerprints = {
                json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                for item in value
            }
            if len(fingerprints) != len(value):
                raise WebDialogueDatasetError(f"{path}: массив нарушает uniqueItems схемы.")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate_value(item, items, f"{path}[{index}]")

    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise WebDialogueDatasetError(f"{path}: некорректный required в JSON Schema.")
        missing = sorted(set(required) - value.keys())
        if missing:
            raise WebDialogueDatasetError(f"{path}: отсутствуют обязательные поля {missing}.")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise WebDialogueDatasetError(f"{path}: некорректные properties в JSON Schema.")
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            property_schema = properties.get(key)
            if property_schema is None:
                if additional is False:
                    raise WebDialogueDatasetError(f"{path}: лишнее поле {key!r} запрещено схемой.")
                property_schema = additional if isinstance(additional, dict) else None
            if isinstance(property_schema, dict):
                _validate_value(item, property_schema, f"{path}.{key}")


def _text_signals(value: Any) -> Counter[str]:
    """Считает рисковые паттерны, не сохраняя исходный текст отчёта."""
    signals: Counter[str] = Counter()
    if isinstance(value, str):
        if _EMAIL.search(value):
            signals["email_like"] += 1
        if _PHONE.search(value):
            signals["phone_like"] += 1
        if _SECRET.search(value):
            signals["credential_like"] += 1
        if any(pattern.search(value) for pattern in _INSTRUCTION_LIKE):
            signals["instruction_like"] += 1
    elif isinstance(value, dict):
        for item in value.values():
            signals.update(_text_signals(item))
    elif isinstance(value, list):
        for item in value:
            signals.update(_text_signals(item))
    return signals


def _load_schema(archive_path: Path) -> dict[str, Any]:
    """Загружает JSON Schema как данные и проверяет её минимальную форму."""
    with zipfile.ZipFile(archive_path) as zip_file:
        entry = _safe_entry(zip_file, "schema.json")
        try:
            schema = json.loads(zip_file.read(entry).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WebDialogueDatasetError("schema.json не является корректным JSON.") from error
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise WebDialogueDatasetError("schema.json должен описывать объект верхнего уровня.")
    if not isinstance(schema.get("properties"), dict) or not schema.get("required"):
        raise WebDialogueDatasetError("schema.json не содержит properties или required.")
    return schema


def _parse_checksums(archive_path: Path) -> dict[str, str]:
    """Читает контрольные суммы без запуска приложенного скрипта проверки."""
    with zipfile.ZipFile(archive_path) as zip_file:
        entry = _safe_entry(zip_file, "checksums.sha256")
        content = zip_file.read(entry).decode("utf-8")
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(content.splitlines(), start=1):
        clean = line.strip()
        if not clean:
            continue
        parts = clean.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            raise WebDialogueDatasetError(
                f"checksums.sha256:{line_number}: ожидается SHA-256 и имя файла."
            )
        entry_name = parts[1].lstrip(" *").replace("\\", "/")
        if not entry_name or entry_name in checksums:
            raise WebDialogueDatasetError(
                f"checksums.sha256:{line_number}: некорректное или повторное имя файла."
            )
        checksums[entry_name] = parts[0].lower()
    if not checksums:
        raise WebDialogueDatasetError("checksums.sha256 пуст.")
    return checksums


def _verify_entry_checksums(archive_path: Path, expected: dict[str, str]) -> dict[str, Any]:
    """Проверяет указанное в архиве содержимое своими средствами."""
    failures: list[str] = []
    with zipfile.ZipFile(archive_path) as zip_file:
        names = {entry.filename.replace("\\", "/") for entry in zip_file.infolist()}
        for entry_name, recorded_hash in expected.items():
            if entry_name not in names:
                failures.append(entry_name)
                continue
            # Контрольные суммы тоже не должны заставлять аудит распаковывать
            # неожиданно большой файл целиком в память.
            entry = _safe_entry(zip_file, entry_name)
            digest = hashlib.sha256()
            with zip_file.open(entry, "r") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            actual = digest.hexdigest()
            if actual != recorded_hash:
                failures.append(entry_name)
    if failures:
        raise WebDialogueDatasetError(
            f"Не совпали внутренние SHA-256 для {len(failures)} файлов."
        )
    return {"entries": len(expected), "verified": len(expected), "failures": []}


def _load_sources(archive_path: Path) -> tuple[set[str], dict[str, Any]]:
    """Проверяет registry первичных источников без запроса к интернету."""
    with zipfile.ZipFile(archive_path) as zip_file:
        entry = _safe_entry(zip_file, "sources.csv")
        text = zip_file.read(entry).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    required = {
        "citation_id",
        "title",
        "url",
        "publisher",
        "source_type",
        "published_at",
        "accessed_at",
        "evidence_summary",
        "redistribution_status",
    }
    if not rows or set(rows[0]) != required:
        raise WebDialogueDatasetError("sources.csv не соответствует ожидаемой структуре registry.")
    citation_ids: set[str] = set()
    urls: set[str] = set()
    source_types: Counter[str] = Counter()
    invalid_urls = 0
    for index, row in enumerate(rows, start=2):
        citation_id = row["citation_id"].strip()
        url = row["url"].strip()
        if not citation_id or citation_id in citation_ids:
            raise WebDialogueDatasetError(f"sources.csv:{index}: пустой или повторный citation_id.")
        citation_ids.add(citation_id)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            invalid_urls += 1
        urls.add(url)
        source_types[row["source_type"].strip()] += 1
    if invalid_urls:
        raise WebDialogueDatasetError("sources.csv содержит некорректный URL источника.")
    return urls, {
        "records": len(rows),
        "unique_citation_ids": len(citation_ids),
        "unique_urls": len(urls),
        "source_types": dict(sorted(source_types.items())),
    }


def _read_split(
    archive_path: Path,
    split: str,
    schema: dict[str, Any],
    source_urls: set[str],
) -> tuple[list[WebDialoguePolicyExample], dict[str, Any], set[str], list[tuple[str, str]]]:
    """Проверяет один split и извлекает только безопасные policy-поля."""
    entry_name = f"{split}.jsonl"
    rows: list[WebDialoguePolicyExample] = []
    identifiers: set[str] = set()
    prompts: set[str] = set()
    normalised_prompts: list[tuple[str, str]] = []
    labels: dict[str, Counter[str]] = {
        field: Counter()
        for field in (
            "language",
            "domain",
            "intent",
            "risk_level",
            "temporal_sensitivity",
            "expected_action",
            "browse_label",
        )
    }
    signals: Counter[str] = Counter()
    evidence_urls: set[str] = set()
    citation_errors = 0
    quality_errors = 0

    for line_number, raw_line in _entry_lines(archive_path, entry_name):
        payload = _read_json_record(raw_line, entry_name, line_number)
        _validate_value(payload, schema, f"{entry_name}:{line_number}")
        if payload["split"] != split:
            raise WebDialogueDatasetError(
                f"{entry_name}:{line_number}: split внутри записи не совпадает с файлом."
            )
        identifier = payload["id"].strip()
        if identifier in identifiers:
            raise WebDialogueDatasetError(f"{entry_name}:{line_number}: повторный id.")
        identifiers.add(identifier)
        user_messages = tuple(
            message["content"]
            for message in payload["conversation"]
            if message["role"] == "user"
        )
        if not user_messages:
            raise WebDialogueDatasetError(
                f"{entry_name}:{line_number}: в диалоге отсутствует реплика пользователя."
            )
        prompt = "\n".join(user_messages)
        prompt_fingerprint = _fingerprint(prompt)
        if prompt_fingerprint in prompts:
            raise WebDialogueDatasetError(
                f"{entry_name}:{line_number}: дублирующая реплика пользователя внутри split."
            )
        prompts.add(prompt_fingerprint)
        normalised_prompts.append((identifier, _normalise(prompt)))
        evidence_ids = {item["evidence_id"] for item in payload["evidence"]}
        if any(value not in evidence_ids for value in payload["citation_map"].values()):
            citation_errors += 1
        evidence_urls.update(item["url"] for item in payload["evidence"])
        quality = payload["quality_checks"]
        if not quality["schema_valid"] or quality["pii_scan"] != "pass":
            quality_errors += 1
        signals.update(_text_signals(payload))
        for field, counter in labels.items():
            counter[payload[field]] += 1
        rows.append(
            WebDialoguePolicyExample(
                identifier=identifier,
                split=split,
                user_messages=user_messages,
                browse_label=payload["browse_label"],
                expected_action=payload["expected_action"],
                clarification_needed=payload["clarification_needed"],
                risk_level=payload["risk_level"],
                temporal_sensitivity=payload["temporal_sensitivity"],
            )
        )
    if not rows:
        raise WebDialogueDatasetError(f"{entry_name}: split пуст.")
    unknown_urls = evidence_urls - source_urls
    if unknown_urls:
        raise WebDialogueDatasetError(
            f"{entry_name}: evidence содержит URL вне sources.csv."
        )
    if citation_errors:
        raise WebDialogueDatasetError(
            f"{entry_name}: найдено {citation_errors} несогласованных citation_map."
        )
    if quality_errors:
        raise WebDialogueDatasetError(
            f"{entry_name}: quality_checks содержит непрошедшие записи."
        )
    return rows, {
        "records": len(rows),
        "unique_ids": len(identifiers),
        "unique_user_prompt_fingerprints": len(prompts),
        "labels": {field: dict(sorted(counter.items())) for field, counter in labels.items()},
        "evidence_urls": len(evidence_urls),
        "risk_signals": dict(sorted(signals.items())),
    }, identifiers, normalised_prompts


def _near_duplicate_count(
    left: list[tuple[str, str]],
    right: list[tuple[str, str]],
) -> int:
    """Считает только почти точные кросс-сплит совпадения без вывода текста."""
    candidates = 0
    for _, left_prompt in left:
        for _, right_prompt in right:
            if not left_prompt or not right_prompt:
                continue
            length_ratio = min(len(left_prompt), len(right_prompt)) / max(
                len(left_prompt), len(right_prompt)
            )
            if length_ratio < NEAR_DUPLICATE_RATIO:
                continue
            if SequenceMatcher(a=left_prompt, b=right_prompt, autojunk=False).ratio() >= NEAR_DUPLICATE_RATIO:
                candidates += 1
    return candidates


def _verify_coverage_matrix(
    archive_path: Path,
    split_reports: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Сверяет заявленную matrix с фактическими label-подсчётами."""
    with zipfile.ZipFile(archive_path) as zip_file:
        entry = _safe_entry(zip_file, "coverage_matrix.csv")
        text = zip_file.read(entry).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    expected_headers = {
        "dimension",
        "category",
        "train",
        "validation",
        "test",
        "total",
        "target_note",
    }
    if not rows or set(rows[0]) != expected_headers:
        raise WebDialogueDatasetError("coverage_matrix.csv имеет неожиданную структуру.")
    mismatches = 0
    dimensions: Counter[str] = Counter()
    for row in rows:
        dimension = row["dimension"]
        category = row["category"]
        dimensions[dimension] += 1
        if dimension not in {"domain", "intent", "risk_level", "browse_label"}:
            raise WebDialogueDatasetError("coverage_matrix.csv содержит неизвестное измерение.")
        actual_total = 0
        for split in SPLITS:
            try:
                expected_count = int(row[split])
            except ValueError as error:
                raise WebDialogueDatasetError("coverage_matrix.csv содержит нецелое количество.") from error
            actual_count = split_reports[split]["labels"][dimension].get(category, 0)
            actual_total += actual_count
            if expected_count != actual_count:
                mismatches += 1
        try:
            recorded_total = int(row["total"])
        except ValueError as error:
            raise WebDialogueDatasetError("coverage_matrix.csv содержит нецелой total.") from error
        if recorded_total != actual_total:
            mismatches += 1
    if mismatches:
        raise WebDialogueDatasetError(
            f"coverage_matrix.csv не совпала с записями: {mismatches} расхождений."
        )
    return {"rows": len(rows), "dimensions": dict(sorted(dimensions.items())), "mismatches": 0}


def audit_web_dialogue_dataset(archive_path: Path) -> dict[str, Any]:
    """Проверяет весь ZIP и возвращает воспроизводимый отчёт без извлечения."""
    resolved = archive_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Не найден архив web dialogue dataset: {resolved}")
    if resolved.stat().st_size < 1 or resolved.stat().st_size > MAX_ARCHIVE_BYTES:
        raise WebDialogueDatasetError("Размер архива выходит за безопасный лимит.")
    with zipfile.ZipFile(resolved) as zip_file:
        broken_entry = zip_file.testzip()
        if broken_entry is not None:
            raise WebDialogueDatasetError(f"ZIP CRC не совпала у {broken_entry!r}.")
        entries = [entry for entry in zip_file.infolist() if not entry.is_dir()]
        if len(entries) > MAX_ARCHIVE_ENTRIES:
            raise WebDialogueDatasetError("В архиве слишком много файлов для безопасного импорта.")
        if sum(entry.file_size for entry in entries) > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise WebDialogueDatasetError(
                "Распакованный размер архива превышает безопасный лимит."
            )
        names = {entry.filename.replace("\\", "/") for entry in entries}
        missing = sorted(REQUIRED_ENTRIES - names)
        if missing:
            raise WebDialogueDatasetError(f"В архиве отсутствуют обязательные файлы: {missing}.")
        suspicious_paths = sum(
            1
            for name in names
            if name.startswith("/") or ":" in name or ".." in name.split("/")
        )
        if suspicious_paths:
            raise WebDialogueDatasetError("В архиве найдены опасные пути файлов.")

    schema = _load_schema(resolved)
    checksums = _verify_entry_checksums(resolved, _parse_checksums(resolved))
    source_urls, sources = _load_sources(resolved)
    records_by_split: dict[str, list[WebDialoguePolicyExample]] = {}
    split_reports: dict[str, dict[str, Any]] = {}
    ids_by_split: dict[str, set[str]] = {}
    prompts_by_split: dict[str, list[tuple[str, str]]] = {}
    for split in SPLITS:
        rows, report, identifiers, prompts = _read_split(resolved, split, schema, source_urls)
        records_by_split[split] = rows
        split_reports[split] = report
        ids_by_split[split] = identifiers
        prompts_by_split[split] = prompts

    cross_split: dict[str, dict[str, int]] = {}
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        left_prompts = {_fingerprint(value) for _, value in prompts_by_split[left]}
        right_prompts = {_fingerprint(value) for _, value in prompts_by_split[right]}
        cross_split[f"{left}-{right}"] = {
            "shared_ids": len(ids_by_split[left] & ids_by_split[right]),
            "shared_user_prompt_fingerprints": len(left_prompts & right_prompts),
            "near_duplicate_candidates": _near_duplicate_count(
                prompts_by_split[left],
                prompts_by_split[right],
            ),
        }
    if any(
        values["shared_ids"]
        or values["shared_user_prompt_fingerprints"]
        or values["near_duplicate_candidates"]
        for values in cross_split.values()
    ):
        raise WebDialogueDatasetError("Найдены межсплитовые дубли или почти точные совпадения.")
    coverage = _verify_coverage_matrix(resolved, split_reports)
    all_browse_labels = {
        example.browse_label
        for rows in records_by_split.values()
        for example in rows
    }
    training_eligible = all(
        set(report["labels"]["browse_label"]) == all_browse_labels
        for report in split_reports.values()
    ) and all_browse_labels == set(BROWSE_LABELS)
    return {
        "format_version": DATASET_FORMAT_VERSION,
        "container": {
            "archive_path": str(resolved),
            "archive_bytes": resolved.stat().st_size,
            "archive_sha256": file_sha256(resolved),
            "zip_crc_verified": True,
            "required_entries_present": True,
        },
        "schema": {
            "validated_records": sum(report["records"] for report in split_reports.values()),
            "required_fields": sorted(schema["required"]),
            "additional_properties": schema.get("additionalProperties"),
        },
        "checksums": checksums,
        "sources": sources,
        "splits": split_reports,
        "cross_split": cross_split,
        "coverage_matrix": coverage,
        "training_decision": {
            "web_policy_classifier_eligible": training_eligible,
            "free_response_generator_eligible": False,
            "policy_reason": (
                "Разметка browse_label согласована во всех split; модель получает "
                "только user-реплики и метку решения о поиске."
            ),
            "generator_reason": (
                "Набор из 200 синтетических seed-записей слишком мал для свободной "
                "генерации; ideal_response, evidence и system-поля не используются "
                "как цели генератора."
            ),
            "untrusted_archive_code_executed": False,
        },
    }


def iter_web_dialogue_policy_examples(
    archive_path: Path,
    expected_split: str | None = None,
) -> Iterator[WebDialoguePolicyExample]:
    """Возвращает policy-примеры после полной безопасной проверки архива."""
    if expected_split is not None and expected_split not in SPLITS:
        raise ValueError(f"Неизвестный split: {expected_split!r}.")
    audit_web_dialogue_dataset(archive_path)
    resolved = archive_path.expanduser().resolve()
    schema = _load_schema(resolved)
    source_urls, _ = _load_sources(resolved)
    for split in SPLITS:
        if expected_split is not None and split != expected_split:
            continue
        rows, _, _, _ = _read_split(resolved, split, schema, source_urls)
        yield from rows


def _write_markdown_report(report: dict[str, Any], output_path: Path) -> None:
    """Сохраняет понятный отчёт аудита без исходного текста диалогов."""
    lines = [
        "# Аудит dialogue_web_training_seed_v1",
        "",
        f"SHA-256 архива: `{report['container']['archive_sha256']}`.",
        "",
        "## Контейнер и схема",
        "",
        f"- ZIP CRC: пройдена.",
        f"- Внутренние SHA-256: {report['checksums']['verified']}/{report['checksums']['entries']}.",
        f"- Проверено JSON Schema записей: {report['schema']['validated_records']}.",
        f"- Источников в registry: {report['sources']['records']}.",
        "",
        "## Split",
        "",
        "| Split | Записей | Уникальных id | Уникальных user-реплик |",
        "| --- | ---: | ---: | ---: |",
    ]
    for split in SPLITS:
        current = report["splits"][split]
        lines.append(
            f"| {split} | {current['records']} | {current['unique_ids']} | "
            f"{current['unique_user_prompt_fingerprints']} |"
        )
    lines.extend(
        [
            "",
            "## Разделение и coverage",
            "",
            "Точных и почти точных межсплитовых user-дублей не найдено. "
            "Coverage matrix совпала с фактической разметкой.",
            "",
            "## Решение об использовании",
            "",
            "Набор разрешён для отдельного локального классификатора решения о "
            "поиске. Он не разрешён для свободной генерации: готовые ответы, "
            "evidence, system и архивный Python-код не используются и не исполняются.",
        ]
    )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Запускает аудит по CLI и сохраняет JSON/Markdown отчёты."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evaluation" / "results" / "dialogue-web-seed-audit",
    )
    args = parser.parse_args()
    try:
        report = audit_web_dialogue_dataset(args.archive)
        if args.output.exists():
            raise ValueError(f"Каталог отчёта уже существует: {args.output}")
        args.output.mkdir(parents=True)
        (args.output / "dialogue_web_seed_audit.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _write_markdown_report(report, args.output / "DIALOGUE_WEB_SEED_AUDIT.md")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (
        FileNotFoundError,
        OSError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        WebDialogueDatasetError,
    ) as error:
        raise SystemExit(f"Аудит dialogue web seed остановлен: {error}") from error


if __name__ == "__main__":
    main()
