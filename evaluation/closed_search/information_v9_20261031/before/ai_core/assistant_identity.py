"""Небольшой локальный слой постоянной идентичности ВатэсAi.

Этот модуль намеренно не зависит от обучающих данных, памяти диалога или
интернета. Так имя ассистента и оговорённая шутливая реакция на вопрос о
создателе остаются стабильными и не могут случайно замениться текстом поиска.
"""

from __future__ import annotations

from dataclasses import dataclass

from .internet_router import normalize_route_text


ASSISTANT_NAME = "ВатэсAi"
IDENTITY_VERSION = "vatesai-identity-v1"

# Варианты нужны только для распознавания вопроса. В ответе используется одно
# утверждённое пользователем написание имени.
_ASSISTANT_MARKERS = (
    "ватэсai",
    "ватесai",
    "vatesai",
    "ватэс",
    "ватес",
    "vates",
)
_ASSISTANT_PRONOUNS = (
    "тебя",
    "тебе",
    "тобой",
    "твой",
    "твоя",
    "твое",
    "твоего",
    "твоему",
    "твоим",
    "твою",
    "ты",
)
_CREATOR_WORD_STEMS = (
    "создател",
    "создал",
    "создали",
    "сделал",
    "сделали",
    "разработ",
    "автор",
)
_NAME_PHRASES = (
    "как тебя зовут",
    "как тебя называть",
    "твое имя",
    "твоё имя",
    "твое название",
    "твоё название",
    "ты кто",
    "кто ты",
)


@dataclass(frozen=True)
class AssistantIdentityReply:
    """Контролируемый ответ о постоянной идентичности ассистента."""

    text: str
    intent: str


def _has_assistant_reference(phrase: str) -> bool:
    """Отделяет вопрос о ВатэсAi от вопроса о стороннем продукте или человеке."""
    words = set(phrase.split())
    return any(marker in phrase for marker in _ASSISTANT_MARKERS) or bool(
        words.intersection(_ASSISTANT_PRONOUNS)
    )


def _is_creator_question(phrase: str) -> bool:
    """Распознаёт формулировки о создателе только при ссылке на ассистента."""
    if not _has_assistant_reference(phrase):
        return False
    return any(stem in phrase for stem in _CREATOR_WORD_STEMS)


def _is_name_question(phrase: str) -> bool:
    """Распознаёт распространённые вопросы о названии ассистента."""
    if any(question in phrase for question in _NAME_PHRASES):
        return True
    return "имя" in phrase and _has_assistant_reference(phrase)


def assistant_identity_reply(prompt: str) -> AssistantIdentityReply | None:
    """Возвращает ответ о ВатэсAi либо ``None`` для обычной маршрутизации.

    Шутка о создателе явно обозначена как слух, поэтому модуль не выдаёт её за
    проверяемый факт о реальном человеке.
    """
    phrase = normalize_route_text(prompt)
    if _is_creator_question(phrase):
        return AssistantIdentityReply(
            text=(
                "Я не знаю, кто мой создатель. Но по слухам, он плавает в "
                "ванне с монетами и хочет Гелендваген."
            ),
            intent="assistant_creator",
        )
    if _is_name_question(phrase):
        return AssistantIdentityReply(
            text=f"Меня зовут {ASSISTANT_NAME}.",
            intent="assistant_name",
        )
    return None
