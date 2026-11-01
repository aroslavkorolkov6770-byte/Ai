"""Строго импортирует только маленький CC-BY staging-пилот русских диалогов.

Модуль не является универсальным загрузчиком Hugging Face. Он принимает данные
с одного закреплённого revision, сверяет исходный SHA-256, рассматривает все
поля как недоверенные данные и пишет только новый JSONL в `staging/`. Никакой
manifest, raw-файл, обучение или работающий чат этим модулем не меняются.
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
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .brain_v3_corpus import MAX_TEXT_LENGTH, content_sha256, file_sha256, text_risk_code


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STAGING_ROOT = ROOT / "data" / "brain_v3" / "staging"

SOURCE_ID = "russian-everyday-dialogues-cc-by-v1"
SOURCE_LICENSE = "CC-BY-4.0"
SOURCE_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
SOURCE_ORIGIN_URL = "https://huggingface.co/datasets/kukunechka/russian-everyday-dialogues"
SOURCE_ATTRIBUTION = "Ekaterina Myskina (2025), Russian Everyday Dialogues, Hugging Face"
SOURCE_REVISION = "3d9c43ca85a50e7a32fa7b05e27c0637a710e988"
SOURCE_HOST = "huggingface.co"
SOURCE_PATH = (
    "/datasets/kukunechka/russian-everyday-dialogues/raw/"
    f"{SOURCE_REVISION}/russian_everyday_dialogues.jsonl"
)
SOURCE_URL = f"https://{SOURCE_HOST}{SOURCE_PATH}"
EXPECTED_RAW_SHA256 = "2599fe92d9f976e262f89b50dc5ec02b97bd2c3f2edad965b83d74e38ecd4169"
EXPECTED_RAW_ROWS = 20
EXPECTED_SITUATIONS = {"магазин": 7, "транспорт": 6, "кафе": 7}

MAX_RAW_BYTES = 128 * 1024
REQUEST_TIMEOUT_SECONDS = 15.0
MAX_NETWORK_RETRIES = 2
RETRY_DELAY_SECONDS = 1.0

_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_FACTUAL_TAGS = frozenset({"цена", "скидки", "лояльность", "маршрут", "расписание", "свежесть", "права покупателя"})
_FACTUAL_MARKER = re.compile(
    r"\b(?:рубл\w*|полчаса|сегодня|утром|станци\w*|вокзал|маршрутк\w*|"
    r"троллейбус\w*|автобус\w*|метро|четырнадцат\w*)\b",
    flags=re.IGNORECASE,
)
_TOPICS = {"магазин": "everyday_shop", "транспорт": "everyday_transport", "кафе": "everyday_cafe"}

RawFetcher = Callable[[str, float], bytes]
Sleeper = Callable[[float], None]


class RussianEverydayDialoguesError(ValueError):
    """Останавливает staging-приём до появления неполного или неверного JSONL."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Запрещает источнику перенаправлять запрос на иной или неожиданный URL."""

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        redirect_url: str,
    ) -> Request | None:
        """Отменяет все HTTP-редиректы без исключения."""
        return None


@dataclass(frozen=True)
class RussianEverydayDialoguesSummary:
    """Содержит метрики staging-приёма без текста исходных диалогов."""

    raw_sha256: str
    output_path: str
    output_sha256: str
    raw_rows_seen: int
    records_written: int
    records_rejected: int
    rejections: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        """Возвращает сериализуемый отчёт без пользовательских реплик."""
        return {
            "format_version": 1,
            "mode": "staging_dialogue_import",
            "source_id": SOURCE_ID,
            "source_url": SOURCE_ORIGIN_URL,
            "source_revision": SOURCE_REVISION,
            "source_raw_sha256": self.raw_sha256,
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "attribution": SOURCE_ATTRIBUTION,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "raw_rows_seen": self.raw_rows_seen,
            "records_written": self.records_written,
            "records_rejected": self.records_rejected,
            "rejections": self.rejections,
            "candidate_is_active": False,
            "active_manifest_updated": False,
            "training_started": False,
        }


def validate_fixed_source_url(url: str) -> None:
    """Разрешает только закреплённый HTTPS URL одного проверенного JSONL."""
    if not isinstance(url, str) or not url or len(url) > 1_000:
        raise RussianEverydayDialoguesError("URL dialogue source имеет недопустимый формат.")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != SOURCE_HOST
        or parsed.path != SOURCE_PATH
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RussianEverydayDialoguesError("Разрешён только закреплённый HTTPS URL dialogue source.")


def _default_fetch(url: str, timeout: float) -> bytes:
    """Скачивает один ограниченный plain-text JSONL без редиректов."""
    validate_fixed_source_url(url)
    request = Request(
        url,
        headers={
            "Accept": "text/plain, application/json;q=0.9",
            "User-Agent": "Ai-Brain-v3-dialogue-staging/0.1",
        },
        method="GET",
    )
    opener = build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        if response.geturl() != url:
            raise RussianEverydayDialoguesError("Dialogue source попытался перенаправить запрос.")
        if response.headers.get_content_type().lower() != "text/plain":
            raise RussianEverydayDialoguesError("Dialogue source вернул не plain-text JSONL.")
        payload = response.read(MAX_RAW_BYTES + 1)
    if len(payload) > MAX_RAW_BYTES:
        raise RussianEverydayDialoguesError("Dialogue source превышает безопасный лимит размера.")
    return payload


def _request_raw(fetcher: RawFetcher, sleeper: Sleeper) -> bytes:
    """Выполняет ограниченные повторные попытки только для сетевых сбоев."""
    for attempt in range(MAX_NETWORK_RETRIES + 1):
        try:
            payload = fetcher(SOURCE_URL, REQUEST_TIMEOUT_SECONDS)
        except (OSError, TimeoutError) as error:
            if attempt < MAX_NETWORK_RETRIES:
                sleeper(RETRY_DELAY_SECONDS * (2**attempt))
                continue
            raise RussianEverydayDialoguesError("Не удалось получить dialogue source за ограниченное число попыток.") from error
        if not isinstance(payload, bytes) or len(payload) > MAX_RAW_BYTES:
            raise RussianEverydayDialoguesError("Fetcher вернул source недопустимого типа или размера.")
        if hashlib.sha256(payload).hexdigest() != EXPECTED_RAW_SHA256:
            raise RussianEverydayDialoguesError("SHA-256 закреплённого dialogue source не совпал.")
        return payload
    raise AssertionError("Ограниченный цикл повторов должен завершиться внутри цикла.")


def _read_raw_rows(payload: bytes) -> list[dict[str, Any]]:
    """Разбирает ровно ожидаемые JSONL-строки, не выполняя их содержимое."""
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RussianEverydayDialoguesError("Dialogue source не является корректным UTF-8.") from error
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(decoded.splitlines(), start=1):
        if not line.strip():
            raise RussianEverydayDialoguesError("Dialogue source содержит пустую JSONL-строку.")
        if len(line.encode("utf-8")) > 16 * 1024:
            raise RussianEverydayDialoguesError("Dialogue source содержит слишком длинную JSONL-строку.")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise RussianEverydayDialoguesError("Dialogue source содержит некорректный JSON.") from error
        if not isinstance(row, dict):
            raise RussianEverydayDialoguesError("Dialogue source содержит строку не-объект.")
        rows.append(row)
    if len(rows) != EXPECTED_RAW_ROWS:
        raise RussianEverydayDialoguesError("Dialogue source изменил ожидаемое число строк.")
    return rows


def _validated_row(row: Any) -> tuple[int, str, str, str, str, tuple[str, ...]]:
    """Проверяет точную публичную схему одного source-диалога."""
    fields = {"id", "situation", "user", "assistant", "register", "tags"}
    if not isinstance(row, dict) or set(row) != fields:
        raise RussianEverydayDialoguesError("Dialogue source содержит недопустимые поля записи.")
    identifier = row["id"]
    if not isinstance(identifier, int) or isinstance(identifier, bool) or not 1 <= identifier <= EXPECTED_RAW_ROWS:
        raise RussianEverydayDialoguesError("Dialogue source содержит недопустимый id.")
    situation = row["situation"]
    register = row["register"]
    user = row["user"]
    assistant = row["assistant"]
    if situation not in _TOPICS or register not in {"нейтральный", "разговорный"}:
        raise RussianEverydayDialoguesError("Dialogue source содержит неизвестную ситуацию или регистр.")
    if not isinstance(user, str) or not isinstance(assistant, str):
        raise RussianEverydayDialoguesError("Dialogue source содержит нестроковую реплику.")
    if not _CYRILLIC.search(user) or not _CYRILLIC.search(assistant):
        raise RussianEverydayDialoguesError("Dialogue source содержит не русскоязычную реплику.")
    for text in (user, assistant):
        if text_risk_code(text, maximum=MAX_TEXT_LENGTH) is not None:
            raise RussianEverydayDialoguesError("Dialogue source содержит небезопасную реплику.")
    tags = row["tags"]
    if not isinstance(tags, list) or not 1 <= len(tags) <= 8 or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise RussianEverydayDialoguesError("Dialogue source содержит недопустимые теги.")
    return identifier, situation, user.strip(), assistant.strip(), register, tuple(tag.strip().casefold() for tag in tags)


def _context_bound_fact(tags: tuple[str, ...], user: str, assistant: str) -> bool:
    """Отсекает локальные цены, маршруты, сроки и другие изменяемые утверждения."""
    return bool(_FACTUAL_TAGS.intersection(tags) or _FACTUAL_MARKER.search(f"{user}\n{assistant}"))


def _split_for_rank(rank: int, total: int) -> str:
    """Даёт все split даже маленькому пилоту после фильтрации, без случайности."""
    train_count = max(1, total * 8 // 10)
    validation_count = max(1, total // 10)
    if train_count + validation_count >= total:
        validation_count = 1
        train_count = total - 2
    if rank < train_count:
        return "train"
    if rank < train_count + validation_count:
        return "validation"
    return "test"


def _brain_record(
    source_identifier: int,
    situation: str,
    user: str,
    assistant: str,
    register: str,
    split: str,
) -> dict[str, Any]:
    """Строит одну Brain v3 dialogue-запись с фиксированным provenance."""
    payload: dict[str, Any] = {
        "format_version": 1,
        "id": f"russian.everyday.dialogues.{source_identifier:03d}",
        "record_type": "dialogue",
        "provenance": {
            "source_id": SOURCE_ID,
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "origin_url": SOURCE_ORIGIN_URL,
            "attribution": SOURCE_ATTRIBUTION,
        },
        "language": "ru",
        "layer": "dialogue",
        "split": split,
        "topic": _TOPICS[situation],
        "quality_tier": "license_verified",
        "expected_response_properties": ["relevant", "natural_tone", "brief"],
        "dialogue_goal": "everyday_exchange",
        "emotional_tone": "neutral" if register == "нейтральный" else "colloquial",
        "turns": [
            {"role": "user", "text": user},
            {"role": "assistant", "text": assistant},
        ],
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _prepare_destination(destination: Path, staging_root: Path) -> Path:
    """Разрешает создавать новый output только в dedicated staging-папке."""
    root = staging_root.expanduser().resolve()
    target = destination.expanduser().resolve()
    if target.suffix.lower() != ".jsonl":
        raise RussianEverydayDialoguesError("Dialogue staging output должен быть JSONL.")
    try:
        target.relative_to(root)
    except ValueError as error:
        raise RussianEverydayDialoguesError("Dialogue output разрешён только внутри data/brain_v3/staging/.") from error
    if target.exists():
        raise RussianEverydayDialoguesError("Нельзя перезаписывать существующий dialogue staging output.")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def import_russian_everyday_dialogues(
    destination: Path,
    *,
    staging_root: Path = DEFAULT_STAGING_ROOT,
    fetcher: RawFetcher | None = None,
    sleeper: Sleeper = time.sleep,
) -> RussianEverydayDialoguesSummary:
    """Создаёт один новый очищенный dialogue staging JSONL без activation."""
    output_path = _prepare_destination(destination, staging_root)
    raw_payload = _request_raw(_default_fetch if fetcher is None else fetcher, sleeper)
    rows = _read_raw_rows(raw_payload)
    situations: Counter[str] = Counter()
    identifiers: set[int] = set()
    accepted: list[tuple[int, str, str, str, str]] = []
    rejections: Counter[str] = Counter()

    for row in rows:
        identifier, situation, user, assistant, register, tags = _validated_row(row)
        if identifier in identifiers:
            raise RussianEverydayDialoguesError("Dialogue source содержит повторный id.")
        identifiers.add(identifier)
        situations[situation] += 1
        if _context_bound_fact(tags, user, assistant):
            rejections["context_bound_local_fact"] += 1
            continue
        accepted.append((identifier, situation, user, assistant, register))

    if identifiers != set(range(1, EXPECTED_RAW_ROWS + 1)) or dict(situations) != EXPECTED_SITUATIONS:
        raise RussianEverydayDialoguesError("Dialogue source изменил ожидаемый coverage или идентификаторы.")
    if len(accepted) < 3:
        raise RussianEverydayDialoguesError("После смыслового фильтра нет достаточных диалогов для split.")

    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".russian-everyday-dialogues-",
            suffix=".jsonl.tmp",
            dir=output_path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination_file:
            for rank, values in enumerate(accepted):
                source_identifier, situation, user, assistant, register = values
                record = _brain_record(
                    source_identifier,
                    situation,
                    user,
                    assistant,
                    register,
                    _split_for_rank(rank, len(accepted)),
                )
                destination_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return RussianEverydayDialoguesSummary(
        raw_sha256=hashlib.sha256(raw_payload).hexdigest(),
        output_path=str(output_path),
        output_sha256=file_sha256(output_path),
        raw_rows_seen=len(rows),
        records_written=len(accepted),
        records_rejected=len(rows) - len(accepted),
        rejections=dict(sorted(rejections.items())),
    )
