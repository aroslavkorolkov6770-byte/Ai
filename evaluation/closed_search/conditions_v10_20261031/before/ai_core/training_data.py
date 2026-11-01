"""Загрузка и проверка обучающих примеров в формате JSONL."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TrainingPair:
    """Один пример: вопрос пользователя и ожидаемый ответ модели."""

    prompt: str
    completion: str


def load_training_pairs(path: str | Path) -> list[TrainingPair]:
    """Загружает JSONL и сообщает точный номер повреждённой строки."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Датасет не найден: {source}")

    pairs: list[TrainingPair] = []
    with source.open("r", encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Некорректный JSON в строке {line_number}: {error.msg}."
                ) from error

            if not isinstance(item, dict):
                raise ValueError(f"Строка {line_number} должна содержать JSON-объект.")

            prompt = item.get("prompt")
            completion = item.get("completion")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(f"В строке {line_number} отсутствует непустой prompt.")
            if not isinstance(completion, str) or not completion.strip():
                raise ValueError(
                    f"В строке {line_number} отсутствует непустой completion."
                )

            pairs.append(TrainingPair(prompt=prompt.strip(), completion=completion.strip()))

    if not pairs:
        raise ValueError("Датасет не содержит ни одного обучающего примера.")
    return pairs
