"""Локальный reusable workflow для небольших review-only repair candidates Brain v3.

В отличие от прежнего фиксированного v1 workflow, этот модуль читает scope
локального candidate и строит queue, human-review ledger и summary по его
явным правилам. Он не меняет active corpus, API, веса или обучение.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import secrets
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ai_core.brain_v3_corpus import (
    ALLOWED_RESPONSE_PROPERTIES,
    NearDuplicateIndex,
    duplicate_signature,
    text_risk_code,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = PROJECT_ROOT / "data" / "brain_v3" / "reviews"
RESULTS_ROOT = PROJECT_ROOT / "evaluation" / "results"

DEFAULT_SCOPE_PATH = REVIEW_ROOT / "brain-v3-memory-repair-candidate-v1.1.scope.json"
DEFAULT_CANDIDATE_PATH = (
    REVIEW_ROOT / "brain-v3-memory-repair-candidate-v1.1.review.jsonl"
)
DEFAULT_REPORT_DIRECTORY = (
    RESULTS_ROOT / "brain-v3-memory-repair-candidate-v1.1-review-queue-v0.1"
)
DEFAULT_QUEUE_PATH = DEFAULT_REPORT_DIRECTORY / "brain_v3_scoped_review_queue.json"
DEFAULT_DECISIONS_PATH = (
    REVIEW_ROOT / "brain-v3-memory-repair-candidate-v1.1.decisions.jsonl"
)
DEFAULT_SUMMARY_DIRECTORY = (
    RESULTS_ROOT / "brain-v3-memory-repair-candidate-v1.1-human-review-v0.1"
)

FORMAT_VERSION = 1
QUEUE_MODE = "brain_v3_scoped_review_only_queue"
DECISION_MODE = "brain_v3_scoped_human_review_decision"
SUMMARY_MODE = "brain_v3_scoped_human_review_summary"
TURN_ROLES = ("user", "assistant", "user", "assistant")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVIEWER_ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
RAW_CONTENT_KEYS = frozenset({"text", "turns", "conversation", "message"})
TOOL_RESULT_TOPICS = frozenset({"tool_routing", "web_grounding"})
TOOL_RESULT_PROPERTIES = frozenset({"tool_result_returned", "no_action_promise"})
ALLOWED_SCOPE_STATUSES = frozenset(
    {
        "authoring_not_started",
        "awaiting_human_review",
        "human_review_in_progress",
        "human_review_coverage_complete",
    }
)

Decision = Literal["approved", "rejected"]

APPROVAL_REASON_CODES = frozenset(
    {
        "provenance_checked",
        "coherent_follow_up",
        "natural_non_template_tone",
        "metadata_route_match",
    }
)
REJECTION_REASON_CODES = frozenset(
    {
        "provenance_risk",
        "personal_data_risk",
        "incoherent_follow_up",
        "template_like",
        "irrelevant_response",
        "unsupported_claim",
        "unfounded_memory",
        "privacy_boundary_failure",
        "unsafe_or_unhelpful_advice",
        "incorrect_metadata",
        "other_quality_failure",
    }
)


class ScopedReviewError(ValueError):
    """Останавливает local review до записи непроверяемых данных."""


def _safe_path_inside(path: Path, root: Path, error_code: str) -> Path:
    """Не допускает чтение или запись за пределами review/results root."""

    resolved_path = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ScopedReviewError(error_code) from error
    return resolved_path


def _contains_raw_turn_content(value: Any) -> bool:
    """Запрещает попадание исходных реплик в metadata-only артефакты."""

    if isinstance(value, Mapping):
        if RAW_CONTENT_KEYS.intersection(value):
            return True
        return any(_contains_raw_turn_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_raw_turn_content(item) for item in value)
    return False


def _read_json_object(path: Path, error_code: str) -> dict[str, Any]:
    """Читает один JSON object, не раскрывая source content при ошибке."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ScopedReviewError(error_code) from error
    if not isinstance(value, dict):
        raise ScopedReviewError(error_code)
    return value


def _read_jsonl(path: Path, error_code: str) -> tuple[list[dict[str, Any]], str]:
    """Читает local JSONL исключительно как данные, не исполняя его текст."""

    try:
        raw = path.read_bytes()
        records = [
            json.loads(line)
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ScopedReviewError(error_code) from error
    if not records or not all(isinstance(record, dict) for record in records):
        raise ScopedReviewError(error_code)
    return records, hashlib.sha256(raw).hexdigest()


def _canonical_json_sha256(value: Mapping[str, Any]) -> str:
    """Строит стабильный per-record SHA-256 для immutable queue."""

    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalise_text(value: str) -> str:
    """Нормализует текст только для duplicate audit в памяти процесса."""

    return " ".join(value.casefold().split())


def _required_scope_checks(value: Any) -> list[str]:
    """Проверяет четыре обязательных human-review checks scope."""

    expected = {
        "provenance_and_no_personal_data",
        "whole_dialogue_coherence",
        "natural_non_template_tone",
        "metadata_and_route_match",
    }
    if not isinstance(value, list) or set(value) != expected or len(value) != len(expected):
        raise ScopedReviewError("scoped_review_scope_checks_invalid")
    return value


def load_scope(
    scope_path: Path = DEFAULT_SCOPE_PATH,
    *,
    review_root: Path = REVIEW_ROOT,
) -> dict[str, Any]:
    """Загружает self-contained scope repair candidate и проверяет policy."""

    safe_path = _safe_path_inside(
        scope_path,
        review_root,
        "scoped_review_scope_outside_review_root",
    )
    if not safe_path.is_file():
        raise ScopedReviewError("scoped_review_scope_missing")
    scope = _read_json_object(safe_path, "scoped_review_scope_invalid")
    scope_id = scope.get("scope_id")
    status = scope.get("status")
    record_id_prefix = scope.get("record_id_prefix")
    if (
        scope.get("format_version") != FORMAT_VERSION
        or not isinstance(scope_id, str)
        or not scope_id
        or status not in ALLOWED_SCOPE_STATUSES
        or not isinstance(record_id_prefix, str)
        or not record_id_prefix
        or scope.get("candidate_is_active") is not False
        or scope.get("training_eligible") is not False
    ):
        raise ScopedReviewError("scoped_review_scope_invalid")
    source = scope.get("source")
    expected_source = {
        "source_id": scope_id,
        "source_kind": "owned",
        "license": "OWNED_BY_PROJECT",
        "authoring_method": "manual_original_only",
        "external_text_allowed": False,
        "personal_chat_allowed": False,
        "template_generator_allowed": False,
    }
    if not isinstance(source, Mapping) or dict(source) != expected_source:
        raise ScopedReviewError("scoped_review_scope_source_invalid")

    tranche = scope.get("first_review_tranche")
    if not isinstance(tranche, Mapping):
        raise ScopedReviewError("scoped_review_scope_tranche_invalid")
    topics = tranche.get("topics")
    topic_goal_map = tranche.get("topic_goal_map")
    tones = tranche.get("required_tones")
    properties = tranche.get("required_properties")
    if (
        not isinstance(tranche.get("records"), int)
        or tranche["records"] < 1
        or tranche.get("turns_per_record") != len(TURN_ROLES)
        or not isinstance(topics, dict)
        or not topics
        or not all(isinstance(key, str) and isinstance(value, int) and value > 0 for key, value in topics.items())
        or sum(topics.values()) != tranche["records"]
        or not isinstance(topic_goal_map, dict)
        or set(topic_goal_map) != set(topics)
        or not all(isinstance(value, str) and value for value in topic_goal_map.values())
        or set(tranche.get("required_dialogue_goals", [])) != set(topic_goal_map.values())
        or not isinstance(tones, list)
        or not tones
        or not all(isinstance(value, str) and value for value in tones)
        or len(tones) != len(set(tones))
        or not isinstance(properties, list)
        or not properties
        or not set(properties) <= ALLOWED_RESPONSE_PROPERTIES
        or len(properties) != len(set(properties))
    ):
        raise ScopedReviewError("scoped_review_scope_tranche_invalid")

    review = scope.get("review")
    if not isinstance(review, Mapping):
        raise ScopedReviewError("scoped_review_scope_review_invalid")
    full_review_topics = review.get("full_review_topics")
    ratio = review.get("minimum_general_stratified_review_ratio")
    if (
        review.get("human_reviewer_required") is not True
        or not isinstance(full_review_topics, list)
        or not full_review_topics
        or not set(full_review_topics) <= set(topics)
        or not isinstance(ratio, (int, float))
        or not 0 <= ratio <= 1
    ):
        raise ScopedReviewError("scoped_review_scope_review_invalid")
    _required_scope_checks(review.get("required_checks"))
    return scope


def load_candidate(
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    *,
    review_root: Path = REVIEW_ROOT,
) -> tuple[list[dict[str, Any]], str]:
    """Загружает только review-only candidate, а не corpus/staging данные."""

    safe_path = _safe_path_inside(
        candidate_path,
        review_root,
        "scoped_review_candidate_outside_review_root",
    )
    if safe_path.suffix.lower() != ".jsonl" or not safe_path.is_file():
        raise ScopedReviewError("scoped_review_candidate_missing")
    return _read_jsonl(safe_path, "scoped_review_candidate_invalid_jsonl")


def _validate_source(value: Any, scope: Mapping[str, Any]) -> dict[str, Any]:
    """Подтверждает project-owned draft без external/personal/template input."""

    expected = {
        "source_id": scope["scope_id"],
        "source_kind": "owned",
        "license": "OWNED_BY_PROJECT",
        "authoring_method": "manual_original_only",
        "draft_author_role": "project_agent",
        "external_text_used": False,
        "personal_chat_used": False,
        "template_generator_used": False,
    }
    if not isinstance(value, dict) or value != expected:
        raise ScopedReviewError("scoped_review_candidate_source_invalid")
    return value


def _validate_scenario(
    value: Any,
    *,
    identifier: str,
    scope: Mapping[str, Any],
) -> dict[str, Any]:
    """Проверяет labels и обязательность full review по scope, а не догадке."""

    expected_keys = {
        "family_id",
        "topic",
        "dialogue_goal",
        "emotional_tone",
        "expected_response_properties",
        "requires_full_human_review",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ScopedReviewError("scoped_review_candidate_scenario_invalid")
    tranche = scope["first_review_tranche"]
    review = scope["review"]
    topic = value.get("topic")
    goal = value.get("dialogue_goal")
    tone = value.get("emotional_tone")
    properties = value.get("expected_response_properties")
    if (
        value.get("family_id") != identifier
        or topic not in tranche["topics"]
        or goal != tranche["topic_goal_map"][topic]
        or tone not in tranche["required_tones"]
        or not isinstance(properties, list)
        or not properties
        or not all(isinstance(item, str) for item in properties)
        or len(properties) != len(set(properties))
        or not set(properties) <= ALLOWED_RESPONSE_PROPERTIES
        or value.get("requires_full_human_review")
        is not (topic in set(review["full_review_topics"]))
    ):
        raise ScopedReviewError("scoped_review_candidate_scenario_invalid")
    property_set = set(properties)
    # Review-only tool/web scenes may describe a final route result, but this
    # metadata must not leak into an ordinary conversation scenario.
    if TOOL_RESULT_PROPERTIES.intersection(property_set) and topic not in TOOL_RESULT_TOPICS:
        raise ScopedReviewError("scoped_review_tool_result_properties_require_tool_topic")
    if "no_action_promise" in property_set and "tool_result_returned" not in property_set:
        raise ScopedReviewError("scoped_review_no_action_promise_requires_tool_result")
    return value


def _validate_turns(value: Any, *, expected_turn_count: int) -> list[dict[str, str]]:
    """Проверяет fixed role alternation и отсутствие рискованных маркеров."""

    if not isinstance(value, list) or len(value) != expected_turn_count:
        raise ScopedReviewError("scoped_review_candidate_turn_count_invalid")
    result: list[dict[str, str]] = []
    for index, turn in enumerate(value):
        if not isinstance(turn, dict) or set(turn) != {"role", "text"}:
            raise ScopedReviewError("scoped_review_candidate_turn_invalid")
        role = turn.get("role")
        text = turn.get("text")
        if role != TURN_ROLES[index] or not isinstance(text, str) or not text.strip():
            raise ScopedReviewError("scoped_review_candidate_turn_invalid")
        if text_risk_code(text, maximum=1_000) is not None:
            raise ScopedReviewError("scoped_review_candidate_turn_risk_detected")
        result.append({"role": role, "text": text})
    return result


def validate_records(
    records: Iterable[dict[str, Any]],
    scope: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Проверяет tranche и возвращает только metadata, пригодные для queue."""

    materialised = list(records)
    tranche = scope["first_review_tranche"]
    if len(materialised) != tranche["records"]:
        raise ScopedReviewError("scoped_review_candidate_record_count_invalid")
    identifiers: set[str] = set()
    families: set[str] = set()
    exact_dialogues: set[str] = set()
    near_duplicates = NearDuplicateIndex()
    topic_counts: Counter[str] = Counter()
    goals: set[str] = set()
    tones: set[str] = set()
    properties_covered: set[str] = set()
    metadata: list[dict[str, Any]] = []
    prefix = scope["record_id_prefix"]

    for record in materialised:
        expected_keys = {
            "format_version",
            "id",
            "review_status",
            "review_only",
            "source",
            "scenario",
            "turns",
        }
        if not isinstance(record, dict) or set(record) != expected_keys:
            raise ScopedReviewError("scoped_review_candidate_record_invalid")
        identifier = record.get("id")
        if (
            not isinstance(identifier, str)
            or not identifier.startswith(prefix)
            or not identifier[len(prefix) :].isdigit()
            or len(identifier[len(prefix) :]) != 3
            or identifier in identifiers
            or record.get("format_version") != FORMAT_VERSION
            or record.get("review_status") != "unreviewed"
            or record.get("review_only") is not True
        ):
            raise ScopedReviewError("scoped_review_candidate_identifier_invalid")
        _validate_source(record.get("source"), scope)
        scenario = _validate_scenario(record.get("scenario"), identifier=identifier, scope=scope)
        turns = _validate_turns(
            record.get("turns"),
            expected_turn_count=tranche["turns_per_record"],
        )
        normalised_dialogue = "\n".join(_normalise_text(turn["text"]) for turn in turns)
        if normalised_dialogue in exact_dialogues:
            raise ScopedReviewError("scoped_review_candidate_exact_duplicate")
        signature = duplicate_signature(normalised_dialogue)
        if near_duplicates.find(signature) is not None:
            raise ScopedReviewError("scoped_review_candidate_near_duplicate")
        family_id = scenario["family_id"]
        if family_id in families:
            raise ScopedReviewError("scoped_review_candidate_duplicate_family")
        identifiers.add(identifier)
        families.add(family_id)
        exact_dialogues.add(normalised_dialogue)
        near_duplicates.add(identifier, signature)
        topic_counts[scenario["topic"]] += 1
        goals.add(scenario["dialogue_goal"])
        tones.add(scenario["emotional_tone"])
        properties_covered.update(scenario["expected_response_properties"])
        metadata.append(
            {
                "id": identifier,
                "record_sha256": _canonical_json_sha256(record),
                "family_id": family_id,
                "topic": scenario["topic"],
                "dialogue_goal": scenario["dialogue_goal"],
                "emotional_tone": scenario["emotional_tone"],
                "expected_response_properties": sorted(scenario["expected_response_properties"]),
                "turn_count": len(turns),
                "requires_full_human_review": scenario["requires_full_human_review"],
                "review_status": "unreviewed",
            }
        )
    if dict(sorted(topic_counts.items())) != dict(sorted(tranche["topics"].items())):
        raise ScopedReviewError("scoped_review_candidate_topic_coverage_invalid")
    if goals != set(tranche["required_dialogue_goals"]):
        raise ScopedReviewError("scoped_review_candidate_goal_coverage_invalid")
    if not set(tranche["required_tones"]) <= tones:
        raise ScopedReviewError("scoped_review_candidate_tone_coverage_invalid")
    if not set(tranche["required_properties"]) <= properties_covered:
        raise ScopedReviewError("scoped_review_candidate_property_coverage_invalid")
    return sorted(metadata, key=lambda item: item["id"])


def _coverage_snapshot(items: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Считает semantic metadata без текстов сцен."""

    topics: Counter[str] = Counter()
    goals: Counter[str] = Counter()
    tones: Counter[str] = Counter()
    properties: Counter[str] = Counter()
    full_review: Counter[str] = Counter()
    for item in items:
        topics[str(item["topic"])] += 1
        goals[str(item["dialogue_goal"])] += 1
        tones[str(item["emotional_tone"])] += 1
        properties.update(item["expected_response_properties"])
        full_review[
            "full_review_required"
            if item["requires_full_human_review"]
            else "stratified_review_pool"
        ] += 1
    return {
        "topics": dict(sorted(topics.items())),
        "dialogue_goals": dict(sorted(goals.items())),
        "emotional_tones": dict(sorted(tones.items())),
        "response_properties": dict(sorted(properties.items())),
        "human_review_scope": dict(sorted(full_review.items())),
    }


def build_queue(
    records: Iterable[dict[str, Any]],
    scope: Mapping[str, Any],
    *,
    candidate_path: Path,
    candidate_sha256: str,
) -> dict[str, Any]:
    """Создаёт immutable metadata-only queue для конкретного scope."""

    items = validate_records(records, scope)
    queue = {
        "format_version": FORMAT_VERSION,
        "mode": QUEUE_MODE,
        "scope_id": scope["scope_id"],
        "candidate_path": str(candidate_path.expanduser().resolve()),
        "candidate_sha256": candidate_sha256,
        "candidate_records": len(items),
        "turns_per_record": scope["first_review_tranche"]["turns_per_record"],
        "review_status": "unreviewed",
        "review_checks_required": scope["review"]["required_checks"],
        "coverage": _coverage_snapshot(items),
        "items": items,
        "metadata_only": True,
        "candidate_is_active": False,
        "training_eligible": False,
        "active_manifest_updated": False,
        "training_started": False,
    }
    if _contains_raw_turn_content(queue):
        raise ScopedReviewError("scoped_review_queue_not_metadata_only")
    return queue


def queue_markdown(queue: Mapping[str, Any]) -> str:
    """Создаёт Markdown queue без исходных реплик."""

    coverage = queue["coverage"]
    return "\n".join(
        [
            f"# Brain v3 — scoped review queue: {queue['scope_id']}",
            "",
            "## Статус",
            "",
            "- Queue содержит только ID, SHA-256 и semantic metadata; реплики не копируются.",
            f"- Сцен: `{queue['candidate_records']}`.",
            "- Active corpus, API, веса и обучение не менялись.",
            "",
            "## Покрытие",
            "",
            f"- Темы: {coverage['topics']}.",
            f"- Goals: {coverage['dialogue_goals']}.",
            f"- Тоны: {coverage['emotional_tones']}.",
            f"- Human review: {coverage['human_review_scope']}.",
            "",
            "## Следующий шаг",
            "",
            "Провести human review согласно scope. Любой verdict остаётся metadata-only и не даёт право на training или promotion.",
            "",
        ]
    )


def create_queue(
    scope_path: Path = DEFAULT_SCOPE_PATH,
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    report_directory: Path = DEFAULT_REPORT_DIRECTORY,
    *,
    review_root: Path = REVIEW_ROOT,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    """Создаёт новую versioned queue без перезаписи результатов review."""

    scope = load_scope(scope_path, review_root=review_root)
    records, candidate_sha256 = load_candidate(candidate_path, review_root=review_root)
    queue = build_queue(
        records,
        scope,
        candidate_path=candidate_path,
        candidate_sha256=candidate_sha256,
    )
    destination = _safe_path_inside(
        report_directory,
        results_root,
        "scoped_review_report_outside_results_root",
    )
    if destination.exists():
        raise ScopedReviewError("scoped_review_report_already_exists")
    destination.mkdir(parents=True)
    (destination / "brain_v3_scoped_review_queue.json").write_text(
        json.dumps(queue, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (destination / "BRAIN_V3_SCOPED_REVIEW_QUEUE.md").write_text(
        queue_markdown(queue),
        encoding="utf-8",
    )
    return queue


def _validate_queue_item(item: Any) -> dict[str, Any]:
    """Проверяет metadata item перед созданием human verdict."""

    required_keys = {
        "id",
        "record_sha256",
        "family_id",
        "topic",
        "dialogue_goal",
        "emotional_tone",
        "expected_response_properties",
        "turn_count",
        "requires_full_human_review",
        "review_status",
    }
    if (
        not isinstance(item, dict)
        or set(item) != required_keys
        or _contains_raw_turn_content(item)
        or not isinstance(item.get("id"), str)
        or not SHA256_PATTERN.fullmatch(str(item.get("record_sha256")))
        or item.get("turn_count") != len(TURN_ROLES)
        or not isinstance(item.get("requires_full_human_review"), bool)
        or item.get("review_status") != "unreviewed"
    ):
        raise ScopedReviewError("scoped_review_queue_item_invalid")
    return item


def load_queue(
    queue_path: Path = DEFAULT_QUEUE_PATH,
    *,
    scope_path: Path = DEFAULT_SCOPE_PATH,
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    results_root: Path = RESULTS_ROOT,
    review_root: Path = REVIEW_ROOT,
) -> dict[str, Any]:
    """Связывает queue с актуальным immutable source по metadata и SHA-256."""

    safe_queue_path = _safe_path_inside(
        queue_path,
        results_root,
        "scoped_review_queue_outside_results_root",
    )
    if not safe_queue_path.is_file():
        raise ScopedReviewError("scoped_review_queue_missing")
    queue = _read_json_object(safe_queue_path, "scoped_review_queue_invalid")
    if _contains_raw_turn_content(queue):
        raise ScopedReviewError("scoped_review_queue_not_metadata_only")
    if (
        queue.get("format_version") != FORMAT_VERSION
        or queue.get("mode") != QUEUE_MODE
        or queue.get("review_status") != "unreviewed"
        or queue.get("metadata_only") is not True
        or queue.get("candidate_is_active") is not False
        or queue.get("training_eligible") is not False
        or queue.get("active_manifest_updated") is not False
        or queue.get("training_started") is not False
    ):
        raise ScopedReviewError("scoped_review_queue_invalid")
    items = queue.get("items")
    if not isinstance(items, list) or not items:
        raise ScopedReviewError("scoped_review_queue_invalid")
    validated_items = [_validate_queue_item(item) for item in items]
    if len({item["id"] for item in validated_items}) != len(validated_items):
        raise ScopedReviewError("scoped_review_queue_duplicate_identifier")
    scope = load_scope(scope_path, review_root=review_root)
    records, candidate_sha256 = load_candidate(candidate_path, review_root=review_root)
    expected = build_queue(
        records,
        scope,
        candidate_path=candidate_path,
        candidate_sha256=candidate_sha256,
    )
    expected_fields = (
        "scope_id",
        "candidate_path",
        "candidate_sha256",
        "candidate_records",
        "turns_per_record",
        "coverage",
        "items",
    )
    if any(queue.get(field) != expected.get(field) for field in expected_fields):
        raise ScopedReviewError("scoped_review_queue_source_mismatch")
    return queue


def _normalise_reason_codes(decision: Decision, reason_codes: Iterable[str]) -> list[str]:
    """Не допускает свободный текст в metadata-only decision ledger."""

    raw_codes = list(reason_codes)
    if not raw_codes or any(not isinstance(item, str) for item in raw_codes):
        raise ScopedReviewError("scoped_review_reason_missing")
    codes = sorted(set(raw_codes))
    allowed = APPROVAL_REASON_CODES if decision == "approved" else REJECTION_REASON_CODES
    if any(code not in allowed for code in codes):
        raise ScopedReviewError("scoped_review_reason_invalid")
    if decision == "approved" and not APPROVAL_REASON_CODES.issubset(codes):
        raise ScopedReviewError("scoped_review_approval_criteria_missing")
    return codes


def _normalise_reviewer_role(value: str) -> str:
    """Оставляет только нейтральную роль reviewer без персональных данных."""

    if not isinstance(value, str) or not REVIEWER_ROLE_PATTERN.fullmatch(value):
        raise ScopedReviewError("scoped_review_reviewer_role_invalid")
    return value


def _queue_item_by_id(queue: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    """Находит ID только в ранее валидированной immutable queue."""

    if not isinstance(candidate_id, str) or not candidate_id:
        raise ScopedReviewError("scoped_review_candidate_id_invalid")
    for item in queue["items"]:
        if item["id"] == candidate_id:
            return item
    raise ScopedReviewError("scoped_review_candidate_not_in_queue")


def create_decision(
    queue_item: Mapping[str, Any],
    scope: Mapping[str, Any],
    *,
    decision: Decision,
    reason_codes: Iterable[str],
    reviewer_role: str,
    reviewed_at: datetime | None = None,
) -> dict[str, Any]:
    """Создаёт один verified human verdict без реплик и без promotion rights."""

    if decision not in {"approved", "rejected"}:
        raise ScopedReviewError("scoped_review_decision_invalid")
    item = _validate_queue_item(dict(queue_item))
    timestamp = reviewed_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ScopedReviewError("scoped_review_timestamp_invalid")
    return {
        "format_version": FORMAT_VERSION,
        "mode": DECISION_MODE,
        "scope_id": scope["scope_id"],
        "candidate_id": item["id"],
        "record_sha256": item["record_sha256"],
        "decision": decision,
        "reason_codes": _normalise_reason_codes(decision, reason_codes),
        "reviewer_role": _normalise_reviewer_role(reviewer_role),
        "reviewed_at": timestamp.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _validate_decision(
    record: Any,
    queue_items: Mapping[str, Mapping[str, Any]],
    *,
    scope_id: str,
) -> dict[str, Any]:
    """Проверяет ledger record against queue without opening dialogue text."""

    expected_keys = {
        "format_version",
        "mode",
        "scope_id",
        "candidate_id",
        "record_sha256",
        "decision",
        "reason_codes",
        "reviewer_role",
        "reviewed_at",
    }
    if (
        not isinstance(record, dict)
        or set(record) != expected_keys
        or _contains_raw_turn_content(record)
        or record.get("format_version") != FORMAT_VERSION
        or record.get("mode") != DECISION_MODE
        or record.get("scope_id") != scope_id
    ):
        raise ScopedReviewError("scoped_review_decision_invalid")
    candidate_id = record.get("candidate_id")
    if candidate_id not in queue_items:
        raise ScopedReviewError("scoped_review_decision_invalid")
    if record.get("record_sha256") != queue_items[candidate_id]["record_sha256"]:
        raise ScopedReviewError("scoped_review_decision_metadata_mismatch")
    decision = record.get("decision")
    if decision not in {"approved", "rejected"}:
        raise ScopedReviewError("scoped_review_decision_invalid")
    _normalise_reason_codes(decision, record.get("reason_codes", []))
    _normalise_reviewer_role(record.get("reviewer_role"))
    if not isinstance(record.get("reviewed_at"), str) or not record["reviewed_at"].endswith("Z"):
        raise ScopedReviewError("scoped_review_timestamp_invalid")
    return record


def load_decisions(
    decisions_path: Path,
    queue: Mapping[str, Any],
    *,
    review_root: Path = REVIEW_ROOT,
) -> list[dict[str, Any]]:
    """Читает append-only metadata decisions; отсутствующий файл означает ноль verdict."""

    safe_path = _safe_path_inside(
        decisions_path,
        review_root,
        "scoped_review_decisions_outside_review_root",
    )
    if not safe_path.exists():
        return []
    if not safe_path.is_file():
        raise ScopedReviewError("scoped_review_decisions_invalid")
    queue_items = {item["id"]: item for item in queue["items"]}
    records: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    try:
        with safe_path.open("r", encoding="utf-8") as source_file:
            for line in source_file:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ScopedReviewError("scoped_review_decisions_invalid") from error
                record = _validate_decision(
                    value,
                    queue_items,
                    scope_id=queue["scope_id"],
                )
                if record["candidate_id"] in identifiers:
                    raise ScopedReviewError("scoped_review_duplicate_decision")
                identifiers.add(record["candidate_id"])
                records.append(record)
    except OSError as error:
        raise ScopedReviewError("scoped_review_decisions_read_failed") from error
    return records


def _atomic_write_lines(destination: Path, lines: Iterable[str]) -> None:
    """Записывает decision ledger атомарно и не оставляет частичный файл."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{secrets.token_hex(8)}.tmp"
    try:
        temporary.write_text("".join(lines), encoding="utf-8")
        temporary.replace(destination)
    except OSError as error:
        raise ScopedReviewError("scoped_review_decisions_write_failed") from error
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def append_decision(
    queue_path: Path,
    decisions_path: Path,
    *,
    candidate_id: str,
    decision: Decision,
    reason_codes: Iterable[str],
    reviewer_role: str,
    scope_path: Path = DEFAULT_SCOPE_PATH,
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    results_root: Path = RESULTS_ROOT,
    review_root: Path = REVIEW_ROOT,
    reviewed_at: datetime | None = None,
) -> dict[str, Any]:
    """Добавляет один реальный human verdict, не изменяя source/queue/model."""

    queue = load_queue(
        queue_path,
        scope_path=scope_path,
        candidate_path=candidate_path,
        results_root=results_root,
        review_root=review_root,
    )
    scope = load_scope(scope_path, review_root=review_root)
    item = _queue_item_by_id(queue, candidate_id)
    decisions = load_decisions(decisions_path, queue, review_root=review_root)
    if candidate_id in {record["candidate_id"] for record in decisions}:
        raise ScopedReviewError("scoped_review_duplicate_decision")
    record = create_decision(
        item,
        scope,
        decision=decision,
        reason_codes=reason_codes,
        reviewer_role=reviewer_role,
        reviewed_at=reviewed_at,
    )
    safe_path = _safe_path_inside(
        decisions_path,
        review_root,
        "scoped_review_decisions_outside_review_root",
    )
    _atomic_write_lines(
        safe_path,
        (
            f"{json.dumps(item, ensure_ascii=False, sort_keys=True)}\n"
            for item in [*decisions, record]
        ),
    )
    return record


def build_summary(
    queue: Mapping[str, Any],
    decisions: Iterable[Mapping[str, Any]],
    scope: Mapping[str, Any],
) -> dict[str, Any]:
    """Сводит review coverage, не превращая его в promotion/training approval."""

    queue_items = {item["id"]: item for item in queue["items"]}
    materialised = [
        _validate_decision(dict(item), queue_items, scope_id=scope["scope_id"])
        for item in decisions
    ]
    decision_ids = [item["candidate_id"] for item in materialised]
    if len(decision_ids) != len(set(decision_ids)):
        raise ScopedReviewError("scoped_review_duplicate_decision")
    decision_by_id = {item["candidate_id"]: item for item in materialised}
    full_items = [item for item in queue["items"] if item["requires_full_human_review"]]
    general_items = [item for item in queue["items"] if not item["requires_full_human_review"]]
    full_reviewed = [item for item in full_items if item["id"] in decision_by_id]
    general_reviewed = [item for item in general_items if item["id"] in decision_by_id]
    minimum_general = math.ceil(
        len(general_items) * scope["review"]["minimum_general_stratified_review_ratio"]
    )
    coverage_complete = (
        len(full_reviewed) == len(full_items)
        and len(general_reviewed) >= minimum_general
    )
    outcome = Counter(item["decision"] for item in materialised)
    rejected_ids = sorted(
        item["candidate_id"] for item in materialised if item["decision"] == "rejected"
    )
    if not materialised:
        review_status = "not_started"
    elif coverage_complete:
        review_status = "required_coverage_complete"
    else:
        review_status = "in_progress"
    if not coverage_complete:
        next_gate = "complete required human-review coverage before any further decision"
    elif rejected_ids:
        next_gate = "investigate rejected repair drafts; staging and training remain prohibited"
    else:
        next_gate = "request a separate owner decision about a new staging/audit candidate"
    return {
        "format_version": FORMAT_VERSION,
        "mode": SUMMARY_MODE,
        "scope_id": scope["scope_id"],
        "queue_records": len(queue_items),
        "reviewed_records": len(materialised),
        "approved_records": outcome["approved"],
        "rejected_records": outcome["rejected"],
        "unreviewed_records": len(queue_items) - len(materialised),
        "full_review_required_records": len(full_items),
        "full_reviewed_records": len(full_reviewed),
        "general_records": len(general_items),
        "minimum_general_reviews": minimum_general,
        "general_reviewed_records": len(general_reviewed),
        "required_human_review_coverage_complete": coverage_complete,
        "review_status": review_status,
        "rejected_candidate_ids": rejected_ids,
        "decisions": [
            {
                "candidate_id": item["candidate_id"],
                "record_sha256": item["record_sha256"],
                "decision": item["decision"],
                "reason_codes": item["reason_codes"],
                "reviewer_role": item["reviewer_role"],
                "reviewed_at": item["reviewed_at"],
            }
            for item in sorted(materialised, key=lambda item: item["candidate_id"])
        ],
        "training_candidate_allowed": False,
        "promotion_allowed": False,
        "next_gate": next_gate,
    }


def summary_markdown(summary: Mapping[str, Any]) -> str:
    """Рендерит summary без raw dialogue text."""

    return "\n".join(
        [
            f"# Brain v3 — scoped human-review summary: {summary['scope_id']}",
            "",
            "## Статус",
            "",
            f"- Проверено: `{summary['reviewed_records']}` из `{summary['queue_records']}`.",
            f"- Одобрено: `{summary['approved_records']}`; отклонено: `{summary['rejected_records']}`.",
            f"- Full review: `{summary['full_reviewed_records']}` из `{summary['full_review_required_records']}`.",
            f"- General sample: `{summary['general_reviewed_records']}` из `{summary['minimum_general_reviews']}`.",
            f"- Required coverage complete: `{str(summary['required_human_review_coverage_complete']).lower()}`.",
            f"- Training candidate allowed: `{str(summary['training_candidate_allowed']).lower()}`.",
            f"- Promotion allowed: `{str(summary['promotion_allowed']).lower()}`.",
            "",
            "## Граница",
            "",
            "Summary содержит только metadata. Он не меняет active manifest, API, веса или обучение и не даёт автоматического права на staging.",
            "",
            "## Следующий gate",
            "",
            f"`{summary['next_gate']}`.",
            "",
        ]
    )


def write_summary(
    destination: Path,
    summary: Mapping[str, Any],
    *,
    results_root: Path = RESULTS_ROOT,
) -> None:
    """Создаёт новый immutable summary directory без перезаписи истории."""

    safe_destination = _safe_path_inside(
        destination,
        results_root,
        "scoped_review_summary_outside_results_root",
    )
    if safe_destination.exists():
        raise ScopedReviewError("scoped_review_summary_already_exists")
    try:
        safe_destination.mkdir(parents=True)
        (safe_destination / "brain_v3_scoped_human_review_summary.json").write_text(
            json.dumps(dict(summary), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (safe_destination / "BRAIN_V3_SCOPED_HUMAN_REVIEW_SUMMARY.md").write_text(
            summary_markdown(summary),
            encoding="utf-8",
        )
    except OSError as error:
        raise ScopedReviewError("scoped_review_summary_write_failed") from error


def render_candidate_for_terminal(
    records: Iterable[dict[str, Any]],
    scope: Mapping[str, Any],
    candidate_id: str,
) -> str:
    """Показывает одну проверенную сцену только по явной локальной команде."""

    materialised = list(records)
    metadata = {item["id"]: item for item in validate_records(materialised, scope)}
    if candidate_id not in metadata:
        raise ScopedReviewError("scoped_review_candidate_not_in_queue")
    for record in materialised:
        if record.get("id") == candidate_id:
            item = metadata[candidate_id]
            lines = [
                f"ID: {item['id']}",
                f"Topic: {item['topic']}",
                f"Goal: {item['dialogue_goal']}",
                f"Tone: {item['emotional_tone']}",
                f"Full human review required: {item['requires_full_human_review']}",
                "",
            ]
            for turn in record["turns"]:
                role = "Пользователь" if turn["role"] == "user" else "ВатэсAi"
                lines.append(f"{role}: {turn['text']}")
            return "\n".join(lines)
    raise ScopedReviewError("scoped_review_candidate_not_in_queue")


def _parser() -> argparse.ArgumentParser:
    """Предоставляет явные операции queue, review, verdict и summary."""

    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--build-queue", action="store_true")
    actions.add_argument("--show-id", metavar="CANDIDATE_ID")
    actions.add_argument("--record-id", metavar="CANDIDATE_ID")
    actions.add_argument("--summary-out", type=Path)
    parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE_PATH)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE_PATH)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE_PATH)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS_PATH)
    parser.add_argument("--decision", choices=("approved", "rejected"))
    parser.add_argument("--reason-code", action="append", default=[])
    parser.add_argument("--reviewer-role", default="project_owner")
    return parser


def _configure_utf8_output() -> None:
    """Явно включает UTF-8 для Windows terminal before Russian display."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """Запускает только одну явно выбранную операцию local review workflow."""

    _configure_utf8_output()
    parser = _parser()
    args = parser.parse_args()
    try:
        if args.build_queue:
            queue = create_queue(args.scope, args.candidate, args.report_directory)
            print(
                json.dumps(
                    {
                        "scope_id": queue["scope_id"],
                        "candidate_records": queue["candidate_records"],
                        "metadata_only": queue["metadata_only"],
                        "training_started": queue["training_started"],
                    },
                    ensure_ascii=False,
                )
            )
            return
        queue = load_queue(args.queue, scope_path=args.scope, candidate_path=args.candidate)
        if args.show_id:
            scope = load_scope(args.scope)
            records, _ = load_candidate(args.candidate)
            _queue_item_by_id(queue, args.show_id)
            print(render_candidate_for_terminal(records, scope, args.show_id))
            return
        if args.record_id:
            if not args.decision or not args.reason_code:
                parser.error("--record-id требует --decision и хотя бы один --reason-code")
            record = append_decision(
                args.queue,
                args.decisions,
                candidate_id=args.record_id,
                decision=args.decision,
                reason_codes=args.reason_code,
                reviewer_role=args.reviewer_role,
                scope_path=args.scope,
                candidate_path=args.candidate,
            )
            print(json.dumps(record, ensure_ascii=False, sort_keys=True))
            return
        if args.decision or args.reason_code:
            parser.error("--decision и --reason-code допустимы только с --record-id")
        scope = load_scope(args.scope)
        decisions = load_decisions(args.decisions, queue)
        summary = build_summary(queue, decisions, scope)
        write_summary(args.summary_out, summary)
        print(
            json.dumps(
                {
                    "review_status": summary["review_status"],
                    "reviewed_records": summary["reviewed_records"],
                    "required_human_review_coverage_complete": summary[
                        "required_human_review_coverage_complete"
                    ],
                    "training_candidate_allowed": summary["training_candidate_allowed"],
                    "promotion_allowed": summary["promotion_allowed"],
                },
                ensure_ascii=False,
            )
        )
    except ScopedReviewError as error:
        raise SystemExit(f"Scoped candidate review остановлена: {error}") from error


if __name__ == "__main__":
    main()
