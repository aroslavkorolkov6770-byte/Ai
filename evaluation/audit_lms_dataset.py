"""Проверяет импортированный LMS-датасет до запуска обучения."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_core.lms_dataset import audit_dataset, audit_markdown, build_context_grouped_splits


def main() -> None:
    """Создаёт человекочитаемый и машинный отчёты аудита."""
    try:
        summary = audit_dataset()
        _, summary["context_grouped_split"] = build_context_grouped_splits(seed=42)
        (ROOT / "LMS_DATASET_AUDIT.md").write_text(
            audit_markdown(summary), encoding="utf-8"
        )
        output = ROOT / "evaluation" / "results" / "lms-dataset-audit"
        output.mkdir(parents=True, exist_ok=True)
        (output / "lms_dataset_audit.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except (FileNotFoundError, OSError, ValueError) as error:
        raise SystemExit(f"Аудит LMS-датасета остановлен: {error}") from error


if __name__ == "__main__":
    main()
