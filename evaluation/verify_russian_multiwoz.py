"""Создаёт независимые audit и active preflight отчёты для Russian MultiWOZ.

Скрипт не скачивает источник, не меняет staging candidate, raw corpus или
manifest. Он использует только существующий JSONL-кандидат и временные hard
links, чтобы до promotion выявить дефекты, exact/near-дубли и cross-source
пересечения.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_core import russian_multiwoz
from evaluation.brain_v3_staging import (
    BrainV3StagingError,
    StagedSourceDescriptor,
    audit_staged_candidate,
    preflight_staged_candidate_against_active,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATE = ROOT / "data" / "brain_v3" / "staging" / "russian-multiwoz-mit-v0.3.jsonl"
DEFAULT_AUDIT_DIRECTORY = ROOT / "evaluation" / "results" / "russian-multiwoz-audit-v0.3"
DEFAULT_PREFLIGHT_DIRECTORY = ROOT / "evaluation" / "results" / "russian-multiwoz-preflight-v0.3"


def _descriptor() -> StagedSourceDescriptor:
    """Возвращает source provenance из адаптера, а не из недоверенного JSONL."""
    return StagedSourceDescriptor(
        source_id=russian_multiwoz.SOURCE_ID,
        license_name=russian_multiwoz.SOURCE_LICENSE,
        license_url=russian_multiwoz.SOURCE_LICENSE_URL,
        origin_url=russian_multiwoz.SOURCE_ORIGIN_URL,
        attribution=russian_multiwoz.SOURCE_ATTRIBUTION,
    )


def audit_markdown(report: dict[str, Any]) -> str:
    """Формирует компактный отчёт isolated audit без исходных диалогов."""
    audit = report["audit"]
    return "\n".join(
        [
            "# Russian MultiWOZ — isolated staging audit",
            "",
            "Статус: candidate проверен через временный hard link; active corpus не менялся.",
            f"SHA-256 candidate: `{report['candidate_sha256']}`.",
            f"Принято: {audit['records_accepted']}; отклонено: {audit['records_rejected']}; причины: {audit['rejections']}.",
            f"Dialogue turns: {audit['unique_dialogue_turns']}; language characters: {audit['language_characters']}.",
            f"Межsplit exact: {audit['cross_split_exact_duplicates']}; near: {audit['cross_split_near_duplicate_candidates']}.",
            f"Основной training gate isolated candidate: `{audit['training_allowed']}`; причины: {audit['training_block_reasons']}.",
            "",
            "Отчёт не содержит реплики, адреса, телефоны, reference-коды или другой source text.",
            "",
        ]
    )


def preflight_markdown(report: dict[str, Any]) -> str:
    """Формирует отчёт merge-проверки без раскрытия текстов обоих источников."""
    audit = report["audit"]
    return "\n".join(
        [
            "# Russian MultiWOZ — preflight против active Brain v3 corpus",
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
) -> dict[str, dict[str, Any]]:
    """Создаёт новые immutable audit/preflight результаты без overwrite прошлых отчётов."""
    if audit_directory.exists() or preflight_directory.exists():
        raise ValueError("Russian MultiWOZ verification report directory уже существует; перезапись запрещена.")
    descriptor = _descriptor()
    isolated_audit = audit_staged_candidate(candidate, descriptor)
    preflight = preflight_staged_candidate_against_active(candidate, descriptor)
    audit_directory.mkdir(parents=True)
    preflight_directory.mkdir(parents=True)
    (audit_directory / "russian_multiwoz_audit.json").write_text(
        json.dumps(isolated_audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (audit_directory / "RUSSIAN_MULTIWOZ_AUDIT_REPORT.md").write_text(
        audit_markdown(isolated_audit),
        encoding="utf-8",
    )
    (preflight_directory / "russian_multiwoz_preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (preflight_directory / "RUSSIAN_MULTIWOZ_PREFLIGHT_REPORT.md").write_text(
        preflight_markdown(preflight),
        encoding="utf-8",
    )
    return {"audit": isolated_audit, "preflight": preflight}


def main() -> None:
    """Запускает fixed candidate verification без изменения active corpus."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--audit-report", type=Path, default=DEFAULT_AUDIT_DIRECTORY)
    parser.add_argument("--preflight-report", type=Path, default=DEFAULT_PREFLIGHT_DIRECTORY)
    args = parser.parse_args()
    try:
        reports = create_verification_reports(args.candidate, args.audit_report, args.preflight_report)
    except (BrainV3StagingError, OSError, ValueError) as error:
        raise SystemExit(f"Russian MultiWOZ verification остановлен: {error}") from error
    print(json.dumps(reports, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
