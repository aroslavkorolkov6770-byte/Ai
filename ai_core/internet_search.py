"""Ограниченный интернет-поиск без выполнения внешних команд."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from math import isfinite
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen


class InternetSearchError(RuntimeError):
    """Ошибка контролируемого поиска, которую можно показать API-клиенту."""


@dataclass(frozen=True)
class SearchResult:
    """Один очищенный результат поиска."""

    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class WebAnswer:
    """Короткий ответ, составленный только из найденных открытых источников."""

    text: str
    results: tuple[SearchResult, ...]


class LearnedInternetRouter(Protocol):
    """Минимальный интерфейс локального обученного маршрутизатора."""

    def should_use_web(self, prompt: str) -> bool:
        """Возвращает True только для уверенного решения о поиске."""


# Эти маркеры не являются обучающими примерами и не дают готовых ответов. Они
# определяют только тот случай, когда локальных знаний недостаточно и вопрос
# действительно требует открытых, меняющихся или явно запрошенных сведений.
EXPLICIT_SEARCH_MARKERS = (
    "найди",
    "поищи",
    "поиск",
    "в интернете",
    "в сети",
    "на сайте",
    "проверь",
)
FRESH_FACT_MARKERS = (
    "актуальн",
    "последн",
    "свеж",
    "новост",
    "погода",
    "курс валют",
    "цена",
    "стоим",
    "расписан",
    "результат матч",
)
# «Сегодня» и «сейчас» сами по себе не являются просьбой о поиске: они часто
# встречаются в обычной беседе («я сегодня устал»). Их обработка ниже требует
# именно формы запроса о знании или свежем факте.
CURRENT_TIME_MARKERS = ("сегодня", "сейчас")
PERSONAL_CONVERSATION_MARKERS = (
    "как мне", "стоит ли мне", "что мне делать", "я сегодня", "мне сегодня",
    "давай сегодня", "хочу сегодня", "поговорим сегодня", "как ты считаешь",
)
# Имя оставлено для обратной совместимости с возможными локальными импортами.
# В отличие от прошлой версии оно содержит только безусловные основания
# обратиться к сети, а относительное время проверяется отдельно.
AUTO_SEARCH_MARKERS = EXPLICIT_SEARCH_MARKERS + FRESH_FACT_MARKERS
QUESTION_PREFIXES = ("кто", "что", "какой", "какая", "какие", "где", "когда")
# Фразы без вопросительного знака тоже могут быть обычной просьбой о знании:
# «Расскажи о космосе» не должна получить отказ только из-за формы глагола.
# Прямые разговорные реплики отсекаются раньше в conversation-brain, поэтому
# этот список не превращает приветствие или благодарность в интернет-поиск.
GENERAL_KNOWLEDGE_PREFIXES = QUESTION_PREFIXES + (
    "почему",
    "зачем",
    "как",
    "объясни",
    "расскажи",
    "покажи",
    "найди",
    "сравни",
    "переведи",
    "посоветуй",
    "напиши",
)
SOURCE_REQUEST_MARKERS = (
    "ссылк",
    "источник",
    "откуда информация",
    "откуда данные",
    "где почитать",
    "где прочитать",
    "покажи ресурс",
    "дай ресурс",
    "документаци",
)
# Часы пункта выдачи зависят минимум от конкретной точки и места. Геолокация
# сама по себе не выбирает нужный сервис или филиал, поэтому до web-route
# требуется явное уточнение пользователя.
PICKUP_POINT_MARKERS = ("пункт выдачи", "пвз")
PICKUP_POINT_HOURS_MARKERS = (
    "работает",
    "работают",
    "открыт",
    "открыта",
    "закрыт",
    "закрыта",
    "час",
    "сегодня",
    "завтра",
)
PICKUP_POINT_CLARIFICATION = (
    "Уточни, какой именно пункт выдачи и в каком городе или по какому адресу."
)
WEATHER_LOCATION_CLARIFICATION = (
    "Чтобы назвать погоду, укажите город, село или другой населённый пункт. "
    "Например: «Какая завтра погода в Старом Осколе?»"
)
# Город сам по себе не определяет нужный ПВЗ. Достаточны либо полный адрес,
# либо имя пункта вместе с городом. Шаблоны с собственными именами намеренно
# не используют ``re.IGNORECASE``: иначе «в пункт выдачи» ошибочно стало бы
# похожим на город.
PICKUP_POINT_ADDRESS_KEYWORD_PATTERN = re.compile(
    r"\b(?:адрес(?:у|е)?|улиц[аеы]?|проспект[е]?|дом(?:е)?|д\.?|район(?:е)?)\b"
    r"[^\n!?]{0,80}?\b\d{1,4}\b",
    flags=re.IGNORECASE,
)
PICKUP_POINT_CAPITALIZED_ADDRESS_PATTERN = re.compile(
    r"\b[А-ЯЁ][А-ЯЁа-яё-]{1,}\s*,?\s*\d{1,4}\b"
)
PICKUP_POINT_CITY_PATTERN = re.compile(
    r"(?:\b(?:г\.?|город(?:е|а)?)\s+[А-ЯЁ][А-ЯЁа-яё-]{1,}|"
    r"\b(?:в|из)\s+[А-ЯЁ][А-ЯЁа-яё-]{1,})"
)
PICKUP_POINT_NAMED_POINT_PATTERN = re.compile(
    r"\b(?:пункт выдачи|пвз)\s*(?:«[^»\n]{2,80}»|\"[^\"\n]{2,80}\")",
    flags=re.IGNORECASE,
)
LOCAL_LMS_MARKERS = (
    "lms",
    "курс",
    "обучен",
    "проходн",
    "пересда",
    "попыт",
    "сертификат",
)
SEARCH_STOPWORDS = frozenset(
    {
        "а",
        "в",
        "во",
        "вы",
        "где",
        "да",
        "для",
        "до",
        "и",
        "из",
        "как",
        "какая",
        "какие",
        "какой",
        "когда",
        "кто",
        "ли",
        "мне",
        "на",
        "не",
        "о",
        "об",
        "по",
        "пожалуйста",
        "про",
        "с",
        "со",
        "что",
        "это",
        "я",
    }
)
WORD_PATTERN = re.compile(r"[a-zа-яё0-9]+", flags=re.IGNORECASE)
# Один общий термин страницы с вопросом ещё не доказывает, что фраза отвечает
# пользователю. Порог 4 требует либо нескольких общих смысловых корней, либо
# одного связанного корня вместе с измеряемой деталью (датой, числом, процентом).
MIN_GROUNDED_EVIDENCE_SCORE = 4
# Пользователь естественно меняет порядок слов: «погода в Минске завтра» и
# «какая завтра погода в Минске» означают одно и то же. Поэтому не привязываем
# извлечение города к одной фиксированной фразе.
WEATHER_LOCATION_PATTERNS = (
    # Место может прозвучать отдельной фразой перед погодным вопросом:
    # «Я буду в Калуге. Во сколько сегодня закат?». Этот паттерн используется
    # только после распознавания weather-intent, поэтому обычная беседа не
    # начинает извлекать города сама по себе.
    re.compile(
        r"\b(?:я\s+)?(?:буду|нахожусь|еду|поеду|приезжаю|прилечу)\s+"
        r"(?:в|на)\s+(?P<location>.+?)(?=[?!.]|$)",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:сегодня|завтра|сейчас)\s+погод[а-яё]*\s+(?:в|на)\s+"
        r"(?P<location>.+?)(?=[?!.]|$)",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\bпогод[а-яё]*\s+(?:на\s+)?(?:сегодня|завтра)\s+(?:в|на)\s+"
        r"(?P<location>.+?)(?=[?!.]|$)",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\bпогод[а-яё]*\s+(?:в|на)\s+(?P<location>.+?)"
        r"(?=\s+(?:сегодня|завтра|сейчас)\b|[?!.]|$)",
        flags=re.IGNORECASE,
    ),
    # Вопрос может вообще не содержать слова «погода»: «Будет ли завтра
    # дождь в Минске?» и «Какая температура в Гомеле?» должны получать тот же
    # структурированный маршрут, а не общий HTML-поиск.
    re.compile(
        r"\b(?:дожд[ьяе]?|ливн[яе]?|снег[а-яё]*|гроз[а-яё]*|град[а-яё]*|"
        r"ветер|температур[а-яё]*|градус[а-яё]*|осадк[а-яё]*|облач[а-яё]*|"
        r"туман[а-яё]*|влажност[а-яё]*|давлен[а-яё]*|видимост[а-яё]*|"
        r"зонт[а-яё]*|одеж[а-яё]*|одет[а-яё]*|надет[а-яё]*|восход[а-яё]*|"
        r"закат[а-яё]*|рассвет[а-яё]*|ясн[а-яё]*|"
        r"солнеч[а-яё]*|тепл[а-яё]*|прохлад[а-яё]*|душн[а-яё]*|метел[а-яё]*|вьюг[а-яё]*)\b"
        r".*?\s+(?:в|на)\s+(?P<location>.+?)"
        r"(?=\s+(?:сегодня|завтра|послезавтра|сейчас|утром|днём|днем|вечером|ночью)\b|[?!.]|$)",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:в|на)\s+(?P<location>.+?)\s+"
        r"(?:будет|ожидается|начн[её]т|законч[аи][её]т|дожд[ьяе]?|ливн[яе]?|"
        r"снег[а-яё]*|гроз[а-яё]*|ветер|температур[а-яё]*|градус[а-яё]*|"
        r"осадк[а-яё]*|облач[а-яё]*|туман[а-яё]*|влажност[а-яё]*|"
        r"давлен[а-яё]*|видимост[а-яё]*|восход[а-яё]*|закат[а-яё]*|рассвет[а-яё]*|"
        r"ясн[а-яё]*|солнеч[а-яё]*|тепл[а-яё]*|прохлад[а-яё]*|душн[а-яё]*|метел[а-яё]*|вьюг[а-яё]*)\b"
        r"(?=\s+(?:сегодня|завтра|послезавтра|сейчас|утром|днём|днем|вечером|ночью)\b|[?!.]|$)",
        flags=re.IGNORECASE,
    ),
)
# Погодный маршрут должен быть точным: ошибка здесь особенно заметна, потому
# что вместо нормального разговора пользователь получает прогноз. Поэтому
# явления отделены от слов, которые часто встречаются вне метеорологии.
# Например, «солнечная система», «температура тела» и «одежда на
# собеседование» не должны попадать в Open-Meteo.
WEATHER_CORE_MARKERS = (
    "погод",
    "дожд",
    "ливн",
    "снег",
    "гроз",
    "град",
    "ветер",
    "осад",
    "облач",
    "туман",
    "голол",
    "замороз",
    "метел",
    "вьюг",
)
WEATHER_MEASUREMENT_MARKERS = (
    "температур",
    "градус",
    "видимост",
    "влажност",
    "давлен",
)
WEATHER_ASTRONOMY_MARKERS = (
    "рассвет",
    "восход",
    "закат",
)
WEATHER_CONDITION_MARKERS = (
    "ясн",
    "солнеч",
    "тепл",
    "прохлад",
    "душн",
    "жар",
    "холод",
    "мороз",
)
# Имя сохранено для обратной совместимости с локальными импортами. Само по
# себе совпадение с этим списком больше не отправляет сообщение в прогноз.
WEATHER_INTENT_MARKERS = (
    WEATHER_CORE_MARKERS
    + WEATHER_MEASUREMENT_MARKERS
    + WEATHER_ASTRONOMY_MARKERS
    + WEATHER_CONDITION_MARKERS
)
WEATHER_WORD_PATTERN = re.compile(
    "|".join(re.escape(marker) for marker in WEATHER_INTENT_MARKERS),
    flags=re.IGNORECASE,
)
WEATHER_TIME_MARKERS = (
    "сегодня",
    "завтра",
    "послезавтра",
    "сейчас",
    "утром",
    "днём",
    "днем",
    "вечером",
    "ночью",
)
WEATHER_NON_METEOROLOGICAL_MARKERS = (
    "температура тела",
    "температуру тела",
    "температура процессора",
    "температура двигателя",
    "температура воды",
    "артериальн",
    "кровян",
    "давление у человека",
    "давление человека",
    "солнечная система",
    "солнечной системы",
    "солнечная энергия",
    "одежда на",
    "костюм на",
)
WEATHER_CLOTHING_PATTERN = re.compile(
    r"(?:^|\s)(?:что|как)\s+(?:мне\s+)?(?:надеть|одеться|одеваться)(?:$|\s|[?!.])",
    flags=re.IGNORECASE,
)
WEATHER_SHORT_FOLLOWUP_PATTERN = re.compile(
    r"^(?:а\s+)?(?:"
    r"(?:со|до)\s+скольки|"
    r"с\s+какого\s+времени|"
    r"до\s+какого\s+времени|"
    r"во\s+сколько|"
    r"когда\s+начн[её]тся|"
    r"когда\s+закончится|"
    r"утром|дн(?:е|ё)м|вечером|ночью"
    r")[?!.]?$",
    flags=re.IGNORECASE,
)
# Узкое продолжение после уже начатого прогноза. Оно не является новым
# weather-intent само по себе: без сохранённого погодного вопроса в текущем
# чате такая фраза остаётся обычной репликой.
WEATHER_COMPACT_TIME_FOLLOWUP_PATTERN = re.compile(
    r"^(?:и\s+)?(?:(?:напиши|покажи|скажи)\s+)?(?:мне\s+)?"
    r"(?:только\s+)?время(?:\s*,?\s*пожалуйста)?[?!.]?$",
    flags=re.IGNORECASE,
)
# Для прямой просьбы «напиши только время» не добавляем название города,
# прогноз или ссылку: пользователю нужен ровно результат инструмента.
WEATHER_TIME_ONLY_REQUEST_PATTERN = re.compile(
    r"(?:\b(?:только|лишь)\s+время\b|\bвремя\s+(?:только|лишь)\b|"
    r"\bбез\s+(?:слов|объяснений|текста)\b)",
    flags=re.IGNORECASE,
)
HOURLY_PRECIPITATION_MARKERS = (
    "со скольки",
    "до скольки",
    "с какого времени",
    "до какого времени",
    "когда начн",
    "когда законч",
    "во сколько",
)
RAIN_WEATHER_CODES = frozenset({51, 53, 55, 61, 63, 65, 80, 81, 82})
SNOW_WEATHER_CODES = frozenset({71, 73, 75, 77, 85, 86})
PREFERRED_SOURCE_DOMAINS = {
    # Структурированный маршрут погоды не парсит рекламные страницы и всегда
    # использует этот источник числового прогноза.
    "open-meteo.com": 100,
    # При общем поиске домены официальных организаций получают приоритет, но
    # не объявляются автоматически истинными: факт всё равно извлекается из
    # текста конкретной страницы.
    "wmo.int": 90,
    "who.int": 90,
    "noaa.gov": 90,
    "metoffice.gov.uk": 85,
    "gov.ru": 80,
    "gov.by": 80,
}
WEATHER_CODES = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "туман с изморозью",
    51: "слабая морось",
    53: "умеренная морось",
    55: "сильная морось",
    61: "слабый дождь",
    63: "умеренный дождь",
    65: "сильный дождь",
    71: "слабый снег",
    73: "умеренный снег",
    75: "сильный снег",
    80: "ливневый дождь",
    81: "умеренный ливневый дождь",
    82: "сильный ливневый дождь",
    95: "гроза",
}


def _clean_text(value: str) -> str:
    """Убирает HTML-сущности и лишние пробелы из текста результата."""
    return " ".join(unescape(value).split())


def should_search_automatically(
    prompt: str,
    learned_router: LearnedInternetRouter | None = None,
) -> bool:
    """Определяет, нужен ли интернет до обращения к локальной модели.

    Обычный вопрос по HTML/CSS/JS не уходит в сеть: для него быстрее и точнее
    локальный проверенный ответ. Явные маркеры остаются быстрой детерминированной
    защитой. Обученный локальный router может расширить охват новых естественных
    формулировок, но не отменяет эти правила и не вызывает сеть сам.
    """
    clean_prompt = " ".join(prompt.casefold().split())
    if not clean_prompt:
        return False
    if any(marker in clean_prompt for marker in AUTO_SEARCH_MARKERS):
        return True
    if any(marker in clean_prompt for marker in CURRENT_TIME_MARKERS):
        # «Как ты считаешь, стоит ли мне сегодня…» — личный совет, а не
        # обновляемый факт. Для таких фраз сначала остаётся разговорный слой.
        if any(marker in clean_prompt for marker in PERSONAL_CONVERSATION_MARKERS):
            return False
        if clean_prompt.startswith(GENERAL_KNOWLEDGE_PREFIXES):
            return True
    if learned_router is None:
        return False
    try:
        return bool(learned_router.should_use_web(prompt))
    except (RuntimeError, TypeError, ValueError):
        # Отдельная модель маршрута не должна ломать чат при повреждённом
        # checkpoint или неожиданном тексте: остаётся старый безопасный путь.
        return False


def should_search_after_local_clarification(prompt: str) -> bool:
    """Разрешает поиск для общего вопроса или просьбы о знании после QA."""
    clean_prompt = " ".join(prompt.casefold().split())
    return clean_prompt.startswith(GENERAL_KNOWLEDGE_PREFIXES)


def is_pickup_point_hours_question(prompt: str) -> bool:
    """Отличает проверку работы ПВЗ от обычного разговора о доставке."""

    clean_prompt = " ".join(prompt.casefold().split())
    return bool(clean_prompt) and any(
        marker in clean_prompt for marker in PICKUP_POINT_MARKERS
    ) and any(marker in clean_prompt for marker in PICKUP_POINT_HOURS_MARKERS)


def _has_pickup_point_location_detail(prompt: str) -> bool:
    """Проверяет, что пользователь дал данные для поиска конкретного ПВЗ."""

    has_address = bool(
        PICKUP_POINT_ADDRESS_KEYWORD_PATTERN.search(prompt)
        or PICKUP_POINT_CAPITALIZED_ADDRESS_PATTERN.search(prompt)
    )
    has_named_point_in_city = bool(
        PICKUP_POINT_NAMED_POINT_PATTERN.search(prompt)
        and PICKUP_POINT_CITY_PATTERN.search(prompt)
    )
    return has_address or has_named_point_in_city


def pickup_point_hours_clarification(prompt: str) -> str | None:
    """Возвращает один точный вопрос до поиска при отсутствии данных о точке."""

    if (
        is_pickup_point_hours_question(prompt)
        and not _has_pickup_point_location_detail(prompt)
    ):
        return PICKUP_POINT_CLARIFICATION
    return None


def pickup_point_hours_followup_query(
    prompt: str,
    previous_messages: list[dict[str, object]],
) -> str | None:
    """Собирает поиск после ответа пользователя на предыдущее уточнение ПВЗ.

    Используется только в текущем диалоге, когда последняя реплика ассистента
    была именно локальным уточнением. Так произвольная фраза с адресом не
    превращается в скрытый интернет-поиск.
    """

    if not _has_pickup_point_location_detail(prompt):
        return None
    if not previous_messages:
        return None
    clarification = " ".join(PICKUP_POINT_CLARIFICATION.casefold().split())
    previous_message = previous_messages[-1]
    previous_content = previous_message.get("content")
    if (
        previous_message.get("role") != "assistant"
        or not isinstance(previous_content, str)
        or " ".join(previous_content.casefold().split()) != clarification
    ):
        return None
    for message in reversed(previous_messages[:-1]):
        content = message.get("content")
        if (
            message.get("role") == "user"
            and isinstance(content, str)
            and is_pickup_point_hours_question(content)
        ):
            return f"{content.strip()} {prompt.strip()}"
    return None


def should_show_source_links(prompt: str) -> bool:
    """Показывает URL только по прямой просьбе пользователя.

    Ссылки всё равно используются внутри маршрута, чтобы проверить факт, но
    не засоряют обычный ответ. Отдельный API ``POST /search`` по-прежнему
    возвращает ссылки, потому что сам его вызов уже является явным запросом
    поиска ресурсов.
    """
    clean_prompt = " ".join(prompt.casefold().split())
    return bool(clean_prompt) and any(
        marker in clean_prompt for marker in SOURCE_REQUEST_MARKERS
    )


def _important_terms(text: str) -> set[str]:
    """Оставляет слова, по которым можно выбрать фразу-ответ со страницы."""
    return {
        word
        for word in WORD_PATTERN.findall(text.casefold())
        if word not in SEARCH_STOPWORDS and len(word) >= 3
    }


def _term_roots(terms: set[str]) -> set[str]:
    """Сглаживает простые словоформы: «Минск» и «Минске» ближе друг к другу."""
    return {term[:5] if len(term) >= 5 else term for term in terms}


def source_reliability_score(url: str) -> int:
    """Возвращает приоритет известного официального домена для чтения первым.

    Оценка не превращает страницу в доказанный факт. Она лишь определяет
    безопасный порядок чтения, когда поисковая выдача смешивает официальный
    ресурс, рекламу и случайные агрегаторы.
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    if not hostname:
        return 0
    for domain, score in PREFERRED_SOURCE_DOMAINS.items():
        if hostname == domain or hostname.endswith("." + domain):
            return score
    if hostname.endswith(".gov") or hostname.endswith(".gov.ru") or hostname.endswith(".gov.by"):
        return 70
    if hostname.endswith(".edu") or hostname.endswith(".ac.uk"):
        return 55
    return 5 if parsed.scheme == "https" else 0


def _prioritize_sources(results: list[SearchResult]) -> list[SearchResult]:
    """Ставит проверяемые домены выше случайных, сохраняя все кандидаты."""
    return sorted(
        results,
        key=lambda result: (-source_reliability_score(result.url), result.url),
    )


def _weather_location(query: str) -> str | None:
    """Извлекает название места из нескольких естественных форм вопроса."""
    for pattern in WEATHER_LOCATION_PATTERNS:
        match = pattern.search(query)
        if match is None:
            continue
        location = " ".join(match.group("location").split()).strip(" ,")
        if 2 <= len(location) <= 80:
            return location
    return None


def weather_location_clarification(
    query: str,
    *,
    location_hint: str | None = None,
    coordinates_hint: tuple[float, float] | None = None,
) -> str | None:
    """Просит место до weather route, когда его нет ни в вопросе, ни в чате."""

    clean_hint = " ".join((location_hint or "").split()).strip(" ,")
    if (
        is_weather_question(query)
        and _weather_location(query) is None
        and not 2 <= len(clean_hint) <= 80
        and _valid_coordinate_pair(coordinates_hint) is None
    ):
        return WEATHER_LOCATION_CLARIFICATION
    return None


def _weather_location_reply(prompt: str) -> str | None:
    """Проверяет короткий ответ пользователя на локальное уточнение о погоде."""

    clean_prompt = " ".join(prompt.split()).strip(" .,!…")
    if "?" in clean_prompt:
        return None
    clean_prompt = re.sub(
        r"^(?:в|во|город(?:е)?|селе|пос[её]лке)\s+",
        "",
        clean_prompt,
        flags=re.IGNORECASE,
    ).strip(" ,")
    if clean_prompt.casefold() in {"моем городе", "моём городе", "здесь", "там"}:
        return None
    if not 2 <= len(clean_prompt) <= 80:
        return None
    if re.fullmatch(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё .,'-]{0,78}", clean_prompt) is None:
        return None
    return clean_prompt


def weather_location_followup(
    prompt: str,
    previous_messages: list[dict[str, object]],
) -> tuple[str, str] | None:
    """Возвращает исходный weather-вопрос и место после точного уточнения.

    Работа ограничена последней локальной репликой ассистента. Поэтому новое
    название города не может внезапно оживить давний вопрос из другого места
    разговора.
    """

    location_reply = _weather_location_reply(prompt)
    if location_reply is None or not previous_messages:
        return None
    last_message = previous_messages[-1]
    last_content = last_message.get("content")
    if (
        last_message.get("role") != "assistant"
        or not isinstance(last_content, str)
        or " ".join(last_content.casefold().split())
        != " ".join(WEATHER_LOCATION_CLARIFICATION.casefold().split())
    ):
        return None
    for message in reversed(previous_messages[:-1]):
        content = message.get("content")
        if (
            message.get("role") == "user"
            and isinstance(content, str)
            and is_weather_question(content)
            and _weather_location(content) is None
        ):
            return content.strip(), location_reply
    return None


def _contains_weather_marker(text: str, markers: tuple[str, ...]) -> bool:
    """Проверяет корни слов после единой нормализации запроса."""
    return any(marker in text for marker in markers)


def _has_non_meteorological_context(clean_query: str) -> bool:
    """Отсекает известные значения слов, не относящиеся к прогнозу."""
    return _contains_weather_marker(clean_query, WEATHER_NON_METEOROLOGICAL_MARKERS)


def _has_weather_time_context(clean_query: str) -> bool:
    """Находит время, которое делает неоднозначный вопрос прогнозным."""
    return _contains_weather_marker(clean_query, WEATHER_TIME_MARKERS)


def is_weather_question(query: str) -> bool:
    """Определяет именно метеорологический вопрос, а не совпадение слов.

    Явления вроде дождя и ветра однозначны. Температура, одежда, холод и
    солнечный свет требуют дополнительного сигнала: места, времени, улицы
    или устойчивой вопросительной формы. Так обычный диалог не зацикливается
    на погодном маршруте.
    """
    clean_query = " ".join(query.casefold().split())
    if not clean_query or _has_non_meteorological_context(clean_query):
        return False
    if _contains_weather_marker(clean_query, WEATHER_CORE_MARKERS):
        return True

    has_time = _has_weather_time_context(clean_query)
    has_location = _weather_location(query) is not None
    mentions_outdoors = "на улице" in clean_query or "на улиц" in clean_query

    if _contains_weather_marker(clean_query, WEATHER_MEASUREMENT_MARKERS):
        return has_time or has_location or mentions_outdoors
    if _contains_weather_marker(clean_query, WEATHER_ASTRONOMY_MARKERS):
        return has_time or has_location
    if WEATHER_CLOTHING_PATTERN.search(clean_query) is not None:
        return has_location or has_time
    if _contains_weather_marker(clean_query, WEATHER_CONDITION_MARKERS):
        if mentions_outdoors or (has_location and has_time):
            return True
        return bool(
            has_time
            and re.search(
                r"(?:^|\s)(?:будет|какая|какой|как)(?:$|\s)",
                clean_query,
                flags=re.IGNORECASE,
            )
        )
    return False


def weather_day_from_query(query: str) -> str | None:
    """Определяет день прогноза из обычной русской формулировки."""
    clean_query = query.casefold()
    # «Послезавтра» содержит подстроку «завтра», поэтому более дальний день
    # проверяется первым.
    if "послезавтра" in clean_query:
        return "day_after_tomorrow"
    if "завтра" in clean_query:
        return "tomorrow"
    if "сегодня" in clean_query:
        return "today"
    if "сейчас" in clean_query:
        return "current"
    return None


def _latest_user_message(messages: list[dict[str, object]]) -> str | None:
    """Возвращает последнюю содержательную реплику пользователя из истории."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None


def weather_question_from_messages(messages: list[dict[str, object]]) -> str | None:
    """Возвращает последний явный weather-вопрос только из текущего чата.

    Подсказка существует лишь во время обработки следующего сообщения. Она не
    становится постоянной памятью, не отправляется в общий поиск и нужна для
    коротких уточнений вроде «и напиши только время».
    """
    content = _latest_user_message(messages)
    if content is None or not is_weather_question(content):
        return None
    return content


def weather_day_from_messages(messages: list[dict[str, object]]) -> str | None:
    """Берёт день только из актуальной последней погодной реплики пользователя."""
    content = weather_question_from_messages(messages)
    return weather_day_from_query(content) if content is not None else None


def is_weather_followup(query: str, has_weather_context: bool) -> bool:
    """Распознаёт только короткое продолжение уже начатого прогноза.

    История с погодой не делает погодными вопросы «во сколько концерт?» или
    «вечером пойду в кино»: время без самостоятельного погодного слова должно
    быть всей короткой репликой пользователя.
    """
    if not has_weather_context or is_weather_question(query):
        return False
    clean_query = " ".join(query.casefold().split())
    if not clean_query or _has_non_meteorological_context(clean_query):
        return False
    if _contains_weather_marker(
        clean_query,
        WEATHER_CORE_MARKERS
        + WEATHER_MEASUREMENT_MARKERS
        + WEATHER_ASTRONOMY_MARKERS,
    ):
        return True
    return WEATHER_SHORT_FOLLOWUP_PATTERN.fullmatch(clean_query) is not None


def is_weather_compact_time_followup(query: str, has_weather_context: bool) -> bool:
    """Распознаёт просьбу оставить только время после weather-вопроса.

    Такая фраза не должна случайно включать прогноз в новой теме, поэтому
    проверяется только при явном погодном вопросе в текущем диалоге.
    """
    if not has_weather_context or is_weather_question(query):
        return False
    clean_query = " ".join(query.casefold().split())
    if not clean_query or _has_non_meteorological_context(clean_query):
        return False
    return WEATHER_COMPACT_TIME_FOLLOWUP_PATTERN.fullmatch(clean_query) is not None


def weather_location_from_messages(messages: list[dict[str, object]]) -> str | None:
    """Берёт последнее явно названное место только из реплик пользователя.

    Ассистент не является источником факта о местоположении. Поэтому мы не
    извлекаем город из его прошлых ответов и не рискуем подхватить выдуманное
    название. Поиск ограничен текущим локальным диалогом, который уже хранится
    в SQLite и не отправляется для этого в интернет.
    """
    content = weather_question_from_messages(messages)
    return _weather_location(content) if content is not None else None


def _normalise_location_word(word: str) -> str:
    """Даёт безопасный именительный вариант частой русской словоформы.

    Геокодер ожидает название из справочника, а человек обычно пишет его в
    предложном падеже: «в Старом Осколе». Исходная фраза всегда остаётся
    первым вариантом, поэтому преобразование лишь помогает, а не подменяет
    написание пользователя.
    """
    lowered = word.casefold()
    if lowered.endswith("скве"):
        return word[:-2] + "ва"  # «Москве» → «Москва».
    if lowered.endswith("ом") and len(word) > 4:
        return word[:-2] + "ый"  # «Старом» → «Старый».
    if lowered.endswith("ое") and len(word) > 4:
        return word[:-2] + "ой"  # «Уренгое» → «Уренгой».
    if lowered.endswith("е") and len(word) > 3:
        return word[:-1]  # «Минске» → «Минск».
    return word


def _geocoding_location_candidates(location: str) -> tuple[str, ...]:
    """Добавляет безопасные варианты названия для русского предложного падежа."""
    candidates = [location]
    words = location.split()
    # Для однословного города форма «в Калуге» часто не находится API напрямую,
    # хотя «Калуга» находится. Исходная форма остаётся первой, а этот вариант
    # лишь даёт геокодеру дополнительную попытку без подмены текста в чате.
    if len(words) == 1 and len(words[0]) > 3 and words[0].casefold().endswith("е"):
        candidates.append(words[0][:-1] + "а")
    normalised_location = " ".join(_normalise_location_word(word) for word in words)
    if normalised_location:
        candidates.append(normalised_location)
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def _format_measurement(value: object, suffix: str) -> str | None:
    """Безопасно оформляет число из структурированного погодного ответа."""
    if not isinstance(value, int | float):
        return None
    numeric = int(value) if float(value).is_integer() else round(float(value), 1)
    separator = "" if suffix == "%" else " "
    return f"{numeric}{separator}{suffix}"


def _finite_number(value: object) -> float | None:
    """Принимает только конечное числовое значение из JSON прогноза."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    return numeric if isfinite(numeric) else None


def _clothing_guidance(
    minimum_temperature: object,
    maximum_temperature: object,
    precipitation_probability: object,
    condition: str,
    wind_speed: object,
) -> str | None:
    """Даёт краткую практическую рекомендацию только по данным прогноза.

    Это не медицинский совет и не попытка угадать личный стиль человека.
    Используются лишь температура, осадки и ветер, необходимые для вопроса
    «что надеть» или «нужен ли зонт».
    """
    minimum = _finite_number(minimum_temperature)
    maximum = _finite_number(maximum_temperature)
    reference_temperature = minimum if minimum is not None else maximum
    if reference_temperature is None:
        return None

    if reference_temperature <= -5:
        guidance = "подойдёт тёплая зимняя куртка, шапка и перчатки"
    elif reference_temperature <= 5:
        guidance = "лучше выбрать тёплую куртку и закрытую обувь"
    elif reference_temperature <= 13:
        guidance = "подойдут лёгкая куртка или ветровка и закрытая обувь"
    elif reference_temperature <= 20:
        guidance = "подойдёт лёгкий верх, который можно снять днём"
    else:
        guidance = "подойдёт лёгкая одежда; на солнце пригодится головной убор"

    probability = _finite_number(precipitation_probability)
    normalised_condition = condition.casefold()
    has_precipitation = (
        (probability is not None and probability >= 35)
        or any(marker in normalised_condition for marker in ("дожд", "снег", "гроз", "ливн", "морось"))
    )
    if has_precipitation:
        guidance += "; возьми зонт или непромокаемый верх"

    wind = _finite_number(wind_speed)
    if wind is not None and wind >= 30:
        guidance += "; учти сильный ветер"
    return guidance


def _weather_focus(query: str) -> str:
    """Выбирает один показатель, если пользователь спросил именно о нём.

    Обычный вопрос «какая погода» остаётся краткой сводкой. Узкий вопрос не
    получает лишние температуру, ветер и ссылки: это делает ответ быстрее для
    чтения и не выдаёт случайный набор деталей за нужный факт.
    """
    clean_query = query.casefold()
    asks_for_time_range = any(
        marker in clean_query for marker in HOURLY_PRECIPITATION_MARKERS
    )
    asks_for_precipitation = any(
        marker in clean_query for marker in ("дожд", "снег", "ливн", "осад")
    )
    if asks_for_time_range and asks_for_precipitation:
        return "precipitation_time"
    if any(marker in clean_query for marker in ("восход", "закат", "рассвет")):
        return "sun"
    if "ветер" in clean_query:
        return "wind"
    if "влажност" in clean_query:
        return "humidity"
    if "давлен" in clean_query:
        return "pressure"
    if "видимост" in clean_query:
        return "visibility"
    if any(marker in clean_query for marker in ("температур", "градус", "ощуща")):
        return "temperature"
    if any(marker in clean_query for marker in ("одеж", "одет", "надет")):
        return "clothing"
    if any(marker in clean_query for marker in ("дожд", "снег", "ливн", "осад", "зонт")):
        return "precipitation"
    return "summary"


def _weather_requests_time_only(query: str) -> bool:
    """Проверяет явную просьбу показать только значение времени.

    Обычный вопрос о восходе или закате получает короткую понятную фразу с
    городом. Голое значение разрешено только когда пользователь явно сузил
    формат ответа, чтобы не сделать обычный диалог слишком телеграфным.
    """
    return WEATHER_TIME_ONLY_REQUEST_PATTERN.search(query) is not None


def _sun_times_for_requested_event(
    query: str,
    sunrise: object,
    sunset: object,
) -> list[str]:
    """Возвращает только запрошенные значения восхода/заката из live JSON."""
    clean_query = query.casefold()
    asks_sunrise = "восход" in clean_query or "рассвет" in clean_query
    asks_sunset = "закат" in clean_query

    def extract_time(value: object) -> str | None:
        if isinstance(value, str) and len(value) >= 16:
            return value[11:16]
        return None

    values: list[str] = []
    if asks_sunrise and not asks_sunset:
        candidate = extract_time(sunrise)
        if candidate is not None:
            values.append(candidate)
        return values
    if asks_sunset and not asks_sunrise:
        candidate = extract_time(sunset)
        if candidate is not None:
            values.append(candidate)
        return values
    for candidate_value in (sunrise, sunset):
        candidate = extract_time(candidate_value)
        if candidate is not None:
            values.append(candidate)
    return values


def _valid_coordinate_pair(
    coordinates_hint: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """Принимает только координаты Земли из текущего запроса браузера."""
    if coordinates_hint is None or len(coordinates_hint) != 2:
        return None
    latitude, longitude = coordinates_hint
    if (
        isinstance(latitude, bool)
        or isinstance(longitude, bool)
        or not isinstance(latitude, int | float)
        or not isinstance(longitude, int | float)
        or not -90 <= float(latitude) <= 90
        or not -180 <= float(longitude) <= 180
    ):
        return None
    return float(latitude), float(longitude)


def _weather_location_phrase(place_name: str, from_browser_location: bool) -> str:
    """Оформляет место без раскрытия координат пользователя в сообщении."""
    return "по вашей геопозиции" if from_browser_location else f"в {place_name}"


def _list_value(values: object, index: int) -> object | None:
    """Безопасно достаёт элемент прогноза нужного дня."""
    if isinstance(values, list) and 0 <= index < len(values):
        return values[index]
    return None


def _hourly_precipitation_period(
    query: str,
    hourly: object,
    target_date: str,
    location_phrase: str,
    day_name: str,
) -> str | None:
    """Отвечает на вопрос, когда именно ожидаются дождь, снег или осадки."""
    clean_query = query.casefold()
    asks_for_time_range = any(
        marker in clean_query for marker in HOURLY_PRECIPITATION_MARKERS
    )
    asks_for_precipitation = any(
        marker in clean_query for marker in ("дожд", "снег", "ливн", "осад")
    )
    if not asks_for_time_range or not asks_for_precipitation:
        return None
    if not isinstance(hourly, dict):
        return None

    times = hourly.get("time")
    weather_codes = hourly.get("weather_code")
    precipitations = hourly.get("precipitation")
    if not isinstance(times, list) or not isinstance(weather_codes, list):
        return None
    if len(times) != len(weather_codes):
        return None

    if "снег" in clean_query:
        phenomenon = "Снег"
        relevant_codes = SNOW_WEATHER_CODES
    elif any(marker in clean_query for marker in ("дожд", "ливн")):
        phenomenon = "Дождь"
        relevant_codes = RAIN_WEATHER_CODES
    else:
        phenomenon = "Осадки"
        relevant_codes = RAIN_WEATHER_CODES | SNOW_WEATHER_CODES

    periods: list[tuple[str, str]] = []
    active_from: str | None = None
    for index, timestamp in enumerate(times):
        if not isinstance(timestamp, str) or not timestamp.startswith(target_date + "T"):
            continue
        weather_code = weather_codes[index]
        precipitation = (
            precipitations[index]
            if isinstance(precipitations, list) and index < len(precipitations)
            else 0
        )
        has_phenomenon = weather_code in relevant_codes or (
            phenomenon == "Осадки"
            and isinstance(precipitation, int | float)
            and precipitation > 0
        )
        time_text = timestamp[11:16]
        if has_phenomenon and active_from is None:
            active_from = time_text
        elif not has_phenomenon and active_from is not None:
            periods.append((active_from, time_text))
            active_from = None
    if active_from is not None:
        periods.append((active_from, "24:00"))

    if not periods:
        return (
            f"{day_name} {location_phrase}: по часовому прогнозу "
            f"{phenomenon.casefold()} не ожидается."
        )
    period_text = ", затем ".join(
        f"с {start} до {end}" for start, end in periods[:3]
    )
    return f"{day_name} {location_phrase}: {phenomenon.casefold()} ожидается {period_text}."


def _hourly_measurement_for_day(
    hourly: object,
    target_date: str,
    field_name: str,
    suffix: str,
    label: str,
    day_name: str,
    location_phrase: str,
) -> str | None:
    """Берёт один полуденный прогнозный показатель для узкого вопроса.

    Для давления, влажности и видимости суточный максимум был бы вводящим в
    заблуждение. Поэтому для будущего дня выбирается значение около 12:00, а
    если его нет — ближайшее доступное значение этого дня с указанным временем.
    """
    if not isinstance(hourly, dict):
        return None
    times = hourly.get("time")
    values = hourly.get(field_name)
    if not isinstance(times, list) or not isinstance(values, list):
        return None
    candidates = [
        index
        for index, timestamp in enumerate(times)
        if isinstance(timestamp, str)
        and timestamp.startswith(target_date + "T")
        and index < len(values)
    ]
    if not candidates:
        return None
    selected_index = next(
        (index for index in candidates if isinstance(times[index], str) and times[index].endswith("T12:00")),
        candidates[len(candidates) // 2],
    )
    measurement = _format_measurement(values[selected_index], suffix)
    timestamp = times[selected_index]
    if not measurement or not isinstance(timestamp, str):
        return None
    return (
        f"{day_name} {location_phrase} около {timestamp[11:16]}: "
        f"{label} {measurement}."
    )


def _extract_result_url(href: str | None) -> str | None:
    """Извлекает настоящий HTTP(S)-адрес из ссылки поискового сервера."""
    if not href:
        return None
    candidate = href
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    parsed = urlparse(candidate)
    if parsed.netloc.endswith("duckduckgo.com"):
        redirected_url = parse_qs(parsed.query).get("uddg", [None])[0]
        if redirected_url:
            candidate = redirected_url
            parsed = urlparse(candidate)
    if parsed.netloc.endswith("bing.com"):
        encoded_url = parse_qs(parsed.query).get("u", [None])[0]
        if encoded_url and encoded_url.startswith("a1"):
            try:
                padding = "=" * (-len(encoded_url[2:]) % 4)
                decoded_url = base64.urlsafe_b64decode(
                    encoded_url[2:] + padding
                ).decode("utf-8")
            except (binascii.Error, UnicodeError, ValueError):
                decoded_url = ""
            if decoded_url:
                candidate = decoded_url
                parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


class _DuckDuckGoParser(HTMLParser):
    """Извлекает только заголовки, адреса и сниппеты результатов."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._current_url: str | None = None
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []
        self._inside_title = False
        self._inside_snippet = False

    @staticmethod
    def _classes(attributes: list[tuple[str, str | None]]) -> set[str]:
        values = dict(attributes).get("class") or ""
        return set(values.split())

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        classes = self._classes(attrs)
        if tag == "a" and classes.intersection({"result__a", "result-link"}):
            self._current_url = _extract_result_url(dict(attrs).get("href"))
            self._title_parts = []
            self._snippet_parts = []
            self._inside_title = True
        elif (
            tag in {"a", "td", "div"}
            and classes.intersection({"result__snippet", "result-snippet"})
            and self._current_url
        ):
            self._inside_snippet = True

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self._title_parts.append(data)
        if self._inside_snippet:
            self._snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._inside_title and tag == "a":
            self._inside_title = False
        elif self._inside_snippet and tag in {"a", "td", "div"}:
            self._inside_snippet = False
            if self._current_url:
                title = _clean_text("".join(self._title_parts))
                snippet = _clean_text("".join(self._snippet_parts))
                if title:
                    self.results.append(
                        SearchResult(
                            title=title,
                            url=self._current_url,
                            snippet=snippet,
                        )
                    )
            self._current_url = None
            self._title_parts = []
            self._snippet_parts = []


def parse_search_results(document: str) -> list[SearchResult]:
    """Разбирает HTML поисковой страницы без подключения готовых моделей."""
    parser = _DuckDuckGoParser()
    try:
        parser.feed(document)
        parser.close()
    except (RuntimeError, ValueError) as error:
        raise InternetSearchError("Не удалось разобрать результаты поиска.") from error
    return parser.results


class _BingParser(HTMLParser):
    """Извлекает результаты из резервной HTML-разметки Bing."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._inside_result = False
        self._inside_title = False
        self._inside_snippet = False
        self._current_url: str | None = None
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []

    @staticmethod
    def _classes(attributes: list[tuple[str, str | None]]) -> set[str]:
        values = dict(attributes).get("class") or ""
        return set(values.split())

    def _finish_result(self) -> None:
        """Сохраняет текущий результат и очищает состояние parser."""
        if self._current_url:
            title = _clean_text("".join(self._title_parts))
            snippet = _clean_text("".join(self._snippet_parts))
            if title:
                self.results.append(
                    SearchResult(
                        title=title,
                        url=self._current_url,
                        snippet=snippet,
                    )
                )
        self._inside_result = False
        self._inside_title = False
        self._inside_snippet = False
        self._current_url = None
        self._title_parts = []
        self._snippet_parts = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        classes = self._classes(attrs)
        if tag == "li" and "b_algo" in classes:
            self._finish_result()
            self._inside_result = True
        elif self._inside_result and tag == "h2":
            self._inside_title = True
        elif self._inside_result and tag == "a" and self._inside_title:
            self._current_url = _extract_result_url(dict(attrs).get("href"))
        elif self._inside_result and tag == "p":
            self._inside_snippet = True

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self._title_parts.append(data)
        if self._inside_snippet:
            self._snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._inside_title and tag == "h2":
            self._inside_title = False
        elif self._inside_snippet and tag == "p":
            self._inside_snippet = False
        elif self._inside_result and tag == "li":
            self._finish_result()


def parse_bing_search_results(document: str) -> list[SearchResult]:
    """Разбирает резервную HTML-страницу Bing без выполнения её JavaScript."""
    parser = _BingParser()
    try:
        parser.feed(document)
        parser.close()
    except (RuntimeError, ValueError) as error:
        raise InternetSearchError("Не удалось разобрать резервные результаты поиска.") from error
    return parser.results


class _ReadableTextParser(HTMLParser):
    """Извлекает читаемый текст страницы, не выполняя её JavaScript."""

    BLOCK_TAGS = frozenset({"p", "li", "h1", "h2", "h3", "h4", "article", "section", "br"})
    IGNORED_TAGS = frozenset({"script", "style", "noscript", "svg", "canvas", "iframe"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag in self.IGNORED_TAGS:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def extract_readable_text(document: str) -> str:
    """Получает текст для поиска фактов, не доверяя странице как инструкции."""
    parser = _ReadableTextParser()
    try:
        parser.feed(document)
        parser.close()
    except (RuntimeError, ValueError) as error:
        raise InternetSearchError("Не удалось прочитать текст открытой страницы.") from error
    return "\n".join(
        line
        for line in (_clean_text(part) for part in "".join(parser.parts).splitlines())
        if line
    )


def _best_excerpt(
    query: str,
    document: str,
    fallback: str,
    source_title: str,
) -> tuple[str, int]:
    """Находит наиболее связанную с вопросом фразу, а не определение из поиска."""
    query_terms = _important_terms(query)
    query_roots = _term_roots(query_terms)
    title_terms = _important_terms(source_title)
    sentences = [
        _clean_text(sentence)
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", document)
        if len(_clean_text(sentence)) >= 35
    ]
    scored: list[tuple[int, int, str]] = []
    for position, sentence in enumerate(sentences):
        sentence_terms = _important_terms(sentence)
        shared_terms = query_terms & sentence_terms
        shared_roots = query_roots & _term_roots(sentence_terms)
        if shared_roots:
            # Фразы с несколькими словами из вопроса важнее первого абзаца.
            # Одно общее слово без измеряемого факта часто оказывается шумом
            # («город», «компания», «история»). Поэтому один точный термин
            # даёт только 3 балла и не проходит quality gate сам по себе;
            # два смысловых совпадения либо одно совпадение с числом уже могут
            # стать основанием для короткой выдержки.
            score = len(shared_terms) * 2 + len(shared_roots)
            # Число, дата или процент часто отличают факт («19 °C», «10:30»)
            # от заголовка страницы. Это только приоритет выдержки, не вывод.
            if any(character.isdigit() for character in sentence):
                score += 2
            # Заголовок из поисковой выдачи не является фактом. Убираем его
            # из кандидатов, если фраза почти полностью повторяет заголовок и
            # не содержит измеряемой детали, даты или другого значения.
            if (
                sentence_terms
                and not any(character.isdigit() for character in sentence)
                and len(sentence_terms & title_terms) / len(sentence_terms) >= 0.7
            ):
                score -= 20
            if any(
                marker in sentence.casefold()
                for marker in (
                    "исключительно для",
                    "политик конфиденциальности",
                    "cookie",
                    "условия использования",
                    "авторские права",
                )
            ):
                score -= 20
            scored.append((score, -position, sentence))
    if scored:
        best_score, _, best = max(scored)
        return best[:500].rstrip(), best_score
    return fallback[:500].rstrip() or "На странице нет короткого описания.", 0


def format_grounded_web_answer(
    query: str,
    results: list[SearchResult],
    page_texts: dict[str, str],
    show_sources: bool = False,
) -> str:
    """Формирует ответ из выдержек, добавляя URL только по прямой просьбе."""
    if not results:
        return "Я не нашёл открытых источников по этому вопросу."

    selected_results = _prioritize_sources(results)[:3]
    evidence = [
        (
            *_best_excerpt(
                query,
                page_texts.get(result.url, ""),
                result.snippet,
                result.title,
            ),
            result,
        )
        for result in selected_results
    ]
    evidence.sort(
        key=lambda item: (
            -item[1],
            -source_reliability_score(item[2].url),
            item[2].url,
        )
    )
    grounded_evidence = [
        item for item in evidence if item[1] >= MIN_GROUNDED_EVIDENCE_SCORE
    ]
    if not grounded_evidence:
        if not show_sources:
            return (
                "Я не смог получить проверяемые детали для краткого ответа. "
                "Можете уточнить вопрос или попросить ссылки на найденные ресурсы."
            )
        lines = [
            "Я не смог получить проверяемые детали для краткого ответа.",
            "Запрошенные источники:",
        ]
        lines.extend(
            f"{index}. {result.title}\n{result.url}"
            for index, result in enumerate(selected_results, start=1)
        )
        return "\n".join(lines)

    lines: list[str] = []
    answer_limit = 2 if show_sources else 1
    selected_evidence = grounded_evidence[:answer_limit]
    for index, (excerpt, _, result) in enumerate(selected_evidence, start=1):
        prefix = f"{index}. " if len(selected_evidence) > 1 else ""
        lines.append(prefix + excerpt)
        if show_sources:
            lines.append(f"Источник: {result.title}\n{result.url}")
    return "\n".join(lines)


class InternetSearchClient:
    """Выполняет только поисковые HTTP-запросы с лимитом и таймаутом."""

    SEARCH_PROVIDERS = (
        ("duckduckgo-lite", "https://lite.duckduckgo.com/lite/?q={}", parse_search_results),
        ("bing", "https://www.bing.com/search?q={}", parse_bing_search_results),
    )
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AiLocalPrototype/0.1"
    MAX_RESPONSE_BYTES = 1_000_000

    def __init__(self, timeout_seconds: float = 8.0, max_results: int = 5) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Таймаут должен быть положительным.")
        if max_results < 1 or max_results > 10:
            raise ValueError("Число результатов должно быть от 1 до 10.")
        self.timeout_seconds = timeout_seconds
        self.max_results = max_results

    def _fetch_document(
        self,
        url: str,
        max_response_bytes: int | None = None,
    ) -> str:
        """Получает HTML с ограничением размера и понятной ошибкой сети."""
        response_limit = max_response_bytes or self.MAX_RESPONSE_BYTES
        if response_limit < 1 or response_limit > self.MAX_RESPONSE_BYTES:
            raise ValueError("Размер ответа страницы выходит за безопасный лимит.")
        request = Request(
            url,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept-Language": "ru,en;q=0.8",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_document = response.read(response_limit)
                encoding = response.headers.get_content_charset() or "utf-8"
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise InternetSearchError("Интернет-поиск временно недоступен.") from error

        try:
            return raw_document.decode(encoding, errors="replace")
        except (LookupError, UnicodeError) as error:
            raise InternetSearchError("Не удалось прочитать ответ поискового сервера.") from error

    def search(
        self,
        query: str,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Возвращает ограниченный список результатов по текстовому запросу."""
        clean_query = " ".join(query.split()) if isinstance(query, str) else ""
        if not 2 <= len(clean_query) <= 300:
            raise ValueError("Поисковый запрос должен содержать от 2 до 300 символов.")
        result_limit = self.max_results if max_results is None else max_results
        if result_limit < 1 or result_limit > self.max_results:
            raise ValueError("Запрошенное число результатов превышает разрешённый лимит.")

        last_error: InternetSearchError | None = None
        successful_response = False
        for provider_name, url_template, parser in self.SEARCH_PROVIDERS:
            try:
                document = self._fetch_document(
                    url_template.format(quote_plus(clean_query))
                )
                successful_response = True
                results = parser(document)
            except InternetSearchError as error:
                last_error = error
                continue
            if results:
                unique_results: list[SearchResult] = []
                known_urls: set[str] = set()
                for result in results:
                    if result.url in known_urls:
                        continue
                    known_urls.add(result.url)
                    unique_results.append(result)
                # Сначала читаем известные официальные и профильные домены.
                # Остальные результаты не скрываются: при отсутствии подробного
                # материала на приоритетной странице они остаются запасным
                # кандидатом, но не перехватывают ответ рекламным заголовком.
                return _prioritize_sources(unique_results)[:result_limit]
            # Пустой ответ одного провайдера не считается окончательной
            # неудачей: другой провайдер может вернуть полезные результаты.

        if successful_response:
            return []
        if last_error is not None:
            raise last_error
        raise InternetSearchError(
            "Не удалось выбрать провайдера интернет-поиска."
        )

    def _fetch_weather_json(self, url: str) -> dict[str, object]:
        """Читает структурированный прогноз с одной повторной попыткой.

        Погодный API иногда кратковременно закрывает одно соединение. Повтор
        выполняется только для небольшого JSON-ответа и не запускает общий
        поиск, поэтому не создаёт случайных ссылок и не обходит сайты.
        """
        last_error: Exception | None = None
        for _ in range(2):
            try:
                payload = json.loads(
                    self._fetch_document(url, max_response_bytes=40_000)
                )
                if isinstance(payload, dict):
                    return payload
                raise ValueError("Структурированный погодный ответ имеет неверный формат.")
            except (InternetSearchError, json.JSONDecodeError, TypeError, ValueError) as error:
                last_error = error
        raise InternetSearchError("Структурированный погодный прогноз временно недоступен.") from last_error

    def _weather_answer(
        self,
        query: str,
        coordinates_hint: tuple[float, float] | None = None,
    ) -> WebAnswer | None:
        """Берёт погоду из структурированного открытого источника без HTML.

        Погодные страницы часто строятся JavaScript-ом и отдают поиску только
        заголовок. Для прямого вопроса о погоде используется публичный API с
        числами, поэтому чат не подменяет температуру названием страницы.
        """
        location = _weather_location(query)
        coordinates = _valid_coordinate_pair(coordinates_hint)
        if location is None and coordinates is None:
            return None
        from_browser_location = location is None and coordinates is not None
        try:
            if coordinates is None:
                place = None
                for location_candidate in _geocoding_location_candidates(location or ""):
                    geocoding_url = (
                        "https://geocoding-api.open-meteo.com/v1/search?name="
                        f"{quote_plus(location_candidate)}&count=1&language=ru&format=json"
                    )
                    geocoding_payload = self._fetch_weather_json(geocoding_url)
                    places = geocoding_payload.get("results")
                    if isinstance(places, list) and places:
                        place = places[0]
                        break
                if not isinstance(place, dict):
                    return None
                latitude = place.get("latitude")
                longitude = place.get("longitude")
                if (
                    isinstance(latitude, bool)
                    or isinstance(longitude, bool)
                    or not isinstance(latitude, int | float)
                    or not isinstance(longitude, int | float)
                ):
                    return None
            else:
                latitude, longitude = coordinates
            weather_url = (
                "https://api.open-meteo.com/v1/forecast?"
                f"latitude={latitude}&longitude={longitude}"
                "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
                "precipitation,weather_code,wind_speed_10m,pressure_msl,visibility"
                "&daily=temperature_2m_max,temperature_2m_min,weather_code,"
                "precipitation_probability_max,wind_speed_10m_max,sunrise,sunset"
                "&hourly=precipitation,weather_code,relative_humidity_2m,pressure_msl,visibility"
                "&timezone=auto"
            )
            weather_payload = self._fetch_weather_json(weather_url)
        except (InternetSearchError, json.JSONDecodeError, TypeError, ValueError):
            return None

        # Для ответа сохраняем форму названия из реплики пользователя:
        # «в Старом Осколе» звучит естественно, а справочное имя геокодера
        # «Старый Оскол» нужно только для поиска координат.
        place_name = location or ""
        location_phrase = _weather_location_phrase(place_name, from_browser_location)
        requested_day = weather_day_from_query(query)
        forecast_day = requested_day or "today"
        day_index = {
            "current": 0,
            "today": 0,
            "tomorrow": 1,
            "day_after_tomorrow": 2,
        }.get(forecast_day, 0)
        day_name = {
            "today": "Сегодня",
            "tomorrow": "Завтра",
            "day_after_tomorrow": "Послезавтра",
        }.get(forecast_day, "Сегодня")
        future_forecast = forecast_day in {"tomorrow", "day_after_tomorrow"}
        focus = _weather_focus(query)
        time_only_requested = focus == "sun" and _weather_requests_time_only(query)
        # «Какая температура в Минске?» обычно означает измерение сейчас, а
        # «Какая температура завтра?» — диапазон прогноза. Не подменяем один
        # смысл другим только потому, что пользователь не написал «сейчас».
        prefer_current_measurement = (
            requested_day is None
            and focus in {"temperature", "wind", "humidity", "pressure", "visibility"}
        )
        daily = weather_payload.get("daily")
        daily_line: str | None = None
        hourly_line: str | None = None
        target_date: str | None = None
        if isinstance(daily, dict):
            maximum_value = _list_value(daily.get("temperature_2m_max"), day_index)
            minimum_value = _list_value(daily.get("temperature_2m_min"), day_index)
            maximum = _format_measurement(
                maximum_value,
                "°C",
            )
            minimum = _format_measurement(
                minimum_value,
                "°C",
            )
            daily_condition = WEATHER_CODES.get(
                _list_value(daily.get("weather_code"), day_index),
                "состояние не указано",
            )
            probability_value = _list_value(
                daily.get("precipitation_probability_max"),
                day_index,
            )
            probability = (
                "вероятность осадков "
                f"{int(probability_value) if float(probability_value).is_integer() else round(float(probability_value), 1)}%"
                if isinstance(probability_value, int | float) and not isinstance(probability_value, bool)
                else None
            )
            maximum_wind_value = _list_value(daily.get("wind_speed_10m_max"), day_index)
            maximum_wind = _format_measurement(maximum_wind_value, "км/ч")
            sunrise = _list_value(daily.get("sunrise"), day_index)
            sunset = _list_value(daily.get("sunset"), day_index)
            target_date_value = _list_value(daily.get("time"), day_index)
            target_date = target_date_value if isinstance(target_date_value, str) else None

            summary_details = [
                detail
                for detail in (
                    f"от {minimum} до {maximum}" if minimum and maximum else None,
                    probability,
                )
                if detail
            ]
            if focus == "wind":
                selected_details = [f"ветер до {maximum_wind}"] if maximum_wind else []
            elif focus == "temperature":
                selected_details = [
                    f"от {minimum} до {maximum}"
                ] if minimum and maximum else []
            elif focus == "clothing":
                clothing = _clothing_guidance(
                    minimum_value,
                    maximum_value,
                    probability_value,
                    daily_condition,
                    maximum_wind_value,
                )
                selected_details = [
                    detail
                    for detail in (
                        clothing,
                        f"температура от {minimum} до {maximum}"
                        if minimum and maximum
                        else None,
                        probability,
                    )
                    if detail
                ]
            elif focus == "precipitation":
                selected_details = [
                    detail
                    for detail in (
                        daily_condition if daily_condition != "состояние не указано" else None,
                        probability,
                    )
                    if detail
                ]
            elif focus == "sun":
                if time_only_requested:
                    selected_details = _sun_times_for_requested_event(
                        query,
                        sunrise,
                        sunset,
                    )
                else:
                    selected_details = [
                        detail
                        for detail in (
                            f"восход в {sunrise[11:16]}"
                            if isinstance(sunrise, str) and len(sunrise) >= 16
                            else None,
                            f"закат в {sunset[11:16]}"
                            if isinstance(sunset, str) and len(sunset) >= 16
                            else None,
                        )
                        if detail
                    ]
            else:
                selected_details = summary_details
                if daily_condition != "состояние не указано":
                    selected_details = [daily_condition, *selected_details]

            if selected_details:
                if time_only_requested:
                    daily_line = ", ".join(selected_details)
                else:
                    daily_line = (
                        f"{day_name} {location_phrase}: "
                        + "; ".join(selected_details[:1])
                        + (
                            ", " + ", ".join(selected_details[1:])
                            if len(selected_details) > 1
                            else ""
                        )
                        + "."
                    )
            if target_date is not None:
                if focus == "precipitation_time":
                    hourly_line = _hourly_precipitation_period(
                        query,
                        weather_payload.get("hourly"),
                        target_date,
                        location_phrase,
                        day_name,
                    )
                elif focus == "humidity":
                    hourly_line = _hourly_measurement_for_day(
                        weather_payload.get("hourly"),
                        target_date,
                        "relative_humidity_2m",
                        "%",
                        "влажность",
                        day_name,
                        location_phrase,
                    )
                elif focus == "pressure":
                    hourly_line = _hourly_measurement_for_day(
                        weather_payload.get("hourly"),
                        target_date,
                        "pressure_msl",
                        "гПа",
                        "давление",
                        day_name,
                        location_phrase,
                    )
                elif focus == "visibility":
                    hourly_line = _hourly_measurement_for_day(
                        weather_payload.get("hourly"),
                        target_date,
                        "visibility",
                        "м",
                        "видимость",
                        day_name,
                        location_phrase,
                    )

        lines: list[str] = []
        if hourly_line is not None:
            lines.append(hourly_line)
        if future_forecast:
            # Текущая температура не отвечает на вопрос о будущем дне и не
            # должна маскироваться под прогноз.
            if not lines:
                if daily_line is None:
                    return None
                lines.append(daily_line)
        elif focus == "sun" and daily_line is not None:
            lines.append(daily_line)
        elif (
            focus in {"humidity", "pressure", "visibility", "summary", "clothing"}
            or forecast_day == "current"
            or prefer_current_measurement
        ):
            current = weather_payload.get("current")
            if not isinstance(current, dict):
                return None
            temperature = _format_measurement(current.get("temperature_2m"), "°C")
            apparent_temperature = _format_measurement(
                current.get("apparent_temperature"),
                "°C",
            )
            wind = _format_measurement(current.get("wind_speed_10m"), "км/ч")
            precipitation = _format_measurement(current.get("precipitation"), "мм")
            humidity = _format_measurement(current.get("relative_humidity_2m"), "%")
            pressure = _format_measurement(current.get("pressure_msl"), "гПа")
            visibility = _format_measurement(current.get("visibility"), "м")
            condition = WEATHER_CODES.get(
                current.get("weather_code"),
                "состояние не указано",
            )
            summary_details = [
                detail
                for detail in (
                    f"температура {temperature}" if temperature else None,
                    f"ощущается как {apparent_temperature}" if apparent_temperature else None,
                    f"ветер {wind}" if wind else None,
                    f"осадки {precipitation}" if precipitation else None,
                )
                if detail
            ]
            if focus == "humidity":
                details = [f"влажность {humidity}"] if humidity else []
            elif focus == "pressure":
                details = [f"давление {pressure}"] if pressure else []
            elif focus == "visibility":
                details = [f"видимость {visibility}"] if visibility else []
            elif focus == "temperature":
                details = [
                    detail
                    for detail in (
                        f"температура {temperature}" if temperature else None,
                        f"ощущается как {apparent_temperature}"
                        if apparent_temperature
                        else None,
                    )
                    if detail
                ]
            elif focus == "wind":
                details = [f"ветер {wind}"] if wind else []
            elif focus == "clothing":
                clothing = _clothing_guidance(
                    current.get("temperature_2m"),
                    current.get("temperature_2m"),
                    current.get("precipitation"),
                    condition,
                    current.get("wind_speed_10m"),
                )
                details = [
                    detail
                    for detail in (
                        clothing,
                        f"температура {temperature}" if temperature else None,
                    )
                    if detail
                ]
            elif focus == "precipitation":
                details = [f"осадки {precipitation}"] if precipitation else []
            else:
                details = summary_details
            if not details:
                return None
            if hourly_line is None:
                lines.append(
                    f"Сейчас {location_phrase}: {condition}; " + ", ".join(details) + "."
                )
                if focus == "summary" and forecast_day == "today" and daily_line:
                    lines.append(daily_line)
        elif daily_line is not None:
            # Вопрос «будет ли дождь сегодня» — это прогноз на день, а не
            # измерение в текущую минуту.
            lines.append(daily_line)
        if not lines:
            return None
        if should_show_source_links(query):
            lines.extend(("Источник: Open-Meteo", "https://open-meteo.com/"))
        source = SearchResult(
            title="Open-Meteo: структурированный прогноз погоды",
            url="https://open-meteo.com/",
            snippet="Публичный прогноз погоды по географическим координатам.",
        )
        return WebAnswer(text="\n".join(lines), results=(source,))

    def answer(
        self,
        query: str,
        location_hint: str | None = None,
        day_hint: str | None = None,
        force_weather: bool = False,
        coordinates_hint: tuple[float, float] | None = None,
        weather_question_hint: str | None = None,
    ) -> WebAnswer:
        """Ищет источники и возвращает выдержки по вопросу с адресами страниц.

        ``location_hint`` приходит только из ранней реплики этого же диалога.
        ``coordinates_hint`` существует лишь в памяти текущего HTTP-запроса и
        передаётся браузером после разрешения пользователя; оно не сохраняется
        в SQLite и не выводится в ответе.
        ``weather_question_hint`` — только последняя явная weather-реплика из
        текущего чата. Она нужна для короткого follow-up и не является
        долговременной памятью или поисковым запросом.
        """
        weather_location = _weather_location(query)
        coordinates = _valid_coordinate_pair(coordinates_hint)
        clean_weather_question_hint = " ".join(
            (weather_question_hint or "").split()
        )
        use_weather_question_hint = (
            force_weather
            and bool(clean_weather_question_hint)
            and is_weather_question(clean_weather_question_hint)
        )
        if is_weather_question(query) or force_weather:
            if weather_location is None:
                clean_hint = " ".join((location_hint or "").split()).strip(" ,")
                if not 2 <= len(clean_hint) <= 80 and coordinates is None:
                    return WebAnswer(text=WEATHER_LOCATION_CLARIFICATION, results=())
                if clean_hint:
                    if use_weather_question_hint:
                        # Для follow-up сохраняем предмет предыдущего вопроса:
                        # «закат» не превращается в общую «погоду», а новый
                        # запрос может сузить формат до одного значения.
                        weather_query = f"{clean_weather_question_hint} {query}"
                        weather_location = (
                            _weather_location(clean_weather_question_hint)
                            or clean_hint
                        )
                    else:
                        day_word = {
                            "tomorrow": "завтра",
                            "day_after_tomorrow": "послезавтра",
                            "today": "сегодня",
                            "current": "сейчас",
                        }.get(weather_day_from_query(query) or day_hint or "", "")
                        # Обычная погодная фраза сохраняет день и город, а
                        # исходный follow-up добавляется после неё для выбора
                        # часового ответа.
                        weather_query = (
                            f"Какая {day_word} погода в {clean_hint}? {query}"
                            if day_word
                            else f"Какая погода в {clean_hint}? {query}"
                        )
                        weather_location = clean_hint
                else:
                    weather_query = query
            else:
                weather_query = query
            weather_answer = self._weather_answer(weather_query, coordinates)
            if weather_answer is not None:
                return weather_answer
            # Распознанный погодный вопрос не передаём в общий поиск. Иначе
            # временная проблема геокодера снова превратится в нерелевантные
            # рекламные ссылки вместо честного сообщения о сбое прогноза.
            location_description = (
                "по вашей геопозиции"
                if coordinates is not None and weather_location is None
                else f"для «{weather_location or clean_hint}»"
            )
            return WebAnswer(
                text=(
                    f"Не удалось получить структурированный прогноз {location_description}. "
                    "Проверьте написание населённого пункта или повторите вопрос позже."
                ),
                results=(),
            )
        results = self.search(query, max_results=min(3, self.max_results))
        page_texts: dict[str, str] = {}
        # Берём не более двух страниц: этого достаточно для краткого ответа и
        # не превращает один запрос пользователя в неограниченный обход сети.
        for result in results[:2]:
            try:
                document = self._fetch_document(
                    result.url,
                    max_response_bytes=400_000,
                )
                page_texts[result.url] = extract_readable_text(document)
            except InternetSearchError:
                # Сниппет поисковой выдачи остаётся прозрачным запасным
                # вариантом; сбой одной страницы не отменяет весь ответ.
                continue
        return WebAnswer(
            text=format_grounded_web_answer(
                query,
                results,
                page_texts,
                show_sources=should_show_source_links(query),
            ),
            results=tuple(results),
        )


def format_search_results(results: list[SearchResult]) -> str:
    """Формирует безопасный текст для показа в локальном чате."""
    if not results:
        return "Поиск не нашёл результатов по этому запросу."

    lines = ["Результаты поиска:"]
    for index, result in enumerate(results, start=1):
        lines.extend(
            [
                f"{index}. {result.title}",
                result.url,
                result.snippet or "Описание отсутствует.",
            ]
        )
    return "\n".join(lines)
