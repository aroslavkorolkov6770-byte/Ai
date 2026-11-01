"""Переносит только проверенный OpenAssistant OASST1 v0.2 из staging в active corpus.

Promotion использует fixed candidate, закреплённые SHA-256, размер и record
metrics, а также два независимых доказательства: isolated audit и preflight
против active corpus. До изменения manifest создаётся immutable backup.
Скрипт не обучает модель, не меняет работающий API и не публикует данные.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_core.brain_v3_corpus import BrainV3CorpusError, audit_corpus, file_sha256, load_corpus_manifest
from ai_core.openassistant_oasst1 import (
    DEFAULT_STAGING_ROOT,
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
)
from evaluation.brain_v3_staging import BrainV3StagingError, staging_candidate_path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_ROOT = ROOT / "data" / "brain_v3"
DEFAULT_CANDIDATE = DEFAULT_STAGING_ROOT / "openassistant-oasst1-apache-v0.2.jsonl"
RAW_FILE_NAME = "openassistant-oasst1-apache-v1.jsonl"
MANIFEST_FILE_NAME = "corpus_manifest.v1.json"
DEFAULT_RAW_PATH = DEFAULT_CORPUS_ROOT / "raw" / RAW_FILE_NAME
DEFAULT_MANIFEST_PATH = DEFAULT_CORPUS_ROOT / MANIFEST_FILE_NAME
BACKUP_FILE_NAME = "corpus_manifest.v1.before-openassistant-oasst1-apache-v1.json"
DEFAULT_BACKUP_PATH = DEFAULT_CORPUS_ROOT / "manifest_backups" / BACKUP_FILE_NAME
DEFAULT_AUDIT_PROOF = (
    ROOT / "evaluation" / "results" / "openassistant-oasst1-audit-v0.2" / "openassistant_oasst1_audit.json"
)
DEFAULT_PREFLIGHT_PROOF = (
    ROOT
    / "evaluation"
    / "results"
    / "openassistant-oasst1-preflight-v0.2"
    / "openassistant_oasst1_preflight.json"
)
DEFAULT_REPORT_DIRECTORY = ROOT / "evaluation" / "results" / "openassistant-oasst1-promotion-v0.1"

EXPECTED_CANDIDATE_SHA256 = "c040157ca4db005e3e919e9d5c6b70d85817657b5ec49ea23f6724e3a8467d0c"
EXPECTED_CANDIDATE_BYTES = 5_174_598
EXPECTED_CANDIDATE_RECORDS = 1_541
COPY_CHUNK_BYTES = 1024 * 1024


class OpenAssistantOASST1PromotionError(ValueError):
    """Останавливает activation до создания незащищённой raw/manifest версии."""


@dataclass(frozen=True)
class OpenAssistantOASST1PromotionSummary:
    """Хранит только provenance, hashes и audit metrics без corpus text."""

    candidate_path: str
    candidate_sha256: str
    raw_path: str
    raw_sha256: str
    raw_bytes: int
    manifest_path: str
    manifest_sha256_before: str
    manifest_sha256_after: str
    manifest_backup_path: str
    manifest_backup_sha256: str
    active_audit_before: dict[str, Any]
    active_audit_after: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Сериализует activation facts без реплик или source filesystem contents."""
        return {
            "format_version": 1,
            "mode": "local_staging_promotion",
            "source_id": SOURCE_ID,
            "candidate_path": self.candidate_path,
            "candidate_sha256": self.candidate_sha256,
            "raw_path": self.raw_path,
            "raw_sha256": self.raw_sha256,
            "raw_bytes": self.raw_bytes,
            "manifest_path": self.manifest_path,
            "manifest_sha256_before": self.manifest_sha256_before,
            "manifest_sha256_after": self.manifest_sha256_after,
            "manifest_backup_path": self.manifest_backup_path,
            "manifest_backup_sha256": self.manifest_backup_sha256,
            "active_audit_before": self.active_audit_before,
            "active_audit_after": self.active_audit_after,
            "active_manifest_updated": True,
            "training_started": False,
            "chat_changed": False,
            "github_changed": False,
        }


def _safe_child(path: Path, root: Path, *, code: str) -> Path:
    """Разрешает mutation только внутри явно переданного local root."""
    resolved_root = root.expanduser().resolve()
    resolved_path = path.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise OpenAssistantOASST1PromotionError(code) from error
    return resolved_path


def _fixed_raw_path(path: Path, corpus_root: Path) -> Path:
    """Принимает ровно один immutable raw filename и запрещает overwrite."""
    raw_root = corpus_root.expanduser().resolve() / "raw"
    raw_path = _safe_child(path, raw_root, code="OASST1 promotion пишет только в data/brain_v3/raw/.")
    if raw_path.name != RAW_FILE_NAME or raw_path.suffix.lower() != ".jsonl":
        raise OpenAssistantOASST1PromotionError("OASST1 raw filename должен быть fixed JSONL target.")
    if raw_path.exists():
        raise OpenAssistantOASST1PromotionError("OASST1 raw source уже существует; перезапись запрещена.")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    return raw_path


def _fixed_manifest_path(path: Path, corpus_root: Path) -> Path:
    """Принимает только canonical active manifest текущего corpus root."""
    corpus_base = corpus_root.expanduser().resolve()
    manifest_path = _safe_child(path, corpus_base, code="OASST1 promotion меняет только local corpus manifest.")
    if manifest_path != corpus_base / MANIFEST_FILE_NAME or not manifest_path.is_file():
        raise OpenAssistantOASST1PromotionError("Не найден canonical active corpus manifest для OASST1 promotion.")
    return manifest_path


def _fixed_backup_path(path: Path, corpus_root: Path) -> Path:
    """Готовит новый immutable backup, не заменяя существующий snapshot."""
    backup_root = corpus_root.expanduser().resolve() / "manifest_backups"
    backup_path = _safe_child(path, backup_root, code="Backup manifest разрешён только в manifest_backups/.")
    if backup_path.name != BACKUP_FILE_NAME or backup_path.suffix.lower() != ".json":
        raise OpenAssistantOASST1PromotionError("OASST1 backup должен иметь fixed JSON filename.")
    if backup_path.exists():
        raise OpenAssistantOASST1PromotionError("OASST1 backup manifest уже существует; перезапись запрещена.")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    return backup_path


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    """Читает proof как данные и не исполняет ни поля, ни строки источника."""
    if not path.is_file():
        raise OpenAssistantOASST1PromotionError(f"Не найден {label} для OASST1 promotion.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OpenAssistantOASST1PromotionError(f"{label} содержит некорректный JSON.") from error
    if not isinstance(payload, dict):
        raise OpenAssistantOASST1PromotionError(f"{label} должен быть JSON object.")
    return payload


def _verified_audit_proof(path: Path) -> None:
    """Требует clean isolated audit именно для fixed OASST1 v0.2."""
    proof = _load_json(path, label="OASST1 staging audit")
    audit = proof.get("audit")
    if (
        proof.get("mode") != "staging_candidate_audit"
        or proof.get("candidate_sha256") != EXPECTED_CANDIDATE_SHA256
        or proof.get("source_id") != SOURCE_ID
        or not isinstance(audit, dict)
        or audit.get("records_total") != EXPECTED_CANDIDATE_RECORDS
        or audit.get("records_accepted") != EXPECTED_CANDIDATE_RECORDS
        or audit.get("records_rejected") != 0
        or audit.get("cross_split_exact_duplicates") != 0
        or audit.get("cross_split_near_duplicate_candidates") != 0
    ):
        raise OpenAssistantOASST1PromotionError("Staging audit не подтверждает fixed clean OASST1 v0.2 candidate.")


def _verified_preflight_proof(path: Path, *, active_source_count: int) -> None:
    """Требует clean preflight против неизменённого состава active sources."""
    proof = _load_json(path, label="OASST1 active preflight")
    audit = proof.get("audit")
    accepted_by_source = audit.get("accepted_by_source") if isinstance(audit, dict) else None
    if (
        proof.get("mode") != "active_corpus_preflight"
        or proof.get("candidate_sha256") != EXPECTED_CANDIDATE_SHA256
        or proof.get("candidate_source_id") != SOURCE_ID
        or proof.get("active_sources_checked") != active_source_count
        or proof.get("clean_merge") is not True
        or not isinstance(audit, dict)
        or audit.get("records_rejected") != 0
        or audit.get("cross_split_exact_duplicates") != 0
        or audit.get("cross_split_near_duplicate_candidates") != 0
        or not isinstance(accepted_by_source, dict)
        or accepted_by_source.get(SOURCE_ID) != EXPECTED_CANDIDATE_RECORDS
    ):
        raise OpenAssistantOASST1PromotionError("Active preflight не подтверждает clean OASST1 merge.")


def _assert_clean_active_audit(report: dict[str, Any], *, label: str) -> None:
    """Отделяет data cleanliness от отдельного training gate и модели."""
    if (
        report.get("records_rejected") != 0
        or report.get("cross_split_exact_duplicates") != 0
        or report.get("cross_split_near_duplicate_candidates") != 0
    ):
        raise OpenAssistantOASST1PromotionError(f"{label} содержит отклонения или межsplit-пересечения.")


def _write_immutable_backup(backup_path: Path, manifest_bytes: bytes) -> str:
    """Записывает backup через exclusive create, чтобы не уничтожить предыдущий снимок."""
    try:
        with backup_path.open("xb") as output:
            output.write(manifest_bytes)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        raise OpenAssistantOASST1PromotionError("Не удалось создать immutable backup active manifest.") from error
    return file_sha256(backup_path)


def _copy_candidate_without_overwrite(candidate: Path, raw_path: Path) -> tuple[str, int]:
    """Создаёт отдельный raw snapshot через temporary file и hard link без overwrite."""
    temporary_path: Path | None = None
    candidate_hash_before = file_sha256(candidate)
    if candidate_hash_before != EXPECTED_CANDIDATE_SHA256 or candidate.stat().st_size != EXPECTED_CANDIDATE_BYTES:
        raise OpenAssistantOASST1PromotionError("OASST1 candidate не совпадает с fixed SHA-256 или размером v0.2.")
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=raw_path.parent,
            prefix=".openassistant-oasst1-promote-",
            suffix=".jsonl.tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            with candidate.open("rb") as source:
                while True:
                    chunk = source.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        if file_sha256(candidate) != candidate_hash_before:
            raise OpenAssistantOASST1PromotionError("OASST1 candidate изменился во время копирования.")
        copied_hash = file_sha256(temporary_path)
        copied_bytes = temporary_path.stat().st_size
        if copied_hash != EXPECTED_CANDIDATE_SHA256 or copied_bytes != EXPECTED_CANDIDATE_BYTES:
            raise OpenAssistantOASST1PromotionError("Временная raw-копия OASST1 не прошла checksum или size check.")
        if raw_path.exists():
            raise OpenAssistantOASST1PromotionError("OASST1 raw target появился во время копирования; overwrite запрещён.")
        try:
            os.link(temporary_path, raw_path)
        except OSError as error:
            raise OpenAssistantOASST1PromotionError("Не удалось atomically создать OASST1 raw source.") from error
        temporary_path.unlink()
        temporary_path = None
        return copied_hash, copied_bytes
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _append_source_to_manifest(manifest_bytes: bytes, raw_sha256: str) -> bytes:
    """Добавляет единственную fixed source-entry после проверки license policy и путей."""
    try:
        payload = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OpenAssistantOASST1PromotionError("Active manifest содержит некорректный JSON.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise OpenAssistantOASST1PromotionError("Active manifest не содержит список sources.")
    sources = payload["sources"]
    source_ids = {item.get("source_id") for item in sources if isinstance(item, dict)}
    raw_paths = {item.get("raw_path") for item in sources if isinstance(item, dict)}
    if SOURCE_ID in source_ids or f"raw/{RAW_FILE_NAME}" in raw_paths:
        raise OpenAssistantOASST1PromotionError("OASST1 source уже указан в active manifest.")
    licenses = payload.get("accepted_licenses")
    if not isinstance(licenses, list) or SOURCE_LICENSE not in licenses:
        raise OpenAssistantOASST1PromotionError("Active manifest не принимает лицензию OASST1 source.")
    sources.append(
        {
            "source_id": SOURCE_ID,
            "raw_path": f"raw/{RAW_FILE_NAME}",
            "raw_sha256": raw_sha256,
            "source_kind": "open_licensed",
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "origin_url": SOURCE_ORIGIN_URL,
            "attribution": SOURCE_ATTRIBUTION,
            "approved_for_training": True,
        }
    )
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _replace_manifest_atomically(manifest_path: Path, manifest_bytes: bytes) -> str:
    """Перезаписывает manifest только через fsync'ed temporary snapshot."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=manifest_path.parent,
            prefix=".openassistant-oasst1-manifest-",
            suffix=".json.tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(manifest_bytes)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, manifest_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return file_sha256(manifest_path)


def _restore_manifest_if_unchanged(manifest_path: Path, expected_current_hash: str, backup_path: Path) -> None:
    """Восстанавливает manifest только если его не менял другой процесс после promotion."""
    if file_sha256(manifest_path) != expected_current_hash:
        raise OpenAssistantOASST1PromotionError("Manifest изменился после promotion; автоматический rollback запрещён.")
    _replace_manifest_atomically(manifest_path, backup_path.read_bytes())


def promotion_markdown(summary: OpenAssistantOASST1PromotionSummary) -> str:
    """Создаёт понятный activation record без данных диалогов или ответов."""
    audit = summary.active_audit_after
    return "\n".join(
        [
            "# OpenAssistant OASST1 — local promotion в active Brain v3 corpus",
            "",
            "Статус: clean staging v0.2 скопирован в новый immutable raw source и добавлен в local manifest.",
            f"SHA-256 candidate/raw: {summary.candidate_sha256} / {summary.raw_sha256}.",
            f"Размер raw source: {summary.raw_bytes} bytes.",
            f"Backup manifest: {summary.manifest_backup_path} ({summary.manifest_backup_sha256}).",
            f"Active manifest SHA-256: {summary.manifest_sha256_before} → {summary.manifest_sha256_after}.",
            f"Итоговый active audit: принято {audit['records_accepted']}; отклонено {audit['records_rejected']}; dialogue turns {audit['unique_dialogue_turns']}; language characters {audit['language_characters']}.",
            f"Training allowed: {audit['training_allowed']}; причины: {audit['training_block_reasons']}.",
            "",
            "Promotion не запускает обучение, не меняет работающий чат и не публикует данные в GitHub.",
            "",
        ]
    )


def promote_openassistant_oasst1(
    candidate: Path = DEFAULT_CANDIDATE,
    raw_path: Path = DEFAULT_RAW_PATH,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    backup_path: Path = DEFAULT_BACKUP_PATH,
    audit_proof: Path = DEFAULT_AUDIT_PROOF,
    preflight_proof: Path = DEFAULT_PREFLIGHT_PROOF,
    report_directory: Path = DEFAULT_REPORT_DIRECTORY,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> dict[str, Any]:
    """Выполняет доказуемый local-only promotion fixed OASST1 v0.2."""
    source_path = staging_candidate_path(candidate, staging_root)
    raw_target = _fixed_raw_path(raw_path, corpus_root)
    active_manifest_path = _fixed_manifest_path(manifest_path, corpus_root)
    backup_target = _fixed_backup_path(backup_path, corpus_root)
    if report_directory.exists():
        raise OpenAssistantOASST1PromotionError("OASST1 promotion report directory уже существует; overwrite запрещён.")
    if file_sha256(source_path) != EXPECTED_CANDIDATE_SHA256 or source_path.stat().st_size != EXPECTED_CANDIDATE_BYTES:
        raise OpenAssistantOASST1PromotionError("OASST1 staging candidate не совпадает с fixed v0.2 checksum или размером.")

    manifest_before_bytes = active_manifest_path.read_bytes()
    manifest_before_sha256 = file_sha256(active_manifest_path)
    try:
        active_manifest = load_corpus_manifest(active_manifest_path)
        active_audit_before = audit_corpus(active_manifest_path)
    except BrainV3CorpusError as error:
        raise OpenAssistantOASST1PromotionError("Current active corpus не прошёл audit до OASST1 promotion.") from error
    _assert_clean_active_audit(active_audit_before, label="Current active corpus")
    _verified_audit_proof(audit_proof)
    _verified_preflight_proof(preflight_proof, active_source_count=len(active_manifest.sources))
    backup_sha256 = _write_immutable_backup(backup_target, manifest_before_bytes)

    raw_sha256, raw_bytes = _copy_candidate_without_overwrite(source_path, raw_target)
    manifest_after_bytes = _append_source_to_manifest(manifest_before_bytes, raw_sha256)
    manifest_after_sha256 = _replace_manifest_atomically(active_manifest_path, manifest_after_bytes)
    try:
        active_audit_after = audit_corpus(active_manifest_path)
        _assert_clean_active_audit(active_audit_after, label="OASST1-promoted active corpus")
        if active_audit_after.get("accepted_by_source", {}).get(SOURCE_ID) != EXPECTED_CANDIDATE_RECORDS:
            raise OpenAssistantOASST1PromotionError("Active audit не подтвердил ожидаемое число OASST1 records.")
    except (BrainV3CorpusError, OpenAssistantOASST1PromotionError) as error:
        _restore_manifest_if_unchanged(active_manifest_path, manifest_after_sha256, backup_target)
        raise OpenAssistantOASST1PromotionError("OASST1 promotion отменён: итоговый active audit не прошёл.") from error

    summary = OpenAssistantOASST1PromotionSummary(
        candidate_path=str(source_path),
        candidate_sha256=EXPECTED_CANDIDATE_SHA256,
        raw_path=str(raw_target),
        raw_sha256=raw_sha256,
        raw_bytes=raw_bytes,
        manifest_path=str(active_manifest_path),
        manifest_sha256_before=manifest_before_sha256,
        manifest_sha256_after=manifest_after_sha256,
        manifest_backup_path=str(backup_target),
        manifest_backup_sha256=backup_sha256,
        active_audit_before=active_audit_before,
        active_audit_after=active_audit_after,
    )
    report = summary.as_dict()
    report_directory.mkdir(parents=True)
    (report_directory / "openassistant_oasst1_promotion.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "OPENASSISTANT_OASST1_PROMOTION_REPORT.md").write_text(
        promotion_markdown(summary),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Запускает fixed local promotion без source URL или training CLI параметров."""
    try:
        report = promote_openassistant_oasst1()
    except (BrainV3StagingError, OSError, OpenAssistantOASST1PromotionError, ValueError) as error:
        raise SystemExit(f"OASST1 local promotion остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
