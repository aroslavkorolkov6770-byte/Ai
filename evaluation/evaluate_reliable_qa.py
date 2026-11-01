"""Приёмка одиночных QA-ответов. Test не участвует в обучении или калибровке."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_core.reliable_qa import ABSTAIN, ReliableQA, fingerprint


def evaluate(checkpoint: Path, cases_path: Path, output: Path) -> dict:
    model = ReliableQA.load(checkpoint)
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    correct, wrong, refused_known, refused_unknown = 0, 0, 0, 0
    details = []
    started = time.perf_counter()
    for case in cases:
        result = model.predict(case["prompt"])
        expected = case["intent"]
        if result.accepted:
            if result.intent == expected:
                correct += 1
            else:
                wrong += 1
        elif expected is None:
            refused_unknown += 1
        else:
            refused_known += 1
        # Проверяется точная принадлежность полному ответу, а не произвольная
        # метрика совпадения букв, которая могла поощрять бессмыслицу.
        complete = result.text in set(model.answers.values()) | {ABSTAIN}
        details.append({**case, "predicted": result.intent, "accepted": result.accepted,
                        "confidence": result.confidence, "text": result.text,
                        "complete_answer": complete})
    supported = sum(case["intent"] is not None for case in cases)
    precision = correct / max(1, correct + wrong)
    coverage = correct / max(1, supported)
    summary = {"model": model.version, "checkpoint_sha256": fingerprint(checkpoint),
               "cases_sha256": fingerprint(cases_path), "cases": len(cases),
               "supported": supported, "accepted_correct": correct,
               "accepted_wrong": wrong, "refused_known": refused_known,
               "refused_unknown": refused_unknown, "precision": precision,
               "supported_correct_fraction": coverage,
               "complete_answer_fraction": sum(row["complete_answer"] for row in details) / len(cases),
               "mean_ms": 1000 * (time.perf_counter() - started) / len(cases)}
    # Это узкая приёмка каталожной QA, а не подтверждение свободной генерации.
    summary["qa_gate_passed"] = (
        precision >= 0.97 and coverage >= 0.75
        and summary["complete_answer_fraction"] == 1.0
        and refused_unknown == len(cases) - supported
    )
    output.mkdir(parents=True, exist_ok=True)
    report = {"summary": summary, "details": details}
    (output / "qa_acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Приёмка собственной QA-модели", "",
             "Режим: нейронный выбор проверенного полного ответа; свободная генерация здесь не оценивается.", "",
             "Отказы не засчитываются как правильные ответы на известные темы.", "",
             "| Показатель | Значение |", "|---|---:|",
             *[f"| {key} | {value} |" for key, value in summary.items()], "",
             "## Все проверенные запросы", ""]
    for row in details:
        lines += [f"- Вопрос: {row['prompt']}", f"  Ожидается: {row['intent']}; выбран: {row['predicted']}.",
                  f"  Ответ: {row['text']}", ""]
    (output / "QA_ACCEPTANCE_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=ROOT / "evaluation/qa_acceptance.jsonl")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        evaluate(args.checkpoint, args.cases, args.output)
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"QA-приёмка остановлена: {error}") from error
