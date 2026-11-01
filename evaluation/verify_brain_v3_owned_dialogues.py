"""Проверяет project-owned Brain v3 dialogue candidate v0.7 до promotion.

Скрипт выполняет три read-only шага: isolated staging audit, preflight с
active corpus через временные hard links и prospective dialogue coverage.
Он создаёт новые versioned JSON/Markdown отчёты, но не меняет raw, active
manifest, API, модельные веса, обучение или GitHub.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_core.brain_v3_owned_dialogues import (
    DEFAULT_CANDIDATE_PATH,
    RESULTS_ROOT,
    source_descriptor,
)
from evaluation.brain_v3_staging import (
    BrainV3StagingError,
    audit_staged_candidate,
    preflight_staged_candidate_against_active,
    prospective_dialogue_coverage_for_staged_candidate,
)


DEFAULT_AUDIT_REPORT = RESULTS_ROOT / "brain-v3-owned-dialogues-audit-v0.7"
DEFAULT_PREFLIGHT_REPORT = RESULTS_ROOT / "brain-v3-owned-dialogues-preflight-v0.7"
DEFAULT_COVERAGE_REPORT = RESULTS_ROOT / "brain-v3-owned-dialogues-prospective-coverage-v0.7"


def _write_new_report(
    directory: Path,
    *,
    json_name: str,
    markdown_name: str,
    payload: dict[str, Any],
    markdown: str,
) -> None:
    """Сохраняет один новый отчёт и отказывается перезаписывать историю."""
    destination = directory.expanduser().resolve()
    if destination.exists():
        raise BrainV3StagingError("Каталог owned dialogue verification report уже существует.")
    destination.mkdir(parents=True)
    (destination / json_name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (destination / markdown_name).write_text(markdown, encoding="utf-8")


def audit_markdown(report: dict[str, Any]) -> str:
    """Рендерит isolated audit без реплик candidate."""
    audit = report["audit"]
    return "\n".join(
        [
            "# Brain v3 — project-owned dialogues v0.7: independent staging audit",
            "",
            "Статус: candidate-only; active manifest, raw, API и обучение не изменялись.",
            f"Источник: `{report['source_id']}`; лицензия: `{report['license']}`.",
            f"SHA-256 candidate JSONL: `{report['candidate_sha256']}`.",
            f"Принято: {audit['records_accepted']}; отклонено: {audit['records_rejected']}; всего: {audit['records_total']}.",
            f"Split: {audit['accepted_by_split']}; диалоговых ходов: {audit['unique_dialogue_turns']}.",
            f"Причины отклонения: {audit['rejections']}.",
            f"Межsplit exact/near: {audit['cross_split_exact_duplicates']}/{audit['cross_split_near_duplicate_candidates']}.",
            f"Training allowed для isolated candidate: `{audit['training_allowed']}`; причины: {audit['training_block_reasons']}.",
            "",
            "Этот audit не означает promotion: candidate должен отдельно пройти active preflight, prospective coverage и ручную стратифицированную проверку качества.",
            "",
        ]
    )


def preflight_markdown(report: dict[str, Any]) -> str:
    """Рендерит active preflight без текстов source или candidate."""
    audit = report["audit"]
    return "\n".join(
        [
            "# Brain v3 — project-owned dialogues v0.7: active corpus preflight",
            "",
            "Статус: временная проверка через hard links; active manifest, raw, API и обучение не изменялись.",
            f"Проверено active sources: {report['active_sources_checked']}; candidate: `{report['candidate_source_id']}`.",
            f"SHA-256 candidate JSONL: `{report['candidate_sha256']}`.",
            f"Принято: {audit['records_accepted']}; отклонено: {audit['records_rejected']}; причины: {audit['rejections']}.",
            f"Межsplit exact/near: {audit['cross_split_exact_duplicates']}/{audit['cross_split_near_duplicate_candidates']}.",
            f"Чистое слияние: `{report['clean_merge']}`.",
            f"Training allowed для временного объединения: `{audit['training_allowed']}`; причины: {audit['training_block_reasons']}.",
            "",
            "Даже чистый preflight не переносит candidate в raw/ и не обновляет active manifest автоматически.",
            "",
        ]
    )


def coverage_markdown(report: dict[str, Any]) -> str:
    """Рендерит prospective coverage без исходных реплик и private context."""
    coverage = report["coverage"]
    observed = coverage["observed"]
    return "\n".join(
        [
            "# Brain v3 — project-owned dialogues v0.7: prospective dialogue coverage",
            "",
            "Статус: coverage рассчитано на временном active+candidate manifest через hard links.",
            "Ни raw, ни active manifest, ни API, ни обучение этим расчётом не изменялись.",
            f"Проверено active sources: {report['active_sources_checked']}; candidate: `{report['candidate_source_id']}`.",
            f"SHA-256 candidate JSONL: `{report['candidate_sha256']}`.",
            f"Dialogue records/turns после временного объединения: {observed['dialogue_records']}/{observed['dialogue_turns']}.",
            f"Multi-turn records/ratio: {observed['multi_turn_dialogue_records']}/{observed['multi_turn_dialogue_ratio']}.",
            f"Coverage observed/allowed: `{coverage['coverage_observed']}`/`{coverage['coverage_allowed']}`.",
            f"Generator training ready по техническим gates: `{coverage['generator_training_ready']}`.",
            f"Coverage blockers: {coverage['coverage_block_reasons']}.",
            f"Generator blockers: {coverage['generator_training_block_reasons']}.",
            "",
            "Эта метрика — не разрешение на запуск обучения: остаются ручная стратифицированная проверка, отдельное решение о local promotion и финальная training-config проверка.",
            "",
        ]
    )


def create_audit_report(candidate: Path, directory: Path) -> dict[str, Any]:
    """Сохраняет immutable isolated audit report v0.7."""
    report = audit_staged_candidate(candidate, source_descriptor())
    _write_new_report(
        directory,
        json_name="brain_v3_owned_dialogues_audit.json",
        markdown_name="BRAIN_V3_OWNED_DIALOGUES_AUDIT.md",
        payload=report,
        markdown=audit_markdown(report),
    )
    return report


def create_preflight_report(candidate: Path, directory: Path) -> dict[str, Any]:
    """Сохраняет immutable active preflight report v0.7."""
    report = preflight_staged_candidate_against_active(candidate, source_descriptor())
    _write_new_report(
        directory,
        json_name="brain_v3_owned_dialogues_preflight.json",
        markdown_name="BRAIN_V3_OWNED_DIALOGUES_PREFLIGHT.md",
        payload=report,
        markdown=preflight_markdown(report),
    )
    return report


def create_coverage_report(candidate: Path, directory: Path) -> dict[str, Any]:
    """Сохраняет immutable prospective coverage report v0.7."""
    report = prospective_dialogue_coverage_for_staged_candidate(candidate, source_descriptor())
    _write_new_report(
        directory,
        json_name="brain_v3_owned_dialogues_prospective_coverage.json",
        markdown_name="BRAIN_V3_OWNED_DIALOGUES_PROSPECTIVE_COVERAGE.md",
        payload=report,
        markdown=coverage_markdown(report),
    )
    return report


def main() -> None:
    """Выполняет все candidate-only проверки fixed owned-dialogue v0.7."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE_PATH)
    parser.add_argument("--audit-report", type=Path, default=DEFAULT_AUDIT_REPORT)
    parser.add_argument("--preflight-report", type=Path, default=DEFAULT_PREFLIGHT_REPORT)
    parser.add_argument("--coverage-report", type=Path, default=DEFAULT_COVERAGE_REPORT)
    args = parser.parse_args()
    destinations = (args.audit_report, args.preflight_report, args.coverage_report)
    if any(destination.expanduser().resolve().exists() for destination in destinations):
        raise SystemExit("Owned dialogue verification остановлена: один из versioned report-каталогов уже существует.")
    try:
        audit = create_audit_report(args.candidate, args.audit_report)
        preflight = create_preflight_report(args.candidate, args.preflight_report)
        coverage = create_coverage_report(args.candidate, args.coverage_report)
    except (BrainV3StagingError, OSError, ValueError) as error:
        raise SystemExit(f"Owned dialogue verification остановлена: {error}") from error
    print(
        json.dumps(
            {"audit": audit, "preflight": preflight, "prospective_coverage": coverage},
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
