"""Проверяет DReaM staging-кандидат вместе с active corpus без activation."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ai_core.brain_v3_corpus import audit_corpus, file_sha256
from ai_core.korp_dream_ru_open import (
    DEFAULT_STAGING_ROOT,
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_ROOT = ROOT / "data" / "brain_v3"
DEFAULT_CANDIDATE = DEFAULT_CORPUS_ROOT / "staging" / "dream-ru-open-sentence-collection-v0.2.jsonl"
DEFAULT_REPORT = ROOT / "evaluation" / "results" / "dream-ru-open-active-preflight-v0.1"


class DReaMActivePreflightError(ValueError):
    """Останавливает preflight до касания active manifest или raw-файлов."""


def _candidate_path(candidate: Path, staging_root: Path) -> Path:
    """Принимает только готовый DReaM JSONL из отдельной staging-папки."""
    root = staging_root.expanduser().resolve()
    path = candidate.expanduser().resolve()
    if path.suffix.lower() != ".jsonl" or not path.is_file():
        raise DReaMActivePreflightError("DReaM candidate должен быть существующим staging JSONL.")
    try:
        path.relative_to(root)
    except ValueError as error:
        raise DReaMActivePreflightError("Preflight разрешён только для data/brain_v3/staging/.") from error
    return path


def _load_manifest_payload(corpus_root: Path) -> dict[str, Any]:
    """Читает active manifest только как исходные метаданные для временной копии."""
    path = corpus_root.expanduser().resolve() / "corpus_manifest.v1.json"
    if not path.is_file():
        raise DReaMActivePreflightError("Не найден active corpus manifest для preflight.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise DReaMActivePreflightError("Active corpus manifest содержит некорректный JSON.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise DReaMActivePreflightError("Active corpus manifest не содержит список sources.")
    return payload


def _active_source_path(corpus_root: Path, raw_path: Any) -> Path:
    """Разрешает hard-link только существующего raw JSONL активного корпуса."""
    if not isinstance(raw_path, str):
        raise DReaMActivePreflightError("Active manifest source имеет недопустимый raw_path.")
    relative = Path(raw_path)
    if relative.is_absolute() or relative.suffix.lower() != ".jsonl":
        raise DReaMActivePreflightError("Active manifest source должен ссылаться на JSONL.")
    root = corpus_root.expanduser().resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root / "raw")
    except ValueError as error:
        raise DReaMActivePreflightError("Preflight принимает active source только из raw/.") from error
    if not candidate.is_file():
        raise DReaMActivePreflightError("Не найден raw JSONL, указанный active manifest.")
    return candidate


def _dream_source_entry(candidate: Path) -> dict[str, Any]:
    """Создаёт временную manifest-запись DReaM без изменения настоящего manifest."""
    return {
        "source_id": SOURCE_ID,
        "raw_path": "raw/dream-candidate.jsonl",
        "raw_sha256": file_sha256(candidate),
        "source_kind": "open_licensed",
        "license": SOURCE_LICENSE,
        "license_url": SOURCE_LICENSE_URL,
        "origin_url": SOURCE_ORIGIN_URL,
        "attribution": SOURCE_ATTRIBUTION,
        "approved_for_training": True,
    }


def preflight_dream_against_active(
    candidate: Path,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> dict[str, Any]:
    """Аудирует temporary manifest: active raw sources + DReaM hard link.

    Единственные временные файлы находятся в `data/brain_v3/.dream-active-*` и
    уничтожаются при выходе из `TemporaryDirectory`. Ни одна запись active
    manifest, raw или staging не перезаписывается.
    """
    source_path = _candidate_path(candidate, staging_root)
    corpus_base = corpus_root.expanduser().resolve()
    payload = _load_manifest_payload(corpus_base)
    active_sources = payload["sources"]
    source_ids = {item.get("source_id") for item in active_sources if isinstance(item, dict)}
    if SOURCE_ID in source_ids:
        raise DReaMActivePreflightError("DReaM source уже присутствует в active manifest.")

    with tempfile.TemporaryDirectory(prefix=".dream-active-preflight-", dir=corpus_base) as directory:
        temporary_root = Path(directory)
        raw_directory = temporary_root / "raw"
        raw_directory.mkdir()
        provisional_sources: list[dict[str, Any]] = []

        for index, source in enumerate(active_sources):
            if not isinstance(source, dict):
                raise DReaMActivePreflightError("Active manifest source должен быть JSON object.")
            original_path = _active_source_path(corpus_base, source.get("raw_path"))
            linked_name = f"active-{index:03d}.jsonl"
            linked_path = raw_directory / linked_name
            try:
                os.link(original_path, linked_path)
            except OSError as error:
                raise DReaMActivePreflightError("Не удалось создать hard link active source для preflight.") from error
            copied_source = dict(source)
            copied_source["raw_path"] = f"raw/{linked_name}"
            copied_source["raw_sha256"] = file_sha256(original_path)
            provisional_sources.append(copied_source)

        dream_link = raw_directory / "dream-candidate.jsonl"
        try:
            os.link(source_path, dream_link)
        except OSError as error:
            raise DReaMActivePreflightError("Не удалось создать hard link DReaM candidate для preflight.") from error
        provisional_sources.append(_dream_source_entry(source_path))

        provisional_manifest = dict(payload)
        provisional_manifest["sources"] = provisional_sources
        manifest_path = temporary_root / "corpus_manifest.v1.json"
        manifest_path.write_text(
            json.dumps(provisional_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        audit = audit_corpus(manifest_path)

    clean_merge = audit["records_rejected"] == 0 and audit["cross_split_exact_duplicates"] == 0 and audit[
        "cross_split_near_duplicate_candidates"
    ] == 0
    return {
        "format_version": 1,
        "mode": "active_corpus_preflight",
        "candidate_path": str(source_path),
        "candidate_sha256": file_sha256(source_path),
        "candidate_source_id": SOURCE_ID,
        "active_sources_checked": len(active_sources),
        "audit": audit,
        "clean_merge": clean_merge,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
    }


def summary_markdown(report: dict[str, Any]) -> str:
    """Формирует ясный preflight-отчёт только из метрик и причин."""
    audit = report["audit"]
    return "\n".join(
        [
            "# Preflight DReaM-ru-open против active Brain v3 corpus",
            "",
            "Статус: временная проверка с hard links; active manifest, raw и обучение не изменялись.",
            f"Проверено active sources: {report['active_sources_checked']}; candidate: `{report['candidate_source_id']}`.",
            f"SHA-256 candidate JSONL: `{report['candidate_sha256']}`.",
            f"Принято: {audit['records_accepted']}; отклонено: {audit['records_rejected']}; причины: {audit['rejections']}.",
            f"Межsplit точных дублей: {audit['cross_split_exact_duplicates']}; близких кандидатов: {audit['cross_split_near_duplicate_candidates']}.",
            f"Чистое слияние: `{report['clean_merge']}`.",
            f"Training allowed временного объединения: `{audit['training_allowed']}`; причины: {audit['training_block_reasons']}.",
            "",
            "Даже чистое слияние не переносит candidate в raw/ автоматически. Для этого нужен отдельный локальный шаг с новым immutable raw JSONL, manifest и повторным active audit.",
            "",
        ]
    )


def create_preflight(candidate: Path, report_directory: Path) -> dict[str, Any]:
    """Сохраняет новый JSON/Markdown report без источниковых предложений."""
    if report_directory.exists():
        raise ValueError(f"Каталог отчёта уже существует: {report_directory}")
    report = preflight_dream_against_active(candidate)
    report_directory.mkdir(parents=True)
    (report_directory / "dream_active_preflight.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "DREAM_ACTIVE_PREFLIGHT_REPORT.md").write_text(
        summary_markdown(report),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Выполняет preflight DReaM candidate без параметров внешнего источника."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        report = create_preflight(args.candidate, args.report)
    except (DReaMActivePreflightError, OSError, ValueError) as error:
        raise SystemExit(f"DReaM active preflight остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
