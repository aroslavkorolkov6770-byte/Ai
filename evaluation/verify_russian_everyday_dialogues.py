"""Независимо проверяет Russian Everyday Dialogues staging до local promotion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_core.russian_everyday_dialogues import (
    DEFAULT_STAGING_ROOT,
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
)
from evaluation.brain_v3_staging import (
    BrainV3StagingError,
    StagedSourceDescriptor,
    audit_staged_candidate,
    preflight_staged_candidate_against_active,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATE = DEFAULT_STAGING_ROOT / "russian-everyday-dialogues-cc-by-v0.1.jsonl"
DEFAULT_AUDIT_REPORT = ROOT / "evaluation" / "results" / "russian-everyday-dialogues-audit-v0.1"
DEFAULT_PREFLIGHT_REPORT = ROOT / "evaluation" / "results" / "russian-everyday-dialogues-preflight-v0.1"
DESCRIPTOR = StagedSourceDescriptor(
    source_id=SOURCE_ID,
    license_name=SOURCE_LICENSE,
    license_url=SOURCE_LICENSE_URL,
    origin_url=SOURCE_ORIGIN_URL,
    attribution=SOURCE_ATTRIBUTION,
)


def audit_markdown(report: dict[str, Any]) -> str:
    """Создаёт Markdown summary audit без реплик и тегов источника."""
    audit = report["audit"]
    return "\n".join(
        [
            "# Russian Everyday Dialogues — независимый staging audit",
            "",
            "Статус: candidate-only; active manifest, raw и обучение не изменялись.",
            f"SHA-256 candidate: `{report['candidate_sha256']}`.",
            f"Принято: {audit['records_accepted']}; отклонено: {audit['records_rejected']}; split: {audit['accepted_by_split']}.",
            f"Диалоговых ходов: {audit['unique_dialogue_turns']}; межsplit exact/near: {audit['cross_split_exact_duplicates']}/{audit['cross_split_near_duplicate_candidates']}.",
            f"Training allowed: `{audit['training_allowed']}`; причины: {audit['training_block_reasons']}.",
            "",
        ]
    )


def preflight_markdown(report: dict[str, Any]) -> str:
    """Создаёт Markdown summary temporary merge без реплик источника."""
    audit = report["audit"]
    return "\n".join(
        [
            "# Russian Everyday Dialogues — active corpus preflight",
            "",
            "Статус: временная проверка с hard links; active manifest, raw и обучение не изменялись.",
            f"Проверено active sources: {report['active_sources_checked']}; clean merge: `{report['clean_merge']}`.",
            f"Принято: {audit['records_accepted']}; отклонено: {audit['records_rejected']}; причины: {audit['rejections']}.",
            f"Межsplit exact/near: {audit['cross_split_exact_duplicates']}/{audit['cross_split_near_duplicate_candidates']}.",
            f"Training allowed временного объединения: `{audit['training_allowed']}`; причины: {audit['training_block_reasons']}.",
            "",
        ]
    )


def create_reports(candidate: Path, audit_directory: Path, preflight_directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Создаёт два новых отчёта после audit и preflight, не выполняя promotion."""
    if audit_directory.exists() or preflight_directory.exists():
        raise BrainV3StagingError("Каталог dialogue audit/preflight отчёта уже существует.")
    audit = audit_staged_candidate(candidate, DESCRIPTOR)
    preflight = preflight_staged_candidate_against_active(candidate, DESCRIPTOR)
    audit_directory.mkdir(parents=True)
    preflight_directory.mkdir(parents=True)
    (audit_directory / "russian_everyday_dialogues_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (audit_directory / "RUSSIAN_EVERYDAY_DIALOGUES_AUDIT_REPORT.md").write_text(
        audit_markdown(audit),
        encoding="utf-8",
    )
    (preflight_directory / "russian_everyday_dialogues_preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (preflight_directory / "RUSSIAN_EVERYDAY_DIALOGUES_PREFLIGHT_REPORT.md").write_text(
        preflight_markdown(preflight),
        encoding="utf-8",
    )
    return audit, preflight


def main() -> None:
    """Запускает fixed-source verification для версии staging v0.1."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--audit-report", type=Path, default=DEFAULT_AUDIT_REPORT)
    parser.add_argument("--preflight-report", type=Path, default=DEFAULT_PREFLIGHT_REPORT)
    args = parser.parse_args()
    try:
        audit, preflight = create_reports(args.candidate, args.audit_report, args.preflight_report)
    except (BrainV3StagingError, OSError, ValueError) as error:
        raise SystemExit(f"Dialogue staging verification остановлена: {error}") from error
    print(json.dumps({"audit": audit, "preflight": preflight}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
