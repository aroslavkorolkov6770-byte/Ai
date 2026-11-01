"""Completed-аудит наблюдений OASST2 и read-only проверка материала."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from ai_core.brain_v3_corpus import DEFAULT_MANIFEST, file_sha256
from evaluation import oasst2_quality_review as review
from evaluation.oasst1_material_triage import material_attention_codes, PRIORITY_SUFFIXES, _contains_raw_content
from evaluation.prepare_oasst2_pilot import validate_unchanged_inputs

RESULTS_ROOT = review.ROOT / "evaluation/results"
DEFAULT_OUTPUT = RESULTS_ROOT / "oasst2-quality-completed-audit-v0.1"
POLICY = review.ROOT / "data/brain_v3/dialogue_coverage_policy.v1.json"


def input_hashes(paths: review.ReviewPaths) -> dict:
    """Связывает вывод с исходниками и решениями, не копирует их текст."""
    return {key: file_sha256(path) for key, path in {
        "candidate": paths.candidate, "pilot_report": paths.report,
        "queue": paths.queue, "decisions": paths.ledger,
        "active_manifest": DEFAULT_MANIFEST, "coverage_policy": POLICY,
    }.items()}


def summarise(queue: dict, records: dict, rows: list[dict]) -> dict:
    """Полное покрытие очереди обязательно; остаток никогда не одобряется."""
    sample_ids = {item["id"] for item in queue["items"]}
    row_ids = [row["candidate_id"] for row in rows]
    if len(row_ids) != len(set(row_ids)) or set(row_ids) != sample_ids:
        raise review.ReviewError("completed_review_required")
    for row in rows:
        review.validate_verdict(row["verdict"], row["reason_code"])
    metadata = {item["id"]: item for item in queue["items"]}
    acceptable = {row["candidate_id"] for row in rows if row["verdict"] == "acceptable"}
    excluded = {row["candidate_id"] for row in rows if row["verdict"] == "exclude"}
    held = {row["candidate_id"] for row in rows if row["verdict"] == "needs_check"}
    # Исключение привязано к семье: смена ветви не отменяет решение человека.
    excluded_families = {metadata[identifier]["family_sha256"] for identifier in excluded}
    all_items = []
    counts = Counter()
    for identifier, record in sorted(records.items()):
        codes = material_attention_codes(record)
        group = "priority_signal" if any(code.endswith(tuple(PRIORITY_SUFFIXES)) for code in codes) else (
            "length_only" if codes else "no_signal")
        quality_state = "acceptable_observation" if identifier in acceptable else (
            "excluded" if identifier in excluded else "needs_check" if identifier in held else "pending")
        if identifier not in excluded:
            counts[group] += 1
        all_items.append({"id": identifier, "content_sha256": record["content_sha256"],
                          "split": record["split"], "quality_state": quality_state,
                          "material_attention_codes": codes, "material_group": group,
                          "material_review": "pending", "training_eligible": False})
    reason_counts = dict(sorted(Counter(row["reason_code"] for row in rows).items()))
    partitions = {}
    for split in ("train", "validation", "test"):
        subset = [item for item in all_items if item["split"] == split]
        partitions[split] = dict(sorted(Counter(item["quality_state"] for item in subset).items()))
    return {
        "format_version": 1, "mode": "oasst2_completed_quality_observations_audit",
        "population_records": len(records), "sample_records": len(sample_ids),
        "reviewed_records": len(rows), "unreviewed_records": len(records) - len(rows),
        "verdict_counts": dict(sorted(Counter(row["verdict"] for row in rows).items())),
        "reason_counts": reason_counts, "split_quality_counts": partitions,
        "excluded_ids": sorted(excluded), "excluded_family_hashes": sorted(excluded_families),
        "held_ids": sorted(held), "acceptable_observation_ids": sorted(acceptable),
        "quality_observed_train_ids": sorted(identifier for identifier in acceptable
                                               if records[identifier]["split"] == "train"),
        "material_triage": {"non_excluded_records": len(records) - len(excluded),
                            "group_counts": dict(sorted(counts.items())),
                            "excluded_with_no_automatic_signal": sorted(identifier for identifier in excluded
                                if not material_attention_codes(records[identifier])),
                            "all_items": all_items},
        "observations_are_fact_or_rights_verification": False,
        "material_signals_are_legal_conclusions": False,
        "sample_estimates_population_quality": False,
        "unreviewed_records_automatically_approved": False,
        "active_manifest_updated": False, "training_started": False,
        "promotion_allowed": False, "training_candidate_allowed": False,
        "full_active_preflight_required": True,
    }


def build_audit(paths: review.ReviewPaths = review.ReviewPaths()) -> dict:
    """Повторная сверка входов защищает от решения по изменённой выборке."""
    before = input_hashes(paths)
    queue, records = review.context(paths)
    rows = review.decisions(paths, queue)
    report = summarise(queue, records, rows)
    pilot, _ = review.load_inputs(paths)
    validate_unchanged_inputs(pilot["inputs"])
    after = input_hashes(paths)
    if before != after:
        raise review.ReviewError("audit_input_changed")
    return {**report, "input_sha256": before, "pilot_active_inputs_unchanged": True}


def markdown(report: dict) -> str:
    """В отчёт включаются только счётчики и ID, без исходных реплик."""
    lines = ["# OASST2: completed quality audit v0.1", "",
             f"Просмотрено: {report['reviewed_records']}/{report['sample_records']}.",
             f"Не просмотрено: {report['unreviewed_records']} из общего пилота {report['population_records']}.", "",
             "## Решения", ""]
    lines.extend(f"- {code}: {count}." for code, count in report["verdict_counts"].items())
    lines.extend(["", "## Состояние по split", ""])
    lines.extend(f"- {split}: {json.dumps(counts, ensure_ascii=False)}."
                 for split, counts in report["split_quality_counts"].items())
    lines.extend(["", "## Материалы", ""])
    lines.extend(f"- {group}: {count}." for group, count in report["material_triage"]["group_counts"].items())
    lines.extend(["", "Исключённые записи:", ""])
    lines.extend(f"- `{identifier}`." for identifier in report["excluded_ids"])
    lines.extend(["", "Эвристика не доказывает происхождение и не подтверждает права.",
                  "Человеческое исключение действует и при отсутствии автоматического сигнала.",
                  "Положительные наблюдения качества не заменяют проверку фактов/материалов.",
                  "Контрольные split не переходят в train; непросмотренный остаток остаётся pending.",
                  "Далее: материал и смысловая разметка quality-observed train; расширение проверки риска.",
                  "Полный active/holdout preflight всё ещё необходим. Обучение/promotion не запущены.", ""])
    return "\n".join(lines)


def write_audit(report: dict, destination: Path = DEFAULT_OUTPUT) -> None:
    """Новый immutable отчёт; повторный запуск не перезаписывает предыдущий."""
    destination = destination.resolve()
    try:
        destination.relative_to(RESULTS_ROOT.resolve())
    except ValueError as error:
        raise review.ReviewError("audit_output_outside_results") from error
    if destination.exists():
        raise review.ReviewError("audit_output_exists")
    if _contains_raw_content(report):
        raise review.ReviewError("audit_raw_content_forbidden")
    destination.mkdir(parents=True)
    review.atomic_json(destination / "oasst2_quality_audit.json", report, exclusive=True)
    with (destination / "OASST2_QUALITY_AUDIT.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown(report))


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", required=True)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        report = build_audit()
        write_audit(report, args.report_directory)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f"OASST2 audit stopped: {error}") from error
    print(json.dumps({key: report[key] for key in
                      ("reviewed_records", "unreviewed_records", "verdict_counts", "training_started")}))


if __name__ == "__main__":
    main()
