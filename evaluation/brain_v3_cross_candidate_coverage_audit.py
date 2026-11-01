"""Сверяет semantic coverage нескольких Brain v3 review-only candidates.

Audit читает только проверенные metadata очередей, verdict-ов и policy. Он не
переносит ни одну сцену в corpus, не создаёт training JSONL и не раскрывает
реплики диалогов в своём отчёте.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ai_core.brain_v3_corpus import DEFAULT_MANIFEST
from ai_core.brain_v3_dialogue_coverage import (
    DEFAULT_POLICY,
    DialogueCoverageError,
    DialogueCoveragePolicy,
    load_dialogue_coverage_policy,
)
from evaluation import brain_v3_manual_candidate_human_review as manual_human_review
from evaluation import brain_v3_manual_candidate_review as manual_review
from evaluation import brain_v3_scoped_review as scoped_review


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = PROJECT_ROOT / "data" / "brain_v3" / "reviews"
RESULTS_ROOT = PROJECT_ROOT / "evaluation" / "results"
BASELINE_COVERAGE_REPORT_PATH = (
    RESULTS_ROOT
    / "brain-v3-dialogue-coverage-v0.4"
    / "brain_v3_dialogue_coverage.json"
)
MANIFEST_REFERENCE_SNAPSHOT_PATH = (
    PROJECT_ROOT
    / "data"
    / "brain_v3"
    / "staging"
    / "brain-v3-pickup-context-repair-v1.4-runtime-contract-v0.1.json"
)

FORMAT_VERSION = 1
AUDIT_MODE = "brain_v3_cross_candidate_coverage_audit"
DEFAULT_AUDIT_REPORT_DIRECTORY = (
    RESULTS_ROOT / "brain-v3-cross-candidate-coverage-v0.4"
)
RAW_DIALOGUE_KEYS = frozenset({"text", "turns", "conversation", "message", "user", "assistant"})
URL_PATTERN = re.compile(r"(?:https?://|www\.)", flags=re.IGNORECASE)
RUNTIME_PROPERTY_MARKERS = frozenset({"tool_result_returned", "no_action_promise"})
STATIC_AUTHORING_EXCLUDED_TOPICS = frozenset({"tool_routing", "web_grounding"})
MEDICAL_HOLD_SCOPE_ID = "brain-v3-core-dialogue-candidate-v1.6"
MEDICAL_HOLD_RECORD_ID = f"{MEDICAL_HOLD_SCOPE_ID}-006"
MEDICAL_COMPLETED_REVIEW_AUDIT_PATH = (
    RESULTS_ROOT
    / f"{MEDICAL_HOLD_SCOPE_ID}-review-audit-v0.1"
    / "brain_v3_core_dialogue_completed_review_audit.json"
)


@dataclass(frozen=True)
class ScopedCandidateSpec:
    """Описывает один reusable scoped-review candidate без свободных путей."""

    scope_id: str
    scope_path: Path
    candidate_path: Path
    queue_path: Path
    decisions_path: Path


SCOPED_CANDIDATES = (
    ScopedCandidateSpec(
        "brain-v3-memory-repair-candidate-v1.1",
        REVIEW_ROOT / "brain-v3-memory-repair-candidate-v1.1.scope.json",
        REVIEW_ROOT / "brain-v3-memory-repair-candidate-v1.1.review.jsonl",
        RESULTS_ROOT
        / "brain-v3-memory-repair-candidate-v1.1-review-queue-v0.1"
        / "brain_v3_scoped_review_queue.json",
        REVIEW_ROOT / "brain-v3-memory-repair-candidate-v1.1.decisions.jsonl",
    ),
    ScopedCandidateSpec(
        "brain-v3-semantic-gap-candidate-v1.2",
        REVIEW_ROOT / "brain-v3-semantic-gap-candidate-v1.2.scope.json",
        REVIEW_ROOT / "brain-v3-semantic-gap-candidate-v1.2.review.jsonl",
        RESULTS_ROOT
        / "brain-v3-semantic-gap-candidate-v1.2-review-queue-v0.1"
        / "brain_v3_scoped_review_queue.json",
        REVIEW_ROOT / "brain-v3-semantic-gap-candidate-v1.2.decisions.jsonl",
    ),
    ScopedCandidateSpec(
        "brain-v3-tool-web-result-repair-v1.3",
        REVIEW_ROOT / "brain-v3-tool-web-result-repair-v1.3.scope.json",
        REVIEW_ROOT / "brain-v3-tool-web-result-repair-v1.3.review.jsonl",
        RESULTS_ROOT
        / "brain-v3-tool-web-result-repair-v1.3-review-queue-v0.1"
        / "brain_v3_scoped_review_queue.json",
        REVIEW_ROOT / "brain-v3-tool-web-result-repair-v1.3.decisions.jsonl",
    ),
    ScopedCandidateSpec(
        "brain-v3-pickup-context-repair-v1.4",
        REVIEW_ROOT / "brain-v3-pickup-context-repair-v1.4.scope.json",
        REVIEW_ROOT / "brain-v3-pickup-context-repair-v1.4.review.jsonl",
        RESULTS_ROOT
        / "brain-v3-pickup-context-repair-v1.4-review-queue-v0.1"
        / "brain_v3_scoped_review_queue.json",
        REVIEW_ROOT / "brain-v3-pickup-context-repair-v1.4.decisions.jsonl",
    ),
    ScopedCandidateSpec(
        "brain-v3-natural-dialogue-candidate-v1.5",
        REVIEW_ROOT / "brain-v3-natural-dialogue-candidate-v1.5.scope.json",
        REVIEW_ROOT / "brain-v3-natural-dialogue-candidate-v1.5.review.jsonl",
        RESULTS_ROOT
        / "brain-v3-natural-dialogue-candidate-v1.5-review-queue-v0.1"
        / "brain_v3_scoped_review_queue.json",
        REVIEW_ROOT / "brain-v3-natural-dialogue-candidate-v1.5.decisions.jsonl",
    ),
    ScopedCandidateSpec(
        MEDICAL_HOLD_SCOPE_ID,
        REVIEW_ROOT / f"{MEDICAL_HOLD_SCOPE_ID}.scope.json",
        REVIEW_ROOT / f"{MEDICAL_HOLD_SCOPE_ID}.review.jsonl",
        RESULTS_ROOT
        / f"{MEDICAL_HOLD_SCOPE_ID}-review-queue-v0.3"
        / "brain_v3_scoped_review_queue.json",
        REVIEW_ROOT / f"{MEDICAL_HOLD_SCOPE_ID}.decisions.jsonl",
    ),
)

LEGACY_SCOPE_PATH = REVIEW_ROOT / "brain-v3-owned-manual-candidate-v1.scope.json"
LEGACY_CANDIDATE_PATH = REVIEW_ROOT / "brain-v3-owned-manual-candidate-v1.review.jsonl"
LEGACY_QUEUE_PATH = (
    RESULTS_ROOT
    / "brain-v3-owned-manual-candidate-v1-review-queue-v0.2"
    / "brain_v3_manual_candidate_review_queue.json"
)
LEGACY_DECISIONS_PATH = REVIEW_ROOT / "brain-v3-owned-manual-candidate-v1.decisions.jsonl"


class CrossCandidateCoverageAuditError(ValueError):
    """Останавливает audit до записи непроверяемого metadata-only отчёта."""


def _safe_path_inside(path: Path, root: Path, error_code: str) -> Path:
    """Разрешает output только в заранее определённой project-папке."""

    resolved_path = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise CrossCandidateCoverageAuditError(error_code) from error
    return resolved_path


def _safe_new_report_directory(path: Path, results_root: Path) -> Path:
    """Защищает versioned отчёты от перезаписи истории."""

    destination = _safe_path_inside(
        path,
        results_root,
        "cross_candidate_coverage_audit_output_outside_results_root",
    )
    if destination.exists():
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_report_already_exists"
        )
    return destination


def _sha256(path: Path, error_code: str) -> str:
    """Возвращает hash входного metadata/source файла без показа содержимого."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise CrossCandidateCoverageAuditError(error_code) from error


def _read_json_object(path: Path, error_code: str) -> dict[str, Any]:
    """Читает metadata JSON и не передаёт его содержимое в сообщение об ошибке."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CrossCandidateCoverageAuditError(error_code) from error
    if not isinstance(value, dict):
        raise CrossCandidateCoverageAuditError(error_code)
    return value


def _load_baseline_snapshot(
    policy: DialogueCoveragePolicy,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Связывает v0.4 coverage snapshot с неизменным текущим manifest.

    Это быстрый planning baseline. Перед training он не заменяет полный raw
    corpus audit: source files должны быть перечитаны и сверены повторно.
    """

    baseline = _read_json_object(
        BASELINE_COVERAGE_REPORT_PATH,
        "cross_candidate_coverage_audit_baseline_report_invalid",
    )
    reference = _read_json_object(
        MANIFEST_REFERENCE_SNAPSHOT_PATH,
        "cross_candidate_coverage_audit_manifest_reference_invalid",
    )
    manifest_at_reference = reference.get("active_manifest_sha256_at_creation")
    current_manifest_sha256 = _sha256(
        DEFAULT_MANIFEST,
        "cross_candidate_coverage_audit_manifest_hash_failed",
    )
    if (
        baseline.get("format_version") != FORMAT_VERSION
        or baseline.get("policy_id") != "brain_v3_dialogue_coverage_v1"
        or baseline.get("coverage_observed") is not True
        or not isinstance(baseline.get("base_corpus_audit"), Mapping)
        or not isinstance(baseline.get("observed"), Mapping)
        or not isinstance(manifest_at_reference, str)
        or len(manifest_at_reference) != 64
    ):
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_baseline_report_invalid"
        )
    if current_manifest_sha256 != manifest_at_reference:
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_manifest_changed_since_reference"
        )
    baseline_requirements = baseline.get("requirements")
    expected_requirements = {
        "required_topics": dict(policy.required_topics),
        "required_dialogue_goals": dict(policy.required_dialogue_goals),
        "required_emotional_tones": dict(policy.required_emotional_tones),
        "required_response_properties": dict(policy.required_response_properties),
    }
    if not isinstance(baseline_requirements, Mapping) or any(
        baseline_requirements.get(key) != value
        for key, value in expected_requirements.items()
    ):
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_policy_changed_since_baseline"
        )
    return baseline, {
        "coverage_snapshot_sha256": _sha256(
            BASELINE_COVERAGE_REPORT_PATH,
            "cross_candidate_coverage_audit_baseline_hash_failed",
        ),
        "current_manifest_sha256": current_manifest_sha256,
        "manifest_matches_verified_reference": True,
        "policy_requirements_match_baseline": True,
        "full_raw_corpus_reaudit_performed": False,
        "full_raw_corpus_reaudit_required_before_training": True,
    }


def _contains_raw_dialogue_content(value: Any) -> bool:
    """Не даёт report сохранить реплики или поля переписки."""

    if isinstance(value, Mapping):
        if RAW_DIALOGUE_KEYS.intersection(value):
            return True
        return any(_contains_raw_dialogue_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_raw_dialogue_content(item) for item in value)
    return False


def _empty_policy_counts(policy: DialogueCoveragePolicy) -> dict[str, Counter[str]]:
    """Создаёт полные счётчики только по tags из неизменяемой policy."""

    return {
        "topic_turns": Counter({tag: 0 for tag, _ in policy.required_topics}),
        "dialogue_goal_turns": Counter(
            {tag: 0 for tag, _ in policy.required_dialogue_goals}
        ),
        "emotional_tone_turns": Counter(
            {tag: 0 for tag, _ in policy.required_emotional_tones}
        ),
        "response_property_turns": Counter(
            {tag: 0 for tag, _ in policy.required_response_properties}
        ),
    }


def _policy_requirements(policy: DialogueCoveragePolicy) -> dict[str, dict[str, int]]:
    """Преобразует contract policy в JSON-friendly requirement maps."""

    return {
        "topic_turns": dict(policy.required_topics),
        "dialogue_goal_turns": dict(policy.required_dialogue_goals),
        "emotional_tone_turns": dict(policy.required_emotional_tones),
        "response_property_turns": dict(policy.required_response_properties),
    }


def _increment_item_coverage(
    counts: dict[str, Counter[str]],
    item: Mapping[str, Any],
) -> None:
    """Добавляет только turn-count и semantic labels approved queue item."""

    turns = item.get("turn_count")
    if not isinstance(turns, int) or turns < 1:
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_turn_count_invalid"
        )
    values = {
        "topic_turns": item.get("topic"),
        "dialogue_goal_turns": item.get("dialogue_goal"),
        "emotional_tone_turns": item.get("emotional_tone"),
    }
    for category, tag in values.items():
        if isinstance(tag, str) and tag in counts[category]:
            counts[category][tag] += turns
    properties = item.get("expected_response_properties")
    if not isinstance(properties, list) or not all(isinstance(item, str) for item in properties):
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_properties_invalid"
        )
    for property_name in properties:
        if property_name in counts["response_property_turns"]:
            counts["response_property_turns"][property_name] += turns


def _is_static_record(record: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
    """Отделяет статичный dialogue draft от runtime web/tool contract."""

    if item.get("topic") in STATIC_AUTHORING_EXCLUDED_TOPICS:
        return False
    properties = item.get("expected_response_properties")
    if not isinstance(properties, list):
        return False
    if RUNTIME_PROPERTY_MARKERS.intersection(properties):
        return False
    turns = record.get("turns")
    if not isinstance(turns, list):
        return False
    for turn in turns:
        if not isinstance(turn, Mapping) or not isinstance(turn.get("text"), str):
            return False
        text = turn["text"]
        if "[[" in text or "]]" in text or URL_PATTERN.search(text):
            return False
    return True


def _scope_status_matches_summary(scope: Mapping[str, Any], summary: Mapping[str, Any]) -> bool:
    """Проверяет, не расходится ли declared scope status с current ledger."""

    declared_complete = scope.get("status") == "human_review_coverage_complete"
    actual_complete = summary.get("required_human_review_coverage_complete") is True
    return declared_complete is actual_complete


def _load_scoped_candidate(
    spec: ScopedCandidateSpec,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Загружает один generic scoped candidate через его строгий workflow."""

    try:
        scope = scoped_review.load_scope(spec.scope_path, review_root=REVIEW_ROOT)
        records, _ = scoped_review.load_candidate(spec.candidate_path, review_root=REVIEW_ROOT)
        queue = scoped_review.load_queue(
            spec.queue_path,
            scope_path=spec.scope_path,
            candidate_path=spec.candidate_path,
            results_root=RESULTS_ROOT,
            review_root=REVIEW_ROOT,
        )
        decisions = scoped_review.load_decisions(
            spec.decisions_path,
            queue,
            review_root=REVIEW_ROOT,
        )
        summary = scoped_review.build_summary(queue, decisions, scope)
    except scoped_review.ScopedReviewError as error:
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_scoped_input_invalid"
        ) from error
    if scope.get("scope_id") != spec.scope_id or queue.get("scope_id") != spec.scope_id:
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_scoped_identity_mismatch"
        )
    return scope, records, queue, decisions, summary


def _pending_domain_hold_ids(
    spec: ScopedCandidateSpec,
    scope: Mapping[str, Any],
    records: list[dict[str, Any]],
    queue: Mapping[str, Any],
    decisions: list[dict[str, Any]],
) -> set[str]:
    """Не засчитывает medical draft до отдельного подтверждённого review."""

    if spec.scope_id != MEDICAL_HOLD_SCOPE_ID:
        return set()
    expected_gate = {
        "record_id": MEDICAL_HOLD_RECORD_ID,
        "status": "pending",
        "required_before_training_snapshot": True,
    }
    if (
        scope.get("medical_domain_review") != expected_gate
        or "medical_domain_review_or_exclude_scene_006_before_training_snapshot"
        not in scope.get("promotion_requirements", [])
    ):
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_medical_gate_invalid"
        )
    records_by_id = {record["id"]: record for record in records}
    items_by_id = {item["id"]: item for item in queue["items"]}
    decision_by_id = {item["candidate_id"]: item for item in decisions}
    if (
        MEDICAL_HOLD_RECORD_ID not in records_by_id
        or MEDICAL_HOLD_RECORD_ID not in items_by_id
        or decision_by_id.get(MEDICAL_HOLD_RECORD_ID, {}).get("decision") != "approved"
        or items_by_id[MEDICAL_HOLD_RECORD_ID].get("topic") != "safety_boundaries"
        or not _is_static_record(
            records_by_id[MEDICAL_HOLD_RECORD_ID],
            items_by_id[MEDICAL_HOLD_RECORD_ID],
        )
    ):
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_medical_record_invalid"
        )
    completed_audit = _read_json_object(
        MEDICAL_COMPLETED_REVIEW_AUDIT_PATH,
        "cross_candidate_coverage_audit_medical_review_audit_invalid",
    )
    expected_hashes = {
        "scope_sha256": _sha256(spec.scope_path, "cross_candidate_coverage_audit_scope_hash_failed"),
        "candidate_sha256": _sha256(
            spec.candidate_path, "cross_candidate_coverage_audit_candidate_hash_failed"
        ),
        "queue_sha256": _sha256(spec.queue_path, "cross_candidate_coverage_audit_queue_hash_failed"),
        "decisions_sha256": _sha256(
            spec.decisions_path, "cross_candidate_coverage_audit_decisions_hash_failed"
        ),
    }
    audit = completed_audit.get("audit")
    if (
        completed_audit.get("scope_id") != MEDICAL_HOLD_SCOPE_ID
        or completed_audit.get("mode") != "brain_v3_core_dialogue_completed_review_audit"
        or completed_audit.get("inputs") != expected_hashes
        or not isinstance(audit, Mapping)
        or audit.get("approved_records") != 6
        or audit.get("full_human_review_coverage_complete") is not True
        or audit.get("medical_domain_review_status") != "pending"
        or audit.get("training_allowed") is not False
        or audit.get("promotion_allowed") is not False
        or completed_audit.get("training_started") is not False
        or _contains_raw_dialogue_content(completed_audit)
    ):
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_medical_review_audit_invalid"
        )
    return {MEDICAL_HOLD_RECORD_ID}


def _load_legacy_state() -> dict[str, Any]:
    """Проверяет старый manual workflow, но не исправляет его состояние сам."""

    try:
        scope = manual_review.load_scope(LEGACY_SCOPE_PATH, review_root=REVIEW_ROOT)
        queue = manual_human_review.load_review_queue(
            LEGACY_QUEUE_PATH,
            scope_path=LEGACY_SCOPE_PATH,
            candidate_path=LEGACY_CANDIDATE_PATH,
            results_root=RESULTS_ROOT,
            review_root=REVIEW_ROOT,
        )
        decisions = manual_human_review.load_review_decisions(
            LEGACY_DECISIONS_PATH,
            queue,
            review_root=REVIEW_ROOT,
        )
        ratio = scope["review"]["minimum_general_stratified_review_ratio"]
        summary = manual_human_review.build_review_summary(
            queue,
            decisions,
            general_review_ratio=ratio,
        )
    except (manual_review.ManualCandidateReviewError, manual_human_review.ManualCandidateHumanReviewError) as error:
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_legacy_input_invalid"
        ) from error
    return {
        "scope": scope,
        "queue": queue,
        "summary": summary,
        "scope_status_matches_summary": _scope_status_matches_summary(scope, summary),
    }


def _counter_as_dict(counter: Counter[str]) -> dict[str, int]:
    """Стабилизирует порядок JSON без пропуска zero requirement tags."""

    return dict(sorted(counter.items()))


def _combined_counts(
    base: Mapping[str, Any],
    delta: Mapping[str, Counter[str]],
) -> dict[str, dict[str, int]]:
    """Считает только hypothetical coverage, не меняя active corpus."""

    result: dict[str, dict[str, int]] = {}
    for key, extra in delta.items():
        baseline = base.get(key)
        if not isinstance(baseline, Mapping):
            raise CrossCandidateCoverageAuditError(
                "cross_candidate_coverage_audit_baseline_counts_invalid"
            )
        result[key] = {
            tag: int(baseline.get(tag, 0)) + amount
            for tag, amount in sorted(extra.items())
        }
    return result


def _shortfalls(
    requirements: Mapping[str, Mapping[str, int]],
    observed: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int]]:
    """Возвращает только недостающие turn counts по каждой policy-категории."""

    return {
        category: {
            tag: max(0, minimum - int(observed[category].get(tag, 0)))
            for tag, minimum in sorted(required.items())
            if max(0, minimum - int(observed[category].get(tag, 0))) > 0
        }
        for category, required in requirements.items()
    }


def _recommended_static_topics(
    requirements: Mapping[str, Mapping[str, int]],
    baseline_topics: Mapping[str, int],
    static_topics: Mapping[str, int],
) -> list[dict[str, int | str]]:
    """Выбирает следующие topic priorities из реального, а не шаблонного дефицита."""

    priorities: list[dict[str, int | str]] = []
    for topic, required_turns in requirements["topic_turns"].items():
        if topic in STATIC_AUTHORING_EXCLUDED_TOPICS:
            continue
        baseline_turns = int(baseline_topics.get(topic, 0))
        approved_static_turns = int(static_topics.get(topic, 0))
        shortfall = max(0, required_turns - baseline_turns - approved_static_turns)
        if shortfall == 0:
            continue
        priorities.append(
            {
                "topic": topic,
                "baseline_turns": baseline_turns,
                "approved_static_review_only_turns": approved_static_turns,
                "remaining_turn_shortfall": shortfall,
            }
        )
    return sorted(
        priorities,
        key=lambda item: (
            int(item["approved_static_review_only_turns"]) > 0,
            -int(item["remaining_turn_shortfall"]),
            str(item["topic"]),
        ),
    )[:6]


def audit_input_paths() -> tuple[Path, ...]:
    """Перечисляет protected inputs для тестов неизменности audit."""

    paths: list[Path] = [
        DEFAULT_MANIFEST,
        DEFAULT_POLICY,
        BASELINE_COVERAGE_REPORT_PATH,
        MANIFEST_REFERENCE_SNAPSHOT_PATH,
    ]
    for spec in SCOPED_CANDIDATES:
        paths.extend(
            [
                spec.scope_path,
                spec.candidate_path,
                spec.queue_path,
                spec.decisions_path,
            ]
        )
    paths.append(MEDICAL_COMPLETED_REVIEW_AUDIT_PATH)
    paths.extend(
        [
            LEGACY_SCOPE_PATH,
            LEGACY_CANDIDATE_PATH,
            LEGACY_QUEUE_PATH,
            LEGACY_DECISIONS_PATH,
        ]
    )
    return tuple(paths)


def build_cross_candidate_coverage_audit() -> dict[str, Any]:
    """Строит строгий metadata-only cross-candidate coverage audit."""

    try:
        policy = load_dialogue_coverage_policy(DEFAULT_POLICY)
        baseline, baseline_verification = _load_baseline_snapshot(policy)
    except CrossCandidateCoverageAuditError:
        raise
    except (DialogueCoverageError, OSError, ValueError) as error:
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_baseline_invalid"
        ) from error
    if baseline.get("coverage_observed") is not True:
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_baseline_not_observed"
        )

    requirements = _policy_requirements(policy)
    static_counts = _empty_policy_counts(policy)
    runtime_counts = _empty_policy_counts(policy)
    candidates: list[dict[str, Any]] = []
    inconsistencies: list[dict[str, str]] = []
    static_turns = 0
    runtime_turns = 0
    approved_static_records_excluded = 0
    approved_incomplete_review_records_excluded = 0
    approved_medical_records_held = 0

    for spec in SCOPED_CANDIDATES:
        scope, records, queue, decisions, summary = _load_scoped_candidate(spec)
        records_by_id = {record["id"]: record for record in records}
        approved_ids = {
            decision["candidate_id"]
            for decision in decisions
            if decision["decision"] == "approved"
        }
        approved_items = [item for item in queue["items"] if item["id"] in approved_ids]
        static_items = [
            item
            for item in approved_items
            if _is_static_record(records_by_id[item["id"]], item)
        ]
        runtime_items = [item for item in approved_items if item not in static_items]
        domain_hold_ids = _pending_domain_hold_ids(spec, scope, records, queue, decisions)
        complete_clean_review = (
            summary["required_human_review_coverage_complete"] is True
            and summary["rejected_records"] == 0
            and summary["unreviewed_records"] == 0
            and scope["status"] == "human_review_coverage_complete"
        )
        provisional_static_items = (
            [item for item in static_items if item["id"] not in domain_hold_ids]
            if complete_clean_review
            else []
        )
        excluded_static_items = [
            item for item in static_items if item not in provisional_static_items
        ]
        approved_static_records_excluded += len(excluded_static_items)
        if not complete_clean_review:
            approved_incomplete_review_records_excluded += len(excluded_static_items)
        approved_medical_records_held += len(domain_hold_ids)
        for item in provisional_static_items:
            _increment_item_coverage(static_counts, item)
            static_turns += item["turn_count"]
        for item in runtime_items:
            _increment_item_coverage(runtime_counts, item)
            runtime_turns += item["turn_count"]
        status_matches = _scope_status_matches_summary(scope, summary)
        if not status_matches:
            inconsistencies.append(
                {
                    "scope_id": spec.scope_id,
                    "code": "scope_status_summary_mismatch",
                }
            )
        candidates.append(
            {
                "scope_id": spec.scope_id,
                "workflow": "scoped_review",
                "scope_status": scope["status"],
                "review_status": summary["review_status"],
                "scope_status_matches_summary": status_matches,
                "candidate_records": queue["candidate_records"],
                "reviewed_records": summary["reviewed_records"],
                "approved_records": summary["approved_records"],
                "rejected_records": summary["rejected_records"],
                "unreviewed_records": summary["unreviewed_records"],
                "approved_static_records": len(provisional_static_items),
                "approved_static_records_excluded": len(excluded_static_items),
                "approved_medical_records_held": len(domain_hold_ids),
                "approved_runtime_contract_records": len(runtime_items),
                "training_candidate_allowed": summary["training_candidate_allowed"],
                "promotion_allowed": summary["promotion_allowed"],
                "input_hashes": {
                    "scope_sha256": _sha256(
                        spec.scope_path,
                        "cross_candidate_coverage_audit_scope_hash_failed",
                    ),
                    "candidate_sha256": _sha256(
                        spec.candidate_path,
                        "cross_candidate_coverage_audit_candidate_hash_failed",
                    ),
                    "queue_sha256": _sha256(
                        spec.queue_path,
                        "cross_candidate_coverage_audit_queue_hash_failed",
                    ),
                    "decisions_sha256": _sha256(
                        spec.decisions_path,
                        "cross_candidate_coverage_audit_decisions_hash_failed",
                    ),
                },
            }
        )

    legacy = _load_legacy_state()
    legacy_scope = legacy["scope"]
    legacy_summary = legacy["summary"]
    legacy_consistent = legacy["scope_status_matches_summary"]
    if not legacy_consistent:
        inconsistencies.append(
            {
                "scope_id": legacy_scope["scope_id"],
                "code": "legacy_scope_status_summary_mismatch",
            }
        )
    approved_static_records_excluded += legacy_summary["approved_records"]
    approved_incomplete_review_records_excluded += legacy_summary["approved_records"]
    candidates.append(
        {
            "scope_id": legacy_scope["scope_id"],
            "workflow": "legacy_manual_review",
            "scope_status": legacy_scope["status"],
            "review_status": legacy_summary["review_status"],
            "scope_status_matches_summary": legacy_consistent,
            "candidate_records": legacy_summary["queue_records"],
            "reviewed_records": legacy_summary["reviewed_records"],
            "approved_records": legacy_summary["approved_records"],
            "rejected_records": legacy_summary["rejected_records"],
            "unreviewed_records": legacy_summary["unreviewed_records"],
            "approved_static_records": 0,
            "approved_static_records_excluded": legacy_summary["approved_records"],
            "approved_medical_records_held": 0,
            "approved_runtime_contract_records": 0,
            "excluded_from_provisional_coverage": "legacy_mixed_outcomes_and_unreviewed_records",
            "training_candidate_allowed": legacy_summary["training_candidate_allowed"],
            "promotion_allowed": legacy_summary["promotion_allowed"],
            "input_hashes": {
                "scope_sha256": _sha256(
                    LEGACY_SCOPE_PATH,
                    "cross_candidate_coverage_audit_legacy_scope_hash_failed",
                ),
                "candidate_sha256": _sha256(
                    LEGACY_CANDIDATE_PATH,
                    "cross_candidate_coverage_audit_legacy_candidate_hash_failed",
                ),
                "queue_sha256": _sha256(
                    LEGACY_QUEUE_PATH,
                    "cross_candidate_coverage_audit_legacy_queue_hash_failed",
                ),
                "decisions_sha256": _sha256(
                    LEGACY_DECISIONS_PATH,
                    "cross_candidate_coverage_audit_legacy_decisions_hash_failed",
                ),
            },
        }
    )

    base_observed = baseline["observed"]
    static_delta = {key: _counter_as_dict(value) for key, value in static_counts.items()}
    runtime_delta = {key: _counter_as_dict(value) for key, value in runtime_counts.items()}
    hypothetical_static = _combined_counts(base_observed, static_counts)
    static_shortfalls = _shortfalls(requirements, hypothetical_static)
    recommended_topics = _recommended_static_topics(
        requirements,
        base_observed["topic_turns"],
        static_delta["topic_turns"],
    )

    report = {
        "format_version": FORMAT_VERSION,
        "mode": AUDIT_MODE,
        "policy_id": policy.policy_id,
        "base_corpus": {
            "records_accepted": baseline["base_corpus_audit"]["records_accepted"],
            "unique_dialogue_turns": baseline["base_corpus_audit"]["unique_dialogue_turns"],
            "coverage_allowed": baseline["coverage_allowed"],
            "generator_training_ready": baseline["generator_training_ready"],
            "baseline_verification": baseline_verification,
        },
        "candidate_audit": {
            "candidates_checked": len(candidates),
            "candidate_states": candidates,
            "state_inconsistencies": inconsistencies,
            "legacy_candidate_excluded_from_provisional_coverage": True,
            "legacy_approved_records_excluded": legacy_summary["approved_records"],
            "approved_static_records_excluded": approved_static_records_excluded,
            "approved_incomplete_review_records_excluded": approved_incomplete_review_records_excluded,
            "approved_medical_records_held": approved_medical_records_held,
        },
        "approved_review_only_static_coverage": {
            "selection_rule": "complete_review_no_rejections_no_unreviewed_records_excluding_pending_medical_review",
            "records": sum(item["approved_static_records"] for item in candidates),
            "approved_turn_count": static_turns,
            "turn_counts_by_semantic_category": static_delta,
            "hypothetical_active_plus_static_review_only": hypothetical_static,
            "remaining_policy_shortfalls": static_shortfalls,
        },
        "approved_runtime_contract_metadata": {
            "records": sum(item["approved_runtime_contract_records"] for item in candidates),
            "approved_runtime_contract_turn_count": runtime_turns,
            "turn_counts_by_semantic_category": runtime_delta,
            "excluded_from_static_training_path": True,
        },
        "recommended_next_static_topics": recommended_topics,
        "boundaries": {
            "active_manifest_updated": False,
            "staging_created": False,
            "training_started": False,
            "training_candidate_allowed": False,
            "promotion_allowed": False,
            "github_changed": False,
            "report_contains_raw_dialogue_content": False,
        },
        "next_gate": "Confirm semantic labels of already reviewed OASST1 records; keep medical scene 006 on hold pending domain review or exclusion, then complete raw corpus and duplicate audits before any separate owner decision about staging or training.",
    }
    if _contains_raw_dialogue_content(report):
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_report_not_metadata_only"
        )
    return report


def audit_markdown(report: Mapping[str, Any]) -> str:
    """Пишет короткий Markdown отчёт без переноса диалоговых реплик."""

    candidate_audit = report["candidate_audit"]
    static_coverage = report["approved_review_only_static_coverage"]
    runtime = report["approved_runtime_contract_metadata"]
    priorities = report["recommended_next_static_topics"]
    baseline_verification = report["base_corpus"]["baseline_verification"]
    lines = [
        "# Brain v3 — cross-candidate semantic coverage audit",
        "",
        "## Результат",
        "",
        f"- Проверено candidate workflows: `{candidate_audit['candidates_checked']}`.",
        f"- В предварительном статичном покрытии: `{static_coverage['records']}` сцен / `{static_coverage['approved_turn_count']}` ходов.",
        f"- Одобренных медицинских сцен на отдельном hold: `{candidate_audit['approved_medical_records_held']}`.",
        f"- Runtime-contract сцен отдельно от static path: `{runtime['records']}`.",
        f"- Несогласованных legacy состояний: `{len(candidate_audit['state_inconsistencies'])}`.",
        "- Ни одна review-only сцена не стала частью active corpus или training snapshot.",
        "",
        "## Приоритеты следующей статичной партии",
        "",
    ]
    for item in priorities:
        lines.append(
            "- `{}`: baseline `{}`, approved static `{}`, remaining shortfall `{}` turns.".format(
                item["topic"],
                item["baseline_turns"],
                item["approved_static_review_only_turns"],
                item["remaining_turn_shortfall"],
            )
        )
    lines.extend(
        [
            "",
            "## Граница",
            "",
            "Отчёт содержит только hashes, labels и агрегированные counts. Runtime web/tool",
            "contracts учтены отдельно. Старый manual candidate имеет смешанные verdict",
            "и непросмотренные сцены; v1.2 проверен лишь частично. Из этих партий",
            f"`{candidate_audit['approved_incomplete_review_records_excluded']}` одобренных записей",
            "не вошли в предварительный расчёт. Отдельно не засчитана одна",
            "медицинская сцена v1.6-006: human review завершён, но профильная",
            "проверка или исключение перед обучением ещё не выполнены. Всего",
            f"исключено `{candidate_audit['approved_static_records_excluded']}` approved static сцен.",
            "Manifest совпадает с проверенным reference snapshot: "
            f"`{str(baseline_verification['manifest_matches_verified_reference']).lower()}`.",
            "Полный raw corpus audit и проверка межкандидатных повторов нужны",
            "перед любым training gate.",
            "",
            "## Следующий шаг",
            "",
            "Подтвердить semantic labels уже просмотренного OASST1 и решить отдельный",
            "medical-domain gate для v1.6-006. Staging, training и изменение active",
            "corpus требуют полного аудита и нового решения владельца.",
            "",
        ]
    )
    return "\n".join(lines)


def write_audit_report(
    report: Mapping[str, Any],
    destination: Path = DEFAULT_AUDIT_REPORT_DIRECTORY,
    *,
    results_root: Path = RESULTS_ROOT,
) -> None:
    """Сохраняет immutable report без изменения review/corpus входов."""

    output = _safe_new_report_directory(destination, results_root)
    try:
        output.mkdir(parents=True)
        (output / "brain_v3_cross_candidate_coverage_audit.json").write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output / "BRAIN_V3_CROSS_CANDIDATE_COVERAGE_AUDIT.md").write_text(
            audit_markdown(report),
            encoding="utf-8",
        )
    except OSError as error:
        raise CrossCandidateCoverageAuditError(
            "cross_candidate_coverage_audit_report_write_failed"
        ) from error


def _parser() -> argparse.ArgumentParser:
    """Оставляет явный read-only audit единственным CLI действием."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", required=True)
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
    """Запускает audit без staging, training или активизации данных."""

    _configure_utf8_output()
    args = _parser().parse_args()
    try:
        report = build_cross_candidate_coverage_audit()
        write_audit_report(report, args.audit_report_directory)
        print(
            json.dumps(
                {
                    "candidates_checked": report["candidate_audit"]["candidates_checked"],
                    "approved_static_records": report[
                        "approved_review_only_static_coverage"
                    ]["records"],
                    "training_started": report["boundaries"]["training_started"],
                    "promotion_allowed": report["boundaries"]["promotion_allowed"],
                },
                ensure_ascii=False,
            )
        )
    except CrossCandidateCoverageAuditError as error:
        raise SystemExit(f"Cross-candidate coverage audit остановлен: {error}") from error


if __name__ == "__main__":
    main()
