"""Безопасно принимает закреплённый Russian MultiWOZ только в Brain v3 staging.

Адаптер не клонирует repository, не запускает его Python-код и не добавляет
источник в active manifest. Он получает только заранее перечисленные текстовые
файлы exact commit, проверяет Git blob SHA-1 и размер каждого файла, очищает
служебные slot/reference значения и создаёт новый JSONL-кандидат в staging.
Активация, обучение и работающий API остаются отдельными шагами.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .brain_v3_corpus import (
    MAX_TEXT_LENGTH,
    MAX_TURNS_PER_DIALOGUE,
    NearDuplicateIndex,
    content_sha256,
    duplicate_signature,
    file_sha256,
    text_risk_code,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STAGING_ROOT = ROOT / "data" / "brain_v3" / "staging"
DEFAULT_CANDIDATE = DEFAULT_STAGING_ROOT / "russian-multiwoz-mit-v0.3.jsonl"
DEFAULT_REPORT_DIRECTORY = ROOT / "evaluation" / "results" / "russian-multiwoz-staging-v0.3"

SOURCE_ID = "russian-multiwoz-mit-v1"
SOURCE_LICENSE = "MIT"
SOURCE_LICENSE_URL = "https://github.com/vonCount/Russian-MultiWOZ/blob/1bd77673533e568ba0746ee304f758296f8dc77f/LICENSE"
SOURCE_ORIGIN_URL = "https://github.com/vonCount/Russian-MultiWOZ/tree/1bd77673533e568ba0746ee304f758296f8dc77f"
SOURCE_ATTRIBUTION = "Nikitin and Kopachinskaya (2021), Russian MultiWOZ, HSE; MIT repository snapshot"
SOURCE_REVISION = "1bd77673533e568ba0746ee304f758296f8dc77f"
SOURCE_HOST = "raw.githubusercontent.com"
SOURCE_REPOSITORY = "vonCount/Russian-MultiWOZ"

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_NETWORK_RETRIES = 2
RETRY_DELAY_SECONDS = 1.0

_SPACE = re.compile(r"\s+")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_SQUARE_DETAIL = re.compile(r"\[[^\]\r\n]{1,160}\]")
_REFERENCE_CODE = re.compile(r"\b[A-Z0-9]{6,}\b")
_POSTCODE = re.compile(r"\b[A-Za-z]{1,3}\d[A-Za-z0-9]{2,7}\b")
_TIME_VALUE = re.compile(r"\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b")
_PHONE_VALUE = re.compile(r"(?<!\w)(?:\+?\d[\d()\-\s]{8,}\d)(?!\w)")


@dataclass(frozen=True)
class SourceFile:
    """Описывает один заранее проверенный Git blob, а не произвольный URL."""

    split: str
    relative_path: str
    git_blob_sha1: str
    expected_bytes: int


SOURCE_FILES = (
    SourceFile("validation", "interim/v1/russian/dev_dialogues_001_ru.txt", "6ea914b8f33b300f7c86f48b324099ef3e1bb2d7", 957_981),
    SourceFile("validation", "interim/v1/russian/dev_dialogues_002_ru.txt", "6668a3ad0b423006529503e8c797b9ca5e86d56c", 884_034),
    SourceFile("test", "interim/v1/russian/test_dialogues_001_ru.txt", "754637d13ea99c72d2324b52eecea0cbc6327ab8", 953_289),
    SourceFile("test", "interim/v1/russian/test_dialogues_002_ru.txt", "09722b849223d576ce88fa38b876314439fd5a58", 884_349),
    SourceFile("train", "interim/v1/russian/train_dialogues_001_ru.txt", "6df4120118de2b1f6bcccf2480c6e68975925455", 867_639),
    SourceFile("train", "interim/v1/russian/train_dialogues_002_ru.txt", "6223ecea6c289e3d6fe3ca5781cd48b91fef89ea", 865_378),
    SourceFile("train", "interim/v1/russian/train_dialogues_003_ru.txt", "aaa8e5267c195b1efc2856e033a5992bb61d9f65", 807_398),
    SourceFile("train", "interim/v1/russian/train_dialogues_004_ru.txt", "0f69357777d2e2e50c7a4f5d1f409fd5269a6767", 835_299),
    SourceFile("train", "interim/v1/russian/train_dialogues_005_ru.txt", "7c6f9752a2dbddff7644fb4817bb907bf6ba8e9b", 861_120),
    SourceFile("train", "interim/v1/russian/train_dialogues_006_ru.txt", "bf430c48f77a7811333a42b73f915d9a279e7984", 839_618),
    SourceFile("train", "interim/v1/russian/train_dialogues_007_ru.txt", "a0b6a67c2dee2db084119a612d4194eb28a81fe2", 843_944),
    SourceFile("train", "interim/v1/russian/train_dialogues_008_ru.txt", "340d4f851d02e647281cb6a7aa34f868fac56361", 828_333),
    SourceFile("train", "interim/v1/russian/train_dialogues_009_ru.txt", "9551dd5a7aca7cc49cac783f9acb093b95af0b2b", 825_948),
    SourceFile("train", "interim/v1/russian/train_dialogues_010_ru.txt", "5dec4ea209f10a8bf4d02272bb7279a87dcf7942", 855_368),
    SourceFile("train", "interim/v1/russian/train_dialogues_011_ru.txt", "784a54e2ec6d1db28c6eb84056b399ebb164a268", 839_802),
    SourceFile("train", "interim/v1/russian/train_dialogues_012_ru.txt", "e11cc9d9970b241bc91620f2bf66124f40b11f17", 872_537),
    SourceFile("train", "interim/v1/russian/train_dialogues_013_ru.txt", "c57c44fb27c446c4e87977414d9f87220971e2b4", 863_588),
    SourceFile("train", "interim/v1/russian/train_dialogues_014_ru.txt", "375301a74ce31cef7c2abdd2850afc18c38c49ff", 835_560),
    SourceFile("train", "interim/v1/russian/train_dialogues_015_ru.txt", "1aa1cc791b96fd05192c8b2ba202c4bf971bc986", 845_458),
    SourceFile("train", "interim/v1/russian/train_dialogues_016_ru.txt", "35083fcb8e2fe2d200e507d75f96822e3f19a9ef", 829_010),
    SourceFile("train", "interim/v1/russian/train_dialogues_017_ru.txt", "f2813ab93b1d878be0895f2d89a4b0186d108948", 407_146),
)

RawFetcher = Callable[[str, float], bytes]
Sleeper = Callable[[float], None]


class RussianMultiWOZError(ValueError):
    """Останавливает intake до появления частичного или непроверенного кандидата."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Запрещает источнику подменять exact raw URL перенаправлением."""

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        redirect_url: str,
    ) -> Request | None:
        """Отклоняет любой редирект до чтения потенциально другого источника."""
        return None


@dataclass(frozen=True)
class RussianMultiWOZSummary:
    """Содержит только метрики import без реплик, адресов или booking-кодов."""

    raw_inventory_sha256: str
    raw_files_seen: int
    raw_bytes_seen: int
    dialogues_seen: int
    records_written: int
    dialogue_turns_written: int
    records_rejected: int
    rejections: dict[str, int]
    output_path: str
    output_sha256: str

    def as_dict(self) -> dict[str, Any]:
        """Возвращает serializable provenance и счётчики staging-приёма."""
        return {
            "format_version": 1,
            "mode": "staging_dialogue_import",
            "source_id": SOURCE_ID,
            "source_url": SOURCE_ORIGIN_URL,
            "source_revision": SOURCE_REVISION,
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "attribution": SOURCE_ATTRIBUTION,
            "raw_inventory_sha256": self.raw_inventory_sha256,
            "raw_files_seen": self.raw_files_seen,
            "raw_bytes_seen": self.raw_bytes_seen,
            "dialogues_seen": self.dialogues_seen,
            "records_written": self.records_written,
            "dialogue_turns_written": self.dialogue_turns_written,
            "records_rejected": self.records_rejected,
            "rejections": self.rejections,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "candidate_is_active": False,
            "active_manifest_updated": False,
            "training_started": False,
        }


def _source_url(source_file: SourceFile) -> str:
    """Строит один exact raw URL исключительно из закреплённого descriptor."""
    return f"https://{SOURCE_HOST}/{SOURCE_REPOSITORY}/{SOURCE_REVISION}/{source_file.relative_path}"


def _git_blob_sha1(payload: bytes) -> str:
    """Проверяет raw file тем же Git blob SHA-1, что указан в pinned tree."""
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def validate_fixed_source_url(url: str) -> SourceFile:
    """Принимает только один URL из hard-coded pinned file inventory."""
    if not isinstance(url, str) or not url or len(url) > 1_000:
        raise RussianMultiWOZError("Russian MultiWOZ URL имеет недопустимый формат.")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != SOURCE_HOST
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RussianMultiWOZError("Разрешён только закреплённый HTTPS raw URL Russian MultiWOZ.")
    for source_file in SOURCE_FILES:
        if parsed.path == f"/{SOURCE_REPOSITORY}/{SOURCE_REVISION}/{source_file.relative_path}":
            return source_file
    raise RussianMultiWOZError("URL не входит в закреплённый Russian MultiWOZ inventory.")


def _default_fetch(url: str, timeout: float) -> bytes:
    """Загружает один маленький pinned text blob без редиректов или remote code."""
    source_file = validate_fixed_source_url(url)
    request = Request(
        url,
        headers={
            "Accept": "text/plain, text/*;q=0.9",
            "User-Agent": "Ai-Brain-v3-russian-multiwoz-staging/0.1",
        },
        method="GET",
    )
    opener = build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        if response.geturl() != url:
            raise RussianMultiWOZError("Russian MultiWOZ source попытался перенаправить запрос.")
        if response.headers.get_content_type().lower() != "text/plain":
            raise RussianMultiWOZError("Russian MultiWOZ source вернул не plain-text файл.")
        payload = response.read(source_file.expected_bytes + 1)
    if len(payload) != source_file.expected_bytes:
        raise RussianMultiWOZError("Размер pinned Russian MultiWOZ file не совпал с inventory.")
    return payload


def _request_one_file(source_file: SourceFile, fetcher: RawFetcher, sleeper: Sleeper) -> bytes:
    """Получает один inventory file с ограниченными retry только для network-сбоев."""
    url = _source_url(source_file)
    for attempt in range(MAX_NETWORK_RETRIES + 1):
        try:
            payload = fetcher(url, REQUEST_TIMEOUT_SECONDS)
        except (OSError, TimeoutError) as error:
            if attempt < MAX_NETWORK_RETRIES:
                sleeper(RETRY_DELAY_SECONDS * (2**attempt))
                continue
            raise RussianMultiWOZError("Не удалось получить pinned Russian MultiWOZ file.") from error
        if not isinstance(payload, bytes) or len(payload) > MAX_FILE_BYTES:
            raise RussianMultiWOZError("Fetcher вернул Russian MultiWOZ file недопустимого типа или размера.")
        if len(payload) != source_file.expected_bytes or _git_blob_sha1(payload) != source_file.git_blob_sha1:
            raise RussianMultiWOZError("Pinned Russian MultiWOZ file не прошёл размер или Git blob checksum.")
        return payload
    raise AssertionError("Ограниченный retry loop должен завершиться внутри цикла.")


def _request_all_files(fetcher: RawFetcher, sleeper: Sleeper) -> tuple[tuple[tuple[SourceFile, bytes], ...], str, int]:
    """Собирает fixed inventory и общий audit fingerprint без записи исходников на диск."""
    inventory_hash = hashlib.sha256()
    downloaded: list[tuple[SourceFile, bytes]] = []
    total_bytes = 0
    for source_file in SOURCE_FILES:
        payload = _request_one_file(source_file, fetcher, sleeper)
        total_bytes += len(payload)
        if total_bytes > MAX_TOTAL_BYTES:
            raise RussianMultiWOZError("Russian MultiWOZ inventory превышает общий безопасный лимит.")
        inventory_hash.update(source_file.relative_path.encode("utf-8"))
        inventory_hash.update(b"\0")
        inventory_hash.update(hashlib.sha256(payload).digest())
        downloaded.append((source_file, payload))
    return tuple(downloaded), inventory_hash.hexdigest(), total_bytes


def _iter_source_dialogues(payload: bytes) -> Iterable[list[str]]:
    """Разделяет plain-text source на блоки диалогов, не интерпретируя их как код."""
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RussianMultiWOZError("Russian MultiWOZ file не является корректным UTF-8.") from error
    current: list[str] = []
    for raw_line in decoded.splitlines():
        line = _SPACE.sub(" ", raw_line).strip()
        if not line:
            if current:
                yield current
                current = []
            continue
        current.append(line)
    if current:
        yield current


def _clean_turn(text: str) -> tuple[str | None, str | None]:
    """Убирает source slot/reference values и сообщает код причины без текста."""
    raw_risk = text_risk_code(text, maximum=MAX_TEXT_LENGTH)
    if raw_risk not in {None, "phone_like"}:
        return None, raw_risk
    cleaned = _SQUARE_DETAIL.sub("[деталь]", text)
    cleaned = _PHONE_VALUE.sub("контактный-номер", cleaned)
    cleaned = _REFERENCE_CODE.sub("код-подтверждения", cleaned)
    cleaned = _POSTCODE.sub("почтовый-код", cleaned)
    cleaned = _TIME_VALUE.sub("время", cleaned)
    cleaned = _SPACE.sub(" ", cleaned).strip()
    cleaned_risk = text_risk_code(cleaned, maximum=MAX_TEXT_LENGTH)
    if cleaned_risk is not None:
        return None, cleaned_risk
    if not _CYRILLIC.search(cleaned):
        return None, "non_russian_turn"
    return cleaned, None


def _segments_for_dialogue(turns: list[str]) -> Iterable[list[str]]:
    """Разрезает только длинные диалоги на непересекающиеся чётные sequence-части."""
    if len(turns) < 2 or len(turns) % 2:
        raise RussianMultiWOZError("Russian MultiWOZ dialogue нарушает ожидаемое user/assistant чередование.")
    for start in range(0, len(turns), MAX_TURNS_PER_DIALOGUE):
        segment = turns[start : start + MAX_TURNS_PER_DIALOGUE]
        if len(segment) < 2 or len(segment) % 2:
            raise RussianMultiWOZError("Russian MultiWOZ dialogue нельзя безопасно разделить на чётные turns.")
        yield segment


def _audit_equivalent_content(turns: list[str]) -> str:
    """Рендерит turns точно как независимый corpus audit перед duplicate-проверкой."""
    return "\n".join(
        f"{'user' if index % 2 == 0 else 'assistant'}:{_SPACE.sub(' ', text).casefold().strip()}"
        for index, text in enumerate(turns)
    )


def _content_fingerprint(turns: list[str]) -> str:
    """Даёт deterministic exact duplicate key без публикации source-текста."""
    rendered = _audit_equivalent_content(turns)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _brain_record(
    source_file: SourceFile,
    file_index: int,
    dialogue_index: int,
    segment_index: int,
    turns: list[str],
) -> dict[str, Any]:
    """Строит одну role-safe Brain v3 dialogue запись с проверяемым provenance."""
    payload: dict[str, Any] = {
        "format_version": 1,
        "id": f"russian.multiwoz.{source_file.split}.{file_index:02d}.{dialogue_index:06d}.{segment_index:02d}",
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
        "split": source_file.split,
        "topic": "tool_routing",
        "quality_tier": "license_verified",
        "expected_response_properties": [
            "relevant",
            "context_retained",
            "ask_when_missing",
            "no_fake_certainty",
            "clear_next_step",
        ],
        "dialogue_goal": "follow_up_with_context",
        "emotional_tone": "calm",
        "turns": [
            {"role": "user" if index % 2 == 0 else "assistant", "text": text}
            for index, text in enumerate(turns)
        ],
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _prepare_destination(destination: Path, staging_root: Path) -> Path:
    """Разрешает создать immutable candidate только в dedicated staging-папке."""
    root = staging_root.expanduser().resolve()
    target = destination.expanduser().resolve()
    if target.suffix.lower() != ".jsonl":
        raise RussianMultiWOZError("Russian MultiWOZ staging output должен иметь расширение JSONL.")
    try:
        target.relative_to(root)
    except ValueError as error:
        raise RussianMultiWOZError("Russian MultiWOZ output разрешён только в data/brain_v3/staging/.") from error
    if target.exists():
        raise RussianMultiWOZError("Нельзя перезаписывать существующий Russian MultiWOZ staging candidate.")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def import_russian_multiwoz(
    destination: Path,
    *,
    staging_root: Path = DEFAULT_STAGING_ROOT,
    fetcher: RawFetcher | None = None,
    sleeper: Sleeper = time.sleep,
) -> RussianMultiWOZSummary:
    """Создаёт новый очищенный candidate в staging без изменения manifest или raw/."""
    output_path = _prepare_destination(destination, staging_root)
    downloaded, inventory_sha256, raw_bytes_seen = _request_all_files(_default_fetch if fetcher is None else fetcher, sleeper)
    exact_fingerprints: set[str] = set()
    near_index = NearDuplicateIndex()
    rejections: Counter[str] = Counter()
    dialogues_seen = 0
    records_written = 0
    dialogue_turns_written = 0
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".russian-multiwoz-",
            suffix=".jsonl.tmp",
            dir=output_path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination_file:
            for file_index, (source_file, raw_payload) in enumerate(downloaded, start=1):
                for dialogue_index, raw_turns in enumerate(_iter_source_dialogues(raw_payload), start=1):
                    dialogues_seen += 1
                    cleaned_turns: list[str] = []
                    rejection_code: str | None = None
                    for raw_turn in raw_turns:
                        cleaned_turn, turn_error = _clean_turn(raw_turn)
                        if turn_error is not None:
                            rejection_code = turn_error
                            break
                        if cleaned_turn is None:
                            rejection_code = "invalid_turn"
                            break
                        cleaned_turns.append(cleaned_turn)
                    if rejection_code is not None:
                        rejections[rejection_code] += 1
                        continue
                    try:
                        segments = tuple(_segments_for_dialogue(cleaned_turns))
                    except RussianMultiWOZError:
                        rejections["invalid_dialogue_shape"] += 1
                        continue
                    for segment_index, segment in enumerate(segments, start=1):
                        exact_fingerprint = _content_fingerprint(segment)
                        if exact_fingerprint in exact_fingerprints:
                            rejections["exact_duplicate"] += 1
                            continue
                        signature = duplicate_signature(_audit_equivalent_content(segment))
                        if near_index.find(signature) is not None:
                            rejections["near_duplicate_candidate"] += 1
                            continue
                        record = _brain_record(source_file, file_index, dialogue_index, segment_index, segment)
                        destination_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                        exact_fingerprints.add(exact_fingerprint)
                        near_index.add(record["id"], signature)
                        records_written += 1
                        dialogue_turns_written += len(segment)
            destination_file.flush()
            os.fsync(destination_file.fileno())
        if records_written == 0:
            raise RussianMultiWOZError("Russian MultiWOZ не дал ни одной безопасной dialogue-записи.")
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return RussianMultiWOZSummary(
        raw_inventory_sha256=inventory_sha256,
        raw_files_seen=len(downloaded),
        raw_bytes_seen=raw_bytes_seen,
        dialogues_seen=dialogues_seen,
        records_written=records_written,
        dialogue_turns_written=dialogue_turns_written,
        records_rejected=sum(rejections.values()),
        rejections=dict(sorted(rejections.items())),
        output_path=str(output_path),
        output_sha256=file_sha256(output_path),
    )


def summary_markdown(summary: RussianMultiWOZSummary) -> str:
    """Формирует Markdown-отчёт staging intake без исходных реплик и секретов."""
    return "\n".join(
        [
            "# Russian MultiWOZ — staging intake Brain v3",
            "",
            "Статус: источниковые текстовые файлы прочитаны только из pinned commit и преобразованы в новый staging candidate.",
            f"Commit: `{SOURCE_REVISION}`. Файлов: {summary.raw_files_seen}; bytes: {summary.raw_bytes_seen}.",
            f"SHA-256 inventory: `{summary.raw_inventory_sha256}`.",
            f"Диалогов во входных файлах: {summary.dialogues_seen}; записано records: {summary.records_written}; turns: {summary.dialogue_turns_written}.",
            f"Отклонено records/segments: {summary.records_rejected}; причины: {summary.rejections}.",
            f"SHA-256 staging candidate: `{summary.output_sha256}`.",
            "",
            "Candidate не активен. Manifest, raw corpus, обучение, рабочий чат и GitHub этим шагом не изменяются.",
            "",
        ]
    )


def create_staging_candidate(
    destination: Path = DEFAULT_CANDIDATE,
    report_directory: Path = DEFAULT_REPORT_DIRECTORY,
) -> dict[str, Any]:
    """Выполняет fixed intake и сохраняет новый JSON/Markdown отчёт без overwrite."""
    if report_directory.exists():
        raise RussianMultiWOZError("Каталог Russian MultiWOZ staging-отчёта уже существует; перезапись запрещена.")
    summary = import_russian_multiwoz(destination)
    report = summary.as_dict()
    report_directory.mkdir(parents=True)
    (report_directory / "russian_multiwoz_staging.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "RUSSIAN_MULTIWOZ_STAGING_REPORT.md").write_text(
        summary_markdown(summary),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Запускает только fixed staging intake Russian MultiWOZ без URL-параметров."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    args = parser.parse_args()
    try:
        report = create_staging_candidate(args.destination, args.report)
    except (OSError, RussianMultiWOZError) as error:
        raise SystemExit(f"Russian MultiWOZ staging intake остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
