"""Запускает read-only audit разнообразия активного dialogue-корпуса Brain v3."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_core.brain_v3_dialogue_coverage import (
    DEFAULT_POLICY,
    DialogueCoverageError,
    audit_dialogue_coverage,
    summary_markdown,
)
from ai_core.brain_v3_corpus import DEFAULT_MANIFEST


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_DIRECTORY = ROOT / "evaluation" / "results" / "brain-v3-dialogue-coverage-v0.1"


def create_report(
    manifest_path: Path,
    policy_path: Path,
    report_directory: Path,
) -> dict[str, Any]:
    """Сохраняет новый immutable отчёт без изменения corpus, чата или обучения."""
    destination = report_directory.expanduser().resolve()
    if destination.exists():
        raise DialogueCoverageError("Каталог coverage report уже существует и не будет перезаписан.")
    report = audit_dialogue_coverage(manifest_path, policy_path)
    destination.mkdir(parents=True)
    (destination / "brain_v3_dialogue_coverage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (destination / "BRAIN_V3_DIALOGUE_COVERAGE_AUDIT.md").write_text(
        summary_markdown(report),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Запускает coverage audit только по явным локальным путям."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    args = parser.parse_args()
    try:
        report = create_report(args.manifest, args.policy, args.report_directory)
    except (DialogueCoverageError, OSError, ValueError) as error:
        raise SystemExit(f"Dialogue coverage audit остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
