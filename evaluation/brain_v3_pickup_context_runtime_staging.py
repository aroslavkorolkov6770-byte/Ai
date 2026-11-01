"""Создаёт и проверяет metadata-only staging/audit snapshot repair-кандидата v1.4.

В v1.4 финальная реплика содержит закрытый runtime marker, а не статический
факт. Его нельзя переносить в corpus, train JSONL или веса: настоящий web
результат должен появляться только в момент живого ответа. Поэтому этот модуль
создаёт отдельный JSON snapshot с provenance и contract-метаданными, но без
реплик, marker-строк, URL и динамических данных.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ai_core.brain_v3_corpus import file_sha256
from evaluation import brain_v3_scoped_review as scoped_review


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = PROJECT_ROOT / "data" / "brain_v3" / "reviews"
STAGING_ROOT = PROJECT_ROOT / "data" / "brain_v3" / "staging"
RESULTS_ROOT = PROJECT_ROOT / "evaluation" / "results"
ACTIVE_MANIFEST_PATH = PROJECT_ROOT / "data" / "brain_v3" / "corpus_manifest.v1.json"

SCOPE_PATH = REVIEW_ROOT / "brain-v3-pickup-context-repair-v1.4.scope.json"
REVIEW_CANDIDATE_PATH = (
    REVIEW_ROOT / "brain-v3-pickup-context-repair-v1.4.review.jsonl"
)
QUEUE_PATH = (
    RESULTS_ROOT
    / "brain-v3-pickup-context-repair-v1.4-review-queue-v0.1"
    / "brain_v3_scoped_review_queue.json"
)
DECISIONS_PATH = (
    REVIEW_ROOT / "brain-v3-pickup-context-repair-v1.4.decisions.jsonl"
)

SOURCE_ID = "brain-v3-pickup-context-repair-v1.4-runtime-contract-v0.1"
SNAPSHOT_MODE = "brain_v3_runtime_contract_staging_candidate"
AUDIT_MODE = "brain_v3_runtime_contract_staging_audit"
FORMAT_VERSION = 1

DEFAULT_SNAPSHOT_PATH = STAGING_ROOT / f"{SOURCE_ID}.json"
DEFAULT_CREATION_REPORT_DIRECTORY = (
    RESULTS_ROOT / "brain-v3-pickup-context-repair-v1.4-runtime-staging-v0.1"
)
DEFAULT_AUDIT_REPORT_DIRECTORY = (
    RESULTS_ROOT / "brain-v3-pickup-context-repair-v1.4-runtime-staging-audit-v0.1"
)

EXPECTED_ORIGIN_IDS = (
    "brain-v3-pickup-context-repair-v1.4-001",
    "brain-v3-pickup-context-repair-v1.4-002",
)
REQUIRED_RESPONSE_PROPERTIES = frozenset(
    {
        "relevant",
        "ask_when_missing",
        "context_retained",
        "fact_grounded",
        "brief",
        "no_url_without_request",
        "no_fake_certainty",
        "tool_result_returned",
        "no_action_promise",
    }
)
RAW_DIALOGUE_KEYS = frozenset({"text", "turns", "conversation", "message", "user", "assistant"})
RUNTIME_MARKER_PATTERN = re.compile(r"^\[\[web\.[a-z0-9_]+\]\]\.$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class PickupContextRuntimeStagingError(ValueError):
    """Останавливает snapshot до записи неподходящего staging-артефакта."""


def _safe_path_inside(path: Path, root: Path, error_code: str) -> Path:
    """Разрешает путь только внутри одной явной проектной папки."""

    resolved_path = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise PickupContextRuntimeStagingError(error_code) from error
    return resolved_path


def _safe_new_snapshot_path(path: Path, staging_root: Path) -> Path:
    """Не допускает перезапись версий и JSONL, пригодного для corpus-import."""

    safe_path = _safe_path_inside(
        path,
        staging_root,
        "pickup_context_runtime_staging_output_outside_staging_root",
    )
    if safe_path.suffix.lower() != ".json":
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_snapshot_suffix_invalid"
        )
    if safe_path.exists():
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_snapshot_already_exists"
        )
    return safe_path


def _safe_existing_snapshot_path(path: Path, staging_root: Path) -> Path:
    """Читает audit-input только из staging и только в metadata JSON-формате."""

    safe_path = _safe_path_inside(
        path,
        staging_root,
        "pickup_context_runtime_staging_input_outside_staging_root",
    )
    if safe_path.suffix.lower() != ".json" or not safe_path.is_file():
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_snapshot_missing"
        )
    return safe_path


def _safe_new_report_directory(path: Path, results_root: Path) -> Path:
    """Создаёт только новую versioned-папку отчёта без перезаписи истории."""

    safe_path = _safe_path_inside(
        path,
        results_root,
        "pickup_context_runtime_staging_report_outside_results_root",
    )
    if safe_path.exists():
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_report_already_exists"
        )
    return safe_path


def _contains_raw_dialogue_content(value: Any) -> bool:
    """Ищет структурные признаки утечки реплик в metadata-only artefact."""

    if isinstance(value, Mapping):
        if RAW_DIALOGUE_KEYS.intersection(value):
            return True
        return any(_contains_raw_dialogue_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_raw_dialogue_content(item) for item in value)
    return False


def _contains_runtime_marker(value: Any) -> bool:
    """Не даёт закрытому marker попасть в snapshot даже как вложенная строка."""

    if isinstance(value, str):
        return "[[" in value or "]]" in value
    if isinstance(value, Mapping):
        return any(_contains_runtime_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_runtime_marker(item) for item in value)
    return False


def _read_json_object(path: Path, error_code: str) -> dict[str, Any]:
    """Читает JSON как данные и не раскрывает источник при ошибке."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PickupContextRuntimeStagingError(error_code) from error
    if not isinstance(value, dict):
        raise PickupContextRuntimeStagingError(error_code)
    return value


def _load_approved_origin_state() -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    str,
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Загружает v1.4 через scope, immutable queue и human ledger.

    Функция читает source records только для проверки marker-контракта. Ни одна
    реплика из этих записей не возвращается в будущий staging snapshot.
    """

    try:
        scope = scoped_review.load_scope(SCOPE_PATH, review_root=REVIEW_ROOT)
        records, candidate_sha256 = scoped_review.load_candidate(
            REVIEW_CANDIDATE_PATH,
            review_root=REVIEW_ROOT,
        )
        queue = scoped_review.load_queue(
            QUEUE_PATH,
            scope_path=SCOPE_PATH,
            candidate_path=REVIEW_CANDIDATE_PATH,
            results_root=RESULTS_ROOT,
            review_root=REVIEW_ROOT,
        )
        decisions = scoped_review.load_decisions(
            DECISIONS_PATH,
            queue,
            review_root=REVIEW_ROOT,
        )
        summary = scoped_review.build_summary(queue, decisions, scope)
    except scoped_review.ScopedReviewError as error:
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_review_source_invalid"
        ) from error

    if scope.get("status") != "human_review_coverage_complete":
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_review_coverage_incomplete"
        )
    if (
        summary.get("required_human_review_coverage_complete") is not True
        or summary.get("reviewed_records") != len(EXPECTED_ORIGIN_IDS)
        or summary.get("approved_records") != len(EXPECTED_ORIGIN_IDS)
        or summary.get("rejected_records") != 0
        or summary.get("training_candidate_allowed") is not False
        or summary.get("promotion_allowed") is not False
    ):
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_review_summary_invalid"
        )

    decision_by_id = {record.get("candidate_id"): record for record in decisions}
    if set(decision_by_id) != set(EXPECTED_ORIGIN_IDS):
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_decision_selection_invalid"
        )
    for identifier in EXPECTED_ORIGIN_IDS:
        decision = decision_by_id.get(identifier)
        if (
            not isinstance(decision, dict)
            or decision.get("decision") != "approved"
            or not isinstance(decision.get("record_sha256"), str)
            or not SHA256_PATTERN.fullmatch(decision["record_sha256"])
        ):
            raise PickupContextRuntimeStagingError(
                "pickup_context_runtime_staging_decision_invalid"
            )

    records_by_id = {record.get("id"): record for record in records}
    if set(records_by_id) != set(EXPECTED_ORIGIN_IDS):
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_origin_selection_invalid"
        )
    return scope, records, candidate_sha256, decisions, summary


def _metadata_selection(
    scope: Mapping[str, Any],
    records: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Строит selection без реплик и подтверждает закрытые web-контракты."""

    records_by_id = {record.get("id"): record for record in records}
    decisions_by_id = {record.get("candidate_id"): record for record in decisions}
    selection: list[dict[str, Any]] = []
    for identifier in EXPECTED_ORIGIN_IDS:
        record = records_by_id.get(identifier)
        decision = decisions_by_id.get(identifier)
        if not isinstance(record, Mapping) or not isinstance(decision, Mapping):
            raise PickupContextRuntimeStagingError(
                "pickup_context_runtime_staging_origin_missing"
            )
        scenario = record.get("scenario")
        turns = record.get("turns")
        if not isinstance(scenario, Mapping) or not isinstance(turns, list) or len(turns) != 4:
            raise PickupContextRuntimeStagingError(
                "pickup_context_runtime_staging_origin_invalid"
            )
        final_turn = turns[-1]
        final_text = final_turn.get("text") if isinstance(final_turn, Mapping) else None
        properties = scenario.get("expected_response_properties")
        if (
            not isinstance(final_turn, Mapping)
            or final_turn.get("role") != "assistant"
            or not isinstance(final_text, str)
            or not RUNTIME_MARKER_PATTERN.fullmatch(final_text)
            or not isinstance(properties, list)
            or not REQUIRED_RESPONSE_PROPERTIES.issubset(set(properties))
            or scenario.get("topic") != "web_grounding"
            or scenario.get("dialogue_goal") != "clarify_then_route_verified_fact"
            or decision.get("decision") != "approved"
        ):
            raise PickupContextRuntimeStagingError(
                "pickup_context_runtime_staging_contract_invalid"
            )
        selection.append(
            {
                "origin_scope_id": str(scope["scope_id"]),
                "origin_candidate_id": identifier,
                "origin_record_sha256": str(decision["record_sha256"]),
                "topic": str(scenario["topic"]),
                "dialogue_goal": str(scenario["dialogue_goal"]),
                "emotional_tone": str(scenario["emotional_tone"]),
                "expected_response_properties": sorted(set(properties)),
                "runtime_result_kind": "verified_web_result",
                "dynamic_runtime_output_excluded": True,
            }
        )
    return selection


def build_runtime_contract_snapshot() -> dict[str, Any]:
    """Собирает воспроизводимый metadata-only snapshot в памяти.

    Этот результат намеренно не является Brain v3 JSONL. Он нужен для audit
    маршрута, provenance и границ данных, а не для next-token обучения.
    """

    scope, records, candidate_sha256, decisions, summary = _load_approved_origin_state()
    if not ACTIVE_MANIFEST_PATH.is_file():
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_active_manifest_missing"
        )
    try:
        active_manifest_sha256 = file_sha256(ACTIVE_MANIFEST_PATH)
    except OSError as error:
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_active_manifest_unreadable"
        ) from error
    selection = _metadata_selection(scope, records, decisions)
    snapshot = {
        "format_version": FORMAT_VERSION,
        "mode": SNAPSHOT_MODE,
        "source_id": SOURCE_ID,
        "origin_scope_id": scope["scope_id"],
        "origin_candidate_path": str(REVIEW_CANDIDATE_PATH.resolve()),
        "origin_candidate_sha256": candidate_sha256,
        "origin_decisions_path": str(DECISIONS_PATH.resolve()),
        "review_proof": {
            "reviewed_records": summary["reviewed_records"],
            "approved_records": summary["approved_records"],
            "rejected_records": summary["rejected_records"],
            "required_human_review_coverage_complete": summary[
                "required_human_review_coverage_complete"
            ],
        },
        "selection": selection,
        "staging_kind": "metadata_only_runtime_contract",
        "dynamic_runtime_content_excluded": True,
        "contains_raw_dialogue_text": False,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_eligible": False,
        "training_started": False,
        "promotion_allowed": False,
        "github_changed": False,
        "active_manifest_sha256_at_creation": active_manifest_sha256,
    }
    _validate_snapshot_shape(snapshot)
    return snapshot


def _validate_snapshot_shape(snapshot: Mapping[str, Any]) -> None:
    """Проверяет границу metadata-only snapshot до записи и во время audit."""

    expected_keys = {
        "format_version",
        "mode",
        "source_id",
        "origin_scope_id",
        "origin_candidate_path",
        "origin_candidate_sha256",
        "origin_decisions_path",
        "review_proof",
        "selection",
        "staging_kind",
        "dynamic_runtime_content_excluded",
        "contains_raw_dialogue_text",
        "candidate_is_active",
        "active_manifest_updated",
        "training_eligible",
        "training_started",
        "promotion_allowed",
        "github_changed",
        "active_manifest_sha256_at_creation",
    }
    if set(snapshot) != expected_keys:
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_snapshot_shape_invalid"
        )
    if (
        snapshot.get("format_version") != FORMAT_VERSION
        or snapshot.get("mode") != SNAPSHOT_MODE
        or snapshot.get("source_id") != SOURCE_ID
        or snapshot.get("origin_scope_id") != "brain-v3-pickup-context-repair-v1.4"
        or snapshot.get("staging_kind") != "metadata_only_runtime_contract"
        or snapshot.get("dynamic_runtime_content_excluded") is not True
        or snapshot.get("contains_raw_dialogue_text") is not False
        or snapshot.get("candidate_is_active") is not False
        or snapshot.get("active_manifest_updated") is not False
        or snapshot.get("training_eligible") is not False
        or snapshot.get("training_started") is not False
        or snapshot.get("promotion_allowed") is not False
        or snapshot.get("github_changed") is not False
        or not isinstance(snapshot.get("origin_candidate_path"), str)
        or not isinstance(snapshot.get("origin_decisions_path"), str)
        or not isinstance(snapshot.get("origin_candidate_sha256"), str)
        or not SHA256_PATTERN.fullmatch(snapshot["origin_candidate_sha256"])
        or not isinstance(snapshot.get("active_manifest_sha256_at_creation"), str)
        or not SHA256_PATTERN.fullmatch(snapshot["active_manifest_sha256_at_creation"])
    ):
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_snapshot_values_invalid"
        )
    proof = snapshot.get("review_proof")
    selection = snapshot.get("selection")
    if (
        not isinstance(proof, Mapping)
        or proof
        != {
            "reviewed_records": 2,
            "approved_records": 2,
            "rejected_records": 0,
            "required_human_review_coverage_complete": True,
        }
        or not isinstance(selection, list)
        or len(selection) != len(EXPECTED_ORIGIN_IDS)
    ):
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_snapshot_review_invalid"
        )
    identifiers = [item.get("origin_candidate_id") for item in selection if isinstance(item, Mapping)]
    if identifiers != list(EXPECTED_ORIGIN_IDS):
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_snapshot_selection_invalid"
        )
    for item in selection:
        if (
            not isinstance(item, Mapping)
            or item.get("origin_scope_id") != "brain-v3-pickup-context-repair-v1.4"
            or item.get("topic") != "web_grounding"
            or item.get("dialogue_goal") != "clarify_then_route_verified_fact"
            or item.get("runtime_result_kind") != "verified_web_result"
            or item.get("dynamic_runtime_output_excluded") is not True
            or not isinstance(item.get("origin_record_sha256"), str)
            or not SHA256_PATTERN.fullmatch(item["origin_record_sha256"])
            or not isinstance(item.get("expected_response_properties"), list)
            or not REQUIRED_RESPONSE_PROPERTIES.issubset(
                set(item["expected_response_properties"])
            )
        ):
            raise PickupContextRuntimeStagingError(
                "pickup_context_runtime_staging_snapshot_contract_invalid"
            )
    if _contains_raw_dialogue_content(snapshot) or _contains_runtime_marker(snapshot):
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_snapshot_not_metadata_only"
        )


def _write_new_file(destination: Path, content: bytes) -> None:
    """Записывает новый snapshot атомарно и не перезаписывает старую версию."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{os.urandom(8).hex()}.tmp"
    try:
        temporary.write_bytes(content)
        if destination.exists():
            raise PickupContextRuntimeStagingError(
                "pickup_context_runtime_staging_snapshot_already_exists"
            )
        os.replace(temporary, destination)
    except OSError as error:
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_snapshot_write_failed"
        ) from error
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _write_report_directory(destination: Path, files: Mapping[str, str]) -> None:
    """Создаёт новый отчёт через временную папку, не смешивая версии."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        for filename, content in files.items():
            (temporary / filename).write_text(content, encoding="utf-8")
        if destination.exists():
            raise PickupContextRuntimeStagingError(
                "pickup_context_runtime_staging_report_already_exists"
            )
        os.replace(temporary, destination)
    except OSError as error:
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_report_write_failed"
        ) from error
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def creation_summary_markdown(summary: Mapping[str, Any]) -> str:
    """Рендерит понятный отчёт без копирования диалога или runtime marker."""

    return "\n".join(
        [
            "# Brain v3 — v1.4 runtime-contract staging candidate",
            "",
            "## Статус",
            "",
            "- Создан отдельный immutable metadata-only staging/audit snapshot.",
            "- Origin review records: `2`; оба имеют реальный approved verdict.",
            "- В snapshot не перенесены реплики, URL, weather/web-факты и runtime markers.",
            "- Active corpus, API, веса, обучение и GitHub не менялись.",
            "",
            "## Почему это не training dataset",
            "",
            "Финальный web-result появляется только во время живого route. Его нельзя",
            "записать как статическую truth-реплику или использовать как вход сети.",
            "Snapshot проверяет provenance и границу маршрута, но не содержит train JSONL.",
            "",
            "## Следующий шаг",
            "",
            "Запустить отдельный audit snapshot. Даже успешный audit не включает",
            "материал в active corpus и не открывает обучение.",
            "",
        ]
    )


def create_runtime_contract_staging_candidate(
    candidate_path: Path = DEFAULT_SNAPSHOT_PATH,
    report_directory: Path = DEFAULT_CREATION_REPORT_DIRECTORY,
    *,
    staging_root: Path = STAGING_ROOT,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    """Создаёт новый metadata-only staging snapshot без activation."""

    output = _safe_new_snapshot_path(candidate_path, staging_root)
    report_destination = _safe_new_report_directory(report_directory, results_root)
    snapshot = build_runtime_contract_snapshot()
    serialized = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    candidate_sha256 = hashlib.sha256(serialized).hexdigest()
    summary = {
        "format_version": FORMAT_VERSION,
        "mode": "brain_v3_runtime_contract_staging_creation",
        "source_id": SOURCE_ID,
        "candidate_path": str(output),
        "candidate_sha256": candidate_sha256,
        "origin_scope_id": snapshot["origin_scope_id"],
        "reviewed_records": snapshot["review_proof"]["reviewed_records"],
        "approved_records": snapshot["review_proof"]["approved_records"],
        "dynamic_runtime_content_excluded": snapshot["dynamic_runtime_content_excluded"],
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_eligible": False,
        "training_started": False,
        "promotion_allowed": False,
        "github_changed": False,
    }
    if _contains_raw_dialogue_content(summary) or _contains_runtime_marker(summary):
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_creation_summary_not_metadata_only"
        )
    _write_report_directory(
        report_destination,
        {
            "brain_v3_runtime_contract_staging_candidate.json": json.dumps(
                summary,
                ensure_ascii=False,
                indent=2,
            ),
            "BRAIN_V3_RUNTIME_CONTRACT_STAGING_CANDIDATE.md": creation_summary_markdown(summary),
        },
    )
    _write_new_file(output, serialized)
    try:
        written_sha256 = file_sha256(output)
    except OSError as error:
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_snapshot_hash_failed"
        ) from error
    if written_sha256 != candidate_sha256:
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_snapshot_hash_mismatch"
        )
    return summary


def _validate_snapshot_against_current_review(snapshot: Mapping[str, Any]) -> None:
    """Доказывает, что snapshot построен из текущих immutable review-verdict."""

    expected = build_runtime_contract_snapshot()
    compared_fields = (
        "format_version",
        "mode",
        "source_id",
        "origin_scope_id",
        "origin_candidate_path",
        "origin_candidate_sha256",
        "origin_decisions_path",
        "review_proof",
        "selection",
        "staging_kind",
        "dynamic_runtime_content_excluded",
        "contains_raw_dialogue_text",
        "candidate_is_active",
        "active_manifest_updated",
        "training_eligible",
        "training_started",
        "promotion_allowed",
        "github_changed",
    )
    if any(snapshot.get(field) != expected.get(field) for field in compared_fields):
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_snapshot_review_mismatch"
        )


def audit_runtime_contract_staging_candidate(
    candidate_path: Path = DEFAULT_SNAPSHOT_PATH,
    *,
    staging_root: Path = STAGING_ROOT,
) -> dict[str, Any]:
    """Проверяет готовый snapshot без импорта в corpus или запуска обучения."""

    source_path = _safe_existing_snapshot_path(candidate_path, staging_root)
    snapshot = _read_json_object(
        source_path,
        "pickup_context_runtime_staging_snapshot_invalid_json",
    )
    _validate_snapshot_shape(snapshot)
    _validate_snapshot_against_current_review(snapshot)
    try:
        candidate_sha256 = file_sha256(source_path)
        active_manifest_sha256 = file_sha256(ACTIVE_MANIFEST_PATH)
    except OSError as error:
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_audit_hash_failed"
        ) from error
    report = {
        "format_version": FORMAT_VERSION,
        "mode": AUDIT_MODE,
        "source_id": SOURCE_ID,
        "candidate_path": str(source_path),
        "candidate_sha256": candidate_sha256,
        "origin_scope_id": snapshot["origin_scope_id"],
        "audit": {
            "records_checked": len(snapshot["selection"]),
            "all_origin_verdicts_approved": True,
            "review_coverage_complete": True,
            "dynamic_runtime_content_excluded": True,
            "raw_dialogue_content_in_snapshot": False,
            "runtime_marker_in_snapshot": False,
            "active_manifest_unchanged_since_creation": (
                snapshot["active_manifest_sha256_at_creation"] == active_manifest_sha256
            ),
            "training_allowed": False,
            "training_block_reasons": [
                "metadata_only_runtime_contract",
                "dynamic_web_result_excluded",
                "no_trainable_jsonl_records",
                "insufficient_reviewed_dialogue_volume",
            ],
        },
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
        "promotion_allowed": False,
        "github_changed": False,
    }
    if _contains_raw_dialogue_content(report) or _contains_runtime_marker(report):
        raise PickupContextRuntimeStagingError(
            "pickup_context_runtime_staging_audit_not_metadata_only"
        )
    return report


def audit_summary_markdown(report: Mapping[str, Any]) -> str:
    """Пишет простое объяснение audit без исходных текстов и marker-строк."""

    audit = report["audit"]
    return "\n".join(
        [
            "# Brain v3 — v1.4 runtime-contract staging audit",
            "",
            "## Результат",
            "",
            f"- Проверено origin contracts: `{audit['records_checked']}`.",
            f"- Все human verdict approved: `{str(audit['all_origin_verdicts_approved']).lower()}`.",
            f"- Runtime content excluded: `{str(audit['dynamic_runtime_content_excluded']).lower()}`.",
            f"- Active manifest unchanged: `{str(audit['active_manifest_unchanged_since_creation']).lower()}`.",
            "",
            "## Граница",
            "",
            "Snapshot содержит только метаданные проверенных route-contracts. Он не",
            "становится частью active corpus, не создаёт обучающий JSONL и не запускает",
            "обучение. Dynamic web-result по-прежнему формируется только runtime route.",
            "",
            "## Следующий шаг",
            "",
            "Использовать audit только как доказательство границы web-контракта. Для",
            "улучшения разговорной модели по-прежнему нужен отдельный широкий corpus",
            "из статичных, human-reviewed и trainable диалогов.",
            "",
        ]
    )


def write_audit_report(
    report: Mapping[str, Any],
    destination: Path = DEFAULT_AUDIT_REPORT_DIRECTORY,
    *,
    results_root: Path = RESULTS_ROOT,
) -> None:
    """Сохраняет новую immutable audit-папку без overwrite старых отчётов."""

    report_destination = _safe_new_report_directory(destination, results_root)
    _write_report_directory(
        report_destination,
        {
            "brain_v3_runtime_contract_staging_audit.json": json.dumps(
                dict(report),
                ensure_ascii=False,
                indent=2,
            ),
            "BRAIN_V3_RUNTIME_CONTRACT_STAGING_AUDIT.md": audit_summary_markdown(report),
        },
    )


def _parser() -> argparse.ArgumentParser:
    """Предоставляет только явные create и audit операции локального snapshot."""

    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--create", action="store_true")
    actions.add_argument("--audit", action="store_true")
    parser.add_argument("--candidate", type=Path, default=DEFAULT_SNAPSHOT_PATH)
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
    """Сохраняет читаемый русский вывод в Windows PowerShell."""

    import sys

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """Выполняет один безопасный локальный этап без activation или training."""

    _configure_utf8_output()
    args = _parser().parse_args()
    try:
        if args.create:
            summary = create_runtime_contract_staging_candidate(
                args.candidate,
                args.creation_report_directory,
            )
            print(
                json.dumps(
                    {
                        "reviewed_records": summary["reviewed_records"],
                        "approved_records": summary["approved_records"],
                        "candidate_is_active": summary["candidate_is_active"],
                        "training_started": summary["training_started"],
                    },
                    ensure_ascii=False,
                )
            )
            return
        report = audit_runtime_contract_staging_candidate(args.candidate)
        write_audit_report(report, args.audit_report_directory)
        print(
            json.dumps(
                {
                    "records_checked": report["audit"]["records_checked"],
                    "runtime_content_excluded": report["audit"][
                        "dynamic_runtime_content_excluded"
                    ],
                    "training_started": report["training_started"],
                },
                ensure_ascii=False,
            )
        )
    except PickupContextRuntimeStagingError as error:
        raise SystemExit(f"Runtime-contract staging остановлен: {error}") from error


if __name__ == "__main__":
    main()
