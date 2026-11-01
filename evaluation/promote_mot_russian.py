"""Локально переносит проверенный MOT Russian v0.2 из staging в active corpus.

Promotion намеренно принимает только один заранее проверенный candidate с
зафиксированной SHA-256 и проверенными audit/preflight отчётами. До изменения
manifest создаётся неизменяемая резервная копия, raw JSONL добавляется без
перезаписи, а после изменения выполняется полный active audit. Обучение, чат и
GitHub этот скрипт не затрагивает.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_core.brain_v3_corpus import BrainV3CorpusError, audit_corpus, file_sha256, load_corpus_manifest
from ai_core.mot_russian import (
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
DEFAULT_CANDIDATE = DEFAULT_STAGING_ROOT / "mot-rus-golosameriki-cc-by-v0.2.jsonl"
RAW_FILE_NAME = "mot-rus-golosameriki-cc-by-v1.jsonl"
DEFAULT_RAW_PATH = DEFAULT_CORPUS_ROOT / "raw" / RAW_FILE_NAME
MANIFEST_FILE_NAME = "corpus_manifest.v1.json"
DEFAULT_MANIFEST_PATH = DEFAULT_CORPUS_ROOT / MANIFEST_FILE_NAME
BACKUP_FILE_NAME = "corpus_manifest.v1.before-mot-rus-golosameriki-cc-by-v1.json"
DEFAULT_BACKUP_PATH = DEFAULT_CORPUS_ROOT / "manifest_backups" / BACKUP_FILE_NAME
DEFAULT_AUDIT_PROOF = ROOT / "evaluation" / "results" / "mot-rus-golosameriki-audit-v0.2" / "mot_russian_audit.json"
DEFAULT_PREFLIGHT_PROOF = (
    ROOT / "evaluation" / "results" / "mot-rus-golosameriki-preflight-v0.2" / "mot_russian_preflight.json"
)
DEFAULT_REPORT_DIRECTORY = ROOT / "evaluation" / "results" / "mot-rus-golosameriki-promotion-v0.1"

# Эти значения принадлежат только уже прошедшей independent verification v0.2.
# CLI не позволяет подменять их произвольными аргументами.
EXPECTED_CANDIDATE_SHA256 = "fcaa3e1af5a64929afd58cb553ed7a4ff67abbd05ff76bc55c3652a14753cee2"
EXPECTED_CANDIDATE_BYTES = 503_295_180
EXPECTED_CANDIDATE_RECORDS = 98_407
COPY_CHUNK_BYTES = 1024 * 1024


class MOTPromotionError(ValueError):
    """Останавливает promotion до незащищённого изменения active corpus."""


@dataclass(frozen=True)
class MOTPromotionSummary:
    """Содержит provenance, hashes и audit-метрики без текста документов."""

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
        """Сериализует только проверяемые metadata и итоговый active audit."""
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
    """Разрешает операцию только внутри указанного локального root."""
    resolved_root = root.expanduser().resolve()
    resolved_path = path.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise MOTPromotionError(code) from error
    return resolved_path


def _fixed_raw_path(path: Path, corpus_root: Path) -> Path:
    """Проверяет точный raw target и запрещает повторную запись."""
    raw_root = corpus_root.expanduser().resolve() / "raw"
    raw_path = _safe_child(path, raw_root, code="MOT promotion пишет только в data/brain_v3/raw/.")
    if raw_path.name != RAW_FILE_NAME or raw_path.suffix.lower() != ".jsonl":
        raise MOTPromotionError("MOT promotion разрешён только для фиксированного raw file name.")
    if raw_path.exists():
        raise MOTPromotionError("MOT raw source уже существует; перезапись запрещена.")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    return raw_path


def _fixed_manifest_path(path: Path, corpus_root: Path) -> Path:
    """Принимает только canonical active manifest выбранного corpus root."""
    corpus_base = corpus_root.expanduser().resolve()
    manifest_path = _safe_child(path, corpus_base, code="MOT promotion меняет только local corpus manifest.")
    if manifest_path != corpus_base / MANIFEST_FILE_NAME or not manifest_path.is_file():
        raise MOTPromotionError("Не найден canonical active corpus manifest для MOT promotion.")
    return manifest_path


def _fixed_backup_path(path: Path, corpus_root: Path) -> Path:
    """Готовит новый immutable backup manifest без возможности overwrite."""
    backup_root = corpus_root.expanduser().resolve() / "manifest_backups"
    backup_path = _safe_child(path, backup_root, code="Backup manifest разрешён только в manifest_backups/.")
    if backup_path.name != BACKUP_FILE_NAME or backup_path.suffix.lower() != ".json":
        raise MOTPromotionError("MOT backup manifest должен иметь фиксированное имя JSON.")
    if backup_path.exists():
        raise MOTPromotionError("MOT backup manifest уже существует; перезапись запрещена.")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    return backup_path


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    """Читает только ожидаемый JSON report без исполнения его содержимого."""
    if not path.is_file():
        raise MOTPromotionError(f"Не найден {label} для MOT promotion.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MOTPromotionError(f"{label} содержит некорректный JSON.") from error
    if not isinstance(payload, dict):
        raise MOTPromotionError(f"{label} должен быть JSON object.")
    return payload


def _verified_audit_proof(path: Path) -> None:
    """Требует clean isolated audit именно для fixed MOT v0.2 candidate."""
    proof = _load_json(path, label="MOT staging audit")
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
        raise MOTPromotionError("MOT staging audit не подтверждает fixed clean v0.2 candidate.")


def _verified_preflight_proof(path: Path, *, active_source_count: int) -> None:
    """Требует clean active preflight для того же candidate и состава sources."""
    proof = _load_json(path, label="MOT active preflight")
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
        raise MOTPromotionError("MOT active preflight не подтверждает clean merge fixed v0.2 candidate.")


def _assert_clean_active_audit(report: dict[str, Any], *, label: str) -> None:
    """Отделяет чистоту данных от training gate, который остаётся отдельным."""
    if (
        report.get("records_rejected") != 0
        or report.get("cross_split_exact_duplicates") != 0
        or report.get("cross_split_near_duplicate_candidates") != 0
    ):
        raise MOTPromotionError(f"{label} содержит отклонения или межsplit-пересечения.")


def _write_immutable_backup(backup_path: Path, manifest_bytes: bytes) -> str:
    """Создаёт backup через exclusive creation, чтобы существующий не заменить."""
    try:
        with backup_path.open("xb") as output:
            output.write(manifest_bytes)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        raise MOTPromotionError("Не удалось создать immutable backup active manifest.") from error
    return file_sha256(backup_path)


def _copy_candidate_without_overwrite(candidate: Path, raw_path: Path) -> tuple[str, int]:
    """Копирует candidate через временный файл и hard link без overwrite raw."""
    temporary_path: Path | None = None
    candidate_hash_before = file_sha256(candidate)
    if candidate_hash_before != EXPECTED_CANDIDATE_SHA256 or candidate.stat().st_size != EXPECTED_CANDIDATE_BYTES:
        raise MOTPromotionError("MOT candidate не совпадает с зафиксированной SHA-256 или размером v0.2.")
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=raw_path.parent,
            prefix=".mot-rus-promote-",
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
            raise MOTPromotionError("MOT candidate изменился во время копирования; promotion остановлен.")
        copied_hash = file_sha256(temporary_path)
        copied_bytes = temporary_path.stat().st_size
        if copied_hash != EXPECTED_CANDIDATE_SHA256 or copied_bytes != EXPECTED_CANDIDATE_BYTES:
            raise MOTPromotionError("Временная копия MOT candidate не прошла checksum или size check.")
        if raw_path.exists():
            raise MOTPromotionError("MOT raw source появился во время копирования; перезапись запрещена.")
        try:
            os.link(temporary_path, raw_path)
        except OSError as error:
            raise MOTPromotionError("Не удалось atomically создать новый MOT raw source без overwrite.") from error
        temporary_path.unlink()
        temporary_path = None
        return copied_hash, copied_bytes
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _append_source_to_manifest(manifest_bytes: bytes, raw_sha256: str) -> bytes:
    """Добавляет единственную заранее определённую source entry в manifest payload."""
    try:
        payload = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MOTPromotionError("Active manifest содержит некорректный JSON.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise MOTPromotionError("Active manifest не содержит список sources.")
    sources = payload["sources"]
    source_ids = {item.get("source_id") for item in sources if isinstance(item, dict)}
    raw_paths = {item.get("raw_path") for item in sources if isinstance(item, dict)}
    if SOURCE_ID in source_ids or f"raw/{RAW_FILE_NAME}" in raw_paths:
        raise MOTPromotionError("MOT source уже указан в active manifest.")
    licenses = payload.get("accepted_licenses")
    if not isinstance(licenses, list) or SOURCE_LICENSE not in licenses:
        raise MOTPromotionError("Active manifest не принимает лицензию MOT source.")
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
    """Перезаписывает manifest только через новый fsync'ed temporary snapshot."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=manifest_path.parent,
            prefix=".mot-rus-manifest-",
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
    """Откатывает только manifest, который всё ещё совпадает с нашей версией."""
    if file_sha256(manifest_path) != expected_current_hash:
        raise MOTPromotionError("Manifest изменился после promotion; автоматический откат запрещён.")
    _replace_manifest_atomically(manifest_path, backup_path.read_bytes())


def promotion_markdown(summary: MOTPromotionSummary) -> str:
    """Формирует Markdown-учёт promotion без текста corpus documents."""
    audit = summary.active_audit_after
    return "\n".join(
        [
            "# MOT Russian — local promotion в active Brain v3 corpus",
            "",
            "Статус: clean staging v0.2 скопирован в новый immutable raw source и добавлен в local manifest.",
            f"SHA-256 candidate/raw: `{summary.candidate_sha256}` / `{summary.raw_sha256}`.",
            f"Размер raw source: {summary.raw_bytes} bytes.",
            f"Backup manifest: `{summary.manifest_backup_path}` (`{summary.manifest_backup_sha256}`).",
            f"Active manifest SHA-256: `{summary.manifest_sha256_before}` → `{summary.manifest_sha256_after}`.",
            f"Итоговый active audit: принято {audit['records_accepted']}; отклонено {audit['records_rejected']}; диалоговых ходов {audit['unique_dialogue_turns']}; языковых символов {audit['language_characters']}.",
            f"Training allowed: `{audit['training_allowed']}`; причины: {audit['training_block_reasons']}.",
            "",
            "Promotion не запускает обучение, не меняет работающий чат и не публикует данные в GitHub.",
            "",
        ]
    )


def promote_mot_russian(
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
    """Выполняет доказуемый local-only promotion fixed MOT v0.2 candidate."""
    source_path = staging_candidate_path(candidate, staging_root)
    raw_target = _fixed_raw_path(raw_path, corpus_root)
    active_manifest_path = _fixed_manifest_path(manifest_path, corpus_root)
    backup_target = _fixed_backup_path(backup_path, corpus_root)
    if report_directory.exists():
        raise MOTPromotionError("Каталог MOT promotion-отчёта уже существует; перезапись запрещена.")
    if file_sha256(source_path) != EXPECTED_CANDIDATE_SHA256 or source_path.stat().st_size != EXPECTED_CANDIDATE_BYTES:
        raise MOTPromotionError("MOT staging candidate не совпадает с зафиксированной v0.2 checksum или размером.")

    manifest_before_bytes = active_manifest_path.read_bytes()
    manifest_before_sha256 = file_sha256(active_manifest_path)
    try:
        active_manifest = load_corpus_manifest(active_manifest_path)
        active_audit_before = audit_corpus(active_manifest_path)
    except BrainV3CorpusError as error:
        raise MOTPromotionError("Current active corpus не прошёл audit до MOT promotion.") from error
    _assert_clean_active_audit(active_audit_before, label="Current active corpus")
    _verified_audit_proof(audit_proof)
    _verified_preflight_proof(preflight_proof, active_source_count=len(active_manifest.sources))
    backup_sha256 = _write_immutable_backup(backup_target, manifest_before_bytes)

    raw_sha256, raw_bytes = _copy_candidate_without_overwrite(source_path, raw_target)
    manifest_after_bytes = _append_source_to_manifest(manifest_before_bytes, raw_sha256)
    manifest_after_sha256 = _replace_manifest_atomically(active_manifest_path, manifest_after_bytes)

    try:
        active_audit_after = audit_corpus(active_manifest_path)
        _assert_clean_active_audit(active_audit_after, label="MOT-promoted active corpus")
        if active_audit_after.get("accepted_by_source", {}).get(SOURCE_ID) != EXPECTED_CANDIDATE_RECORDS:
            raise MOTPromotionError("Active audit не подтвердил ожидаемое число MOT records.")
    except (BrainV3CorpusError, MOTPromotionError) as error:
        _restore_manifest_if_unchanged(active_manifest_path, manifest_after_sha256, backup_target)
        raise MOTPromotionError("MOT promotion был отменён: итоговый active audit не прошёл.") from error

    summary = MOTPromotionSummary(
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
    (report_directory / "mot_russian_promotion.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "MOT_RUSSIAN_PROMOTION_REPORT.md").write_text(
        promotion_markdown(summary),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Запускает только fixed local MOT v0.2 promotion без CLI-override source."""
    try:
        report = promote_mot_russian()
    except (MOTPromotionError, BrainV3StagingError, OSError, ValueError) as error:
        raise SystemExit(f"MOT local promotion остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
