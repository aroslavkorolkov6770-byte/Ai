"""Единый локальный API для собственной модели и памяти диалогов."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import torch
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator

try:
    from .conversation_context import (
        build_context,
        encode_prompt_with_context,
    )
    from .knowledge_base import LocalKnowledgeBase
    from .internet_search import (
        InternetSearchClient,
        InternetSearchError,
        is_weather_compact_time_followup,
        is_weather_followup,
        is_weather_question,
        is_pickup_point_hours_question,
        pickup_point_hours_clarification,
        pickup_point_hours_followup_query,
        should_search_after_local_clarification,
        should_search_automatically,
        weather_day_from_messages,
        weather_location_clarification,
        weather_location_followup,
        weather_location_from_messages,
        weather_question_from_messages,
    )
    from .internet_router import DEFAULT_ROUTER_ARTIFACT, InternetRouter
    from .clarification_policy import (
        clarification_followup_web_query,
        clarification_for_prompt,
    )
    from .conversation_brain import (
        DEFAULT_CONVERSATION_BRAIN_ARTIFACT,
        ConversationBrain,
        direct_conversation_reply,
    )
    from .conversation_mind import (
        DEFAULT_CONVERSATION_MIND_ARTIFACT,
        ConversationMind,
        ConversationMindReply,
        response_pool_size,
    )
    from .assistant_identity import IDENTITY_VERSION, assistant_identity_reply
    from .web_dialogue_policy import (
        DEFAULT_WEB_DIALOGUE_POLICY_ARTIFACT,
        WebDialoguePolicy,
    )
    from .temporal_context import describe_current_temporal_context
    from .local_tts import LocalVoiceError, synthesize_ivona_tatyana
    from .memory_store import ConversationStore, FeedbackAlreadySubmittedError
    from .reliable_qa import Prediction, ReliableQA, DEFAULT_ARTIFACT
    from .reliable_dialogue import ReliableDialogue, DEFAULT_DIALOGUE_ARTIFACT
    from .lms_reliable import LmsPrediction, ReliableLms, DEFAULT_LMS_ARTIFACT
    from .lms_knowledge import (
        DEFAULT_LMS_KNOWLEDGE_INDEX,
        LmsKnowledgeIndex,
        LmsKnowledgeMatch,
    )
    from .model import (
        TinyGRUAttentionSeq2Seq,
        TinySeq2SeqTransformer,
        TinyTransformer,
    )
    from .training_corpus import load_training_corpus
    from .tokenizer import (
        CharacterTokenizer,
        SubwordBPETokenizer,
        WordTokenizer,
        load_tokenizer,
    )
except ImportError:
    from conversation_context import build_context, encode_prompt_with_context
    from knowledge_base import LocalKnowledgeBase
    from internet_search import (
        InternetSearchClient,
        InternetSearchError,
        is_weather_compact_time_followup,
        is_weather_followup,
        is_weather_question,
        is_pickup_point_hours_question,
        pickup_point_hours_clarification,
        pickup_point_hours_followup_query,
        should_search_after_local_clarification,
        should_search_automatically,
        weather_day_from_messages,
        weather_location_clarification,
        weather_location_followup,
        weather_location_from_messages,
        weather_question_from_messages,
    )
    from internet_router import DEFAULT_ROUTER_ARTIFACT, InternetRouter
    from clarification_policy import (
        clarification_followup_web_query,
        clarification_for_prompt,
    )
    from ai_core.conversation_brain import (
        DEFAULT_CONVERSATION_BRAIN_ARTIFACT,
        ConversationBrain,
        direct_conversation_reply,
    )
    from ai_core.conversation_mind import (
        DEFAULT_CONVERSATION_MIND_ARTIFACT,
        ConversationMind,
        ConversationMindReply,
        response_pool_size,
    )
    from ai_core.assistant_identity import IDENTITY_VERSION, assistant_identity_reply
    from ai_core.web_dialogue_policy import (
        DEFAULT_WEB_DIALOGUE_POLICY_ARTIFACT,
        WebDialoguePolicy,
    )
    from temporal_context import describe_current_temporal_context
    from local_tts import LocalVoiceError, synthesize_ivona_tatyana
    from memory_store import ConversationStore, FeedbackAlreadySubmittedError
    from reliable_qa import Prediction, ReliableQA, DEFAULT_ARTIFACT
    from ai_core.reliable_dialogue import ReliableDialogue, DEFAULT_DIALOGUE_ARTIFACT
    from ai_core.lms_reliable import LmsPrediction, ReliableLms, DEFAULT_LMS_ARTIFACT
    from ai_core.lms_knowledge import (
        DEFAULT_LMS_KNOWLEDGE_INDEX,
        LmsKnowledgeIndex,
        LmsKnowledgeMatch,
    )
    from model import (
        TinyGRUAttentionSeq2Seq,
        TinySeq2SeqTransformer,
        TinyTransformer,
    )
    from training_corpus import load_training_corpus
    from tokenizer import (
        CharacterTokenizer,
        SubwordBPETokenizer,
        WordTokenizer,
        load_tokenizer,
    )


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = PROJECT_ROOT / "model_artifacts"
WEB_CLIENT_DIR = PROJECT_ROOT / "web_client"
MEMORY_DB_PATH = Path(os.environ.get(
    "AI_MEMORY_DB_PATH", str(PROJECT_ROOT / "conversation_data" / "conversations.sqlite3"),
))
# Ключ существует только в памяти процесса, если пользователь намеренно не
# настроил AI_FEEDBACK_SIGNING_KEY в окружении. Поэтому технический token не
# раскрывает текст чата и не переживает перезапуск без отдельной настройки.
_configured_feedback_signing_key = os.environ.get("AI_FEEDBACK_SIGNING_KEY")
FEEDBACK_TOKEN_SECRET = (
    _configured_feedback_signing_key.encode("utf-8")
    if _configured_feedback_signing_key
    else secrets.token_bytes(32)
)
TOKENIZER_PATH = ARTIFACTS_DIR / "tokenizer.json"
CHECKPOINT_PATH = ARTIFACTS_DIR / "tiny_transformer.pt"
MODEL_VERSION = "tiny-transformer-seq2seq-v0.3"
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

tokenizer: WordTokenizer | CharacterTokenizer | SubwordBPETokenizer | None = None
model: TinyTransformer | TinySeq2SeqTransformer | TinyGRUAttentionSeq2Seq | None = None
model_load_error: str | None = None
model_config: dict[str, Any] = {}
model_architecture = "unknown"
loaded_model_version = MODEL_VERSION
loaded_training_topic = "all"
conversation_store = ConversationStore(MEMORY_DB_PATH)
knowledge_base: LocalKnowledgeBase | None = None
# Общий поиск не должен удерживать чат слишком долго, а структурированная
# погода обычно отвечает заметно быстрее, чем обход нескольких HTML-страниц.
internet_search = InternetSearchClient(timeout_seconds=5.0)
internet_router: InternetRouter | None = None
internet_router_load_error: str | None = None
conversation_brain: ConversationBrain | None = None
conversation_brain_load_error: str | None = None
conversation_mind: ConversationMind | None = None
conversation_mind_load_error: str | None = None
web_dialogue_policy: WebDialoguePolicy | None = None
web_dialogue_policy_load_error: str | None = None
reliable_qa: ReliableQA | None = None
reliable_load_error: str | None = None
reliable_dialogue: ReliableDialogue | None = None
reliable_dialogue_load_error: str | None = None
reliable_lms: ReliableLms | None = None
reliable_lms_load_error: str | None = None
lms_knowledge: LmsKnowledgeIndex | None = None
lms_knowledge_load_error: str | None = None


def create_feedback_token() -> str:
    """Создаёт одноразовый token для добровольной оценки нового ответа.

    В token нет текста вопроса, ответа, диалога или координат. Подпись нужна,
    чтобы API не принимал произвольные идентификаторы, а в SQLite запись
    появляется только после нажатия пользователем кнопки оценки.
    """
    response_id = str(uuid4())
    signature = hmac.new(
        FEEDBACK_TOKEN_SECRET,
        response_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{response_id}.{signature}"


def response_id_from_feedback_token(feedback_token: str) -> str:
    """Проверяет token и возвращает его случайный идентификатор ответа."""
    response_id, separator, signature = feedback_token.strip().partition(".")
    if not separator or len(signature) != 64:
        raise ValueError("Некорректный token оценки ответа.")
    try:
        # UUID нужен только как строгая проверка формата случайного id.
        if str(UUID(response_id)) != response_id:
            raise ValueError("Некорректный token оценки ответа.")
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("Некорректный token оценки ответа.") from error

    expected_signature = hmac.new(
        FEEDBACK_TOKEN_SECRET,
        response_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("Срок действия token оценки ответа истёк.")
    return response_id


class GenerateRequest(BaseModel):
    """Параметры генерации ответа."""

    prompt: str = Field(..., min_length=1, max_length=2000)
    response_mode: Literal["auto", "reliable", "experimental", "lms"] = Field(
        default="auto",
        description=(
            "auto: единый безопасный чат с локальными знаниями и внешними "
            "источниками при необходимости; reliable: каталог изученных веб-ответов; "
            "experimental: свободная генерация GRU; lms: изолированный "
            "совместимый режим для старых интеграций."
        ),
    )
    lms_context: str | None = Field(
        default=None,
        max_length=6000,
        description="Явный контекст LMS для режима lms: правила курса, раздел или фрагмент документа.",
    )
    conversation_id: str | None = Field(default=None, max_length=100)
    max_tokens: int = Field(default=64, ge=1, le=128,
                            description="Лимит токенов только experimental; reliable возвращает ответ целиком.")
    temperature: float = Field(default=0.8, gt=0.05, le=2.0)
    top_k: int = Field(default=1, ge=0, le=100)
    use_knowledge_base: bool = True
    repetition_penalty: float = Field(default=1.0, ge=1.0, le=2.0)
    no_repeat_ngram_size: int = Field(default=0, ge=0, le=8)
    decoding: Literal["sample", "beam"] = "sample"
    beam_width: int = Field(default=3, ge=2, le=5)
    length_penalty: float = Field(default=0.7, ge=0.0, le=2.0)
    use_internet: bool = Field(
        default=False,
        description="Явно запрашивает интернет для обратной совместимости внешнего API.",
    )
    use_conversation_context: bool = True
    latitude: float | None = Field(
        default=None,
        ge=-90,
        le=90,
        description=(
            "Широта из разрешённой геолокации браузера. Используется только для "
            "текущего погодного запроса и не сохраняется."
        ),
    )
    longitude: float | None = Field(
        default=None,
        ge=-180,
        le=180,
        description=(
            "Долгота из разрешённой геолокации браузера. Используется только для "
            "текущего погодного запроса и не сохраняется."
        ),
    )
    location_accuracy_m: float | None = Field(
        default=None,
        ge=0,
        le=100_000,
        description="Точность геолокации в метрах; в память диалога не записывается.",
    )

    @model_validator(mode="after")
    def coordinates_are_complete(self) -> "GenerateRequest":
        """Не допускает случайной передачи одной координаты без второй."""
        has_latitude = self.latitude is not None
        has_longitude = self.longitude is not None
        if has_latitude != has_longitude:
            raise ValueError("Для геолокации нужны одновременно latitude и longitude.")
        return self


class GenerateResponse(BaseModel):
    """Ответ API с текстом и идентификатором локального диалога."""

    text: str
    conversation_id: str
    model: str
    device: str
    source: str
    match_score: float | None = None
    context_used: bool = False
    intent: str | None = None
    confidence: float | None = None
    needs_clarification: bool = False
    response_mode: str = "reliable"
    # Token нужен только для добровольной оценки этого живого ответа. Он не
    # содержит текст чата и не выдаётся при повторной загрузке истории.
    feedback_token: str | None = None


class FeedbackRequest(BaseModel):
    """Минимальная добровольная оценка без передачи текста переписки."""

    feedback_token: str = Field(..., min_length=100, max_length=140)
    rating: Literal["helpful", "needs_improvement"]


class FeedbackResponse(BaseModel):
    """Подтверждает сохранение одной локальной оценки."""

    rating: Literal["helpful", "needs_improvement"]
    status: Literal["saved"] = "saved"


class SearchRequest(BaseModel):
    """Параметры отдельного контролируемого интернет-поиска."""

    query: str = Field(..., min_length=2, max_length=300)
    max_results: int = Field(default=5, ge=1, le=5)


class SearchResultResponse(BaseModel):
    """Безопасная часть результата поиска для внешнего клиента."""

    title: str
    url: str
    snippet: str


class SearchResponse(BaseModel):
    """Ответ отдельного поискового endpoint."""

    query: str
    results: list[SearchResultResponse]


class VoiceSynthesisRequest(BaseModel):
    """Только текст текущего ответа для локальной IVONA-озвучки."""

    text: str = Field(..., min_length=1, max_length=4_000)


def load_model_artifacts() -> None:
    """Загружает только наш локальный словарь и веса модели."""
    global tokenizer, model, model_config, model_load_error
    global model_architecture, loaded_model_version, loaded_training_topic
    try:
        if not TOKENIZER_PATH.exists() or not CHECKPOINT_PATH.exists():
            raise FileNotFoundError(
                "Артефакты модели ещё не созданы. Сначала запустите обучение."
            )

        tokenizer = load_tokenizer(TOKENIZER_PATH)
        checkpoint = torch.load(CHECKPOINT_PATH, map_location=DEVICE)
        checkpoint_version = checkpoint.get("model_version", MODEL_VERSION)
        if not isinstance(checkpoint_version, str) or not checkpoint_version.strip():
            raise ValueError("В checkpoint отсутствует корректная версия модели.")
        loaded_model_version = checkpoint_version.strip()
        checkpoint_topic = checkpoint.get("training_topic", "all")
        if not isinstance(checkpoint_topic, str) or not checkpoint_topic.strip():
            raise ValueError("В checkpoint отсутствует корректная тема обучения.")
        loaded_training_topic = checkpoint_topic.strip().lower()
        model_config = dict(checkpoint["model_config"])
        model_architecture = str(checkpoint.get("architecture", ""))
        if not model_architecture:
            state_keys = checkpoint.get("model_state", {}).keys()
            model_architecture = (
                "seq2seq"
                if any(str(key).startswith("decoder.") for key in state_keys)
                else "causal"
            )
        if model_architecture == "seq2seq":
            model = TinySeq2SeqTransformer(**model_config).to(DEVICE)
        elif model_architecture == "gru-attention-seq2seq":
            model = TinyGRUAttentionSeq2Seq(**model_config).to(DEVICE)
        elif model_architecture == "causal":
            model = TinyTransformer(**model_config).to(DEVICE)
        else:
            raise ValueError(f"Неизвестная архитектура модели: {model_architecture}")
        model.load_state_dict(checkpoint["model_state"])
        model.eval()
        model_load_error = None
        logger.info("Модель загружена на устройство %s.", DEVICE)
    except (FileNotFoundError, KeyError, OSError, RuntimeError, ValueError) as error:
        tokenizer = None
        model = None
        model_config = {}
        model_architecture = "unknown"
        loaded_model_version = MODEL_VERSION
        loaded_training_topic = "all"
        model_load_error = str(error)
        logger.warning("Модель пока недоступна: %s", error)


def load_knowledge_base() -> None:
    """Загружает локальные ответы для точных вопросов из датасета."""
    global knowledge_base
    try:
        knowledge_base = LocalKnowledgeBase(load_training_corpus())
        logger.info(
            "Локальная база примеров загружена: %s записей.",
            knowledge_base.size,
        )
    except (FileNotFoundError, OSError, ValueError) as error:
        knowledge_base = None
        logger.warning("Локальная база примеров недоступна: %s", error)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Инициализирует память и пытается загрузить модель при старте API."""
    conversation_store.initialize()
    load_knowledge_base()
    load_model_artifacts()
    load_internet_router()
    load_conversation_brain()
    load_conversation_mind()
    load_web_dialogue_policy()
    load_reliable_artifacts()
    yield


def load_internet_router() -> None:
    """Загружает отдельный локальный выбор маршрута без внешнего API."""
    global internet_router, internet_router_load_error
    internet_router = None
    internet_router_load_error = None
    router_path = Path(
        os.environ.get(
            "AI_INTERNET_ROUTER_CHECKPOINT",
            str(DEFAULT_ROUTER_ARTIFACT),
        )
    )
    if not router_path.exists():
        internet_router_load_error = f"Файл не найден: {router_path}"
        logger.warning("Internet-router недоступен: %s", internet_router_load_error)
        return
    try:
        internet_router = InternetRouter.load(router_path)
        logger.info("Internet-router загружен: %s.", internet_router.version)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        internet_router_load_error = str(error)
        logger.warning("Internet-router недоступен: %s", error)


def load_conversation_brain() -> None:
    """Загружает локальный разговорный маршрут отдельно от QA и поиска."""
    global conversation_brain, conversation_brain_load_error
    conversation_brain = None
    conversation_brain_load_error = None
    brain_path = Path(
        os.environ.get(
            "AI_CONVERSATION_BRAIN_CHECKPOINT",
            str(DEFAULT_CONVERSATION_BRAIN_ARTIFACT),
        )
    )
    if not brain_path.exists():
        conversation_brain_load_error = f"Файл не найден: {brain_path}"
        logger.warning("Conversation-brain недоступен: %s", conversation_brain_load_error)
        return
    try:
        conversation_brain = ConversationBrain.load(brain_path)
        logger.info("Conversation-brain загружен: %s.", conversation_brain.version)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        conversation_brain_load_error = str(error)
        logger.warning("Conversation-brain недоступен: %s", error)


def load_conversation_mind() -> None:
    """Загружает новый разговорный планировщик отдельно от QA и поиска.

    Если checkpoint отсутствует или повреждён, чат не падает: он продолжит
    использовать проверенный conversation-brain как резервный слой.
    """
    global conversation_mind, conversation_mind_load_error
    conversation_mind = None
    conversation_mind_load_error = None
    mind_path = Path(
        os.environ.get(
            "AI_CONVERSATION_MIND_CHECKPOINT",
            str(DEFAULT_CONVERSATION_MIND_ARTIFACT),
        )
    )
    if not mind_path.exists():
        conversation_mind_load_error = f"Файл не найден: {mind_path}"
        logger.warning("Conversation-mind недоступен: %s", conversation_mind_load_error)
        return
    try:
        conversation_mind = ConversationMind.load(mind_path)
        logger.info("Conversation-mind загружен: %s.", conversation_mind.version)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        conversation_mind_load_error = str(error)
        logger.warning("Conversation-mind недоступен: %s", error)


def load_web_dialogue_policy() -> None:
    """Загружает отдельный policy-сигнал для осторожного веб-маршрута."""
    global web_dialogue_policy, web_dialogue_policy_load_error
    web_dialogue_policy = None
    web_dialogue_policy_load_error = None
    policy_path = Path(
        os.environ.get(
            "AI_WEB_DIALOGUE_POLICY_CHECKPOINT",
            str(DEFAULT_WEB_DIALOGUE_POLICY_ARTIFACT),
        )
    )
    if not policy_path.exists():
        web_dialogue_policy_load_error = f"Файл не найден: {policy_path}"
        logger.warning("Web-dialogue-policy недоступен: %s", web_dialogue_policy_load_error)
        return
    try:
        web_dialogue_policy = WebDialoguePolicy.load(policy_path)
        logger.info("Web-dialogue-policy загружен: %s.", web_dialogue_policy.version)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        web_dialogue_policy_load_error = str(error)
        logger.warning("Web-dialogue-policy недоступен: %s", error)


def load_lms_knowledge() -> None:
    """Загружает локальный индекс только для изолированной legacy-интеграции."""
    global lms_knowledge, lms_knowledge_load_error
    lms_knowledge = None
    lms_knowledge_load_error = None
    index_path = Path(
        os.environ.get(
            "AI_LMS_KNOWLEDGE_INDEX",
            str(DEFAULT_LMS_KNOWLEDGE_INDEX),
        )
    )
    try:
        lms_knowledge = LmsKnowledgeIndex.from_path(index_path)
        logger.info(
            "LMS-индекс загружен: %s контекстных групп.",
            lms_knowledge.context_groups,
        )
    except (FileNotFoundError, OSError, ValueError) as error:
        lms_knowledge_load_error = str(error)
        logger.warning("LMS-индекс недоступен: %s", error)


def load_reliable_artifacts() -> None:
    """Загружает QA, follow-up и изолированный legacy-checkpoint отдельно от GRU."""
    global reliable_qa, reliable_load_error, reliable_dialogue, reliable_lms
    global reliable_dialogue_load_error, reliable_lms_load_error
    load_lms_knowledge()
    reliable_lms = None
    reliable_lms_load_error = None
    lms_path = Path(os.environ.get("AI_LMS_CHECKPOINT", str(DEFAULT_LMS_ARTIFACT)))
    if not lms_path.exists():
        reliable_lms_load_error = f"Файл не найден: {lms_path}"
    else:
        try:
            reliable_lms = ReliableLms.load(lms_path)
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            reliable_lms_load_error = str(error)
            logger.warning("LMS-модель недоступна: %s", error)
    reliable_dialogue = None
    reliable_dialogue_load_error = None
    try:
        qa_path = Path(os.environ.get("AI_QA_CHECKPOINT", str(DEFAULT_ARTIFACT)))
        reliable_qa = ReliableQA.load(qa_path)
        reliable_load_error = None
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        reliable_qa = None
        reliable_load_error = str(error)
        logger.warning("QA-модель недоступна: %s", error)
    if reliable_qa is not None:
        dialogue_path = Path(os.environ.get("AI_DIALOGUE_CHECKPOINT", str(DEFAULT_DIALOGUE_ARTIFACT)))
        if dialogue_path.exists():
            try:
                reliable_dialogue = ReliableDialogue.load(reliable_qa, dialogue_path, qa_path)
            except (OSError, ValueError, RuntimeError, KeyError) as error:
                reliable_dialogue_load_error = str(error)
                logger.warning("Диалоговая модель недоступна: %s", error)
        else:
            reliable_dialogue_load_error = f"Файл не найден: {dialogue_path}"


app = FastAPI(
    title="Собственная нейросеть",
    description=(
        "Локальный API собственной QA- и диалоговой модели с контролируемыми "
        "структурированными интернет-данными."
    ),
    version="0.3.0",
    lifespan=lifespan,
)
from .site_search_api import router as closed_site_search_router

# Изолированный контур закрытого поиска требует серверный ключ сайта.
# Обычный чат/старые интеграции не получают доступ к его материалам автоматически.
app.include_router(closed_site_search_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _adjust_logits_for_generation(
    logits: torch.Tensor,
    answer_ids: list[int],
    request: GenerateRequest,
) -> torch.Tensor:
    """Применяет одинаковые ограничения к sample и beam декодированию."""
    adjusted_logits = logits.clone()
    # Служебные токены не являются текстом. EOS разрешён, а PAD, BOS,
    # разделитель вопроса и UNK никогда не должны появляться внутри ответа.
    if tokenizer is not None:
        for token_id in (tokenizer.pad_id, tokenizer.bos_id, tokenizer.sep_id, tokenizer.unk_id):
            adjusted_logits[token_id] = float("-inf")
    if request.repetition_penalty > 1.0:
        for token_id in set(answer_ids):
            if adjusted_logits[token_id] < 0:
                adjusted_logits[token_id] *= request.repetition_penalty
            else:
                adjusted_logits[token_id] /= request.repetition_penalty

    if request.no_repeat_ngram_size > 1 and len(answer_ids) >= request.no_repeat_ngram_size - 1:
        prefix_length = request.no_repeat_ngram_size - 1
        current_prefix = tuple(answer_ids[-prefix_length:])
        for index in range(len(answer_ids) - request.no_repeat_ngram_size + 1):
            previous_ngram = tuple(answer_ids[index:index + prefix_length])
            if previous_ngram == current_prefix:
                forbidden_token = answer_ids[index + prefix_length]
                adjusted_logits[forbidden_token] = float("-inf")

    return adjusted_logits


def _sample_next_token(
    logits: torch.Tensor,
    answer_ids: list[int],
    request: GenerateRequest,
) -> int:
    """Выбирает следующий токен с ограничением top-k и temperature."""
    adjusted_logits = _adjust_logits_for_generation(
        logits,
        answer_ids,
        request,
    )
    scaled_logits = adjusted_logits / request.temperature
    if request.top_k > 0:
        limit = min(request.top_k, scaled_logits.shape[-1])
        values, indices = torch.topk(scaled_logits, limit)
        probabilities = torch.softmax(values, dim=-1)
        selected_index = torch.multinomial(probabilities, num_samples=1)
        return int(indices[selected_index].item())

    probabilities = torch.softmax(scaled_logits, dim=-1)
    return int(torch.multinomial(probabilities, num_samples=1).item())


def _beam_rank(score: float, token_count: int, length_penalty: float) -> float:
    """Сравнивает beam-пути с мягкой поправкой на длину ответа."""
    if token_count < 1 or length_penalty == 0.0:
        return score
    return score / (token_count**length_penalty)


def _beam_search_decode(
    seq2seq_model: TinySeq2SeqTransformer | TinyGRUAttentionSeq2Seq,
    encoder_memory: Any,
    tokenizer_for_generation: WordTokenizer
    | CharacterTokenizer
    | SubwordBPETokenizer,
    request: GenerateRequest,
    max_len: int,
) -> list[int]:
    """Детерминированно ищет несколько наиболее вероятных ответов.

    Beam search не добавляет чужих знаний и использует те же локальные веса.
    Он нужен только для проверки гипотезы декодирования: если ранний токен
    оказался неудачным, модель получает ещё несколько маршрутов до EOS.
    """
    beam_width = request.beam_width
    beams: list[tuple[list[int], float, bool]] = [([], 0.0, False)]

    with torch.no_grad():
        for _ in range(request.max_tokens):
            candidates: list[tuple[list[int], float, bool]] = []
            for answer_ids, score, finished in beams:
                if finished:
                    candidates.append((answer_ids, score, True))
                    continue

                # decoder_input_ids всегда начинается с BOS и содержит уже
                # выбранный ответ конкретной beam-ветки.
                if len(answer_ids) + 1 >= max_len:
                    candidates.append((answer_ids, score, True))
                    continue
                decoder_input_ids = torch.tensor(
                    [[tokenizer_for_generation.bos_id, *answer_ids]],
                    dtype=torch.long,
                    device=DEVICE,
                )
                logits = seq2seq_model.decode(
                    decoder_input_ids,
                    encoder_memory,
                )[0, -1, :]
                adjusted_logits = _adjust_logits_for_generation(
                    logits,
                    answer_ids,
                    request,
                )
                log_probabilities = torch.log_softmax(
                    adjusted_logits / request.temperature,
                    dim=-1,
                )
                candidate_count = min(
                    beam_width,
                    int(log_probabilities.shape[-1]),
                )
                values, indices = torch.topk(
                    log_probabilities,
                    candidate_count,
                )
                for value, index in zip(values.tolist(), indices.tolist()):
                    token_id = int(index)
                    candidate_score = score + float(value)
                    if token_id in {
                        tokenizer_for_generation.eos_id,
                        tokenizer_for_generation.pad_id,
                    }:
                        candidates.append((answer_ids, candidate_score, True))
                    else:
                        candidates.append(
                            (
                                [*answer_ids, token_id],
                                candidate_score,
                                False,
                            )
                        )

            if not candidates:
                break

            beams = sorted(
                candidates,
                key=lambda item: _beam_rank(
                    item[1],
                    len(item[0]),
                    request.length_penalty,
                ),
                reverse=True,
            )[:beam_width]
            if all(item[2] for item in beams):
                break

    if not beams:
        return []
    best_beam = max(
        beams,
        key=lambda item: _beam_rank(
            item[1],
            len(item[0]),
            request.length_penalty,
        ),
    )
    return best_beam[0]


@torch.inference_mode()
def _generate_text(request: GenerateRequest, context: str = "") -> str:
    """Генерирует ответ с текущим вопросом и ограниченной историей."""
    if model is None or tokenizer is None:
        raise HTTPException(
            status_code=503,
            detail="Модель не загружена. Сначала обучите её командой из README.md.",
        )

    max_len = int(model_config.get("max_len", 128))
    prompt_ids = encode_prompt_with_context(
        tokenizer,
        request.prompt,
        context,
        max_prompt_length=max(2, max_len - 1),
    )
    input_ids = prompt_ids + [tokenizer.sep_id]
    generated = torch.tensor([input_ids], dtype=torch.long, device=DEVICE)
    encoder_memory = (
        model.encode(generated)
        if isinstance(model, (TinySeq2SeqTransformer, TinyGRUAttentionSeq2Seq))
        else None
    )
    answer_ids: list[int] = []

    if (
        request.decoding == "beam"
        and isinstance(model, (TinySeq2SeqTransformer, TinyGRUAttentionSeq2Seq))
    ):
        answer_ids = _beam_search_decode(
            model,
            encoder_memory,
            tokenizer,
            request,
            max_len,
        )
    else:
        decoder_input_ids = torch.tensor(
            [[tokenizer.bos_id]],
            dtype=torch.long,
            device=DEVICE,
        )
        with torch.no_grad():
            for _ in range(request.max_tokens):
                sequence_length = (
                    decoder_input_ids.shape[1]
                    if isinstance(
                        model,
                        (TinySeq2SeqTransformer, TinyGRUAttentionSeq2Seq),
                    )
                    else generated.shape[1]
                )
                if sequence_length >= max_len:
                    break
                if isinstance(
                    model,
                    (TinySeq2SeqTransformer, TinyGRUAttentionSeq2Seq),
                ):
                    logits = model.decode(decoder_input_ids, encoder_memory)[0, -1, :]
                else:
                    logits = model(generated)[0, -1, :]
                next_token = _sample_next_token(logits, answer_ids, request)
                if next_token == tokenizer.eos_id or next_token == tokenizer.pad_id:
                    break
                answer_ids.append(next_token)
                next_tensor = torch.tensor(
                    [[next_token]],
                    dtype=torch.long,
                    device=DEVICE,
                )
                if isinstance(
                    model,
                    (TinySeq2SeqTransformer, TinyGRUAttentionSeq2Seq),
                ):
                    decoder_input_ids = torch.cat(
                        (decoder_input_ids, next_tensor),
                        dim=1,
                    )
                else:
                    generated = torch.cat((generated, next_tensor), dim=1)

    return tokenizer.decode(answer_ids) or "Модель пока не смогла сформировать ответ."


@app.get("/health")
async def health() -> dict[str, Any]:
    """Возвращает состояние API без раскрытия внутренних секретов."""
    return {
        "status": "ok",
        "model_ready": model is not None and tokenizer is not None,
        "model_error": model_load_error,
        "device": str(DEVICE),
        "architecture": model_architecture,
        "model_version": loaded_model_version,
        "training_topic": loaded_training_topic,
        "knowledge_base_ready": knowledge_base is not None,
        "knowledge_base_examples": knowledge_base.size if knowledge_base else 0,
        "knowledge_base_conflicts": (
            knowledge_base.conflict_count if knowledge_base else 0
        ),
        "internet_search_ready": internet_search is not None,
        "internet_router_ready": internet_router is not None,
        "internet_router_version": internet_router.version if internet_router else None,
        "internet_router_error": internet_router_load_error,
        "conversation_brain_ready": conversation_brain is not None,
        "conversation_brain_version": (
            conversation_brain.version if conversation_brain else None
        ),
        "conversation_brain_error": conversation_brain_load_error,
        "conversation_mind_ready": conversation_mind is not None,
        "conversation_mind_version": (
            conversation_mind.version if conversation_mind else None
        ),
        "conversation_mind_error": conversation_mind_load_error,
        "conversation_mind_response_pool_size": response_pool_size(),
        "web_dialogue_policy_ready": web_dialogue_policy is not None,
        "web_dialogue_policy_version": (
            web_dialogue_policy.version if web_dialogue_policy else None
        ),
        "web_dialogue_policy_error": web_dialogue_policy_load_error,
        "conversation_context_ready": True,
        "default_response_mode": "auto",
        "reliable_model_ready": reliable_qa is not None,
        "reliable_model_version": reliable_qa.version if reliable_qa else None,
        "supported_intents": len(reliable_qa.intents) if reliable_qa else 0,
        "decoding_scope": "complete-answer-catalog",
        "reliable_dialogue_ready": reliable_dialogue is not None,
        "reliable_dialogue_version": reliable_dialogue.version if reliable_dialogue else None,
        "reliable_dialogue_error": reliable_dialogue_load_error,
        "weather_route_ready": True,
        "browser_location_persisted": False,
        "local_voice_route": "IVONA 2 Tatyana OEM",
        "local_voice_cloud_provider": None,
    }


@app.post("/voice/synthesize", response_class=Response, include_in_schema=False)
async def synthesize_voice(request: VoiceSynthesisRequest) -> Response:
    """Возвращает временный WAV IVONA без сохранения текста или аудио в проекте."""

    try:
        audio = await asyncio.to_thread(synthesize_ivona_tatyana, request.text)
    except LocalVoiceError as error:
        # Детали subprocess не уходят в браузер: в них могут быть локальные пути.
        logger.warning("Локальная IVONA-озвучка недоступна: %s", error)
        raise HTTPException(
            status_code=503,
            detail="Локальная озвучка IVONA сейчас недоступна. Текстовый чат продолжит работать.",
        ) from error

    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'inline; filename="vatesai-ivona.wav"',
        },
    )


@dataclass(frozen=True)
class AutomaticResponse:
    """Единый результат маршрутизации без свободной генерации текста."""

    text: str
    model: str
    device: str
    source: str
    match_score: float | None
    context_used: bool
    intent: str | None
    confidence: float | None
    needs_clarification: bool


async def _conversation_mind_reply(
    prompt: str,
    previous_messages: list[dict[str, Any]],
    conversation_id: str,
) -> ConversationMindReply | None:
    """Получает локальный разговорный ответ, не ломая основной чат при сбое.

    Conversation-mind сам отказывается от погоды, дат, фактов и запросов к
    интернету. Эта обёртка нужна лишь для того, чтобы ошибка отдельного
    checkpoint не сделала недоступными QA, память и поиск.
    """
    if conversation_mind is None:
        return None
    try:
        return await asyncio.to_thread(
            conversation_mind.reply,
            prompt,
            previous_messages,
            conversation_id,
        )
    except (RuntimeError, ValueError, KeyError) as error:
        logger.warning("Conversation-mind не ответил, включаю резервный маршрут: %s", error)
        return None


def _automatic_mind_response(reply: ConversationMindReply) -> AutomaticResponse:
    """Переводит контролируемый разговорный ответ в единый API-формат."""
    if conversation_mind is None:
        raise RuntimeError("Conversation-mind недоступен для формирования ответа.")
    return AutomaticResponse(
        text=reply.text,
        model=conversation_mind.version,
        device="cpu",
        source="conversation-mind",
        match_score=None,
        context_used=reply.context_used,
        intent=reply.intent,
        confidence=reply.confidence,
        needs_clarification=False,
    )


def _previous_user_messages(messages: list[dict[str, Any]]) -> list[str]:
    """Берёт только факты, которые действительно написал пользователь."""
    return [
        str(message["content"])
        for message in messages
        if message.get("role") == "user" and str(message.get("content", "")).strip()
    ]


def _focused_question(prompt: str) -> str:
    """Отделяет вопрос от естественно написанных пользователем деталей курса.

    Например, в сообщении «Я специалист, доступ 45 дней. Какой проходной
    балл?» срок нужен индексу для поиска контекста, но не должен заставлять
    fact-ranker ответить сроком вместо проходного балла.
    """
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", prompt)
        if sentence.strip()
    ]
    questions = [sentence for sentence in sentences if "?" in sentence]
    return questions[-1] if questions else prompt


def _lms_clarification(match: LmsKnowledgeMatch) -> str:
    """Формулирует понятное уточнение вместо случайного выбора варианта."""
    topic_labels = {
        "passing_score": "проходном результате для завершения обучения",
        "attempt_count": "числе попыток или пересдаче",
        "access_period": "сроке доступа к материалам",
        "certificate": "сертификате или документе о прохождении",
        "support": "поддержке или консультации",
        "schedule": "дате, времени или расписании",
    }
    detected_topics = [
        topic_labels[topic]
        for topic in match.semantic_topics
        if topic in topic_labels
    ]
    detected_topic = detected_topics[0] if detected_topics else None
    if match.source == "lms-knowledge-ambiguous":
        if len(match.candidate_courses) == 1:
            course = match.candidate_courses[0]
            return (
                f"Я понял, что вопрос о {detected_topic or 'правиле курса'}. "
                f"Для курса «{course}» в локальных материалах есть несколько "
                "разных вариантов правила. Я не буду выбирать случайный. "
                "Напишите в этом же чате видимую деталь страницы курса, например "
                "роль или срок доступа, и я продолжу."
            )
        return (
            f"Я понял, что вопрос о {detected_topic or 'правиле курса'}. "
            "В локальных материалах для него есть несколько разных "
            "вариантов. Я не буду выбирать случайный ответ. Напишите в этом же "
            "чате название курса, а при необходимости роль или срок доступа."
        )
    if detected_topic:
        return (
            f"Я понял, что вопрос о {detected_topic}. В локальных материалах "
            "не нашлось одного подтверждённого правила для ответа. Напишите "
            "название курса обычным сообщением в этом же чате — отдельное поле "
            "контекста не нужно."
        )
    return (
        "Я пока не нашёл подтверждённого правила для этого вопроса в локальных "
        "материалах. Напишите в чате название курса или уточните, о каком "
        "разделе LMS идёт речь."
    )


async def _predict_reliable(
    prompt: str,
    previous_messages: list[dict[str, Any]],
) -> Prediction:
    """Получает ответ веб-модели, если она уверенно распознала вопрос."""
    if reliable_qa is None:
        raise RuntimeError("QA-модель не загружена.")
    if reliable_dialogue is not None:
        return await asyncio.to_thread(
            reliable_dialogue.predict,
            prompt,
            previous_messages,
        )
    return await asyncio.to_thread(reliable_qa.predict, prompt)


def _automatic_lms_response(
    prediction: LmsPrediction,
    source: str,
    match_score: float | None,
) -> AutomaticResponse:
    """Преобразует безопасный LMS-ответ в общий формат API."""
    if reliable_lms is None:
        raise RuntimeError("LMS-модель не загружена.")
    return AutomaticResponse(
        text=prediction.text,
        model=reliable_lms.version,
        device="cpu",
        source=source,
        match_score=match_score,
        context_used=prediction.context_used,
        intent=prediction.task_type,
        confidence=prediction.confidence,
        needs_clarification=not prediction.accepted,
    )


async def _answer_automatically(
    prompt: str,
    conversation_id: str,
    previous_messages: list[dict[str, Any]],
    use_conversation_context: bool,
) -> AutomaticResponse:
    """Отвечает обычному чату только собственным QA- и диалоговым маршрутом.

    Старый контекстный интеграционный маршрут полностью исключён из ``auto``:
    вопрос вне подтверждённого набора либо отправится к открытым источникам,
    либо получит честное уточнение. Поэтому тема обучения больше не может
    перехватить запрос о погоде, времени или любой другой новой теме.
    """
    # Аргумент сохранён в сигнатуре для стабильности существующих вызовов
    # API. Автоматический чат больше не читает и не записывает LMS-состояние.
    # ID диалога остаётся нужен новому composer: он даёт разные естественные
    # варианты ответа, не используя глобальную случайность.
    del use_conversation_context
    mind_reply = await _conversation_mind_reply(
        prompt,
        previous_messages,
        conversation_id,
    )
    if mind_reply is not None:
        return _automatic_mind_response(mind_reply)
    if reliable_qa is None:
        if conversation_brain is not None:
            brain_reply = await asyncio.to_thread(
                conversation_brain.reply,
                prompt,
                previous_messages,
            )
            if brain_reply is not None:
                return AutomaticResponse(
                    text=brain_reply.text,
                    model=conversation_brain.version,
                    device="cpu",
                    source="conversation-brain",
                    match_score=None,
                    context_used=brain_reply.context_used,
                    intent=brain_reply.intent,
                    confidence=brain_reply.confidence,
                    needs_clarification=False,
                )
        return AutomaticResponse(
            text=(
                "Локальный каталог ответов пока не загружен. Перезапустите "
                "локальный чат после проверки артефактов."
            ),
            model="reliable-qa-unavailable-v1",
            device="cpu",
            source="reliable-model-unavailable",
            match_score=None,
            context_used=False,
            intent=None,
            confidence=None,
            needs_clarification=True,
        )

    prediction = await _predict_reliable(prompt, previous_messages)
    if not prediction.accepted and conversation_brain is not None:
        brain_reply = await asyncio.to_thread(
            conversation_brain.reply,
            prompt,
            previous_messages,
        )
        if brain_reply is not None:
            return AutomaticResponse(
                text=brain_reply.text,
                model=conversation_brain.version,
                device="cpu",
                source="conversation-brain",
                match_score=None,
                context_used=brain_reply.context_used,
                intent=brain_reply.intent,
                confidence=brain_reply.confidence,
                needs_clarification=False,
            )
    return AutomaticResponse(
        text=prediction.text,
        model=(
            reliable_dialogue.version
            if prediction.context_used and reliable_dialogue is not None
            else reliable_qa.version
        ),
        device="cpu",
        source=prediction.source,
        match_score=None,
        context_used=prediction.context_used,
        intent=prediction.intent,
        confidence=prediction.confidence,
        needs_clarification=not prediction.accepted,
    )


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest) -> GenerateResponse:
    """Сохраняет вопрос, генерирует ответ и сохраняет его в SQLite."""
    clean_prompt = request.prompt.strip()
    if not clean_prompt:
        raise HTTPException(status_code=422, detail="Вопрос не может быть пустым.")

    # Координаты существуют только пока обрабатывается этот запрос. В SQLite
    # попадают только текстовые реплики пользователя и ассистента, поэтому
    # история диалога не становится журналом геолокации.
    browser_coordinates = (
        (request.latitude, request.longitude)
        if request.latitude is not None and request.longitude is not None
        else None
    )
    conversation_id = request.conversation_id or conversation_store.create_conversation()
    try:
        clean_request = request.model_copy(update={"prompt": clean_prompt})
        prediction = None
        lms_prediction = None
        automatic_response = None
        internet_response_used = False
        temporal_response_used = False
        previous_messages = []
        model_context = ""
        if request.use_conversation_context and not request.use_internet:
            previous_messages = conversation_store.list_messages(
                conversation_id,
                limit=6,
            )
            model_context = build_context(previous_messages)
        # Постоянная идентичность не зависит от модели, истории или сети. Она
        # проверяется первой, чтобы вопрос о ВатэсAi не ушёл в web-search даже
        # при явно включённом интернете.
        identity_reply = assistant_identity_reply(clean_prompt)
        temporal_response = (
            describe_current_temporal_context(clean_prompt)
            if request.response_mode == "auto" and not request.use_internet
            else None
        )
        weather_location_hint = weather_location_from_messages(previous_messages)
        weather_day_hint = weather_day_from_messages(previous_messages)
        weather_question_hint = weather_question_from_messages(previous_messages)
        weather_question = is_weather_question(clean_prompt)
        pickup_point_hours_question = is_pickup_point_hours_question(clean_prompt)
        weather_followup = (
            is_weather_followup(
                clean_prompt,
                has_weather_context=(
                    weather_location_hint is not None or browser_coordinates is not None
                ),
            )
            or is_weather_compact_time_followup(
                clean_prompt,
                has_weather_context=weather_question_hint is not None,
            )
        )
        pickup_point_followup_query = pickup_point_hours_followup_query(
            clean_prompt,
            previous_messages,
        )
        pickup_point_context_clarification = (
            None
            if pickup_point_followup_query is not None
            else pickup_point_hours_clarification(clean_prompt)
        )
        weather_location_followup_value = (
            weather_location_followup(clean_prompt, previous_messages)
            if request.response_mode == "auto"
            else None
        )
        weather_context_clarification = (
            None
            if (
                request.response_mode != "auto"
                or weather_location_followup_value is not None
            )
            else weather_location_clarification(
                clean_prompt,
                location_hint=weather_location_hint,
                coordinates_hint=browser_coordinates,
            )
        )
        general_web_followup_query = (
            clarification_followup_web_query(clean_prompt, previous_messages)
            if request.response_mode == "auto"
            else None
        )
        general_context_clarification = (
            None
            if (
                request.response_mode != "auto"
                or general_web_followup_query is not None
            )
            else clarification_for_prompt(clean_prompt, previous_messages)
        )
        # Простые человеческие реплики не должны ждать QA-каталог или случайно
        # уходить в интернет. Погода сохраняет приоритет: просьба выбрать одежду
        # по погоде всё равно должна попасть в структурированный прогноз.
        conversation_route_eligible = (
            request.response_mode == "auto"
            and not request.use_internet
            and not weather_question
            and not weather_followup
            and weather_location_followup_value is None
            and weather_context_clarification is None
            and not pickup_point_hours_question
            and pickup_point_context_clarification is None
            and pickup_point_followup_query is None
            and general_context_clarification is None
            and general_web_followup_query is None
        )
        # Новый слой получает первый шанс только на обычное общение. Он сам
        # откажется от фактического вопроса, чтобы не заслонить QA или поиск.
        mind_direct_reply = (
            await _conversation_mind_reply(
                clean_prompt,
                previous_messages,
                conversation_id,
            )
            if conversation_route_eligible
            else None
        )
        # Старый узкий слой остаётся резервом, пока новый checkpoint не
        # загружен или сознательно пропустил непрофильную реплику.
        direct_reply = (
            direct_conversation_reply(clean_prompt, previous_messages)
            if conversation_route_eligible and mind_direct_reply is None
            else None
        )
        if identity_reply is not None:
            automatic_response = AutomaticResponse(
                text=identity_reply.text,
                model=IDENTITY_VERSION,
                device="cpu",
                source="assistant-identity",
                match_score=None,
                context_used=False,
                intent=identity_reply.intent,
                confidence=1.0,
                needs_clarification=False,
            )
            response_text = automatic_response.text
            response_source = automatic_response.source
            match_score = None
        elif temporal_response is not None:
            response_text = temporal_response
            response_source = "local-temporal-context"
            match_score = None
            temporal_response_used = True
        elif pickup_point_context_clarification is not None:
            automatic_response = AutomaticResponse(
                text=pickup_point_context_clarification,
                model="web-context-clarification-v1",
                device="cpu",
                source="web-context-clarification",
                match_score=None,
                context_used=False,
                intent="clarify_required_web_context",
                confidence=1.0,
                needs_clarification=True,
            )
            response_text = automatic_response.text
            response_source = automatic_response.source
            match_score = None
        elif weather_context_clarification is not None:
            automatic_response = AutomaticResponse(
                text=weather_context_clarification,
                model="weather-context-clarification-v1",
                device="cpu",
                source="weather-context-clarification",
                match_score=None,
                context_used=bool(previous_messages),
                intent="clarify_missing_weather_location",
                confidence=1.0,
                needs_clarification=True,
            )
            response_text = automatic_response.text
            response_source = automatic_response.source
            match_score = None
        elif general_context_clarification is not None:
            automatic_response = AutomaticResponse(
                text=general_context_clarification.text,
                model="clarification-policy-v1",
                device="cpu",
                source="context-clarification",
                match_score=None,
                context_used=bool(previous_messages),
                intent=general_context_clarification.intent,
                confidence=1.0,
                needs_clarification=True,
            )
            response_text = automatic_response.text
            response_source = automatic_response.source
            match_score = None
        elif request.use_internet:
            if len(clean_prompt) < 2:
                raise HTTPException(
                    status_code=422,
                    detail="Для интернет-поиска нужно минимум 2 символа.",
                )
            # Внешний API может по-прежнему попросить интернет явно. Ответ
            # состоит только из найденных выдержек и ссылок; они не становятся
            # командами для системы или скрытыми инструкциями модели.
            web_query = (
                pickup_point_followup_query
                or general_web_followup_query
                or clean_prompt
            )
            if weather_location_followup_value is not None:
                weather_query, weather_location = weather_location_followup_value
                internet_arguments = (weather_query, weather_location)
            elif is_weather_question(clean_prompt) and browser_coordinates is not None:
                internet_arguments = (web_query, None, None, False, browser_coordinates)
            else:
                internet_arguments = (web_query,)
            web_answer = await asyncio.to_thread(internet_search.answer, *internet_arguments)
            response_text = web_answer.text
            response_source = "internet-search-explicit"
            match_score = None
            internet_response_used = True
        elif mind_direct_reply is not None:
            automatic_response = _automatic_mind_response(mind_direct_reply)
            response_text = automatic_response.text
            response_source = automatic_response.source
            match_score = None
        elif direct_reply is not None:
            automatic_response = AutomaticResponse(
                text=direct_reply.text,
                model=(
                    conversation_brain.version
                    if conversation_brain is not None
                    else "conversation-policy-v1"
                ),
                device="cpu",
                source="conversation-direct",
                match_score=None,
                context_used=direct_reply.context_used,
                intent=direct_reply.intent,
                confidence=direct_reply.confidence,
                needs_clarification=False,
            )
            response_text = automatic_response.text
            response_source = automatic_response.source
            match_score = None
        elif request.response_mode == "auto":
            rule_requests_web = should_search_automatically(
                clean_prompt,
                internet_router,
            ) if (
                pickup_point_followup_query is None
                and general_web_followup_query is None
                and weather_location_followup_value is None
                and not pickup_point_hours_question
            ) else False
            # Новый policy-слой не отменяет старые надёжные правила. Он лишь
            # добавляет локально обученный сигнал для живых формулировок,
            # которые старый router ещё не видел.
            policy_requests_web = (
                pickup_point_followup_query is None
                and general_web_followup_query is None
                and weather_location_followup_value is None
                and not pickup_point_hours_question
                and not rule_requests_web
                and web_dialogue_policy is not None
                and web_dialogue_policy.should_search(clean_prompt)
            )
            if (
                pickup_point_followup_query is not None
                or general_web_followup_query is not None
                or weather_location_followup_value is not None
                or pickup_point_hours_question
                or weather_question
                or rule_requests_web
                or policy_requests_web
                or weather_followup
            ):
                if pickup_point_followup_query is not None:
                    internet_arguments = (pickup_point_followup_query,)
                elif general_web_followup_query is not None:
                    internet_arguments = (general_web_followup_query,)
                elif weather_location_followup_value is not None:
                    weather_query, weather_location = weather_location_followup_value
                    internet_arguments = (weather_query, weather_location)
                elif pickup_point_hours_question:
                    internet_arguments = (clean_prompt,)
                elif weather_question or weather_followup:
                    internet_arguments = (
                        clean_prompt,
                        weather_location_hint,
                        weather_day_hint,
                        weather_followup,
                        browser_coordinates,
                        weather_question_hint,
                    )
                else:
                    internet_arguments = (clean_prompt,)
                web_answer = await asyncio.to_thread(
                    internet_search.answer,
                    *internet_arguments,
                )
                response_text = web_answer.text
                response_source = (
                    "internet-search-auto-policy"
                    if policy_requests_web
                    else "internet-search-auto"
                )
                match_score = None
                internet_response_used = True
            else:
                automatic_response = await _answer_automatically(
                    clean_prompt,
                    conversation_id,
                    previous_messages,
                    request.use_conversation_context,
                )
                if (
                    automatic_response.needs_clarification
                    and should_search_after_local_clarification(clean_prompt)
                ):
                    web_answer = await asyncio.to_thread(
                        internet_search.answer,
                        clean_prompt,
                    )
                    response_text = web_answer.text
                    response_source = "internet-search-auto"
                    match_score = None
                    automatic_response = None
                    internet_response_used = True
                else:
                    response_text = automatic_response.text
                    response_source = automatic_response.source
                    match_score = automatic_response.match_score
        elif request.response_mode == "lms":
            if reliable_lms is None:
                raise HTTPException(
                    status_code=503,
                    detail="LMS-модель ещё не загружена. Проверьте локальный запуск.",
                )
            lms_context = (request.lms_context or "").strip()
            if not lms_context:
                raise HTTPException(
                    status_code=422,
                    detail="Для режима lms нужно передать непустой lms_context.",
                )
            lms_prediction = await asyncio.to_thread(
                reliable_lms.predict,
                clean_prompt,
                lms_context,
            )
            response_text = lms_prediction.text
            response_source = lms_prediction.source
            match_score = None
            if lms_prediction.accepted:
                # Прямой API-вызов тоже помогает последующим коротким вопросам
                # того же диалога, но только после подтверждённого ответа.
                conversation_store.set_lms_context(conversation_id, lms_context)
        elif request.response_mode == "reliable":
            if reliable_qa is None:
                raise HTTPException(status_code=503, detail="QA-модель ещё не загружена. Проверьте локальный запуск.")
            if reliable_dialogue is not None:
                prediction = await asyncio.to_thread(
                    reliable_dialogue.predict, clean_prompt, previous_messages,
                )
            else:
                prediction = await asyncio.to_thread(reliable_qa.predict, clean_prompt)
            response_text = prediction.text
            response_source = prediction.source
            match_score = None
        else:
            known_answer = None
            similar_answer: tuple[str, float] | None = None
            has_dataset_conflict = False
            if request.use_knowledge_base and knowledge_base:
                has_dataset_conflict = knowledge_base.is_conflicted_prompt(
                    clean_prompt
                )
                if not has_dataset_conflict:
                    known_answer = knowledge_base.lookup(clean_prompt)
                if known_answer is None and not has_dataset_conflict:
                    similar_answer = knowledge_base.lookup_similar(clean_prompt)
            if has_dataset_conflict:
                response_text = (
                    "В локальном датасете для этого вопроса есть несколько "
                    "противоречивых ответов. Нужна ручная проверка данных."
                )
                response_source = "local-dataset-conflict"
                match_score = None
            elif known_answer is not None:
                response_text = known_answer
                response_source = "local-dataset-exact-match"
                match_score = 1.0
            elif similar_answer is not None:
                response_text, match_score = similar_answer
                response_source = "local-dataset-similar-match"
            else:
                response_text = _generate_text(clean_request, context=model_context)
                response_source = "neural-model"
                match_score = None
        context_used = automatic_response.context_used if automatic_response else (
            lms_prediction.context_used if lms_prediction else (
            prediction.context_used if prediction else (
                response_source == "neural-model" and bool(model_context)
            )
            )
        )
        conversation_store.add_message(conversation_id, "user", clean_prompt)
        conversation_store.add_message(conversation_id, "assistant", response_text)
        # Создание token не пишет ничего в SQLite. Запись появится только если
        # пользователь сам оценит видимый ему ответ в браузере.
        feedback_token = create_feedback_token()
        if temporal_response_used:
            response_model = "local-temporal-context-v1"
            response_device = "cpu"
            response_intent = "current_temporal_context"
            response_confidence = 1.0
            response_needs_clarification = False
        elif internet_response_used:
            response_model = "source-grounded-web-v1"
            response_device = "network"
            response_intent = "internet_answer"
            response_confidence = None
            response_needs_clarification = False
        elif automatic_response is not None:
            response_model = automatic_response.model
            response_device = automatic_response.device
            response_intent = automatic_response.intent
            response_confidence = automatic_response.confidence
            response_needs_clarification = automatic_response.needs_clarification
        elif lms_prediction is not None and reliable_lms is not None:
            response_model = reliable_lms.version
            response_device = "cpu"
            response_intent = lms_prediction.task_type
            response_confidence = lms_prediction.confidence
            response_needs_clarification = not lms_prediction.accepted
        else:
            response_model = (
                reliable_dialogue.version if prediction and prediction.context_used and reliable_dialogue
                else reliable_qa.version if prediction and reliable_qa else loaded_model_version
            )
            response_device = "cpu" if prediction else str(DEVICE)
            response_intent = prediction.intent if prediction else None
            response_confidence = prediction.confidence if prediction else None
            response_needs_clarification = bool(prediction and not prediction.accepted)
        return GenerateResponse(
            text=response_text,
            conversation_id=conversation_id,
            model=response_model,
            device=response_device,
            source=response_source,
            match_score=match_score,
            context_used=context_used,
            intent=response_intent,
            confidence=response_confidence,
            needs_clarification=response_needs_clarification,
            response_mode=request.response_mode,
            feedback_token=feedback_token,
        )
    except HTTPException:
        raise
    except InternetSearchError as error:
        logger.warning("Интернет-поиск недоступен: %s", error)
        raise HTTPException(
            status_code=502,
            detail="Интернет-поиск временно недоступен.",
        ) from error
    except (OSError, RuntimeError, ValueError) as error:
        logger.exception("Ошибка обработки запроса.")
        raise HTTPException(
            status_code=500,
            detail="Внутренняя ошибка обработки запроса.",
        ) from error


@app.post("/feedback", response_model=FeedbackResponse, status_code=201)
async def save_feedback(request: FeedbackRequest) -> FeedbackResponse:
    """Сохраняет добровольную оценку ответа без копирования текста чата."""
    try:
        response_id = response_id_from_feedback_token(request.feedback_token)
        conversation_store.save_response_feedback(response_id, request.rating)
        return FeedbackResponse(rating=request.rating)
    except FeedbackAlreadySubmittedError as error:
        raise HTTPException(
            status_code=409,
            detail="Для этого ответа оценка уже сохранена.",
        ) from error
    except ValueError as error:
        # Token становится недействительным после перезапуска без настроенного
        # ключа. Это не влияет на сам чат и не раскрывает его историю.
        raise HTTPException(
            status_code=422,
            detail="Эту оценку уже нельзя принять. Оцените новый ответ.",
        ) from error
    except (OSError, RuntimeError) as error:
        logger.exception("Не удалось сохранить добровольную оценку ответа.")
        raise HTTPException(
            status_code=500,
            detail="Не удалось сохранить оценку. Попробуйте позже.",
        ) from error


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    """Даёт сайту отдельный endpoint контролируемого интернет-поиска."""
    clean_query = request.query.strip()
    if not 2 <= len(clean_query) <= 300:
        raise HTTPException(
            status_code=422,
            detail="Поисковый запрос должен содержать от 2 до 300 символов.",
        )
    try:
        results = await asyncio.to_thread(
            internet_search.search,
            clean_query,
            request.max_results,
        )
        return SearchResponse(
            query=clean_query,
            results=[
                SearchResultResponse(
                    title=result.title,
                    url=result.url,
                    snippet=result.snippet,
                )
                for result in results
            ],
        )
    except InternetSearchError as error:
        logger.warning("Интернет-поиск недоступен: %s", error)
        raise HTTPException(
            status_code=502,
            detail="Интернет-поиск временно недоступен.",
        ) from error
    except (OSError, RuntimeError, ValueError) as error:
        logger.exception("Ошибка поискового запроса.")
        raise HTTPException(
            status_code=500,
            detail="Внутренняя ошибка поискового запроса.",
        ) from error


@app.get("/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str) -> dict[str, Any]:
    """Возвращает сохранённые сообщения конкретного локального диалога."""
    try:
        return {
            "conversation_id": conversation_id,
            "messages": conversation_store.list_messages(conversation_id),
        }
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(
            status_code=500,
            detail="Не удалось прочитать историю диалога.",
        ) from error


@app.get("/", include_in_schema=False)
async def web_client() -> FileResponse:
    """Отдаёт тестовый веб-интерфейс из локальной папки проекта."""
    index_path = WEB_CLIENT_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Веб-интерфейс не найден.")
    return FileResponse(index_path)
