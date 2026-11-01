"""Общие read-only проверки staging-источника Brain v3 до local promotion.

Здесь нет загрузки сети, выбора URL или добавления source в manifest. Модуль
принимает уже созданный JSONL и descriptor, заданный исходным адаптером в коде;
все проверки выполняются через временные hard link без записи в active corpus.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ai_core.brain_v3_corpus import BrainV3CorpusError, audit_corpus, file_sha256
from ai_core.brain_v3_dialogue_coverage import (
    DEFAULT_POLICY,
    DialogueCoverageError,
    audit_dialogue_coverage,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_ROOT = ROOT / "data" / "brain_v3"
DEFAULT_STAGING_ROOT = DEFAULT_CORPUS_ROOT / "staging"


class BrainV3StagingError(ValueError):
    """Останавливает staging audit/preflight до изменения active corpus."""


@dataclass(frozen=True)
class StagedSourceDescriptor:
    """Проверяемое provenance source, определённое адаптером, а не JSONL-данными."""

    source_id: str
    license_name: str
    license_url: str | None
    origin_url: str | None
    attribution: str
    source_kind: str = "open_licensed"


def staging_candidate_path(candidate: Path, staging_root: Path = DEFAULT_STAGING_ROOT) -> Path:
    """Принимает только существующий JSONL в отдельной staging-папке."""
    root = staging_root.expanduser().resolve()
    path = candidate.expanduser().resolve()
    if path.suffix.lower() != ".jsonl" or not path.is_file():
        raise BrainV3StagingError("Staging candidate должен быть существующим JSONL-файлом.")
    try:
        path.relative_to(root)
    except ValueError as error:
        raise BrainV3StagingError("Проверка разрешена только внутри data/brain_v3/staging/.") from error
    return path


def _source_entry(candidate: Path, descriptor: StagedSourceDescriptor) -> dict[str, Any]:
    """Строит временную manifest-запись, не доверяя provenance из candidate."""
    return {
        "source_id": descriptor.source_id,
        "raw_path": "raw/candidate.jsonl",
        "raw_sha256": file_sha256(candidate),
        "source_kind": descriptor.source_kind,
        "license": descriptor.license_name,
        "license_url": descriptor.license_url,
        "origin_url": descriptor.origin_url,
        "attribution": descriptor.attribution,
        "approved_for_training": True,
    }


def _candidate_manifest(candidate: Path, descriptor: StagedSourceDescriptor) -> dict[str, Any]:
    """Создаёт строгий временный manifest только для isolated candidate audit."""
    return {
        "format_version": 1,
        "corpus_name": "brain-v3-staging-candidate",
        "language_scope": ["ru"],
        "accepted_licenses": [descriptor.license_name],
        "quality_gate": {
            "minimum_unique_dialogue_turns": 20_000,
            "minimum_language_characters": 100_000_000,
            "maximum_rejected_record_ratio": 0.0,
            "require_all_splits": True,
        },
        "sources": [_source_entry(candidate, descriptor)],
    }


def audit_staged_candidate(
    candidate: Path,
    descriptor: StagedSourceDescriptor,
    *,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> dict[str, Any]:
    """Проводит независимый audit одной staging-версии без activation.

    Для audit используется временный hard link на том же локальном диске. После
    выхода из TemporaryDirectory не остаётся ни manifest, ни копии source.
    """
    source_path = staging_candidate_path(candidate, staging_root)
    temporary_parent = staging_root.expanduser().resolve().parent
    with tempfile.TemporaryDirectory(prefix=".brain-v3-staged-audit-", dir=temporary_parent) as directory:
        temporary_root = Path(directory)
        raw_directory = temporary_root / "raw"
        raw_directory.mkdir()
        linked_candidate = raw_directory / "candidate.jsonl"
        try:
            os.link(source_path, linked_candidate)
        except OSError as error:
            raise BrainV3StagingError("Не удалось создать hard link staging candidate для audit.") from error
        manifest_path = temporary_root / "corpus_manifest.v1.json"
        manifest_path.write_text(
            json.dumps(_candidate_manifest(source_path, descriptor), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        audit = audit_corpus(manifest_path)
    return {
        "format_version": 1,
        "mode": "staging_candidate_audit",
        "candidate_path": str(source_path),
        "candidate_sha256": file_sha256(source_path),
        "source_id": descriptor.source_id,
        "license": descriptor.license_name,
        "audit": audit,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
    }


def _load_active_manifest_payload(corpus_root: Path) -> dict[str, Any]:
    """Читает active manifest только как данные для временной проверки."""
    path = corpus_root.expanduser().resolve() / "corpus_manifest.v1.json"
    if not path.is_file():
        raise BrainV3StagingError("Не найден active corpus manifest для preflight.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise BrainV3StagingError("Active corpus manifest содержит некорректный JSON.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise BrainV3StagingError("Active corpus manifest не содержит список sources.")
    return payload


def _active_raw_path(corpus_root: Path, raw_path: Any) -> Path:
    """Разрешает hard link только существующего immutable raw JSONL."""
    if not isinstance(raw_path, str):
        raise BrainV3StagingError("Active manifest source имеет недопустимый raw_path.")
    relative = Path(raw_path)
    if relative.is_absolute() or relative.suffix.lower() != ".jsonl":
        raise BrainV3StagingError("Active manifest source должен ссылаться на JSONL.")
    root = corpus_root.expanduser().resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root / "raw")
    except ValueError as error:
        raise BrainV3StagingError("Preflight принимает active source только из raw/.") from error
    if not path.is_file():
        raise BrainV3StagingError("Не найден raw JSONL, указанный active manifest.")
    return path


def preflight_staged_candidate_against_active(
    candidate: Path,
    descriptor: StagedSourceDescriptor,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> dict[str, Any]:
    """Проверяет candidate вместе с active raw источниками без их изменения."""
    with _temporary_candidate_manifest_against_active(
        candidate,
        descriptor,
        corpus_root=corpus_root,
        staging_root=staging_root,
    ) as (source_path, manifest_path, active_sources_checked):
        audit = audit_corpus(manifest_path)

    clean_merge = audit["records_rejected"] == 0 and audit["cross_split_exact_duplicates"] == 0 and audit[
        "cross_split_near_duplicate_candidates"
    ] == 0
    return {
        "format_version": 1,
        "mode": "active_corpus_preflight",
        "candidate_path": str(source_path),
        "candidate_sha256": file_sha256(source_path),
        "candidate_source_id": descriptor.source_id,
        "active_sources_checked": active_sources_checked,
        "audit": audit,
        "clean_merge": clean_merge,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
    }


def preflight_and_prospective_dialogue_coverage_for_staged_candidate(
    candidate: Path,
    descriptor: StagedSourceDescriptor,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
    policy_path: Path = DEFAULT_POLICY,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Делает preflight и dialogue coverage в одном verified read-only проходе.

    Один full corpus audit одновременно доказывает чистоту prospective merge и
    становится проверенным входом metadata-only coverage audit. Temporary
    manifest и hard links живут на протяжении обоих шагов, поэтому coverage не
    принимает результат от другого candidate или изменённого source.
    """

    source_path = staging_candidate_path(candidate, staging_root)
    candidate_sha256 = file_sha256(source_path)
    try:
        with _temporary_candidate_manifest_against_active(
            source_path,
            descriptor,
            corpus_root=corpus_root,
            staging_root=staging_root,
        ) as (source_path, manifest_path, active_sources_checked):
            audit = audit_corpus(manifest_path)
            coverage = audit_dialogue_coverage(
                manifest_path,
                policy_path,
                verified_base_audit=audit,
            )
    except (BrainV3CorpusError, DialogueCoverageError) as error:
        raise BrainV3StagingError(
            "Не удалось выполнить единый prospective preflight и dialogue coverage audit."
        ) from error
    if file_sha256(source_path) != candidate_sha256:
        raise BrainV3StagingError("Staging candidate изменился во время prospective audit.")

    clean_merge = (
        audit["records_rejected"] == 0
        and audit["cross_split_exact_duplicates"] == 0
        and audit["cross_split_near_duplicate_candidates"] == 0
    )
    preflight = {
        "format_version": 1,
        "mode": "active_corpus_preflight",
        "candidate_path": str(source_path),
        "candidate_sha256": candidate_sha256,
        "candidate_source_id": descriptor.source_id,
        "active_sources_checked": active_sources_checked,
        "audit": audit,
        "clean_merge": clean_merge,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
    }
    prospective_coverage = {
        "format_version": 1,
        "mode": "prospective_dialogue_coverage",
        "candidate_path": str(source_path),
        "candidate_sha256": candidate_sha256,
        "candidate_source_id": descriptor.source_id,
        "active_sources_checked": active_sources_checked,
        "coverage": coverage,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
    }
    return preflight, prospective_coverage


@contextmanager
def _temporary_candidate_manifest_against_active(
    candidate: Path,
    descriptor: StagedSourceDescriptor,
    *,
    corpus_root: Path,
    staging_root: Path,
    replaced_source_id: str | None = None,
) -> Iterator[tuple[Path, Path, int]]:
    """Готовит temporary active+candidate manifest для read-only checks.

    В helper попадают только hard links к immutable JSONL. Он не меняет
    настоящий manifest, raw, staging-кандидат или API и гарантированно удаляет
    временный каталог после того, как вызывающая проверка завершится. При
    ``replaced_source_id`` временная копия исключает ровно один active parent:
    это моделирует replacement snapshot, но не трогает active manifest.
    """
    source_path = staging_candidate_path(candidate, staging_root)
    corpus_base = corpus_root.expanduser().resolve()
    payload = _load_active_manifest_payload(corpus_base)
    active_sources = payload["sources"]
    source_ids = {item.get("source_id") for item in active_sources if isinstance(item, dict)}
    if descriptor.source_id in source_ids:
        raise BrainV3StagingError("Этот source уже присутствует в active manifest.")
    if replaced_source_id is not None:
        if not isinstance(replaced_source_id, str) or not replaced_source_id.strip():
            raise BrainV3StagingError("Replacement source_id должен быть непустой строкой.")
        matching_sources = [
            source
            for source in active_sources
            if isinstance(source, dict) and source.get("source_id") == replaced_source_id
        ]
        if len(matching_sources) != 1:
            raise BrainV3StagingError(
                "Replacement source_id должен совпадать ровно с одним active source."
            )
        active_sources = [
            source
            for source in active_sources
            if not (isinstance(source, dict) and source.get("source_id") == replaced_source_id)
        ]
        if not active_sources:
            raise BrainV3StagingError(
                "Replacement snapshot не должен удалять все active sources."
            )

    with tempfile.TemporaryDirectory(prefix=".brain-v3-prospective-", dir=corpus_base) as directory:
        temporary_root = Path(directory)
        raw_directory = temporary_root / "raw"
        raw_directory.mkdir()
        provisional_sources: list[dict[str, Any]] = []
        for index, source in enumerate(active_sources):
            if not isinstance(source, dict):
                raise BrainV3StagingError("Active manifest source должен быть JSON object.")
            original_path = _active_raw_path(corpus_base, source.get("raw_path"))
            linked_name = f"active-{index:03d}.jsonl"
            linked_path = raw_directory / linked_name
            try:
                os.link(original_path, linked_path)
            except OSError as error:
                raise BrainV3StagingError("Не удалось создать hard link active source для prospective audit.") from error
            copied_source = dict(source)
            copied_source["raw_path"] = f"raw/{linked_name}"
            copied_source["raw_sha256"] = file_sha256(original_path)
            provisional_sources.append(copied_source)

        candidate_link = raw_directory / "candidate.jsonl"
        try:
            os.link(source_path, candidate_link)
        except OSError as error:
            raise BrainV3StagingError("Не удалось создать hard link candidate для prospective audit.") from error
        provisional_sources.append(_source_entry(source_path, descriptor))
        provisional_manifest = dict(payload)
        provisional_manifest["sources"] = provisional_sources
        manifest_path = temporary_root / "corpus_manifest.v1.json"
        manifest_path.write_text(
            json.dumps(provisional_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        yield source_path, manifest_path, len(active_sources)


def preflight_staged_replacement_against_active(
    candidate: Path,
    descriptor: StagedSourceDescriptor,
    *,
    replaced_source_id: str,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> dict[str, Any]:
    """Проверяет non-active snapshot с заменой одного родительского source.

    Это не promotion: функция создаёт только временные hard link, исключает
    parent из копии manifest и добавляет candidate вместо него. Настоящий
    active manifest и все raw JSONL остаются неизменными.
    """
    if replaced_source_id == descriptor.source_id:
        raise BrainV3StagingError(
            "Replacement source_id не может совпадать с candidate source_id."
        )
    with _temporary_candidate_manifest_against_active(
        candidate,
        descriptor,
        corpus_root=corpus_root,
        staging_root=staging_root,
        replaced_source_id=replaced_source_id,
    ) as (source_path, manifest_path, active_sources_checked):
        audit = audit_corpus(manifest_path)

    clean_snapshot = (
        audit["records_rejected"] == 0
        and audit["cross_split_exact_duplicates"] == 0
        and audit["cross_split_near_duplicate_candidates"] == 0
    )
    return {
        "format_version": 1,
        "mode": "active_corpus_replacement_preflight",
        "candidate_path": str(source_path),
        "candidate_sha256": file_sha256(source_path),
        "candidate_source_id": descriptor.source_id,
        "replaced_source_id": replaced_source_id,
        "active_sources_checked": active_sources_checked,
        "audit": audit,
        "clean_snapshot": clean_snapshot,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
    }


def prospective_dialogue_coverage_for_staged_candidate(
    candidate: Path,
    descriptor: StagedSourceDescriptor,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
    policy_path: Path = DEFAULT_POLICY,
) -> dict[str, Any]:
    """Проверяет будущее dialogue-покрытие без promotion или обучения.

    В отличие от isolated audit, эта проверка временно объединяет active corpus
    и candidate через hard links и запускает тот же coverage-audit, который
    применяется к активному corpus. Результат является перспективной метрикой:
    candidate остаётся staging-only даже если requirements пройдены.
    """
    try:
        with _temporary_candidate_manifest_against_active(
            candidate,
            descriptor,
            corpus_root=corpus_root,
            staging_root=staging_root,
        ) as (source_path, manifest_path, active_sources_checked):
            coverage = audit_dialogue_coverage(manifest_path, policy_path)
    except DialogueCoverageError as error:
        raise BrainV3StagingError("Не удалось выполнить prospective dialogue coverage audit.") from error
    return {
        "format_version": 1,
        "mode": "prospective_dialogue_coverage",
        "candidate_path": str(source_path),
        "candidate_sha256": file_sha256(source_path),
        "candidate_source_id": descriptor.source_id,
        "active_sources_checked": active_sources_checked,
        "coverage": coverage,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
    }


def prospective_dialogue_coverage_for_staged_replacement(
    candidate: Path,
    descriptor: StagedSourceDescriptor,
    *,
    replaced_source_id: str,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
    policy_path: Path = DEFAULT_POLICY,
) -> dict[str, Any]:
    """Измеряет coverage временной замены parent source без promotion.

    В отличие от обычного prospective merge, активный parent не добавляется к
    candidate. Это единственный корректный способ проверить approved subset,
    который является его подмножеством и поэтому не может быть добавлен рядом
    с исходным source.
    """
    if replaced_source_id == descriptor.source_id:
        raise BrainV3StagingError(
            "Replacement source_id не может совпадать с candidate source_id."
        )
    try:
        with _temporary_candidate_manifest_against_active(
            candidate,
            descriptor,
            corpus_root=corpus_root,
            staging_root=staging_root,
            replaced_source_id=replaced_source_id,
        ) as (source_path, manifest_path, active_sources_checked):
            coverage = audit_dialogue_coverage(manifest_path, policy_path)
    except DialogueCoverageError as error:
        raise BrainV3StagingError(
            "Не удалось выполнить prospective dialogue coverage replacement snapshot."
        ) from error
    return {
        "format_version": 1,
        "mode": "prospective_dialogue_coverage_replacement",
        "candidate_path": str(source_path),
        "candidate_sha256": file_sha256(source_path),
        "candidate_source_id": descriptor.source_id,
        "replaced_source_id": replaced_source_id,
        "active_sources_checked": active_sources_checked,
        "coverage": coverage,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
    }
