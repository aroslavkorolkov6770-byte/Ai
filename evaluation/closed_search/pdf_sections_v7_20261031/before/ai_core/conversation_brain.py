"""Разговорный слой собственной модели без свободной небезопасной генерации.

Модуль решает более узкую и проверяемую задачу, чем большой языковой
генератор: отделяет обычное человеческое общение от запросов, которым нужен
факт, локальный QA-каталог или интернет. Для спокойных разговорных реплик
используется небольшой набор прозрачных, полных ответов. Так неизвестная
реплика не превращается в набор случайных символов и не наследует старую
узкую тему обучения.

Нейросеть получает только реплику пользователя и метку поведения. Готовые
ответы, system-подсказки и веб-контекст из пользовательского архива в её
признаки и ответы не попадают.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from .internet_router import RouterFeatureEncoder, RouterNetwork, normalize_route_text


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONVERSATION_BRAIN_ARTIFACT = (
    ROOT / "model_artifacts" / "conversation_brain" / "brain.pt"
)
FORMAT_VERSION = 1
LABELS = ("TASK", "DIALOGUE")

# Последняя линия защиты должна быть нейтральной: ни LMS, ни старая тема HTML
# не имеют отношения к обычному разговору пользователя.
NEUTRAL_FALLBACK = (
    "Я не хочу отвечать наугад. Расскажи, что тебе нужно: факт, совет, "
    "объяснение, помощь с текстом или просто разговор. Я подстроюсь под тему."
)

_INFORMATION_REQUEST = re.compile(
    r"(?:^|\b)(?:кто|что|какой|какая|какие|где|когда|сколько|почему|зачем|"
    r"как|объясни|расскажи|покажи|найди|переведи|сравни|рассчитай|реши|"
    r"напиши|сделай)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ConversationPrediction:
    """Осторожное решение собственного разговорного классификатора."""

    is_dialogue: bool
    label: str
    confidence: float
    coverage: float
    accepted: bool


@dataclass(frozen=True)
class ConversationReply:
    """Готовый контролируемый ответ для нормального диалога."""

    text: str
    intent: str
    confidence: float
    context_used: bool


def _normalise_prompt(prompt: str) -> str:
    """Приводит реплику к стабильному виду без исполнения её содержимого."""
    if not isinstance(prompt, str):
        return ""
    return " ".join(prompt.casefold().replace("ё", "е").split())


def _has_previous_user_message(messages: list[dict[str, Any]]) -> bool:
    """Проверяет только наличие истории, не делает старые реплики фактами."""
    return any(
        message.get("role") == "user"
        and isinstance(message.get("content"), str)
        and message["content"].strip()
        for message in messages
    )


def _direct_intent(clean_prompt: str) -> str | None:
    """Распознаёт короткие очевидные реплики до статистической модели.

    Эти правила не хранят варианты диалогов и не заменяют обучение. Они нужны,
    чтобы приветствие, благодарность или прямое исправление пользователя не
    зависели от уверенности маленькой модели на редкой формулировке.
    """
    words = normalize_route_text(clean_prompt).split()
    if not words:
        return None
    phrase = " ".join(words)
    if phrase in {"привет", "здравствуй", "здравствуйте", "приветик", "хай"}:
        return "greeting"
    # Резервный слой должен понимать тот же короткий дружеский старт, что и
    # основной conversation-mind. Иначе при недоступности checkpoint чат мог
    # снова попасть в старый узкий QA-каталог вместо нормального разговора.
    # Шаблон не принимает «Привет, как сделать…», чтобы не заслонять задачу.
    if re.fullmatch(
        r"(?:привет|здравствуй|здравствуйте|приветик|хай)\s+(?:"
        r"как\s+(?:ты|дела)(?:\s+у\s+тебя)?|"
        r"что\s+нового\s+у\s+тебя"
        r")",
        phrase,
    ):
        return "greeting"
    if phrase.startswith(("доброе утро", "добрый день", "добрый вечер")):
        return "greeting"
    if phrase.startswith(("спасибо", "благодарю", "спс", "благодарочка")):
        return "gratitude"
    if phrase in {"пока", "до свидания", "до встречи", "увидимся", "бывай"}:
        return "farewell"
    if phrase.startswith(
        (
            "ты не понял",
            "не так",
            "неправильно",
            "я имел в виду",
            "я имела в виду",
            "я хотел",
            "я хотела",
            "передумал",
            "передумала",
        )
    ):
        return "correction"
    if any(
        marker in phrase
        for marker in (
            "мне грустно",
            "мне плохо",
            "мне тяжело",
            "мне тревожно",
            "мне одиноко",
            "я тревожусь",
            "я волнуюсь",
            "я устал",
            "я устала",
            "я не знаю что делать",
            "я не понимаю что делать",
            "не знаю как поступить",
        )
    ):
        return "support"
    if phrase.startswith(("как дела", "что нового у тебя", "поговорим", "давай поговорим")):
        return "conversation"
    if phrase.startswith(("что ты думаешь", "как ты считаешь", "твое мнение", "твоё мнение")):
        return "opinion"
    if any(
        marker in phrase
        for marker in (
            "помоги выбрать",
            "не могу выбрать",
            "нужно выбрать",
            "надо выбрать",
            "выбрать между",
            "что выбрать",
            "какой вариант выбрать",
            "посоветуй мне",
            "стоит ли мне",
        )
    ):
        return "choice"
    if phrase.startswith(("расскажи шутку", "придумай", "давай придумаем", "давай сочиним")):
        return "creative"
    return None


def _compose_reply(intent: str, messages: list[dict[str, Any]]) -> ConversationReply:
    """Формирует полный ответ без вероятностного декодирования по буквам."""
    has_context = _has_previous_user_message(messages)
    responses = {
        "greeting": (
            "Привет. Я здесь: можем спокойно поговорить, разобрать идею "
            "или решить конкретный вопрос. С чего начнём?"
        ),
        "gratitude": "Пожалуйста. Когда захочешь, продолжим.",
        "farewell": "До связи. Когда захочешь продолжить, просто напиши.",
        "correction": (
            "Понял, спасибо, что поправил. Скажи, что именно для тебя важно, "
            "и я перестрою ответ."
        ),
        "support": (
            "Понимаю. Не нужно формулировать всё идеально — расскажи немного "
            "подробнее, что происходит. Попробуем спокойно разобрать это по шагам."
        ),
        "conversation": (
            "Понял. Давай продолжим: что сейчас важнее — разобраться в фактах, "
            "выбрать вариант или просто обсудить мысль?"
            if has_context
            else "Я здесь и готов поговорить. О чём тебе хочется начать?"
        ),
        "opinion": (
            "У меня нет личного опыта, но я могу помочь посмотреть на вопрос с "
            "разных сторон. Расскажи, о чём именно ты хочешь узнать мнение."
        ),
        "choice": (
            "Давай выберем вместе. Напиши варианты и что для тебя важнее: цена, "
            "время, надёжность, удобство или что-то другое."
        ),
        "creative": (
            "Давай. Можем придумать идею, сюжет, название, план или готовый "
            "небольшой текст. С чего хочешь начать?"
        ),
        "dialogue": (
            "Понял. Давай продолжим: что в этой теме сейчас важнее — "
            "разобраться в фактах, выбрать вариант или просто обсудить мысль?"
            if has_context
            else "Я тебя слушаю. Можем обсудить тему обычным языком или "
            "разобрать конкретную задачу — продолжай."
        ),
    }
    return ConversationReply(
        text=responses[intent],
        intent=intent,
        confidence=1.0 if intent != "dialogue" else 0.0,
        context_used=has_context,
    )


def direct_conversation_reply(
    prompt: str,
    messages: list[dict[str, Any]],
) -> ConversationReply | None:
    """Отвечает на короткую явную разговорную реплику без ожидания модели."""
    clean_prompt = _normalise_prompt(prompt)
    if not clean_prompt:
        return None
    intent = _direct_intent(clean_prompt)
    return _compose_reply(intent, messages) if intent is not None else None


class ConversationBrain:
    """Загружает локальные веса и выбирает безопасный разговорный маршрут."""

    def __init__(self, checkpoint: dict[str, Any]) -> None:
        if checkpoint.get("format_version") != FORMAT_VERSION:
            raise ValueError("Неизвестный формат checkpoint conversation-brain.")
        labels = tuple(checkpoint.get("labels", ()))
        if labels != LABELS:
            raise ValueError("Conversation-brain должен содержать метки TASK и DIALOGUE.")
        thresholds = checkpoint.get("thresholds")
        if not isinstance(thresholds, dict):
            raise ValueError("В checkpoint conversation-brain отсутствуют пороги.")
        confidence = thresholds.get("dialogue_confidence")
        coverage = thresholds.get("minimum_coverage")
        if not isinstance(confidence, int | float) or not 0.5 <= float(confidence) <= 1:
            raise ValueError("Некорректный порог confidence conversation-brain.")
        if not isinstance(coverage, int | float) or not 0 <= float(coverage) <= 1:
            raise ValueError("Некорректный порог coverage conversation-brain.")
        version = checkpoint.get("model_version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("В checkpoint conversation-brain отсутствует версия модели.")
        self.version = version.strip()
        self.labels = labels
        self.thresholds = {
            "dialogue_confidence": float(confidence),
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
    def load(
        cls,
        path: Path = DEFAULT_CONVERSATION_BRAIN_ARTIFACT,
    ) -> "ConversationBrain":
        """Загружает только локальный checkpoint без готовой внешней модели."""
        return cls(torch.load(path, map_location="cpu", weights_only=True))

    def predict(self, prompt: str) -> ConversationPrediction:
        """Оценивает возможность разговорного ответа по порогам checkpoint."""
        clean_prompt = prompt.strip() if isinstance(prompt, str) else ""
        if not clean_prompt:
            return ConversationPrediction(False, "TASK", 0.0, 0.0, False)
        indices, offsets, weights, coverage = self.encoder.encode_batch([clean_prompt])
        with torch.inference_mode():
            probabilities = self.network(indices, offsets, weights).softmax(dim=-1)[0]
        dialogue_index = self.labels.index("DIALOGUE")
        confidence = float(probabilities[dialogue_index])
        accepted = (
            confidence >= self.thresholds["dialogue_confidence"]
            and coverage[0] >= self.thresholds["minimum_coverage"]
        )
        return ConversationPrediction(
            is_dialogue=accepted,
            label="DIALOGUE" if accepted else "TASK",
            confidence=confidence,
            coverage=coverage[0],
            accepted=accepted,
        )

    def reply(
        self,
        prompt: str,
        messages: list[dict[str, Any]],
    ) -> ConversationReply | None:
        """Возвращает ответ только для обычного разговора, иначе ``None``.

        Фактический или прикладной вопрос не перехватывается этим слоем: он
        продолжает путь к локальному каталогу или контролируемому интернету.
        """
        direct_reply = direct_conversation_reply(prompt, messages)
        if direct_reply is not None:
            return direct_reply
        clean_prompt = _normalise_prompt(prompt)
        if not clean_prompt:
            return None
        if _INFORMATION_REQUEST.search(clean_prompt):
            return None
        prediction = self.predict(clean_prompt)
        if not prediction.is_dialogue:
            return None
        reply = _compose_reply("dialogue", messages)
        return ConversationReply(
            text=reply.text,
            intent=reply.intent,
            confidence=prediction.confidence,
            context_used=reply.context_used,
        )
