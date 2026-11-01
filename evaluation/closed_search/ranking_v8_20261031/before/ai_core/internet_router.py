"""Собственный обучаемый классификатор: нужен ли вопросу интернет.

Это не генератор ответов и не готовая внешняя модель. Он получает только
текст сообщения пользователя и выбирает один из двух маршрутов: ``USE_WEB``
или ``NO_WEB``. Уже известные погодные и локальные правила остаются отдельными
детерминированными защитами в ``internet_search.py``.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import torch
from torch import nn


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROUTER_ARTIFACT = ROOT / "model_artifacts" / "internet_router" / "router.pt"
FORMAT_VERSION = 1
LABELS = ("NO_WEB", "USE_WEB")
EMPTY_FEATURE = "<empty>"
WORD_PATTERN = re.compile(r"[\w+#]+", flags=re.UNICODE)
STOPWORDS = frozenset(
    {
        "а",
        "в",
        "во",
        "вы",
        "да",
        "для",
        "до",
        "и",
        "из",
        "ли",
        "мне",
        "на",
        "не",
        "о",
        "об",
        "по",
        "пожалуйста",
        "про",
        "с",
        "со",
        "это",
        "я",
    }
)


def normalize_route_text(text: str) -> str:
    """Нормализует регистр и пробелы, не выполняя текст как инструкцию."""
    if not isinstance(text, str):
        raise ValueError("Текст для internet-router должен быть строкой.")
    return " ".join(WORD_PATTERN.findall(text.casefold().replace("ё", "е")))


def route_features(text: str, stopwords: frozenset[str] = STOPWORDS) -> set[str]:
    """Создаёт прозрачные собственные признаки слов и их частей."""
    words = [word for word in normalize_route_text(text).split() if word not in stopwords]
    result = {f"word:{word}" for word in words}
    result.update(
        f"bigram:{left}/{right}" for left, right in zip(words, words[1:])
    )
    for word in words:
        if len(word) < 4:
            continue
        marked = f"^{word}$"
        for size in (3, 4):
            result.update(
                f"char:{marked[index:index + size]}"
                for index in range(len(marked) - size + 1)
            )
    return result


class RouterFeatureEncoder:
    """Компактный словарь признаков, построенный только на train split."""

    def __init__(
        self,
        vocabulary: list[str],
        idf: list[float],
        stopwords: list[str] | None = None,
    ) -> None:
        if not vocabulary or vocabulary[0] != EMPTY_FEATURE or len(vocabulary) != len(idf):
            raise ValueError("Некорректный словарь internet-router.")
        self.vocabulary = vocabulary
        self.indices = {feature: index for index, feature in enumerate(vocabulary)}
        if len(self.indices) != len(vocabulary):
            raise ValueError("Словарь internet-router содержит повторяющиеся признаки.")
        self.idf = [float(value) for value in idf]
        self.stopwords = frozenset(stopwords) if stopwords is not None else STOPWORDS

    @classmethod
    def fit(cls, texts: list[str], max_features: int = 12_000) -> "RouterFeatureEncoder":
        """Отбирает частые признаки без заглядывания в validation и test."""
        if not texts:
            raise ValueError("Нельзя обучить словарь internet-router на пустом train split.")
        if max_features < 128:
            raise ValueError("max_features internet-router должен быть не меньше 128.")
        document_counts: Counter[str] = Counter()
        for text in texts:
            document_counts.update(route_features(text, STOPWORDS))
        selected = sorted(
            document_counts,
            key=lambda feature: (-document_counts[feature], feature),
        )[:max_features]
        if not selected:
            raise ValueError("Train split internet-router не дал ни одного признака.")
        vocabulary = [EMPTY_FEATURE, *selected]
        idf = [1.0, *(
            math.log((1 + len(texts)) / (1 + document_counts[feature])) + 1
            for feature in selected
        )]
        return cls(vocabulary, idf, sorted(STOPWORDS))

    def encode_batch(
        self,
        texts: list[str],
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, list[float]]:
        """Кодирует неплотный набор признаков для быстрой EmbeddingBag-сети."""
        if not texts:
            raise ValueError("Нельзя закодировать пустой batch internet-router.")
        flat_indices: list[int] = []
        offsets: list[int] = []
        weights: list[float] = []
        coverage: list[float] = []
        for text in texts:
            extracted = route_features(text, self.stopwords)
            known = sorted(
                self.indices[feature]
                for feature in extracted
                if feature in self.indices
            )
            offsets.append(len(flat_indices))
            coverage.append(len(known) / max(1, len(extracted)))
            if not known:
                known = [0]
            flat_indices.extend(known)
            weights.extend(self.idf[index] for index in known)
        return (
            torch.tensor(flat_indices, dtype=torch.long),
            torch.tensor(offsets, dtype=torch.long),
            torch.tensor(weights, dtype=torch.float32),
            coverage,
        )


class RouterNetwork(nn.Module):
    """Небольшая собственная сеть для разреженных признаков вопроса."""

    def __init__(
        self,
        vocabulary_size: int,
        embedding_dim: int = 48,
        hidden_dim: int = 32,
        classes: int = 2,
    ) -> None:
        super().__init__()
        if vocabulary_size < 2 or embedding_dim < 4 or hidden_dim < 4 or classes < 2:
            raise ValueError("Некорректная конфигурация RouterNetwork.")
        self.embedding = nn.EmbeddingBag(
            vocabulary_size,
            embedding_dim,
            mode="sum",
            include_last_offset=False,
        )
        self.layers = nn.Sequential(
            nn.LayerNorm(embedding_dim),
            nn.Linear(embedding_dim, hidden_dim),
            nn.Tanh(),
            nn.Dropout(0.1),
            nn.Linear(hidden_dim, classes),
        )

    def forward(
        self,
        indices: torch.Tensor,
        offsets: torch.Tensor,
        weights: torch.Tensor,
    ) -> torch.Tensor:
        """Возвращает логиты классов, не создавая огромную плотную матрицу."""
        pooled = self.embedding(indices, offsets, per_sample_weights=weights)
        return self.layers(nn.functional.normalize(pooled, dim=1))


@dataclass(frozen=True)
class RouterPrediction:
    """Решение модели для одного пользовательского вопроса."""

    should_use_web: bool
    label: str
    confidence: float
    coverage: float
    accepted: bool


class InternetRouter:
    """Загружает локальные веса и принимает осторожное решение о поиске."""

    def __init__(self, checkpoint: dict) -> None:
        if checkpoint.get("format_version") != FORMAT_VERSION:
            raise ValueError("Неизвестный формат checkpoint internet-router.")
        labels = tuple(checkpoint.get("labels", ()))
        if labels != LABELS:
            raise ValueError("Internet-router должен содержать метки NO_WEB и USE_WEB.")
        thresholds = checkpoint.get("thresholds")
        if not isinstance(thresholds, dict):
            raise ValueError("В checkpoint internet-router отсутствуют пороги.")
        confidence = thresholds.get("use_web_confidence")
        coverage = thresholds.get("minimum_coverage")
        if not isinstance(confidence, int | float) or not 0.5 <= float(confidence) <= 1:
            raise ValueError("Некорректный порог confidence internet-router.")
        if not isinstance(coverage, int | float) or not 0 <= float(coverage) <= 1:
            raise ValueError("Некорректный порог coverage internet-router.")
        self.version = str(checkpoint["model_version"])
        self.labels = labels
        self.thresholds = {
            "use_web_confidence": float(confidence),
            "minimum_coverage": float(coverage),
        }
        self.encoder = RouterFeatureEncoder(
            checkpoint["vocabulary"],
            checkpoint["idf"],
            checkpoint.get("feature_stopwords"),
        )
        self.network = RouterNetwork(**checkpoint["model_config"])
        self.network.load_state_dict(checkpoint["model_state"])
        self.network.eval()

    @classmethod
    def load(cls, path: Path = DEFAULT_ROUTER_ARTIFACT) -> "InternetRouter":
        """Загружает только локальный checkpoint, без сети и сторонних моделей."""
        return cls(torch.load(path, map_location="cpu", weights_only=True))

    def predict(self, prompt: str) -> RouterPrediction:
        """Возвращает поиск только при достаточной уверенности и покрытии."""
        clean_prompt = prompt.strip() if isinstance(prompt, str) else ""
        if not clean_prompt:
            return RouterPrediction(False, "NO_WEB", 0.0, 0.0, False)
        indices, offsets, weights, coverage = self.encoder.encode_batch([clean_prompt])
        with torch.inference_mode():
            probabilities = self.network(indices, offsets, weights).softmax(dim=-1)[0]
        use_web_index = self.labels.index("USE_WEB")
        confidence = float(probabilities[use_web_index])
        accepted = (
            confidence >= self.thresholds["use_web_confidence"]
            and coverage[0] >= self.thresholds["minimum_coverage"]
        )
        return RouterPrediction(
            should_use_web=accepted,
            label="USE_WEB" if accepted else "NO_WEB",
            confidence=confidence,
            coverage=coverage[0],
            accepted=accepted,
        )

    def should_use_web(self, prompt: str) -> bool:
        """Короткий интерфейс для общего автоматического маршрута."""
        return self.predict(prompt).should_use_web
