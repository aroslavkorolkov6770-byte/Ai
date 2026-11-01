"""Собственная обучаемая сеть выбора смысла с законченным декодированием.

Это ограниченная QA-модель, а не свободная языковая модель. Её выходной
словарь состоит из идентификаторов проверенных ответов. Поэтому уверенность
в выборе смысла и корректность текста измеряются независимо.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import torch
from torch import nn

ROOT = Path(__file__).resolve().parent.parent
CURRICULUM_PATH = ROOT / "training_data/qa_curriculum.json"
DEFAULT_ARTIFACT = ROOT / "model_artifacts/reliable_qa/qa.pt"
ABSTAIN = (
    "Я не хочу отвечать наугад. Расскажи, что тебе нужно: факт, совет, "
    "объяснение, помощь с текстом или просто разговор. Я подстроюсь под тему."
)
WORD_PATTERN = re.compile(r"[\w+#]+", re.UNICODE)
FUNCTION_WORDS = frozenset(
    "как какой какая каким какое какие что зачем почему для из на в во с со "
    "к ко и а но по о об у его ее их это такой таким ли пожалуйста мне "
    "нужен нужна нужно нужно хочу надо можно пример покажи сделать использовать "
    "используй через помощью свойство задай задать добавить".split()
)
TRAIN_FUNCTION_WORDS = FUNCTION_WORDS | frozenset(
    "напиши расскажи покажите показ какова какого помощью какой-то каким-то "
    "чем пусть нужен нужно пожалуйста странице документе html-документе "
    "новый новое новую другой другую текущую это этом этого тебя тебе твои "
    "один одной некоторый про вместо".split()
)


def normalize(text: str) -> str:
    """Стабильное представление регистра и пробелов без потери C++ и C#."""
    return " ".join(WORD_PATTERN.findall(text.casefold().replace("ё", "е")))


def features(text: str, stopwords: frozenset[str] = FUNCTION_WORDS) -> set[str]:
    """Собственные признаки слов и их частей; чужих словарей здесь нет."""
    words = [word for word in normalize(text).split() if word not in stopwords]
    result = {"w:" + word for word in words}
    result.update("b:" + first + "/" + second for first, second in zip(words, words[1:]))
    for word in words:
        marked = "^" + word + "$"
        for size in (3, 4, 5):
            result.update("c:" + marked[i:i + size] for i in range(len(marked) - size + 1))
    return result


class FeatureEncoder:
    """Обучает веса признаков только на тренировочных вопросах."""

    def __init__(self, vocabulary: list[str], idf: list[float],
                 stopwords: list[str] | None = None):
        if len(vocabulary) != len(idf) or not vocabulary:
            raise ValueError("Некорректный словарь QA-признаков.")
        self.vocabulary = vocabulary
        self.indices = {feature: index for index, feature in enumerate(vocabulary)}
        self.idf = torch.tensor(idf, dtype=torch.float32)
        self.stopwords = frozenset(stopwords) if stopwords is not None else FUNCTION_WORDS

    @classmethod
    def fit(cls, texts: list[str]) -> "FeatureEncoder":
        counts = Counter(feature for text in texts for feature in features(text, TRAIN_FUNCTION_WORDS))
        vocabulary = sorted(counts)
        idf = [math.log((1 + len(texts)) / (1 + counts[item])) + 1 for item in vocabulary]
        return cls(vocabulary, idf, sorted(TRAIN_FUNCTION_WORDS))

    def transform(self, texts: list[str]) -> tuple[torch.Tensor, list[float]]:
        matrix = torch.zeros(len(texts), len(self.vocabulary))
        coverage = []
        for row, text in enumerate(texts):
            extracted = features(text, self.stopwords)
            known = [self.indices[item] for item in extracted if item in self.indices]
            if known:
                matrix[row, known] = self.idf[known]
            coverage.append(len(known) / max(1, len(extracted)))
        return nn.functional.normalize(matrix, dim=1), coverage


class IntentNetwork(nn.Module):
    """Два обучаемых слоя: признаки вопроса → скрытое состояние → смысл."""

    def __init__(self, input_dim: int, classes: int, hidden_dim: int = 96):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_dim, hidden_dim), nn.Tanh(),
            nn.Dropout(0.1), nn.Linear(hidden_dim, classes),
        )

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return self.layers(inputs)


@dataclass(frozen=True)
class Prediction:
    text: str
    intent: str | None
    confidence: float
    accepted: bool
    source: str = "neural-catalog"
    context_used: bool = False


def load_curriculum(path: Path = CURRICULUM_PATH) -> dict:
    """Строго проверяет схему и разделение формулировок до обучения."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not data.get("intents"):
        raise ValueError("Неизвестная схема curriculum.")
    seen: dict[str, str] = {}
    seen_ids: set[str] = set()
    for entry in data["intents"]:
        if not isinstance(entry.get("id"), str) or entry["id"] in seen_ids:
            raise ValueError("Идентификаторы ответов должны быть уникальными.")
        seen_ids.add(entry["id"])
        if not isinstance(entry.get("answer"), str) or not entry["answer"].strip():
            raise ValueError("Ответ не может быть пустым.")
    for entry in [*data["intents"], data["unknown"]]:
        for split in ("train", "validation"):
            if not entry.get(split):
                raise ValueError("Для каждой задачи нужны train и validation.")
            for text in entry[split]:
                key = normalize(text)
                if not key or key in seen:
                    raise ValueError(f"Повтор или пересечение curriculum: {text}")
                seen[key] = split
    return data


class ReliableQA:
    """Запускает собственные веса и выдаёт полный ответ либо уточнение."""

    def __init__(self, checkpoint: dict):
        if checkpoint.get("format_version") != 1:
            raise ValueError("Неизвестный формат QA-checkpoint.")
        self.version = checkpoint["model_version"]
        self.intents = checkpoint["intents"]
        self.answers = checkpoint["answers"]
        self.thresholds = checkpoint["thresholds"]
        self.encoder = FeatureEncoder(checkpoint["vocabulary"], checkpoint["idf"],
                                      checkpoint.get("feature_stopwords"))
        self.network = IntentNetwork(**checkpoint["model_config"])
        self.network.load_state_dict(checkpoint["model_state"])
        self.network.eval()

    @classmethod
    def load(cls, path: Path = DEFAULT_ARTIFACT) -> "ReliableQA":
        return cls(torch.load(path, map_location="cpu", weights_only=True))

    def probabilities(self, prompts: list[str]) -> tuple[torch.Tensor, list[float]]:
        inputs, coverage = self.encoder.transform(prompts)
        with torch.inference_mode():
            return self.network(inputs).softmax(dim=-1), coverage

    def predict(self, prompt: str) -> Prediction:
        probabilities, coverage = self.probabilities([prompt])
        values, indices = probabilities[0].topk(2)
        index = int(indices[0])
        confidence = float(values[0])
        accepted = (
            index < len(self.intents)
            and confidence >= self.thresholds["confidence"]
            and float(values[0] - values[1]) >= self.thresholds["margin"]
            and coverage[0] >= self.thresholds["coverage"]
        )
        if not accepted:
            return Prediction(ABSTAIN, None, confidence, False, "clarification")
        intent = self.intents[index]
        return Prediction(self.answers[intent], intent, confidence, True)

    def intent_for_answer(self, text: str) -> str | None:
        """Восстанавливает явное состояние последней реплики из нашего каталога."""
        return next((key for key, value in self.answers.items() if value == text), None)


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
