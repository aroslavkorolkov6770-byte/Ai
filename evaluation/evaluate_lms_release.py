"""Проверяет замороженный LMS-кандидат на ручном release-наборе вне обучения."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_core.lms_dataset import TASK_TYPES
from ai_core.lms_reliable import ReliableLms, fingerprint


def _load_cases(path: Path) -> list[dict]:
    """Загружает компактный ручной набор и проверяет обязательные поля."""
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Некорректный JSON в release-наборе, строка {line_number}.") from error
        required = {"id", "task_type", "context", "user", "required_fragments"}
        missing = sorted(required - set(row))
        if missing:
            raise ValueError(f"В release-кейсе {line_number} нет полей: {', '.join(missing)}.")
        if row["task_type"] not in TASK_TYPES:
            raise ValueError(f"В release-кейсе {line_number} неизвестный task_type.")
        if not all(isinstance(row[key], str) and row[key].strip() for key in ("id", "context", "user")):
            raise ValueError(f"В release-кейсе {line_number} есть пустой текст.")
        if not isinstance(row["required_fragments"], list) or not row["required_fragments"]:
            raise ValueError(f"В release-кейсе {line_number} нужен непустой список required_fragments.")
        cases.append(row)
    if not cases:
        raise ValueError("Release-набор LMS не содержит кейсов.")
    if len({row["id"] for row in cases}) != len(cases):
        raise ValueError("В release-наборе LMS повторяются id кейсов.")
    return cases


def _complete_human_text(text: str) -> bool:
    """Отсекает пустой и явно повреждённый текст без субъективной языковой оценки."""
    return (
        12 <= len(text.strip()) <= 1200
        and "\ufffd" not in text
        and bool(re.search(r"[А-Яа-яЁё]{3,}", text))
    )


def evaluate(checkpoint: Path, output: Path, cases_path: Path | None = None) -> dict:
    """Выполняет неизменяемую release-приёмку без дообучения на её кейсах."""
    cases_path = cases_path or ROOT / "evaluation" / "lms_release_holdout.jsonl"
    model = ReliableLms.load(checkpoint)
    cases = _load_cases(cases_path)
    details = []
    for case in cases:
        prediction = model.predict(case["user"], case["context"])
        lowered = prediction.text.casefold()
        required = [str(value) for value in case["required_fragments"]]
        forbidden = [str(value) for value in case.get("forbidden_fragments", [])]
        required_ok = all(fragment.casefold() in lowered for fragment in required)
        forbidden_ok = all(fragment.casefold() not in lowered for fragment in forbidden)
        task_ok = prediction.accepted and prediction.task_type == case["task_type"]
        text_ok = _complete_human_text(prediction.text)
        details.append(
            {
                "id": case["id"],
                "expected_task": case["task_type"],
                "predicted_task": prediction.task_type,
                "accepted": prediction.accepted,
                "confidence": prediction.confidence,
                "text": prediction.text,
                "task_ok": task_ok,
                "required_ok": required_ok,
                "forbidden_ok": forbidden_ok,
                "complete_human_text": text_ok,
                "passed": task_ok and required_ok and forbidden_ok and text_ok,
            }
        )
    summary = {
        "model": model.version,
        "checkpoint_sha256": fingerprint(checkpoint),
        "cases_file": cases_path.name,
        "cases_sha256": fingerprint(cases_path),
        "cases": len(details),
        "task_correct": sum(row["task_ok"] for row in details),
        "required_content_correct": sum(row["required_ok"] for row in details),
        "forbidden_content_absent": sum(row["forbidden_ok"] for row in details),
        "complete_human_text": sum(row["complete_human_text"] for row in details),
        "passed_cases": sum(row["passed"] for row in details),
    }
    summary["release_gate_passed"] = summary["passed_cases"] == summary["cases"]
    output.mkdir(parents=True, exist_ok=True)
    (output / "lms_release_acceptance.json").write_text(
        json.dumps({"summary": summary, "details": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Release-приёмка LMS-кандидата",
        "",
        "Набор создан отдельно от train и validation и не используется для дообучения.",
        "",
        "| Показатель | Значение |",
        "|---|---:|",
        *[f"| {key} | {value} |" for key, value in summary.items()],
        "",
        "## Кейсы",
        "",
        "| ID | Ожидаемый тип | Полученный тип | Итог |",
        "|---|---|---|---:|",
    ]
    for row in details:
        lines.append(
            f"| {row['id']} | {row['expected_task']} | {row['predicted_task']} | {row['passed']} |"
        )
    (output / "LMS_RELEASE_ACCEPTANCE_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=Path)
    args = parser.parse_args()
    try:
        evaluate(args.checkpoint, args.output, args.cases)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"Release-приёмка LMS остановлена: {error}") from error


if __name__ == "__main__":
    main()
