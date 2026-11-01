"""Единый ранний слой уточнения для неполных вопросов в обычном чате.

Слой не пытается угадать факты и не заменяет разговорную модель. Он задаёт
один короткий вопрос только тогда, когда у запроса явно нет обязательного
предмета: например, неизвестны матч, новостная тема, услуга или варианты
выбора. Это предотвращает бессмысленный поиск и ложную уверенность.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClarificationDecision:
    """Описывает одну безопасную просьбу добавить недостающий контекст."""

    text: str
    intent: str
    followup_uses_web: bool


MISSING_MATCH = ClarificationDecision(
    text="Какой матч: команды, турнир или вид спорта?",
    intent="clarify_missing_match",
    followup_uses_web=True,
)
MISSING_NEWS = ClarificationDecision(
    text="Какие новости нужны: тема, место или событие?",
    intent="clarify_missing_news_topic",
    followup_uses_web=True,
)
MISSING_EXTERNAL_SUBJECT = ClarificationDecision(
    text="Уточни, о каком предмете, сервисе или событии речь.",
    intent="clarify_missing_external_subject",
    followup_uses_web=True,
)
MISSING_SERVICE = ClarificationDecision(
    text="Какой сервис, место или организация нужна?",
    intent="clarify_missing_service",
    followup_uses_web=True,
)
MISSING_OPTIONS = ClarificationDecision(
    text="Какие варианты ты выбираешь и что для тебя важнее?",
    intent="clarify_missing_options",
    followup_uses_web=False,
)
MISSING_TASK = ClarificationDecision(
    text="Что именно нужно сделать или решить?",
    intent="clarify_missing_task",
    followup_uses_web=False,
)
MISSING_REFERENCE = ClarificationDecision(
    text="Что именно ты имеешь в виду?",
    intent="clarify_missing_reference",
    followup_uses_web=False,
)


def _normalise(value: str) -> str:
    """Сводит фразу к виду, пригодному только для локальной проверки правил."""

    return " ".join(value.casefold().replace("ё", "е").split())


_WEB_RULES: tuple[tuple[re.Pattern[str], ClarificationDecision], ...] = (
    (
        re.compile(
            r"^кто выиграл(?:\s+(?:вчерашний|сегодняшний|последний))?\s+матч[.?!\s]*$"
        ),
        MISSING_MATCH,
    ),
    (
        re.compile(r"^(?:какие|что за)?\s*новости(?:\s+(?:сегодня|сейчас|на сегодня))?[.?!\s]*$"),
        MISSING_NEWS,
    ),
    (
        re.compile(
            r"^(?:сколько стоит|какая цена|какие цены|какой тариф|какие условия|"
            r"какие правила|какое расписание)(?:\s+(?:сегодня|сейчас|на сегодня))?[.?!\s]*$"
        ),
        MISSING_EXTERNAL_SUBJECT,
    ),
    (
        re.compile(r"^(?:какой|сколько)\s+курс(?:\s+валют)?(?:\s+сейчас)?[.?!\s]*$"),
        MISSING_EXTERNAL_SUBJECT,
    ),
    (
        re.compile(
            r"^(?:работает ли|до скольки работает|когда открывается|когда закрывается)"
            r"(?:\s+(?:сегодня|завтра))?[.?!\s]*$"
        ),
        MISSING_SERVICE,
    ),
)

_DIALOGUE_RULES: tuple[tuple[re.Pattern[str], ClarificationDecision], ...] = (
    (
        re.compile(r"^(?:помоги|помогите|нужна помощь|не получается)[.?!\s]*$"),
        MISSING_TASK,
    ),
    (
        re.compile(r"^(?:что выбрать|какой вариант выбрать|что лучше)[.?!\s]*$"),
        MISSING_OPTIONS,
    ),
    (
        re.compile(r"^(?:что думаешь|как ты считаешь)[.?!\s]*$"),
        MISSING_REFERENCE,
    ),
    (
        re.compile(r"^(?:как (?:это )?сделать|как поступить)[.?!\s]*$"),
        MISSING_TASK,
    ),
    (
        re.compile(r"^(?:что это|где это|когда это|сколько это стоит)[.?!\s]*$"),
        MISSING_REFERENCE,
    ),
    (
        re.compile(r"^(?:что|где|когда|сколько|почему|зачем|как)[.?!\s]*$"),
        MISSING_REFERENCE,
    ),
)

_DECISIONS_BY_TEXT = {
    _normalise(decision.text): decision
    for _, decision in (*_WEB_RULES, *_DIALOGUE_RULES)
}
_NON_ANSWERS = frozenset(
    {
        "не знаю",
        "не помню",
        "сам не знаю",
        "сама не знаю",
        "без разницы",
        "не важно",
        "неважно",
        "да",
        "нет",
        "потом",
        "не хочу",
    }
)


def _last_own_clarification(
    messages: list[dict[str, Any]],
) -> ClarificationDecision | None:
    """Находит только непосредственно предыдущее уточнение этого слоя."""

    if not messages:
        return None
    last_message = messages[-1]
    content = last_message.get("content")
    if last_message.get("role") != "assistant" or not isinstance(content, str):
        return None
    return _DECISIONS_BY_TEXT.get(_normalise(content))


def _has_usable_dialogue_context(messages: list[dict[str, Any]]) -> bool:
    """Разрешает короткую ссылку «это» только при содержательной истории чата."""

    for message in reversed(messages[-4:]):
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, str):
            continue
        clean_content = _normalise(content)
        if len(clean_content) >= 16 and clean_content not in _NON_ANSWERS:
            return True
    return False


def _is_substantive_followup(prompt: str) -> bool:
    """Не запускает web route после ответа вроде «не знаю» или нового вопроса."""

    clean_prompt = _normalise(prompt).strip(" .,!…")
    if len(clean_prompt) < 2 or clean_prompt in _NON_ANSWERS:
        return False
    if "?" in prompt:
        return False
    return bool(re.search(r"[a-zа-яё0-9]", clean_prompt, flags=re.IGNORECASE))


def clarification_for_prompt(
    prompt: str,
    previous_messages: list[dict[str, Any]],
) -> ClarificationDecision | None:
    """Возвращает уточнение только для очевидно неполного пользовательского запроса.

    Понятные вопросы, обычные реплики, эмоциональная поддержка и запросы с
    содержательной текущей историей не перехватываются. Поэтому ассистент не
    превращает разговор в цепочку ненужных вопросов.
    """

    clean_prompt = _normalise(prompt)
    if not clean_prompt:
        return None
    previous_decision = _last_own_clarification(previous_messages)
    if previous_decision is not None:
        return None if _is_substantive_followup(prompt) else previous_decision
    for pattern, decision in _WEB_RULES:
        if pattern.fullmatch(clean_prompt):
            return decision
    if _has_usable_dialogue_context(previous_messages):
        return None
    for pattern, decision in _DIALOGUE_RULES:
        if pattern.fullmatch(clean_prompt):
            return decision
    return None


def clarification_followup_web_query(
    prompt: str,
    previous_messages: list[dict[str, Any]],
) -> str | None:
    """Собирает один web-запрос после точного уточнения в этом же чате.

    В историю не записывается склеенный запрос: он существует только в памяти
    текущего HTTP-вызова. Это исключает скрытое использование старой темы или
    случайный поиск после произвольной реплики с деталями.
    """

    decision = _last_own_clarification(previous_messages)
    if decision is None or not decision.followup_uses_web:
        return None
    if not _is_substantive_followup(prompt):
        return None
    for message in reversed(previous_messages[:-1]):
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str):
            return f"{content.strip()} {prompt.strip()}"
    return None
