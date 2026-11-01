"""Независимо проверяет staging-кандидат DReaM без изменения active manifest."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ai_core.brain_v3_corpus import audit_corpus, file_sha256
from ai_core.korp_dream_ru_open import (
    DEFAULT_STAGING_ROOT,
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATE = ROOT / "data" / "brain_v3" / "staging" / "dream-ru-open-sentence-collection-v0.2.jsonl"
DEFAULT_REPORT = ROOT / "evaluation" / "results" / "dream-ru-open-sentence-collection-audit-v0.2"


class StagedDReaMAuditError(ValueError):
    """Останавливает audit, если вход не является локальным staging-кандидатом."""


def _staging_candidate(candidate: Path, staging_root: Path) -> Path:
    """Принимает только уже существующий JSONL внутри ожидаемой staging-папки."""
    root = staging_root.expanduser().resolve()
    path = candidate.expanduser().resolve()
    if path.suffix.lower() != ".jsonl" or not path.is_file():
        raise StagedDReaMAuditError("DReaM candidate должен быть существующим JSONL-файлом.")
    try:
        path.relative_to(root)
    except ValueError as error:
        raise StagedDReaMAuditError("Audit разрешён только для data/brain_v3/staging/.") from error
    return path


def _candidate_manifest(candidate: Path) -> dict[str, Any]:
    """Строит временный manifest только для независимой проверки кандидат-файла."""
    return {
        "format_version": 1,
        "corpus_name": "brain-v3-dream-staging-candidate",
        "language_scope": ["ru"],
        "accepted_licenses": ["CC-BY-4.0"],
        "quality_gate": {
            "minimum_unique_dialogue_turns": 20_000,
            "minimum_language_characters": 100_000_000,
            "maximum_rejected_record_ratio": 0.0,
            "require_all_splits": True,
        },
        "sources": [
            {
                "source_id": SOURCE_ID,
                "raw_path": "raw/candidate.jsonl",
                "raw_sha256": file_sha256(candidate),
                "source_kind": "open_licensed",
                "license": SOURCE_LICENSE,
                "license_url": SOURCE_LICENSE_URL,
                "origin_url": SOURCE_ORIGIN_URL,
                "attribution": SOURCE_ATTRIBUTION,
                "approved_for_training": True,
            }
        ],
    }


def audit_staged_dream_ru_open(
    candidate: Path,
    *,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> dict[str, Any]:
    """Проверяет candidate через основной audit без копирования и activation.

    Временный manifest и hard link создаются рядом со staging-папкой на том же
    локальном томе. После проверки `TemporaryDirectory` удаляется; active
    `corpus_manifest.v1.json` никогда не читается для записи и не изменяется.
    """
    source_path = _staging_candidate(candidate, staging_root)
    temporary_parent = staging_root.expanduser().resolve().parent
    with tempfile.TemporaryDirectory(prefix=".dream-staged-audit-", dir=temporary_parent) as directory:
        temporary_root = Path(directory)
        raw_directory = temporary_root / "raw"
        raw_directory.mkdir()
        linked_candidate = raw_directory / "candidate.jsonl"
        try:
            os.link(source_path, linked_candidate)
        except OSError as error:
            raise StagedDReaMAuditError("Не удалось создать локальный hard link для isolated audit.") from error
        manifest_path = temporary_root / "corpus_manifest.v1.json"
        manifest_path.write_text(
            json.dumps(_candidate_manifest(source_path), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        audit = audit_corpus(manifest_path)

    return {
        "format_version": 1,
        "mode": "staging_candidate_audit",
        "candidate_path": str(source_path),
        "candidate_sha256": file_sha256(source_path),
        "source_id": SOURCE_ID,
        "license": SOURCE_LICENSE,
        "audit": audit,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
    }


def summary_markdown(report: dict[str, Any]) -> str:
    """Формирует Markdown-учёт без строк проверенного корпуса."""
    audit = report["audit"]
    return "\n".join(
        [
            "# Независимый audit staging-кандидата DReaM-ru-open",
            "",
            "Статус: candidate-only; active manifest и обучение не изменялись.",
            f"Источник: `{report['source_id']}`; лицензия: `{report['license']}`.",
            f"SHA-256 candidate JSONL: `{report['candidate_sha256']}`.",
            f"Принято: {audit['records_accepted']}; отклонено: {audit['records_rejected']}; всего: {audit['records_total']}.",
            f"Split: {audit['accepted_by_split']}.",
            f"Типы: {audit['accepted_by_type']}; языковых символов: {audit['language_characters']}; диалоговых ходов: {audit['unique_dialogue_turns']}.",
            f"Причины отклонения: {audit['rejections']}.",
            f"Межsplit точных дублей: {audit['cross_split_exact_duplicates']}; близких кандидатов: {audit['cross_split_near_duplicate_candidates']}.",
            f"Training allowed: `{audit['training_allowed']}`; причины блокировки: {audit['training_block_reasons']}.",
            "",
            "Аудит использовал временный hard link и не копировал тексты в отчёт. До отдельного решения candidate нельзя переносить в raw/ или добавлять в active manifest.",
            "",
        ]
    )


def create_audit(candidate: Path, report_directory: Path) -> dict[str, Any]:
    """Создаёт новый machine/Markdown report из одного локального staging-кандидата."""
    if report_directory.exists():
        raise ValueError(f"Каталог отчёта уже существует: {report_directory}")
    report = audit_staged_dream_ru_open(candidate)
    report_directory.mkdir(parents=True)
    (report_directory / "dream_ru_open_staging_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "DREAM_RU_OPEN_STAGING_AUDIT_REPORT.md").write_text(
        summary_markdown(report),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Запускает audit только для versioned staging JSONL и нового report-dir."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        report = create_audit(args.candidate, args.report)
    except (StagedDReaMAuditError, OSError, ValueError) as error:
        raise SystemExit(f"DReaM staging audit остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
