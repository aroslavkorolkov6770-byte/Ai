"""Скачивает один проверенный MOT Russian release asset без import или activation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_core.mot_russian import (
    DEFAULT_ARCHIVE_PATH,
    DEFAULT_SOURCE_ARCHIVE_ROOT,
    MOTRussianError,
    download_mot_russian_archive,
    download_summary_markdown,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_DIRECTORY = ROOT / "evaluation" / "results" / "mot-rus-golosameriki-download-v0.1"


def create_download_report(archive: Path, report_directory: Path) -> dict:
    """Создаёт новый immutable local archive snapshot и отдельный report-dir."""
    if report_directory.exists():
        raise MOTRussianError("Каталог MOT download report уже существует; перезапись запрещена.")
    summary = download_mot_russian_archive(archive, archive_root=DEFAULT_SOURCE_ARCHIVE_ROOT)
    report = summary.as_dict()
    report_directory.mkdir(parents=True)
    (report_directory / "mot_russian_download.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "MOT_RUSSIAN_DOWNLOAD_REPORT.md").write_text(
        download_summary_markdown(summary),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Выполняет fixed-source download без произвольного URL или overwrite."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE_PATH)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    args = parser.parse_args()
    try:
        report = create_download_report(args.archive, args.report_directory)
    except (MOTRussianError, OSError, ValueError) as error:
        raise SystemExit(f"MOT Russian download остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
