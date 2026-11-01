"""Собственный классификатор решения о веб-поиске по seed-набору диалогов.

Модуль не генерирует свободный текст и не читает готовые ответы, system-поля,
evidence или поисковые запросы из обучающего ZIP. Его единственная задача —
осторожно дополнить уже существующие правила маршрутизации решением: нужен ли
контролируемый поиск для текущей реплики пользователя.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from .internet_router import RouterFeatureEncoder, RouterNetwork
from .web_dialogue_dataset import BROWSE_LABELS


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEB_DIALOGUE_POLICY_ARTIFACT = (
    ROOT / "model_artifacts" / "web_dialogue_policy" / "policy.pt"
)
FORMAT_VERSION = 1
LABELS = BROWSE_LABELS
SEARCH_LABELS = frozenset({"must_browse", "should_browse"})


@dataclass(frozen=True)
class WebDialoguePolicyPrediction:
    """Результат локального выбора веб-маршрута для одной реплики."""

    browse_label: str
    confidence: float
    margin: float
    coverage: float
    accepted: bool
    should_search: bool


class WebDialoguePolicy:
    """Загружает собственные локальные веса и принимает осторожное решение.

    Нулевая уверенность, неизвестные слова и пустой текст не могут включить
    поиск. Положительное решение возможно только для ``must_browse`` или
    ``should_browse`` при сохранённых порогах качества.
    """

    def __init__(self, checkpoint: dict[str, Any]) -> None:
        if checkpoint.get("format_version") != FORMAT_VERSION:
            raise ValueError("Неизвестный формат checkpoint web-dialogue-policy.")
        labels = tuple(checkpoint.get("labels", ()))
        if labels != LABELS:
            raise ValueError("Web-dialogue-policy содержит неизвестный порядок browse_label.")
        version = checkpoint.get("model_version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("В checkpoint web-dialogue-policy отсутствует версия.")
        thresholds = checkpoint.get("thresholds")
        if not isinstance(thresholds, dict):
            raise ValueError("В checkpoint web-dialogue-policy отсутствуют пороги.")
        search_confidence = thresholds.get("search_confidence")
        minimum_margin = thresholds.get("minimum_margin")
        minimum_coverage = thresholds.get("minimum_coverage")
        for name, value in (
            ("search_confidence", search_confidence),
            ("minimum_margin", minimum_margin),
            ("minimum_coverage", minimum_coverage),
        ):
            if not isinstance(value, int | float) or not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"Некорректный порог {name} web-dialogue-policy.")
        if float(search_confidence) < 0.2:
            raise ValueError("Порог search_confidence web-dialogue-policy слишком низкий.")

        self.version = version.strip()
        self.labels = labels
        self.thresholds = {
            "search_confidence": float(search_confidence),
            "minimum_margin": float(minimum_margin),
            "minimum_coverage": float(minimum_coverage),
        }
        self.encoder = RouterFeatureEncoder(
            checkpoint["vocabulary"],
            checkpoint["idf"],
            checkpoint.get("feature_stopwords"),
        )
        model_config = checkpoint.get("model_config")
        if not isinstance(model_config, dict) or model_config.get("classes") != len(LABELS):
            raise ValueError("Некорректная конфигурация сети web-dialogue-policy.")
        self.network = RouterNetwork(**model_config)
        self.network.load_state_dict(checkpoint["model_state"])
        self.network.eval()

    @classmethod
    def load(
        cls,
        path: Path = DEFAULT_WEB_DIALOGUE_POLICY_ARTIFACT,
    ) -> "WebDialoguePolicy":
        """Загружает только локальный checkpoint без запроса к сети."""
        return cls(torch.load(path, map_location="cpu", weights_only=True))

    def predict(self, prompt: str) -> WebDialoguePolicyPrediction:
        """Возвращает решение и диагностические числа без текста архива."""
        clean_prompt = prompt.strip() if isinstance(prompt, str) else ""
        if not clean_prompt:
            return WebDialoguePolicyPrediction(
                browse_label="must_not_browse",
                confidence=0.0,
                margin=0.0,
                coverage=0.0,
                accepted=False,
                should_search=False,
            )
        indices, offsets, weights, coverage = self.encoder.encode_batch([clean_prompt])
        with torch.inference_mode():
            probabilities = self.network(indices, offsets, weights).softmax(dim=-1)[0]
        top_values, top_indices = torch.topk(probabilities, k=2)
        label = self.labels[int(top_indices[0])]
        confidence = float(top_values[0])
        margin = float(top_values[0] - top_values[1])
        accepted = (
            label in SEARCH_LABELS
            and confidence >= self.thresholds["search_confidence"]
            and margin >= self.thresholds["minimum_margin"]
            and coverage[0] >= self.thresholds["minimum_coverage"]
        )
        return WebDialoguePolicyPrediction(
            browse_label=label,
            confidence=confidence,
            margin=margin,
            coverage=coverage[0],
            accepted=accepted,
            should_search=accepted,
        )

    def should_search(self, prompt: str) -> bool:
        """Короткий интерфейс для единого автоматического маршрута API."""
        return self.predict(prompt).should_search
