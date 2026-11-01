"""Очищает MOT Russian staging-кандидат от пересечений с active Brain v3 corpus.

Это локальный подготовительный шаг. Он не скачивает данные, не меняет active
manifest или raw/ и не запускает обучение. Новый JSONL создаётся только в
staging и только под новым именем: исходный staging-снимок остаётся неизменным
для воспроизводимости и проверки происхождения.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
from ai_core.mot_russian import (
    DEFAULT_STAGING_ROOT,
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_ROOT = ROOT / "data" / "brain_v3"
DEFAULT_CANDIDATE = DEFAULT_STAGING_ROOT / "mot-rus-golosameriki-cc-by-v0.1.jsonl"
DEFAULT_OUTPUT = DEFAULT_STAGING_ROOT / "mot-rus-golosameriki-cc-by-v0.2.jsonl"
DEFAULT_REPORT = ROOT / "evaluation" / "results" / "mot-rus-golosameriki-active-clean-v0.2"


class MOTActiveCleanError(ValueError):
    """Останавливает очистку до появления частичного нового JSONL."""


@dataclass(frozen=True)
class MOTActiveCleanSummary:
    """Содержит только проверяемые метрики локальной очистки без текстов."""

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
        """Возвращает отчёт без текста документов или метаданных статей."""
        return {
            "format_version": 1,
            "mode": "staging_active_overlap_clean",
            "source_id": SOURCE_ID,
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
        }


@dataclass(frozen=True)
class MOTActiveCleanProgress:
    """Передаёт безопасный статус очистки без текста active или staging данных."""

    phase: str
    active_sources_checked: int
    candidate_records_seen: int
    records_written: int
    records_rejected: int
    output_bytes: int

    def as_dict(self) -> dict[str, int | str]:
        """Возвращает только счётчики и фиксированное имя фазы."""
        return {
            "phase": self.phase,
            "active_sources_checked": self.active_sources_checked,
            "candidate_records_seen": self.candidate_records_seen,
            "records_written": self.records_written,
            "records_rejected": self.records_rejected,
            "output_bytes": self.output_bytes,
        }


def _staging_jsonl(path: Path, staging_root: Path, *, must_exist: bool) -> Path:
    """Разрешает только versioned JSONL внутри отдельной staging-папки."""
    root = staging_root.expanduser().resolve()
    candidate = path.expanduser().resolve()
    if candidate.suffix.lower() != ".jsonl":
        raise MOTActiveCleanError("MOT candidate и output должны быть JSONL-файлами.")
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise MOTActiveCleanError("Очистка разрешена только внутри data/brain_v3/staging/.") from error
    if must_exist and not candidate.is_file():
        raise MOTActiveCleanError("Не найден исходный MOT staging-кандидат.")
    if not must_exist and candidate.exists():
        raise MOTActiveCleanError("Нельзя перезаписывать существующий MOT staging JSONL.")
    candidate.parent.mkdir(parents=True, exist_ok=True)
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
        raise MOTActiveCleanError("Не найден active corpus manifest.")
    try:
        active_audit = audit_corpus(manifest_path)
        manifest = load_corpus_manifest(manifest_path)
    except BrainV3CorpusError as error:
        raise MOTActiveCleanError("Active corpus не прошёл собственный audit.") from error
    if active_audit["records_rejected"] != 0:
        raise MOTActiveCleanError("Active corpus содержит отклонённые записи; очистка остановлена.")
    if active_audit["cross_split_exact_duplicates"] or active_audit["cross_split_near_duplicate_candidates"]:
        raise MOTActiveCleanError("Active corpus содержит межsplit-пересечения; очистка остановлена.")
    if any(source.source_id == SOURCE_ID for source in manifest.sources):
        raise MOTActiveCleanError("MOT source уже присутствует в active manifest.")
    return active_audit, manifest.sources


def _active_indexes(sources: tuple[CorpusSource, ...]) -> tuple[set[str], NearDuplicateIndex, set[str]]:
    """Строит fingerprint/simhash-индексы уже активных проверенных записей."""
    exact_fingerprints: set[str] = set()
    near_index = NearDuplicateIndex()
    identifiers: set[str] = set()
    for source in sources:
        if file_sha256(source.raw_path) != source.raw_sha256:
            raise MOTActiveCleanError("Контрольная сумма active source изменилась во время очистки.")
        for _, payload in _read_jsonl(source.raw_path):
            try:
                record = _parse_record(payload, source)
            except BrainV3RecordError as error:
                raise MOTActiveCleanError("Active source содержит недопустимую запись.") from error
            exact_fingerprints.add(record.content_fingerprint)
            identifiers.add(record.identifier)
            near_index.add(
                record.identifier,
                DuplicateSignature(simhash=record.simhash, token_count=record.token_count),
            )
    return exact_fingerprints, near_index, identifiers


def _emit_progress(
    callback: Callable[[MOTActiveCleanProgress], None] | None,
    *,
    phase: str,
    active_sources_checked: int,
    candidate_records_seen: int,
    records_written: int,
    rejections: Counter[str],
    output_bytes: int,
) -> None:
    """Отправляет opt-in progress без record ID, URL или исходного текста."""
    if callback is None:
        return
    callback(
        MOTActiveCleanProgress(
            phase=phase,
            active_sources_checked=active_sources_checked,
            candidate_records_seen=candidate_records_seen,
            records_written=records_written,
            records_rejected=sum(rejections.values()),
            output_bytes=output_bytes,
        )
    )


def clean_staged_mot_against_active(
    candidate: Path,
    output: Path,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
    progress_callback: Callable[[MOTActiveCleanProgress], None] | None = None,
    progress_every_records: int = 25_000,
) -> dict[str, Any]:
    """Фильтрует только cross-source exact/near пересечения в новый JSONL.

    Candidate повторно валидируется построчно. Если выявляется malformed record,
    коллизия id или внутренний дубль, очистка прекращается вместо молчаливой
    правки. Active manifest, raw и исходный candidate не изменяются.
    """
    if isinstance(progress_every_records, bool) or not isinstance(progress_every_records, int) or progress_every_records <= 0:
        raise MOTActiveCleanError("Интервал progress очистки MOT должен быть положительным целым числом.")
    source_path = _staging_jsonl(candidate, staging_root, must_exist=True)
    output_path = _staging_jsonl(output, staging_root, must_exist=False)
    if source_path == output_path:
        raise MOTActiveCleanError("Новый staging JSONL должен иметь другое имя.")

    rejections: Counter[str] = Counter()
    _emit_progress(
        progress_callback,
        phase="active_audit_started",
        active_sources_checked=0,
        candidate_records_seen=0,
        records_written=0,
        rejections=rejections,
        output_bytes=0,
    )
    active_audit, active_sources = _assert_clean_active_corpus(corpus_root)
    _emit_progress(
        progress_callback,
        phase="active_audit_complete",
        active_sources_checked=len(active_sources),
        candidate_records_seen=0,
        records_written=0,
        rejections=rejections,
        output_bytes=0,
    )
    active_exact, active_near, active_identifiers = _active_indexes(active_sources)
    _emit_progress(
        progress_callback,
        phase="active_indexes_ready",
        active_sources_checked=len(active_sources),
        candidate_records_seen=0,
        records_written=0,
        rejections=rejections,
        output_bytes=0,
    )
    candidate_source = _candidate_source(source_path)
    candidate_exact: set[str] = set()
    candidate_near = NearDuplicateIndex()
    candidate_identifiers: set[str] = set()
    seen = 0
    written = 0
    output_bytes = 0
    temporary_path: Path | None = None

    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".mot-active-clean-",
            suffix=".jsonl.tmp",
            dir=output_path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination:
            for _, payload in _read_jsonl(source_path):
                seen += 1
                if seen % progress_every_records == 0:
                    _emit_progress(
                        progress_callback,
                        phase="candidate_filtering",
                        active_sources_checked=len(active_sources),
                        candidate_records_seen=seen,
                        records_written=written,
                        rejections=rejections,
                        output_bytes=output_bytes,
                    )
                try:
                    record = _parse_record(payload, candidate_source)
                except BrainV3RecordError as error:
                    raise MOTActiveCleanError("Staging candidate не прошёл строгую повторную проверку.") from error
                if record.identifier in active_identifiers or record.identifier in candidate_identifiers:
                    raise MOTActiveCleanError("Найдена коллизия id; candidate нельзя очищать автоматически.")
                if record.content_fingerprint in candidate_exact:
                    raise MOTActiveCleanError("Candidate содержит внутренний точный дубль.")
                signature = DuplicateSignature(record.simhash, record.token_count)
                if candidate_near.find(signature) is not None:
                    raise MOTActiveCleanError("Candidate содержит внутренний близкий дубль.")
                if record.content_fingerprint in active_exact:
                    rejections["exact_duplicate_with_active"] += 1
                    continue
                if active_near.find(signature) is not None:
                    rejections["near_duplicate_with_active"] += 1
                    continue

                serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                encoded_bytes = len(serialized.encode("utf-8")) + 1
                if output_bytes + encoded_bytes > MAX_SOURCE_BYTES:
                    raise MOTActiveCleanError("Очищенный MOT output превысил лимит размера source JSONL.")
                candidate_identifiers.add(record.identifier)
                candidate_exact.add(record.content_fingerprint)
                candidate_near.add(record.identifier, signature)
                destination.write(serialized + "\n")
                output_bytes += encoded_bytes
                written += 1

        if written == 0:
            raise MOTActiveCleanError("После фильтрации не осталось записей; новый JSONL не создаётся.")
        if output_path.exists():
            raise MOTActiveCleanError("MOT staging output появился во время очистки; перезапись запрещена.")
        assert temporary_path is not None
        os.replace(temporary_path, output_path)
        temporary_path = None
    except (BrainV3CorpusError, OSError) as error:
        raise MOTActiveCleanError("Не удалось безопасно создать очищенный MOT staging JSONL.") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    _emit_progress(
        progress_callback,
        phase="candidate_filtering_complete",
        active_sources_checked=len(active_sources),
        candidate_records_seen=seen,
        records_written=written,
        rejections=rejections,
        output_bytes=output_bytes,
    )

    summary = MOTActiveCleanSummary(
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
    )
    return summary.as_dict()


def summary_markdown(report: dict[str, Any]) -> str:
    """Формирует Markdown-учёт только из счётчиков и контрольных сумм."""
    return "\n".join(
        [
            "# Очистка MOT Russian относительно active Brain v3 corpus",
            "",
            "Статус: создан новый staging JSONL; active manifest, raw и обучение не изменялись.",
            f"Проверено active sources: {report['active_sources_checked']}.",
            f"Исходный candidate SHA-256: `{report['candidate_sha256']}`.",
            f"Новый candidate SHA-256: `{report['output_sha256']}`.",
            f"Размер нового candidate: {report['output_bytes']} bytes.",
            f"Проверено строк: {report['candidate_records_seen']}; записано: {report['records_written']}; исключено: {report['records_rejected']}.",
            f"Причины исключения: {report['rejections']}.",
            "",
            "Новый файл остаётся staging-кандидатом. До любого добавления в active corpus требуются независимый staging audit и повторный preflight.",
            "",
        ]
    )


def create_cleaned_candidate(
    candidate: Path,
    output: Path,
    report_directory: Path,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
    progress_every_records: int = 25_000,
) -> dict[str, Any]:
    """Создаёт новый JSONL и отдельный JSON/Markdown отчёт без raw documents."""
    if report_directory.exists():
        raise MOTActiveCleanError("Каталог MOT clean-отчёта уже существует; перезапись запрещена.")
    report = clean_staged_mot_against_active(
        candidate,
        output,
        corpus_root=corpus_root,
        staging_root=staging_root,
        progress_callback=_print_progress,
        progress_every_records=progress_every_records,
    )
    report_directory.mkdir(parents=True)
    (report_directory / "mot_active_clean.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_directory / "MOT_ACTIVE_CLEAN_REPORT.md").write_text(summary_markdown(report), encoding="utf-8")
    return report


def _print_progress(progress: MOTActiveCleanProgress) -> None:
    """Печатает безопасные счётчики долгой очистки без текстов документов."""
    print(
        json.dumps(
            {
                "event": "mot_active_clean_progress",
                **progress.as_dict(),
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


def main() -> None:
    """Запускает локальную очистку MOT candidate с фиксированными путями по умолчанию."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--progress-every-records",
        type=int,
        default=25_000,
        help="Печатать счётчики после каждых N candidate records (по умолчанию: 25000).",
    )
    args = parser.parse_args()
    try:
        report = create_cleaned_candidate(
            args.candidate,
            args.output,
            args.report,
            progress_every_records=args.progress_every_records,
        )
    except (MOTActiveCleanError, OSError, ValueError) as error:
        raise SystemExit(f"Очистка MOT остановлена: {error}") from error
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
