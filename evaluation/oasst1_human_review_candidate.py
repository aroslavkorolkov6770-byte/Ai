"""Создаёт и проверяет approved-only staging candidate после human review OASST1.

Этот этап намеренно отделён от исходного OASST1 source и active corpus. Он
читает только уже проверенные queue, decision ledger и raw JSONL, формирует
новый immutable JSONL исключительно из вручную одобренных ID, а затем может
запустить read-only audit/preflight. Ни этот модуль, ни его отчёты не запускают
обучение, не меняют active manifest и не публикуют данные.

В JSON и Markdown отчёты попадают только хэши, счётчики, split и статусы. Текст
диалогов остаётся только внутри staging JSONL, который необходим для будущего
изолированного технического аудита, и никогда не копируется в отчёт.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from ai_core.brain_v3_corpus import content_sha256, file_sha256, load_corpus_manifest
from ai_core.openassistant_oasst1 import (
    SOURCE_ID as ORIGINAL_SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
)
from evaluation.brain_v3_staging import (
    DEFAULT_CORPUS_ROOT,
    DEFAULT_STAGING_ROOT,
    BrainV3StagingError,
    StagedSourceDescriptor,
    audit_staged_candidate,
    preflight_staged_candidate_against_active,
    preflight_staged_replacement_against_active,
    prospective_dialogue_coverage_for_staged_candidate,
    prospective_dialogue_coverage_for_staged_replacement,
)
from evaluation.oasst1_human_review import (
    DEFAULT_DECISIONS_PATH,
    DEFAULT_QUEUE_PATH,
    DEFAULT_SOURCE_PATH,
    RAW_ROOT,
    RESULTS_ROOT,
    REVIEW_ROOT,
    OASST1HumanReviewError,
    build_review_summary,
    load_review_decisions,
    load_review_queue,
    load_source_records_for_queue,
)


ROOT = Path(__file__).resolve().parent.parent
SUBSET_SOURCE_ID = "openassistant-oasst1-human-reviewed-v0.1"
SUBSET_ATTRIBUTION = (
    "OpenAssistant Conversations Dataset (OASST1), Apache-2.0; "
    "local human-reviewed approved subset v0.4"
)
DEFAULT_CANDIDATE = (
    DEFAULT_STAGING_ROOT / "openassistant-oasst1-human-reviewed-v0.1.jsonl"
)
DEFAULT_BUILD_REPORT_DIRECTORY = (
    RESULTS_ROOT / "oasst1-human-reviewed-candidate-v0.1"
)
DEFAULT_AUDIT_REPORT_DIRECTORY = (
    RESULTS_ROOT / "oasst1-human-reviewed-candidate-audit-v0.1"
)
DEFAULT_REPLACEMENT_REPORT_DIRECTORY = (
    RESULTS_ROOT / "oasst1-human-reviewed-replacement-snapshot-v0.1"
)


class OASST1HumanReviewCandidateError(ValueError):
    """Останавливает candidate workflow до изменения active corpus или training."""


def human_reviewed_descriptor() -> StagedSourceDescriptor:
    """Возвращает единственное provenance-описание approved-only candidate."""
    return StagedSourceDescriptor(
        source_id=SUBSET_SOURCE_ID,
        license_name=SOURCE_LICENSE,
        license_url=SOURCE_LICENSE_URL,
        origin_url=SOURCE_ORIGIN_URL,
        attribution=SUBSET_ATTRIBUTION,
    )


def _safe_path_inside(path: Path, root: Path, error_code: str) -> Path:
    """Разрешает candidate и reports только в их локальных служебных папках."""
    resolved_path = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise OASST1HumanReviewCandidateError(error_code) from error
    return resolved_path


def _prepare_candidate_destination(destination: Path, staging_root: Path) -> Path:
    """Проверяет immutable output до чтения raw и запрещает перезапись JSONL."""
    safe_destination = _safe_path_inside(
        destination,
        staging_root,
        "oasst1_human_review_candidate_outside_staging",
    )
    if safe_destination.suffix.lower() != ".jsonl":
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_candidate_extension_invalid"
        )
    if safe_destination.exists():
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_candidate_already_exists"
        )
    safe_destination.parent.mkdir(parents=True, exist_ok=True)
    return safe_destination


def _prepare_report_directory(directory: Path, results_root: Path) -> Path:
    """Резервирует новый versioned report directory без перезаписи истории."""
    safe_directory = _safe_path_inside(
        directory,
        results_root,
        "oasst1_human_review_candidate_report_outside_results",
    )
    if safe_directory.exists():
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_candidate_report_already_exists"
        )
    return safe_directory


def _candidate_record(
    record: Mapping[str, Any],
    descriptor: StagedSourceDescriptor,
) -> dict[str, Any]:
    """Меняет только provenance и self-hash уже проверенной raw-записи."""
    candidate = dict(record)
    candidate["provenance"] = {
        "source_id": descriptor.source_id,
        "license": descriptor.license_name,
        "license_url": descriptor.license_url,
        "origin_url": descriptor.origin_url,
        "attribution": descriptor.attribution,
    }
    candidate["content_sha256"] = content_sha256(candidate)
    return candidate


def _write_immutable_jsonl(destination: Path, records: list[Mapping[str, Any]]) -> None:
    """Создаёт JSONL через временный файл и hard link без права перезаписи."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            for record in records:
                output.write(
                    json.dumps(dict(record), ensure_ascii=False, sort_keys=True)
                )
                output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary_path, destination)
        except FileExistsError as error:
            raise OASST1HumanReviewCandidateError(
                "oasst1_human_review_candidate_already_exists"
            ) from error
        except OSError as error:
            raise OASST1HumanReviewCandidateError(
                "oasst1_human_review_candidate_write_failed"
            ) from error
    except OSError as error:
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_candidate_write_failed"
        ) from error
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_report(
    directory: Path,
    filename_prefix: str,
    report: Mapping[str, Any],
    markdown: str,
) -> None:
    """Записывает metadata-only JSON и Markdown в новый immutable directory."""
    try:
        directory.mkdir(parents=True)
        (directory / f"{filename_prefix}.json").write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (directory / f"{filename_prefix.upper()}.md").write_text(
            markdown,
            encoding="utf-8",
        )
    except OSError as error:
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_candidate_report_write_failed"
        ) from error


def build_report_markdown(report: Mapping[str, Any]) -> str:
    """Рендерит журнал build без текста или фрагментов source-диалогов."""
    split_counts = report["candidate_records_by_split"]
    splits = ", ".join(
        f"{split}: {split_counts.get(split, 0)}"
        for split in ("train", "validation", "test")
    )
    return "\n".join(
        (
            "# Brain v3 — OASST1 approved human-review candidate",
            "",
            "## Результат",
            "",
            f"- Записей в review queue: `{report['queue_selected_records']}`.",
            f"- Ручных решений: `{report['reviewed_records']}`.",
            f"- Одобрено человеком: `{report['approved_records']}`.",
            f"- Отклонено человеком: `{report['rejected_records']}`.",
            f"- В immutable staging candidate записано: `{report['candidate_records']}`.",
            f"- Split: `{splits}`.",
            f"- SHA-256 candidate: `{report['candidate_sha256']}`.",
            "",
            "## Граница",
            "",
            "Candidate создан только для следующего изолированного audit. Он не "
            "является active source, не меняет manifest, не запускает обучение и "
            "не передаётся в GitHub.",
            "",
            "## Следующий gate",
            "",
            "Запустить isolated audit, active-corpus preflight и только при чистом "
            "preflight — prospective dialogue coverage и отдельную quality review.",
            "",
        )
    )


def build_human_reviewed_candidate(
    *,
    queue_path: Path = DEFAULT_QUEUE_PATH,
    source_path: Path = DEFAULT_SOURCE_PATH,
    decisions_path: Path = DEFAULT_DECISIONS_PATH,
    destination: Path = DEFAULT_CANDIDATE,
    report_directory: Path = DEFAULT_BUILD_REPORT_DIRECTORY,
    results_root: Path = RESULTS_ROOT,
    raw_root: Path = RAW_ROOT,
    reviews_root: Path = REVIEW_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> dict[str, Any]:
    """Создаёт approved-only JSONL после полного ручного review.

    До первой записи функция проверяет полноту ledger и резервирует оба новых
    output-path. При сбое она не изменяет active corpus, текущий raw source,
    веса или training state.
    """
    safe_destination = _prepare_candidate_destination(destination, staging_root)
    safe_report_directory = _prepare_report_directory(report_directory, results_root)
    queue = load_review_queue(queue_path, results_root=results_root)
    decisions = load_review_decisions(
        decisions_path,
        queue,
        reviews_root=reviews_root,
    )
    review_summary = build_review_summary(queue, decisions)
    if not review_summary["review_complete"]:
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_candidate_review_incomplete"
        )
    if review_summary["approved_records"] <= 0:
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_candidate_no_approved_records"
        )

    source_records = load_source_records_for_queue(
        queue,
        source_path,
        raw_root=raw_root,
    )
    decisions_by_id = {
        decision["candidate_id"]: decision for decision in decisions
    }
    descriptor = human_reviewed_descriptor()
    approved_records = [
        _candidate_record(source_records[item["id"]], descriptor)
        for item in queue["items"]
        if decisions_by_id[item["id"]]["decision"] == "approved"
    ]
    if len(approved_records) != review_summary["approved_records"]:
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_candidate_approved_count_mismatch"
        )

    _write_immutable_jsonl(safe_destination, approved_records)
    split_counts = Counter(record["split"] for record in approved_records)
    report: dict[str, Any] = {
        "format_version": 1,
        "mode": "oasst1_human_review_approved_candidate",
        "candidate_source_id": descriptor.source_id,
        "parent_source_id": ORIGINAL_SOURCE_ID,
        "queue_sha256": file_sha256(queue_path.expanduser().resolve()),
        "review_ledger_sha256": file_sha256(decisions_path.expanduser().resolve()),
        "source_raw_sha256": file_sha256(source_path.expanduser().resolve()),
        "candidate_path": str(safe_destination),
        "candidate_sha256": file_sha256(safe_destination),
        "candidate_bytes": safe_destination.stat().st_size,
        "queue_selected_records": review_summary["queue_selected_records"],
        "reviewed_records": review_summary["reviewed_records"],
        "approved_records": review_summary["approved_records"],
        "rejected_records": review_summary["rejected_records"],
        "candidate_records": len(approved_records),
        "candidate_records_by_split": {
            split: split_counts[split] for split in ("train", "validation", "test")
        },
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
        "chat_changed": False,
        "github_changed": False,
        "next_gate": "isolated audit, active-corpus preflight, coverage and quality review",
    }
    _write_report(
        safe_report_directory,
        "oasst1_human_review_candidate",
        report,
        build_report_markdown(report),
    )
    return report


def audit_report_markdown(report: Mapping[str, Any]) -> str:
    """Рендерит результат read-only candidate gates без raw dialogue text."""
    isolated = report["isolated_audit"]
    preflight = report["active_corpus_preflight"]
    lines = [
        "# Brain v3 — OASST1 human-review candidate audit",
        "",
        "## Isolated audit",
        "",
        f"- Записей принято: `{isolated['audit']['records_accepted']}`.",
        f"- Записей отклонено: `{isolated['audit']['records_rejected']}`.",
        f"- Candidate training allowed сам по себе: `{str(isolated['audit']['training_allowed']).lower()}`.",
        "",
        "## Active-corpus preflight",
        "",
        f"- Статус: `{preflight['status']}`.",
        f"- Clean merge: `{str(preflight['clean_merge']).lower()}`.",
    ]
    if preflight.get("records_rejected") is not None:
        lines.append(
            f"- Отклонений при временном объединении: `{preflight['records_rejected']}`."
        )
    lines.extend(
        (
            "",
            "## Dialogue coverage",
            "",
            f"- Статус: `{report['prospective_dialogue_coverage']['status']}`.",
            "",
            "## Граница",
            "",
            "Все проверки read-only: candidate остаётся staging-only, active manifest "
            "и raw corpus не изменялись, обучение не запускалось, GitHub не затрагивался.",
            "",
        )
    )
    return "\n".join(lines)


def audit_human_reviewed_candidate(
    *,
    candidate: Path = DEFAULT_CANDIDATE,
    report_directory: Path = DEFAULT_AUDIT_REPORT_DIRECTORY,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    """Выполняет isolated audit и preflight без promotion или training.

    Prospective coverage имеет смысл только после чистого preflight. Если
    candidate дублирует уже активный source, coverage сознательно не запускается:
    результат нельзя использовать как аргумент для обхода дедупликации.
    """
    safe_report_directory = _prepare_report_directory(report_directory, results_root)
    descriptor = human_reviewed_descriptor()
    try:
        isolated = audit_staged_candidate(
            candidate,
            descriptor,
            staging_root=staging_root,
        )
    except BrainV3StagingError as error:
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_candidate_isolated_audit_failed"
        ) from error

    try:
        active_manifest = load_corpus_manifest(
            corpus_root.expanduser().resolve() / "corpus_manifest.v1.json"
        )
    except (OSError, ValueError) as error:
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_candidate_active_manifest_invalid"
        ) from error
    active_source_ids = {source.source_id for source in active_manifest.sources}

    if ORIGINAL_SOURCE_ID in active_source_ids:
        # Candidate является approved-only подмножеством именно этого raw
        # source. Повторный полный audit всего корпуса заранее гарантированно
        # встретит те же ID/content и лишь потратит время, поэтому фиксируем
        # честный source-level конфликт до тяжёлой дедупликации.
        preflight: dict[str, Any] = {
            "status": "blocked_parent_source_already_active",
            "clean_merge": False,
            "records_rejected": None,
            "active_parent_source_id": ORIGINAL_SOURCE_ID,
            "active_sources_checked": len(active_source_ids),
        }
        coverage: dict[str, Any] = {
            "status": "skipped_parent_source_already_active",
        }
    elif isolated["audit"]["records_rejected"]:
        preflight: dict[str, Any] = {
            "status": "skipped_isolated_audit_rejections",
            "clean_merge": False,
            "records_rejected": None,
        }
        coverage: dict[str, Any] = {
            "status": "skipped_isolated_audit_rejections",
        }
    else:
        try:
            raw_preflight = preflight_staged_candidate_against_active(
                candidate,
                descriptor,
                corpus_root=corpus_root,
                staging_root=staging_root,
            )
        except BrainV3StagingError as error:
            raise OASST1HumanReviewCandidateError(
                "oasst1_human_review_candidate_preflight_failed"
            ) from error
        preflight = {
            "status": "completed",
            "clean_merge": raw_preflight["clean_merge"],
            "records_rejected": raw_preflight["audit"]["records_rejected"],
            "cross_split_exact_duplicates": raw_preflight["audit"][
                "cross_split_exact_duplicates"
            ],
            "cross_split_near_duplicate_candidates": raw_preflight["audit"][
                "cross_split_near_duplicate_candidates"
            ],
        }
        if raw_preflight["clean_merge"]:
            try:
                raw_coverage = prospective_dialogue_coverage_for_staged_candidate(
                    candidate,
                    descriptor,
                    corpus_root=corpus_root,
                    staging_root=staging_root,
                )
            except BrainV3StagingError as error:
                raise OASST1HumanReviewCandidateError(
                    "oasst1_human_review_candidate_coverage_failed"
                ) from error
            coverage = {
                "status": "completed",
                "training_allowed": raw_coverage["coverage"]["training_allowed"],
                "training_block_reasons": raw_coverage["coverage"][
                    "training_block_reasons"
                ],
            }
        else:
            coverage = {"status": "skipped_preflight_not_clean"}

    report: dict[str, Any] = {
        "format_version": 1,
        "mode": "oasst1_human_review_approved_candidate_audit",
        "candidate_path": str(candidate.expanduser().resolve()),
        "candidate_sha256": file_sha256(candidate.expanduser().resolve()),
        "candidate_source_id": descriptor.source_id,
        "isolated_audit": isolated,
        "active_corpus_preflight": preflight,
        "prospective_dialogue_coverage": coverage,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
        "chat_changed": False,
        "github_changed": False,
    }
    _write_report(
        safe_report_directory,
        "oasst1_human_review_candidate_audit",
        report,
        audit_report_markdown(report),
    )
    return report


def replacement_report_markdown(report: Mapping[str, Any]) -> str:
    """Описывает replacement snapshot без текста диалогов или ложного запуска."""
    isolated = report["isolated_audit"]
    preflight = report["replacement_preflight"]
    coverage = report["replacement_dialogue_coverage"]
    lines = [
        "# Brain v3 — OASST1 replacement snapshot audit",
        "",
        "## Isolated audit",
        "",
        f"- Принято: `{isolated['audit']['records_accepted']}`; отклонено: `{isolated['audit']['records_rejected']}`.",
        "",
        "## Non-active replacement preflight",
        "",
        f"- Статус: `{preflight['status']}`.",
        f"- Временный snapshot чистый: `{str(preflight['clean_snapshot']).lower()}`.",
        f"- Временно заменён parent source: `{report['parent_source_id']}`.",
    ]
    if preflight.get("records_rejected") is not None:
        lines.append(
            f"- Отклонений в snapshot audit: `{preflight['records_rejected']}`."
        )
    lines.extend(
        (
            "",
            "## Dialogue coverage",
            "",
            f"- Статус: `{coverage['status']}`.",
        )
    )
    if coverage.get("generator_training_ready") is not None:
        lines.append(
            "- Generator training ready: "
            f"`{str(coverage['generator_training_ready']).lower()}`."
        )
    if coverage.get("generator_training_block_reasons"):
        lines.append(
            "- Причины блока: `"
            + ", ".join(coverage["generator_training_block_reasons"])
            + "`."
        )
    lines.extend(
        (
            "",
            "## Граница",
            "",
            "Проверка использует только временные hard link. Active manifest, raw corpus, "
            "чат, веса и GitHub не изменялись; обучение генератора этой проверкой не запускается.",
            "",
        )
    )
    return "\n".join(lines)


def audit_human_reviewed_replacement_snapshot(
    *,
    candidate: Path = DEFAULT_CANDIDATE,
    report_directory: Path = DEFAULT_REPLACEMENT_REPORT_DIRECTORY,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    """Проверяет замену full OASST1 на approved subset без promotion.

    Approved subset является подмножеством active parent, поэтому обычный
    merge корректно блокируется. Эта отдельная операция строит лишь временный
    snapshot: в нём parent исключён, а subset добавлен как новый source. Ни
    active manifest, ни raw JSONL, ни training state не меняются.
    """
    safe_report_directory = _prepare_report_directory(report_directory, results_root)
    descriptor = human_reviewed_descriptor()
    try:
        isolated = audit_staged_candidate(
            candidate,
            descriptor,
            staging_root=staging_root,
        )
    except BrainV3StagingError as error:
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_replacement_isolated_audit_failed"
        ) from error

    try:
        active_manifest = load_corpus_manifest(
            corpus_root.expanduser().resolve() / "corpus_manifest.v1.json"
        )
    except (OSError, ValueError) as error:
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_replacement_active_manifest_invalid"
        ) from error
    active_source_ids = {source.source_id for source in active_manifest.sources}
    if ORIGINAL_SOURCE_ID not in active_source_ids:
        raise OASST1HumanReviewCandidateError(
            "oasst1_human_review_replacement_parent_not_active"
        )

    if isolated["audit"]["records_rejected"]:
        preflight: dict[str, Any] = {
            "status": "skipped_isolated_audit_rejections",
            "clean_snapshot": False,
            "records_rejected": None,
        }
        coverage: dict[str, Any] = {
            "status": "skipped_isolated_audit_rejections",
            "generator_training_ready": None,
            "generator_training_block_reasons": [],
        }
    else:
        try:
            raw_preflight = preflight_staged_replacement_against_active(
                candidate,
                descriptor,
                replaced_source_id=ORIGINAL_SOURCE_ID,
                corpus_root=corpus_root,
                staging_root=staging_root,
            )
        except BrainV3StagingError as error:
            raise OASST1HumanReviewCandidateError(
                "oasst1_human_review_replacement_preflight_failed"
            ) from error
        preflight = {
            "status": "completed",
            "clean_snapshot": raw_preflight["clean_snapshot"],
            "records_rejected": raw_preflight["audit"]["records_rejected"],
            "cross_split_exact_duplicates": raw_preflight["audit"][
                "cross_split_exact_duplicates"
            ],
            "cross_split_near_duplicate_candidates": raw_preflight["audit"][
                "cross_split_near_duplicate_candidates"
            ],
        }
        if raw_preflight["clean_snapshot"]:
            try:
                raw_coverage = prospective_dialogue_coverage_for_staged_replacement(
                    candidate,
                    descriptor,
                    replaced_source_id=ORIGINAL_SOURCE_ID,
                    corpus_root=corpus_root,
                    staging_root=staging_root,
                )
            except BrainV3StagingError as error:
                raise OASST1HumanReviewCandidateError(
                    "oasst1_human_review_replacement_coverage_failed"
                ) from error
            coverage = {
                "status": "completed",
                "generator_training_ready": raw_coverage["coverage"][
                    "generator_training_ready"
                ],
                "generator_training_block_reasons": raw_coverage["coverage"][
                    "generator_training_block_reasons"
                ],
            }
        else:
            coverage = {
                "status": "skipped_replacement_preflight_not_clean",
                "generator_training_ready": None,
                "generator_training_block_reasons": [],
            }

    report: dict[str, Any] = {
        "format_version": 1,
        "mode": "oasst1_human_review_replacement_snapshot_audit",
        "candidate_path": str(candidate.expanduser().resolve()),
        "candidate_sha256": file_sha256(candidate.expanduser().resolve()),
        "candidate_source_id": descriptor.source_id,
        "parent_source_id": ORIGINAL_SOURCE_ID,
        "isolated_audit": isolated,
        "replacement_preflight": preflight,
        "replacement_dialogue_coverage": coverage,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
        "chat_changed": False,
        "github_changed": False,
    }
    _write_report(
        safe_report_directory,
        "oasst1_human_review_replacement_snapshot",
        report,
        replacement_report_markdown(report),
    )
    return report


def _parser() -> argparse.ArgumentParser:
    """Создаёт явный CLI: build и audit выполняются раздельными командами."""
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--build", action="store_true")
    action.add_argument("--audit", action="store_true")
    action.add_argument("--replacement-audit", action="store_true")
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--report-directory", type=Path)
    return parser


def main() -> None:
    """Выполняет только явно выбранный локальный build либо read-only audit."""
    args = _parser().parse_args()
    try:
        if args.build:
            report = build_human_reviewed_candidate(
                destination=args.candidate,
                report_directory=(
                    args.report_directory or DEFAULT_BUILD_REPORT_DIRECTORY
                ),
            )
            print(
                json.dumps(
                    {
                        "candidate_records": report["candidate_records"],
                        "candidate_sha256": report["candidate_sha256"],
                        "candidate_is_active": report["candidate_is_active"],
                        "training_started": report["training_started"],
                    },
                    ensure_ascii=False,
                )
            )
            return
        if args.replacement_audit:
            report = audit_human_reviewed_replacement_snapshot(
                candidate=args.candidate,
                report_directory=(
                    args.report_directory or DEFAULT_REPLACEMENT_REPORT_DIRECTORY
                ),
            )
            print(
                json.dumps(
                    {
                        "preflight_status": report["replacement_preflight"]["status"],
                        "clean_snapshot": report["replacement_preflight"][
                            "clean_snapshot"
                        ],
                        "coverage_status": report["replacement_dialogue_coverage"][
                            "status"
                        ],
                        "generator_training_ready": report[
                            "replacement_dialogue_coverage"
                        ]["generator_training_ready"],
                        "training_started": report["training_started"],
                    },
                    ensure_ascii=False,
                )
            )
            return
        report = audit_human_reviewed_candidate(
            candidate=args.candidate,
            report_directory=(
                args.report_directory or DEFAULT_AUDIT_REPORT_DIRECTORY
            ),
        )
        print(
            json.dumps(
                {
                    "isolated_records_rejected": report["isolated_audit"]["audit"][
                        "records_rejected"
                    ],
                    "preflight_status": report["active_corpus_preflight"]["status"],
                    "clean_merge": report["active_corpus_preflight"]["clean_merge"],
                    "coverage_status": report["prospective_dialogue_coverage"]["status"],
                    "training_started": report["training_started"],
                },
                ensure_ascii=False,
            )
        )
    except (
        OASST1HumanReviewCandidateError,
        OASST1HumanReviewError,
        OSError,
        ValueError,
    ) as error:
        raise SystemExit(f"OASST1 human-review candidate остановлен: {error}") from error


if __name__ == "__main__":
    main()
