"""Быстрые локальные ответы о текущем времени и календаре."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LOCAL_TIMEZONE_NAME = "Europe/Minsk"
WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)
MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
MONTHS_NOMINATIVE = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


def _season_name(month: int) -> str:
    """Возвращает время года для номера месяца северного полушария."""
    if month in (12, 1, 2):
        return "зима"
    if month in (3, 4, 5):
        return "весна"
    if month in (6, 7, 8):
        return "лето"
    return "осень"


def _minsk_timezone() -> ZoneInfo | timezone:
    """Возвращает Минск и работает на Windows без установленного ``tzdata``.

    В стандартной Windows-сборке Python база IANA-часовых поясов может не быть
    установлена. Беларусь использует постоянный UTC+3, поэтому резервный
    встроенный объект даёт тот же локальный результат и не требует загрузки
    пакета из интернета.
    """
    try:
        return ZoneInfo(LOCAL_TIMEZONE_NAME)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=3), name=LOCAL_TIMEZONE_NAME)


def describe_current_temporal_context(
    prompt: str,
    now: datetime | None = None,
    timezone_name: str = LOCAL_TIMEZONE_NAME,
) -> str | None:
    """Даёт локальный ответ, если вопрос явно просит текущий календарный факт.

    Время определяется на компьютере, где запущен сервер. Геопозиция браузера
    и внешний сервис не нужны: это быстрее и не запрашивает личные данные.
    Параметр ``now`` нужен только для воспроизводимой автоматической проверки.
    """
    clean_prompt = " ".join(prompt.casefold().split())
    asks_time = any(
        phrase in clean_prompt
        for phrase in (
            "сколько времени",
            "который час",
            "какое сейчас время",
            "время сейчас",
        )
    )
    asks_date = any(
        phrase in clean_prompt
        for phrase in ("какая сегодня дата", "какое сегодня число", "какая дата")
    )
    asks_weekday = any(
        phrase in clean_prompt
        for phrase in ("какой сегодня день", "какой день недели", "какой сейчас день")
    )
    asks_month = any(
        phrase in clean_prompt
        for phrase in ("какой сейчас месяц", "какой месяц сейчас", "какой сегодня месяц")
    ) or (
        "месяц" in clean_prompt
        and "какой" in clean_prompt
        and ("сейчас" in clean_prompt or "сегодня" in clean_prompt)
    )
    asks_season = any(
        phrase in clean_prompt
        for phrase in ("какое сейчас время года", "какое время года", "какой сейчас сезон")
    ) or (
        "время года" in clean_prompt
        and ("какой" in clean_prompt or "какое" in clean_prompt)
    )
    if not any((asks_time, asks_date, asks_weekday, asks_month, asks_season)):
        return None

    local_timezone = (
        _minsk_timezone()
        if timezone_name == LOCAL_TIMEZONE_NAME
        else ZoneInfo(timezone_name)
    )
    local_now = now or datetime.now(local_timezone)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=local_timezone)
    else:
        local_now = local_now.astimezone(local_timezone)

    date_text = (
        f"{local_now.day} {MONTHS_GENITIVE[local_now.month - 1]} {local_now.year} года"
    )
    weekday = WEEKDAYS[local_now.weekday()]
    season = _season_name(local_now.month)
    requested_parts: list[str] = []
    if asks_time:
        requested_parts.append(
            f"Сейчас {local_now:%H:%M} по минскому времени"
        )
    if asks_date:
        requested_parts.append(f"Сегодня {date_text}")
    if asks_weekday:
        requested_parts.append(f"Сегодня {weekday}")
    if asks_month:
        requested_parts.append(f"Сейчас {MONTHS_NOMINATIVE[local_now.month - 1]}")
    if asks_season:
        requested_parts.append(f"Сейчас {season}")
    return ". ".join(requested_parts) + "."
