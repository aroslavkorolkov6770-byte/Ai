"""Отдельная локальная проверка предложенных тем approved train-диалогов.

Полные исходные реплики читаются только для показа человеку. Журнал решений
содержит исключительно metadata и не меняет прежний quality review, корпус,
split, staging или обучение.
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from evaluation import oasst1_human_review as quality
from evaluation.oasst1_approved_label_inventory import (
    DEFAULT_REPORT_DIRECTORY,
    build_label_inventory,
)
from evaluation.oasst1_semantic_review import CANDIDATE_TOPICS


INVENTORY_PATH = DEFAULT_REPORT_DIRECTORY / "oasst1_approved_label_inventory.json"
DECISIONS_PATH = quality.REVIEW_ROOT / "oasst1-label-review-decisions-v0.1.jsonl"
DECISION_MODE = "oasst1_approved_train_primary_topic_decision"
VERDICTS = frozenset({"confirmed", "rejected", "corrected"})
MAX_LEDGER_LINE_BYTES = 4096
_WRITE_LOCK = threading.Lock()


class OASST1LabelReviewError(ValueError):
    """Останавливает показ или запись при несогласованном review-состоянии."""


@dataclass(frozen=True)
class LabelReviewPaths:
    """Ограничивает input/output заранее известными локальными каталогами."""

    inventory_path: Path = INVENTORY_PATH
    decisions_path: Path = DECISIONS_PATH
    queue_path: Path = quality.DEFAULT_QUEUE_PATH
    source_path: Path = quality.DEFAULT_SOURCE_PATH
    inventory_root: Path = quality.RESULTS_ROOT
    results_root: Path = quality.RESULTS_ROOT
    reviews_root: Path = quality.REVIEW_ROOT
    raw_root: Path = quality.RAW_ROOT
    batch_id: str = "pilot_v0.1"


DEFAULT_PATHS = LabelReviewPaths()
REMAINING_INVENTORY_PATH = (
    quality.RESULTS_ROOT / "oasst1-remaining-label-inventory-v0.2"
    / "oasst1_remaining_label_inventory.json"
)
REMAINING_DECISIONS_PATH = quality.REVIEW_ROOT / "oasst1-label-review-decisions-v0.2.jsonl"
REMAINING_PATHS = LabelReviewPaths(
    inventory_path=REMAINING_INVENTORY_PATH,
    decisions_path=REMAINING_DECISIONS_PATH,
    batch_id="remaining_train_v0.2",
)


def _inside(path: Path, root: Path, code: str) -> Path:
    """Не позволяет выйти из controlled directory при чтении или записи."""

    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root.expanduser().resolve())
    except ValueError as error:
        raise OASST1LabelReviewError(code) from error
    return resolved


def _pilot_context(paths: LabelReviewPaths) -> tuple[dict[str, Any], dict[str, Any]]:
    """Пересверяет immutable inventory с source и прежним quality ledger."""

    inventory_path = _inside(
        paths.inventory_path, paths.inventory_root, "oasst1_label_review_inventory_outside_results"
    )
    try:
        saved = json.loads(inventory_path.read_text(encoding="utf-8"))
        if paths.batch_id == "pilot_v0.1":
            expected = build_label_inventory()
            expected_count = 24
        elif paths.batch_id == "remaining_train_v0.2":
            # Импорт внутри ветки избегает цикла: новый inventory сверяет
            # уже завершённый старый audit через этот же модуль.
            from evaluation.oasst1_remaining_label_inventory import build_remaining_inventory

            expected = build_remaining_inventory()
            expected_count = 51
        else:
            raise OASST1LabelReviewError("oasst1_label_review_unknown_batch")
        queue = quality.load_review_queue(paths.queue_path, results_root=paths.results_root)
    except (OSError, ValueError, quality.OASST1HumanReviewError) as error:
        raise OASST1LabelReviewError("oasst1_label_review_inventory_invalid") from error
    if saved != expected or not isinstance(saved, dict):
        raise OASST1LabelReviewError("oasst1_label_review_inventory_drift")
    pilot = saved["label_review_pilot"]
    if pilot["selected_records"] != expected_count or pilot["split"] != "train":
        raise OASST1LabelReviewError("oasst1_label_review_pilot_invalid")
    queue_by_id = {item["id"]: item for item in queue["items"]}
    if any(
        item["id"] not in queue_by_id
        or item["split"] != "train"
        or queue_by_id[item["id"]]["content_sha256"] != item["content_sha256"]
        or queue_by_id[item["id"]]["primary_candidate_topic"]
        != item["proposed_primary_topic"]
        for item in pilot["items"]
    ):
        raise OASST1LabelReviewError("oasst1_label_review_pilot_queue_mismatch")
    return saved, queue


def _validate_decision(record: Any, pilot_by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Не пропускает в ledger текст или решение вне проверенного pilot."""

    required = {
        "format_version", "mode", "candidate_id", "content_sha256", "split",
        "proposed_primary_topic", "topic_verdict", "corrected_topic",
        "reviewer_role", "reviewed_at",
    }
    if not isinstance(record, dict) or set(record) != required:
        raise OASST1LabelReviewError("oasst1_label_review_decision_invalid")
    candidate_id = record["candidate_id"]
    verdict = record["topic_verdict"]
    if not isinstance(candidate_id, str) or not isinstance(verdict, str):
        raise OASST1LabelReviewError("oasst1_label_review_decision_invalid")
    item = pilot_by_id.get(candidate_id)
    if (
        item is None
        or record["format_version"] != 1
        or record["mode"] != DECISION_MODE
        or record["content_sha256"] != item["content_sha256"]
        or record["split"] != "train"
        or record["proposed_primary_topic"] != item["proposed_primary_topic"]
        or verdict not in VERDICTS
        or record["reviewer_role"] != "local_human_reviewer"
    ):
        raise OASST1LabelReviewError("oasst1_label_review_decision_invalid")
    replacement = record["corrected_topic"]
    if record["topic_verdict"] == "corrected":
        if replacement not in CANDIDATE_TOPICS or replacement == item["proposed_primary_topic"]:
            raise OASST1LabelReviewError("oasst1_label_review_correction_invalid")
    elif replacement is not None:
        raise OASST1LabelReviewError("oasst1_label_review_correction_invalid")
    timestamp = record["reviewed_at"]
    if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
        raise OASST1LabelReviewError("oasst1_label_review_timestamp_invalid")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise OASST1LabelReviewError("oasst1_label_review_timestamp_invalid") from error
    return record


def _load_decisions(paths: LabelReviewPaths, pilot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Проверяет каждую существующую строку append-only metadata ledger."""

    ledger = _inside(
        paths.decisions_path, paths.reviews_root, "oasst1_label_review_ledger_outside_reviews"
    )
    if not ledger.exists():
        return []
    if not ledger.is_file():
        raise OASST1LabelReviewError("oasst1_label_review_ledger_invalid")
    pilot_by_id = {item["id"]: item for item in pilot["items"]}
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        with ledger.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                if len(line.encode("utf-8")) > MAX_LEDGER_LINE_BYTES:
                    raise OASST1LabelReviewError("oasst1_label_review_ledger_line_too_large")
                record = _validate_decision(json.loads(line), pilot_by_id)
                if record["candidate_id"] in seen:
                    raise OASST1LabelReviewError("oasst1_label_review_duplicate_decision")
                seen.add(record["candidate_id"])
                decisions.append(record)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OASST1LabelReviewError("oasst1_label_review_ledger_invalid") from error
    return decisions


def load_review_snapshot(
    paths: LabelReviewPaths = DEFAULT_PATHS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Даёт проверенные metadata для отдельного read-only аудита."""

    inventory, _ = _pilot_context(paths)
    return inventory, _load_decisions(paths, inventory["label_review_pilot"])


def review_status(paths: LabelReviewPaths = DEFAULT_PATHS) -> dict[str, Any]:
    """Возвращает только счётчики; не выводит реплики или исходные метки."""

    inventory, _ = _pilot_context(paths)
    pilot = inventory["label_review_pilot"]
    decisions = _load_decisions(paths, pilot)
    selected = pilot["selected_records"]
    counts = {verdict: sum(item["topic_verdict"] == verdict for item in decisions) for verdict in sorted(VERDICTS)}
    return {
        "pilot_records": selected,
        "reviewed_records": len(decisions),
        "pending_records": selected - len(decisions),
        "verdict_counts": counts,
        "training_candidate_allowed": False,
        "promotion_allowed": False,
    }


def next_dialogue(
    paths: LabelReviewPaths = DEFAULT_PATHS, *, after_id: str | None = None
) -> dict[str, Any] | None:
    """Показывает один полный исходный диалог, не записывая его на диск."""

    inventory, queue = _pilot_context(paths)
    items = inventory["label_review_pilot"]["items"]
    by_id = {item["id"]: item for item in items}
    if after_id is not None and after_id not in by_id:
        raise OASST1LabelReviewError("oasst1_label_review_unknown_candidate")
    reviewed = {item["candidate_id"] for item in _load_decisions(paths, inventory["label_review_pilot"])}
    remaining = [item for item in items if item["id"] not in reviewed]
    if not remaining:
        return None
    if after_id is not None:
        order = [item["id"] for item in items]
        pivot = order.index(after_id)
        chosen = next(
            (by_id[identifier] for identifier in order[pivot + 1:] + order[:pivot + 1] if identifier not in reviewed),
            remaining[0],
        )
    else:
        chosen = remaining[0]
    source = quality.load_source_records_for_queue(queue, paths.source_path, raw_root=paths.raw_root)
    record = source[chosen["id"]]
    if record["content_sha256"] != chosen["content_sha256"] or record["split"] != "train":
        raise OASST1LabelReviewError("oasst1_label_review_source_mismatch")
    return {
        "id": chosen["id"],
        "split": "train",
        "proposed_primary_topic": chosen["proposed_primary_topic"],
        "available_topics": list(CANDIDATE_TOPICS),
        "quote_attention_codes": list(chosen.get("quote_attention_codes", [])),
        "turns": [{"role": turn["role"], "text": turn["text"]} for turn in record["turns"]],
    }


def record_topic_decision(
    candidate_id: str,
    verdict: Literal["confirmed", "rejected", "corrected"],
    *,
    corrected_topic: str | None = None,
    read_confirmed: bool = False,
    paths: LabelReviewPaths = DEFAULT_PATHS,
) -> dict[str, Any]:
    """Пишет явное human decision атомарно, не меняя source/quality ledger."""

    if read_confirmed is not True:
        raise OASST1LabelReviewError("oasst1_label_review_full_read_required")
    with _WRITE_LOCK:
        inventory, _ = _pilot_context(paths)
        pilot = inventory["label_review_pilot"]
        by_id = {item["id"]: item for item in pilot["items"]}
        if candidate_id not in by_id:
            raise OASST1LabelReviewError("oasst1_label_review_unknown_candidate")
        decisions = _load_decisions(paths, pilot)
        if candidate_id in {item["candidate_id"] for item in decisions}:
            raise OASST1LabelReviewError("oasst1_label_review_duplicate_decision")
        item = by_id[candidate_id]
        record = _validate_decision(
            {
                "format_version": 1,
                "mode": DECISION_MODE,
                "candidate_id": candidate_id,
                "content_sha256": item["content_sha256"],
                "split": "train",
                "proposed_primary_topic": item["proposed_primary_topic"],
                "topic_verdict": verdict,
                "corrected_topic": corrected_topic,
                "reviewer_role": "local_human_reviewer",
                "reviewed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            },
            by_id,
        )
        ledger = _inside(
            paths.decisions_path, paths.reviews_root, "oasst1_label_review_ledger_outside_reviews"
        )
        if ledger.exists() and not ledger.is_file():
            raise OASST1LabelReviewError("oasst1_label_review_ledger_invalid")
        temporary = ledger.parent / f".{ledger.name}.{secrets.token_hex(8)}.tmp"
        try:
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as output:
                for previous in decisions + [record]:
                    output.write(json.dumps(previous, ensure_ascii=False, separators=(",", ":")) + "\n")
            temporary.replace(ledger)
        except OSError as error:
            raise OASST1LabelReviewError("oasst1_label_review_write_failed") from error
        finally:
            temporary.unlink(missing_ok=True)
        return {"candidate_id": candidate_id, "topic_verdict": verdict, "reviewed_at": record["reviewed_at"]}
