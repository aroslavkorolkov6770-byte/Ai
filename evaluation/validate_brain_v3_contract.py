"""Проверяет и фиксирует закрытый контракт качества Brain v3."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_core.brain_v3_contract import (
    DEFAULT_BRAIN_V3_CONTRACT,
    BrainV3ContractError,
    contract_summary,
    load_brain_v3_contract,
    summary_markdown,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "evaluation" / "results" / "brain-v3-contract-v0.1"


def validate(cases_path: Path, output: Path) -> dict:
    """Проверяет сценарии и пишет отчёт только в новый каталог результата."""
    if output.exists():
        raise ValueError(f"Каталог результата уже существует: {output}")
    contract = load_brain_v3_contract(cases_path)
    summary = contract_summary(contract)
    output.mkdir(parents=True)
    (output / "brain_v3_contract_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "BRAIN_V3_CONTRACT_REPORT.md").write_text(
        summary_markdown(summary),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    """Разбирает CLI-параметры валидатора без запуска обучения или чата."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_BRAIN_V3_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.cases, args.output), ensure_ascii=False, indent=2))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError, BrainV3ContractError) as error:
        raise SystemExit(f"Контракт Brain v3 остановлен: {error}") from error


if __name__ == "__main__":
    main()
