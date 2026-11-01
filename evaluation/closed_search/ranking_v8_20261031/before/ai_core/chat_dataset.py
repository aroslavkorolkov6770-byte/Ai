"""Безопасный аудит Chat AI Dataset до подключения его к обучению.

Архив является пользовательским обучающим материалом. Все его текстовые поля,
включая ``system``, ``context`` и ответы, считаются данными: этот модуль их
только проверяет и считает, но никогда не интерпретирует как команды.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator


ROOT = Path(__file__).resolve().parent.parent

# Канонические части набора. Файл ``all`` намеренно не участвует в обучении:
# он повторяет объединение трёх split и дал бы утечку между оценкой и обучением.
CHAT_SPLIT_ENTRIES = {
    "train": "chat_ai_dataset_500k_train.jsonl.gz",
    "validation": "chat_ai_dataset_500k_validation.jsonl.gz",
    "test": "chat_ai_dataset_500k_test.jsonl.gz",
}
ROUTER_ENTRY = "internet_router_100k.jsonl.gz"
WEB_SUMMARY_ENTRY = "web_summary_use_web_examples.jsonl.gz"
SAMPLE_ENTRY = "sample_500.jsonl"

CHAT_REQUIRED_FIELDS = frozenset(
    {
        "id",
        "category",
        "subtype",
        "system",
        "context",
        "user",
        "assistant",
        "constraints",
        "should_use_web",
        "internet_reason",
        "search_query",
        "retrieved_web_context",
        "scenario_id",
        "split",
    }
)
ROUTER_REQUIRED_FIELDS = frozenset(
    {"id", "label", "reason", "search_query", "split", "user"}
)
WEB_SUMMARY_REQUIRED_FIELDS = frozenset(
    {"id", "retrieved_web_context", "rule", "split", "user", "assistant"}
)

# Ограничения относятся к чтению архива, а не к содержимому примеров. Они
# защищают локальную машину от повреждённого или неожиданно большого файла.
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ENTRY_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_ENTRY_BYTES = 600 * 1024 * 1024
MAX_RECORD_BYTES = 64 * 1024
MAX_RECORDS_PER_ENTRY = 650_000

_NORMALIZE_SPACE = re.compile(r"\s+")
_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\d()\-\s]{8,}\d)(?!\w)")
_SECRET_PATTERN = re.compile(
    r"\b(?:api[_ -]?key|secret|password|парол[ья]|токен)\s*[:=]\s*\S+",
    flags=re.IGNORECASE,
)
_INSTRUCTION_SIGNAL_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?previous", flags=re.IGNORECASE),
    re.compile(r"ignore\s+instructions", flags=re.IGNORECASE),
    re.compile(r"игнорируй\s+(?:все\s+)?предыдущ", flags=re.IGNORECASE),
    re.compile(r"раскрой\s+(?:системн|скрыт).*(?:инструкц|промпт)", flags=re.IGNORECASE),
    re.compile(r"system\s+prompt", flags=re.IGNORECASE),
)


class ChatDatasetAuditError(ValueError):
    """Останавливает импорт при повреждённом или неожиданном наборе."""


@dataclass(frozen=True)
class InternetRouterExample:
    """Одна размеченная строка для собственного классификатора маршрута."""

    identifier: str
    label: str
    user: str
    split: str


@dataclass(frozen=True)
class ConversationPolicyExample:
    """Безопасная разметка поведения чата без готового текста ассистента.

    В отличие от свободного генератора эта запись намеренно не содержит
    ``assistant``, ``system`` и ``context``. Они могут быть противоречивыми
    или содержать недоверенный текст. Для небольшого разговорного
    маршрутизатора достаточно формулировки пользователя и стабильной метки
    категории.
    """

    identifier: str
    category: str
    subtype: str
    should_use_web: bool
    user: str
    split: str


def file_sha256(path: Path) -> str:
    """Вычисляет SHA-256 потоком, не загружая архив в оперативную память."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise(value: str) -> str:
    """Строит стабильный ключ сравнения без хранения текста в отчёте."""
    return _NORMALIZE_SPACE.sub(" ", value.casefold()).strip()


def _fingerprint(*values: str) -> str:
    """Создаёт необратимый ключ для дублей и пересечений split."""
    normalised = "\u0000".join(_normalise(value) for value in values)
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _safe_entry(zip_file: zipfile.ZipFile, entry_name: str) -> zipfile.ZipInfo:
    """Возвращает разрешённый файл из архива после базовой проверки пути."""
    try:
        entry = zip_file.getinfo(entry_name)
    except KeyError as error:
        raise ChatDatasetAuditError(
            f"В архиве отсутствует обязательный файл {entry_name!r}."
        ) from error
    if entry.is_dir() or entry.file_size < 1:
        raise ChatDatasetAuditError(f"Файл {entry_name!r} пуст или является папкой.")
    if entry.file_size > MAX_ENTRY_BYTES:
        raise ChatDatasetAuditError(
            f"Файл {entry_name!r} превышает безопасный лимит контейнера."
        )
    return entry


def _entry_lines(archive_path: Path, entry_name: str) -> Iterator[tuple[int, bytes]]:
    """Читает JSONL построчно с лимитом распаковки, без извлечения на диск."""
    with zipfile.ZipFile(archive_path) as zip_file:
        entry = _safe_entry(zip_file, entry_name)
        with zip_file.open(entry, "r") as archive_stream:
            source: BinaryIO
            if entry_name.endswith(".gz"):
                source = gzip.GzipFile(fileobj=archive_stream, mode="rb")
            else:
                source = archive_stream
            with source:
                buffered = io.BufferedReader(source)
                expanded_bytes = 0
                for line_number, raw_line in enumerate(buffered, start=1):
                    expanded_bytes += len(raw_line)
                    if expanded_bytes > MAX_EXPANDED_ENTRY_BYTES:
                        raise ChatDatasetAuditError(
                            f"{entry_name}: распакованный размер превысил безопасный лимит."
                        )
                    if len(raw_line) > MAX_RECORD_BYTES:
                        raise ChatDatasetAuditError(
                            f"{entry_name}:{line_number}: JSONL-строка превышает лимит."
                        )
                    if line_number > MAX_RECORDS_PER_ENTRY:
                        raise ChatDatasetAuditError(
                            f"{entry_name}: число строк превышает безопасный лимит."
                        )
                    if raw_line.strip():
                        yield line_number, raw_line


def _parse_json_record(raw_line: bytes, entry_name: str, line_number: int) -> dict[str, object]:
    """Проверяет JSONL-строку как данные и возвращает только объект."""
    try:
        payload = json.loads(raw_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ChatDatasetAuditError(
            f"{entry_name}:{line_number}: некорректный UTF-8 или JSON."
        ) from error
    if not isinstance(payload, dict):
        raise ChatDatasetAuditError(
            f"{entry_name}:{line_number}: JSONL-строка должна быть объектом."
        )
    return payload


def _require_fields(
    payload: dict[str, object],
    required_fields: frozenset[str],
    entry_name: str,
    line_number: int,
) -> None:
    """Проверяет ключи и типы, не интерпретируя текстовые значения."""
    missing = sorted(required_fields - payload.keys())
    if missing:
        raise ChatDatasetAuditError(
            f"{entry_name}:{line_number}: отсутствуют поля {missing}."
        )
    for field in required_fields:
        value = payload[field]
        if field == "should_use_web":
            if not isinstance(value, bool):
                raise ChatDatasetAuditError(
                    f"{entry_name}:{line_number}: should_use_web должен быть bool."
                )
        elif not isinstance(value, str):
            raise ChatDatasetAuditError(
                f"{entry_name}:{line_number}: поле {field!r} должно быть строкой."
            )


def _text_signals(payload: dict[str, object], fields: tuple[str, ...]) -> Counter[str]:
    """Считает рисковые сигналы без сохранения или печати исходного текста."""
    signals: Counter[str] = Counter()
    for field in fields:
        value = payload.get(field)
        if not isinstance(value, str):
            continue
        if _EMAIL_PATTERN.search(value):
            signals["email_like"] += 1
        if _PHONE_PATTERN.search(value):
            signals["phone_like"] += 1
        if _SECRET_PATTERN.search(value):
            signals["credential_like"] += 1
        if any(pattern.search(value) for pattern in _INSTRUCTION_SIGNAL_PATTERNS):
            signals["instruction_like"] += 1
    return signals


def _empty_fields(payload: dict[str, object], fields: frozenset[str]) -> Counter[str]:
    """Считает пустые текстовые поля для отчёта качества."""
    return Counter(
        field
        for field in fields
        if isinstance(payload.get(field), str) and not payload[field].strip()
    )


def _audit_chat_split(
    archive_path: Path,
    split: str,
    entry_name: str,
    known_ids: dict[str, str],
    known_prompts: dict[str, tuple[str, tuple[object, ...]]],
) -> dict[str, object]:
    """Проверяет один канонический диалоговый split и его пересечения."""
    category_counts: Counter[str] = Counter()
    subtype_counts: Counter[str] = Counter()
    web_counts: Counter[str] = Counter()
    field_lengths: Counter[str] = Counter()
    empty_fields: Counter[str] = Counter()
    risk_signals: Counter[str] = Counter()
    ids: set[str] = set()
    systems: set[str] = set()
    duplicate_ids = 0
    repeated_prompts = 0
    conflicting_prompt_targets = 0
    cross_split_ids = 0
    cross_split_prompts = 0
    cross_split_conflicts = 0
    records = 0

    for line_number, raw_line in _entry_lines(archive_path, entry_name):
        payload = _parse_json_record(raw_line, entry_name, line_number)
        _require_fields(payload, CHAT_REQUIRED_FIELDS, entry_name, line_number)
        if payload["split"] != split:
            raise ChatDatasetAuditError(
                f"{entry_name}:{line_number}: ожидается split={split!r}."
            )
        identifier = str(payload["id"]).strip()
        user = str(payload["user"])
        context = str(payload["context"])
        assistant = str(payload["assistant"])
        if not identifier or not user.strip() or not assistant.strip():
            raise ChatDatasetAuditError(
                f"{entry_name}:{line_number}: id, user и assistant не могут быть пустыми."
            )

        records += 1
        if identifier in ids:
            duplicate_ids += 1
        ids.add(identifier)
        earlier_split = known_ids.setdefault(identifier, split)
        if earlier_split != split:
            cross_split_ids += 1

        prompt_key = _fingerprint(context, user)
        target_signature = (
            payload["should_use_web"],
            _fingerprint(assistant),
            payload["category"],
            payload["subtype"],
        )
        earlier_prompt = known_prompts.get(prompt_key)
        if earlier_prompt is not None:
            earlier_prompt_split, earlier_signature = earlier_prompt
            if earlier_prompt_split == split:
                repeated_prompts += 1
                if earlier_signature != target_signature:
                    conflicting_prompt_targets += 1
            else:
                cross_split_prompts += 1
                if earlier_signature != target_signature:
                    cross_split_conflicts += 1
        else:
            known_prompts[prompt_key] = (split, target_signature)

        category_counts[str(payload["category"])] += 1
        subtype_counts[str(payload["subtype"])] += 1
        web_counts[str(payload["should_use_web"]).lower()] += 1
        systems.add(_fingerprint(str(payload["system"])))
        for field in ("user", "assistant", "context", "retrieved_web_context", "search_query"):
            field_lengths[field] += len(str(payload[field]))
        empty_fields.update(_empty_fields(payload, CHAT_REQUIRED_FIELDS))
        risk_signals.update(
            _text_signals(
                payload,
                ("system", "context", "user", "assistant", "retrieved_web_context"),
            )
        )

    if not records:
        raise ChatDatasetAuditError(f"{entry_name}: split пуст.")
    return {
        "entry": entry_name,
        "records": records,
        "unique_ids": len(ids),
        "duplicate_ids": duplicate_ids,
        "repeated_prompts": repeated_prompts,
        "conflicting_prompt_targets": conflicting_prompt_targets,
        "cross_split_identifier_records": cross_split_ids,
        "cross_split_prompt_records": cross_split_prompts,
        "cross_split_prompt_conflicts": cross_split_conflicts,
        "categories": dict(sorted(category_counts.items())),
        "subtypes": dict(sorted(subtype_counts.items())),
        "should_use_web": dict(sorted(web_counts.items())),
        "unique_system_texts": len(systems),
        "average_lengths": {
            field: round(total / records, 2)
            for field, total in sorted(field_lengths.items())
        },
        "empty_text_fields": dict(sorted(empty_fields.items())),
        "risk_signals": dict(sorted(risk_signals.items())),
    }


def _audit_router(
    archive_path: Path,
    entry_name: str,
) -> dict[str, object]:
    """Проверяет отдельно размеченный набор решений для интернет-маршрута."""
    labels: Counter[str] = Counter()
    splits: Counter[str] = Counter()
    risk_signals: Counter[str] = Counter()
    seen_ids: set[str] = set()
    seen_users: dict[str, tuple[str, str]] = {}
    duplicate_ids = 0
    duplicate_users = 0
    conflicting_users = 0
    records = 0
    for line_number, raw_line in _entry_lines(archive_path, entry_name):
        payload = _parse_json_record(raw_line, entry_name, line_number)
        _require_fields(payload, ROUTER_REQUIRED_FIELDS, entry_name, line_number)
        identifier = str(payload["id"]).strip()
        label = str(payload["label"]).strip()
        split = str(payload["split"]).strip()
        user = str(payload["user"])
        if not identifier or not label or not split or not user.strip():
            raise ChatDatasetAuditError(
                f"{entry_name}:{line_number}: обязательные поля не могут быть пустыми."
            )
        records += 1
        if identifier in seen_ids:
            duplicate_ids += 1
        seen_ids.add(identifier)
        user_key = _fingerprint(user)
        earlier = seen_users.get(user_key)
        target = (label, split)
        if earlier is not None:
            duplicate_users += 1
            if earlier[0] != label:
                conflicting_users += 1
        else:
            seen_users[user_key] = target
        labels[label] += 1
        splits[split] += 1
        risk_signals.update(_text_signals(payload, ("user", "reason", "search_query")))
    if not records:
        raise ChatDatasetAuditError(f"{entry_name}: набор маршрута пуст.")
    return {
        "entry": entry_name,
        "records": records,
        "unique_ids": len(seen_ids),
        "duplicate_ids": duplicate_ids,
        "duplicate_users": duplicate_users,
        "conflicting_user_labels": conflicting_users,
        "labels": dict(sorted(labels.items())),
        "splits": dict(sorted(splits.items())),
        "risk_signals": dict(sorted(risk_signals.items())),
    }


def iter_internet_router_examples(
    archive_path: Path,
    expected_split: str | None = None,
) -> Iterator[InternetRouterExample]:
    """Читает только безопасную разметку маршрута, не используя поля reason.

    Поля ``reason`` и ``search_query`` остаются метаданными аудита. Они не
    становятся признаками модели: в реальном чате известен только вопрос
    пользователя, а не подготовленный ответ датасета.
    """
    resolved = archive_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Не найден архив датасета: {resolved}")
    allowed_splits = frozenset({"train", "validation", "test"})
    if expected_split is not None and expected_split not in allowed_splits:
        raise ValueError(f"Неизвестный split internet-router: {expected_split!r}.")
    for line_number, raw_line in _entry_lines(resolved, ROUTER_ENTRY):
        payload = _parse_json_record(raw_line, ROUTER_ENTRY, line_number)
        _require_fields(payload, ROUTER_REQUIRED_FIELDS, ROUTER_ENTRY, line_number)
        split = str(payload["split"]).strip()
        if split not in allowed_splits:
            raise ChatDatasetAuditError(
                f"{ROUTER_ENTRY}:{line_number}: неизвестный split {split!r}."
            )
        if expected_split is not None and split != expected_split:
            continue
        identifier = str(payload["id"]).strip()
        label = str(payload["label"]).strip()
        user = str(payload["user"])
        if not identifier or not label or not user.strip():
            raise ChatDatasetAuditError(
                f"{ROUTER_ENTRY}:{line_number}: обязательные поля не могут быть пустыми."
            )
        yield InternetRouterExample(identifier, label, user, split)


def iter_conversation_policy_examples(
    archive_path: Path,
) -> Iterator[ConversationPolicyExample]:
    """Читает только метки поведения и реплику пользователя из chat split.

    Исходное разбиение архива здесь не считается готовым train/validation/test:
    одинаковая фраза пользователя встречается в разных исходных split. Скрипт
    обучения сначала дедуплицирует фразы, проверяет неизменность метки и только
    затем создаёт независимое групповое разбиение. Эта функция лишь безопасно
    извлекает нужные поля и никогда не передаёт текст ассистента в модель.
    """
    resolved = archive_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Не найден архив датасета: {resolved}")
    for expected_split, entry_name in CHAT_SPLIT_ENTRIES.items():
        for line_number, raw_line in _entry_lines(resolved, entry_name):
            payload = _parse_json_record(raw_line, entry_name, line_number)
            _require_fields(payload, CHAT_REQUIRED_FIELDS, entry_name, line_number)
            split = str(payload["split"]).strip()
            if split != expected_split:
                raise ChatDatasetAuditError(
                    f"{entry_name}:{line_number}: ожидается split={expected_split!r}."
                )
            identifier = str(payload["id"]).strip()
            category = str(payload["category"]).strip()
            subtype = str(payload["subtype"]).strip()
            user = str(payload["user"])
            if not identifier or not category or not subtype or not user.strip():
                raise ChatDatasetAuditError(
                    f"{entry_name}:{line_number}: обязательные поля не могут быть пустыми."
                )
            yield ConversationPolicyExample(
                identifier=identifier,
                category=category,
                subtype=subtype,
                should_use_web=bool(payload["should_use_web"]),
                user=user,
                split=split,
            )


def _audit_web_summary(archive_path: Path, entry_name: str) -> dict[str, object]:
    """Проверяет вспомогательные примеры краткого ответа по веб-контексту."""
    splits: Counter[str] = Counter()
    risk_signals: Counter[str] = Counter()
    field_lengths: Counter[str] = Counter()
    identifiers: set[str] = set()
    duplicate_ids = 0
    records = 0
    for line_number, raw_line in _entry_lines(archive_path, entry_name):
        payload = _parse_json_record(raw_line, entry_name, line_number)
        _require_fields(payload, WEB_SUMMARY_REQUIRED_FIELDS, entry_name, line_number)
        identifier = str(payload["id"]).strip()
        if not identifier or not str(payload["user"]).strip() or not str(payload["assistant"]).strip():
            raise ChatDatasetAuditError(
                f"{entry_name}:{line_number}: id, user и assistant не могут быть пустыми."
            )
        records += 1
        if identifier in identifiers:
            duplicate_ids += 1
        identifiers.add(identifier)
        splits[str(payload["split"])] += 1
        for field in ("user", "assistant", "retrieved_web_context", "rule"):
            field_lengths[field] += len(str(payload[field]))
        risk_signals.update(
            _text_signals(payload, ("user", "assistant", "retrieved_web_context", "rule"))
        )
    if not records:
        raise ChatDatasetAuditError(f"{entry_name}: набор web-summary пуст.")
    return {
        "entry": entry_name,
        "records": records,
        "unique_ids": len(identifiers),
        "duplicate_ids": duplicate_ids,
        "splits": dict(sorted(splits.items())),
        "average_lengths": {
            field: round(total / records, 2)
            for field, total in sorted(field_lengths.items())
        },
        "risk_signals": dict(sorted(risk_signals.items())),
    }


def _container_inventory(archive_path: Path) -> dict[str, object]:
    """Проверяет ZIP-контейнер и возвращает только метаданные его файлов."""
    archive_size = archive_path.stat().st_size
    if archive_size < 1 or archive_size > MAX_ARCHIVE_BYTES:
        raise ChatDatasetAuditError("Размер архива выходит за безопасный лимит.")
    with zipfile.ZipFile(archive_path) as zip_file:
        broken_entry = zip_file.testzip()
        if broken_entry is not None:
            raise ChatDatasetAuditError(
                f"ZIP-контейнер повреждён: контрольная сумма не совпала у {broken_entry!r}."
            )
        entries = []
        suspicious_paths = 0
        for entry in zip_file.infolist():
            path = entry.filename.replace("\\", "/")
            if path.startswith("/") or ":" in path or ".." in path.split("/"):
                suspicious_paths += 1
            entries.append(
                {
                    "name": path,
                    "container_bytes": entry.file_size,
                    "compressed_bytes": entry.compress_size,
                }
            )
        return {
            "archive_path": str(archive_path),
            "archive_bytes": archive_size,
            "archive_sha256": file_sha256(archive_path),
            "zip_crc_verified": True,
            "entry_count": len(entries),
            "suspicious_paths": suspicious_paths,
            "entries": sorted(entries, key=lambda item: item["name"]),
        }


def audit_chat_dataset_archive(archive_path: Path) -> dict[str, object]:
    """Проводит полный детерминированный аудит без распаковки на диск."""
    resolved = archive_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Не найден архив датасета: {resolved}")
    inventory = _container_inventory(resolved)
    entry_names = {entry["name"] for entry in inventory["entries"]}
    required_entries = set(CHAT_SPLIT_ENTRIES.values()) | {
        ROUTER_ENTRY,
        WEB_SUMMARY_ENTRY,
        SAMPLE_ENTRY,
    }
    missing_entries = sorted(required_entries - entry_names)
    if missing_entries:
        raise ChatDatasetAuditError(
            f"Архив не содержит обязательные части: {missing_entries}."
        )

    known_ids: dict[str, str] = {}
    known_prompts: dict[str, tuple[str, tuple[object, ...]]] = {}
    chat_splits = {
        split: _audit_chat_split(
            resolved,
            split,
            entry_name,
            known_ids,
            known_prompts,
        )
        for split, entry_name in CHAT_SPLIT_ENTRIES.items()
    }
    router = _audit_router(resolved, ROUTER_ENTRY)
    web_summary = _audit_web_summary(resolved, WEB_SUMMARY_ENTRY)

    # ``sample_500`` проверяется только как схема: он не становится ещё одним
    # источником обучения и не может продублировать канонические split.
    sample_records = 0
    for line_number, raw_line in _entry_lines(resolved, SAMPLE_ENTRY):
        payload = _parse_json_record(raw_line, SAMPLE_ENTRY, line_number)
        _require_fields(payload, CHAT_REQUIRED_FIELDS, SAMPLE_ENTRY, line_number)
        sample_records += 1
    if not sample_records:
        raise ChatDatasetAuditError("sample_500.jsonl пуст.")

    chat_generation_blockers = []
    for split, report in chat_splits.items():
        if report["repeated_prompts"]:
            chat_generation_blockers.append(
                f"{split}: повторяющиеся пары context+user ({report['repeated_prompts']})"
            )
        if report["conflicting_prompt_targets"] or report["cross_split_prompt_conflicts"]:
            chat_generation_blockers.append(
                f"{split}: конфликтующие assistant-цели для одинакового вопроса"
            )
        if report["cross_split_identifier_records"]:
            chat_generation_blockers.append(f"{split}: id пересекаются между split")
    router_blockers = []
    if router["duplicate_ids"] or router["conflicting_user_labels"]:
        router_blockers.append("internet-router: дубли или конфликтующие метки")
    if web_summary["duplicate_ids"]:
        chat_generation_blockers.append("web-summary: повторяющиеся id")

    router_labels = set(router["labels"])
    router_ready = (
        not router_blockers
        and len(router_labels) == 2
        and all(router["splits"].get(split, 0) > 0 for split in ("train", "validation", "test"))
    )
    return {
        "format_version": 1,
        "trust_boundary": (
            "Все строки архива, включая поля system и ответы, проверялись только как "
            "данные и не исполнялись как инструкции."
        ),
        "container": inventory,
        "chat_splits": chat_splits,
        "internet_router": router,
        "web_summary": web_summary,
        "schema_only_sample_records": sample_records,
        "chat_generation_blockers": chat_generation_blockers,
        "router_blockers": router_blockers,
        "training_decision": {
            "internet_router_eligible": router_ready,
            "chat_generation_eligible": False,
            "web_summary_generation_eligible": False,
            "reason": (
                "Текущая собственная модель надёжно работает как маршрутизатор и "
                "каталог проверенных ответов. Свободное обучение на сотнях тысяч "
                "разных assistant-строк без отдельной архитектуры генерации может "
                "снова вызвать случайные символы и не будет подключено к активному чату."
            ),
        },
    }


def audit_markdown(summary: dict[str, object]) -> str:
    """Создаёт отчёт без публикации исходных сообщений из архива."""
    container = summary["container"]
    chat_splits = summary["chat_splits"]
    router = summary["internet_router"]
    web_summary = summary["web_summary"]
    decision = summary["training_decision"]
    lines = [
        "# Аудит Chat AI Dataset 500000",
        "",
        "Этот отчёт не выводит реплики из датасета. Любые поля архива, в том числе "
        "`system`, рассматриваются исключительно как недоверенные данные и никогда "
        "не исполняются как команды проекта.",
        "",
        "## Контейнер",
        "",
        f"- Архив: `{container['archive_path']}`.",
        f"- Размер: {container['archive_bytes']} байт.",
        f"- SHA-256: `{container['archive_sha256']}`.",
        f"- ZIP CRC проверен: {'да' if container['zip_crc_verified'] else 'нет'}.",
        f"- Файлов: {container['entry_count']}; подозрительных путей: {container['suspicious_paths']}.",
        "",
        "## Канонические диалоговые split",
        "",
        "Файл `chat_ai_dataset_500k_all.jsonl.gz` не используется: он объединяет "
        "данные и сделал бы оценку нечестной. В работу берутся только исходные "
        "`train`, `validation`, `test`.",
        "",
        "| Split | Записей | Уникальных id | Дубли | Конфликты | Пересечения prompt с другими split | use_web=true | use_web=false |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for split in ("train", "validation", "test"):
        row = chat_splits[split]
        web_counts = row["should_use_web"]
        lines.append(
            f"| {split} | {row['records']} | {row['unique_ids']} | "
            f"{row['duplicate_ids']} | "
            f"{row['conflicting_prompt_targets'] + row['cross_split_prompt_conflicts']} | "
            f"{row['cross_split_prompt_records']} | {web_counts.get('true', 0)} | "
            f"{web_counts.get('false', 0)} |"
        )
    lines += ["", "### Категории", ""]
    for split in ("train", "validation", "test"):
        categories = chat_splits[split]["categories"]
        formatted = ", ".join(f"`{name}` — {count}" for name, count in categories.items())
        lines.append(f"- {split}: {formatted}.")
    lines += ["", "### Сигналы качества", ""]
    for split in ("train", "validation", "test"):
        row = chat_splits[split]
        signals = row["risk_signals"] or {"нет": 0}
        lines.append(
            f"- {split}: уникальных system-текстов — {row['unique_system_texts']}; "
            f"сигналы, требующие фильтра при будущей генерации — {dict(signals)}."
        )
    lines += [
        "",
        "## Набор решений об интернете",
        "",
        f"- Записей: {router['records']}; уникальных id: {router['unique_ids']}.",
        f"- Метки: {router['labels']}.",
        f"- Split: {router['splits']}.",
        f"- Дубли id: {router['duplicate_ids']}; конфликтующие метки одинакового вопроса: {router['conflicting_user_labels']}.",
        "",
        "## Примеры ответа по веб-контексту",
        "",
        f"- Записей: {web_summary['records']}; уникальных id: {web_summary['unique_ids']}.",
        f"- Split: {web_summary['splits']}.",
        f"- Дубли id: {web_summary['duplicate_ids']}.",
        "",
        "## Решение об использовании",
        "",
        f"- Отдельный обучаемый интернет-маршрутизатор: {'можно обучать' if decision['internet_router_eligible'] else 'нельзя обучать до исправления данных'}.",
        "- Свободная генерация из диалоговых и web-summary ответов: не подключается к активному чату автоматически.",
        f"- Причина: {decision['reason']}",
        "",
    ]
    if summary["chat_generation_blockers"]:
        lines += ["## Ограничения для свободной генерации", ""]
        lines.extend(f"- {item}." for item in summary["chat_generation_blockers"])
        lines.append("")
    if summary["router_blockers"]:
        lines += ["## Блокирующие проблемы маршрутизатора", ""]
        lines.extend(f"- {item}." for item in summary["router_blockers"])
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    """Запускает аудит и сохраняет машинный и человекочитаемый отчёты."""
    parser = argparse.ArgumentParser(description="Аудит Chat AI Dataset 500000")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=ROOT / "CHAT_AI_500K_DATASET_AUDIT.md",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=ROOT / "evaluation" / "results" / "chat-ai-500k-audit" / "chat_ai_500k_audit.json",
    )
    args = parser.parse_args()
    try:
        summary = audit_chat_dataset_archive(args.archive)
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(audit_markdown(summary), encoding="utf-8")
        args.json_output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary["training_decision"], ensure_ascii=False, indent=2))
    except (FileNotFoundError, OSError, ChatDatasetAuditError, zipfile.BadZipFile) as error:
        raise SystemExit(f"Аудит Chat AI Dataset остановлен: {error}") from error


if __name__ == "__main__":
    main()
