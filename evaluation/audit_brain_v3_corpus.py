"""Запускает безопасный аудит нового корпуса Brain v3 и пишет только отчёт."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_core.brain_v3_corpus import (
    DEFAULT_MANIFEST,
    BrainV3CorpusError,
    audit_corpus,
    summary_markdown,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "evaluation" / "results" / "brain-v3-corpus-audit-v0.1"


def audit(manifest: Path, output: Path) -> dict:
    """Аудирует манифест и сохраняет результат только в новый каталог."""
    if output.exists():
        raise ValueError(f"Каталог результата уже существует: {output}")
    report = audit_corpus(manifest)
    output.mkdir(parents=True)
    (output / "brain_v3_corpus_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "BRAIN_V3_CORPUS_AUDIT_REPORT.md").write_text(
        summary_markdown(report),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Разбирает CLI-параметры без загрузки корпуса в активную модель."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        report = audit(args.manifest, args.output)
    except (BrainV3CorpusError, FileNotFoundError, OSError, ValueError) as error:
        raise SystemExit(f"Аудит корпуса Brain v3 остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
