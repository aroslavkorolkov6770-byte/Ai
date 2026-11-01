"""Ведёт metadata-only human-review ledger для первого review-only candidate Brain v3.

Модуль не одобряет сцены сам, не меняет active corpus и не запускает обучение.
Только человек-reviewer по явной команде может записать один verdict. В ledger
и summary никогда не попадают тексты диалогов: там остаются лишь ID, hashes,
фиксированные reason codes и время решения.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import secrets
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from evaluation.brain_v3_manual_candidate_review import (
    DEFAULT_CANDIDATE_PATH,
    DEFAULT_REPORT_DIRECTORY,
    DEFAULT_SCOPE_PATH,
    HIGH_RISK_TOPICS,
    PROJECT_ROOT,
    RESULTS_ROOT,
    REVIEW_ROOT,
    SCOPE_ID,
    ManualCandidateReviewError,
    build_review_queue,
    load_review_candidate,
    load_scope,
    render_candidate_for_terminal,
)


QUEUE_MODE = "brain_v3_owned_manual_review_only_queue"
DECISION_MODE = "brain_v3_owned_manual_human_review_decision"
SUMMARY_MODE = "brain_v3_owned_manual_human_review_summary"
DEFAULT_QUEUE_PATH = DEFAULT_REPORT_DIRECTORY / "brain_v3_manual_candidate_review_queue.json"
DEFAULT_DECISIONS_PATH = REVIEW_ROOT / f"{SCOPE_ID}.decisions.jsonl"
DEFAULT_SUMMARY_DIRECTORY = (
    RESULTS_ROOT / "brain-v3-owned-manual-candidate-v1-human-review-v0.1"
)

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVIEWER_ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
RAW_CONTENT_KEYS = frozenset({"text", "turns", "conversation", "message"})

Decision = Literal["approved", "rejected"]

# Одобрение означает, что reviewer лично подтвердил все четыре обязательные
# проверки scope. Свободное пояснение намеренно не принимается: оно могло бы
# случайно записать личные данные или текст сцены в metadata-only ledger.
APPROVAL_REASON_CODES = frozenset(
    {
        "provenance_checked",
        "coherent_follow_up",
        "natural_non_template_tone",
        "metadata_route_match",
    }
)
REQUIRED_APPROVAL_REASON_CODES = APPROVAL_REASON_CODES
REJECTION_REASON_CODES = frozenset(
    {
        "provenance_risk",
        "personal_data_risk",
        "incoherent_follow_up",
        "template_like",
        "irrelevant_response",
        "unsupported_claim",
        "unfounded_memory",
        "privacy_boundary_failure",
        "unsafe_or_unhelpful_advice",
        "incorrect_metadata",
        "other_quality_failure",
    }
)


class ManualCandidateHumanReviewError(ValueError):
    """Останавливает review workflow до записи непроверяемого verdict."""


def _safe_path_inside(path: Path, root: Path, error_code: str) -> Path:
    """Разрешает локальное чтение и запись только внутри ожидаемого root."""

    resolved_path = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ManualCandidateHumanReviewError(error_code) from error
    return resolved_path


def _contains_raw_turn_content(value: Any) -> bool:
    """Не допускает реплики source-диалогов в очередь, ledger и summary."""

    if isinstance(value, Mapping):
        if RAW_CONTENT_KEYS.intersection(value):
            return True
        return any(_contains_raw_turn_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_raw_turn_content(item) for item in value)
    return False


def _read_json_object(path: Path, error_code: str) -> dict[str, Any]:
    """Читает один JSON object, не выводя source content при ошибке."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManualCandidateHumanReviewError(error_code) from error
    if not isinstance(value, dict):
        raise ManualCandidateHumanReviewError(error_code)
    return value


def _validate_queue_item(item: Any) -> dict[str, Any]:
    """Проверяет одну metadata-only запись immutable queue."""

    required_keys = {
        "id",
        "record_sha256",
        "family_id",
        "topic",
        "dialogue_goal",
        "emotional_tone",
        "expected_response_properties",
        "turn_count",
        "requires_full_human_review",
        "review_status",
    }
    if (
        not isinstance(item, dict)
        or set(item) != required_keys
        or _contains_raw_turn_content(item)
    ):
        raise ManualCandidateHumanReviewError("manual_human_review_queue_item_invalid")
    identifier = item.get("id")
    record_sha256 = item.get("record_sha256")
    properties = item.get("expected_response_properties")
    if (
        not isinstance(identifier, str)
        or not identifier
        or not isinstance(record_sha256, str)
        or not SHA256_PATTERN.fullmatch(record_sha256)
        or not isinstance(item.get("family_id"), str)
        or not item["family_id"]
        or not isinstance(item.get("topic"), str)
        or not isinstance(item.get("dialogue_goal"), str)
        or not isinstance(item.get("emotional_tone"), str)
        or not isinstance(properties, list)
        or not properties
        or not all(isinstance(value, str) and value for value in properties)
        or item.get("turn_count") != 4
        or not isinstance(item.get("requires_full_human_review"), bool)
        or item.get("review_status") != "unreviewed"
    ):
        raise ManualCandidateHumanReviewError("manual_human_review_queue_item_invalid")
    if item["requires_full_human_review"] is not (item["topic"] in HIGH_RISK_TOPICS):
        raise ManualCandidateHumanReviewError("manual_human_review_queue_item_invalid")
    return item


def load_review_queue(
    queue_path: Path = DEFAULT_QUEUE_PATH,
    *,
    scope_path: Path = DEFAULT_SCOPE_PATH,
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    results_root: Path = RESULTS_ROOT,
    review_root: Path = REVIEW_ROOT,
) -> dict[str, Any]:
    """Связывает immutable queue с текущим review-only source по SHA и metadata."""

    safe_queue_path = _safe_path_inside(
        queue_path,
        results_root,
        "manual_human_review_queue_outside_results_root",
    )
    if not safe_queue_path.is_file():
        raise ManualCandidateHumanReviewError("manual_human_review_queue_missing")
    queue = _read_json_object(safe_queue_path, "manual_human_review_queue_invalid")
    if _contains_raw_turn_content(queue):
        raise ManualCandidateHumanReviewError("manual_human_review_queue_not_metadata_only")
    if (
        queue.get("format_version") != 1
        or queue.get("mode") != QUEUE_MODE
        or queue.get("scope_id") != SCOPE_ID
        or queue.get("review_status") != "unreviewed"
        or queue.get("metadata_only") is not True
        or queue.get("candidate_is_active") is not False
        or queue.get("training_eligible") is not False
        or queue.get("active_manifest_updated") is not False
        or queue.get("training_started") is not False
    ):
        raise ManualCandidateHumanReviewError("manual_human_review_queue_invalid")
    items = queue.get("items")
    if not isinstance(items, list) or not items:
        raise ManualCandidateHumanReviewError("manual_human_review_queue_invalid")
    validated_items = [_validate_queue_item(item) for item in items]
    identifiers = [item["id"] for item in validated_items]
    if len(identifiers) != len(set(identifiers)):
        raise ManualCandidateHumanReviewError("manual_human_review_queue_duplicate_identifier")

    try:
        scope = load_scope(scope_path, review_root=review_root)
        records, candidate_sha256 = load_review_candidate(
            candidate_path,
            review_root=review_root,
        )
        expected_queue = build_review_queue(
            records,
            scope,
            candidate_path=candidate_path,
            candidate_sha256=candidate_sha256,
        )
    except ManualCandidateReviewError as error:
        raise ManualCandidateHumanReviewError("manual_human_review_source_invalid") from error
    expected_fields = (
        "candidate_path",
        "candidate_sha256",
        "candidate_records",
        "turns_per_record",
        "coverage",
        "items",
    )
    if any(queue.get(field) != expected_queue.get(field) for field in expected_fields):
        raise ManualCandidateHumanReviewError("manual_human_review_queue_source_mismatch")
    return queue


def _normalise_reason_codes(
    decision: Decision,
    reason_codes: Iterable[str],
) -> list[str]:
    """Принимает только фиксированные reason codes для metadata-only ledger."""

    raw_codes = list(reason_codes)
    if not raw_codes or any(not isinstance(code, str) for code in raw_codes):
        raise ManualCandidateHumanReviewError("manual_human_review_reason_missing")
    codes = sorted(set(raw_codes))
    allowed_codes = APPROVAL_REASON_CODES if decision == "approved" else REJECTION_REASON_CODES
    if any(code not in allowed_codes for code in codes):
        raise ManualCandidateHumanReviewError("manual_human_review_reason_invalid")
    if decision == "approved" and not REQUIRED_APPROVAL_REASON_CODES.issubset(codes):
        raise ManualCandidateHumanReviewError("manual_human_review_approval_criteria_missing")
    return codes


def _normalise_reviewer_role(reviewer_role: str) -> str:
    """Не записывает имя или свободный комментарий вместо нейтральной роли."""

    if not isinstance(reviewer_role, str) or not REVIEWER_ROLE_PATTERN.fullmatch(
        reviewer_role
    ):
        raise ManualCandidateHumanReviewError("manual_human_review_reviewer_role_invalid")
    return reviewer_role


def _queue_item_by_id(queue: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    """Находит candidate только среди immutable queue IDs."""

    if not isinstance(candidate_id, str) or not candidate_id:
        raise ManualCandidateHumanReviewError("manual_human_review_candidate_id_invalid")
    for item in queue["items"]:
        if item["id"] == candidate_id:
            return item
    raise ManualCandidateHumanReviewError("manual_human_review_candidate_not_in_queue")


def create_review_decision(
    queue_item: Mapping[str, Any],
    *,
    decision: Decision,
    reason_codes: Iterable[str],
    reviewer_role: str,
    reviewed_at: datetime | None = None,
) -> dict[str, Any]:
    """Создаёт один human verdict без текста сцены и без training permission."""

    if decision not in {"approved", "rejected"}:
        raise ManualCandidateHumanReviewError("manual_human_review_decision_invalid")
    item = _validate_queue_item(dict(queue_item))
    timestamp = reviewed_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ManualCandidateHumanReviewError("manual_human_review_timestamp_invalid")
    return {
        "format_version": 1,
        "mode": DECISION_MODE,
        "scope_id": SCOPE_ID,
        "candidate_id": item["id"],
        "record_sha256": item["record_sha256"],
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
    """Проверяет сохранённый verdict against immutable queue metadata."""

    required_keys = {
        "format_version",
        "mode",
        "scope_id",
        "candidate_id",
        "record_sha256",
        "decision",
        "reason_codes",
        "reviewer_role",
        "reviewed_at",
    }
    if (
        not isinstance(record, dict)
        or set(record) != required_keys
        or _contains_raw_turn_content(record)
        or record.get("format_version") != 1
        or record.get("mode") != DECISION_MODE
        or record.get("scope_id") != SCOPE_ID
    ):
        raise ManualCandidateHumanReviewError("manual_human_review_decision_invalid")
    candidate_id = record.get("candidate_id")
    if candidate_id not in queue_items:
        raise ManualCandidateHumanReviewError("manual_human_review_decision_invalid")
    queue_item = queue_items[candidate_id]
    if record.get("record_sha256") != queue_item["record_sha256"]:
        raise ManualCandidateHumanReviewError("manual_human_review_decision_metadata_mismatch")
    decision = record.get("decision")
    if decision not in {"approved", "rejected"}:
        raise ManualCandidateHumanReviewError("manual_human_review_decision_invalid")
    _normalise_reason_codes(decision, record.get("reason_codes", []))
    _normalise_reviewer_role(record.get("reviewer_role"))
    reviewed_at = record.get("reviewed_at")
    if not isinstance(reviewed_at, str) or not reviewed_at.endswith("Z"):
        raise ManualCandidateHumanReviewError("manual_human_review_timestamp_invalid")
    return record


def load_review_decisions(
    decisions_path: Path,
    queue: Mapping[str, Any],
    *,
    review_root: Path = REVIEW_ROOT,
) -> list[dict[str, Any]]:
    """Загружает append-only metadata ledger; отсутствующий файл означает ноль verdict."""

    safe_decisions_path = _safe_path_inside(
        decisions_path,
        review_root,
        "manual_human_review_decisions_outside_review_root",
    )
    if not safe_decisions_path.exists():
        return []
    if not safe_decisions_path.is_file():
        raise ManualCandidateHumanReviewError("manual_human_review_decisions_invalid")
    queue_items = {item["id"]: item for item in queue["items"]}
    decisions: list[dict[str, Any]] = []
    decision_ids: set[str] = set()
    try:
        with safe_decisions_path.open("r", encoding="utf-8") as source_file:
            for line in source_file:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ManualCandidateHumanReviewError(
                        "manual_human_review_decisions_invalid"
                    ) from error
                validated_record = _validate_decision(record, queue_items)
                candidate_id = validated_record["candidate_id"]
                if candidate_id in decision_ids:
                    raise ManualCandidateHumanReviewError(
                        "manual_human_review_duplicate_decision"
                    )
                decision_ids.add(candidate_id)
                decisions.append(validated_record)
    except OSError as error:
        raise ManualCandidateHumanReviewError(
            "manual_human_review_decisions_read_failed"
        ) from error
    return decisions


def _atomic_write_lines(destination: Path, lines: Iterable[str]) -> None:
    """Сохраняет ledger атомарно, не оставляя частичный файл при ошибке."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.parent / f".{destination.name}.{secrets.token_hex(8)}.tmp"
    try:
        temporary_path.write_text("".join(lines), encoding="utf-8")
        temporary_path.replace(destination)
    except OSError as error:
        raise ManualCandidateHumanReviewError(
            "manual_human_review_decisions_write_failed"
        ) from error
    finally:
        if temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def append_review_decision(
    queue_path: Path,
    decisions_path: Path,
    *,
    candidate_id: str,
    decision: Decision,
    reason_codes: Iterable[str],
    reviewer_role: str,
    scope_path: Path = DEFAULT_SCOPE_PATH,
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    results_root: Path = RESULTS_ROOT,
    review_root: Path = REVIEW_ROOT,
    reviewed_at: datetime | None = None,
) -> dict[str, Any]:
    """Добавляет один явный human verdict, не изменяя queue и review-only source."""

    queue = load_review_queue(
        queue_path,
        scope_path=scope_path,
        candidate_path=candidate_path,
        results_root=results_root,
        review_root=review_root,
    )
    item = _queue_item_by_id(queue, candidate_id)
    decisions = load_review_decisions(decisions_path, queue, review_root=review_root)
    if candidate_id in {record["candidate_id"] for record in decisions}:
        raise ManualCandidateHumanReviewError("manual_human_review_duplicate_decision")
    new_decision = create_review_decision(
        item,
        decision=decision,
        reason_codes=reason_codes,
        reviewer_role=reviewer_role,
        reviewed_at=reviewed_at,
    )
    safe_decisions_path = _safe_path_inside(
        decisions_path,
        review_root,
        "manual_human_review_decisions_outside_review_root",
    )
    _atomic_write_lines(
        safe_decisions_path,
        (
            f"{json.dumps(record, ensure_ascii=False, sort_keys=True)}\n"
            for record in [*decisions, new_decision]
        ),
    )
    return new_decision


def build_review_summary(
    queue: Mapping[str, Any],
    decisions: Iterable[Mapping[str, Any]],
    *,
    general_review_ratio: float,
) -> dict[str, Any]:
    """Сводит progress human review, но никогда не даёт promotion/training право."""

    if not 0 < general_review_ratio <= 1:
        raise ManualCandidateHumanReviewError("manual_human_review_ratio_invalid")
    queue_items = {item["id"]: item for item in queue["items"]}
    materialised = [_validate_decision(dict(item), queue_items) for item in decisions]
    decision_ids = [item["candidate_id"] for item in materialised]
    if len(decision_ids) != len(set(decision_ids)):
        raise ManualCandidateHumanReviewError("manual_human_review_duplicate_decision")
    decision_by_id = {item["candidate_id"]: item for item in materialised}
    high_risk_items = [item for item in queue["items"] if item["requires_full_human_review"]]
    general_items = [item for item in queue["items"] if not item["requires_full_human_review"]]
    reviewed_high_risk = [item for item in high_risk_items if item["id"] in decision_by_id]
    reviewed_general = [item for item in general_items if item["id"] in decision_by_id]
    minimum_general_reviews = math.ceil(len(general_items) * general_review_ratio)
    required_coverage_complete = (
        len(reviewed_high_risk) == len(high_risk_items)
        and len(reviewed_general) >= minimum_general_reviews
    )
    all_items_reviewed = len(materialised) == len(queue_items)
    outcome_counts = Counter(item["decision"] for item in materialised)
    rejection_ids = sorted(
        item["candidate_id"]
        for item in materialised
        if item["decision"] == "rejected"
    )
    if not materialised:
        review_status = "not_started"
    elif all_items_reviewed:
        review_status = "all_items_reviewed"
    elif required_coverage_complete:
        review_status = "required_coverage_complete"
    else:
        review_status = "in_progress"
    if not required_coverage_complete:
        next_gate = "complete required human-review coverage before any staging decision"
    elif rejection_ids:
        next_gate = "investigate rejected drafts; staging and training remain prohibited"
    else:
        next_gate = "request a separate owner decision about a new staging/audit candidate"
    return {
        "format_version": 1,
        "mode": SUMMARY_MODE,
        "scope_id": SCOPE_ID,
        "queue_records": len(queue_items),
        "reviewed_records": len(materialised),
        "approved_records": outcome_counts["approved"],
        "rejected_records": outcome_counts["rejected"],
        "unreviewed_records": len(queue_items) - len(materialised),
        "high_risk_required_records": len(high_risk_items),
        "high_risk_reviewed_records": len(reviewed_high_risk),
        "general_records": len(general_items),
        "minimum_general_reviews": minimum_general_reviews,
        "general_reviewed_records": len(reviewed_general),
        "required_human_review_coverage_complete": required_coverage_complete,
        "all_items_reviewed": all_items_reviewed,
        "review_status": review_status,
        "rejected_candidate_ids": rejection_ids,
        "decisions": [
            {
                "candidate_id": item["candidate_id"],
                "record_sha256": item["record_sha256"],
                "decision": item["decision"],
                "reason_codes": item["reason_codes"],
                "reviewer_role": item["reviewer_role"],
                "reviewed_at": item["reviewed_at"],
            }
            for item in sorted(materialised, key=lambda item: item["candidate_id"])
        ],
        "training_candidate_allowed": False,
        "promotion_allowed": False,
        "next_gate": next_gate,
    }


def review_summary_markdown(summary: Mapping[str, Any]) -> str:
    """Рендерит metadata-only Markdown summary без утверждения о готовности модели."""

    return "\n".join(
        [
            "# Brain v3 — manual candidate human-review summary",
            "",
            "## Статус",
            "",
            f"- В queue: `{summary['queue_records']}`.",
            f"- Проверено: `{summary['reviewed_records']}`.",
            f"- Одобрено: `{summary['approved_records']}`.",
            f"- Отклонено: `{summary['rejected_records']}`.",
            f"- Без verdict: `{summary['unreviewed_records']}`.",
            f"- High-risk: `{summary['high_risk_reviewed_records']}` из `{summary['high_risk_required_records']}`.",
            f"- General sample: `{summary['general_reviewed_records']}` из требуемых `{summary['minimum_general_reviews']}`.",
            f"- Required human-review coverage complete: `{str(summary['required_human_review_coverage_complete']).lower()}`.",
            f"- Training candidate allowed: `{str(summary['training_candidate_allowed']).lower()}`.",
            f"- Promotion allowed: `{str(summary['promotion_allowed']).lower()}`.",
            "",
            "## Граница",
            "",
            "Summary содержит только metadata решений. Даже полный положительный review не создаёт staging candidate, не меняет active manifest и не запускает обучение: требуется отдельное решение владельца и отдельные audit/preflight gates.",
            "",
            "## Следующий gate",
            "",
            f"`{summary['next_gate']}`.",
            "",
        ]
    )


def write_review_summary(
    summary_directory: Path,
    summary: Mapping[str, Any],
    *,
    results_root: Path = RESULTS_ROOT,
) -> None:
    """Создаёт новый versioned summary и запрещает перезапись review history."""

    safe_directory = _safe_path_inside(
        summary_directory,
        results_root,
        "manual_human_review_summary_outside_results_root",
    )
    if safe_directory.exists():
        raise ManualCandidateHumanReviewError("manual_human_review_summary_already_exists")
    try:
        safe_directory.mkdir(parents=True)
        (safe_directory / "brain_v3_manual_candidate_human_review_summary.json").write_text(
            json.dumps(dict(summary), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (safe_directory / "BRAIN_V3_MANUAL_CANDIDATE_HUMAN_REVIEW_SUMMARY.md").write_text(
            review_summary_markdown(summary),
            encoding="utf-8",
        )
    except OSError as error:
        raise ManualCandidateHumanReviewError(
            "manual_human_review_summary_write_failed"
        ) from error


def _parser() -> argparse.ArgumentParser:
    """Создаёт явный CLI: просмотр, один verdict либо immutable summary."""

    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--show-id", metavar="CANDIDATE_ID")
    actions.add_argument("--record-id", metavar="CANDIDATE_ID")
    actions.add_argument("--summary-out", type=Path)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE_PATH)
    parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE_PATH)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE_PATH)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS_PATH)
    parser.add_argument("--decision", choices=("approved", "rejected"))
    parser.add_argument("--reason-code", action="append", default=[])
    parser.add_argument("--reviewer-role", default="project_owner")
    return parser


def _configure_utf8_output() -> None:
    """Выбирает UTF-8 в Windows terminal для явного локального просмотра сцен."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """Выполняет только явно выбранное действие review workflow."""

    _configure_utf8_output()
    parser = _parser()
    args = parser.parse_args()
    try:
        queue = load_review_queue(
            args.queue,
            scope_path=args.scope,
            candidate_path=args.candidate,
        )
        if args.show_id:
            scope = load_scope(args.scope)
            records, _ = load_review_candidate(args.candidate)
            _queue_item_by_id(queue, args.show_id)
            print(render_candidate_for_terminal(records, scope, args.show_id))
            return
        if args.record_id:
            if not args.decision or not args.reason_code:
                parser.error("--record-id требует --decision и хотя бы один --reason-code")
            record = append_review_decision(
                args.queue,
                args.decisions,
                candidate_id=args.record_id,
                decision=args.decision,
                reason_codes=args.reason_code,
                reviewer_role=args.reviewer_role,
                scope_path=args.scope,
                candidate_path=args.candidate,
            )
            print(json.dumps(record, ensure_ascii=False, sort_keys=True))
            return
        if args.decision or args.reason_code:
            parser.error("--decision и --reason-code допустимы только с --record-id")
        decisions = load_review_decisions(args.decisions, queue)
        scope = load_scope(args.scope)
        summary = build_review_summary(
            queue,
            decisions,
            general_review_ratio=scope["review"]["minimum_general_stratified_review_ratio"],
        )
        write_review_summary(args.summary_out, summary)
        print(
            json.dumps(
                {
                    "review_status": summary["review_status"],
                    "reviewed_records": summary["reviewed_records"],
                    "required_human_review_coverage_complete": summary[
                        "required_human_review_coverage_complete"
                    ],
                    "training_candidate_allowed": summary["training_candidate_allowed"],
                    "promotion_allowed": summary["promotion_allowed"],
                },
                ensure_ascii=False,
            )
        )
    except (ManualCandidateHumanReviewError, ManualCandidateReviewError) as error:
        raise SystemExit(f"Manual candidate human review остановлена: {error}") from error


if __name__ == "__main__":
    main()
