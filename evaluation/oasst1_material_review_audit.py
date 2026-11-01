"""Аудит наблюдений о фрагментах OASST1 с явным статусом незавершённой очереди.

Читает проверенные metadata и создаёт отдельный отчёт. Даже полная выборка
не устанавливает права на исходные тексты и не даёт допуск к обучению.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from evaluation.oasst1_material_review import (
    DEFAULT_PATHS,
    MaterialReviewPaths,
    OASST1MaterialReviewError,
    VERDICTS,
    load_review_snapshot,
)
from evaluation.oasst1_material_triage import (
    FULL_AUDIT_PATH,
    RESULTS_ROOT,
    _contains_raw_content,
)


DEFAULT_REPORT_DIRECTORY = RESULTS_ROOT / "oasst1-material-review-audit-v0.1-pending"


class OASST1MaterialReviewAuditError(ValueError):
    """Останавливает аудит при drift или неверных решениях."""


def _input_hashes(paths: MaterialReviewPaths) -> dict[str, str | None]:
    """Отсутствующий ledger означает отсутствие решений, а не пустой hash."""

    inputs = {
        "source_sha256": paths.source_path,
        "quality_queue_sha256": paths.queue_path,
        "full_label_audit_sha256": FULL_AUDIT_PATH,
        "material_triage_sha256": paths.triage_path,
        "material_decisions_sha256": paths.decisions_path,
    }
    hashes: dict[str, str | None] = {}
    try:
        for name, path in inputs.items():
            if name == "material_decisions_sha256" and not path.exists():
                hashes[name] = None
            else:
                hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise OASST1MaterialReviewAuditError("oasst1_material_audit_input_unreadable") from error
    return hashes


def build_material_review_audit(
    paths: MaterialReviewPaths = DEFAULT_PATHS, *, require_complete: bool = False,
) -> dict[str, Any]:
    """Сверяет snapshot и показывает pending, hold и предложения исключения."""

    before = _input_hashes(paths)
    try:
        triage_report, decisions = load_review_snapshot(paths)
    except OASST1MaterialReviewError as error:
        raise OASST1MaterialReviewAuditError("oasst1_material_audit_review_invalid") from error
    if before != _input_hashes(paths):
        raise OASST1MaterialReviewAuditError("oasst1_material_audit_input_drift")
    for key in ("source_sha256", "quality_queue_sha256", "full_label_audit_sha256"):
        if before[key] != triage_report["inputs"][key]:
            raise OASST1MaterialReviewAuditError("oasst1_material_audit_input_drift")

    triage = triage_report["triage"]
    by_id = {item["candidate_id"]: item for item in decisions}
    sample_ids = triage["suggested_review_sample_ids"]
    complete = len(by_id) == len(sample_ids)
    if require_complete and not complete:
        raise OASST1MaterialReviewAuditError("oasst1_material_audit_incomplete")
    counts = Counter(item["verdict"] for item in decisions)
    all_by_id = {item["id"]: item for item in triage["all_items"]}
    items = []
    for identifier in sample_ids:
        item = all_by_id[identifier]
        decision = by_id.get(identifier)
        items.append({
            "id": identifier,
            "content_sha256": item["content_sha256"],
            "split": item["split"],
            "triage_group": item["triage_group"],
            "material_verdict": decision["verdict"] if decision else "pending",
            "reason_code": decision["reason_code"] if decision else None,
        })
    finding_count = counts["needs_source_check"] + counts["exclude_candidate"]
    state = (
        "incomplete" if not complete else
        "sample_complete_with_findings" if finding_count else
        "sample_complete_no_obvious_excerpt"
    )
    groups = {}
    for group in triage["group_counts"]:
        selected = [item for item in items if item["triage_group"] == group]
        reviewed = [item for item in selected if item["material_verdict"] != "pending"]
        groups[group] = {
            "population_records": triage["group_counts"][group],
            "selected_records": len(selected),
            "reviewed_records": len(reviewed),
            "pending_records": len(selected) - len(reviewed),
            "verdict_counts": {
                verdict: sum(item["material_verdict"] == verdict for item in reviewed)
                for verdict in sorted(VERDICTS)
            },
        }
    report = {
        "format_version": 1,
        "mode": "oasst1_approved_train_material_observations_audit",
        "source_id": triage_report["source_id"],
        "inputs": before,
        "review": {
            "state": state,
            "sample_complete": complete,
            "train_population_records": triage["train_records"],
            "sample_records": len(sample_ids),
            "reviewed_records": len(decisions),
            "pending_records": len(sample_ids) - len(decisions),
            "records_outside_sample": triage["train_records"] - len(sample_ids),
            "verdict_counts": {verdict: counts[verdict] for verdict in sorted(VERDICTS)},
            "groups": groups,
            "pending_ids": [item["id"] for item in items if item["material_verdict"] == "pending"],
            "needs_source_check_ids": [item["id"] for item in items if item["material_verdict"] == "needs_source_check"],
            "exclude_candidate_ids": [item["id"] for item in items if item["material_verdict"] == "exclude_candidate"],
            "items": items,
            "not_a_population_quality_estimate": True,
        },
        "boundaries": {
            "raw_dialogue_content_in_report": False,
            "record_level_rights_confirmed": False,
            "unsampled_records_automatically_accepted": False,
            "training_candidate_allowed": False,
            "promotion_allowed": False,
            "training_started": False,
            "source_or_ledgers_modified": False,
            "active_corpus_modified": False,
        },
        "next_gate": (
            "Complete pending observations, investigate source-check findings and "
            "proposed exclusions, then assess remaining records and full raw corpus "
            "duplicates/holdout. Medical hold v1.6-006 remains open."
        ),
    }
    if _contains_raw_content(report):
        raise OASST1MaterialReviewAuditError("oasst1_material_audit_not_metadata_only")
    return report


def audit_markdown(report: Mapping[str, Any]) -> str:
    """Описывает наблюдения и оставшийся объём без исходных реплик."""

    review = report["review"]
    counts = review["verdict_counts"]
    lines = [
        "# Brain v3 — аудит проверки материалов OASST1",
        "",
        f"Статус: `{review['state']}`. Проверено {review['reviewed_records']}/{review['sample_records']} выбранных диалогов.",
        f"Осталось в выборке: {review['pending_records']}; вне выборки: {review['records_outside_sample']} train-запись.",
        "",
        f"- Не видно явной вставки: {counts['no_obvious_excerpt']}.",
        f"- Нужна проверка источника: {counts['needs_source_check']}.",
        f"- Предложено исключение: {counts['exclude_candidate']}.",
        "",
        "| Группа | Всего | Выбрано | Прочитано | Осталось |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, group in review["groups"].items():
        lines.append(
            f"| {name} | {group['population_records']} | {group['selected_records']} | "
            f"{group['reviewed_records']} | {group['pending_records']} |"
        )
    lines.extend([
        "",
        "Решение «не видно явной вставки» фиксирует наблюдение человека.",
        "Оно не подтверждает права на текст. Остальные записи не приняты автоматически.",
        "Отчёт сверяет SHA-256 до и после чтения; содержит только ID и metadata.",
        "Training snapshot и promotion не разрешены этим отчётом.",
        "",
        "Далее: закончить чтение выборки, разобрать source-check и предложения",
        "исключения, проверить оставшиеся записи, raw corpus, повторы и holdout.",
        "Медицинский hold v1.6-006 остаётся открытым.",
        "",
    ])
    return "\n".join(lines)


def write_audit(
    report: Mapping[str, Any], directory: Path = DEFAULT_REPORT_DIRECTORY,
    *, results_root: Path = RESULTS_ROOT,
) -> None:
    """Сохраняет новый snapshot в results, не перезаписывая прошлый."""

    destination = directory.resolve()
    try:
        destination.relative_to(results_root.resolve())
    except ValueError as error:
        raise OASST1MaterialReviewAuditError("oasst1_material_audit_output_outside_results") from error
    if destination.exists():
        raise OASST1MaterialReviewAuditError("oasst1_material_audit_report_exists")
    if _contains_raw_content(report):
        raise OASST1MaterialReviewAuditError("oasst1_material_audit_not_metadata_only")
    try:
        destination.mkdir(parents=True)
        (destination / "oasst1_material_review_audit.json").write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (destination / "OASST1_MATERIAL_REVIEW_AUDIT.md").write_text(
            audit_markdown(report), encoding="utf-8",
        )
    except OSError as error:
        raise OASST1MaterialReviewAuditError("oasst1_material_audit_write_failed") from error


def main() -> None:
    """Записывает аудит только по явной команде; completed-режим требует 24 решения."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", required=True)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    args = parser.parse_args()
    try:
        report = build_material_review_audit(require_complete=args.require_complete)
        write_audit(report, args.report_directory)
    except OASST1MaterialReviewAuditError as error:
        raise SystemExit(f"Аудит материалов остановлен: {error}") from error
    print(json.dumps({
        "state": report["review"]["state"],
        "reviewed_records": report["review"]["reviewed_records"],
        "pending_records": report["review"]["pending_records"],
        "training_started": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
