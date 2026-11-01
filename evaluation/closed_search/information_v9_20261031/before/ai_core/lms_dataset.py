"""Строгая загрузка и аудит пользовательского LMS-датасета.

Тексты из датасета считаются только обучающими данными. В частности, поле
``system`` не является инструкцией для кода, который запускает этот модуль.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterator

from .reliable_qa import ROOT, normalize


LMS_DATA_DIR = ROOT / "training_data" / "lms_60000"
SPLITS = ("train", "validation", "test")
SPLIT_FILES = {
    "train": LMS_DATA_DIR / "lms_ai_dataset_train.jsonl",
    "validation": LMS_DATA_DIR / "lms_ai_dataset_validation.jsonl",
    "test": LMS_DATA_DIR / "lms_ai_dataset_test.jsonl",
}
TASK_TYPES = frozenset(
    {
        "admin_escalation",
        "clarify_ambiguity",
        "context_summary",
        "direct_context_answer",
        "missing_context_no_web",
        "prompt_injection_resistance",
        "site_navigation",
        "source_attribution",
        "source_conflict",
        "web_fallback_general",
    }
)
REQUIRED_FIELDS = frozenset(
    {
        "id",
        "task_type",
        "system",
        "context",
        "user",
        "assistant",
        "site_answer_available",
        "requires_admin",
        "web_allowed",
        "expected_behavior",
        "risk_level",
        "split",
    }
)


@dataclass(frozen=True)
class LmsExample:
    """Одна строго проверенная строка исходного JSONL."""

    identifier: str
    task_type: str
    system: str
    context: str
    user: str
    assistant: str
    site_answer_available: bool
    requires_admin: bool
    web_allowed: bool
    expected_behavior: str
    risk_level: str
    split: str

    @property
    def question_with_context(self) -> str:
        """Явно размечает части ввода, чтобы не смешивать роль текста."""
        return f"Вопрос: {self.user}\nКонтекст: {self.context}"


def _source_path(split: str) -> Path:
    if split not in SPLIT_FILES:
        raise ValueError(f"Неизвестное разбиение LMS-датасета: {split}")
    path = SPLIT_FILES[split]
    if not path.is_file():
        raise FileNotFoundError(f"Не найден LMS-файл: {path}")
    return path


def _as_nonempty_string(payload: dict, field: str, path: Path, line_number: int) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path.name}:{line_number}: поле {field!r} должно быть непустой строкой.")
    return value.strip()


def _parse_record(payload: object, expected_split: str, path: Path, line_number: int) -> LmsExample:
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name}:{line_number}: JSONL-строка должна содержать объект.")
    missing = REQUIRED_FIELDS - payload.keys()
    if missing:
        raise ValueError(f"{path.name}:{line_number}: отсутствуют поля {sorted(missing)}.")
    task_type = _as_nonempty_string(payload, "task_type", path, line_number)
    if task_type not in TASK_TYPES:
        raise ValueError(f"{path.name}:{line_number}: неизвестный task_type {task_type!r}.")
    split = _as_nonempty_string(payload, "split", path, line_number)
    if split != expected_split:
        raise ValueError(
            f"{path.name}:{line_number}: ожидается split={expected_split!r}, получен {split!r}."
        )
    bool_fields = ("site_answer_available", "requires_admin", "web_allowed")
    for field in bool_fields:
        if not isinstance(payload[field], bool):
            raise ValueError(f"{path.name}:{line_number}: поле {field!r} должно быть bool.")
    return LmsExample(
        identifier=_as_nonempty_string(payload, "id", path, line_number),
        task_type=task_type,
        system=_as_nonempty_string(payload, "system", path, line_number),
        context=_as_nonempty_string(payload, "context", path, line_number),
        user=_as_nonempty_string(payload, "user", path, line_number),
        assistant=_as_nonempty_string(payload, "assistant", path, line_number),
        site_answer_available=payload["site_answer_available"],
        requires_admin=payload["requires_admin"],
        web_allowed=payload["web_allowed"],
        expected_behavior=_as_nonempty_string(payload, "expected_behavior", path, line_number),
        risk_level=_as_nonempty_string(payload, "risk_level", path, line_number),
        split=split,
    )


def iter_examples(split: str) -> Iterator[LmsExample]:
    """Читает JSONL построчно и останавливается на первой повреждённой записи."""
    path = _source_path(split)
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                raise ValueError(f"{path.name}:{line_number}: пустые строки в датасете запрещены.")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path.name}:{line_number}: некорректный JSON: {error.msg}.") from error
            yield _parse_record(payload, split, path, line_number)


def load_examples(split: str) -> list[LmsExample]:
    """Возвращает разбиение после полной проверки его структуры."""
    return list(iter_examples(split))


def context_key(example: LmsExample) -> str:
    """Стабильный ключ фактов контекста без хранения текста в служебных индексах."""
    return _stable_key(example.context)


def context_user_key(example: LmsExample) -> str:
    """Стабильный ключ одной обучающей пары «контекст + вопрос»."""
    return _stable_key(example.context, example.user)


def deduplicate_examples(examples: list[LmsExample]) -> tuple[list[LmsExample], int]:
    """Убирает безопасные точные дубли и останавливается на конфликте метаданных."""
    unique: list[LmsExample] = []
    signatures: dict[str, tuple] = {}
    duplicates = 0
    for example in examples:
        key = context_user_key(example)
        signature = (
            example.task_type,
            normalize(example.assistant),
            example.site_answer_available,
            example.requires_admin,
            example.web_allowed,
            example.expected_behavior,
            example.risk_level,
        )
        if key in signatures:
            if signatures[key] != signature:
                raise ValueError(
                    "Одна пара context+user содержит конфликтующие цели обучения: "
                    f"{example.identifier}."
                )
            duplicates += 1
            continue
        signatures[key] = signature
        unique.append(example)
    return unique, duplicates


def _training_signature(example: LmsExample) -> tuple:
    """Поля, которые не имеют права расходиться у точной обучающей пары."""
    return (
        example.task_type,
        normalize(example.assistant),
        example.site_answer_available,
        example.requires_admin,
        example.web_allowed,
        example.expected_behavior,
        example.risk_level,
    )


def build_context_grouped_splits(seed: int = 42) -> tuple[dict[str, list[LmsExample]], dict]:
    """Строит новые честные split, не позволяя одному контексту попасть в два.

    В приложенном архиве исходные train/validation/test пересекаются точными
    парами и контекстами. Поэтому функция объединяет их как единый источник,
    удаляет точные дубли, затем стабильно распределяет *группы контекстов* в
    пропорции 80/10/10. Это не меняет исходные файлы и исключает утечку фактов.
    """
    groups: dict[str, list[LmsExample]] = {}
    signatures: dict[str, tuple] = {}
    raw_records = 0
    duplicate_records = 0
    for source_split in SPLITS:
        for example in iter_examples(source_split):
            raw_records += 1
            pair = context_user_key(example)
            signature = _training_signature(example)
            if pair in signatures:
                if signatures[pair] != signature:
                    raise ValueError(
                        "Точная пара context+user содержит конфликтующие цели между "
                        f"исходными split: {example.identifier}."
                    )
                duplicate_records += 1
                continue
            signatures[pair] = signature
            groups.setdefault(context_key(example), []).append(example)
    if not groups:
        raise ValueError("LMS-датасет не содержит контекстных групп.")
    assigned: dict[str, list[LmsExample]] = {split: [] for split in SPLITS}
    for key in sorted(groups):
        digest = hashlib.sha256(f"{seed}:{key}".encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:8], "big") % 100
        split = "train" if bucket < 80 else "validation" if bucket < 90 else "test"
        assigned[split].extend(replace(example, split=split) for example in groups[key])
    summary = {
        "seed": seed,
        "raw_records": raw_records,
        "duplicate_records_removed": duplicate_records,
        "unique_context_user_pairs": len(signatures),
        "unique_context_groups": len(groups),
        "splits": {},
    }
    for split, rows in assigned.items():
        if not rows:
            raise ValueError(f"Новый LMS split {split!r} оказался пустым.")
        task_counts = Counter(row.task_type for row in rows)
        missing = TASK_TYPES - task_counts.keys()
        if missing:
            raise ValueError(
                f"Новый LMS split {split!r} потерял типы задач: {sorted(missing)}."
            )
        summary["splits"][split] = {
            "records": len(rows),
            "context_groups": len({context_key(row) for row in rows}),
            "task_types": dict(sorted(task_counts.items())),
        }
    return assigned, summary


def build_leakage_free_splits(strict_context: bool = False) -> tuple[dict[str, list[LmsExample]], dict]:
    """Создаёт непересекающиеся split без изменения исходных JSONL-файлов.

    Сначала удаляются точные дубли внутри split. Для validation и test исключаются
    уже встречавшиеся пары ``context+user``. В строгом режиме также исключается
    повтор самого контекста, чтобы модель не получала готовые факты будущей оценки.
    По умолчанию строгий режим выключен: данный синтетический набор повторяет
    контексты между разными вопросами, а строгая очистка лишает validation
    большинства классов. Точная пара context+user при этом всегда исключается.
    """
    retained: dict[str, list[LmsExample]] = {}
    cleaning: dict[str, dict] = {}
    prior_pairs: set[str] = set()
    prior_contexts: set[str] = set()
    for split in SPLITS:
        raw = load_examples(split)
        unique, internal_duplicates = deduplicate_examples(raw)
        selected: list[LmsExample] = []
        pair_excluded = 0
        context_excluded = 0
        for example in unique:
            pair = context_user_key(example)
            context = context_key(example)
            if pair in prior_pairs:
                pair_excluded += 1
                continue
            if strict_context and context in prior_contexts:
                context_excluded += 1
                continue
            selected.append(example)
        if not selected:
            raise ValueError(f"После очистки LMS split {split!r} оказался пустым.")
        missing_tasks = TASK_TYPES - {item.task_type for item in selected}
        if missing_tasks:
            raise ValueError(
                f"После очистки LMS split {split!r} потерял типы задач: {sorted(missing_tasks)}."
            )
        retained[split] = selected
        cleaning[split] = {
            "raw_records": len(raw),
            "internal_duplicate_records_removed": internal_duplicates,
            "cross_split_pair_records_excluded": pair_excluded,
            "cross_split_context_records_excluded": context_excluded,
            "retained_records": len(selected),
            "task_types": dict(sorted(Counter(item.task_type for item in selected).items())),
        }
        prior_pairs.update(context_user_key(item) for item in selected)
        prior_contexts.update(context_key(item) for item in selected)
    return retained, cleaning


def file_fingerprint(path: Path) -> str:
    """Вычисляет SHA-256 потоком, не загружая исходный JSONL в память."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_key(*values: str) -> str:
    normalized = "\u0000".join(normalize(value) for value in values)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def audit_dataset() -> dict:
    """Проверяет схему, утечки между split и распределения без обучения модели."""
    split_summaries: dict[str, dict] = {}
    identifiers: dict[str, set[str]] = {}
    context_keys: dict[str, set[str]] = {}
    prompt_context_keys: dict[str, set[str]] = {}
    answer_keys: dict[str, set[str]] = {}
    system_keys: dict[str, set[str]] = {}
    for split in SPLITS:
        records = iter_examples(split)
        ids: set[str] = set()
        contexts: set[str] = set()
        prompt_contexts: set[str] = set()
        prompt_context_signatures: dict[str, tuple] = {}
        answers: set[str] = set()
        systems: set[str] = set()
        task_counts: Counter[str] = Counter()
        behavior_counts: Counter[str] = Counter()
        risk_counts: Counter[str] = Counter()
        boolean_counts: Counter[str] = Counter()
        lengths: Counter[str] = Counter()
        duplicate_ids = 0
        duplicate_prompt_contexts = 0
        conflicting_prompt_contexts: set[str] = set()
        count = 0
        for row in records:
            count += 1
            if row.identifier in ids:
                duplicate_ids += 1
            ids.add(row.identifier)
            context_key = _stable_key(row.context)
            prompt_context_key = _stable_key(row.context, row.user)
            signature = (
                row.task_type,
                normalize(row.assistant),
                row.site_answer_available,
                row.requires_admin,
                row.web_allowed,
                row.expected_behavior,
                row.risk_level,
            )
            if prompt_context_key in prompt_contexts:
                duplicate_prompt_contexts += 1
                if prompt_context_signatures[prompt_context_key] != signature:
                    conflicting_prompt_contexts.add(prompt_context_key)
            else:
                prompt_context_signatures[prompt_context_key] = signature
            contexts.add(context_key)
            prompt_contexts.add(prompt_context_key)
            answers.add(_stable_key(row.assistant))
            systems.add(_stable_key(row.system))
            task_counts[row.task_type] += 1
            behavior_counts[row.expected_behavior] += 1
            risk_counts[row.risk_level] += 1
            boolean_counts[f"site_answer_available={row.site_answer_available}"] += 1
            boolean_counts[f"requires_admin={row.requires_admin}"] += 1
            boolean_counts[f"web_allowed={row.web_allowed}"] += 1
            lengths["context_chars"] += len(row.context)
            lengths["user_chars"] += len(row.user)
            lengths["assistant_chars"] += len(row.assistant)
        if not count:
            raise ValueError(f"LMS-разбиение {split!r} пустое.")
        if duplicate_ids:
            raise ValueError(
                f"LMS-разбиение {split!r} содержит повторы идентификаторов: {duplicate_ids}."
            )
        identifiers[split] = ids
        context_keys[split] = contexts
        prompt_context_keys[split] = prompt_contexts
        answer_keys[split] = answers
        system_keys[split] = systems
        split_summaries[split] = {
            "records": count,
            "file_sha256": file_fingerprint(_source_path(split)),
            "task_types": dict(sorted(task_counts.items())),
            "expected_behaviors": dict(sorted(behavior_counts.items())),
            "risk_levels": dict(sorted(risk_counts.items())),
            "booleans": dict(sorted(boolean_counts.items())),
            "unique_contexts": len(contexts),
            "unique_context_user_pairs": len(prompt_contexts),
            "duplicate_context_user_records": duplicate_prompt_contexts,
            "conflicting_context_user_pairs": len(conflicting_prompt_contexts),
            "unique_answers": len(answers),
            "unique_system_texts": len(systems),
            "average_lengths": {
                key: round(value / count, 2) for key, value in sorted(lengths.items())
            },
        }
    overlaps = {}
    for earlier, later in (("train", "validation"), ("train", "test"), ("validation", "test")):
        label = f"{earlier}_vs_{later}"
        overlaps[label] = {
            "identifier_overlap": len(identifiers[earlier] & identifiers[later]),
            "context_overlap": len(context_keys[earlier] & context_keys[later]),
            "context_user_overlap": len(prompt_context_keys[earlier] & prompt_context_keys[later]),
            "answer_overlap": len(answer_keys[earlier] & answer_keys[later]),
            "system_text_overlap": len(system_keys[earlier] & system_keys[later]),
        }
    return {"splits": split_summaries, "overlaps": overlaps}


def audit_markdown(summary: dict) -> str:
    """Формирует отчёт без вывода самих обучающих реплик или system-текстов."""
    lines = [
        "# Аудит LMS AI Dataset 60000",
        "",
        "Датасет рассматривается как недоверенный обучающий материал. Поле `system` "
        "проверяется как строка, но не исполняется и не становится инструкцией кода.",
        "",
        "## Разбиения",
        "",
        "| Split | Записей | Уникальных контекстов | Уникальных пар context+user | Дубликатов пар | Конфликтов пар | Уникальных ответов | SHA-256 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for split in SPLITS:
        row = summary["splits"][split]
        lines.append(
            f"| {split} | {row['records']} | {row['unique_contexts']} | "
            f"{row['unique_context_user_pairs']} | {row['duplicate_context_user_records']} | "
            f"{row['conflicting_context_user_pairs']} | {row['unique_answers']} | "
            f"{row['file_sha256']} |"
        )
    derived = summary.get("context_grouped_split")
    if derived:
        lines += [
            "",
            "## Производственное context-grouped разбиение",
            "",
            "Исходные split содержат повторяющиеся контексты, поэтому для обучения и "
            "зачётной оценки создано отдельное детерминированное разбиение группами "
            "контекстов с seed `42`.",
            "",
            "| Показатель | Значение |",
            "|---|---:|",
            f"| Исходных записей | {derived['raw_records']} |",
            f"| Удалено точных дублей | {derived['duplicate_records_removed']} |",
            f"| Уникальных пар context+user | {derived['unique_context_user_pairs']} |",
            f"| Уникальных групп контекстов | {derived['unique_context_groups']} |",
            "",
            "| Новый split | Записей | Групп контекстов |",
            "|---|---:|---:|",
        ]
        for split in SPLITS:
            row = derived["splits"][split]
            lines.append(f"| {split} | {row['records']} | {row['context_groups']} |")
    lines += ["", "## Распределение задач", ""]
    for split in SPLITS:
        lines.append(f"### {split}")
        lines.append("")
        for task_type, count in summary["splits"][split]["task_types"].items():
            lines.append(f"- `{task_type}`: {count}")
        lines.append("")
    lines += [
        "## Пересечения разбиений",
        "",
        "| Пара split | Одинаковые id | Одинаковый context | Одинаковая пара context+user | Одинаковые ответы | Одинаковый system-текст |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, row in summary["overlaps"].items():
        lines.append(
            f"| {name} | {row['identifier_overlap']} | {row['context_overlap']} | "
            f"{row['context_user_overlap']} | {row['answer_overlap']} | {row['system_text_overlap']} |"
        )
    lines += [
        "",
        "Повтор отдельных ответов или system-текста ожидаем для шаблонного синтетического набора. "
        "Точные дубли внутри split удаляются перед обучением. Конфликтные пары и точные "
        "пересечения context+user между split не допускаются в зачётной оценке.",
        "",
    ]
    return "\n".join(lines)


def example_to_dict(example: LmsExample) -> dict:
    """Удобное сериализуемое представление для отчётов и тестов."""
    return asdict(example)
