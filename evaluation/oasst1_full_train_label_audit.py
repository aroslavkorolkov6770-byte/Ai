"""Аудит двух завершённых партий тем OASST1 и сигналов для проверки цитат.

Тексты диалогов читаются только в памяти. Отчёт содержит ID, hashes, метки и
фиксированные сигналы; он не формирует training snapshot и не решает вопросы
авторских прав автоматически.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from evaluation import oasst1_human_review as quality
from evaluation.oasst1_label_review import (
    DEFAULT_PATHS,
    REMAINING_PATHS,
    OASST1LabelReviewError,
    load_review_snapshot,
)
from evaluation.oasst1_label_review_audit import build_label_review_audit
from evaluation.oasst1_remaining_label_inventory import quote_attention_codes


RESULTS_ROOT = quality.RESULTS_ROOT
PILOT_AUDIT_PATH = (
    RESULTS_ROOT / "oasst1-approved-label-review-audit-v0.1" / "oasst1_label_review_audit.json"
)
DEFAULT_REPORT_DIRECTORY = RESULTS_ROOT / "oasst1-full-train-label-audit-v0.2"
RAW_CONTENT_KEYS = frozenset({"text", "turns", "conversation", "message", "user", "assistant"})


class OASST1FullTrainLabelAuditError(ValueError):
    """Не объявляет audit завершённым при несогласованных входах."""


def _sha256(path: Path) -> str:
    """Связывает отчёт с текущими байтами исходного файла."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_input_unreadable") from error


def _contains_raw_content(value: Any) -> bool:
    """Запрещает записывать реплики и поля raw dialogue в отчёт."""

    if isinstance(value, Mapping):
        return bool(RAW_CONTENT_KEYS.intersection(value)) or any(
            _contains_raw_content(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_raw_content(item) for item in value)
    return False


def _load_saved_pilot_audit() -> dict[str, Any]:
    """Требует совпадения старого immutable audit с пересчитанным состоянием."""

    try:
        saved = json.loads(PILOT_AUDIT_PATH.read_text(encoding="utf-8"))
        current = build_label_review_audit()
    except (OSError, ValueError) as error:
        raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_pilot_invalid") from error
    if not isinstance(saved, dict) or saved != current:
        raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_pilot_drift")
    return saved


def build_full_train_label_audit() -> dict[str, Any]:
    """Сверяет 24+51 решений, split и source, затем агрегирует metadata."""

    saved_pilot = _load_saved_pilot_audit()
    try:
        pilot_inventory, pilot_decisions = load_review_snapshot(DEFAULT_PATHS)
        remaining_inventory, remaining_decisions = load_review_snapshot(REMAINING_PATHS)
        queue = quality.load_review_queue(quality.DEFAULT_QUEUE_PATH)
        quality_decisions = quality.load_review_decisions(quality.DEFAULT_DECISIONS_PATH, queue)
        source = quality.load_source_records_for_queue(queue, quality.DEFAULT_SOURCE_PATH)
    except (OASST1LabelReviewError, quality.OASST1HumanReviewError) as error:
        raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_review_invalid") from error
    if (
        len(pilot_decisions) != 24
        or len(remaining_decisions) != 51
        or saved_pilot["pilot"]["reviewed_records"] != 24
    ):
        raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_incomplete")
    queue_by_id = {item["id"]: item for item in queue["items"]}
    pilot_ids = {item["candidate_id"] for item in pilot_decisions}
    remaining_ids = {item["candidate_id"] for item in remaining_decisions}
    approved_train_ids = {
        item["candidate_id"] for item in quality_decisions
        if item["decision"] == "approved"
        and queue_by_id[item["candidate_id"]]["split"] == "train"
    }
    if (
        len(pilot_ids) != 24
        or len(remaining_ids) != 51
        or pilot_ids.intersection(remaining_ids)
        or len(approved_train_ids) != 75
        or pilot_ids.union(remaining_ids) != approved_train_ids
    ):
        raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_partition_invalid")
    decisions = pilot_decisions + remaining_decisions
    verdict_counts = Counter(item["topic_verdict"] for item in decisions)
    transition_counts = Counter(
        (item["proposed_primary_topic"], item["corrected_topic"])
        for item in decisions if item["topic_verdict"] == "corrected"
    )
    proposed_counts = Counter(item["proposed_primary_topic"] for item in decisions)
    confirmed_counts = Counter(
        item["proposed_primary_topic"] for item in decisions
        if item["topic_verdict"] == "confirmed"
    )
    final_topic_counts = Counter(
        item["corrected_topic"] if item["topic_verdict"] == "corrected"
        else item["proposed_primary_topic"]
        for item in decisions if item["topic_verdict"] != "rejected"
    )
    reviewed_items = []
    attention_items = []
    for decision in sorted(decisions, key=lambda item: item["candidate_id"]):
        identifier = decision["candidate_id"]
        queue_item = queue_by_id[identifier]
        if (
            decision["split"] != "train"
            or queue_item["content_sha256"] != decision["content_sha256"]
            or source[identifier]["content_sha256"] != decision["content_sha256"]
        ):
            raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_metadata_mismatch")
        batch = "pilot_v0.1" if identifier in pilot_ids else "remaining_train_v0.2"
        final_topic = (
            None if decision["topic_verdict"] == "rejected"
            else decision["corrected_topic"] if decision["topic_verdict"] == "corrected"
            else decision["proposed_primary_topic"]
        )
        reviewed_items.append({
            "id": identifier,
            "content_sha256": decision["content_sha256"],
            "split": "train",
            "batch_id": batch,
            "proposed_primary_topic": decision["proposed_primary_topic"],
            "topic_verdict": decision["topic_verdict"],
            "human_reviewed_primary_topic": final_topic,
        })
        codes = quote_attention_codes(source[identifier])
        if codes:
            attention_items.append({
                "id": identifier,
                "content_sha256": decision["content_sha256"],
                "batch_id": batch,
                "attention_codes": codes,
                "human_material_review_status": "pending",
            })
    attention_counts = Counter(code for item in attention_items for code in item["attention_codes"])
    report = {
        "format_version": 1,
        "mode": "oasst1_all_approved_train_primary_topic_completed_review_audit",
        "source_id": pilot_inventory["source_id"],
        "inputs": {
            "source_sha256": _sha256(quality.DEFAULT_SOURCE_PATH),
            "quality_queue_sha256": _sha256(quality.DEFAULT_QUEUE_PATH),
            "quality_decisions_sha256": _sha256(quality.DEFAULT_DECISIONS_PATH),
            "pilot_inventory_sha256": _sha256(DEFAULT_PATHS.inventory_path),
            "pilot_decisions_sha256": _sha256(DEFAULT_PATHS.decisions_path),
            "pilot_completed_audit_sha256": _sha256(PILOT_AUDIT_PATH),
            "remaining_inventory_sha256": _sha256(REMAINING_PATHS.inventory_path),
            "remaining_decisions_sha256": _sha256(REMAINING_PATHS.decisions_path),
        },
        "review": {
            "approved_train_records": 75,
            "reviewed_primary_topics": len(decisions),
            "pilot_records": len(pilot_decisions),
            "remaining_batch_records": len(remaining_decisions),
            "verdict_counts": {key: verdict_counts.get(key, 0) for key in ("confirmed", "corrected", "rejected")},
            "proposed_topic_counts": dict(sorted(proposed_counts.items())),
            "confirmed_proposed_topic_counts": dict(sorted(confirmed_counts.items())),
            "human_reviewed_final_topic_counts": dict(sorted(final_topic_counts.items())),
            "correction_transitions": [
                {"from_topic": old, "to_topic": new, "records": count}
                for (old, new), count in sorted(transition_counts.items())
            ],
            "items": reviewed_items,
            "not_a_population_quality_estimate": True,
        },
        "material_attention": {
            "records_with_any_signal": len(attention_items),
            "code_counts": dict(sorted(attention_counts.items())),
            "items": attention_items,
            "heuristic_only": True,
            "not_a_copyright_or_license_verdict": True,
        },
        "boundaries": {
            "raw_dialogue_content_in_report": False,
            "validation_or_test_reclassified_as_train": False,
            "quality_or_topic_ledgers_modified": False,
            "active_corpus_modified": False,
            "staging_modified": False,
            "training_started": False,
            "promotion_allowed": False,
        },
        "next_gate": (
            "Human-check flagged material and a sample without signals for quotations, provenance "
            "and actual reuse rights. Full raw corpus/duplicates audit and medical hold remain open. "
            "No automatic training or promotion."
        ),
    }
    expected_hashes = (
        (report["inputs"]["source_sha256"], pilot_inventory["inputs"]["source_sha256"]),
        (report["inputs"]["source_sha256"], remaining_inventory["inputs"]["source_sha256"]),
        (report["inputs"]["quality_queue_sha256"], remaining_inventory["inputs"]["queue_sha256"]),
        (report["inputs"]["quality_decisions_sha256"], remaining_inventory["inputs"]["decisions_sha256"]),
        (report["inputs"]["pilot_decisions_sha256"], saved_pilot["inputs"]["topic_decisions_sha256"]),
        (report["inputs"]["pilot_completed_audit_sha256"], remaining_inventory["inputs"]["completed_pilot_audit_sha256"]),
    )
    if any(actual != expected for actual, expected in expected_hashes):
        raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_input_drift")
    if _contains_raw_content(report):
        raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_not_metadata_only")
    return report


def audit_markdown(report: Mapping[str, Any]) -> str:
    """Пишет короткий итог без реплик и без ложной оценки лицензии."""

    review = report["review"]
    counts = review["verdict_counts"]
    attention = report["material_attention"]
    lines = [
        "# Brain v3 — все approved train OASST1: аудит тем v0.2",
        "",
        "## Смысловые метки",
        "",
        f"- Проверено `{review['reviewed_primary_topics']}/{review['approved_train_records']}` approved train-ID: "
        f"`{review['pilot_records']}` в первом пилоте и `{review['remaining_batch_records']}` во второй партии.",
        f"- Предложенная тема подтверждена у `{counts['confirmed']}`, исправлена у `{counts['corrected']}`, "
        f"отклонена без замены у `{counts['rejected']}`.",
        "- Это не оценка точности на всём OASST1 и не проверка качества",
        "  validation/test. Исправленные темы записаны отдельно; active corpus",
        "  не переписывался.",
        "",
        "## Сигналы для проверки материалов",
        "",
        f"- У `{attention['records_with_any_signal']}` из 75 train-записей есть хотя бы один сигнал.",
    ]
    for code, count in attention["code_counts"].items():
        lines.append(f"- `{code}`: `{count}` записей.")
    lines.extend([
        "",
        "Это только сортировка для чтения человеком: длинный ответ или кавычки",
        "не доказывают заимствование, а отсутствие сигнала не доказывает",
        "безопасность. ID и codes в JSON; исходных реплик и URL в отчёте нет.",
        "",
        "## Граница и следующий шаг",
        "",
        "Все входные SHA-256 связаны с immutable отчётом. Validation/test",
        "остались в своих split. До отдельной проверки цитат, происхождения и",
        "прав использования, полного raw corpus/duplicate audit и медицинского",
        "hold v1.6-006 training snapshot и promotion не разрешены.",
        "",
    ])
    return "\n".join(lines)


def write_audit(
    report: Mapping[str, Any], directory: Path = DEFAULT_REPORT_DIRECTORY,
    *, results_root: Path = RESULTS_ROOT,
) -> None:
    """Создаёт новую immutable metadata-only папку внутри results."""

    destination = directory.expanduser().resolve()
    try:
        destination.relative_to(results_root.expanduser().resolve())
    except ValueError as error:
        raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_output_outside_results") from error
    if destination.exists():
        raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_report_exists")
    if _contains_raw_content(report):
        raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_not_metadata_only")
    try:
        destination.mkdir(parents=True)
        (destination / "oasst1_full_train_label_audit.json").write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (destination / "OASST1_FULL_TRAIN_LABEL_AUDIT.md").write_text(
            audit_markdown(report), encoding="utf-8"
        )
    except OSError as error:
        raise OASST1FullTrainLabelAuditError("oasst1_full_train_audit_write_failed") from error


def main() -> None:
    """Запускает read-only проверку и отдельный отчёт по явному --audit."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", required=True)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    args = parser.parse_args()
    try:
        report = build_full_train_label_audit()
        write_audit(report, args.report_directory)
    except OASST1FullTrainLabelAuditError as error:
        raise SystemExit(f"OASST1 full train audit остановлен: {error}") from error
    print(json.dumps({"reviewed_records": report["review"]["reviewed_primary_topics"],
                      "verdict_counts": report["review"]["verdict_counts"],
                      "material_attention_records": report["material_attention"]["records_with_any_signal"],
                      "training_started": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
