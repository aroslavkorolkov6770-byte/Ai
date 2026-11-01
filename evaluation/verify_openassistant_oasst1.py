"""Создаёт independent audit и active preflight для staging-кандидата OASST1.

Скрипт не скачивает данные, не меняет candidate/raw/manifest и не запускает
обучение. Проверка читает только уже созданный local staging JSONL и временно
связывает его с audit-инфраструктурой, чтобы выявить schema defects, exact/near
duplicates и пересечения с действующим корпусом до отдельного promotion.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_core import openassistant_oasst1
from evaluation.brain_v3_staging import (
    BrainV3StagingError,
    DEFAULT_CORPUS_ROOT,
    DEFAULT_STAGING_ROOT,
    StagedSourceDescriptor,
    audit_staged_candidate,
    preflight_staged_candidate_against_active,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATE = ROOT / "data" / "brain_v3" / "staging" / "openassistant-oasst1-apache-v0.1.jsonl"
DEFAULT_AUDIT_DIRECTORY = ROOT / "evaluation" / "results" / "openassistant-oasst1-audit-v0.1"
DEFAULT_PREFLIGHT_DIRECTORY = ROOT / "evaluation" / "results" / "openassistant-oasst1-preflight-v0.1"


def _descriptor() -> StagedSourceDescriptor:
    """Берёт trusted provenance из fixed local адаптера, а не из candidate text."""
    return StagedSourceDescriptor(
        source_id=openassistant_oasst1.SOURCE_ID,
        license_name=openassistant_oasst1.SOURCE_LICENSE,
        license_url=openassistant_oasst1.SOURCE_LICENSE_URL,
        origin_url=openassistant_oasst1.SOURCE_ORIGIN_URL,
        attribution=openassistant_oasst1.SOURCE_ATTRIBUTION,
    )


def audit_markdown(report: dict[str, Any]) -> str:
    """Создаёт compact isolated-audit record без внешних реплик или identifiers."""
    audit = report["audit"]
    return "\n".join(
        [
            "# OpenAssistant OASST1 — isolated staging audit",
            "",
            "Статус: candidate проверен через временный hard link; active corpus не изменялся.",
            f"SHA-256 candidate: `{report['candidate_sha256']}`.",
            f"Принято: {audit['records_accepted']}; отклонено: {audit['records_rejected']}; причины: {audit['rejections']}.",
            f"Dialogue turns: {audit['unique_dialogue_turns']}; language characters: {audit['language_characters']}.",
            f"Межsplit exact: {audit['cross_split_exact_duplicates']}; near: {audit['cross_split_near_duplicate_candidates']}.",
            f"Основной training gate isolated candidate: `{audit['training_allowed']}`; причины: {audit['training_block_reasons']}.",
            "",
            "Отчёт не содержит исходные реплики, user IDs, служебные labels, адреса или телефоны.",
            "",
        ]
    )


def preflight_markdown(report: dict[str, Any]) -> str:
    """Создаёт отчёт merge-проверки без раскрытия candidate и active source text."""
    audit = report["audit"]
    return "\n".join(
        [
            "# OpenAssistant OASST1 — preflight против active Brain v3 corpus",
            "",
            "Статус: active raw и candidate сопоставлены только через временные hard links.",
            f"Проверено active sources: {report['active_sources_checked']}; candidate: `{report['candidate_source_id']}`.",
            f"SHA-256 candidate: `{report['candidate_sha256']}`.",
            f"Принято: {audit['records_accepted']}; отклонено: {audit['records_rejected']}; причины: {audit['rejections']}.",
            f"Межsplit exact: {audit['cross_split_exact_duplicates']}; near: {audit['cross_split_near_duplicate_candidates']}.",
            f"Чистое слияние: `{report['clean_merge']}`.",
            f"Training gate временного объединения: `{audit['training_allowed']}`; причины: {audit['training_block_reasons']}.",
            "",
            "Preflight не переносит candidate в raw/, не меняет manifest и не запускает обучение.",
            "",
        ]
    )


def create_verification_reports(
    candidate: Path = DEFAULT_CANDIDATE,
    audit_directory: Path = DEFAULT_AUDIT_DIRECTORY,
    preflight_directory: Path = DEFAULT_PREFLIGHT_DIRECTORY,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> dict[str, dict[str, Any]]:
    """Создаёт новые immutable audit/preflight результаты без overwrite старых отчётов."""
    if audit_directory.exists() or preflight_directory.exists():
        raise BrainV3StagingError("OASST1 verification report directory уже существует; перезапись запрещена.")
    descriptor = _descriptor()
    isolated_audit = audit_staged_candidate(candidate, descriptor, staging_root=staging_root)
    preflight = preflight_staged_candidate_against_active(
        candidate,
        descriptor,
        corpus_root=corpus_root,
        staging_root=staging_root,
    )
    audit_directory.mkdir(parents=True)
    preflight_directory.mkdir(parents=True)
    (audit_directory / "openassistant_oasst1_audit.json").write_text(
        json.dumps(isolated_audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (audit_directory / "OPENASSISTANT_OASST1_AUDIT_REPORT.md").write_text(
        audit_markdown(isolated_audit),
        encoding="utf-8",
    )
    (preflight_directory / "openassistant_oasst1_preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (preflight_directory / "OPENASSISTANT_OASST1_PREFLIGHT_REPORT.md").write_text(
        preflight_markdown(preflight),
        encoding="utf-8",
    )
    return {"audit": isolated_audit, "preflight": preflight}


def main() -> None:
    """Проверяет fixed OASST1 staging candidate без source download или activation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--audit-report", type=Path, default=DEFAULT_AUDIT_DIRECTORY)
    parser.add_argument("--preflight-report", type=Path, default=DEFAULT_PREFLIGHT_DIRECTORY)
    args = parser.parse_args()
    try:
        reports = create_verification_reports(args.candidate, args.audit_report, args.preflight_report)
    except (BrainV3StagingError, OSError, ValueError) as error:
        raise SystemExit(f"OpenAssistant OASST1 verification остановлен: {error}") from error
    print(json.dumps(reports, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
