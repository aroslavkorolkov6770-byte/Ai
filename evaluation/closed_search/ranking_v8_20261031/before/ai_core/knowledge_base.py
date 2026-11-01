"""Локальная точная база обучающих примеров как временная страховка качества."""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

try:
    from .dataset_tools import normalize_prompt
    from .training_data import TrainingPair, load_training_pairs
except ImportError:
    from dataset_tools import normalize_prompt
    from training_data import TrainingPair, load_training_pairs


class LocalKnowledgeBase:
    """Возвращает сохранённый ответ только для точного вопроса из датасета.

    Этот слой не заменяет обучение. Он позволяет проверить интерфейс и дать
    правильный ответ на уже известный пример, пока генеративная модель ещё
    обучается и не умеет хорошо обобщать.
    """

    def __init__(self, pairs: list[TrainingPair]) -> None:
        grouped_answers: dict[str, set[str]] = {}
        for pair in pairs:
            key = self.normalize(pair.prompt)
            grouped_answers.setdefault(key, set()).add(pair.completion)

        # Для конфликтного вопроса нельзя выбирать первый попавшийся ответ:
        # это превращает ошибку данных в уверенный, но случайный ответ API.
        self._answers = {
            key: next(iter(answers))
            for key, answers in grouped_answers.items()
            if len(answers) == 1
        }
        self._conflicting_prompts = {
            key
            for key, answers in grouped_answers.items()
            if len(answers) > 1
        }
        self._questions = [
            (key, set(key.split()), answer)
            for key, answer in self._answers.items()
        ]

    @staticmethod
    def normalize(text: str) -> str:
        """Приводит регистр, пробелы и знаки препинания к единому виду."""
        return normalize_prompt(text)

    @property
    def size(self) -> int:
        return len(self._answers)

    @property
    def conflict_count(self) -> int:
        """Возвращает число вопросов с несколькими разными ответами."""
        return len(self._conflicting_prompts)

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "LocalKnowledgeBase":
        """Создаёт базу из локального JSONL-файла."""
        return cls(load_training_pairs(path))

    def lookup(self, prompt: str) -> str | None:
        """Возвращает ответ для точного нормализованного совпадения."""
        return self._answers.get(self.normalize(prompt))

    def is_conflicted_prompt(self, prompt: str) -> bool:
        """Проверяет, есть ли для вопроса несколько противоречивых ответов."""
        return self.normalize(prompt) in self._conflicting_prompts

    def lookup_similar(
        self,
        prompt: str,
        threshold: float = 0.70,
    ) -> tuple[str, float] | None:
        """Ищет ближайший вопрос по строковому и словарному сходству.

        Это прозрачный baseline без готовой языковой модели. Он используется
        только при достаточно высоком сходстве, чтобы не подменять случайный
        вопрос неподходящим ответом.
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("Порог сходства должен быть между 0 и 1.")

        normalized_prompt = self.normalize(prompt)
        prompt_words = set(normalized_prompt.split())
        best_answer: str | None = None
        best_score = 0.0
        for candidate, candidate_words, answer in self._questions:
            sequence_score = SequenceMatcher(
                None,
                normalized_prompt,
                candidate,
            ).ratio()
            union = prompt_words | candidate_words
            word_score = (
                len(prompt_words & candidate_words) / len(union)
                if union
                else 0.0
            )
            score = sequence_score * 0.65 + word_score * 0.35
            if score > best_score:
                best_answer = answer
                best_score = score

        if best_answer is None or best_score < threshold:
            return None
        return best_answer, best_score
