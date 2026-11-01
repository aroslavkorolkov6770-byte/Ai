"""Создаёт маленький проверяемый staging-preview DReaM-ru-open без обучения."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_core.korp_dream_ru_open import (
    DEFAULT_MATCHES_PER_PAGE,
    DEFAULT_MAX_PAGES,
    KorpDreaMPreviewError,
    preview_dream_ru_open,
    summary_markdown,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "data" / "brain_v3" / "staging" / "dream-ru-open-sentence-preview-v0.2.jsonl"
DEFAULT_REPORT = ROOT / "evaluation" / "results" / "dream-ru-open-sentence-preview-v0.2"


def create_preview(
    output: Path,
    report_directory: Path,
    *,
    pages: int = DEFAULT_MAX_PAGES,
    matches_per_page: int = DEFAULT_MATCHES_PER_PAGE,
) -> dict:
    """Записывает данные только в staging и метаданные только в новый report-dir."""
    if report_directory.exists():
        raise ValueError(f"Каталог отчёта уже существует: {report_directory}")
    summary = preview_dream_ru_open(
        output,
        max_pages=pages,
        matches_per_page=matches_per_page,
    )
    report = summary.as_dict()
    report_directory.mkdir(parents=True)
    (report_directory / "dream_ru_open_preview.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "DREAM_RU_OPEN_PREVIEW_REPORT.md").write_text(
        summary_markdown(summary),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Разбирает только безопасные размеры snapshot и запускает один preview."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--matches-per-page", type=int, default=DEFAULT_MATCHES_PER_PAGE)
    args = parser.parse_args()
    try:
        report = create_preview(
            args.output,
            args.report,
            pages=args.pages,
            matches_per_page=args.matches_per_page,
        )
    except (KorpDreaMPreviewError, OSError, ValueError) as error:
        raise SystemExit(f"DReaM preview остановлен: {error}") from error
    # Windows PowerShell can use a legacy console encoding; escaped JSON keeps
    # the CLI result readable without changing the UTF-8 report files on disk.
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
