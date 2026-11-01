"""Собирает локальные источники данных для обучения без изменения оригинала."""

from __future__ import annotations

from pathlib import Path

try:
    from .data_quality import TRAINING_TOPIC_CHOICES, filter_pairs_by_topic
    from .training_data import TrainingPair, load_training_pairs
except ImportError:
    from data_quality import TRAINING_TOPIC_CHOICES, filter_pairs_by_topic
    from training_data import TrainingPair, load_training_pairs


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = PROJECT_ROOT / "training_data" / "dataset.jsonl"
WEB_FOUNDATION_DATASET_PATH = (
    PROJECT_ROOT / "training_data" / "web_foundation.jsonl"
)
WEB_DIALOGUE_DATASET_PATH = (
    PROJECT_ROOT / "training_data" / "web_dialogue_examples.jsonl"
)
WEB_DIALOGUE_EXTENDED_DATASET_PATH = (
    PROJECT_ROOT / "training_data" / "web_dialogue_examples_v2.jsonl"
)


def load_primary_training_pairs() -> list[TrainingPair]:
    """Возвращает неизменённый исходный JSONL пользователя."""
    return load_training_pairs(DATASET_PATH)


def load_web_foundation_pairs() -> list[TrainingPair]:
    """Возвращает вручную составленные базовые примеры веб-разработки.

    Файл является дополнительным источником: его отсутствие не мешает
    запуску ранних версий проекта, а исходный датасет при этом не меняется.
    """
    if not WEB_FOUNDATION_DATASET_PATH.exists():
        return []
    return load_training_pairs(WEB_FOUNDATION_DATASET_PATH)


def load_training_corpus() -> list[TrainingPair]:
    """Объединяет все разрешённые локальные источники в фиксированном порядке."""
    return [*load_primary_training_pairs(), *load_web_foundation_pairs()]


def load_training_pairs_for_topic(topic: str) -> list[TrainingPair]:
    """Возвращает тематическую выборку и не фильтрует ручной веб-набор.

    Классификатор исходного смешанного набора — это прозрачная эвристика.
    Новый файл уже создан только для веб-разработки, поэтому при выборе
    ``web`` он добавляется целиком, без риска потерять корректную запись из-за
    ключевых слов. Для ``all`` дополнительный набор также сохраняется.
    """
    normalized_topic = topic.strip().lower()
    if normalized_topic not in TRAINING_TOPIC_CHOICES:
        choices = ", ".join(TRAINING_TOPIC_CHOICES)
        raise ValueError(f"Неизвестная тема {topic!r}. Доступно: {choices}.")

    primary_pairs = load_primary_training_pairs()
    web_foundation_pairs = load_web_foundation_pairs()
    if normalized_topic == "all":
        return [*primary_pairs, *web_foundation_pairs]

    selected_pairs = filter_pairs_by_topic(primary_pairs, normalized_topic)
    if normalized_topic == "web":
        return [*selected_pairs, *web_foundation_pairs]
    return selected_pairs
