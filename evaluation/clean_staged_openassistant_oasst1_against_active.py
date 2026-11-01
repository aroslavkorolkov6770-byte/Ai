"""Создаёт OASST1 v0.2 без подтверждённых overlap с active Brain v3 corpus.

v0.1 остаётся неизменяемым доказательством исходного audit. Этот запуск не
загружает источник, не меняет active manifest/raw/API и не обучает модель.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_core import openassistant_oasst1
from evaluation.brain_v3_active_clean import (
    BrainV3ActiveCleanError,
    clean_staged_candidate_against_active,
    summary_markdown,
)
from evaluation.brain_v3_staging import StagedSourceDescriptor


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STAGING_ROOT = ROOT / "data" / "brain_v3" / "staging"
DEFAULT_CANDIDATE = DEFAULT_STAGING_ROOT / "openassistant-oasst1-apache-v0.1.jsonl"
DEFAULT_OUTPUT = DEFAULT_STAGING_ROOT / "openassistant-oasst1-apache-v0.2.jsonl"
DEFAULT_REPORT_DIRECTORY = ROOT / "evaluation" / "results" / "openassistant-oasst1-active-clean-v0.2"


def _descriptor() -> StagedSourceDescriptor:
    """Возвращает fixed OASST1 provenance, не доверяя candidate JSONL."""
    return StagedSourceDescriptor(
        source_id=openassistant_oasst1.SOURCE_ID,
        license_name=openassistant_oasst1.SOURCE_LICENSE,
        license_url=openassistant_oasst1.SOURCE_LICENSE_URL,
        origin_url=openassistant_oasst1.SOURCE_ORIGIN_URL,
        attribution=openassistant_oasst1.SOURCE_ATTRIBUTION,
    )


def create_cleaned_candidate(
    candidate: Path = DEFAULT_CANDIDATE,
    output: Path = DEFAULT_OUTPUT,
    report_directory: Path = DEFAULT_REPORT_DIRECTORY,
) -> dict[str, Any]:
    """Сохраняет immutable v0.2 и audit trail без overwrite результатов."""
    destination = report_directory.expanduser().resolve()
    if destination.exists():
        raise BrainV3ActiveCleanError("OASST1 clean report directory уже существует; overwrite запрещён.")
    report = clean_staged_candidate_against_active(candidate, output, _descriptor())
    destination.mkdir(parents=True)
    (destination / "openassistant_oasst1_active_clean.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (destination / "OPENASSISTANT_OASST1_ACTIVE_CLEAN_REPORT.md").write_text(
        summary_markdown(report),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Выполняет fixed v0.1→v0.2 local clean без CLI source overrides."""
    try:
        report = create_cleaned_candidate()
    except (BrainV3ActiveCleanError, OSError, ValueError) as error:
        raise SystemExit(f"OpenAssistant OASST1 active clean остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
