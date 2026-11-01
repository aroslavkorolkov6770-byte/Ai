"""Готовит metadata-only очередь остальных approved train OASST1-диалогов.

Очередь опирается на завершённый immutable pilot audit и исключает его 24 ID.
Сигналы цитирования — только подсказки для человека, не лицензионный вывод.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from evaluation import oasst1_human_review as quality
from evaluation.oasst1_approved_label_inventory import build_label_inventory
from evaluation.oasst1_label_review_audit import build_label_review_audit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = PROJECT_ROOT / "evaluation" / "results"
PILOT_AUDIT_PATH = (
    RESULTS_ROOT / "oasst1-approved-label-review-audit-v0.1" / "oasst1_label_review_audit.json"
)
DEFAULT_REPORT_DIRECTORY = RESULTS_ROOT / "oasst1-remaining-label-inventory-v0.2"
SOURCE_PATH = quality.DEFAULT_SOURCE_PATH
QUEUE_PATH = quality.DEFAULT_QUEUE_PATH
QUALITY_DECISIONS_PATH = quality.DEFAULT_DECISIONS_PATH
RAW_CONTENT_KEYS = frozenset({"text", "turns", "conversation", "message", "user", "assistant"})
URL_PATTERN = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]{1,100}\]\(https?://", re.IGNORECASE)
LONG_QUOTE_PATTERN = re.compile(r"[«\"][^»\"]{120,}[»\"]")
ATTRIBUTION_PATTERN = re.compile(r"\b(?:цитата|источник|согласно|по данным)\b", re.IGNORECASE)
LONG_ASSISTANT_CHARS = 700


class OASST1RemainingInventoryError(ValueError):
    """Останавливает подготовку очереди при drift или неполном pilot."""


def _sha256(path: Path) -> str:
    """Связывает новую очередь с точными входными файлами."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise OASST1RemainingInventoryError("oasst1_remaining_inventory_input_unreadable") from error


def _contains_raw_content(value: Any) -> bool:
    """Защищает metadata-only report от копирования исходных реплик."""

    if isinstance(value, Mapping):
        return bool(RAW_CONTENT_KEYS.intersection(value)) or any(
            _contains_raw_content(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_raw_content(item) for item in value)
    return False


def quote_attention_codes(record: Mapping[str, Any]) -> list[str]:
    """Отмечает поверхностные сигналы для ручной проверки, без текста/URL."""

    codes: set[str] = set()
    for turn in record["turns"]:
        if turn["role"] != "assistant":
            continue
        content = turn["text"]
        if URL_PATTERN.search(content) or MARKDOWN_LINK_PATTERN.search(content):
            codes.add("assistant_contains_link")
        if len(content) >= LONG_ASSISTANT_CHARS:
            codes.add("long_assistant_turn")
        if LONG_QUOTE_PATTERN.search(content):
            codes.add("long_quoted_span")
        if ATTRIBUTION_PATTERN.search(content):
            codes.add("attribution_word")
    return sorted(codes)


def _load_completed_pilot_audit() -> dict[str, Any]:
    """Требует byte-bound совпадения сохранённого и пересчитанного pilot audit."""

    try:
        saved = json.loads(PILOT_AUDIT_PATH.read_text(encoding="utf-8"))
        current = build_label_review_audit()
    except (OSError, ValueError) as error:
        raise OASST1RemainingInventoryError("oasst1_remaining_inventory_pilot_audit_invalid") from error
    if not isinstance(saved, dict) or saved != current:
        raise OASST1RemainingInventoryError("oasst1_remaining_inventory_pilot_audit_drift")
    pilot = saved["pilot"]
    if pilot["reviewed_records"] != 24 or len(pilot["items"]) != 24:
        raise OASST1RemainingInventoryError("oasst1_remaining_inventory_pilot_incomplete")
    return saved


def build_remaining_inventory() -> dict[str, Any]:
    """Сверяет source/quality review и выбирает ровно 51 непроверенный train-ID."""

    pilot_inventory = build_label_inventory()
    completed_pilot = _load_completed_pilot_audit()
    try:
        queue = quality.load_review_queue(QUEUE_PATH)
        quality_decisions = quality.load_review_decisions(QUALITY_DECISIONS_PATH, queue)
        source = quality.load_source_records_for_queue(queue, SOURCE_PATH)
    except quality.OASST1HumanReviewError as error:
        raise OASST1RemainingInventoryError("oasst1_remaining_inventory_source_invalid") from error
    approved_ids = {
        decision["candidate_id"] for decision in quality_decisions
        if decision["decision"] == "approved"
    }
    pilot_ids = {item["id"] for item in completed_pilot["pilot"]["items"]}
    approved_train = [
        item for item in queue["items"]
        if item["split"] == "train" and item["id"] in approved_ids
    ]
    remaining = [item for item in approved_train if item["id"] not in pilot_ids]
    if (
        len(approved_train) != 75
        or len(pilot_ids) != 24
        or len(remaining) != 51
        or len({item["id"] for item in remaining}) != 51
        or pilot_ids.intersection(item["id"] for item in remaining)
        or pilot_inventory["inputs"]["source_sha256"] != _sha256(SOURCE_PATH)
        or completed_pilot["inputs"]["quality_decisions_sha256"] != _sha256(QUALITY_DECISIONS_PATH)
    ):
        raise OASST1RemainingInventoryError("oasst1_remaining_inventory_counts_or_hashes_changed")
    ordered = sorted(remaining, key=lambda item: (item["primary_candidate_topic"], item["id"]))
    items = [
        {
            "id": item["id"],
            "content_sha256": item["content_sha256"],
            "split": "train",
            "proposed_primary_topic": item["primary_candidate_topic"],
            "human_topic_verdict": "pending",
            "quote_attention_codes": quote_attention_codes(source[item["id"]]),
        }
        for item in ordered
    ]
    code_counts = Counter(code for item in items for code in item["quote_attention_codes"])
    report = {
        "format_version": 1,
        "mode": "oasst1_remaining_approved_train_primary_topic_inventory",
        "batch_id": "remaining_train_v0.2",
        "source_id": queue["source_id"],
        "inputs": {
            "source_sha256": _sha256(SOURCE_PATH),
            "queue_sha256": _sha256(QUEUE_PATH),
            "decisions_sha256": _sha256(QUALITY_DECISIONS_PATH),
            "completed_pilot_audit_sha256": _sha256(PILOT_AUDIT_PATH),
        },
        "existing_human_review": pilot_inventory["existing_human_review"],
        "label_review_pilot": {
            "selected_records": len(items),
            "split": "train",
            "selection_rule": "all_approved_train_ids_excluding_completed_24_id_pilot",
            "excluded_completed_pilot_records": 24,
            "items": items,
            "not_a_statistical_quality_estimate": True,
        },
        "quote_attention": {
            "signal_code_counts": dict(sorted(code_counts.items())),
            "records_with_any_signal": sum(bool(item["quote_attention_codes"]) for item in items),
            "heuristic_only": True,
            "not_a_copyright_or_license_verdict": True,
        },
        "boundaries": {
            "raw_dialogue_content_in_report": False,
            "previous_pilot_decisions_modified": False,
            "quality_decisions_modified": False,
            "validation_or_test_reclassified_as_train": False,
            "active_corpus_modified": False,
            "training_started": False,
            "promotion_allowed": False,
        },
        "next_gate": (
            "Human-review the remaining 51 primary topics and separately inspect "
            "potential quotations and links. No training or promotion follows automatically."
        ),
    }
    if _contains_raw_content(report):
        raise OASST1RemainingInventoryError("oasst1_remaining_inventory_not_metadata_only")
    return report


def inventory_markdown(report: Mapping[str, Any]) -> str:
    """Объясняет новую очередь и ограниченность сигналов без исходного текста."""

    selected = report["label_review_pilot"]["selected_records"]
    attention = report["quote_attention"]
    lines = [
        "# Brain v3 — остальные approved train OASST1: очередь тем v0.2",
        "",
        "## Проверенный состав",
        "",
        f"- В очереди `{selected}` ранее одобренных train-ID, не входящих в завершённый pilot из 24.",
        "- Исходный split сохранён. Validation и test не включены.",
        "- Все предложенные темы пока эвристические; human topic verdict не создан.",
        "",
        "## Сигналы для отдельной проверки цитат и ссылок",
        "",
        f"- У `{attention['records_with_any_signal']}` записей обнаружен хотя бы один поверхностный сигнал.",
    ]
    for code, count in attention["signal_code_counts"].items():
        lines.append(f"- `{code}`: `{count}` записей.")
    lines.extend([
        "",
        "Сигналы не доказывают наличие чужой цитаты, нарушения прав или",
        "пригодность примера для обучения. Отсутствие сигнала не гарантирует",
        "безопасность. Ссылки и исходные реплики намеренно не копируются в отчёт.",
        "",
        "## Граница",
        "",
        "Очередь опирается на exact hashes завершённого pilot audit, source и",
        "quality review. Она не меняет прошлый ledger, active corpus, staging",
        "или веса. Следующее действие — ручная проверка тем и отдельно примеров",
        "со сторонними материалами. Обучение не разрешено.",
        "",
    ])
    return "\n".join(lines)


def write_inventory(
    report: Mapping[str, Any], directory: Path = DEFAULT_REPORT_DIRECTORY,
    *, results_root: Path = RESULTS_ROOT,
) -> None:
    """Записывает только новую immutable папку внутри evaluation/results."""

    destination = directory.expanduser().resolve()
    try:
        destination.relative_to(results_root.expanduser().resolve())
    except ValueError as error:
        raise OASST1RemainingInventoryError("oasst1_remaining_inventory_output_outside_results") from error
    if destination.exists():
        raise OASST1RemainingInventoryError("oasst1_remaining_inventory_report_exists")
    if _contains_raw_content(report):
        raise OASST1RemainingInventoryError("oasst1_remaining_inventory_not_metadata_only")
    try:
        destination.mkdir(parents=True)
        (destination / "oasst1_remaining_label_inventory.json").write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (destination / "OASST1_REMAINING_LABEL_INVENTORY.md").write_text(
            inventory_markdown(report), encoding="utf-8"
        )
    except OSError as error:
        raise OASST1RemainingInventoryError("oasst1_remaining_inventory_write_failed") from error


def main() -> None:
    """Создаёт очередь только по явной команде --audit."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", required=True)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    args = parser.parse_args()
    try:
        report = build_remaining_inventory()
        write_inventory(report, args.report_directory)
    except OASST1RemainingInventoryError as error:
        raise SystemExit(f"OASST1 remaining inventory остановлена: {error}") from error
    print(json.dumps({"selected_records": report["label_review_pilot"]["selected_records"],
                      "quote_attention_records": report["quote_attention"]["records_with_any_signal"],
                      "training_started": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
