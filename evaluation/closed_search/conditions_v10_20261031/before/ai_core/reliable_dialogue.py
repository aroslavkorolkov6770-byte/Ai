"""Контекстная сеть поверх замороженной модели одиночных QA-ответов."""

from __future__ import annotations

import re
from pathlib import Path

import torch
from torch import nn

from .reliable_qa import (
    ABSTAIN,
    FeatureEncoder,
    Prediction,
    ReliableQA,
    ROOT,
    fingerprint,
    normalize,
)

DEFAULT_DIALOGUE_ARTIFACT = ROOT / "model_artifacts/reliable_qa/dialogue.pt"
REFERENCES = re.compile(
    r"\b(его|ее|её|ему|ей|ею|него|нее|неё|нему|ней|ним|нем|нём|их|ими|ними|них|это|этот|эту|эти|этим|этих|"
    r"этой|этого|этими|такой|теперь|она|он)\b", re.IGNORECASE,
)


class ContextNetwork(nn.Module):
    """Учит взаимодействие текущей реплики и предыдущего смысла."""

    def __init__(self, input_dim: int, states: int, hidden_dim: int = 96):
        super().__init__()
        self.query = nn.Sequential(nn.Linear(input_dim, hidden_dim), nn.Tanh())
        self.state = nn.Embedding(states, hidden_dim)
        self.output = nn.Sequential(nn.Linear(hidden_dim * 3, hidden_dim), nn.Tanh(),
                                    nn.Linear(hidden_dim, states))

    def forward(self, inputs: torch.Tensor, state_ids: torch.Tensor) -> torch.Tensor:
        query = self.query(inputs)
        state = self.state(state_ids)
        return self.output(torch.cat((query, state, query * state), dim=-1))


def mask_transitions(logits: torch.Tensor, state_ids: torch.Tensor,
                     allowed: list[list[int]] | None) -> torch.Tensor:
    """Ограничивает декодер графом переходов, известным из train-структуры.

    Класс уточнения всегда доступен: граф не вынуждает отвечать.
    """
    if allowed is None:
        return logits
    mask = torch.ones_like(logits, dtype=torch.bool)
    for row, state in enumerate(state_ids.tolist()):
        mask[row, allowed[state]] = False
        mask[row, -1] = False
    return logits.masked_fill(mask, float("-inf"))


def semantic_intent_ids(prompt: str, intents: list[str],
                        markers: dict[str, list[str]]) -> list[int]:
    """Находит явные технические признаки смысла из curriculum."""
    words = normalize(prompt).split()
    matches: set[int] = set()
    for intent, intent_markers in markers.items():
        if intent not in intents:
            raise ValueError(f"Неизвестный intent в semantic_markers: {intent}")
        for raw_marker in intent_markers:
            if raw_marker.endswith("*"):
                root = normalize(raw_marker[:-1])
                matched = bool(root) and any(word.startswith(root) for word in words)
            else:
                marker_words = normalize(raw_marker).split()
                matched = bool(marker_words) and any(
                    words[offset:offset + len(marker_words)] == marker_words
                    for offset in range(len(words) - len(marker_words) + 1)
                )
            if matched:
                matches.add(intents.index(intent))
                break
    return sorted(matches)


def mask_semantic_markers(
    logits: torch.Tensor,
    prompts: list[str],
    state_ids: torch.Tensor,
    intents: list[str],
    markers: dict[str, list[str]] | None,
    allowed: list[list[int]] | None,
) -> tuple[torch.Tensor, list[list[int]]]:
    """Оставляет только цели, подтверждённые явным техническим маркером.

    Класс уточнения остаётся доступным. Если маркер противоречит состоянию,
    декодер обязан отказаться, а не проигнорировать объект из вопроса.
    """
    if not markers:
        return logits, [[] for _ in prompts]
    adjusted = logits.clone()
    evidence: list[list[int]] = []
    for row, (prompt, state) in enumerate(zip(prompts, state_ids.tolist())):
        candidates = semantic_intent_ids(prompt, intents, markers)
        if not candidates:
            evidence.append([])
            continue
        valid = candidates if allowed is None else [item for item in candidates if item in allowed[state]]
        mask = torch.ones_like(adjusted[row], dtype=torch.bool)
        mask[-1] = False
        if valid:
            mask[valid] = False
        adjusted[row] = adjusted[row].masked_fill(mask, float("-inf"))
        evidence.append(valid)
    return adjusted, evidence


def force_hard_semantic_markers(
    logits: torch.Tensor,
    prompts: list[str],
    state_ids: torch.Tensor,
    intents: list[str],
    markers: dict[str, list[str]] | None,
    allowed: list[list[int]] | None,
) -> tuple[torch.Tensor, list[list[int]]]:
    """Фиксирует однозначную цель для явного API/CSS/DOM-маркера."""
    if not markers:
        return logits, [[] for _ in prompts]
    adjusted = logits.clone()
    evidence: list[list[int]] = []
    for row, (prompt, state) in enumerate(zip(prompts, state_ids.tolist())):
        candidates = semantic_intent_ids(prompt, intents, markers)
        valid = candidates if allowed is None else [item for item in candidates if item in allowed[state]]
        if len(valid) == 1:
            adjusted[row].fill_(float("-inf"))
            adjusted[row, valid[0]] = 0.0
        evidence.append(valid)
    return adjusted, evidence


class ReliableDialogue:
    def __init__(self, qa: ReliableQA, checkpoint: dict):
        if checkpoint["intents"] != qa.intents:
            raise ValueError("Словари QA и диалогового checkpoint не совпали.")
        self.qa = qa
        self.version = checkpoint["model_version"]
        self.base_sha256 = checkpoint["base_qa_sha256"]
        self.encoder = FeatureEncoder(checkpoint["vocabulary"], checkpoint["idf"],
                                      checkpoint["feature_stopwords"])
        self.network = ContextNetwork(**checkpoint["model_config"])
        self.network.load_state_dict(checkpoint["model_state"])
        self.network.eval()
        self.thresholds = checkpoint["thresholds"]
        self.state_thresholds = checkpoint.get("state_thresholds", {})
        self.allowed = checkpoint.get("allowed_transitions")
        self.include_qa_probabilities = checkpoint.get("include_qa_probabilities", False)
        self.training_prompts = frozenset(checkpoint.get("training_prompts", []))
        self.semantic_markers = checkpoint.get("semantic_markers", {})
        self.hard_semantic_markers = checkpoint.get("hard_semantic_markers", {})

    @classmethod
    def load(cls, qa: ReliableQA, path: Path, qa_path: Path) -> "ReliableDialogue":
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
        if checkpoint["base_qa_sha256"] != fingerprint(qa_path):
            raise ValueError("Диалоговая сеть обучена для другой QA-модели.")
        return cls(qa, checkpoint)

    def predict(self, prompt: str, messages: list[dict]) -> Prediction:
        if not REFERENCES.search(prompt):
            # Явно сформулированный новый вопрос не наследует старую тему.
            return self.qa.predict(prompt)
        previous = None
        if messages and messages[-1].get("role") == "assistant":
            previous = self.qa.intent_for_answer(messages[-1].get("content", ""))
        if previous is None:
            return Prediction(ABSTAIN, None, 0.0, False, "clarification")
        inputs, coverage = self.encoder.transform([prompt])
        if self.include_qa_probabilities:
            qa_probabilities, _ = self.qa.probabilities([prompt])
            inputs = torch.cat((inputs, qa_probabilities), dim=1)
        state_ids = torch.tensor([self.qa.intents.index(previous)])
        with torch.inference_mode():
            logits = mask_transitions(self.network(inputs, state_ids), state_ids, self.allowed)
            logits, semantic_evidence = mask_semantic_markers(
                logits,
                [prompt],
                state_ids,
                self.qa.intents,
                self.semantic_markers,
                self.allowed,
            )
            logits, hard_evidence = force_hard_semantic_markers(
                logits,
                [prompt],
                state_ids,
                self.qa.intents,
                self.hard_semantic_markers,
                self.allowed,
            )
            scores = logits.softmax(-1)[0]
        values, indices = scores.topk(2)
        index = int(indices[0])
        thresholds = self.state_thresholds.get(str(int(state_ids[0])), self.thresholds)
        explicit_evidence = len(hard_evidence[0]) == 1
        minimum_coverage = 0.0 if explicit_evidence or len(semantic_evidence[0]) == 1 else thresholds["coverage"]
        accepted = (index < len(self.qa.intents)
                    and float(values[0]) >= thresholds["confidence"]
                    and float(values[0] - values[1]) >= thresholds["margin"]
                    and coverage[0] >= minimum_coverage)
        if not accepted:
            return Prediction(ABSTAIN, None, float(values[0]), False, "clarification", True)
        intent = self.qa.intents[index]
        return Prediction(self.qa.answers[intent], intent, float(values[0]), True,
                          "semantic-dialogue-catalog" if explicit_evidence
                          else "neural-dialogue-catalog", True)
