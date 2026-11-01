"""Очищает staging-кандидат DReaM от пересечений с active Brain v3 corpus.

Это локальный подготовительный шаг. Он не скачивает данные, не меняет active
manifest или raw/ и не запускает обучение. Новый JSONL можно создать только
в data/brain_v3/staging/ и только под новым именем: исходный staging-снимок
остаётся неизменным для воспроизводимости.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_core.brain_v3_corpus import (
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
DEFAULT_CANDIDATE = DEFAULT_STAGING_ROOT / "dream-ru-open-sentence-collection-v0.2.jsonl"
DEFAULT_OUTPUT = DEFAULT_STAGING_ROOT / "dream-ru-open-sentence-collection-v0.3.jsonl"
DEFAULT_REPORT = ROOT / "evaluation" / "results" / "dream-ru-open-active-clean-v0.3"


class DReaMActiveCleanError(ValueError):
    """Останавливает очистку до появления частичного нового JSONL."""


@dataclass(frozen=True)
class DReaMActiveCleanSummary:
    """Содержит только проверяемые метрики локальной очистки без текстов."""

    candidate_path: str
    candidate_sha256: str
    output_path: str
    output_sha256: str
    active_sources_checked: int
    candidate_records_seen: int
    records_written: int
    records_rejected: int
    rejections: dict[str, int]
    active_audit: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Возвращает отчёт, не раскрывающий предложения из корпуса."""
        return {
            "format_version": 1,
            "mode": "staging_active_overlap_clean",
            "source_id": SOURCE_ID,
            "candidate_path": self.candidate_path,
            "candidate_sha256": self.candidate_sha256,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "active_sources_checked": self.active_sources_checked,
            "candidate_records_seen": self.candidate_records_seen,
            "records_written": self.records_written,
            "records_rejected": self.records_rejected,
            "rejections": self.rejections,
            "active_audit": self.active_audit,
            "output_is_active": False,
            "active_manifest_updated": False,
            "training_started": False,
        }


def _staging_jsonl(path: Path, staging_root: Path, *, must_exist: bool) -> Path:
    """Разрешает только versioned JSONL внутри отдельной staging-папки."""
    root = staging_root.expanduser().resolve()
    candidate = path.expanduser().resolve()
    if candidate.suffix.lower() != ".jsonl":
        raise DReaMActiveCleanError("DReaM candidate и output должны быть JSONL-файлами.")
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise DReaMActiveCleanError("Очистка разрешена только внутри data/brain_v3/staging/.") from error
    if must_exist and not candidate.is_file():
        raise DReaMActiveCleanError("Не найден исходный staging-кандидат DReaM.")
    if not must_exist and candidate.exists():
        raise DReaMActiveCleanError("Нельзя перезаписывать существующий staging JSONL.")
    return candidate


def _candidate_source(path: Path) -> CorpusSource:
    """Описывает candidate теми же проверяемыми реквизитами, что и intake."""
    return CorpusSource(
        source_id=SOURCE_ID,
        raw_path=path,
        raw_sha256=file_sha256(path),
        source_kind="open_licensed",
        license_name=SOURCE_LICENSE,
        license_url=SOURCE_LICENSE_URL,
        origin_url=SOURCE_ORIGIN_URL,
        attribution=SOURCE_ATTRIBUTION,
    )


def _assert_clean_active_corpus(corpus_root: Path) -> tuple[dict[str, Any], tuple[CorpusSource, ...]]:
    """Проверяет active corpus перед чтением его отпечатков для фильтрации."""
    manifest_path = corpus_root.expanduser().resolve() / "corpus_manifest.v1.json"
    if not manifest_path.is_file():
        raise DReaMActiveCleanError("Не найден active corpus manifest.")
    try:
        active_audit = audit_corpus(manifest_path)
        manifest = load_corpus_manifest(manifest_path)
    except BrainV3CorpusError as error:
        raise DReaMActiveCleanError("Active corpus не прошёл собственный audit.") from error
    if active_audit["records_rejected"] != 0:
        raise DReaMActiveCleanError("Active corpus содержит отклонённые записи; очистка остановлена.")
    if active_audit["cross_split_exact_duplicates"] or active_audit["cross_split_near_duplicate_candidates"]:
        raise DReaMActiveCleanError("Active corpus содержит межsplit-пересечения; очистка остановлена.")
    if any(source.source_id == SOURCE_ID for source in manifest.sources):
        raise DReaMActiveCleanError("DReaM source уже добавлен в active manifest.")
    return active_audit, manifest.sources


def _active_indexes(sources: tuple[CorpusSource, ...]) -> tuple[set[str], NearDuplicateIndex, set[str]]:
    """Строит безопасные fingerprint/simhash-индексы уже активных записей."""
    exact_fingerprints: set[str] = set()
    near_index = NearDuplicateIndex()
    identifiers: set[str] = set()
    for source in sources:
        if file_sha256(source.raw_path) != source.raw_sha256:
            raise DReaMActiveCleanError("Контрольная сумма active source изменилась во время очистки.")
        for _, payload in _read_jsonl(source.raw_path):
            try:
                record = _parse_record(payload, source)
            except BrainV3RecordError as error:
                raise DReaMActiveCleanError("Active source содержит недопустимую запись.") from error
            exact_fingerprints.add(record.content_fingerprint)
            identifiers.add(record.identifier)
            near_index.add(
                record.identifier,
                DuplicateSignature(simhash=record.simhash, token_count=record.token_count),
            )
    return exact_fingerprints, near_index, identifiers


def clean_staged_dream_against_active(
    candidate: Path,
    output: Path,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> dict[str, Any]:
    """Фильтрует только cross-source exact/near пересечения в новый JSONL.

    Candidate обязан уже пройти отдельный staging audit. Если здесь выявляется
    ошибка исходного candidate или его внутренняя дубликация, выполнение
    останавливается вместо молчаливой правки данных.
    """
    source_path = _staging_jsonl(candidate, staging_root, must_exist=True)
    output_path = _staging_jsonl(output, staging_root, must_exist=False)
    if source_path == output_path:
        raise DReaMActiveCleanError("Новый staging JSONL должен иметь другое имя.")

    active_audit, active_sources = _assert_clean_active_corpus(corpus_root)
    active_exact, active_near, active_identifiers = _active_indexes(active_sources)
    candidate_source = _candidate_source(source_path)
    candidate_exact: set[str] = set()
    candidate_near = NearDuplicateIndex()
    candidate_identifiers: set[str] = set()
    rejections: Counter[str] = Counter()
    seen = 0
    written = 0
    temporary_path: Path | None = None

    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".dream-active-clean-",
            suffix=".jsonl.tmp",
            dir=output_path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination:
            for _, payload in _read_jsonl(source_path):
                seen += 1
                try:
                    record = _parse_record(payload, candidate_source)
                except BrainV3RecordError as error:
                    raise DReaMActiveCleanError("Staging candidate не прошёл строгую повторную проверку.") from error
                if record.identifier in active_identifiers or record.identifier in candidate_identifiers:
                    raise DReaMActiveCleanError("Найдена коллизия id; candidate нельзя очищать автоматически.")
                if record.content_fingerprint in candidate_exact:
                    raise DReaMActiveCleanError("Candidate содержит внутренний точный дубль.")
                if candidate_near.find(DuplicateSignature(record.simhash, record.token_count)) is not None:
                    raise DReaMActiveCleanError("Candidate содержит внутренний близкий дубль.")
                if record.content_fingerprint in active_exact:
                    rejections["exact_duplicate_with_active"] += 1
                    continue
                if active_near.find(DuplicateSignature(record.simhash, record.token_count)) is not None:
                    rejections["near_duplicate_with_active"] += 1
                    continue

                candidate_identifiers.add(record.identifier)
                candidate_exact.add(record.content_fingerprint)
                candidate_near.add(
                    record.identifier,
                    DuplicateSignature(simhash=record.simhash, token_count=record.token_count),
                )
                destination.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                written += 1

        if written == 0:
            raise DReaMActiveCleanError("После фильтрации не осталось записей; новый JSONL не создаётся.")
        os.replace(temporary_path, output_path)
        temporary_path = None
    except (BrainV3CorpusError, OSError) as error:
        raise DReaMActiveCleanError("Не удалось безопасно создать очищенный staging JSONL.") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    summary = DReaMActiveCleanSummary(
        candidate_path=str(source_path),
        candidate_sha256=file_sha256(source_path),
        output_path=str(output_path),
        output_sha256=file_sha256(output_path),
        active_sources_checked=len(active_sources),
        candidate_records_seen=seen,
        records_written=written,
        records_rejected=seen - written,
        rejections=dict(sorted(rejections.items())),
        active_audit=active_audit,
    )
    return summary.as_dict()


def summary_markdown(report: dict[str, Any]) -> str:
    """Формирует короткий Markdown-учёт только из чисел и контрольных сумм."""
    return "\n".join(
        [
            "# Очистка DReaM-ru-open относительно active Brain v3 corpus",
            "",
            "Статус: создан новый staging JSONL; active manifest, raw и обучение не изменялись.",
            f"Проверено active sources: {report['active_sources_checked']}.",
            f"Исходный candidate SHA-256: `{report['candidate_sha256']}`.",
            f"Новый candidate SHA-256: `{report['output_sha256']}`.",
            f"Проверено строк: {report['candidate_records_seen']}; записано: {report['records_written']}; исключено: {report['records_rejected']}.",
            f"Причины исключения: {report['rejections']}.",
            "",
            "Новый файл всё ещё является staging-кандидатом. До какого-либо добавления в active corpus требуются независимый staging audit и повторный preflight.",
            "",
        ]
    )


def create_cleaned_candidate(candidate: Path, output: Path, report_directory: Path) -> dict[str, Any]:
    """Создаёт новый JSONL и отдельный безопасный JSON/Markdown отчёт."""
    if report_directory.exists():
        raise DReaMActiveCleanError(f"Каталог отчёта уже существует: {report_directory}")
    report = clean_staged_dream_against_active(candidate, output)
    report_directory.mkdir(parents=True)
    (report_directory / "dream_active_clean.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "DREAM_ACTIVE_CLEAN_REPORT.md").write_text(summary_markdown(report), encoding="utf-8")
    return report


def main() -> None:
    """Запускает локальную очистку с фиксированными versioned путями по умолчанию."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        report = create_cleaned_candidate(args.candidate, args.output, args.report)
    except (DReaMActiveCleanError, OSError, ValueError) as error:
        raise SystemExit(f"Очистка DReaM остановлена: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
