"""Безопасная подготовка уникальных реплик для conversation-mind v2.

Chat AI Dataset содержит сотни тысяч строк, но многие из них являются
повторениями одних и тех же user-реплик. Этот модуль не использует assistant,
system, context, URL или готовые ответы как цель генерации. Он извлекает только
уникальный текст пользователя и стабильную метку типа общения.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .chat_dataset import (
    CHAT_REQUIRED_FIELDS,
    CHAT_SPLIT_ENTRIES,
    ChatDatasetAuditError,
    _entry_lines,
    _parse_json_record,
    _require_fields,
    file_sha256,
)
from .internet_router import normalize_route_text


ROOT = Path(__file__).resolve().parent.parent
FORMAT_VERSION = 1
CONVERSATION_LABELS = (
    "choice_help",
    "clarify",
    "concise_help",
    "context_continuity",
    "correction",
    "followup",
    "greeting",
    "polite_disagreement",
    "preference_following",
    "scope_limit",
    "simple_explanation",
    "uncertainty",
)
# Редкие исходные subtype не подходят как независимые классы: например,
# greeting и followup встречаются в уникальных данных лишь несколько раз.
# Поэтому сеть учится на устойчивых планах ответа, а точный явный случай
# уточняется безопасными правилами в conversation_mind.py.
CONVERSATION_PLANS = (
    "CLARIFY",
    "GUIDE",
    "PREFERENCE",
    "RELATIONSHIP",
)
SUBTYPE_TO_PLAN = {
    "clarify": "CLARIFY",
    "uncertainty": "CLARIFY",
    "scope_limit": "CLARIFY",
    "choice_help": "GUIDE",
    "concise_help": "GUIDE",
    "simple_explanation": "GUIDE",
    "preference_following": "PREFERENCE",
    "context_continuity": "RELATIONSHIP",
    "correction": "RELATIONSHIP",
    "followup": "RELATIONSHIP",
    "greeting": "RELATIONSHIP",
    "polite_disagreement": "RELATIONSHIP",
}
MAX_USER_LENGTH = 500
_INSTRUCTION_LIKE = (
    re.compile(r"ignore\s+(?:all\s+)?previous", flags=re.IGNORECASE),
    re.compile(r"ignore\s+instructions", flags=re.IGNORECASE),
    re.compile(r"игнорируй\s+(?:все\s+)?предыдущ", flags=re.IGNORECASE),
    re.compile(r"system\s+prompt", flags=re.IGNORECASE),
)
_URL_LIKE = re.compile(r"(?:https?://|www\.)", flags=re.IGNORECASE)
_SECRET_LIKE = re.compile(
    r"\b(?:api[_ -]?key|secret|password|парол[ья]|токен)\s*[:=]\s*\S+",
    flags=re.IGNORECASE,
)


class ConversationMindDatasetError(ValueError):
    """Останавливает подготовку при нарушении границ обучающих данных."""


@dataclass(frozen=True)
class ConversationMindExample:
    """Одна уникальная user-реплика с меткой разговорного действия."""

    identifier: str
    user: str
    subtype: str
    plan: str
    source_split: str


def _fingerprint(value: str) -> str:
    """Строит необратимый ключ для дедупликации без записи текста в отчёт."""
    return hashlib.sha256(normalize_route_text(value).encode("utf-8")).hexdigest()


def _is_safe_user_text(value: str) -> str | None:
    """Возвращает причину исключения или ``None`` для пригодной реплики."""
    clean = value.strip()
    if not 2 <= len(clean) <= MAX_USER_LENGTH:
        return "user_length"
    if _URL_LIKE.search(clean):
        return "url_like"
    if _SECRET_LIKE.search(clean):
        return "credential_like"
    if any(pattern.search(clean) for pattern in _INSTRUCTION_LIKE):
        return "instruction_like"
    if not normalize_route_text(clean):
        return "empty_after_normalization"
    return None


def build_conversation_mind_examples(
    archive_path: Path,
) -> tuple[list[ConversationMindExample], dict[str, Any]]:
    """Извлекает уникальные policy-примеры из всех canonical split.

    Изначальные train/validation/test намеренно не применяются как готовое
    разбиение: они пересекаются по формулировкам. Позже trainer выполнит своё
    детерминированное разбиение по уникальной реплике без утечки.
    """
    resolved = archive_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Не найден Chat AI Dataset: {resolved}")
    unique: dict[str, ConversationMindExample] = {}
    labels_by_prompt: dict[str, str] = {}
    conflicts: set[str] = set()
    source_counts: Counter[str] = Counter()
    subtype_counts: Counter[str] = Counter()
    plan_counts: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    duplicate_rows = 0
    source_records = 0

    for expected_split, entry_name in CHAT_SPLIT_ENTRIES.items():
        for line_number, raw_line in _entry_lines(resolved, entry_name):
            payload = _parse_json_record(raw_line, entry_name, line_number)
            _require_fields(payload, CHAT_REQUIRED_FIELDS, entry_name, line_number)
            if payload["split"] != expected_split:
                raise ConversationMindDatasetError(
                    f"{entry_name}:{line_number}: split не совпадает с именем файла."
                )
            if payload["category"] != "communication":
                continue
            source_records += 1
            subtype = str(payload["subtype"]).strip()
            user = str(payload["user"]).strip()
            if subtype not in CONVERSATION_LABELS:
                rejected["unknown_subtype"] += 1
                continue
            if payload["should_use_web"]:
                rejected["web_marked"] += 1
                continue
            # У разговорных строк context должен быть пустым. Не смешиваем
            # скрытый или неявный контекст с текущей репликой пользователя.
            if str(payload["context"]).strip():
                rejected["nonempty_context"] += 1
                continue
            reason = _is_safe_user_text(user)
            if reason is not None:
                rejected[reason] += 1
                continue
            key = _fingerprint(user)
            existing_label = labels_by_prompt.get(key)
            if existing_label is not None:
                if existing_label != subtype:
                    conflicts.add(key)
                else:
                    duplicate_rows += 1
                continue
            labels_by_prompt[key] = subtype
            unique[key] = ConversationMindExample(
                identifier=str(payload["id"]),
                user=user,
                subtype=subtype,
                plan=SUBTYPE_TO_PLAN[subtype],
                source_split=expected_split,
            )
            source_counts[expected_split] += 1
            subtype_counts[subtype] += 1
            plan_counts[SUBTYPE_TO_PLAN[subtype]] += 1

    for key in conflicts:
        unique.pop(key, None)
    examples = sorted(
        unique.values(),
        key=lambda item: (item.subtype, normalize_route_text(item.user)),
    )
    final_labels = {item.subtype for item in examples}
    missing_labels = sorted(set(CONVERSATION_LABELS) - final_labels)
    if missing_labels:
        raise ConversationMindDatasetError(
            f"После дедупликации не хватает типов общения: {missing_labels}."
        )
    if len(examples) < len(CONVERSATION_LABELS) * 10:
        raise ConversationMindDatasetError("Недостаточно уникальных реплик для conversation-mind.")
    final_counts = Counter(item.subtype for item in examples)
    final_plan_counts = Counter(item.plan for item in examples)
    if set(final_plan_counts) != set(CONVERSATION_PLANS):
        raise ConversationMindDatasetError("Не все устойчивые планы ответа попали в набор.")
    return examples, {
        "format_version": FORMAT_VERSION,
        "archive_path": str(resolved),
        "archive_sha256": file_sha256(resolved),
        "source_communication_records": source_records,
        "source_unique_candidates_by_split": dict(sorted(source_counts.items())),
        "source_unique_candidates_by_subtype": dict(sorted(subtype_counts.items())),
        "duplicate_rows_removed": duplicate_rows,
        "conflicting_prompt_labels_removed": len(conflicts),
        "safe_unique_examples": len(examples),
        "safe_unique_examples_by_subtype": dict(sorted(final_counts.items())),
        "safe_unique_examples_by_plan": dict(sorted(final_plan_counts.items())),
        "rejected": dict(sorted(rejected.items())),
        "safe_input_fields": ["category", "subtype", "should_use_web", "context", "user"],
        "excluded_fields": [
            "assistant",
            "system",
            "retrieved_web_context",
            "search_query",
            "constraints",
        ],
        "source_split_is_not_training_split": True,
    }


def audit_conversation_mind_dataset(archive_path: Path) -> dict[str, Any]:
    """Создаёт машинный отчёт о пригодности данных для policy-модели."""
    examples, report = build_conversation_mind_examples(archive_path)
    report["training_decision"] = {
        "conversation_policy_eligible": True,
        "free_response_generator_eligible": False,
        "policy_reason": (
            "Для сети используются только уникальные user-реплики. Редкие "
            "исходные subtype объединены в четыре устойчивых плана ответа, "
            "а собственное разбиение выполняется после дедупликации."
        ),
        "generator_reason": (
            "Assistant-ответы не используются: в исходном архиве их слишком мало "
            "уникальных и они не являются согласованной целью свободной генерации."
        ),
        "records_exported": len(examples),
    }
    return report


def _markdown(report: dict[str, Any]) -> str:
    """Готовит отчёт без исходных текстов пользователей и ассистента."""
    rows = [
        "# Аудит данных conversation-mind v2",
        "",
        f"SHA-256 архива: `{report['archive_sha256']}`.",
        "",
        "## Дедупликация",
        "",
        f"- Коммуникационных строк в источнике: {report['source_communication_records']}.",
        f"- Удалено повторов той же реплики с той же меткой: {report['duplicate_rows_removed']}.",
        f"- Исключено конфликтных user-реплик: {report['conflicting_prompt_labels_removed']}.",
        f"- Уникальных безопасных примеров policy: {report['safe_unique_examples']}.",
        f"- Устойчивых планов ответа: {len(report['safe_unique_examples_by_plan'])}.",
        "",
        "## Граница использования",
        "",
        "Набор используется только для классификации типа общения. Поля assistant, "
        "system, URL, контекст и готовые ответы не экспортируются. Он не является "
        "датасетом для свободного генератора текста.",
        "",
    ]
    return "\n".join(rows)


def main() -> None:
    """Проводит аудит и сохраняет JSON/Markdown без извлечения ZIP на диск."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evaluation" / "results" / "conversation-mind-v2-data-audit",
    )
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError(f"Каталог отчёта уже существует: {args.output}")
        report = audit_conversation_mind_dataset(args.archive)
        args.output.mkdir(parents=True)
        (args.output / "conversation_mind_data_audit.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.output / "CONVERSATION_MIND_DATA_AUDIT.md").write_text(
            _markdown(report), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (
        FileNotFoundError,
        OSError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        ChatDatasetAuditError,
        ConversationMindDatasetError,
    ) as error:
        raise SystemExit(f"Аудит conversation-mind остановлен: {error}") from error


if __name__ == "__main__":
    main()
