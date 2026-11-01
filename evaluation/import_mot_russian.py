"""Создаёт staging JSONL из ранее проверенного локального MOT Russian archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_core.mot_russian import (
    DEFAULT_ARCHIVE_PATH,
    DEFAULT_SOURCE_ARCHIVE_ROOT,
    DEFAULT_STAGING_ROOT,
    MOTRussianError,
    MOTRussianImportProgress,
    import_mot_russian,
    import_summary_markdown,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = DEFAULT_STAGING_ROOT / "mot-rus-golosameriki-cc-by-v0.1.jsonl"
DEFAULT_REPORT_DIRECTORY = ROOT / "evaluation" / "results" / "mot-rus-golosameriki-import-v0.1"


def _print_progress(progress: MOTRussianImportProgress) -> None:
    """Показывает безопасный промежуточный статус без текста новостей."""
    print(
        json.dumps(
            {
                "event": "mot_russian_import_progress",
                **progress.as_dict(),
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


def create_import_report(
    archive: Path,
    output: Path,
    report_directory: Path,
    *,
    progress_every_members: int = 25_000,
) -> dict:
    """Импортирует только в новый staging JSONL и создаёт отдельный report-dir."""
    if report_directory.exists():
        raise MOTRussianError("Каталог MOT import report уже существует; перезапись запрещена.")
    summary = import_mot_russian(
        archive,
        output,
        archive_root=DEFAULT_SOURCE_ARCHIVE_ROOT,
        staging_root=DEFAULT_STAGING_ROOT,
        progress_callback=_print_progress,
        progress_every_members=progress_every_members,
    )
    report = summary.as_dict()
    report_directory.mkdir(parents=True)
    (report_directory / "mot_russian_import.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "MOT_RUSSIAN_IMPORT_REPORT.md").write_text(
        import_summary_markdown(summary),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Запускает archive-to-staging conversion без source URL, activation или training."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    parser.add_argument(
        "--progress-every-members",
        type=int,
        default=25_000,
        help="Печатать только счётчики после каждых N tar members (по умолчанию: 25000).",
    )
    args = parser.parse_args()
    try:
        report = create_import_report(
            args.archive,
            args.output,
            args.report_directory,
            progress_every_members=args.progress_every_members,
        )
    except (FileNotFoundError, MOTRussianError, OSError, ValueError) as error:
        raise SystemExit(f"MOT Russian import остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
