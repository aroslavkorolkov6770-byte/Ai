"""Локальный, metadata-only учёт ручной проверки OASST1 semantic queue.

Скрипт намеренно отделён от staging, promotion и обучения. По явной команде
``--show-id`` он выводит один уже лицензированный исходный диалог только в
локальный терминал. Вердикты сохраняются без реплик: только ID, хэш, решение,
стандартизированные reason codes и время проверки.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from evaluation.oasst1_semantic_review import SOURCE_ID


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data"
RAW_ROOT = DATA_ROOT / "brain_v3" / "raw"
REVIEW_ROOT = DATA_ROOT / "brain_v3" / "reviews"
RESULTS_ROOT = PROJECT_ROOT / "evaluation" / "results"

QUEUE_MODE = "oasst1_semantic_candidate_review_queue"
DECISION_MODE = "oasst1_semantic_human_review_decision"
SUMMARY_MODE = "oasst1_semantic_human_review_summary"
DEFAULT_QUEUE_PATH = (
    RESULTS_ROOT
    / "oasst1-semantic-review-queue-v0.4"
    / "oasst1_semantic_review_queue.json"
)
DEFAULT_SOURCE_PATH = RAW_ROOT / "openassistant-oasst1-apache-v1.jsonl"
DEFAULT_DECISIONS_PATH = REVIEW_ROOT / "oasst1-semantic-review-decisions-v0.1.jsonl"
DEFAULT_SUMMARY_DIRECTORY = RESULTS_ROOT / "oasst1-semantic-human-review-v0.1"

MAX_SOURCE_LINE_BYTES = 1_000_000
MAX_TERMINAL_TURN_CHARACTERS = 3_000
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVIEWER_ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")

Decision = Literal["approved", "rejected"]

# Эти коды не содержат свободного текста и не позволяют случайно записать в
# review ledger имя, адрес или содержание исходного диалога.
APPROVAL_REASON_CODES = frozenset(
    {
        "natural_russian",
        "coherent_follow_up",
        "relevant_response",
        "no_reviewer_detected_risk",
    }
)
REJECTION_REASON_CODES = frozenset(
    {
        "bad_russian",
        "incoherent_follow_up",
        "irrelevant_response",
        "template_like",
        "unsupported_claim",
        "unwanted_link_or_promotion",
        "personal_data_risk",
        "unsafe_or_unhelpful_advice",
        "legacy_identity_or_capability",
        "not_target_semantics",
        "other_quality_failure",
    }
)
REQUIRED_APPROVAL_REASON_CODES = frozenset(
    {
        "natural_russian",
        "coherent_follow_up",
        "relevant_response",
        "no_reviewer_detected_risk",
    }
)
RAW_CONTENT_KEYS = frozenset({"text", "turns", "conversation", "message"})


class OASST1HumanReviewError(ValueError):
    """Останавливает workflow безопасным коротким кодом причины."""


def _safe_path_inside(path: Path, root: Path, error_code: str) -> Path:
    """Разрешает чтение или запись только внутри ожидаемого локального корня."""

    resolved_path = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise OASST1HumanReviewError(error_code) from error
    return resolved_path


def _contains_raw_turn_content(value: Any) -> bool:
    """Проверяет, что metadata-only объект не содержит реплик source-диалога."""

    if isinstance(value, Mapping):
        if RAW_CONTENT_KEYS.intersection(value):
            return True
        return any(_contains_raw_turn_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_raw_turn_content(item) for item in value)
    return False


def _read_json_object(path: Path, error_code: str) -> dict[str, Any]:
    """Читает один JSON object и не выводит его исходное содержимое при ошибке."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OASST1HumanReviewError(error_code) from error
    if not isinstance(payload, dict):
        raise OASST1HumanReviewError(error_code)
    return payload


def _validate_queue_item(item: Any) -> dict[str, Any]:
    """Проверяет одну metadata-only запись очереди перед обращением к raw source."""

    if not isinstance(item, dict) or _contains_raw_turn_content(item):
        raise OASST1HumanReviewError("oasst1_human_review_queue_item_invalid")
    identifier = item.get("id")
    content_sha256 = item.get("content_sha256")
    split = item.get("split")
    turn_count = item.get("turn_count")
    topics = item.get("candidate_topic_labels")
    if not isinstance(identifier, str) or not identifier:
        raise OASST1HumanReviewError("oasst1_human_review_queue_item_invalid")
    if not isinstance(content_sha256, str) or not SHA256_PATTERN.fullmatch(content_sha256):
        raise OASST1HumanReviewError("oasst1_human_review_queue_item_invalid")
    if split not in {"train", "validation", "test"}:
        raise OASST1HumanReviewError("oasst1_human_review_queue_item_invalid")
    if not isinstance(turn_count, int) or turn_count < 4:
        raise OASST1HumanReviewError("oasst1_human_review_queue_item_invalid")
    if not isinstance(topics, list) or not topics or not all(
        isinstance(topic, str) and topic for topic in topics
    ):
        raise OASST1HumanReviewError("oasst1_human_review_queue_item_invalid")
    if item.get("review_status") != "unreviewed":
        raise OASST1HumanReviewError("oasst1_human_review_queue_item_invalid")
    return item


def load_review_queue(
    queue_path: Path = DEFAULT_QUEUE_PATH,
    *,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    """Загружает неизменяемую очередь v0.4 и проверяет только её metadata."""

    safe_queue_path = _safe_path_inside(
        queue_path,
        results_root,
        "oasst1_human_review_queue_outside_results",
    )
    if not safe_queue_path.is_file():
        raise OASST1HumanReviewError("oasst1_human_review_queue_missing")
    queue = _read_json_object(safe_queue_path, "oasst1_human_review_queue_invalid")
    if _contains_raw_turn_content(queue):
        raise OASST1HumanReviewError("oasst1_human_review_queue_not_metadata_only")
    if queue.get("mode") != QUEUE_MODE or queue.get("source_id") != SOURCE_ID:
        raise OASST1HumanReviewError("oasst1_human_review_queue_invalid")
    if queue.get("review_status") != "unreviewed":
        raise OASST1HumanReviewError("oasst1_human_review_queue_invalid")
    items = queue.get("items")
    if not isinstance(items, list) or not items:
        raise OASST1HumanReviewError("oasst1_human_review_queue_invalid")
    validated_items = [_validate_queue_item(item) for item in items]
    identifiers = [item["id"] for item in validated_items]
    if len(identifiers) != len(set(identifiers)):
        raise OASST1HumanReviewError("oasst1_human_review_queue_duplicate_identifier")
    if queue.get("selected_records") != len(validated_items):
        raise OASST1HumanReviewError("oasst1_human_review_queue_invalid")
    return queue


def _queue_item_by_id(queue: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    """Находит ID только среди уже выбранных metadata-only кандидатов."""

    if not isinstance(candidate_id, str) or not candidate_id:
        raise OASST1HumanReviewError("oasst1_human_review_candidate_id_invalid")
    for item in queue["items"]:
        if item["id"] == candidate_id:
            return item
    raise OASST1HumanReviewError("oasst1_human_review_candidate_not_in_queue")


def _validate_source_record(record: Any, queue_item: Mapping[str, Any]) -> dict[str, Any]:
    """Связывает source record с queue по ID, SHA-256, split и числу ходов."""

    if not isinstance(record, dict):
        raise OASST1HumanReviewError("oasst1_human_review_source_record_invalid")
    if record.get("id") != queue_item["id"]:
        raise OASST1HumanReviewError("oasst1_human_review_source_record_invalid")
    if record.get("content_sha256") != queue_item["content_sha256"]:
        raise OASST1HumanReviewError("oasst1_human_review_source_hash_mismatch")
    if record.get("split") != queue_item["split"]:
        raise OASST1HumanReviewError("oasst1_human_review_source_split_mismatch")
    if record.get("language") != "ru" or record.get("record_type") != "dialogue":
        raise OASST1HumanReviewError("oasst1_human_review_source_record_invalid")
    turns = record.get("turns")
    if not isinstance(turns, list) or len(turns) != queue_item["turn_count"]:
        raise OASST1HumanReviewError("oasst1_human_review_source_turn_count_mismatch")
    for turn in turns:
        if (
            not isinstance(turn, dict)
            or turn.get("role") not in {"user", "assistant"}
            or not isinstance(turn.get("text"), str)
            or not turn["text"].strip()
        ):
            raise OASST1HumanReviewError("oasst1_human_review_source_turn_invalid")
    return record


def load_source_records_for_queue(
    queue: Mapping[str, Any],
    source_path: Path = DEFAULT_SOURCE_PATH,
    *,
    raw_root: Path = RAW_ROOT,
) -> dict[str, dict[str, Any]]:
    """Читает нужные исходные записи в память, не создавая новый текстовый файл."""

    safe_source_path = _safe_path_inside(
        source_path,
        raw_root,
        "oasst1_human_review_source_outside_raw",
    )
    if not safe_source_path.is_file():
        raise OASST1HumanReviewError("oasst1_human_review_source_missing")
    expected_items = {item["id"]: item for item in queue["items"]}
    matched_records: dict[str, dict[str, Any]] = {}
    try:
        with safe_source_path.open("r", encoding="utf-8") as source_file:
            for line_number, line in enumerate(source_file, start=1):
                if len(line.encode("utf-8")) > MAX_SOURCE_LINE_BYTES:
                    raise OASST1HumanReviewError("oasst1_human_review_source_line_too_large")
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise OASST1HumanReviewError(
                        "oasst1_human_review_source_json_invalid"
                    ) from error
                identifier = record.get("id") if isinstance(record, dict) else None
                if identifier not in expected_items:
                    continue
                if identifier in matched_records:
                    raise OASST1HumanReviewError("oasst1_human_review_source_duplicate_identifier")
                matched_records[identifier] = _validate_source_record(
                    record,
                    expected_items[identifier],
                )
    except OSError as error:
        raise OASST1HumanReviewError("oasst1_human_review_source_read_failed") from error
    missing_identifiers = sorted(set(expected_items).difference(matched_records))
    if missing_identifiers:
        raise OASST1HumanReviewError("oasst1_human_review_source_candidate_missing")
    return matched_records


def render_candidate_for_terminal(
    queue_item: Mapping[str, Any],
    source_record: Mapping[str, Any],
) -> str:
    """Рендерит один диалог для явного локального просмотра без записи на диск."""

    _validate_source_record(source_record, queue_item)
    lines = [
        f"ID: {queue_item['id']}",
        f"Split: {queue_item['split']}",
        f"Candidate topics: {', '.join(queue_item['candidate_topic_labels'])}",
        "",
    ]
    role_labels = {"user": "Пользователь", "assistant": "Ассистент"}
    for position, turn in enumerate(source_record["turns"], start=1):
        text = turn["text"].strip()
        if len(text) > MAX_TERMINAL_TURN_CHARACTERS:
            text = f"{text[:MAX_TERMINAL_TURN_CHARACTERS]}\n[Реплика сокращена только в выводе]"
        lines.extend((f"{position}. {role_labels[turn['role']]}:", text, ""))
    return "\n".join(lines).rstrip()


def _normalise_reason_codes(
    decision: Decision,
    reason_codes: Iterable[str],
) -> list[str]:
    """Разрешает только короткие фиксированные codes вместо свободного текста."""

    raw_codes = list(reason_codes)
    if not raw_codes or any(not isinstance(code, str) for code in raw_codes):
        raise OASST1HumanReviewError("oasst1_human_review_reason_missing")
    codes = sorted(set(raw_codes))
    allowed_codes = (
        APPROVAL_REASON_CODES if decision == "approved" else REJECTION_REASON_CODES
    )
    if any(code not in allowed_codes for code in codes):
        raise OASST1HumanReviewError("oasst1_human_review_reason_invalid")
    if decision == "approved" and not REQUIRED_APPROVAL_REASON_CODES.issubset(codes):
        raise OASST1HumanReviewError("oasst1_human_review_approval_criteria_missing")
    return codes


def _normalise_reviewer_role(reviewer_role: str) -> str:
    """Не допускает длинную свободную строку reviewer вместо нейтральной роли."""

    if not isinstance(reviewer_role, str) or not REVIEWER_ROLE_PATTERN.fullmatch(
        reviewer_role
    ):
        raise OASST1HumanReviewError("oasst1_human_review_reviewer_role_invalid")
    return reviewer_role


def create_review_decision(
    queue_item: Mapping[str, Any],
    *,
    decision: Decision,
    reason_codes: Iterable[str],
    reviewer_role: str,
    reviewed_at: datetime | None = None,
) -> dict[str, Any]:
    """Создаёт одну проверяемую decision-запись без текста conversation."""

    if decision not in {"approved", "rejected"}:
        raise OASST1HumanReviewError("oasst1_human_review_decision_invalid")
    validated_item = _validate_queue_item(dict(queue_item))
    timestamp = reviewed_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise OASST1HumanReviewError("oasst1_human_review_timestamp_invalid")
    return {
        "format_version": 1,
        "mode": DECISION_MODE,
        "source_id": SOURCE_ID,
        "candidate_id": validated_item["id"],
        "content_sha256": validated_item["content_sha256"],
        "split": validated_item["split"],
        "decision": decision,
        "reason_codes": _normalise_reason_codes(decision, reason_codes),
        "reviewer_role": _normalise_reviewer_role(reviewer_role),
        "reviewed_at": timestamp.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _validate_decision(
    record: Any,
    queue_items: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Проверяет сохранённый decision against immutable queue metadata."""

    if not isinstance(record, dict) or _contains_raw_turn_content(record):
        raise OASST1HumanReviewError("oasst1_human_review_decision_record_invalid")
    candidate_id = record.get("candidate_id")
    if record.get("format_version") != 1 or record.get("mode") != DECISION_MODE:
        raise OASST1HumanReviewError("oasst1_human_review_decision_record_invalid")
    if record.get("source_id") != SOURCE_ID or candidate_id not in queue_items:
        raise OASST1HumanReviewError("oasst1_human_review_decision_record_invalid")
    queue_item = queue_items[candidate_id]
    if (
        record.get("content_sha256") != queue_item["content_sha256"]
        or record.get("split") != queue_item["split"]
    ):
        raise OASST1HumanReviewError("oasst1_human_review_decision_metadata_mismatch")
    decision = record.get("decision")
    if decision not in {"approved", "rejected"}:
        raise OASST1HumanReviewError("oasst1_human_review_decision_invalid")
    _normalise_reason_codes(decision, record.get("reason_codes", []))
    _normalise_reviewer_role(record.get("reviewer_role"))
    reviewed_at = record.get("reviewed_at")
    if not isinstance(reviewed_at, str) or not reviewed_at.endswith("Z"):
        raise OASST1HumanReviewError("oasst1_human_review_timestamp_invalid")
    return record


def load_review_decisions(
    decisions_path: Path,
    queue: Mapping[str, Any],
    *,
    reviews_root: Path = REVIEW_ROOT,
) -> list[dict[str, Any]]:
    """Загружает существующий append-only decision ledger без raw conversation."""

    safe_decisions_path = _safe_path_inside(
        decisions_path,
        reviews_root,
        "oasst1_human_review_decisions_outside_reviews",
    )
    if not safe_decisions_path.exists():
        return []
    if not safe_decisions_path.is_file():
        raise OASST1HumanReviewError("oasst1_human_review_decisions_invalid")
    queue_items = {item["id"]: item for item in queue["items"]}
    decisions: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    try:
        with safe_decisions_path.open("r", encoding="utf-8") as decisions_file:
            for line in decisions_file:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise OASST1HumanReviewError(
                        "oasst1_human_review_decisions_invalid"
                    ) from error
                validated_record = _validate_decision(record, queue_items)
                candidate_id = validated_record["candidate_id"]
                if candidate_id in identifiers:
                    raise OASST1HumanReviewError(
                        "oasst1_human_review_duplicate_decision"
                    )
                identifiers.add(candidate_id)
                decisions.append(validated_record)
    except OSError as error:
        raise OASST1HumanReviewError("oasst1_human_review_decisions_read_failed") from error
    return decisions


def _atomic_write_lines(destination: Path, lines: Iterable[str]) -> None:
    """Перезаписывает controlled ledger атомарно, не оставляя полупустой файл."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.parent / f".{destination.name}.{secrets.token_hex(8)}.tmp"
    try:
        temporary_path.write_text("".join(lines), encoding="utf-8")
        temporary_path.replace(destination)
    except OSError as error:
        raise OASST1HumanReviewError("oasst1_human_review_decisions_write_failed") from error
    finally:
        if temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def append_review_decision(
    queue_path: Path,
    source_path: Path,
    decisions_path: Path,
    *,
    candidate_id: str,
    decision: Decision,
    reason_codes: Iterable[str],
    reviewer_role: str,
    results_root: Path = RESULTS_ROOT,
    raw_root: Path = RAW_ROOT,
    reviews_root: Path = REVIEW_ROOT,
    reviewed_at: datetime | None = None,
) -> dict[str, Any]:
    """Проверяет source на месте и добавляет один metadata-only verdict в ledger."""

    queue = load_review_queue(queue_path, results_root=results_root)
    queue_item = _queue_item_by_id(queue, candidate_id)
    source_records = load_source_records_for_queue(
        queue,
        source_path,
        raw_root=raw_root,
    )
    _validate_source_record(source_records[candidate_id], queue_item)
    existing_decisions = load_review_decisions(
        decisions_path,
        queue,
        reviews_root=reviews_root,
    )
    if candidate_id in {record["candidate_id"] for record in existing_decisions}:
        raise OASST1HumanReviewError("oasst1_human_review_duplicate_decision")
    new_record = create_review_decision(
        queue_item,
        decision=decision,
        reason_codes=reason_codes,
        reviewer_role=reviewer_role,
        reviewed_at=reviewed_at,
    )
    safe_decisions_path = _safe_path_inside(
        decisions_path,
        reviews_root,
        "oasst1_human_review_decisions_outside_reviews",
    )
    _atomic_write_lines(
        safe_decisions_path,
        (
            f"{json.dumps(record, ensure_ascii=False, sort_keys=True)}\n"
            for record in [*existing_decisions, new_record]
        ),
    )
    return new_record


def build_review_summary(
    queue: Mapping[str, Any],
    decisions: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Создаёт metadata-only snapshot прогресса, который не разрешает training."""

    queue_items = {item["id"]: item for item in queue["items"]}
    materialized_decisions = [
        _validate_decision(dict(record), queue_items) for record in decisions
    ]
    decision_ids = [record["candidate_id"] for record in materialized_decisions]
    if len(decision_ids) != len(set(decision_ids)):
        raise OASST1HumanReviewError("oasst1_human_review_duplicate_decision")
    decision_by_id = {record["candidate_id"]: record for record in materialized_decisions}
    outcome_counts = Counter(record["decision"] for record in materialized_decisions)
    unresolved_items = [
        item for item in queue["items"] if item["id"] not in decision_by_id
    ]
    return {
        "format_version": 1,
        "mode": SUMMARY_MODE,
        "source_id": SOURCE_ID,
        "queue_selected_records": len(queue_items),
        "reviewed_records": len(materialized_decisions),
        "approved_records": outcome_counts["approved"],
        "rejected_records": outcome_counts["rejected"],
        "unreviewed_records": len(unresolved_items),
        "review_complete": not unresolved_items,
        "review_status": "complete" if not unresolved_items else "in_progress",
        "training_candidate_allowed": False,
        "promotion_allowed": False,
        "next_gate": (
            "separate immutable candidate, audit, preflight, coverage and quality review"
        ),
        "decisions": [
            {
                "candidate_id": record["candidate_id"],
                "content_sha256": record["content_sha256"],
                "split": record["split"],
                "decision": record["decision"],
                "reason_codes": record["reason_codes"],
                "reviewer_role": record["reviewer_role"],
                "reviewed_at": record["reviewed_at"],
            }
            for record in sorted(materialized_decisions, key=lambda item: item["candidate_id"])
        ],
        "unreviewed_candidate_ids": [item["id"] for item in unresolved_items],
    }


def review_summary_markdown(summary: Mapping[str, Any]) -> str:
    """Рендерит Markdown-учёт без turn text и без утверждения о готовности model."""

    lines = [
        "# Brain v3 — OASST1 human review summary",
        "",
        "## Статус",
        "",
        f"- Выбрано в исходной queue: `{summary['queue_selected_records']}`.",
        f"- Проверено: `{summary['reviewed_records']}`.",
        f"- Одобрено: `{summary['approved_records']}`.",
        f"- Отклонено: `{summary['rejected_records']}`.",
        f"- Остаётся без решения: `{summary['unreviewed_records']}`.",
        f"- Review complete: `{str(summary['review_complete']).lower()}`.",
        f"- Training candidate allowed: `{str(summary['training_candidate_allowed']).lower()}`.",
        f"- Promotion allowed: `{str(summary['promotion_allowed']).lower()}`.",
        "",
        "## Граница",
        "",
        "В этом отчёте есть только metadata решений. Даже полный review не запускает "
        "обучение и не меняет active manifest: далее обязательны отдельные immutable "
        "candidate, audit, preflight, coverage и quality gate.",
        "",
        "## Следующий gate",
        "",
        f"`{summary['next_gate']}`.",
        "",
    ]
    return "\n".join(lines)


def write_review_summary(
    summary_directory: Path,
    summary: Mapping[str, Any],
    *,
    results_root: Path = RESULTS_ROOT,
) -> None:
    """Создаёт новый versioned report и запрещает перезапись истории review."""

    safe_directory = _safe_path_inside(
        summary_directory,
        results_root,
        "oasst1_human_review_summary_outside_results",
    )
    if safe_directory.exists():
        raise OASST1HumanReviewError("oasst1_human_review_summary_exists")
    try:
        safe_directory.mkdir(parents=True)
        (safe_directory / "oasst1_human_review_summary.json").write_text(
            json.dumps(dict(summary), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (safe_directory / "OASST1_HUMAN_REVIEW_SUMMARY.md").write_text(
            review_summary_markdown(summary),
            encoding="utf-8",
        )
    except OSError as error:
        raise OASST1HumanReviewError("oasst1_human_review_summary_write_failed") from error


def _parser() -> argparse.ArgumentParser:
    """Создаёт CLI с взаимоисключающими действиями review workflow."""

    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--show-id", metavar="CANDIDATE_ID")
    actions.add_argument("--record-id", metavar="CANDIDATE_ID")
    actions.add_argument("--summary-out", type=Path)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE_PATH)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS_PATH)
    parser.add_argument("--decision", choices=("approved", "rejected"))
    parser.add_argument("--reason-code", action="append", default=[])
    parser.add_argument("--reviewer-role", default="local_reviewer")
    return parser


def _configure_utf8_output() -> None:
    """Явно выбирает UTF-8 для Windows terminal до вывода русских реплик."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """Выполняет локальный просмотр, запись verdict или summary по явной команде."""

    _configure_utf8_output()
    parser = _parser()
    args = parser.parse_args()
    try:
        if args.show_id:
            queue = load_review_queue(args.queue)
            queue_item = _queue_item_by_id(queue, args.show_id)
            source_records = load_source_records_for_queue(queue, args.source)
            print(render_candidate_for_terminal(queue_item, source_records[args.show_id]))
            return
        if args.record_id:
            if not args.decision or not args.reason_code:
                parser.error("--record-id требует --decision и хотя бы один --reason-code")
            record = append_review_decision(
                args.queue,
                args.source,
                args.decisions,
                candidate_id=args.record_id,
                decision=args.decision,
                reason_codes=args.reason_code,
                reviewer_role=args.reviewer_role,
            )
            print(json.dumps(record, ensure_ascii=False, sort_keys=True))
            return
        if args.decision or args.reason_code:
            parser.error("--decision и --reason-code допустимы только вместе с --record-id")
        queue = load_review_queue(args.queue)
        decisions = load_review_decisions(args.decisions, queue)
        summary = build_review_summary(queue, decisions)
        write_review_summary(args.summary_out, summary)
        print(
            json.dumps(
                {
                    "review_status": summary["review_status"],
                    "reviewed_records": summary["reviewed_records"],
                    "training_candidate_allowed": summary["training_candidate_allowed"],
                    "promotion_allowed": summary["promotion_allowed"],
                },
                ensure_ascii=False,
            )
        )
    except OASST1HumanReviewError as error:
        raise SystemExit(f"OASST1 human review остановлена: {error}") from error


if __name__ == "__main__":
    main()
