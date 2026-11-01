"""Независимо проверяет MOT Russian staging-кандидат до local promotion.

Скрипт не скачивает архив, не меняет active manifest и не запускает обучение.
Он читает уже созданный staging JSONL как данные, проводит isolated audit и
временный preflight с активными источниками через hard links.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_core.mot_russian import (
    DEFAULT_STAGING_ROOT,
    MOT_RUS_RELEASE_URL,
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
)
from evaluation.brain_v3_staging import (
    DEFAULT_CORPUS_ROOT,
    BrainV3StagingError,
    StagedSourceDescriptor,
    audit_staged_candidate,
    preflight_staged_candidate_against_active,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATE = DEFAULT_STAGING_ROOT / "mot-rus-golosameriki-cc-by-v0.1.jsonl"
DEFAULT_AUDIT_REPORT = ROOT / "evaluation" / "results" / "mot-rus-golosameriki-audit-v0.1"
DEFAULT_PREFLIGHT_REPORT = ROOT / "evaluation" / "results" / "mot-rus-golosameriki-preflight-v0.1"
DESCRIPTOR = StagedSourceDescriptor(
    source_id=SOURCE_ID,
    license_name=SOURCE_LICENSE,
    license_url=SOURCE_LICENSE_URL,
    origin_url=SOURCE_ORIGIN_URL,
    attribution=SOURCE_ATTRIBUTION,
)


def audit_markdown(report: dict[str, Any]) -> str:
    """Формирует audit summary без сохранения текста импортированных статей."""
    audit = report["audit"]
    return "\n".join(
        [
            "# MOT Russian — независимый staging audit",
            "",
            f"Источник: [{SOURCE_ID}]({MOT_RUS_RELEASE_URL}).",
            f"Лицензия: [{SOURCE_LICENSE}]({SOURCE_LICENSE_URL}).",
            "Статус: candidate-only; active manifest, raw и обучение не изменялись.",
            f"SHA-256 candidate: `{report['candidate_sha256']}`.",
            f"Принято: {audit['records_accepted']}; отклонено: {audit['records_rejected']}; split: {audit['accepted_by_split']}.",
            f"Языковых символов: {audit['language_characters']}; диалоговых ходов: {audit['unique_dialogue_turns']}.",
            f"Межsplit exact/near: {audit['cross_split_exact_duplicates']}/{audit['cross_split_near_duplicate_candidates']}.",
            f"Training allowed: `{audit['training_allowed']}`; причины: {audit['training_block_reasons']}.",
            "",
        ]
    )


def preflight_markdown(report: dict[str, Any]) -> str:
    """Формирует temporary-merge summary без раскрытия материалов corpus."""
    audit = report["audit"]
    return "\n".join(
        [
            "# MOT Russian — active corpus preflight",
            "",
            f"Источник: [{SOURCE_ID}]({MOT_RUS_RELEASE_URL}).",
            "Статус: временная проверка с hard links; active manifest, raw и обучение не изменялись.",
            f"Проверено active sources: {report['active_sources_checked']}; clean merge: `{report['clean_merge']}`.",
            f"Принято: {audit['records_accepted']}; отклонено: {audit['records_rejected']}; причины: {audit['rejections']}.",
            f"Языковых символов: {audit['language_characters']}; диалоговых ходов: {audit['unique_dialogue_turns']}.",
            f"Межsplit exact/near: {audit['cross_split_exact_duplicates']}/{audit['cross_split_near_duplicate_candidates']}.",
            f"Training allowed временного объединения: `{audit['training_allowed']}`; причины: {audit['training_block_reasons']}.",
            "",
        ]
    )


def create_reports(
    candidate: Path,
    audit_directory: Path,
    preflight_directory: Path,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Создаёт новые audit/preflight отчёты, не выполняя promotion кандидата."""
    if audit_directory.exists() or preflight_directory.exists():
        raise BrainV3StagingError("Каталог MOT audit/preflight отчёта уже существует; перезапись запрещена.")
    audit = audit_staged_candidate(candidate, DESCRIPTOR, staging_root=staging_root)
    preflight = preflight_staged_candidate_against_active(
        candidate,
        DESCRIPTOR,
        corpus_root=corpus_root,
        staging_root=staging_root,
    )
    audit_directory.mkdir(parents=True)
    preflight_directory.mkdir(parents=True)
    (audit_directory / "mot_russian_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (audit_directory / "MOT_RUSSIAN_AUDIT_REPORT.md").write_text(
        audit_markdown(audit),
        encoding="utf-8",
    )
    (preflight_directory / "mot_russian_preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (preflight_directory / "MOT_RUSSIAN_PREFLIGHT_REPORT.md").write_text(
        preflight_markdown(preflight),
        encoding="utf-8",
    )
    return audit, preflight


def main() -> None:
    """Запускает fixed-source verification для MOT Russian staging v0.1."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--audit-report", type=Path, default=DEFAULT_AUDIT_REPORT)
    parser.add_argument("--preflight-report", type=Path, default=DEFAULT_PREFLIGHT_REPORT)
    args = parser.parse_args()
    try:
        audit, preflight = create_reports(args.candidate, args.audit_report, args.preflight_report)
    except (BrainV3StagingError, OSError, ValueError) as error:
        raise SystemExit(f"MOT Russian staging verification остановлена: {error}") from error
    print(json.dumps({"audit": audit, "preflight": preflight}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
