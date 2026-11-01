"""Создаёт и учитывает staging-пилот Russian Everyday Dialogues без activation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_core.russian_everyday_dialogues import (
    DEFAULT_STAGING_ROOT,
    RussianEverydayDialoguesError,
    import_russian_everyday_dialogues,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = DEFAULT_STAGING_ROOT / "russian-everyday-dialogues-cc-by-v0.1.jsonl"
DEFAULT_REPORT = ROOT / "evaluation" / "results" / "russian-everyday-dialogues-staging-v0.1"


def summary_markdown(report: dict[str, Any]) -> str:
    """Пишет учёт только из хэшей и метрик, не добавляя диалоги в отчёт."""
    return "\n".join(
        [
            "# Russian Everyday Dialogues — staging-приём Brain v3",
            "",
            "Статус: локальный staging-кандидат; active manifest, raw и обучение не изменялись.",
            f"Источник: `{report['source_id']}`; revision: `{report['source_revision']}`.",
            f"SHA-256 исходного JSONL: `{report['source_raw_sha256']}`.",
            f"SHA-256 staging JSONL: `{report['output_sha256']}`.",
            f"Строк источника: {report['raw_rows_seen']}; записано: {report['records_written']}; исключено: {report['records_rejected']}.",
            f"Причины исключения: {report['rejections']}.",
            "",
            "Пилот слишком мал для обучения. Перед возможным local promotion нужны независимый staging audit и preflight с active corpus.",
            "",
        ]
    )


def create_import_report(output: Path, report_directory: Path) -> dict[str, Any]:
    """Импортирует один pinned source и сохраняет отдельный JSON/Markdown отчёт."""
    if report_directory.exists():
        raise RussianEverydayDialoguesError(f"Каталог отчёта уже существует: {report_directory}")
    summary = import_russian_everyday_dialogues(output)
    report = summary.as_dict()
    report_directory.mkdir(parents=True)
    (report_directory / "russian_everyday_dialogues_staging.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "RUSSIAN_EVERYDAY_DIALOGUES_STAGING_REPORT.md").write_text(
        summary_markdown(report),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Запускает только fixed-source import в новый versioned staging output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        report = create_import_report(args.output, args.report)
    except (RussianEverydayDialoguesError, OSError, ValueError) as error:
        raise SystemExit(f"Russian Everyday Dialogues staging import остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
