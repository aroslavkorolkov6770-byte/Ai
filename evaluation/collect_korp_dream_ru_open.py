"""Создаёт полный staging-снимок DReaM-ru-open без active import или обучения."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_core.korp_dream_ru_open import (
    KorpDreaMPreviewError,
    collect_dream_ru_open,
    summary_markdown,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "data" / "brain_v3" / "staging" / "dream-ru-open-sentence-collection-v0.2.jsonl"
DEFAULT_REPORT = ROOT / "evaluation" / "results" / "dream-ru-open-sentence-collection-v0.2"


def create_collection(output: Path, report_directory: Path) -> dict:
    """Собирает только новый staging JSONL и отдельный отчёт без текстов source."""
    if report_directory.exists():
        raise ValueError(f"Каталог отчёта уже существует: {report_directory}")
    summary = collect_dream_ru_open(output)
    report = summary.as_dict()
    report_directory.mkdir(parents=True)
    (report_directory / "dream_ru_open_collection.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "DREAM_RU_OPEN_COLLECTION_REPORT.md").write_text(
        summary_markdown(summary),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Разрешает только versioned output/report, без сетевых или corpus параметров."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        report = create_collection(args.output, args.report)
    except (KorpDreaMPreviewError, OSError, ValueError) as error:
        raise SystemExit(f"DReaM collection остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
