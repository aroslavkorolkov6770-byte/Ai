"""Строгая локальная проверка будущего корпуса Brain v3.

Модуль создаёт границу между недоверенным исходным текстом и данными, которые
теоретически могут попасть в обучение. Он не запускает код из источников, не
скачивает материалы и не формирует учебные ответы. До прохождения проверки
данные остаются только сырым локальным материалом.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "data" / "brain_v3" / "corpus_manifest.v1.json"
FORMAT_VERSION = 1
ALLOWED_SPLITS = frozenset({"train", "validation", "test"})
ALLOWED_SOURCE_KINDS = frozenset({"owned", "open_licensed", "consented_feedback"})
ALLOWED_RECORD_TYPES = frozenset({"dialogue", "language_document"})
ALLOWED_LAYERS = frozenset({"language", "dialogue", "reasoning", "tool"})
ALLOWED_QUALITY_TIERS = frozenset({"license_verified", "human_reviewed"})
ALLOWED_ROLES = ("user", "assistant")
TOOL_RESULT_PROPERTIES = frozenset({"tool_result_returned", "no_action_promise"})
ALLOWED_RESPONSE_PROPERTIES = frozenset(
    {
        "relevant",
        "natural_tone",
        "brief",
        "context_retained",
        "no_repeat",
        "ask_when_missing",
        "fact_grounded",
        "no_url_without_request",
        "no_unfounded_memory",
        "privacy_respected",
        "acknowledges_correction",
        "appropriate_empathy",
        "clear_next_step",
        "no_fake_certainty",
        "safe_boundary",
        "source_on_request",
        "uses_requested_style",
        "tool_result_returned",
        "no_action_promise",
    }
)

MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_RECORD_BYTES = 256 * 1024
MAX_RECORDS_PER_SOURCE = 1_000_000
MAX_TURNS_PER_DIALOGUE = 32
MAX_TEXT_LENGTH = 20_000
MAX_DOCUMENT_LENGTH = 80_000
NEAR_DUPLICATE_HAMMING_DISTANCE = 3

_SPACE = re.compile(r"\s+")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WORD = re.compile(r"[\w]+", flags=re.UNICODE)
_URL = re.compile(r"(?:https?://|www\.)", flags=re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d()\-\s]{8,}\d)(?!\w)")
_SECRET = re.compile(
    r"\b(?:api[_ -]?key|secret|password|парол[ья]|токен)\s*[:=]\s*\S+",
    flags=re.IGNORECASE,
)
_INSTRUCTION_LIKE = (
    re.compile(r"ignore\s+(?:all\s+)?previous", flags=re.IGNORECASE),
    re.compile(r"ignore\s+instructions", flags=re.IGNORECASE),
    re.compile(r"игнорируй\s+(?:все\s+)?предыдущ", flags=re.IGNORECASE),
    re.compile(r"system\s+prompt", flags=re.IGNORECASE),
    re.compile(r"раскрой\s+(?:системн|скрыт).*(?:инструкц|промпт)", flags=re.IGNORECASE),
)


class BrainV3CorpusError(ValueError):
    """Останавливает аудит при повреждении манифеста или источника."""


class BrainV3RecordError(BrainV3CorpusError):
    """Описывает причину отклонения одной строки без вывода её текста."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DuplicateSignature:
    """Компактный отпечаток текста для поиска близких повторов без текста."""

    simhash: int
    token_count: int


class NearDuplicateIndex:
    """Индекс кандидатов близкого дубля, общий для import и независимого audit.

    В памяти остаются только 64-bit подпись, длина и переданное вызывающей
    стороной безопасное мета-значение. Исходные сообщения не сохраняются.
    """

    def __init__(self) -> None:
        self._buckets: dict[tuple[int, int], list[tuple[object, DuplicateSignature]]] = defaultdict(list)

    def find(self, candidate: DuplicateSignature) -> object | None:
        """Возвращает мета-значение близкого дубля либо ``None``."""
        for bucket in _signature_buckets(candidate.simhash):
            for value, previous in self._buckets[bucket]:
                similar_length = min(previous.token_count, candidate.token_count) / max(
                    previous.token_count,
                    candidate.token_count,
                )
                if (
                    similar_length >= 0.85
                    and _hamming_distance(previous.simhash, candidate.simhash)
                    <= NEAR_DUPLICATE_HAMMING_DISTANCE
                ):
                    return value
        return None

    def add(self, value: object, signature: DuplicateSignature) -> None:
        """Добавляет уже принятый безопасный мета-идентификатор в индекс."""
        for bucket in _signature_buckets(signature.simhash):
            self._buckets[bucket].append((value, signature))


@dataclass(frozen=True)
class CorpusSource:
    """Проверенный источник, описанный в манифесте до чтения JSONL."""

    source_id: str
    raw_path: Path
    raw_sha256: str
    source_kind: str
    license_name: str
    license_url: str | None
    origin_url: str | None
    attribution: str


@dataclass(frozen=True)
class CorpusQualityGate:
    """Минимальные условия, при которых корпус можно передать в обучение."""

    minimum_unique_dialogue_turns: int
    minimum_language_characters: int
    maximum_rejected_record_ratio: float
    require_all_splits: bool


@dataclass(frozen=True)
class CorpusManifest:
    """Полный контролируемый список источников будущего корпуса."""

    corpus_name: str
    language_scope: tuple[str, ...]
    accepted_licenses: frozenset[str]
    quality_gate: CorpusQualityGate
    sources: tuple[CorpusSource, ...]


@dataclass(frozen=True)
class AcceptedRecord:
    """Минимальный набор метаданных для отчёта и дедупликации без текста."""

    identifier: str
    source_id: str
    split: str
    record_type: str
    content_fingerprint: str
    simhash: int
    token_count: int
    dialogue_turns: int
    language_characters: int


def file_sha256(path: Path) -> str:
    """Вычисляет SHA-256 потоком, не загружая источник целиком в память."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_text(value: str) -> str:
    """Строит ключ сравнения с сохранением слов и пунктуации."""
    return _SPACE.sub(" ", value.casefold()).strip()


def _canonical_json(value: Any) -> str:
    """Даёт стабильное представление для контрольной суммы записи."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_sha256(payload: dict[str, Any]) -> str:
    """Считает хэш записи без поля с её собственным хэшем."""
    value = dict(payload)
    value.pop("content_sha256", None)
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _fingerprint(value: str) -> str:
    """Создаёт необратимый ключ точного дубля без попадания текста в отчёт."""
    return hashlib.sha256(_normalise_text(value).encode("utf-8")).hexdigest()


def _require_string(value: Any, *, maximum: int, code: str) -> str:
    """Проверяет короткую строку без включения её содержимого в ошибку."""
    if not isinstance(value, str):
        raise BrainV3RecordError(code)
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise BrainV3RecordError(code)
    return normalized


def _require_identifier(value: Any, code: str) -> str:
    """Проверяет безопасный стабильный идентификатор записи или источника."""
    identifier = _require_string(value, maximum=96, code=code)
    if _IDENTIFIER.fullmatch(identifier) is None:
        raise BrainV3RecordError(code)
    return identifier


def _optional_http_url(value: Any, code: str) -> str | None:
    """Разрешает null для локального источника или корректный HTTP(S) URL."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2_000:
        raise BrainV3RecordError(code)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BrainV3RecordError(code)
    return value


def _safe_relative_file(base: Path, raw_path: str) -> Path:
    """Разрешает читать только неизменяемый JSONL из папки ``raw`` корпуса."""
    candidate = Path(raw_path)
    if candidate.is_absolute() or candidate.suffix.lower() != ".jsonl":
        raise BrainV3CorpusError("Источник должен быть относительным JSONL-файлом.")
    if not candidate.parts or candidate.parts[0] != "raw":
        raise BrainV3CorpusError("Источник должен находиться в неизменяемой папке raw/.")
    resolved_base = base.resolve()
    resolved = (resolved_base / candidate).resolve()
    try:
        resolved.relative_to(resolved_base)
    except ValueError as error:
        raise BrainV3CorpusError("Путь источника выходит за пределы папки корпуса.") from error
    return resolved


def _parse_quality_gate(payload: Any) -> CorpusQualityGate:
    """Проверяет явные числовые пороги, не подменяя их скрытыми defaults."""
    fields = {
        "minimum_unique_dialogue_turns",
        "minimum_language_characters",
        "maximum_rejected_record_ratio",
        "require_all_splits",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        raise BrainV3CorpusError("quality_gate содержит недопустимые или отсутствующие поля.")

    def non_negative_integer(field: str) -> int:
        value = payload[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise BrainV3CorpusError(f"quality_gate.{field} должен быть неотрицательным integer.")
        return value

    ratio = payload["maximum_rejected_record_ratio"]
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 <= ratio <= 1:
        raise BrainV3CorpusError("quality_gate.maximum_rejected_record_ratio должен быть от 0 до 1.")
    if not isinstance(payload["require_all_splits"], bool):
        raise BrainV3CorpusError("quality_gate.require_all_splits должен быть bool.")
    return CorpusQualityGate(
        minimum_unique_dialogue_turns=non_negative_integer("minimum_unique_dialogue_turns"),
        minimum_language_characters=non_negative_integer("minimum_language_characters"),
        maximum_rejected_record_ratio=float(ratio),
        require_all_splits=payload["require_all_splits"],
    )


def _parse_source(
    payload: Any,
    *,
    base: Path,
    accepted_licenses: frozenset[str],
) -> CorpusSource:
    """Разбирает одну approved запись манифеста и проверяет её происхождение."""
    fields = {
        "source_id",
        "raw_path",
        "raw_sha256",
        "source_kind",
        "license",
        "license_url",
        "origin_url",
        "attribution",
        "approved_for_training",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        raise BrainV3CorpusError("Источник манифеста содержит недопустимые или отсутствующие поля.")
    source_id = _require_identifier(payload["source_id"], "invalid_source_id")
    raw_path = payload["raw_path"]
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise BrainV3CorpusError("raw_path источника должен быть непустой строкой.")
    checksum = _require_string(payload["raw_sha256"], maximum=64, code="invalid_raw_sha256")
    if _SHA256.fullmatch(checksum) is None:
        raise BrainV3CorpusError("raw_sha256 источника должен быть SHA-256 в нижнем регистре.")
    source_kind = _require_string(payload["source_kind"], maximum=40, code="invalid_source_kind")
    if source_kind not in ALLOWED_SOURCE_KINDS:
        raise BrainV3CorpusError("source_kind источника не поддерживается.")
    license_name = _require_string(payload["license"], maximum=120, code="invalid_license")
    if license_name not in accepted_licenses:
        raise BrainV3CorpusError("Лицензия источника не входит в accepted_licenses манифеста.")
    license_url = _optional_http_url(payload["license_url"], "invalid_license_url")
    origin_url = _optional_http_url(payload["origin_url"], "invalid_origin_url")
    attribution = _require_string(payload["attribution"], maximum=1_000, code="invalid_attribution")
    if payload["approved_for_training"] is not True:
        raise BrainV3CorpusError("Источник не имеет явного approved_for_training=true.")
    if source_kind == "open_licensed" and (license_url is None or origin_url is None):
        raise BrainV3CorpusError("Открытый источник обязан содержать URL лицензии и происхождения.")
    return CorpusSource(
        source_id=source_id,
        raw_path=_safe_relative_file(base, raw_path),
        raw_sha256=checksum,
        source_kind=source_kind,
        license_name=license_name,
        license_url=license_url,
        origin_url=origin_url,
        attribution=attribution,
    )


def load_corpus_manifest(path: Path = DEFAULT_MANIFEST) -> CorpusManifest:
    """Загружает строгий манифест, не читая источники до проверки правил."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Не найден манифест корпуса Brain v3: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise BrainV3CorpusError("Манифест корпуса содержит некорректный JSON.") from error
    required = {
        "format_version",
        "corpus_name",
        "language_scope",
        "accepted_licenses",
        "quality_gate",
        "sources",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise BrainV3CorpusError("Манифест корпуса содержит недопустимые или отсутствующие поля.")
    if payload["format_version"] != FORMAT_VERSION:
        raise BrainV3CorpusError("Неизвестная версия манифеста корпуса Brain v3.")
    corpus_name = _require_string(payload["corpus_name"], maximum=120, code="invalid_corpus_name")
    language_scope = payload["language_scope"]
    if not isinstance(language_scope, list) or language_scope != ["ru"]:
        raise BrainV3CorpusError("Первая версия корпуса должна иметь language_scope [\"ru\"].")
    accepted_licenses_raw = payload["accepted_licenses"]
    if not isinstance(accepted_licenses_raw, list) or not accepted_licenses_raw:
        raise BrainV3CorpusError("accepted_licenses должен быть непустым массивом.")
    accepted_licenses = frozenset(
        _require_string(item, maximum=120, code="invalid_accepted_license")
        for item in accepted_licenses_raw
    )
    if len(accepted_licenses) != len(accepted_licenses_raw):
        raise BrainV3CorpusError("accepted_licenses содержит повторяющиеся лицензии.")
    quality_gate = _parse_quality_gate(payload["quality_gate"])
    raw_sources = payload["sources"]
    if not isinstance(raw_sources, list):
        raise BrainV3CorpusError("sources манифеста должен быть массивом.")
    sources = tuple(
        _parse_source(item, base=resolved.parent, accepted_licenses=accepted_licenses)
        for item in raw_sources
    )
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise BrainV3CorpusError("В манифесте повторяется source_id.")
    paths = [source.raw_path for source in sources]
    if len(paths) != len(set(paths)):
        raise BrainV3CorpusError("В манифесте один raw_path указан несколько раз.")
    return CorpusManifest(
        corpus_name=corpus_name,
        language_scope=("ru",),
        accepted_licenses=accepted_licenses,
        quality_gate=quality_gate,
        sources=sources,
    )


def text_risk_code(text: str, *, maximum: int) -> str | None:
    """Возвращает код запрета без сохранения опасного или личного текста."""
    if not text.strip() or len(text) > maximum or "\x00" in text:
        return "invalid_text_length"
    if _URL.search(text):
        return "url_like"
    if _EMAIL.search(text):
        return "email_like"
    if _PHONE.search(text):
        return "phone_like"
    if _SECRET.search(text):
        return "credential_like"
    if any(pattern.search(text) for pattern in _INSTRUCTION_LIKE):
        return "instruction_like"
    return None


def _parse_properties(value: Any) -> tuple[str, ...]:
    """Проверяет ожидаемые свойства без возможности добавить скрытую команду."""
    if not isinstance(value, list) or not value:
        raise BrainV3RecordError("invalid_expected_response_properties")
    properties = tuple(
        _require_string(item, maximum=80, code="invalid_expected_response_property")
        for item in value
    )
    if len(properties) != len(set(properties)) or not set(properties) <= ALLOWED_RESPONSE_PROPERTIES:
        raise BrainV3RecordError("invalid_expected_response_properties")
    return properties


def _verify_provenance(value: Any, source: CorpusSource) -> None:
    """Требует совпадения лицензии записи с заранее проверенным манифестом."""
    fields = {"source_id", "license", "license_url", "origin_url", "attribution"}
    if not isinstance(value, dict) or set(value) != fields:
        raise BrainV3RecordError("invalid_provenance")
    if value != {
        "source_id": source.source_id,
        "license": source.license_name,
        "license_url": source.license_url,
        "origin_url": source.origin_url,
        "attribution": source.attribution,
    }:
        raise BrainV3RecordError("provenance_manifest_mismatch")


def _simhash(text: str) -> tuple[int, int]:
    """Строит компактную подпись близости по словам, не сохраняя сам текст."""
    tokens = _WORD.findall(_normalise_text(text))
    if not tokens:
        raise BrainV3RecordError("empty_content")
    weights = [0] * 64
    for token in tokens:
        value = int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:8], "big")
        for bit in range(64):
            weights[bit] += 1 if value & (1 << bit) else -1
    signature = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            signature |= 1 << bit
    return signature, len(tokens)


def duplicate_signature(text: str) -> DuplicateSignature:
    """Строит подпись повторов тем же алгоритмом, что использует audit корпуса."""
    simhash, token_count = _simhash(text)
    return DuplicateSignature(simhash=simhash, token_count=token_count)


def _signature_buckets(signature: int) -> tuple[tuple[int, int], ...]:
    """Даёт четыре индекса кандидатов; hamming <= 3 точно делит один bucket."""
    return tuple((index, (signature >> (index * 16)) & 0xFFFF) for index in range(4))


def _hamming_distance(left: int, right: int) -> int:
    """Считает количество отличающихся битов двух 64-bit подписей."""
    return (left ^ right).bit_count()


def _parse_record(payload: Any, source: CorpusSource) -> AcceptedRecord:
    """Проверяет одну строку корпуса как данные и возвращает только метаданные."""
    if not isinstance(payload, dict):
        raise BrainV3RecordError("record_not_object")
    record_type = payload.get("record_type")
    common = {
        "format_version",
        "id",
        "record_type",
        "provenance",
        "language",
        "layer",
        "split",
        "topic",
        "quality_tier",
        "expected_response_properties",
        "content_sha256",
    }
    if record_type == "dialogue":
        expected_fields = common | {"dialogue_goal", "emotional_tone", "turns"}
    elif record_type == "language_document":
        expected_fields = common | {"document_kind", "text"}
    else:
        raise BrainV3RecordError("invalid_record_type")
    if set(payload) != expected_fields:
        raise BrainV3RecordError("unexpected_record_fields")
    if payload["format_version"] != FORMAT_VERSION:
        raise BrainV3RecordError("invalid_record_format_version")
    identifier = _require_identifier(payload["id"], "invalid_record_id")
    language = _require_string(payload["language"], maximum=12, code="invalid_language")
    if language != "ru":
        raise BrainV3RecordError("unsupported_language")
    layer = _require_string(payload["layer"], maximum=32, code="invalid_layer")
    if layer not in ALLOWED_LAYERS:
        raise BrainV3RecordError("invalid_layer")
    if record_type == "language_document" and layer != "language":
        raise BrainV3RecordError("document_layer_mismatch")
    split = _require_string(payload["split"], maximum=20, code="invalid_split")
    if split not in ALLOWED_SPLITS:
        raise BrainV3RecordError("invalid_split")
    _require_string(payload["topic"], maximum=96, code="invalid_topic")
    quality_tier = _require_string(payload["quality_tier"], maximum=40, code="invalid_quality_tier")
    if quality_tier not in ALLOWED_QUALITY_TIERS:
        raise BrainV3RecordError("invalid_quality_tier")
    properties = _parse_properties(payload["expected_response_properties"])
    # Свойства финального результата инструмента допустимы только в tool-слое.
    # Так обычный диалог не сможет случайно заявить, что получил свежий факт.
    if TOOL_RESULT_PROPERTIES.intersection(properties) and layer != "tool":
        raise BrainV3RecordError("tool_result_properties_require_tool_layer")
    if "no_action_promise" in properties and "tool_result_returned" not in properties:
        raise BrainV3RecordError("no_action_promise_requires_tool_result")
    _verify_provenance(payload["provenance"], source)
    stored_hash = _require_string(payload["content_sha256"], maximum=64, code="invalid_content_sha256")
    if _SHA256.fullmatch(stored_hash) is None or stored_hash != content_sha256(payload):
        raise BrainV3RecordError("content_hash_mismatch")

    dialogue_turns = 0
    language_characters = 0
    if record_type == "dialogue":
        _require_string(payload["dialogue_goal"], maximum=96, code="invalid_dialogue_goal")
        _require_string(payload["emotional_tone"], maximum=64, code="invalid_emotional_tone")
        turns = payload["turns"]
        if not isinstance(turns, list) or not 2 <= len(turns) <= MAX_TURNS_PER_DIALOGUE:
            raise BrainV3RecordError("invalid_turn_count")
        normalized_turns: list[str] = []
        for index, turn in enumerate(turns):
            if not isinstance(turn, dict) or set(turn) != {"role", "text"}:
                raise BrainV3RecordError("invalid_turn")
            expected_role = ALLOWED_ROLES[index % 2]
            if turn.get("role") != expected_role:
                raise BrainV3RecordError("turn_roles_must_alternate")
            text = turn.get("text")
            if not isinstance(text, str):
                raise BrainV3RecordError("invalid_turn_text")
            risk = text_risk_code(text, maximum=MAX_TEXT_LENGTH)
            if risk is not None:
                raise BrainV3RecordError(risk)
            normalized_turns.append(f"{expected_role}:{_normalise_text(text)}")
        content = "\n".join(normalized_turns)
        dialogue_turns = len(turns)
    else:
        _require_string(payload["document_kind"], maximum=96, code="invalid_document_kind")
        text = payload["text"]
        if not isinstance(text, str):
            raise BrainV3RecordError("invalid_document_text")
        risk = text_risk_code(text, maximum=MAX_DOCUMENT_LENGTH)
        if risk is not None:
            raise BrainV3RecordError(risk)
        content = _normalise_text(text)
        language_characters = len(text)

    signature = duplicate_signature(content)
    return AcceptedRecord(
        identifier=identifier,
        source_id=source.source_id,
        split=split,
        record_type=record_type,
        content_fingerprint=_fingerprint(content),
        simhash=signature.simhash,
        token_count=signature.token_count,
        dialogue_turns=dialogue_turns,
        language_characters=language_characters,
    )


def _read_jsonl(path: Path) -> Iterable[tuple[int, Any]]:
    """Потоково читает ограниченный JSONL без извлечения данных в память."""
    if not path.is_file():
        raise BrainV3CorpusError(f"Не найден файл источника: {path}")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise BrainV3CorpusError("Источник превышает безопасный лимит размера.")
    with path.open("rb") as source:
        for line_number, raw_line in enumerate(source, start=1):
            if line_number > MAX_RECORDS_PER_SOURCE:
                raise BrainV3CorpusError("Источник превышает безопасный лимит числа записей.")
            if len(raw_line) > MAX_RECORD_BYTES:
                raise BrainV3CorpusError("JSONL-строка источника превышает безопасный лимит.")
            if not raw_line.strip():
                continue
            try:
                yield line_number, json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise BrainV3CorpusError(
                    f"Источник содержит некорректный UTF-8 или JSON в строке {line_number}."
                ) from error


def audit_corpus(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    """Аудирует все approved источники и возвращает отчёт без исходных текстов."""
    manifest = load_corpus_manifest(manifest_path)
    seen_ids: set[str] = set()
    exact_records: dict[str, AcceptedRecord] = {}
    near_index = NearDuplicateIndex()
    counts_by_split: Counter[str] = Counter()
    counts_by_source: Counter[str] = Counter()
    counts_by_type: Counter[str] = Counter()
    rejections: Counter[str] = Counter()
    rejected_records = 0
    accepted_records = 0
    dialogue_turns = 0
    language_characters = 0
    cross_split_exact_duplicates = 0
    cross_split_near_candidates = 0

    for source in manifest.sources:
        if file_sha256(source.raw_path) != source.raw_sha256:
            raise BrainV3CorpusError(
                f"Контрольная сумма источника не совпала: {source.source_id}."
            )
        for _, payload in _read_jsonl(source.raw_path):
            try:
                record = _parse_record(payload, source)
                if record.identifier in seen_ids:
                    raise BrainV3RecordError("duplicate_record_id")
                seen_ids.add(record.identifier)
                previous_exact = exact_records.get(record.content_fingerprint)
                if previous_exact is not None:
                    if previous_exact.split != record.split:
                        cross_split_exact_duplicates += 1
                    raise BrainV3RecordError("exact_duplicate")

                previous_near = near_index.find(
                    DuplicateSignature(
                        simhash=record.simhash,
                        token_count=record.token_count,
                    )
                )
                if isinstance(previous_near, AcceptedRecord):
                    if previous_near.split != record.split:
                        cross_split_near_candidates += 1
                    raise BrainV3RecordError("near_duplicate_candidate")
            except BrainV3RecordError as error:
                rejected_records += 1
                rejections[error.code] += 1
                continue

            exact_records[record.content_fingerprint] = record
            near_index.add(
                record,
                DuplicateSignature(
                    simhash=record.simhash,
                    token_count=record.token_count,
                ),
            )
            accepted_records += 1
            counts_by_split[record.split] += 1
            counts_by_source[record.source_id] += 1
            counts_by_type[record.record_type] += 1
            dialogue_turns += record.dialogue_turns
            language_characters += record.language_characters

    total_records = accepted_records + rejected_records
    rejection_ratio = rejected_records / total_records if total_records else 0.0
    gate_reasons: list[str] = []
    if not manifest.sources:
        gate_reasons.append("no_approved_sources")
    if accepted_records == 0:
        gate_reasons.append("no_accepted_records")
    if manifest.quality_gate.require_all_splits:
        missing_splits = sorted(ALLOWED_SPLITS - set(counts_by_split))
        if missing_splits:
            gate_reasons.append("missing_required_splits")
    if dialogue_turns < manifest.quality_gate.minimum_unique_dialogue_turns:
        gate_reasons.append("insufficient_unique_dialogue_turns")
    if language_characters < manifest.quality_gate.minimum_language_characters:
        gate_reasons.append("insufficient_language_characters")
    if rejection_ratio > manifest.quality_gate.maximum_rejected_record_ratio:
        gate_reasons.append("rejected_record_ratio_exceeded")

    return {
        "format_version": FORMAT_VERSION,
        "corpus_name": manifest.corpus_name,
        "language_scope": list(manifest.language_scope),
        "sources_declared": len(manifest.sources),
        "records_total": total_records,
        "records_accepted": accepted_records,
        "records_rejected": rejected_records,
        "rejected_record_ratio": round(rejection_ratio, 6),
        "rejections": dict(sorted(rejections.items())),
        "accepted_by_split": dict(sorted(counts_by_split.items())),
        "accepted_by_source": dict(sorted(counts_by_source.items())),
        "accepted_by_type": dict(sorted(counts_by_type.items())),
        "unique_dialogue_turns": dialogue_turns,
        "language_characters": language_characters,
        "cross_split_exact_duplicates": cross_split_exact_duplicates,
        "cross_split_near_duplicate_candidates": cross_split_near_candidates,
        "quality_gate": {
            "minimum_unique_dialogue_turns": manifest.quality_gate.minimum_unique_dialogue_turns,
            "minimum_language_characters": manifest.quality_gate.minimum_language_characters,
            "maximum_rejected_record_ratio": manifest.quality_gate.maximum_rejected_record_ratio,
            "require_all_splits": manifest.quality_gate.require_all_splits,
        },
        "training_allowed": not gate_reasons,
        "training_block_reasons": gate_reasons,
    }


def summary_markdown(report: dict[str, Any]) -> str:
    """Пишет понятный data-quality отчёт без фрагментов исходных сообщений."""
    reasons = report["training_block_reasons"]
    status = "разрешено" if report["training_allowed"] else "заблокировано"
    reasons_text = ", ".join(reasons) if reasons else "нет"
    return "\n".join(
        [
            "# Brain v3 — аудит корпуса",
            "",
            f"Корпус: `{report['corpus_name']}`.",
            f"Источников в манифесте: {report['sources_declared']}.",
            f"Записей: {report['records_total']}; принято: {report['records_accepted']}; отклонено: {report['records_rejected']}.",
            f"Уникальных диалоговых ходов: {report['unique_dialogue_turns']}.",
            f"Символов языковых документов: {report['language_characters']}.",
            f"Точное пересечение split: {report['cross_split_exact_duplicates']}; кандидаты близких пересечений: {report['cross_split_near_duplicate_candidates']}.",
            "",
            "## Решение",
            "",
            f"Передача корпуса в генеративное обучение: **{status}**.",
            f"Причины блокировки: {reasons_text}.",
            "",
            "Отчёт намеренно не содержит тексты сообщений, URL, личные данные или готовые ответы. Сырые источники не изменялись.",
            "",
        ]
    )
