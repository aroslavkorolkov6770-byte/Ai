"""Воспроизводимая подготовка датасета и защита выборок от утечек."""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections import defaultdict
from dataclasses import dataclass

try:
    from .training_data import TrainingPair
except ImportError:
    from training_data import TrainingPair


@dataclass(frozen=True)
class DatasetSplit:
    """Три непересекающиеся части одного набора примеров."""

    train: list[TrainingPair]
    validation: list[TrainingPair]
    test: list[TrainingPair]


def normalize_prompt(text: str) -> str:
    """Приводит вопрос к виду, удобному для поиска дублей.

    Символы ``+`` и ``#`` сохраняются: они различают языки C++, C# и похожие
    технические обозначения. Раньше их удаление ошибочно объединяло разные
    вопросы и скрывало реальную структуру датасета.
    """
    if not isinstance(text, str):
        raise TypeError("Вопрос для нормализации должен быть строкой.")
    lowered = text.casefold()
    without_punctuation = re.sub(
        r"[^\w\s+#]",
        " ",
        lowered,
        flags=re.UNICODE,
    )
    return " ".join(without_punctuation.split())


def dataset_fingerprint(pairs: list[TrainingPair]) -> str:
    """Возвращает SHA-256 отпечаток точного порядка примеров датасета."""
    digest = hashlib.sha256()
    for pair in pairs:
        payload = json.dumps(
            {"prompt": pair.prompt, "completion": pair.completion},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(payload)
        digest.update(b"\n")
    return digest.hexdigest()


def _group_pairs_by_prompt(
    pairs: list[TrainingPair],
) -> list[list[TrainingPair]]:
    """Собирает одинаковые вопросы в одну группу для защиты от утечки."""
    groups: dict[str, list[TrainingPair]] = defaultdict(list)
    for pair in pairs:
        groups[normalize_prompt(pair.prompt)].append(pair)
    return list(groups.values())


def _target_count(total: int, ratio: float) -> int:
    """Считает размер выборки и оставляет минимум один пример при ratio > 0."""
    if ratio == 0.0:
        return 0
    return max(1, round(total * ratio))


def _take_groups(
    groups: list[list[TrainingPair]],
    target_count: int,
    start_index: int,
) -> tuple[list[TrainingPair], int]:
    """Берёт целые группы, пока целевой размер не будет достигнут."""
    selected: list[TrainingPair] = []
    index = start_index
    while len(selected) < target_count and index < len(groups):
        selected.extend(groups[index])
        index += 1
    return selected, index


def split_dataset(
    pairs: list[TrainingPair],
    validation_ratio: float = 0.1,
    test_ratio: float = 0.1,
    seed: int = 42,
) -> DatasetSplit:
    """Делит данные воспроизводимо и не разделяет дубли между выборками."""
    if not pairs:
        raise ValueError("Нельзя разделить пустой датасет.")
    if not 0.0 <= validation_ratio < 1.0:
        raise ValueError("validation_ratio должен быть от 0 до 1.")
    if not 0.0 <= test_ratio < 1.0:
        raise ValueError("test_ratio должен быть от 0 до 1.")
    if validation_ratio + test_ratio >= 1.0:
        raise ValueError("Сумма validation_ratio и test_ratio должна быть меньше 1.")

    groups = _group_pairs_by_prompt(pairs)
    random.Random(seed).shuffle(groups)
    validation_target = _target_count(len(pairs), validation_ratio)
    test_target = _target_count(len(pairs), test_ratio)
    test, next_index = _take_groups(groups, test_target, 0)
    validation, next_index = _take_groups(groups, validation_target, next_index)
    train = [pair for group in groups[next_index:] for pair in group]

    if not train:
        raise ValueError("После разделения не осталось примеров для обучения.")
    if validation_ratio > 0.0 and not validation:
        raise ValueError("Не удалось сформировать validation-выборку.")
    if test_ratio > 0.0 and not test:
        raise ValueError("Не удалось сформировать test-выборку.")

    return DatasetSplit(train=train, validation=validation, test=test)


def prompt_overlap(split: DatasetSplit) -> set[str]:
    """Возвращает нормализованные вопросы, попавшие более чем в одну выборку."""
    memberships: dict[str, set[str]] = defaultdict(set)
    for name, pairs in (
        ("train", split.train),
        ("validation", split.validation),
        ("test", split.test),
    ):
        for pair in pairs:
            memberships[normalize_prompt(pair.prompt)].add(name)
    return {
        prompt
        for prompt, parts in memberships.items()
        if len(parts) > 1
    }
