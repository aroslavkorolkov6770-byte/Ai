"""Проверяет смысл follow-up, пользу контекста и реакцию на чужую историю."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_core.reliable_qa import ABSTAIN, ReliableQA, fingerprint, normalize
from ai_core.reliable_dialogue import ReliableDialogue


def evaluate(qa_path: Path, dialogue_path: Path, output: Path,
             cases_path: Path | None = None) -> dict:
    qa = ReliableQA.load(qa_path)
    model = ReliableDialogue.load(qa, dialogue_path, qa_path)
    cases_path = cases_path or ROOT / "evaluation/dialogue_acceptance.jsonl"
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    details = []
    for case in cases:
        messages = [{"role": "assistant", "content": qa.answers[case["previous"]]}]
        result = model.predict(case["prompt"], messages)
        without = model.predict(case["prompt"], [])
        wrong_contexts = []
        if model.allowed is not None and case["intent"] is not None:
            target = qa.intents.index(case["intent"])
            for state, allowed in enumerate(model.allowed[:-1]):
                if not allowed or target in allowed:
                    continue
                wrong = model.predict(
                    case["prompt"],
                    [{"role": "assistant", "content": qa.answers[qa.intents[state]]}],
                )
                wrong_contexts.append({
                    "previous": qa.intents[state],
                    "predicted": wrong.intent,
                    "accepted": wrong.accepted,
                    "valid_transition": (
                        not wrong.accepted
                        or qa.intents.index(wrong.intent) in allowed
                    ),
                })
        details.append({**case, "predicted": result.intent, "accepted": result.accepted,
                        "confidence": result.confidence, "text": result.text,
                        "context_used": result.context_used,
                        "without_context_intent": without.intent,
                        "wrong_contexts": wrong_contexts})
    accepted = [row for row in details if row["accepted"]]
    supported = [row for row in details if row["intent"] is not None]
    rejection_cases = [row for row in details if row["intent"] is None]
    correct = sum(row["accepted"] and row["predicted"] == row["intent"]
                  for row in supported)
    wrong = sum(row["accepted"] and row["predicted"] != row["intent"]
                for row in details)
    rejections_correct = sum(not row["accepted"] for row in rejection_cases)
    complete = sum(row["text"] in {*qa.answers.values(), ABSTAIN} for row in details)
    wrong_contexts = [context for row in details for context in row["wrong_contexts"]]
    training_overlap = sorted({normalize(row["prompt"]) for row in details}
                              & model.training_prompts)
    summary = {"model": model.version, "qa_sha256": fingerprint(qa_path),
               "dialogue_sha256": fingerprint(dialogue_path),
               "cases_file": cases_path.name,
               "cases_sha256": fingerprint(cases_path), "cases": len(cases),
               "supported": len(supported), "rejection_cases": len(rejection_cases),
               "accepted_correct": correct, "accepted_wrong": wrong,
               "rejections_correct": rejections_correct,
               "clarifications": len(cases) - len(accepted),
               "without_context_accepted": sum(row["without_context_intent"] is not None
                                               for row in details),
               "wrong_context_attempts": len(wrong_contexts),
               "wrong_context_accepted": sum(row["accepted"] for row in wrong_contexts),
               "wrong_context_invalid": sum(not row["valid_transition"]
                                            for row in wrong_contexts),
               "correct_fraction": correct / max(1, len(supported)),
               "rejection_accuracy": (
                   rejections_correct / len(rejection_cases) if rejection_cases else 1.0
               ),
               "overall_accuracy": (correct + rejections_correct) / len(cases),
               "precision": correct / max(1, len(accepted)),
               "complete_answer_fraction": complete / len(cases),
               "training_prompt_overlap": len(training_overlap),
               "context_used": sum(row["context_used"] for row in details)}
    summary["dialogue_gate_passed"] = (
        summary["precision"] == 1.0
        and summary["correct_fraction"] >= 0.95
        and summary["rejection_accuracy"] == 1.0
        and summary["accepted_wrong"] == 0
        and summary["without_context_accepted"] == 0
        and summary["wrong_context_invalid"] == 0
        and summary["complete_answer_fraction"] == 1.0
        and summary["training_prompt_overlap"] == 0
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "dialogue_acceptance.json").write_text(json.dumps({"summary": summary, "details": details},
                                                               ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Проверка контекстной QA-сети", "",
             "История задана правильным предыдущим ответом. Реальные последовательные API-запросы проверяются отдельно.",
             "", "| Показатель | Значение |", "|---|---:|",
             *[f"| {key} | {value} |" for key, value in summary.items()], ""]
    for row in details:
        lines += [f"- Контекст: {row['previous']}. Вопрос: {row['prompt']}",
                  f"  Цель: {row['intent']}, результат: {row['predicted']}.", f"  {row['text']}", ""]
    (output / "DIALOGUE_ACCEPTANCE_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-checkpoint", type=Path, required=True)
    parser.add_argument("--dialogue-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=Path)
    args = parser.parse_args()
    try:
        evaluate(args.qa_checkpoint, args.dialogue_checkpoint, args.output, args.cases)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        raise SystemExit(f"Проверка диалога остановлена: {error}") from error
