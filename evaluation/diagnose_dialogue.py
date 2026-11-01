"""Показывает сырые оценки диалоговой сети без изменения checkpoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_core.reliable_dialogue import (
    ReliableDialogue,
    force_hard_semantic_markers,
    mask_semantic_markers,
    mask_transitions,
)
from ai_core.reliable_qa import ReliableQA


def score(model: ReliableDialogue, prompt: str, state: str) -> dict:
    inputs, coverage = model.encoder.transform([prompt])
    if model.include_qa_probabilities:
        qa_probabilities, _ = model.qa.probabilities([prompt])
        inputs = torch.cat((inputs, qa_probabilities), dim=1)
    state_id = torch.tensor([model.qa.intents.index(state)])
    with torch.inference_mode():
        logits = mask_transitions(model.network(inputs, state_id), state_id, model.allowed)
        logits, _ = mask_semantic_markers(
            logits,
            [prompt],
            state_id,
            model.qa.intents,
            model.semantic_markers,
            model.allowed,
        )
        logits, _ = force_hard_semantic_markers(
            logits,
            [prompt],
            state_id,
            model.qa.intents,
            model.hard_semantic_markers,
            model.allowed,
        )
        probabilities = logits.softmax(-1)[0]
    values, indices = probabilities.topk(2)
    index = int(indices[0])
    return {
        "predicted": model.qa.intents[index] if index < len(model.qa.intents) else None,
        "confidence": float(values[0]),
        "margin": float(values[0] - values[1]),
        "coverage": coverage[0],
    }


def diagnose(qa_path: Path, dialogue_path: Path) -> None:
    qa = ReliableQA.load(qa_path)
    model = ReliableDialogue.load(qa, dialogue_path, qa_path)
    cases_path = ROOT / "evaluation/dialogue_acceptance.jsonl"
    cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = []
    for case in cases:
        correct = score(model, case["prompt"], case["previous"])
        wrong = score(model, case["prompt"], "greeting")
        rows.append({**case, "correct_state": correct, "wrong_state": wrong})

    sweeps = []
    for confidence in (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.92):
        accepted_correct = accepted_wrong = wrong_state_accepted = 0
        for row in rows:
            result = row["correct_state"]
            accepted = (
                result["predicted"] is not None
                and result["confidence"] >= confidence
                and result["margin"] >= 0.05
                and result["coverage"] >= 0.25
            )
            if accepted and result["predicted"] == row["intent"]:
                accepted_correct += 1
            elif accepted:
                accepted_wrong += 1
            wrong = row["wrong_state"]
            wrong_state_accepted += int(
                wrong["predicted"] is not None
                and wrong["confidence"] >= confidence
                and wrong["margin"] >= 0.05
                and wrong["coverage"] >= 0.25
            )
        sweeps.append({
            "confidence": confidence,
            "accepted_correct": accepted_correct,
            "accepted_wrong": accepted_wrong,
            "wrong_state_accepted": wrong_state_accepted,
        })
    print(json.dumps({"threshold_sweep": sweeps, "cases": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-checkpoint", type=Path, required=True)
    parser.add_argument("--dialogue-checkpoint", type=Path, required=True)
    arguments = parser.parse_args()
    diagnose(arguments.qa_checkpoint, arguments.dialogue_checkpoint)
