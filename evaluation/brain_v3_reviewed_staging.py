"""Создаёт и проверяет isolated staging candidate из реально одобренных review-сцен.

Модуль берёт только заранее зафиксированные project-owned сцены с настоящими
human verdict. Он не добавляет новые реплики, не меняет review-источники,
active corpus, API, веса или обучение. Получившийся JSONL — небольшой
воспроизводимый staging/audit snapshot, а не training dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_core.brain_v3_corpus import content_sha256, file_sha256
from evaluation import brain_v3_manual_candidate_human_review as manual_human_review
from evaluation import brain_v3_manual_candidate_review as manual_review
from evaluation import brain_v3_scoped_review as scoped_review
from evaluation.brain_v3_staging import (
    BrainV3StagingError,
    StagedSourceDescriptor,
    audit_staged_candidate,
    preflight_and_prospective_dialogue_coverage_for_staged_candidate,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAGING_ROOT = PROJECT_ROOT / "data" / "brain_v3" / "staging"
RESULTS_ROOT = PROJECT_ROOT / "evaluation" / "results"

SOURCE_ID = "brain-v3-owned-reviewed-dialogues-v0.1"
SOURCE_LICENSE = "OWNED_BY_PROJECT"
SOURCE_ATTRIBUTION = (
    "Original Russian dialogue scenes authored for the local Brain v3 project; "
    "selected only after recorded human review; staging-only v0.1"
)

DEFAULT_CANDIDATE_PATH = STAGING_ROOT / f"{SOURCE_ID}.jsonl"
DEFAULT_CREATION_REPORT_DIRECTORY = (
    RESULTS_ROOT / "brain-v3-owned-reviewed-dialogues-staging-v0.1"
)
DEFAULT_AUDIT_REPORT_DIRECTORY = (
    RESULTS_ROOT / "brain-v3-owned-reviewed-dialogues-audit-v0.1"
)

# Это намеренно фиксированный снимок текущих verdict. Новые решения в старых
# review-ledger не могут молча поменять состав v0.1 при повторном запуске.
MANUAL_APPROVED_IDS = (
    "brain-v3-owned-manual-v1-001",
    "brain-v3-owned-manual-v1-005",
    "brain-v3-owned-manual-v1-006",
    "brain-v3-owned-manual-v1-007",
    "brain-v3-owned-manual-v1-008",
    "brain-v3-owned-manual-v1-009",
    "brain-v3-owned-manual-v1-017",
    "brain-v3-owned-manual-v1-020",
    "brain-v3-owned-manual-v1-021",
    "brain-v3-owned-manual-v1-022",
    "brain-v3-owned-manual-v1-023",
    "brain-v3-owned-manual-v1-024",
)
MANUAL_REJECTED_IDS = (
    "brain-v3-owned-manual-v1-018",
    "brain-v3-owned-manual-v1-019",
)
REPAIR_APPROVED_IDS = (
    "brain-v3-memory-repair-v1.1-001",
    "brain-v3-memory-repair-v1.1-002",
    "brain-v3-memory-repair-v1.1-003",
    "brain-v3-memory-repair-v1.1-004",
)

# Связанные privacy/memory-сцены находятся в одном test split. Это исключает
# утечку почти одинакового поведения между train и оценкой в малом snapshot.
SPLIT_BY_ORIGIN_ID = {
    "brain-v3-owned-manual-v1-001": "train",
    "brain-v3-owned-manual-v1-005": "train",
    "brain-v3-owned-manual-v1-006": "train",
    "brain-v3-owned-manual-v1-007": "train",
    "brain-v3-owned-manual-v1-008": "train",
    "brain-v3-owned-manual-v1-009": "train",
    "brain-v3-owned-manual-v1-017": "test",
    "brain-v3-owned-manual-v1-020": "test",
    "brain-v3-owned-manual-v1-021": "validation",
    "brain-v3-owned-manual-v1-022": "validation",
    "brain-v3-owned-manual-v1-023": "validation",
    "brain-v3-owned-manual-v1-024": "validation",
    "brain-v3-memory-repair-v1.1-001": "test",
    "brain-v3-memory-repair-v1.1-002": "test",
    "brain-v3-memory-repair-v1.1-003": "test",
    "brain-v3-memory-repair-v1.1-004": "test",
}

EXPECTED_RECORDS = 16
EXPECTED_TURNS = 64
RAW_CONTENT_KEYS = frozenset({"text", "turns", "conversation", "message"})


class ReviewedStagingError(ValueError):
    """Останавливает создание snapshot до частичной или непроверяемой записи."""


@dataclass(frozen=True)
class ApprovedOrigin:
    """Хранит одну одобренную сцену и её проверяемую metadata-связь."""

    source_key: str
    scope_id: str
    origin_id: str
    origin_record_sha256: str
    record: dict[str, Any]


@dataclass(frozen=True)
class ReviewProof:
    """Описывает review-источник без копирования его реплик в отчёт."""

    source_key: str
    scope_id: str
    candidate_path: str
    candidate_sha256: str
    decisions_path: str
    approved_origin_ids: tuple[str, ...]
    rejected_origin_ids: tuple[str, ...]
    required_coverage_complete: bool

    def as_dict(self) -> dict[str, Any]:
        """Сериализует только provenance и verdict metadata."""

        return {
            "source_key": self.source_key,
            "scope_id": self.scope_id,
            "candidate_path": self.candidate_path,
            "candidate_sha256": self.candidate_sha256,
            "decisions_path": self.decisions_path,
            "approved_origin_ids": list(self.approved_origin_ids),
            "rejected_origin_ids": list(self.rejected_origin_ids),
            "required_coverage_complete": self.required_coverage_complete,
        }


def source_descriptor() -> StagedSourceDescriptor:
    """Возвращает trusted owned-source descriptor, а не читает его из JSONL."""

    return StagedSourceDescriptor(
        source_id=SOURCE_ID,
        license_name=SOURCE_LICENSE,
        license_url=None,
        origin_url=None,
        attribution=SOURCE_ATTRIBUTION,
        source_kind="owned",
    )


def _contains_raw_turn_content(value: Any) -> bool:
    """Не допускает попадание реплик в metadata-only selection/audit reports."""

    if isinstance(value, dict):
        if RAW_CONTENT_KEYS.intersection(value):
            return True
        return any(_contains_raw_turn_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_raw_turn_content(item) for item in value)
    return False


def _safe_new_path(path: Path, root: Path, *, suffix: str) -> Path:
    """Разрешает новый immutable output только в ожидаемой локальной папке."""

    resolved_root = root.expanduser().resolve()
    resolved_path = path.expanduser().resolve()
    if resolved_path.suffix.lower() != suffix:
        raise ReviewedStagingError("reviewed_staging_output_suffix_invalid")
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ReviewedStagingError("reviewed_staging_output_outside_allowed_root") from error
    if resolved_path.exists():
        raise ReviewedStagingError("reviewed_staging_output_already_exists")
    return resolved_path


def _safe_new_directory(path: Path, root: Path) -> Path:
    """Разрешает новый versioned report directory без перезаписи истории."""

    resolved_root = root.expanduser().resolve()
    resolved_path = path.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ReviewedStagingError("reviewed_staging_report_outside_results_root") from error
    if resolved_path.exists():
        raise ReviewedStagingError("reviewed_staging_report_already_exists")
    return resolved_path


def _require_exact_outcomes(
    decisions: list[dict[str, Any]],
    *,
    expected_approved: tuple[str, ...],
    expected_rejected: tuple[str, ...],
    error_code: str,
) -> dict[str, dict[str, Any]]:
    """Не даёт v0.1 молча поменять состав после изменения старого ledger."""

    decision_by_id = {record["candidate_id"]: record for record in decisions}
    if len(decision_by_id) != len(decisions):
        raise ReviewedStagingError(error_code)
    approved = {
        identifier
        for identifier, decision in decision_by_id.items()
        if decision.get("decision") == "approved"
    }
    rejected = {
        identifier
        for identifier, decision in decision_by_id.items()
        if decision.get("decision") == "rejected"
    }
    if (
        approved != set(expected_approved)
        or rejected != set(expected_rejected)
        or len(approved) + len(rejected) != len(decisions)
    ):
        raise ReviewedStagingError(error_code)
    return decision_by_id


def _origins_from_records(
    records: list[dict[str, Any]],
    decision_by_id: dict[str, dict[str, Any]],
    *,
    source_key: str,
    scope_id: str,
    expected_ids: tuple[str, ...],
) -> list[ApprovedOrigin]:
    """Связывает только одобренные immutable record с их decision SHA-256."""

    records_by_id = {record.get("id"): record for record in records}
    if len(records_by_id) != len(records):
        raise ReviewedStagingError("reviewed_staging_duplicate_origin_identifier")
    origins: list[ApprovedOrigin] = []
    for identifier in expected_ids:
        record = records_by_id.get(identifier)
        decision = decision_by_id.get(identifier)
        if (
            not isinstance(record, dict)
            or not isinstance(decision, dict)
            or decision.get("decision") != "approved"
            or not isinstance(decision.get("record_sha256"), str)
        ):
            raise ReviewedStagingError("reviewed_staging_approved_origin_missing")
        origins.append(
            ApprovedOrigin(
                source_key=source_key,
                scope_id=scope_id,
                origin_id=identifier,
                origin_record_sha256=decision["record_sha256"],
                record=record,
            )
        )
    return origins


def _load_manual_origins() -> tuple[list[ApprovedOrigin], ReviewProof]:
    """Загружает v1 только через его проверенные queue и human ledger."""

    try:
        queue = manual_human_review.load_review_queue()
        decisions = manual_human_review.load_review_decisions(
            manual_human_review.DEFAULT_DECISIONS_PATH,
            queue,
        )
        summary = manual_human_review.build_review_summary(
            queue,
            decisions,
            general_review_ratio=0.1,
        )
        records, candidate_sha256 = manual_review.load_review_candidate()
    except (
        manual_human_review.ManualCandidateHumanReviewError,
        manual_review.ManualCandidateReviewError,
    ) as error:
        raise ReviewedStagingError("reviewed_staging_manual_review_source_invalid") from error
    if not summary.get("required_human_review_coverage_complete"):
        raise ReviewedStagingError("reviewed_staging_manual_review_coverage_incomplete")
    decisions_by_id = _require_exact_outcomes(
        decisions,
        expected_approved=MANUAL_APPROVED_IDS,
        expected_rejected=MANUAL_REJECTED_IDS,
        error_code="reviewed_staging_manual_review_outcomes_changed",
    )
    return (
        _origins_from_records(
            records,
            decisions_by_id,
            source_key="manual",
            scope_id=manual_review.SCOPE_ID,
            expected_ids=MANUAL_APPROVED_IDS,
        ),
        ReviewProof(
            source_key="manual",
            scope_id=manual_review.SCOPE_ID,
            candidate_path=str(manual_review.DEFAULT_CANDIDATE_PATH.resolve()),
            candidate_sha256=candidate_sha256,
            decisions_path=str(manual_human_review.DEFAULT_DECISIONS_PATH.resolve()),
            approved_origin_ids=MANUAL_APPROVED_IDS,
            rejected_origin_ids=MANUAL_REJECTED_IDS,
            required_coverage_complete=True,
        ),
    )


def _load_repair_origins() -> tuple[list[ApprovedOrigin], ReviewProof]:
    """Загружает repair v1.1 только через его проверенные queue и ledger."""

    try:
        scope = scoped_review.load_scope()
        queue = scoped_review.load_queue()
        decisions = scoped_review.load_decisions(scoped_review.DEFAULT_DECISIONS_PATH, queue)
        summary = scoped_review.build_summary(queue, decisions, scope)
        records, candidate_sha256 = scoped_review.load_candidate()
    except scoped_review.ScopedReviewError as error:
        raise ReviewedStagingError("reviewed_staging_repair_review_source_invalid") from error
    if not summary.get("required_human_review_coverage_complete"):
        raise ReviewedStagingError("reviewed_staging_repair_review_coverage_incomplete")
    decisions_by_id = _require_exact_outcomes(
        decisions,
        expected_approved=REPAIR_APPROVED_IDS,
        expected_rejected=(),
        error_code="reviewed_staging_repair_review_outcomes_changed",
    )
    return (
        _origins_from_records(
            records,
            decisions_by_id,
            source_key="repair",
            scope_id=scope["scope_id"],
            expected_ids=REPAIR_APPROVED_IDS,
        ),
        ReviewProof(
            source_key="repair",
            scope_id=str(scope["scope_id"]),
            candidate_path=str(scoped_review.DEFAULT_CANDIDATE_PATH.resolve()),
            candidate_sha256=candidate_sha256,
            decisions_path=str(scoped_review.DEFAULT_DECISIONS_PATH.resolve()),
            approved_origin_ids=REPAIR_APPROVED_IDS,
            rejected_origin_ids=(),
            required_coverage_complete=True,
        ),
    )


def load_approved_origins() -> tuple[list[ApprovedOrigin], list[ReviewProof]]:
    """Собирает фиксированный набор из двух завершённых review workflows."""

    manual_origins, manual_proof = _load_manual_origins()
    repair_origins, repair_proof = _load_repair_origins()
    origins = [*manual_origins, *repair_origins]
    origin_ids = [origin.origin_id for origin in origins]
    expected_ids = [*MANUAL_APPROVED_IDS, *REPAIR_APPROVED_IDS]
    if (
        origin_ids != expected_ids
        or len(origin_ids) != EXPECTED_RECORDS
        or len(set(origin_ids)) != len(origin_ids)
        or set(SPLIT_BY_ORIGIN_ID) != set(origin_ids)
    ):
        raise ReviewedStagingError("reviewed_staging_origin_selection_invalid")
    return origins, [manual_proof, repair_proof]


def _staged_record_id(origin: ApprovedOrigin) -> str:
    """Создаёт стабильный ID без переноса review ID в active-like namespace."""

    suffix = origin.origin_id.rsplit("-", maxsplit=1)[-1]
    if not suffix.isdigit() or len(suffix) != 3:
        raise ReviewedStagingError("reviewed_staging_origin_identifier_invalid")
    return f"brain-v3-owned-reviewed-v0.1.{origin.source_key}-{suffix}"


def _staged_record(origin: ApprovedOrigin) -> dict[str, Any]:
    """Преобразует уже approved review-сцену в строгую canonical JSONL-запись."""

    descriptor = source_descriptor()
    scenario = origin.record.get("scenario")
    turns = origin.record.get("turns")
    split = SPLIT_BY_ORIGIN_ID.get(origin.origin_id)
    if (
        not isinstance(scenario, dict)
        or not isinstance(turns, list)
        or split not in {"train", "validation", "test"}
    ):
        raise ReviewedStagingError("reviewed_staging_origin_record_invalid")
    record: dict[str, Any] = {
        "format_version": 1,
        "id": _staged_record_id(origin),
        "record_type": "dialogue",
        "provenance": {
            "source_id": descriptor.source_id,
            "license": descriptor.license_name,
            "license_url": descriptor.license_url,
            "origin_url": descriptor.origin_url,
            "attribution": descriptor.attribution,
        },
        "language": "ru",
        "layer": "dialogue",
        "split": split,
        "topic": scenario.get("topic"),
        "quality_tier": "human_reviewed",
        "expected_response_properties": sorted(scenario.get("expected_response_properties", [])),
        "dialogue_goal": scenario.get("dialogue_goal"),
        "emotional_tone": scenario.get("emotional_tone"),
        "turns": [
            {"role": turn.get("role"), "text": turn.get("text")}
            for turn in turns
            if isinstance(turn, dict)
        ],
    }
    if len(record["turns"]) != len(turns):
        raise ReviewedStagingError("reviewed_staging_origin_turn_invalid")
    record["content_sha256"] = content_sha256(record)
    return record


def _coverage_snapshot(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Считает metadata покрытия будущего staging snapshot без реплик."""

    split_records: Counter[str] = Counter()
    split_turns: Counter[str] = Counter()
    topics: Counter[str] = Counter()
    goals: Counter[str] = Counter()
    tones: Counter[str] = Counter()
    properties: Counter[str] = Counter()
    for record in records:
        turn_count = len(record["turns"])
        split_records[str(record["split"])] += 1
        split_turns[str(record["split"])] += turn_count
        topics[str(record["topic"])] += 1
        goals[str(record["dialogue_goal"])] += 1
        tones[str(record["emotional_tone"])] += 1
        for property_name in record["expected_response_properties"]:
            properties[str(property_name)] += turn_count
    return {
        "records": len(records),
        "dialogue_turns": sum(split_turns.values()),
        "records_by_split": dict(sorted(split_records.items())),
        "turns_by_split": dict(sorted(split_turns.items())),
        "records_by_topic": dict(sorted(topics.items())),
        "turns_by_dialogue_goal": dict(sorted(goals.items())),
        "turns_by_emotional_tone": dict(sorted(tones.items())),
        "turns_by_response_property": dict(sorted(properties.items())),
    }


def build_staged_records() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Строит candidate в памяти и проверяет неизменяемый selection contract."""

    origins, proofs = load_approved_origins()
    records = [_staged_record(origin) for origin in origins]
    identifiers = [record["id"] for record in records]
    coverage = _coverage_snapshot(records)
    if (
        len(records) != EXPECTED_RECORDS
        or len(identifiers) != len(set(identifiers))
        or coverage["dialogue_turns"] != EXPECTED_TURNS
        or coverage["records_by_split"] != {"test": 6, "train": 6, "validation": 4}
        or coverage["turns_by_split"] != {"test": 24, "train": 24, "validation": 16}
    ):
        raise ReviewedStagingError("reviewed_staging_coverage_contract_invalid")
    selection = [
        {
            "origin_scope_id": origin.scope_id,
            "origin_candidate_id": origin.origin_id,
            "origin_record_sha256": origin.origin_record_sha256,
            "staged_record_id": record["id"],
            "split": record["split"],
        }
        for origin, record in zip(origins, records, strict=True)
    ]
    metadata = {
        "review_proofs": [proof.as_dict() for proof in proofs],
        "selection": selection,
        "coverage": coverage,
    }
    if _contains_raw_turn_content(metadata):
        raise ReviewedStagingError("reviewed_staging_metadata_contains_raw_content")
    return records, metadata


def _serialize_jsonl(records: list[dict[str, Any]]) -> bytes:
    """Сериализует небольшой immutable JSONL стабильно для SHA-256."""

    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    ).encode("utf-8")


def _write_new_file(destination: Path, content: bytes) -> None:
    """Записывает новый файл через временный путь, не перезаписывая версию."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{os.urandom(8).hex()}.tmp"
    try:
        temporary.write_bytes(content)
        if destination.exists():
            raise ReviewedStagingError("reviewed_staging_output_already_exists")
        os.replace(temporary, destination)
    except OSError as error:
        raise ReviewedStagingError("reviewed_staging_output_write_failed") from error
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def creation_summary_markdown(summary: dict[str, Any]) -> str:
    """Рендерит понятный отчёт без текстов candidate-сцен."""

    coverage = summary["coverage"]
    return "\n".join(
        [
            "# Brain v3 — reviewed staging candidate v0.1",
            "",
            "## Статус",
            "",
            "- Создан отдельный immutable staging/audit snapshot из реально одобренных review-сцен.",
            f"- Сцен: `{coverage['records']}`; диалоговых ходов: `{coverage['dialogue_turns']}`.",
            f"- Split records: `{coverage['records_by_split']}`.",
            f"- Topics: `{coverage['records_by_topic']}`.",
            "- Active corpus, API, веса, обучение и GitHub не менялись.",
            "",
            "## Граница качества",
            "",
            "Это не training dataset и не доказательство универсального разговорного качества: snapshot намеренно мал и покрывает только уже проверенные темы.",
            "Записи 018 и 019 из исходного v1 не включены; их поведение заменено отдельным одобренным repair v1.1.",
            "",
            "## Следующий шаг",
            "",
            "Запустить isolated audit, active-corpus preflight и prospective dialogue coverage. Даже чистый результат не разрешает promotion или обучение без отдельного решения.",
            "",
        ]
    )


def _write_report_directory(destination: Path, files: dict[str, str]) -> None:
    """Создаёт versioned report directory через временный каталог без overwrite."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        for name, content in files.items():
            (temporary / name).write_text(content, encoding="utf-8")
        if destination.exists():
            raise ReviewedStagingError("reviewed_staging_report_already_exists")
        os.replace(temporary, destination)
    except OSError as error:
        raise ReviewedStagingError("reviewed_staging_report_write_failed") from error
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def create_reviewed_staging_candidate(
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    report_directory: Path = DEFAULT_CREATION_REPORT_DIRECTORY,
    *,
    staging_root: Path = STAGING_ROOT,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    """Создаёт candidate и metadata-only selection report без activation."""

    output = _safe_new_path(candidate_path, staging_root, suffix=".jsonl")
    report_destination = _safe_new_directory(report_directory, results_root)
    records, metadata = build_staged_records()
    serialized = _serialize_jsonl(records)
    candidate_sha256 = hashlib.sha256(serialized).hexdigest()
    summary = {
        "format_version": 1,
        "mode": "brain_v3_reviewed_staging_candidate",
        "source_id": SOURCE_ID,
        "candidate_path": str(output),
        "candidate_sha256": candidate_sha256,
        "candidate_bytes": len(serialized),
        "coverage": metadata["coverage"],
        "review_proofs": metadata["review_proofs"],
        "selection": metadata["selection"],
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
        "github_changed": False,
    }
    if _contains_raw_turn_content(summary):
        raise ReviewedStagingError("reviewed_staging_summary_contains_raw_content")
    _write_report_directory(
        report_destination,
        {
            "brain_v3_reviewed_staging_candidate.json": json.dumps(
                summary,
                ensure_ascii=False,
                indent=2,
            ),
            "BRAIN_V3_REVIEWED_STAGING_CANDIDATE.md": creation_summary_markdown(summary),
        },
    )
    try:
        _write_new_file(output, serialized)
    except ReviewedStagingError:
        # Временный report не удаляется: он честно показывает, что запись JSONL
        # не завершилась, а повторный запуск не может скрыто перезаписать версию.
        raise
    if file_sha256(output) != candidate_sha256:
        raise ReviewedStagingError("reviewed_staging_output_hash_mismatch")
    return summary


def audit_reviewed_staging_candidate(
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    *,
    staging_root: Path = STAGING_ROOT,
) -> dict[str, Any]:
    """Запускает isolated и объединённый prospective audit без обучения."""

    descriptor = source_descriptor()
    try:
        isolated = audit_staged_candidate(
            candidate_path,
            descriptor,
            staging_root=staging_root,
        )
        active_preflight, prospective_coverage = (
            preflight_and_prospective_dialogue_coverage_for_staged_candidate(
                candidate_path,
                descriptor,
                staging_root=staging_root,
            )
        )
    except BrainV3StagingError as error:
        # Внешний audit не должен раскрывать text candidate в исключении CLI.
        raise ReviewedStagingError("reviewed_staging_audit_failed") from error
    report = {
        "format_version": 1,
        "mode": "brain_v3_reviewed_staging_audit",
        "source_id": SOURCE_ID,
        "candidate_path": str(candidate_path.expanduser().resolve()),
        "candidate_sha256": file_sha256(candidate_path),
        "isolated_audit": isolated,
        "active_corpus_preflight": active_preflight,
        "prospective_dialogue_coverage": prospective_coverage,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
        "github_changed": False,
    }
    if _contains_raw_turn_content(report):
        raise ReviewedStagingError("reviewed_staging_audit_contains_raw_content")
    return report


def audit_summary_markdown(report: dict[str, Any]) -> str:
    """Пишет audit verdict простыми словами без копирования реплик."""

    isolated = report["isolated_audit"]["audit"]
    preflight = report["active_corpus_preflight"]
    coverage = report["prospective_dialogue_coverage"]["coverage"]
    return "\n".join(
        [
            "# Brain v3 — reviewed staging candidate v0.1 audit",
            "",
            "## Isolated audit",
            "",
            f"- Принято: `{isolated['records_accepted']}`; отклонено: `{isolated['records_rejected']}`.",
            f"- Training allowed: `{str(isolated['training_allowed']).lower()}`.",
            f"- Причины блокировки: `{isolated['training_block_reasons']}`.",
            "",
            "## Проверка вместе с active corpus",
            "",
            f"- Clean merge: `{str(preflight['clean_merge']).lower()}`.",
            f"- Active sources checked: `{preflight['active_sources_checked']}`.",
            f"- Merge rejections: `{preflight['audit']['records_rejected']}`.",
            "",
            "## Prospective dialogue coverage",
            "",
            f"- Coverage observed: `{str(coverage['coverage_observed']).lower()}`.",
            f"- Coverage allowed: `{str(coverage['coverage_allowed']).lower()}`.",
            f"- Generator training ready: `{str(coverage['generator_training_ready']).lower()}`.",
            f"- Coverage block reasons: `{coverage['coverage_block_reasons']}`.",
            "",
            "## Граница",
            "",
            "Отчёт не активирует candidate и не запускает обучение. Малый approved snapshot нужен только для проверки происхождения, split, schema и пересечений перед следующим расширением корпуса.",
            "",
        ]
    )


def write_audit_report(
    report: dict[str, Any],
    destination: Path = DEFAULT_AUDIT_REPORT_DIRECTORY,
    *,
    results_root: Path = RESULTS_ROOT,
) -> None:
    """Создаёт отдельный immutable audit report directory."""

    report_destination = _safe_new_directory(destination, results_root)
    _write_report_directory(
        report_destination,
        {
            "brain_v3_reviewed_staging_audit.json": json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
            "BRAIN_V3_REVIEWED_STAGING_AUDIT.md": audit_summary_markdown(report),
        },
    )


def _parser() -> argparse.ArgumentParser:
    """Предоставляет только явные create или audit операции."""

    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--create", action="store_true")
    actions.add_argument("--audit", action="store_true")
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE_PATH)
    parser.add_argument(
        "--creation-report-directory",
        type=Path,
        default=DEFAULT_CREATION_REPORT_DIRECTORY,
    )
    parser.add_argument(
        "--audit-report-directory",
        type=Path,
        default=DEFAULT_AUDIT_REPORT_DIRECTORY,
    )
    return parser


def _configure_utf8_output() -> None:
    """Включает UTF-8 для русских сообщений в Windows terminal."""

    import sys

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """Запускает одну явно выбранную локальную операцию staging/audit."""

    _configure_utf8_output()
    args = _parser().parse_args()
    try:
        if args.create:
            summary = create_reviewed_staging_candidate(
                args.candidate,
                args.creation_report_directory,
            )
            print(
                json.dumps(
                    {
                        "candidate_records": summary["coverage"]["records"],
                        "dialogue_turns": summary["coverage"]["dialogue_turns"],
                        "candidate_is_active": summary["candidate_is_active"],
                        "training_started": summary["training_started"],
                    },
                    ensure_ascii=False,
                )
            )
            return
        report = audit_reviewed_staging_candidate(args.candidate)
        write_audit_report(report, args.audit_report_directory)
        coverage = report["prospective_dialogue_coverage"]["coverage"]
        print(
            json.dumps(
                {
                    "isolated_records_accepted": report["isolated_audit"]["audit"][
                        "records_accepted"
                    ],
                    "clean_merge": report["active_corpus_preflight"]["clean_merge"],
                    "generator_training_ready": coverage["generator_training_ready"],
                    "training_started": report["training_started"],
                },
                ensure_ascii=False,
            )
        )
    except ReviewedStagingError as error:
        raise SystemExit(f"Reviewed staging остановлен: {error}") from error


if __name__ == "__main__":
    main()
