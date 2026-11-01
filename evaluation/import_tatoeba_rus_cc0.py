"""Создаёт проверяемый raw JSONL из официального русского Tatoeba CC0-экспорта."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_core.tatoeba_rus_cc0 import (
    TatoebaImportError,
    import_tatoeba_rus_cc0,
    summary_markdown,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE = ROOT / "data" / "brain_v3" / "raw" / "tatoeba-rus-cc0.tsv.bz2"
DEFAULT_OUTPUT = ROOT / "data" / "brain_v3" / "raw" / "tatoeba-rus-cc0-v1.jsonl"
DEFAULT_REPORT = ROOT / "evaluation" / "results" / "tatoeba-rus-cc0-import-v0.1"


def import_source(archive: Path, output: Path, report_directory: Path) -> dict:
    """Импортирует только в новый raw-файл и новый каталог отчёта."""
    if report_directory.exists():
        raise ValueError(f"Каталог отчёта уже существует: {report_directory}")
    summary = import_tatoeba_rus_cc0(archive, output)
    report = summary.as_dict()
    report_directory.mkdir(parents=True)
    (report_directory / "tatoeba_rus_cc0_import.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "TATOEBA_RUS_CC0_IMPORT_REPORT.md").write_text(
        summary_markdown(summary),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Разбирает параметры локального импорта без запуска обучения."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        report = import_source(args.archive, args.output, args.report)
    except (FileNotFoundError, OSError, TatoebaImportError, ValueError) as error:
        raise SystemExit(f"Импорт Tatoeba остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
