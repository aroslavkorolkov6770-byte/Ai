"""Безопасно готовит staging-кандидат из русского MOT/VOA release.

Этот модуль намеренно не является универсальным загрузчиком архивов. Он знает
только один предварительно проверенный русский asset Multilingual Open Text
(MOT), получает его через строго ограниченный HTTPS redirect и читает ``.tgz``
потоково без распаковки путей на диск. Результат всегда остаётся в staging и не
меняет active manifest, чат или обучение.

Все поля внешнего архива считаются данными. В отчёты попадают только счётчики,
версии и контрольные суммы; текст новостей, URL источника внутри статьи и
свободные метаданные архива не выводятся.
"""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .brain_v3_corpus import (
    BrainV3RecordError,
    MAX_DOCUMENT_LENGTH,
    NearDuplicateIndex,
    content_sha256,
    duplicate_signature,
    file_sha256,
    text_risk_code,
)


# Непереопределяемая внешняя граница источника. Release tag, имя asset и размер
# проверены по официальному MOT repository/release до написания импортёра.
SOURCE_RELEASE_TAG = "v1.11"
SOURCE_ASSET_NAME = "rus_golosameriki.tgz"
ARCHIVE_SNAPSHOT_NAME = f"mot-rus-golosameriki-{SOURCE_RELEASE_TAG}.tgz"
MOT_RUS_RELEASE_URL = (
    "https://github.com/bltlab/mot/releases/download/"
    f"{SOURCE_RELEASE_TAG}/{SOURCE_ASSET_NAME}"
)
MOT_REDIRECT_HOST = "release-assets.githubusercontent.com"
MOT_REDIRECT_PATH_PREFIX = "/github-production-release-asset/"
EXPECTED_ARCHIVE_BYTES = 444_157_097
MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 45.0

SOURCE_ID = "mot-rus-golosameriki-cc-by-v1"
SOURCE_LICENSE = "CC-BY-4.0"
SOURCE_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
SOURCE_ORIGIN_URL = "https://github.com/bltlab/mot"
SOURCE_ATTRIBUTION = (
    "Palen-Michel, Kim, and Lignos (2022), Multilingual Open Text (MOT), "
    "Russian VOA release v1.11"
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_ARCHIVE_ROOT = ROOT / "data" / "brain_v3" / "source_archives"
DEFAULT_STAGING_ROOT = ROOT / "data" / "brain_v3" / "staging"
DEFAULT_ARCHIVE_PATH = DEFAULT_SOURCE_ARCHIVE_ROOT / ARCHIVE_SNAPSHOT_NAME

# Защита от archive bomb и от записи raw JSONL, который будущий corpus-audit не
# сможет прочитать из-за своего лимита 512 MiB. Вывод строго меньше него.
MAX_ARCHIVE_MEMBERS = 1_000_000
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_EXPANDED_MEMBER_BYTES = 4 * 1024 * 1024 * 1024
MAX_OUTPUT_BYTES = 480 * 1024 * 1024
MAX_RECORDS = 500_000
MAX_PARAGRAPHS_PER_DOCUMENT = 2_000


class MOTRussianError(ValueError):
    """Останавливает загрузку или import до появления непроверенного output."""


class _MOTRedirectHandler(HTTPRedirectHandler):
    """Разрешает ровно один HTTPS redirect только к GitHub release asset host."""

    def __init__(self) -> None:
        super().__init__()
        self.redirects = 0

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        redirect_url: str,
    ) -> Request | None:
        """Не даёт source URL перейти на произвольный сервер или второй redirect."""
        if self.redirects >= 1 or not is_allowed_release_redirect(redirect_url):
            return None
        self.redirects += 1
        return super().redirect_request(request, file_pointer, code, message, headers, redirect_url)


@dataclass(frozen=True)
class MOTRussianDownloadSummary:
    """Фиксирует только постоянный source URL и hash локального archive snapshot."""

    archive_path: str
    archive_sha256: str
    archive_bytes: int

    def as_dict(self) -> dict[str, Any]:
        """Возвращает отчёт без временного signed redirect URL."""
        return {
            "format_version": 1,
            "source_id": SOURCE_ID,
            "source_url": MOT_RUS_RELEASE_URL,
            "origin_url": SOURCE_ORIGIN_URL,
            "release_tag": SOURCE_RELEASE_TAG,
            "asset_name": SOURCE_ASSET_NAME,
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "attribution": SOURCE_ATTRIBUTION,
            "archive_path": self.archive_path,
            "archive_sha256": self.archive_sha256,
            "archive_bytes": self.archive_bytes,
            "active_manifest_updated": False,
            "training_started": False,
        }


@dataclass(frozen=True)
class MOTRussianImportSummary:
    """Содержит безопасную статистику staging import без текста documents."""

    archive_path: str
    archive_sha256: str
    archive_bytes: int
    output_path: str
    output_sha256: str
    archive_members_seen: int
    json_members_read: int
    records_written: int
    records_rejected: int
    output_limit_reached: bool
    split_counts: dict[str, int]
    rejections: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        """Сериализует только provenance, hashes и счётчики import-процесса."""
        return {
            "format_version": 1,
            "source_id": SOURCE_ID,
            "source_url": MOT_RUS_RELEASE_URL,
            "origin_url": SOURCE_ORIGIN_URL,
            "release_tag": SOURCE_RELEASE_TAG,
            "asset_name": SOURCE_ASSET_NAME,
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "attribution": SOURCE_ATTRIBUTION,
            "archive_path": self.archive_path,
            "archive_sha256": self.archive_sha256,
            "archive_bytes": self.archive_bytes,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "archive_members_seen": self.archive_members_seen,
            "json_members_read": self.json_members_read,
            "records_written": self.records_written,
            "records_rejected": self.records_rejected,
            "output_limit_reached": self.output_limit_reached,
            "split_counts": self.split_counts,
            "rejections": self.rejections,
            "candidate_is_active": False,
            "active_manifest_updated": False,
            "training_started": False,
        }


@dataclass(frozen=True)
class MOTRussianImportProgress:
    """Передаёт безопасный progress import без текста archive или JSONL."""

    archive_members_seen: int
    json_members_read: int
    records_written: int
    records_rejected: int
    output_bytes: int

    def as_dict(self) -> dict[str, int]:
        """Возвращает только счётчики, пригодные для консоли и мониторинга."""
        return {
            "archive_members_seen": self.archive_members_seen,
            "json_members_read": self.json_members_read,
            "records_written": self.records_written,
            "records_rejected": self.records_rejected,
            "output_bytes": self.output_bytes,
        }


def _safe_child(path: Path, root: Path, *, code: str) -> Path:
    """Разрешает запись/чтение только внутри заранее названного локального root."""
    resolved_root = root.expanduser().resolve()
    resolved_path = path.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise MOTRussianError(code) from error
    return resolved_path


def validate_fixed_release_url(url: str) -> None:
    """Принимает только неизменяемый URL заранее проверенного release asset."""
    if url != MOT_RUS_RELEASE_URL:
        raise MOTRussianError("Разрешён только зафиксированный MOT Russian release URL.")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.path != f"/bltlab/mot/releases/download/{SOURCE_RELEASE_TAG}/{SOURCE_ASSET_NAME}"
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise MOTRussianError("MOT Russian release URL имеет недопустимый формат.")


def is_allowed_release_redirect(url: str) -> bool:
    """Проверяет GitHub-controlled release asset redirect без логирования token query."""
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == MOT_REDIRECT_HOST
        and parsed.path.startswith(MOT_REDIRECT_PATH_PREFIX)
        and bool(parsed.query)
        and not parsed.params
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )


def _prepare_archive_destination(path: Path, archive_root: Path) -> Path:
    """Готовит новый immutable archive snapshot без возможности overwrite."""
    destination = _safe_child(
        path,
        archive_root,
        code="MOT archive разрешён только внутри data/brain_v3/source_archives/.",
    )
    if destination.name != ARCHIVE_SNAPSHOT_NAME or destination.suffix.lower() != ".tgz":
        raise MOTRussianError("MOT archive destination должен иметь фиксированное имя .tgz.")
    if destination.exists():
        raise MOTRussianError("MOT archive уже существует; перезапись запрещена.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def download_mot_russian_archive(
    destination: Path = DEFAULT_ARCHIVE_PATH,
    *,
    archive_root: Path = DEFAULT_SOURCE_ARCHIVE_ROOT,
) -> MOTRussianDownloadSummary:
    """Скачивает один fixed MOT asset атомарно и только после строгих проверок.

    Временный redirect URL от GitHub никогда не сохраняется или печатается.
    Любой второй redirect, другой host, неожиданный content type или размер
    останавливают operation до появления destination.
    """
    validate_fixed_release_url(MOT_RUS_RELEASE_URL)
    output_path = _prepare_archive_destination(destination, archive_root)
    redirect_handler = _MOTRedirectHandler()
    request = Request(
        MOT_RUS_RELEASE_URL,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "Ai-Brain-v3-MOT-Russian-staging/0.1",
        },
        method="GET",
    )
    temporary_path: Path | None = None
    try:
        opener = build_opener(redirect_handler)
        try:
            response = opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        except (HTTPError, URLError, OSError) as error:
            raise MOTRussianError("Не удалось получить MOT Russian release asset.") from error
        with response:
            final_url = response.geturl()
            content_type = response.headers.get_content_type().lower()
            content_length = response.headers.get("Content-Length")
            if redirect_handler.redirects != 1 or not is_allowed_release_redirect(final_url):
                raise MOTRussianError("MOT release redirect не прошёл allowlist.")
            if content_type != "application/octet-stream":
                raise MOTRussianError("MOT release вернул неожиданный Content-Type.")
            if content_length is None or not content_length.isdecimal() or int(content_length) != EXPECTED_ARCHIVE_BYTES:
                raise MOTRussianError("MOT release имеет неожиданный размер archive.")

            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=output_path.parent,
                prefix=".mot-rus-download-",
                suffix=".tgz.tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                bytes_written = 0
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > MAX_ARCHIVE_BYTES:
                        raise MOTRussianError("MOT archive превысил безопасный лимит размера.")
                    temporary.write(chunk)

        if bytes_written != EXPECTED_ARCHIVE_BYTES:
            raise MOTRussianError("MOT archive скачан не полностью.")
        if output_path.exists():
            raise MOTRussianError("MOT archive появился во время загрузки; перезапись запрещена.")
        assert temporary_path is not None
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return MOTRussianDownloadSummary(
        archive_path=str(output_path),
        archive_sha256=file_sha256(output_path),
        archive_bytes=output_path.stat().st_size,
    )


def _safe_archive(path: Path, archive_root: Path) -> Path:
    """Проверяет immutable archive snapshot до запуска tar parser."""
    archive_path = _safe_child(
        path,
        archive_root,
        code="MOT import разрешён только для archive из data/brain_v3/source_archives/.",
    )
    if not archive_path.is_file() or archive_path.suffix.lower() != ".tgz":
        raise MOTRussianError("Ожидается существующий MOT Russian .tgz archive.")
    if archive_path.name != ARCHIVE_SNAPSHOT_NAME:
        raise MOTRussianError("MOT import разрешён только для зафиксированного archive name.")
    archive_bytes = archive_path.stat().st_size
    if archive_bytes != EXPECTED_ARCHIVE_BYTES or archive_bytes > MAX_ARCHIVE_BYTES:
        raise MOTRussianError("MOT archive не соответствует зафиксированному размеру release.")
    return archive_path


def _prepare_staging_destination(path: Path, staging_root: Path) -> Path:
    """Не позволяет import записать source вне staging или перезаписать JSONL."""
    destination = _safe_child(
        path,
        staging_root,
        code="MOT import разрешён только внутри data/brain_v3/staging/.",
    )
    if destination.suffix.lower() != ".jsonl":
        raise MOTRussianError("MOT staging output должен быть JSONL-файлом.")
    if destination.exists():
        raise MOTRussianError("MOT staging output уже существует; перезапись запрещена.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def _member_name_is_safe(name: str) -> bool:
    """Проверяет tar name, хотя member никогда не извлекается в файловую систему."""
    member_path = PurePosixPath(name)
    return bool(name) and not member_path.is_absolute() and ".." not in member_path.parts


def _normalise_text(value: str) -> str:
    """Сохраняет абзацные границы, но убирает лишние пробелы внутри абзацев."""
    return " ".join(value.split())


def _article_paragraphs(payload: Any) -> list[str] | None:
    """Берёт только заявленное MOT поле paragraphs, без URL и прочей metadata."""
    if not isinstance(payload, dict):
        return None
    raw_paragraphs = payload.get("paragraphs")
    if not isinstance(raw_paragraphs, list) or not raw_paragraphs or len(raw_paragraphs) > MAX_PARAGRAPHS_PER_DOCUMENT:
        return None
    paragraphs: list[str] = []
    for paragraph in raw_paragraphs:
        if not isinstance(paragraph, str):
            return None
        normalized = _normalise_text(paragraph)
        if normalized:
            paragraphs.append(normalized)
    return paragraphs or None


def _paragraph_groups(paragraphs: list[str]) -> list[str]:
    """Делит длинную статью на безопасные documents без разрыва абзаца."""
    groups: list[str] = []
    current: list[str] = []
    current_length = 0
    for paragraph in paragraphs:
        if len(paragraph) > MAX_DOCUMENT_LENGTH:
            continue
        separator_length = 2 if current else 0
        if current and current_length + separator_length + len(paragraph) > MAX_DOCUMENT_LENGTH:
            groups.append("\n\n".join(current))
            current = []
            current_length = 0
            separator_length = 0
        current.append(paragraph)
        current_length += separator_length + len(paragraph)
    if current:
        groups.append("\n\n".join(current))
    return groups


def _deterministic_split(article_digest: str, group_index: int) -> str:
    """Разносит documents стабильно, не завися от времени или порядка запуска."""
    bucket = hashlib.sha256(f"{SOURCE_ID}:{article_digest}:{group_index}".encode("utf-8")).digest()[0] % 100
    if bucket < 90:
        return "train"
    if bucket < 95:
        return "validation"
    return "test"


def _record(article_digest: str, group_index: int, text: str) -> dict[str, Any]:
    """Строит one language_document record с повторяемым id и content hash."""
    payload: dict[str, Any] = {
        "format_version": 1,
        "id": f"mot.rus.v1_11.{article_digest}.{group_index:03d}",
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
        "split": _deterministic_split(article_digest, group_index),
        "topic": "public_domain_news",
        "quality_tier": "license_verified",
        "expected_response_properties": ["relevant"],
        "document_kind": "public_domain_news_article",
        "text": text,
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _read_member(member_file: Any, member_size: int) -> bytes | None:
    """Читает bounded tar member и отличает неполное чтение от valid data."""
    raw = member_file.read(member_size + 1)
    if len(raw) != member_size or len(raw) > MAX_MEMBER_BYTES:
        return None
    return raw


def _emit_import_progress(
    callback: Callable[[MOTRussianImportProgress], None] | None,
    *,
    archive_members_seen: int,
    json_members_read: int,
    records_written: int,
    rejections: Counter[str],
    output_bytes: int,
) -> None:
    """Сообщает счётчики только по явному opt-in callback без исходного текста."""
    if callback is None:
        return
    callback(
        MOTRussianImportProgress(
            archive_members_seen=archive_members_seen,
            json_members_read=json_members_read,
            records_written=records_written,
            records_rejected=sum(rejections.values()),
            output_bytes=output_bytes,
        )
    )


def import_mot_russian(
    archive: Path = DEFAULT_ARCHIVE_PATH,
    destination: Path = DEFAULT_STAGING_ROOT / "mot-rus-golosameriki-cc-by-v0.1.jsonl",
    *,
    archive_root: Path = DEFAULT_SOURCE_ARCHIVE_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
    progress_callback: Callable[[MOTRussianImportProgress], None] | None = None,
    progress_every_members: int = 25_000,
) -> MOTRussianImportSummary:
    """Потоково преобразует fixed MOT archive в новый Brain v3 staging JSONL.

    Import не распаковывает tar members на диск и останавливается до activation.
    Внутри source удаляются unsafe documents, exact/near duplicates и records,
    которые не помещаются в детерминированный output limit. Исходный archive не
    меняется.
    """
    if isinstance(progress_every_members, bool) or not isinstance(progress_every_members, int) or progress_every_members <= 0:
        raise MOTRussianError("Интервал progress MOT import должен быть положительным целым числом.")
    archive_path = _safe_archive(archive, archive_root)
    output_path = _prepare_staging_destination(destination, staging_root)
    archive_sha256 = file_sha256(archive_path)
    rejections: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    exact_fingerprints: set[str] = set()
    near_duplicates = NearDuplicateIndex()
    archive_members_seen = 0
    json_members_read = 0
    records_written = 0
    expanded_member_bytes = 0
    output_bytes = 0
    output_limit_reached = False
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=".mot-rus-import-",
            suffix=".jsonl.tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            try:
                archive_stream = tarfile.open(archive_path, mode="r|gz")
            except (tarfile.TarError, OSError) as error:
                raise MOTRussianError("MOT archive не является корректным gzip tar.") from error
            with archive_stream:
                for member in archive_stream:
                    archive_members_seen += 1
                    if archive_members_seen > MAX_ARCHIVE_MEMBERS:
                        raise MOTRussianError("MOT archive превышает лимит числа members.")
                    if archive_members_seen % progress_every_members == 0:
                        _emit_import_progress(
                            progress_callback,
                            archive_members_seen=archive_members_seen,
                            json_members_read=json_members_read,
                            records_written=records_written,
                            rejections=rejections,
                            output_bytes=output_bytes,
                        )
                    if not member.isreg():
                        rejections["non_regular_member"] += 1
                        continue
                    if not _member_name_is_safe(member.name):
                        rejections["unsafe_member_name"] += 1
                        continue
                    if member.size <= 0 or member.size > MAX_MEMBER_BYTES:
                        rejections["invalid_member_size"] += 1
                        continue
                    if PurePosixPath(member.name).suffix.lower() != ".json":
                        rejections["non_json_member"] += 1
                        continue
                    expanded_member_bytes += member.size
                    if expanded_member_bytes > MAX_EXPANDED_MEMBER_BYTES:
                        raise MOTRussianError("MOT archive превышает безопасный лимит распакованных JSON bytes.")
                    member_file = archive_stream.extractfile(member)
                    if member_file is None:
                        rejections["member_read_failed"] += 1
                        continue
                    with member_file:
                        raw_document = _read_member(member_file, member.size)
                    if raw_document is None:
                        rejections["member_read_failed"] += 1
                        continue
                    json_members_read += 1
                    try:
                        source_payload = json.loads(raw_document.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        rejections["invalid_json"] += 1
                        continue
                    paragraphs = _article_paragraphs(source_payload)
                    if paragraphs is None:
                        rejections["invalid_article_paragraphs"] += 1
                        continue
                    groups = _paragraph_groups(paragraphs)
                    if not groups:
                        rejections["no_safe_paragraph_group"] += 1
                        continue
                    article_digest = hashlib.sha256(member.name.encode("utf-8")).hexdigest()[:24]
                    for group_index, text in enumerate(groups, start=1):
                        risk = text_risk_code(text, maximum=MAX_DOCUMENT_LENGTH)
                        if risk is not None:
                            rejections[risk] += 1
                            continue
                        fingerprint = hashlib.sha256(" ".join(text.casefold().split()).encode("utf-8")).hexdigest()
                        if fingerprint in exact_fingerprints:
                            rejections["exact_duplicate"] += 1
                            continue
                        try:
                            signature = duplicate_signature(text)
                        except BrainV3RecordError as error:
                            # text_risk_code отсеивает пустую строку, но строка
                            # из одних знаков препинания не содержит token для
                            # SimHash. Это обычный брак source, не причина
                            # останавливать весь archive import.
                            rejections[error.code] += 1
                            continue
                        if near_duplicates.find(signature) is not None:
                            rejections["near_duplicate_candidate"] += 1
                            continue
                        record = _record(article_digest, group_index, text)
                        encoded_record = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                        encoded_bytes = len(encoded_record.encode("utf-8")) + 1
                        if output_bytes + encoded_bytes > MAX_OUTPUT_BYTES:
                            output_limit_reached = True
                            break
                        if records_written >= MAX_RECORDS:
                            raise MOTRussianError("MOT import превышает лимит числа output records.")
                        temporary.write(encoded_record)
                        temporary.write("\n")
                        output_bytes += encoded_bytes
                        exact_fingerprints.add(fingerprint)
                        near_duplicates.add(article_digest, signature)
                        split_counts[record["split"]] += 1
                        records_written += 1
                    if output_limit_reached:
                        break

        if records_written == 0:
            raise MOTRussianError("MOT import не получил ни одного безопасного language document.")
        if output_path.exists():
            raise MOTRussianError("MOT staging output появился во время import; перезапись запрещена.")
        assert temporary_path is not None
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return MOTRussianImportSummary(
        archive_path=str(archive_path),
        archive_sha256=archive_sha256,
        archive_bytes=archive_path.stat().st_size,
        output_path=str(output_path),
        output_sha256=file_sha256(output_path),
        archive_members_seen=archive_members_seen,
        json_members_read=json_members_read,
        records_written=records_written,
        records_rejected=sum(rejections.values()),
        output_limit_reached=output_limit_reached,
        split_counts=dict(sorted(split_counts.items())),
        rejections=dict(sorted(rejections.items())),
    )


def download_summary_markdown(summary: MOTRussianDownloadSummary) -> str:
    """Пишет attribution и checksum download snapshot без signed redirect URL."""
    return "\n".join(
        [
            "# MOT Russian — фиксированный archive download",
            "",
            f"Источник: [{SOURCE_ID}]({MOT_RUS_RELEASE_URL}).",
            f"Лицензия: [{SOURCE_LICENSE}]({SOURCE_LICENSE_URL}).",
            f"Release / asset: `{SOURCE_RELEASE_TAG}` / `{SOURCE_ASSET_NAME}`.",
            f"Размер archive: {summary.archive_bytes} bytes.",
            f"SHA-256 archive: `{summary.archive_sha256}`.",
            "",
            "Загрузка создала только локальный source archive snapshot. Active manifest, обучение и чат не менялись.",
            "",
        ]
    )


def import_summary_markdown(summary: MOTRussianImportSummary) -> str:
    """Пишет staging import summary без содержания русских новостей."""
    return "\n".join(
        [
            "# MOT Russian — staging import",
            "",
            f"Источник: [{SOURCE_ID}]({MOT_RUS_RELEASE_URL}).",
            f"Лицензия: [{SOURCE_LICENSE}]({SOURCE_LICENSE_URL}).",
            f"SHA-256 archive: `{summary.archive_sha256}`.",
            f"SHA-256 staging JSONL: `{summary.output_sha256}`.",
            f"Members: {summary.archive_members_seen}; JSON прочитано: {summary.json_members_read}.",
            f"Записей создано: {summary.records_written}; отклонено: {summary.records_rejected}; split: {summary.split_counts}.",
            f"Достигнут выходной лимит: `{summary.output_limit_reached}`; причины: {summary.rejections}.",
            "",
            "Output остаётся staging-кандидатом. Он не меняет active manifest и перед любым promotion обязан пройти independent audit и active preflight.",
            "",
        ]
    )
