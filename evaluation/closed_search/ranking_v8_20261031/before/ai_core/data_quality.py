"""Неразрушающий анализ качества локального JSONL-датасета."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean

try:
    from .dataset_tools import normalize_prompt
    from .training_data import TrainingPair
except ImportError:
    from dataset_tools import normalize_prompt
    from training_data import TrainingPair


@dataclass(frozen=True)
class PromptConflict:
    """Один нормализованный вопрос с несколькими разными ответами."""

    normalized_prompt: str
    source_prompts: tuple[str, ...]
    completions: tuple[str, ...]


@dataclass(frozen=True)
class DatasetQualityReport:
    """Сводка повторов, конфликтов и длины текстов в датасете."""

    total_examples: int
    unique_normalized_prompts: int
    duplicate_prompt_groups: int
    duplicate_rows: int
    conflicting_prompt_groups: int
    mean_prompt_characters: float
    mean_completion_characters: float
    conflicts: tuple[PromptConflict, ...]


# Это только прозрачная карта текущих примеров. Она не меняет датасет и не
# используется в обучении до отдельного решения о целевой тематике модели.
TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "web",
        (
            "html",
            "css",
            "javascript",
            "react",
            "браузер",
            "страниц",
            "тег",
        ),
    ),
    (
        "python",
        (
            "python",
            "pip",
            "django",
            "flask",
            "pandas",
            "numpy",
        ),
    ),
    (
        "data",
        (
            "sql",
            "база данных",
            "csv",
            "json",
            "таблиц",
        ),
    ),
    (
        "system",
        (
            "windows",
            "linux",
            "macos",
            "файл",
            "папк",
            "команд",
            "терминал",
        ),
    ),
    (
        "math",
        (
            "числ",
            "формул",
            "процент",
            "площад",
            "квадрат",
            "делен",
            "метр",
            "градус",
            "час",
            "минут",
        ),
    ),
)

TOPIC_LABELS = {
    "web": "Веб-разработка",
    "python": "Python",
    "data": "Данные и базы",
    "system": "Операционные системы и команды",
    "math": "Математика и конвертации",
    "other": "Остальные смешанные темы",
}
TRAINING_TOPIC_CHOICES = ("all", *TOPIC_LABELS.keys())


def analyze_dataset_quality(pairs: list[TrainingPair]) -> DatasetQualityReport:
    """Строит отчёт без изменения порядка или содержимого примеров."""
    if not pairs:
        raise ValueError("Нельзя анализировать пустой датасет.")

    groups: dict[str, list[TrainingPair]] = defaultdict(list)
    for pair in pairs:
        groups[normalize_prompt(pair.prompt)].append(pair)

    duplicate_groups = [
        grouped_pairs
        for grouped_pairs in groups.values()
        if len(grouped_pairs) > 1
    ]
    conflicts: list[PromptConflict] = []
    for normalized_prompt, grouped_pairs in groups.items():
        completions = tuple(sorted({pair.completion for pair in grouped_pairs}))
        if len(completions) > 1:
            source_prompts = tuple(
                sorted({pair.prompt for pair in grouped_pairs})
            )
            conflicts.append(
                PromptConflict(
                    normalized_prompt=normalized_prompt,
                    source_prompts=source_prompts,
                    completions=completions,
                )
            )

    return DatasetQualityReport(
        total_examples=len(pairs),
        unique_normalized_prompts=len(groups),
        duplicate_prompt_groups=len(duplicate_groups),
        duplicate_rows=sum(len(group) - 1 for group in duplicate_groups),
        conflicting_prompt_groups=len(conflicts),
        mean_prompt_characters=mean(len(pair.prompt) for pair in pairs),
        mean_completion_characters=mean(
            len(pair.completion) for pair in pairs
        ),
        conflicts=tuple(sorted(conflicts, key=lambda item: item.normalized_prompt)),
    )


def classify_topic(pair: TrainingPair) -> str:
    """Относит пару к первой подходящей прозрачной эвристической теме."""
    text = normalize_prompt(f"{pair.prompt} {pair.completion}")
    words = set(text.split())
    for topic, keywords in TOPIC_RULES:
        if any(
            _matches_topic_keyword(text, words, keyword)
            for keyword in keywords
        ):
            return topic
    return "other"


def _matches_topic_keyword(
    text: str,
    words: set[str],
    keyword: str,
) -> bool:
    """Ищет термин по словам, не путая его с частью другого слова."""
    if " " in keyword:
        return keyword in text
    return any(
        word == keyword or word.startswith(keyword)
        for word in words
    )


def analyze_topic_distribution(pairs: list[TrainingPair]) -> dict[str, int]:
    """Считает темы без фильтрации или изменения обучающих примеров."""
    if not pairs:
        raise ValueError("Нельзя анализировать пустой датасет.")
    counts = {topic: 0 for topic in TOPIC_LABELS}
    for pair in pairs:
        counts[classify_topic(pair)] += 1
    return counts


def filter_pairs_by_topic(
    pairs: list[TrainingPair],
    topic: str,
) -> list[TrainingPair]:
    """Возвращает обратимую тематическую выборку без изменения источника."""
    normalized_topic = topic.strip().lower()
    if normalized_topic not in TRAINING_TOPIC_CHOICES:
        choices = ", ".join(TRAINING_TOPIC_CHOICES)
        raise ValueError(f"Неизвестная тема {topic!r}. Доступно: {choices}.")
    if normalized_topic == "all":
        return list(pairs)
    return [pair for pair in pairs if classify_topic(pair) == normalized_topic]


def render_quality_report(report: DatasetQualityReport) -> str:
    """Преобразует сводку в понятный Markdown для ручной проверки."""
    lines = [
        "# Отчёт о качестве датасета",
        "",
        "Отчёт сформирован автоматически и не изменяет `dataset.jsonl`.",
        "",
        "## Сводка",
        "",
        f"- Примеров: {report.total_examples}.",
        (
            "- Уникальных вопросов после безопасной нормализации: "
            f"{report.unique_normalized_prompts}."
        ),
        f"- Групп повторяющихся вопросов: {report.duplicate_prompt_groups}.",
        f"- Повторяющихся строк сверх первой: {report.duplicate_rows}.",
        (
            "- Вопросов с разными ответами: "
            f"{report.conflicting_prompt_groups}."
        ),
        (
            "- Средняя длина вопроса: "
            f"{report.mean_prompt_characters:.2f} символа."
        ),
        (
            "- Средняя длина ответа: "
            f"{report.mean_completion_characters:.2f} символа."
        ),
        "",
        "## Вопросы, требующие ручного решения",
        "",
    ]
    if not report.conflicts:
        lines.append("Конфликтующих ответов не найдено.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "Эти записи не были удалены автоматически. Локальная база "
            "знаний не выбирает ответ для таких точных вопросов, пока "
            "человек не исправит источник.",
            "",
        ]
    )
    for index, conflict in enumerate(report.conflicts, start=1):
        lines.append(f"### Конфликт {index}")
        lines.append("")
        lines.append(f"Нормализованный вопрос: `{conflict.normalized_prompt}`")
        lines.append("")
        lines.append("Исходные формулировки:")
        lines.append("")
        for prompt in conflict.source_prompts:
            lines.append(f"- {prompt}")
        lines.append("")
        lines.append("Варианты ответов:")
        lines.append("")
        for completion in conflict.completions:
            lines.append(f"- {completion}")
        lines.append("")
    return "\n".join(lines)


def render_topic_distribution(topic_counts: dict[str, int]) -> str:
    """Создаёт Markdown-раздел с ориентировочной тематикой набора."""
    total = sum(topic_counts.values())
    if total < 1:
        raise ValueError("Для отчёта тем нужен хотя бы один пример.")

    lines = [
        "## Ориентировочная карта тем",
        "",
        "Это простая проверяемая эвристика по ключевым словам. Она не "
        "используется для обучения и не является автоматическим решением о "
        "том, какие данные оставить.",
        "",
        "| Тема | Примеров | Доля |",
        "|---|---:|---:|",
    ]
    ordered_topics = [topic for topic, _ in TOPIC_RULES] + ["other"]
    for topic in ordered_topics:
        count = topic_counts.get(topic, 0)
        share = count / total * 100
        lines.append(
            f"| {TOPIC_LABELS[topic]} | {count} | {share:.1f}% |"
        )
    lines.extend(
        [
            "",
            "Вывод: перед следующим крупным обучением нужно выбрать одну "
            "основную задачу модели или вручную утвердить несколько совместимых "
            "тем. Смешивание всего набора ослабляет маленькую модель.",
            "",
        ]
    )
    return "\n".join(lines)
