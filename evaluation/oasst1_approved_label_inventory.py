"""Строит metadata-only инвентаризацию уже проверенных OASST1 диалогов.

Кандидатные темы существующей очереди эвристические. Этот модуль выбирает
только train-ID для будущего ручного подтверждения меток и ничего не меняет
в raw corpus, verdict ledger, split или обучении.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from evaluation import oasst1_human_review as review


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = PROJECT_ROOT / "evaluation" / "results"
QUEUE_PATH = review.DEFAULT_QUEUE_PATH
DECISIONS_PATH = review.DEFAULT_DECISIONS_PATH
SOURCE_PATH = review.DEFAULT_SOURCE_PATH
DEFAULT_REPORT_DIRECTORY = RESULTS_ROOT / "oasst1-approved-label-inventory-v0.1"
PILOT_SIZE = 24
PILOT_INITIAL_PER_TOPIC = 2
PILOT_EXTRA_TOPICS = (
    "explanation_and_instruction",
    "context_and_memory",
    "casual_dialogue",
)
RAW_CONTENT_KEYS = frozenset({"text", "turns", "conversation", "message", "user", "assistant"})


class OASST1LabelInventoryError(ValueError):
    """Останавливает инвентаризацию при несогласованной queue или ledger."""


def audit_input_paths() -> tuple[Path, ...]:
    """Перечисляет защищённые входы для read-only проверки."""

    return QUEUE_PATH, DECISIONS_PATH, SOURCE_PATH


def _sha256(path: Path) -> str:
    """Связывает отчёт с неизменной версией входного файла."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise OASST1LabelInventoryError("oasst1_label_inventory_input_unreadable") from error


def _has_raw_content(value: Any) -> bool:
    """Не пропускает в отчёт текст диалогов или их структурные поля."""

    if isinstance(value, Mapping):
        if RAW_CONTENT_KEYS.intersection(value):
            return True
        return any(_has_raw_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_raw_content(item) for item in value)
    return False


def _pilot_sort_key(item: Mapping[str, Any]) -> tuple[str, str]:
    """Даёт стабильный порядок внутри topic без зависимости от очереди."""

    identifier = str(item["id"])
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest(), identifier


def _select_train_label_pilot(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Сначала берёт разные topics, затем добавляет ключевые пробелы."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["primary_candidate_topic"], []).append(item)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for topic in sorted(grouped):
        for item in sorted(grouped[topic], key=_pilot_sort_key)[:PILOT_INITIAL_PER_TOPIC]:
            selected.append(item)
            selected_ids.add(item["id"])
    remaining = {
        topic: [
            item
            for item in sorted(grouped.get(topic, []), key=_pilot_sort_key)
            if item["id"] not in selected_ids
        ]
        for topic in PILOT_EXTRA_TOPICS
    }
    while len(selected) < PILOT_SIZE and any(remaining.values()):
        for topic in PILOT_EXTRA_TOPICS:
            if len(selected) >= PILOT_SIZE:
                break
            if remaining[topic]:
                item = remaining[topic].pop(0)
                selected.append(item)
                selected_ids.add(item["id"])
    if len(selected) != PILOT_SIZE:
        raise OASST1LabelInventoryError("oasst1_label_inventory_pilot_size_unavailable")
    return sorted(selected, key=lambda item: (item["primary_candidate_topic"], item["id"]))


def build_label_inventory() -> dict[str, Any]:
    """Перепроверяет существующий review и составляет план label-пилота."""

    try:
        queue = review.load_review_queue(QUEUE_PATH)
        decisions = review.load_review_decisions(DECISIONS_PATH, queue)
        summary = review.build_review_summary(queue, decisions)
        # Валидатор читает source только в память и сверяет ID/hash/split.
        source_records = review.load_source_records_for_queue(queue, SOURCE_PATH)
    except review.OASST1HumanReviewError as error:
        raise OASST1LabelInventoryError("oasst1_label_inventory_review_invalid") from error
    source_sha256 = _sha256(SOURCE_PATH)
    if (
        queue.get("source_sha256") != source_sha256
        or summary["queue_selected_records"] != 104
        or summary["reviewed_records"] != 104
        or summary["approved_records"] != 97
        or summary["rejected_records"] != 7
        or summary["unreviewed_records"] != 0
        or summary["training_candidate_allowed"] is not False
        or summary["promotion_allowed"] is not False
        or len(source_records) != 104
        or queue.get("candidate_labels_are_unverified") is not True
    ):
        raise OASST1LabelInventoryError("oasst1_label_inventory_review_state_changed")

    decision_by_id = {item["candidate_id"]: item for item in decisions}
    approved = [
        item
        for item in queue["items"]
        if decision_by_id[item["id"]]["decision"] == "approved"
    ]
    train_approved = [item for item in approved if item["split"] == "train"]
    split_counts = Counter(item["split"] for item in approved)
    topic_counts = Counter(item["primary_candidate_topic"] for item in approved)
    train_topic_counts = Counter(item["primary_candidate_topic"] for item in train_approved)
    if split_counts != {"train": 75, "validation": 7, "test": 15}:
        raise OASST1LabelInventoryError("oasst1_label_inventory_split_counts_changed")
    pilot = _select_train_label_pilot(train_approved)

    report = {
        "format_version": 1,
        "mode": "oasst1_approved_semantic_label_inventory",
        "source_id": queue["source_id"],
        "inputs": {
            "source_sha256": source_sha256,
            "queue_sha256": _sha256(QUEUE_PATH),
            "decisions_sha256": _sha256(DECISIONS_PATH),
        },
        "existing_human_review": {
            "reviewed_records": 104,
            "approved_records": 97,
            "rejected_records": 7,
            "unreviewed_records": 0,
            "approved_by_split": dict(sorted(split_counts.items())),
            "approved_by_proposed_primary_topic": dict(sorted(topic_counts.items())),
            "train_approved_by_proposed_primary_topic": dict(sorted(train_topic_counts.items())),
            "topic_labels_human_confirmed": False,
        },
        "label_review_pilot": {
            "selected_records": len(pilot),
            "split": "train",
            "selection_rule": "up_to_two_hash_ordered_approved_train_ids_per_proposed_topic_then_extra_explanation_context_casual",
            "items": [
                {
                    "id": item["id"],
                    "content_sha256": item["content_sha256"],
                    "split": item["split"],
                    "proposed_primary_topic": item["primary_candidate_topic"],
                    "human_topic_verdict": "pending",
                }
                for item in pilot
            ],
            "not_a_statistical_quality_estimate": True,
        },
        "boundaries": {
            "raw_dialogue_content_in_report": False,
            "existing_human_verdicts_modified": False,
            "source_or_active_manifest_modified": False,
            "validation_or_test_reclassified_as_train": False,
            "training_started": False,
            "promotion_allowed": False,
        },
        "next_gate": "Human-confirm or reject proposed topic labels for pilot IDs without changing original OASST1 verdicts; then decide whether wider relabeling is justified. No training rights follow from this inventory.",
    }
    if _has_raw_content(report):
        raise OASST1LabelInventoryError("oasst1_label_inventory_not_metadata_only")
    return report


def inventory_markdown(report: Mapping[str, Any]) -> str:
    """Объясняет пилот и границы без показа чужих реплик."""

    human = report["existing_human_review"]
    pilot = report["label_review_pilot"]
    return "\n".join(
        [
            "# Brain v3 — OASST1 approved label inventory",
            "",
            "## Что подтверждено",
            "",
            f"- Existing human review: `{human['reviewed_records']}/104`; approved `{human['approved_records']}`, rejected `{human['rejected_records']}`.",
            f"- Approved по исходному split: {human['approved_by_split']}.",
            "- Проверены исходные ID, content SHA-256, split и source SHA-256.",
            "- Human review касался качества реплик, не истинности proposed topic labels.",
            "",
            "## Пилот подтверждения меток",
            "",
            f"- Отобрано `{pilot['selected_records']}` только approved train ID; validation/test не используются для доразметки train.",
            "- Полный metadata-only список ID и proposed topics находится в JSON-отчёте.",
            "- Сначала по возможности два ID на каждый proposed topic, затем дополнительные",
            "  примеры объяснения, контекста и обычной беседы. Выбор стабилен по hash ID.",
            "- Это навигационная выборка, а не статистическая оценка качества всей OASST1.",
            "- Ни одна proposed label пока не стала подтверждённой; решения о метках",
            "  не записывались автоматически.",
            "",
            "## Граница и следующий шаг",
            "",
            "Нужен отдельный human label-review workflow: читать полный диалог по одному",
            "ID, подтвердить или снять proposed topic, сохранить metadata verdict.",
            "До него нельзя менять исходные метки, active corpus или запускать обучение.",
            "",
        ]
    )


def write_inventory(
    report: Mapping[str, Any],
    directory: Path = DEFAULT_REPORT_DIRECTORY,
    *,
    results_root: Path = RESULTS_ROOT,
) -> None:
    """Создаёт новую immutable metadata-only папку внутри results."""

    destination = directory.expanduser().resolve()
    root = results_root.expanduser().resolve()
    try:
        destination.relative_to(root)
    except ValueError as error:
        raise OASST1LabelInventoryError("oasst1_label_inventory_output_outside_results") from error
    if destination.exists():
        raise OASST1LabelInventoryError("oasst1_label_inventory_report_exists")
    if _has_raw_content(report):
        raise OASST1LabelInventoryError("oasst1_label_inventory_not_metadata_only")
    try:
        destination.mkdir(parents=True)
        (destination / "oasst1_approved_label_inventory.json").write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (destination / "OASST1_APPROVED_LABEL_INVENTORY.md").write_text(
            inventory_markdown(report), encoding="utf-8"
        )
    except OSError as error:
        raise OASST1LabelInventoryError("oasst1_label_inventory_write_failed") from error


def main() -> None:
    """Запускает только read-only инвентаризацию по явному --audit."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", required=True)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    args = parser.parse_args()
    try:
        report = build_label_inventory()
        write_inventory(report, args.report_directory)
    except OASST1LabelInventoryError as error:
        raise SystemExit(f"OASST1 label inventory остановлена: {error}") from error
    print(
        json.dumps(
            {
                "approved_records": report["existing_human_review"]["approved_records"],
                "pilot_records": report["label_review_pilot"]["selected_records"],
                "training_started": report["boundaries"]["training_started"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
