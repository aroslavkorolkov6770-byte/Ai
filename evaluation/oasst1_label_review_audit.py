"""Фиксирует итог human topic review OASST1 без публикации или обучения."""

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
    LabelReviewPaths,
    OASST1LabelReviewError,
    load_review_snapshot,
)


RESULTS_ROOT = quality.RESULTS_ROOT
DEFAULT_REPORT_DIRECTORY = RESULTS_ROOT / "oasst1-approved-label-review-audit-v0.1"
RAW_CONTENT_KEYS = frozenset({"text", "turns", "conversation", "message", "user", "assistant"})


class OASST1LabelReviewAuditError(ValueError):
    """Не создаёт отчёт при неполном или изменившемся review."""


def _sha256(path: Path) -> str:
    """Связывает immutable отчёт с точными входами."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise OASST1LabelReviewAuditError("oasst1_label_review_audit_input_unreadable") from error


def _contains_raw_content(value: Any) -> bool:
    """Не допускает исходные реплики в JSON и Markdown отчёта."""

    if isinstance(value, Mapping):
        return bool(RAW_CONTENT_KEYS.intersection(value)) or any(
            _contains_raw_content(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_raw_content(item) for item in value)
    return False


def build_label_review_audit(paths: LabelReviewPaths = DEFAULT_PATHS) -> dict[str, Any]:
    """Подтверждает завершение 24 решений и агрегирует исправленные темы."""

    try:
        inventory, decisions = load_review_snapshot(paths)
    except OASST1LabelReviewError as error:
        raise OASST1LabelReviewAuditError("oasst1_label_review_audit_review_invalid") from error
    pilot = inventory["label_review_pilot"]
    if len(decisions) != pilot["selected_records"] or len(decisions) != 24:
        raise OASST1LabelReviewAuditError("oasst1_label_review_audit_incomplete")
    by_id = {item["candidate_id"]: item for item in decisions}
    if len(by_id) != 24:
        raise OASST1LabelReviewAuditError("oasst1_label_review_audit_duplicate")
    counts = Counter(item["topic_verdict"] for item in decisions)
    transition_counts = Counter(
        (item["proposed_primary_topic"], item["corrected_topic"])
        for item in decisions
        if item["topic_verdict"] == "corrected"
    )
    final_topics = Counter(
        item["corrected_topic"] if item["topic_verdict"] == "corrected"
        else item["proposed_primary_topic"]
        for item in decisions
        if item["topic_verdict"] != "rejected"
    )
    items = []
    for pilot_item in pilot["items"]:
        decision = by_id[pilot_item["id"]]
        final_topic = (
            None if decision["topic_verdict"] == "rejected"
            else decision["corrected_topic"] if decision["topic_verdict"] == "corrected"
            else decision["proposed_primary_topic"]
        )
        items.append({
            "id": pilot_item["id"],
            "content_sha256": pilot_item["content_sha256"],
            "split": "train",
            "proposed_primary_topic": pilot_item["proposed_primary_topic"],
            "topic_verdict": decision["topic_verdict"],
            "human_reviewed_primary_topic": final_topic,
        })
    report = {
        "format_version": 1,
        "mode": "oasst1_approved_train_primary_topic_completed_review_audit",
        "source_id": inventory["source_id"],
        "inputs": {
            "source_sha256": _sha256(paths.source_path),
            "quality_queue_sha256": _sha256(paths.queue_path),
            "quality_decisions_sha256": _sha256(quality.DEFAULT_DECISIONS_PATH),
            "pilot_inventory_sha256": _sha256(paths.inventory_path),
            "topic_decisions_sha256": _sha256(paths.decisions_path),
        },
        "pilot": {
            "selected_records": 24,
            "reviewed_records": len(decisions),
            "approved_train_records": inventory["existing_human_review"]["approved_by_split"]["train"],
            "approved_train_records_outside_pilot": (
                inventory["existing_human_review"]["approved_by_split"]["train"] - len(decisions)
            ),
            "split": "train",
            "verdict_counts": {key: counts.get(key, 0) for key in ("confirmed", "corrected", "rejected")},
            "correction_transitions": [
                {"from_topic": old, "to_topic": new, "records": count}
                for (old, new), count in sorted(transition_counts.items())
            ],
            "human_reviewed_final_topic_counts": dict(sorted(final_topics.items())),
            "items": items,
            "not_a_population_quality_estimate": True,
        },
        "boundaries": {
            "raw_dialogue_content_in_report": False,
            "labels_confirmed_outside_pilot": False,
            "validation_or_test_reclassified_as_train": False,
            "quality_ledger_modified": False,
            "active_corpus_modified": False,
            "staging_modified": False,
            "training_started": False,
            "promotion_allowed": False,
        },
        "next_gate": (
            "Review whether corrections justify a wider train-only label audit; "
            "check third-party quotations, provenance and licensing before any corpus use. "
            "Medical hold and full raw corpus audit remain open."
        ),
    }
    for report_key, inventory_key in (
        ("source_sha256", "source_sha256"),
        ("quality_queue_sha256", "queue_sha256"),
        ("quality_decisions_sha256", "decisions_sha256"),
    ):
        if report["inputs"][report_key] != inventory["inputs"][inventory_key]:
            raise OASST1LabelReviewAuditError("oasst1_label_review_audit_input_drift")
    if _contains_raw_content(report):
        raise OASST1LabelReviewAuditError("oasst1_label_review_audit_not_metadata_only")
    return report


def audit_markdown(report: Mapping[str, Any]) -> str:
    """Даёт человеку короткую сводку без текста исходных диалогов."""

    pilot = report["pilot"]
    counts = pilot["verdict_counts"]
    lines = [
        "# Brain v3 — аудит проверки тем OASST1, пилот v0.1",
        "",
        "## Итог",
        "",
        f"- Проверено `{pilot['reviewed_records']}/{pilot['selected_records']}` approved train-диалогов.",
        f"- Предложенная тема подтверждена: `{counts['confirmed']}`; исправлена: `{counts['corrected']}`; отклонена без замены: `{counts['rejected']}`.",
        f"- {counts['corrected']} исправлений показывают, что эвристические метки нельзя принимать",
        "  автоматически. Это намеренная pilot-выборка, не оценка точности всех",
        f"  {pilot['approved_train_records']} approved train-записей или полного OASST1.",
        "",
        "## Исправления темы",
        "",
    ]
    for transition in pilot["correction_transitions"]:
        lines.append(
            f"- `{transition['from_topic']}` → `{transition['to_topic']}`: `{transition['records']}`."
        )
    lines.extend([
        "",
        "## Граница",
        "",
        "Отчёт содержит только ID, hashes и метки, без исходных реплик.",
        "Сверены hashes source, quality queue/decisions, inventory и topic ledger.",
        "Validation/test не переименованы и не перенесены в train.",
        f"Остальные {pilot['approved_train_records_outside_pilot']} approved train-записей не имеют подтверждённой темы.",
        "Никакой training snapshot или promotion этим аудитом не разрешены.",
        "",
        "## Дальше",
        "",
        "Проверить, насколько ошибки меток типичны для оставшейся обучающей",
        "части, и отдельно оценить сторонние цитаты/лицензии. До обучения",
        "сохраняются полный raw corpus audit и медицинский hold v1.6-006.",
        "",
    ])
    return "\n".join(lines)


def write_audit(
    report: Mapping[str, Any], directory: Path = DEFAULT_REPORT_DIRECTORY,
    *, results_root: Path = RESULTS_ROOT,
) -> None:
    """Создаёт новую immutable папку отчёта только внутри results."""

    destination = directory.expanduser().resolve()
    try:
        destination.relative_to(results_root.expanduser().resolve())
    except ValueError as error:
        raise OASST1LabelReviewAuditError("oasst1_label_review_audit_output_outside_results") from error
    if destination.exists():
        raise OASST1LabelReviewAuditError("oasst1_label_review_audit_report_exists")
    if _contains_raw_content(report):
        raise OASST1LabelReviewAuditError("oasst1_label_review_audit_not_metadata_only")
    try:
        destination.mkdir(parents=True)
        (destination / "oasst1_label_review_audit.json").write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (destination / "OASST1_LABEL_REVIEW_AUDIT.md").write_text(
            audit_markdown(report), encoding="utf-8"
        )
    except OSError as error:
        raise OASST1LabelReviewAuditError("oasst1_label_review_audit_write_failed") from error


def main() -> None:
    """Создаёт отчёт только по явной команде --audit."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", required=True)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    args = parser.parse_args()
    try:
        report = build_label_review_audit()
        write_audit(report, args.report_directory)
    except OASST1LabelReviewAuditError as error:
        raise SystemExit(f"OASST1 label review audit остановлен: {error}") from error
    print(json.dumps({"reviewed_records": report["pilot"]["reviewed_records"],
                      "verdict_counts": report["pilot"]["verdict_counts"],
                      "training_started": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
