"""Безопасно создаёт новый staging JSONL без пересечений с active Brain v3 corpus.

Модуль принимает только уже существующий staging-кандидат и trusted descriptor
из локального адаптера. Он не скачивает данные, не исполняет source text, не
меняет manifest/raw и не запускает обучение. При conflict создаётся новая
versioned staging-копия; исходный candidate остаётся неизменным.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_core.brain_v3_corpus import (
    MAX_SOURCE_BYTES,
    BrainV3CorpusError,
    BrainV3RecordError,
    CorpusSource,
    DuplicateSignature,
    NearDuplicateIndex,
    _parse_record,
    _read_jsonl,
    audit_corpus,
    file_sha256,
    load_corpus_manifest,
)
from evaluation.brain_v3_staging import (
    DEFAULT_CORPUS_ROOT,
    DEFAULT_STAGING_ROOT,
    BrainV3StagingError,
    StagedSourceDescriptor,
    staging_candidate_path,
)


class BrainV3ActiveCleanError(ValueError):
    """Останавливает очистку до создания частичного нового staging JSONL."""


@dataclass(frozen=True)
class BrainV3ActiveCleanSummary:
    """Фиксирует метрики очистки без candidate/active source text."""

    source_id: str
    candidate_path: str
    candidate_sha256: str
    output_path: str
    output_sha256: str
    output_bytes: int
    active_sources_checked: int
    candidate_records_seen: int
    records_written: int
    records_rejected: int
    rejections: dict[str, int]
    active_audit: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Сериализует safe hashes и counters без исходных реплик."""
        return {
            "format_version": 1,
            "mode": "staging_active_overlap_clean",
            "source_id": self.source_id,
            "candidate_path": self.candidate_path,
            "candidate_sha256": self.candidate_sha256,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "output_bytes": self.output_bytes,
            "active_sources_checked": self.active_sources_checked,
            "candidate_records_seen": self.candidate_records_seen,
            "records_written": self.records_written,
            "records_rejected": self.records_rejected,
            "rejections": self.rejections,
            "active_audit": self.active_audit,
            "output_is_active": False,
            "active_manifest_updated": False,
            "training_started": False,
            "chat_changed": False,
            "github_changed": False,
        }


def _new_staging_path(path: Path, staging_root: Path) -> Path:
    """Разрешает immutable output только внутри staging без overwrite."""
    root = staging_root.expanduser().resolve()
    output = path.expanduser().resolve()
    if output.suffix.lower() != ".jsonl":
        raise BrainV3ActiveCleanError("Очищенный candidate должен быть JSONL-файлом.")
    try:
        output.relative_to(root)
    except ValueError as error:
        raise BrainV3ActiveCleanError("Очистка разрешена только внутри data/brain_v3/staging/.") from error
    if output.exists():
        raise BrainV3ActiveCleanError("Очищенный staging JSONL уже существует; перезапись запрещена.")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def _candidate_source(path: Path, descriptor: StagedSourceDescriptor) -> CorpusSource:
    """Создаёт trusted временное source-описание, а не читает provenance из JSONL."""
    return CorpusSource(
        source_id=descriptor.source_id,
        raw_path=path,
        raw_sha256=file_sha256(path),
        source_kind="open_licensed",
        license_name=descriptor.license_name,
        license_url=descriptor.license_url,
        origin_url=descriptor.origin_url,
        attribution=descriptor.attribution,
    )


def _assert_clean_active_corpus(
    corpus_root: Path,
    *,
    candidate_source_id: str,
) -> tuple[dict[str, Any], tuple[CorpusSource, ...]]:
    """Не позволяет очищать относительно повреждённого или уже изменённого corpus."""
    manifest_path = corpus_root.expanduser().resolve() / "corpus_manifest.v1.json"
    if not manifest_path.is_file():
        raise BrainV3ActiveCleanError("Не найден active corpus manifest для cross-source clean.")
    try:
        active_audit = audit_corpus(manifest_path)
        manifest = load_corpus_manifest(manifest_path)
    except (BrainV3CorpusError, OSError) as error:
        raise BrainV3ActiveCleanError("Active corpus не прошёл собственный audit до clean.") from error
    if active_audit["records_rejected"] != 0:
        raise BrainV3ActiveCleanError("Active corpus содержит отклонённые записи; clean остановлен.")
    if active_audit["cross_split_exact_duplicates"] or active_audit["cross_split_near_duplicate_candidates"]:
        raise BrainV3ActiveCleanError("Active corpus содержит межsplit-пересечения; clean остановлен.")
    if any(source.source_id == candidate_source_id for source in manifest.sources):
        raise BrainV3ActiveCleanError("Этот source уже присутствует в active manifest.")
    return active_audit, manifest.sources


def _active_indexes(sources: tuple[CorpusSource, ...]) -> tuple[set[str], NearDuplicateIndex, set[str]]:
    """Собирает только hashes/signatures/IDs предварительно чистых active records."""
    exact_fingerprints: set[str] = set()
    near_index = NearDuplicateIndex()
    identifiers: set[str] = set()
    for source in sources:
        if file_sha256(source.raw_path) != source.raw_sha256:
            raise BrainV3ActiveCleanError("Контрольная сумма active source изменилась во время clean.")
        for _, payload in _read_jsonl(source.raw_path):
            try:
                record = _parse_record(payload, source)
            except BrainV3RecordError as error:
                raise BrainV3ActiveCleanError("Active source содержит недопустимую запись.") from error
            exact_fingerprints.add(record.content_fingerprint)
            identifiers.add(record.identifier)
            near_index.add(
                record.identifier,
                DuplicateSignature(simhash=record.simhash, token_count=record.token_count),
            )
    return exact_fingerprints, near_index, identifiers


def clean_staged_candidate_against_active(
    candidate: Path,
    output: Path,
    descriptor: StagedSourceDescriptor,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> dict[str, Any]:
    """Исключает только exact/near overlaps с active corpus в новый JSONL.

    Внутренний дубль или повреждённая candidate-запись считаются ошибкой всего
    candidate, а не поводом молча изменить dataset. Удаляются исключительно
    подтверждённые совпадения с уже чистым active corpus.
    """
    try:
        source_path = staging_candidate_path(candidate, staging_root)
    except BrainV3StagingError as error:
        raise BrainV3ActiveCleanError("Не найден корректный staging candidate для clean.") from error
    output_path = _new_staging_path(output, staging_root)
    if source_path == output_path:
        raise BrainV3ActiveCleanError("Новый staging JSONL должен иметь другое имя.")

    active_audit, active_sources = _assert_clean_active_corpus(
        corpus_root,
        candidate_source_id=descriptor.source_id,
    )
    active_exact, active_near, active_identifiers = _active_indexes(active_sources)
    candidate_source = _candidate_source(source_path, descriptor)
    candidate_exact: set[str] = set()
    candidate_near = NearDuplicateIndex()
    candidate_identifiers: set[str] = set()
    rejections: Counter[str] = Counter()
    seen = 0
    written = 0
    output_bytes = 0
    temporary_path: Path | None = None

    try:
        descriptor_handle, temporary_name = tempfile.mkstemp(
            prefix=".brain-v3-active-clean-",
            suffix=".jsonl.tmp",
            dir=output_path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor_handle, "w", encoding="utf-8", newline="\n") as destination:
            for _, payload in _read_jsonl(source_path):
                seen += 1
                try:
                    record = _parse_record(payload, candidate_source)
                except BrainV3RecordError as error:
                    raise BrainV3ActiveCleanError("Staging candidate не прошёл строгую повторную проверку.") from error
                if record.identifier in active_identifiers or record.identifier in candidate_identifiers:
                    raise BrainV3ActiveCleanError("Найдена коллизия record ID; candidate нельзя очищать автоматически.")
                if record.content_fingerprint in candidate_exact:
                    raise BrainV3ActiveCleanError("Candidate содержит внутренний точный duplicate.")
                signature = DuplicateSignature(record.simhash, record.token_count)
                if candidate_near.find(signature) is not None:
                    raise BrainV3ActiveCleanError("Candidate содержит внутренний near-duplicate.")
                if record.content_fingerprint in active_exact:
                    rejections["exact_duplicate_with_active"] += 1
                    continue
                if active_near.find(signature) is not None:
                    rejections["near_duplicate_with_active"] += 1
                    continue

                serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                encoded_bytes = len(serialized.encode("utf-8")) + 1
                if output_bytes + encoded_bytes > MAX_SOURCE_BYTES:
                    raise BrainV3ActiveCleanError("Очищенный candidate превысил safe source byte limit.")
                candidate_identifiers.add(record.identifier)
                candidate_exact.add(record.content_fingerprint)
                candidate_near.add(record.identifier, signature)
                destination.write(serialized)
                destination.write("\n")
                output_bytes += encoded_bytes
                written += 1

        if written == 0:
            raise BrainV3ActiveCleanError("После cross-source clean не осталось безопасных candidate records.")
        if output_path.exists():
            raise BrainV3ActiveCleanError("Очищенный output появился во время clean; overwrite запрещён.")
        assert temporary_path is not None
        os.replace(temporary_path, output_path)
        temporary_path = None
    except (BrainV3CorpusError, OSError) as error:
        raise BrainV3ActiveCleanError("Не удалось атомарно создать очищенный staging JSONL.") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return BrainV3ActiveCleanSummary(
        source_id=descriptor.source_id,
        candidate_path=str(source_path),
        candidate_sha256=file_sha256(source_path),
        output_path=str(output_path),
        output_sha256=file_sha256(output_path),
        output_bytes=output_bytes,
        active_sources_checked=len(active_sources),
        candidate_records_seen=seen,
        records_written=written,
        records_rejected=seen - written,
        rejections=dict(sorted(rejections.items())),
        active_audit=active_audit,
    ).as_dict()


def summary_markdown(report: dict[str, Any]) -> str:
    """Рендерит audit trail очистки без внешних диалогов или идентификаторов."""
    return "\n".join(
        [
            "# Brain v3 — cross-source clean staging candidate",
            "",
            "Статус: создан новый staging JSONL; active manifest, raw и обучение не изменялись.",
            f"Source ID: `{report['source_id']}`; active sources checked: {report['active_sources_checked']}.",
            f"Исходный candidate SHA-256: `{report['candidate_sha256']}`.",
            f"Новый candidate SHA-256: `{report['output_sha256']}`; bytes: {report['output_bytes']}.",
            f"Проверено records: {report['candidate_records_seen']}; записано: {report['records_written']}; исключено: {report['records_rejected']}.",
            f"Причины исключения: {report['rejections']}.",
            "",
            "Новый файл остаётся staging-кандидатом. До promotion требуются новый isolated audit и active preflight.",
            "",
        ]
    )
