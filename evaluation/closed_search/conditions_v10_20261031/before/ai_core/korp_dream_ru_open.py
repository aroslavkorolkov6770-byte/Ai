"""Создаёт только ограниченный staging-снимок DReaM-ru-open из Korp API.

Этот модуль намеренно не является универсальным загрузчиком URL. Он принимает
данные только от одного заранее проверенного HTTPS-адреса и только от
``DREAM-RU-OPEN``. Результат попадает исключительно в ``data/brain_v3/staging``:
он не меняет active manifest, не запускает audit как разрешение на обучение и
не влияет на работающий чат. Текст из ответа Korp всегда рассматривается как
данные, а не как инструкции или код.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .brain_v3_corpus import (
    MAX_DOCUMENT_LENGTH,
    NearDuplicateIndex,
    content_sha256,
    duplicate_signature,
    file_sha256,
    text_risk_code,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STAGING_ROOT = ROOT / "data" / "brain_v3" / "staging"

# Внешняя граница источника. Их нельзя переопределить аргументом CLI или API.
KORP_API_HOST = "ws.spraakbanken.gu.se"
KORP_API_PATH = "/ws/korp/v8/query"
KORP_API_ENDPOINT = f"https://{KORP_API_HOST}{KORP_API_PATH}"
KORP_CORPUS_ID = "DREAM-RU-OPEN"
# CQP-якоря структурный sentence, поэтому один hit представляет одно
# предложение, а не очередной токен уже показанного контекста.
KORP_CQP = "<sentence> []+ </sentence>"
KORP_SHOW_STRUCT = "sentence_id"
QUERY_MODE = "sentence_anchored_v0.2"

SOURCE_ID = "dream-ru-open-cc-by-v1"
SOURCE_LICENSE = "CC-BY-4.0"
SOURCE_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
SOURCE_ORIGIN_URL = "https://spraakbanken.gu.se/en/resources/dream-ru-open"
SOURCE_ATTRIBUTION = "Språkbanken, DReaM-ru-open (2019), DOI 10.23695/gzsh-rp33"

# Preview остаётся маленьким даже при ошибочном параметре в ручном запуске.
DEFAULT_MAX_PAGES = 1
DEFAULT_MATCHES_PER_PAGE = 20
# Полная staging-выгрузка получает тот же верхний лимит от источника: 150 page
# по 500 предложений покрывают текущие 64 128 структурных sentence hits, но не
# позволяют случайно открыть бесконечный сетевой обход.
DEFAULT_COLLECTION_MAX_PAGES = 150
DEFAULT_COLLECTION_MATCHES_PER_PAGE = 500
MAX_PAGES = DEFAULT_COLLECTION_MAX_PAGES
MAX_MATCHES_PER_PAGE = DEFAULT_COLLECTION_MATCHES_PER_PAGE
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_TOKENS_PER_CONTEXT = 1_024
MAX_TOKEN_CHARACTERS = 256
MAX_API_OFFSET = 2_000_000_000
REQUEST_TIMEOUT_SECONDS = 15.0
MAX_NETWORK_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0
PAGE_DELAY_SECONDS = 0.75

_EXPECTED_QUERY_KEYS = frozenset(
    {
        "corpus",
        "cqp",
        "start",
        "end",
        "default_context",
        "default_within",
        "show_struct",
    }
)
_OPTIONAL_QUERY_KEYS = frozenset({"query_data"})
_QUERY_DATA = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
_NO_SPACE_BEFORE = frozenset({".", ",", ";", ":", "!", "?", "%", ")", "]", "}", "»", "…"})
_NO_SPACE_AFTER = frozenset({"(", "[", "{", "«", "№"})

KorpFetcher = Callable[[str, float], bytes]
Sleeper = Callable[[float], None]
Clock = Callable[[], datetime]


class KorpDreaMPreviewError(ValueError):
    """Останавливает staging-preview до появления частичного активного корпуса."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Не даёт urllib перейти на другой адрес при ответе с HTTP-редиректом."""

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        redirect_url: str,
    ) -> Request | None:
        """Запрещает любое перенаправление, включая переход на тот же домен."""
        return None


@dataclass(frozen=True)
class KorpDreaMPreviewSummary:
    """Содержит проверяемые метаданные preview без предложений источника."""

    retrieved_at_utc: str
    output_path: str
    output_sha256: str
    max_pages: int
    matches_per_page: int
    requests_made: int
    api_total_hits: int
    kwic_hits_received: int
    records_written: int
    records_rejected: int
    rejections: dict[str, int]
    collection_complete: bool

    def as_dict(self) -> dict[str, Any]:
        """Возвращает безопасный JSON-отчёт, не содержащий текстов Korp."""
        return {
            "format_version": 1,
            "mode": "staging_collection" if self.collection_complete else "staging_preview",
            "source_id": SOURCE_ID,
            "source_kind": "open_licensed",
            "source_url": SOURCE_ORIGIN_URL,
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "attribution": SOURCE_ATTRIBUTION,
            "api_endpoint": KORP_API_ENDPOINT,
            "corpus_id": KORP_CORPUS_ID,
            "query_mode": QUERY_MODE,
            "cqp": KORP_CQP,
            "show_struct": KORP_SHOW_STRUCT,
            "retrieved_at_utc": self.retrieved_at_utc,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "max_pages": self.max_pages,
            "matches_per_page": self.matches_per_page,
            "requests_made": self.requests_made,
            "api_total_hits": self.api_total_hits,
            "kwic_hits_received": self.kwic_hits_received,
            "records_written": self.records_written,
            "records_rejected": self.records_rejected,
            "rejections": self.rejections,
            "collection_complete": self.collection_complete,
            "active_manifest_updated": False,
            "training_started": False,
        }


def _require_bounded_integer(value: int, *, minimum: int, maximum: int, code: str) -> int:
    """Проверяет числовой лимит до обращения к сети или файловой системе."""
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise KorpDreaMPreviewError(code)
    return value


def _valid_query_data(value: Any) -> bool:
    """Принимает только короткий URL-safe cache token, выданный самим Korp."""
    return isinstance(value, str) and 16 <= len(value) <= 256 and _QUERY_DATA.fullmatch(value) is not None


def validate_fixed_korp_url(url: str) -> None:
    """Подтверждает, что URL относится только к разрешённому Korp-запросу."""
    if not isinstance(url, str) or not url or len(url) > 2_000:
        raise KorpDreaMPreviewError("Korp URL имеет недопустимый формат.")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != KORP_API_HOST
        or parsed.path != KORP_API_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise KorpDreaMPreviewError("Разрешён только фиксированный HTTPS Korp endpoint.")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise KorpDreaMPreviewError("Korp URL содержит некорректные параметры.") from error
    if (
        not _EXPECTED_QUERY_KEYS.issubset(query)
        or not set(query).issubset(_EXPECTED_QUERY_KEYS | _OPTIONAL_QUERY_KEYS)
        or any(len(values) != 1 for values in query.values())
    ):
        raise KorpDreaMPreviewError("Korp URL содержит недопустимый набор параметров.")
    if (
        query["corpus"] != [KORP_CORPUS_ID]
        or query["cqp"] != [KORP_CQP]
        or query["default_context"] != ["1 sentence"]
        or query["default_within"] != ["sentence"]
        or query["show_struct"] != [KORP_SHOW_STRUCT]
    ):
        raise KorpDreaMPreviewError("Korp URL пытается обратиться не к DReaM-ru-open.")
    start_raw = query["start"][0]
    end_raw = query["end"][0]
    if not start_raw.isdecimal() or not end_raw.isdecimal():
        raise KorpDreaMPreviewError("Korp URL содержит нечисловую границу страницы.")
    start = int(start_raw)
    end = int(end_raw)
    if start > MAX_API_OFFSET or end < start or end - start + 1 > MAX_MATCHES_PER_PAGE:
        raise KorpDreaMPreviewError("Граница Korp page выходит за безопасный лимит.")
    if "query_data" in query and not _valid_query_data(query["query_data"][0]):
        raise KorpDreaMPreviewError("Korp URL содержит недопустимый cache token.")


def build_korp_query_url(start: int, end: int, *, query_data: str | None = None) -> str:
    """Строит единственный допустимый запрос одной маленькой страницы Korp."""
    _require_bounded_integer(start, minimum=0, maximum=MAX_API_OFFSET, code="Некорректное начало Korp page.")
    _require_bounded_integer(end, minimum=start, maximum=MAX_API_OFFSET, code="Некорректный конец Korp page.")
    if end - start + 1 > MAX_MATCHES_PER_PAGE:
        raise KorpDreaMPreviewError("Korp page превышает лимит совпадений.")
    if query_data is not None and not _valid_query_data(query_data):
        raise KorpDreaMPreviewError("Korp cache token имеет недопустимый формат.")
    parameters = {
        "corpus": KORP_CORPUS_ID,
        "cqp": KORP_CQP,
        "start": str(start),
        "end": str(end),
        "default_context": "1 sentence",
        "default_within": "sentence",
        "show_struct": KORP_SHOW_STRUCT,
    }
    if query_data is not None:
        parameters["query_data"] = query_data
    query = urlencode(parameters)
    url = f"{KORP_API_ENDPOINT}?{query}"
    validate_fixed_korp_url(url)
    return url


def _default_fetch(url: str, timeout: float) -> bytes:
    """Скачивает один JSON-ответ без редиректов и с лимитом размера."""
    validate_fixed_korp_url(url)
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Ai-Brain-v3-DReaM-staging/0.2",
        },
        method="GET",
    )
    opener = build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        if response.geturl() != url:
            raise KorpDreaMPreviewError("Korp endpoint попытался перенаправить запрос.")
        content_type = response.headers.get_content_type().lower()
        if content_type != "application/json":
            raise KorpDreaMPreviewError("Korp endpoint вернул ответ не в JSON-формате.")
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise KorpDreaMPreviewError("Ответ Korp превышает безопасный лимит размера.")
    return body


def _retry_delay(attempt: int) -> float:
    """Даёт источнику растущую паузу после временной сетевой ошибки."""
    return RETRY_DELAY_SECONDS * (2**attempt)


def _request_json(url: str, fetcher: KorpFetcher, sleeper: Sleeper) -> dict[str, Any]:
    """Выполняет до четырёх попыток с backoff и принимает только JSON-object."""
    for attempt in range(MAX_NETWORK_RETRIES + 1):
        try:
            raw = fetcher(url, REQUEST_TIMEOUT_SECONDS)
        except (OSError, TimeoutError) as error:
            if attempt < MAX_NETWORK_RETRIES:
                sleeper(_retry_delay(attempt))
                continue
            raise KorpDreaMPreviewError("Не удалось получить Korp JSON за ограниченное число попыток.") from error
        if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
            raise KorpDreaMPreviewError("Fetcher вернул ответ недопустимого размера или типа.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise KorpDreaMPreviewError("Korp endpoint вернул некорректный JSON UTF-8.") from error
        if not isinstance(payload, dict):
            raise KorpDreaMPreviewError("Korp JSON должен быть объектом.")
        return payload
    raise AssertionError("Цикл запросов должен завершиться внутри ограниченного числа попыток.")


def _read_payload_page(payload: dict[str, Any], matches_per_page: int) -> tuple[int, list[Any], str | None]:
    """Извлекает счётчик, ограниченный KWIC page и безопасный cache token."""
    hits = payload.get("hits")
    kwic = payload.get("kwic")
    if not isinstance(hits, int) or isinstance(hits, bool) or hits < 0:
        raise KorpDreaMPreviewError("Korp JSON не содержит корректный счётчик hits.")
    if not isinstance(kwic, list) or len(kwic) > matches_per_page:
        raise KorpDreaMPreviewError("Korp JSON содержит недопустимый размер KWIC page.")
    query_data = payload.get("query_data")
    return hits, kwic, query_data if _valid_query_data(query_data) else None


def _detokenize(words: list[str]) -> str:
    """Восстанавливает читаемое предложение из поверхностных токенов Korp."""
    output: list[str] = []
    previous: str | None = None
    for word in words:
        if previous is None:
            output.append(word)
        elif word in _NO_SPACE_BEFORE or previous in _NO_SPACE_AFTER:
            output.append(word)
        else:
            output.append(" ")
            output.append(word)
        previous = word
    return "".join(output).strip()


def _preview_text_risk_code(text: str) -> str | None:
    """Дополняет общий фильтр URL-проверкой после token-level пробелов."""
    risk = text_risk_code(text, maximum=MAX_DOCUMENT_LENGTH)
    if risk is not None:
        return risk
    compact = "".join(text.split()).casefold()
    if "http://" in compact or "https://" in compact or "www." in compact:
        return "url_like"
    return None


def _extract_sentence(hit: Any) -> tuple[str, str]:
    """Преобразует один структурный sentence hit в id и безопасный текст-кандидат."""
    if not isinstance(hit, dict) or hit.get("corpus") != KORP_CORPUS_ID:
        raise KorpDreaMPreviewError("unexpected_corpus")
    structs = hit.get("structs")
    if not isinstance(structs, dict):
        raise KorpDreaMPreviewError("missing_sentence_struct")
    sentence_id = structs.get(KORP_SHOW_STRUCT)
    if not isinstance(sentence_id, str) or not sentence_id.strip() or len(sentence_id) > 256:
        raise KorpDreaMPreviewError("invalid_sentence_id")
    normalised_sentence_id = sentence_id.strip()
    tokens = hit.get("tokens")
    if not isinstance(tokens, list) or not 1 <= len(tokens) <= MAX_TOKENS_PER_CONTEXT:
        raise KorpDreaMPreviewError("invalid_tokens")
    words: list[str] = []
    for token in tokens:
        if not isinstance(token, dict):
            raise KorpDreaMPreviewError("invalid_token")
        word = token.get("word")
        if not isinstance(word, str) or not word.strip() or len(word) > MAX_TOKEN_CHARACTERS:
            raise KorpDreaMPreviewError("invalid_token_word")
        if "\x00" in word:
            raise KorpDreaMPreviewError("invalid_token_word")
        words.append(word.strip())
    text = _detokenize(words)
    if not any("а" <= character.casefold() <= "я" or character.casefold() == "ё" for character in text):
        raise KorpDreaMPreviewError("non_russian_text")
    return normalised_sentence_id, text


def _normalised_fingerprint(text: str) -> str:
    """Создаёт необратимый ключ точного повтора без хранения текста в отчёте."""
    normalised = " ".join(text.casefold().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _deterministic_split(fingerprint: str) -> str:
    """Стабильно распределяет staging-запись без зависимости от page API."""
    bucket = int(fingerprint[:8], 16) % 100
    if bucket < 90:
        return "train"
    if bucket < 95:
        return "validation"
    return "test"


def _record(text: str, fingerprint: str) -> dict[str, Any]:
    """Создаёт будущую language_document-запись с provenance и content hash."""
    payload: dict[str, Any] = {
        "format_version": 1,
        "id": f"dream.ru.open.{fingerprint[:32]}",
        "record_type": "language_document",
        "provenance": {
            "source_id": SOURCE_ID,
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "origin_url": SOURCE_ORIGIN_URL,
            "attribution": SOURCE_ATTRIBUTION,
        },
        "language": "ru",
        "layer": "language",
        "split": _deterministic_split(fingerprint),
        "topic": "open_sentence_corpus",
        "quality_tier": "license_verified",
        "expected_response_properties": ["natural_tone", "relevant"],
        "document_kind": "korp_sentence_context",
        "text": text,
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _prepare_destination(destination: Path, staging_root: Path) -> Path:
    """Разрешает писать новый JSONL только внутрь отдельной staging-папки."""
    root = staging_root.expanduser().resolve()
    target = destination.expanduser().resolve()
    if target.suffix.lower() != ".jsonl":
        raise KorpDreaMPreviewError("Staging output DReaM должен быть JSONL-файлом.")
    try:
        target.relative_to(root)
    except ValueError as error:
        raise KorpDreaMPreviewError("DReaM preview разрешён только внутри data/brain_v3/staging/.") from error
    if target.exists():
        raise KorpDreaMPreviewError("Staging output уже существует; перезапись запрещена.")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _utc_now() -> datetime:
    """Возвращает timezone-aware время для воспроизводимого мета-снимка."""
    return datetime.now(timezone.utc)


def preview_dream_ru_open(
    destination: Path,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    matches_per_page: int = DEFAULT_MATCHES_PER_PAGE,
    staging_root: Path = DEFAULT_STAGING_ROOT,
    fetcher: KorpFetcher | None = None,
    sleeper: Sleeper = time.sleep,
    clock: Clock = _utc_now,
    require_complete: bool = False,
) -> KorpDreaMPreviewSummary:
    """Сохраняет маленький immutable DReaM snapshot, но не импортирует источник.

    ``fetcher`` и ``staging_root`` существуют только для изолированных тестов.
    CLI не принимает от пользователя ни endpoint, ни corpus id, ни staging root.
    ``require_complete`` используется только полным staging-сборщиком.
    """
    pages = _require_bounded_integer(max_pages, minimum=1, maximum=MAX_PAGES, code="Некорректный лимит Korp pages.")
    page_size = _require_bounded_integer(
        matches_per_page,
        minimum=1,
        maximum=MAX_MATCHES_PER_PAGE,
        code="Некорректный размер Korp page.",
    )
    if not isinstance(require_complete, bool):
        raise KorpDreaMPreviewError("Флаг полноты DReaM collection должен быть bool.")
    output_path = _prepare_destination(destination, staging_root)
    active_fetcher = _default_fetch if fetcher is None else fetcher
    rejections: Counter[str] = Counter()
    seen_sentence_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    near_duplicates = NearDuplicateIndex()
    requests_made = 0
    api_total_hits: int | None = None
    kwic_hits_received = 0
    records_written = 0
    next_query_data: str | None = None
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=".dream-ru-open-preview-",
            suffix=".jsonl.tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            for page_number in range(pages):
                start = page_number * page_size
                if api_total_hits is not None and start >= api_total_hits:
                    break
                if page_number > 0:
                    sleeper(PAGE_DELAY_SECONDS)
                url = build_korp_query_url(
                    start,
                    start + page_size - 1,
                    query_data=next_query_data,
                )
                payload = _request_json(url, active_fetcher, sleeper)
                requests_made += 1
                current_total_hits, kwic, returned_query_data = _read_payload_page(payload, page_size)
                if returned_query_data is not None:
                    next_query_data = returned_query_data
                if api_total_hits is None:
                    api_total_hits = current_total_hits
                elif current_total_hits != api_total_hits:
                    raise KorpDreaMPreviewError("Korp счётчик hits изменился во время одного preview.")
                kwic_hits_received += len(kwic)

                for hit in kwic:
                    try:
                        sentence_id, text = _extract_sentence(hit)
                    except KorpDreaMPreviewError as error:
                        rejections[str(error)] += 1
                        continue
                    if sentence_id in seen_sentence_ids:
                        rejections["duplicate_sentence_id"] += 1
                        continue
                    seen_sentence_ids.add(sentence_id)
                    risk = _preview_text_risk_code(text)
                    if risk is not None:
                        rejections[risk] += 1
                        continue
                    fingerprint = _normalised_fingerprint(text)
                    if fingerprint in seen_fingerprints:
                        rejections["exact_duplicate_context"] += 1
                        continue
                    signature = duplicate_signature(text)
                    if near_duplicates.find(signature) is not None:
                        rejections["near_duplicate_candidate"] += 1
                        continue
                    seen_fingerprints.add(fingerprint)
                    near_duplicates.add(sentence_id, signature)
                    record = _record(text, fingerprint)
                    temporary.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                    temporary.write("\n")
                    records_written += 1

                if len(kwic) < page_size:
                    break

        collection_complete = api_total_hits is not None and kwic_hits_received >= api_total_hits
        if require_complete and not collection_complete:
            raise KorpDreaMPreviewError("DReaM collection не получил все заявленные sentence hits.")
        if records_written == 0:
            raise KorpDreaMPreviewError("DReaM preview не получил безопасных русскоязычных записей.")
        if output_path.exists():
            raise KorpDreaMPreviewError("Staging output появился во время preview; перезапись запрещена.")
        assert temporary_path is not None
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    retrieved_at = clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return KorpDreaMPreviewSummary(
        retrieved_at_utc=retrieved_at,
        output_path=str(output_path),
        output_sha256=file_sha256(output_path),
        max_pages=pages,
        matches_per_page=page_size,
        requests_made=requests_made,
        api_total_hits=api_total_hits if api_total_hits is not None else 0,
        kwic_hits_received=kwic_hits_received,
        records_written=records_written,
        records_rejected=sum(rejections.values()),
        rejections=dict(sorted(rejections.items())),
        collection_complete=collection_complete,
    )


def collect_dream_ru_open(
    destination: Path,
    *,
    max_pages: int = DEFAULT_COLLECTION_MAX_PAGES,
    matches_per_page: int = DEFAULT_COLLECTION_MATCHES_PER_PAGE,
    staging_root: Path = DEFAULT_STAGING_ROOT,
    fetcher: KorpFetcher | None = None,
    sleeper: Sleeper = time.sleep,
    clock: Clock = _utc_now,
) -> KorpDreaMPreviewSummary:
    """Собирает все sentence hits только в staging и требует завершённость.

    Верхние лимиты фиксированы в модуле. Эта функция не добавляет JSONL в
    manifest и не вызывает обучение, даже после полного прохода источника.
    """
    return preview_dream_ru_open(
        destination,
        max_pages=max_pages,
        matches_per_page=matches_per_page,
        staging_root=staging_root,
        fetcher=fetcher,
        sleeper=sleeper,
        clock=clock,
        require_complete=True,
    )


def summary_markdown(summary: KorpDreaMPreviewSummary) -> str:
    """Формирует Markdown-учёт preview без показа текстов исходного корпуса."""
    return "\n".join(
        [
            "# Предварительный снимок DReaM-ru-open",
            "",
            "Статус: staging-collection завершён, но источник ещё не активен."
            if summary.collection_complete
            else "Статус: staging-preview, не активный источник обучения.",
            f"Источник: [{SOURCE_ID}]({SOURCE_ORIGIN_URL}).",
            f"Лицензия: [{SOURCE_LICENSE}]({SOURCE_LICENSE_URL}).",
            f"Korp corpus id: `{KORP_CORPUS_ID}`; endpoint: `{KORP_API_ENDPOINT}`.",
            f"Режим запроса: `{QUERY_MODE}`; CQP: `{KORP_CQP}`; show_struct: `{KORP_SHOW_STRUCT}`.",
            f"Время получения: `{summary.retrieved_at_utc}`.",
            f"Запросов: {summary.requests_made}; KWIC-совпадений: {summary.kwic_hits_received}; записей: {summary.records_written}; отклонено: {summary.records_rejected}.",
            f"Лимит: {summary.max_pages} page × {summary.matches_per_page} совпадений; API сообщил всего hits: {summary.api_total_hits}.",
            f"Причины отклонения: {summary.rejections}.",
            f"Все sentence hits получены: `{summary.collection_complete}`.",
            f"SHA-256 staging JSONL: `{summary.output_sha256}`.",
            "",
            "Файл не добавлен в active manifest, не проходил полный corpus audit как approved source и не запускал обучение. Тексты предложений в отчёт не выводятся.",
            "",
        ]
    )
