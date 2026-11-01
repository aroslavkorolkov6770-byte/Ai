"""Создаёт metadata-only очередь ручной проверки первого manual candidate Brain v3.

Этот модуль обслуживает только маленькую review-only партию. Он не меняет
active manifest, staging corpus, API, веса или обучение. Тексты сцен читаются
локально для валидации и, по явной команде ``--show-id``, для ручной проверки в
терминале; в отчёты попадают только ID, hashes и semantic metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ai_core.brain_v3_corpus import (
    ALLOWED_RESPONSE_PROPERTIES,
    NearDuplicateIndex,
    duplicate_signature,
    text_risk_code,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = PROJECT_ROOT / "data" / "brain_v3" / "reviews"
RESULTS_ROOT = PROJECT_ROOT / "evaluation" / "results"

SCOPE_ID = "brain-v3-owned-manual-candidate-v1"
QUEUE_MODE = "brain_v3_owned_manual_review_only_queue"
DEFAULT_SCOPE_PATH = REVIEW_ROOT / f"{SCOPE_ID}.scope.json"
DEFAULT_CANDIDATE_PATH = REVIEW_ROOT / f"{SCOPE_ID}.review.jsonl"
DEFAULT_REPORT_DIRECTORY = (
    RESULTS_ROOT / "brain-v3-owned-manual-candidate-v1-review-queue-v0.2"
)

FORMAT_VERSION = 1
TURN_ROLES = ("user", "assistant", "user", "assistant")
HIGH_RISK_TOPICS = frozenset(
    {"emotional_support", "privacy_and_memory", "safety_boundaries"}
)
GOAL_BY_TOPIC = {
    "casual_dialogue": "casual_connection",
    "emotional_support": "emotional_support",
    "correction": "acknowledge_and_correct",
    "uncertainty_and_clarification": "clarify_missing_context",
    "privacy_and_memory": "respect_privacy",
    "safety_boundaries": "safe_boundary",
}
RECORD_IDENTIFIER = re.compile(r"^brain-v3-owned-manual-v1-[0-9]{3}$")
RAW_CONTENT_KEYS = frozenset({"text", "turns", "conversation", "message"})


class ManualCandidateReviewError(ValueError):
    """Останавливает workflow коротким кодом до появления review-артефакта."""


def _safe_path_inside(path: Path, root: Path, error_code: str) -> Path:
    """Не допускает чтение и запись за пределами локальных review/results root."""

    resolved_path = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ManualCandidateReviewError(error_code) from error
    return resolved_path


def _read_json_object(path: Path, error_code: str) -> dict[str, Any]:
    """Читает JSON object и не печатает данные source при ошибке."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManualCandidateReviewError(error_code) from error
    if not isinstance(value, dict):
        raise ManualCandidateReviewError(error_code)
    return value


def _read_jsonl(path: Path, error_code: str) -> tuple[list[dict[str, Any]], str]:
    """Читает local JSONL как данные, не исполняя содержимое реплик."""

    try:
        raw = path.read_bytes()
        records = [
            json.loads(line)
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManualCandidateReviewError(error_code) from error
    if not records or not all(isinstance(record, dict) for record in records):
        raise ManualCandidateReviewError(error_code)
    return records, hashlib.sha256(raw).hexdigest()


def _canonical_json_sha256(value: Mapping[str, Any]) -> str:
    """Возвращает стабильный hash одной неизменённой review-only записи."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalise_text(value: str) -> str:
    """Нормализует только для exact-duplicate check, без записи текста в отчёт."""

    return " ".join(value.casefold().split())


def _contains_raw_turn_content(value: Any) -> bool:
    """Проверяет, что queue/report не получили исходные реплики по ошибке."""

    if isinstance(value, Mapping):
        if RAW_CONTENT_KEYS.intersection(value):
            return True
        return any(_contains_raw_turn_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_raw_turn_content(item) for item in value)
    return False


def load_scope(
    scope_path: Path = DEFAULT_SCOPE_PATH,
    *,
    review_root: Path = REVIEW_ROOT,
) -> dict[str, Any]:
    """Загружает и проверяет только policy scope первого review-only tranche."""

    safe_scope_path = _safe_path_inside(
        scope_path,
        review_root,
        "manual_candidate_scope_outside_review_root",
    )
    if not safe_scope_path.is_file():
        raise ManualCandidateReviewError("manual_candidate_scope_missing")
    scope = _read_json_object(safe_scope_path, "manual_candidate_scope_invalid")
    if (
        scope.get("format_version") != FORMAT_VERSION
        or scope.get("scope_id") != SCOPE_ID
        or scope.get("status") not in {
            "awaiting_human_review",
            "human_review_in_progress",
            "human_review_coverage_complete",
        }
        or scope.get("candidate_is_active") is not False
        or scope.get("training_eligible") is not False
    ):
        raise ManualCandidateReviewError("manual_candidate_scope_invalid")

    source = scope.get("source")
    if not isinstance(source, Mapping) or source != {
        "source_id": SCOPE_ID,
        "source_kind": "owned",
        "license": "OWNED_BY_PROJECT",
        "authoring_method": "manual_original_only",
        "external_text_allowed": False,
        "personal_chat_allowed": False,
        "template_generator_allowed": False,
    }:
        raise ManualCandidateReviewError("manual_candidate_scope_source_invalid")

    tranche = scope.get("first_review_tranche")
    if not isinstance(tranche, Mapping):
        raise ManualCandidateReviewError("manual_candidate_scope_tranche_invalid")
    expected_topics = tranche.get("topics")
    if (
        tranche.get("records") != 24
        or tranche.get("turns_per_record") != len(TURN_ROLES)
        or expected_topics != {topic: 4 for topic in GOAL_BY_TOPIC}
        or set(tranche.get("required_dialogue_goals", [])) != set(GOAL_BY_TOPIC.values())
        or not isinstance(tranche.get("required_tones"), list)
        or not isinstance(tranche.get("required_properties"), list)
    ):
        raise ManualCandidateReviewError("manual_candidate_scope_tranche_invalid")
    if not set(tranche["required_properties"]) <= ALLOWED_RESPONSE_PROPERTIES:
        raise ManualCandidateReviewError("manual_candidate_scope_properties_invalid")

    split_policy = scope.get("split_family_policy")
    if (
        not isinstance(split_policy, Mapping)
        or split_policy.get("initial_review_tranche_split") != "review_only"
        or split_policy.get("family_must_stay_in_one_split") is not True
        or split_policy.get("family_ledger_required_before_staging") is not True
    ):
        raise ManualCandidateReviewError("manual_candidate_scope_split_policy_invalid")

    review = scope.get("review")
    required_checks = {
        "provenance_and_no_personal_data",
        "whole_dialogue_coherence",
        "natural_non_template_tone",
        "metadata_and_route_match",
    }
    if (
        not isinstance(review, Mapping)
        or review.get("human_reviewer_required") is not True
        or review.get("high_risk_records_require_full_review") is not True
        or review.get("required_checks") != sorted(required_checks)
        and set(review.get("required_checks", [])) != required_checks
    ):
        raise ManualCandidateReviewError("manual_candidate_scope_review_invalid")
    return scope


def load_review_candidate(
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    *,
    review_root: Path = REVIEW_ROOT,
) -> tuple[list[dict[str, Any]], str]:
    """Загружает review-only JSONL; active corpus и staging намеренно недоступны."""

    safe_candidate_path = _safe_path_inside(
        candidate_path,
        review_root,
        "manual_candidate_path_outside_review_root",
    )
    if safe_candidate_path.suffix.lower() != ".jsonl" or not safe_candidate_path.is_file():
        raise ManualCandidateReviewError("manual_candidate_missing")
    return _read_jsonl(safe_candidate_path, "manual_candidate_invalid_jsonl")


def _validate_source(value: Any) -> dict[str, Any]:
    """Подтверждает, что draft имеет только разрешённое project-owned происхождение."""

    expected = {
        "source_id": SCOPE_ID,
        "source_kind": "owned",
        "license": "OWNED_BY_PROJECT",
        "authoring_method": "manual_original_only",
        "draft_author_role": "project_agent",
        "external_text_used": False,
        "personal_chat_used": False,
        "template_generator_used": False,
    }
    if not isinstance(value, dict) or value != expected:
        raise ManualCandidateReviewError("manual_candidate_source_invalid")
    return value


def _validate_scenario(
    value: Any,
    *,
    identifier: str,
    scope: Mapping[str, Any],
) -> dict[str, Any]:
    """Проверяет labels и обязательный full review для высокорисковых тем."""

    if not isinstance(value, dict) or set(value) != {
        "family_id",
        "topic",
        "dialogue_goal",
        "emotional_tone",
        "expected_response_properties",
        "requires_full_human_review",
    }:
        raise ManualCandidateReviewError("manual_candidate_scenario_invalid")
    topic = value.get("topic")
    goal = value.get("dialogue_goal")
    tone = value.get("emotional_tone")
    properties = value.get("expected_response_properties")
    required_tones = set(scope["first_review_tranche"]["required_tones"])
    if (
        value.get("family_id") != identifier
        or topic not in GOAL_BY_TOPIC
        or goal != GOAL_BY_TOPIC[topic]
        or not isinstance(tone, str)
        or tone not in required_tones
        or not isinstance(properties, list)
        or not properties
        or not all(isinstance(item, str) for item in properties)
        or len(properties) != len(set(properties))
        or not set(properties) <= ALLOWED_RESPONSE_PROPERTIES
        or value.get("requires_full_human_review") is not (topic in HIGH_RISK_TOPICS)
    ):
        raise ManualCandidateReviewError("manual_candidate_scenario_invalid")
    return value


def _validate_turns(value: Any, *, expected_turn_count: int) -> list[dict[str, str]]:
    """Проверяет четыре непустых безопасных реплики с фиксированным чередованием."""

    if not isinstance(value, list) or len(value) != expected_turn_count:
        raise ManualCandidateReviewError("manual_candidate_turn_count_invalid")
    validated: list[dict[str, str]] = []
    for index, turn in enumerate(value):
        if not isinstance(turn, dict) or set(turn) != {"role", "text"}:
            raise ManualCandidateReviewError("manual_candidate_turn_invalid")
        role = turn.get("role")
        text = turn.get("text")
        if role != TURN_ROLES[index] or not isinstance(text, str) or not text.strip():
            raise ManualCandidateReviewError("manual_candidate_turn_invalid")
        if text_risk_code(text, maximum=1_000) is not None:
            raise ManualCandidateReviewError("manual_candidate_turn_risk_detected")
        validated.append({"role": role, "text": text})
    return validated


def validate_review_records(
    records: Iterable[dict[str, Any]],
    scope: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Проверяет весь tranche и возвращает record metadata только для очереди."""

    materialised = list(records)
    tranche = scope["first_review_tranche"]
    if len(materialised) != tranche["records"]:
        raise ManualCandidateReviewError("manual_candidate_record_count_invalid")

    identifiers: set[str] = set()
    family_identifiers: set[str] = set()
    exact_dialogues: set[str] = set()
    near_duplicate_index = NearDuplicateIndex()
    topic_counts: Counter[str] = Counter()
    goals: set[str] = set()
    tones: set[str] = set()
    properties_covered: set[str] = set()
    metadata: list[dict[str, Any]] = []

    for record in materialised:
        if not isinstance(record, dict) or set(record) != {
            "format_version",
            "id",
            "review_status",
            "review_only",
            "source",
            "scenario",
            "turns",
        }:
            raise ManualCandidateReviewError("manual_candidate_record_invalid")
        identifier = record.get("id")
        if not isinstance(identifier, str) or not RECORD_IDENTIFIER.fullmatch(identifier):
            raise ManualCandidateReviewError("manual_candidate_identifier_invalid")
        if identifier in identifiers:
            raise ManualCandidateReviewError("manual_candidate_duplicate_identifier")
        if (
            record.get("format_version") != FORMAT_VERSION
            or record.get("review_status") != "unreviewed"
            or record.get("review_only") is not True
        ):
            raise ManualCandidateReviewError("manual_candidate_record_invalid")
        _validate_source(record.get("source"))
        scenario = _validate_scenario(record.get("scenario"), identifier=identifier, scope=scope)
        turns = _validate_turns(
            record.get("turns"),
            expected_turn_count=tranche["turns_per_record"],
        )

        normalised_dialogue = "\n".join(_normalise_text(turn["text"]) for turn in turns)
        if normalised_dialogue in exact_dialogues:
            raise ManualCandidateReviewError("manual_candidate_exact_duplicate")
        near_duplicate = near_duplicate_index.find(duplicate_signature(normalised_dialogue))
        if near_duplicate is not None:
            raise ManualCandidateReviewError("manual_candidate_near_duplicate")
        exact_dialogues.add(normalised_dialogue)
        near_duplicate_index.add(identifier, duplicate_signature(normalised_dialogue))

        family_id = scenario["family_id"]
        if family_id in family_identifiers:
            raise ManualCandidateReviewError("manual_candidate_duplicate_family")
        identifiers.add(identifier)
        family_identifiers.add(family_id)
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

    expected_topic_counts = tranche["topics"]
    if dict(sorted(topic_counts.items())) != dict(sorted(expected_topic_counts.items())):
        raise ManualCandidateReviewError("manual_candidate_topic_coverage_invalid")
    if goals != set(tranche["required_dialogue_goals"]):
        raise ManualCandidateReviewError("manual_candidate_goal_coverage_invalid")
    if not set(tranche["required_tones"]) <= tones:
        raise ManualCandidateReviewError("manual_candidate_tone_coverage_invalid")
    if not set(tranche["required_properties"]) <= properties_covered:
        raise ManualCandidateReviewError("manual_candidate_property_coverage_invalid")
    return sorted(metadata, key=lambda item: item["id"])


def _coverage_snapshot(items: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Строит count metadata без добавления фраз диалогов в результат."""

    topics: Counter[str] = Counter()
    goals: Counter[str] = Counter()
    tones: Counter[str] = Counter()
    properties: Counter[str] = Counter()
    turns: Counter[str] = Counter()
    full_review: Counter[str] = Counter()
    for item in items:
        topics[str(item["topic"])] += 1
        goals[str(item["dialogue_goal"])] += 1
        tones[str(item["emotional_tone"])] += 1
        properties.update(item["expected_response_properties"])
        turns[str(item["turn_count"])] += 1
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
        "turn_counts": dict(sorted(turns.items())),
        "human_review_scope": dict(sorted(full_review.items())),
    }


def build_review_queue(
    records: Iterable[dict[str, Any]],
    scope: Mapping[str, Any],
    *,
    candidate_path: Path,
    candidate_sha256: str,
) -> dict[str, Any]:
    """Создаёт immutable metadata-only queue без promotion или обучения."""

    items = validate_review_records(records, scope)
    report = {
        "format_version": FORMAT_VERSION,
        "mode": QUEUE_MODE,
        "scope_id": SCOPE_ID,
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
    if _contains_raw_turn_content(report):
        raise ManualCandidateReviewError("manual_candidate_queue_not_metadata_only")
    return report


def review_queue_markdown(report: Mapping[str, Any]) -> str:
    """Рендерит отчёт без реплик, скрытых prompt и персональных данных."""

    coverage = report["coverage"]
    return "\n".join(
        [
            "# Brain v3 — first manual review-only candidate: queue",
            "",
            "## Статус",
            "",
            "- Статус: `unreviewed`.",
            f"- В очереди: {report['candidate_records']} оригинальные четырёхходовые сцены.",
            "- Отчёт содержит только IDs, SHA-256 и semantic metadata; тексты реплик не копируются.",
            "- Active manifest, staging corpus, API, веса и обучение не менялись.",
            "",
            "## Покрытие",
            "",
            f"- Темы: {coverage['topics']}.",
            f"- Dialogue goals: {coverage['dialogue_goals']}.",
            f"- Тоны: {coverage['emotional_tones']}.",
            f"- Response properties: {coverage['response_properties']}.",
            f"- Полная ручная проверка / пул для стратифицированной выборки: {coverage['human_review_scope']}.",
            "",
            "## Что должен сделать reviewer",
            "",
            "Проверить все 12 high-risk сцен и минимум 2 стратифицированно выбранные остальные сцены: происхождение и отсутствие личных данных, связность всех четырёх ходов, естественный нетемплейтный тон и совпадение metadata с реальным смыслом. Текст одной сцены можно вывести только локально по её ID через `--show-id`; положительный verdict не создаёт training candidate автоматически.",
            "",
        ]
    )


def create_review_queue(
    scope_path: Path = DEFAULT_SCOPE_PATH,
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    report_directory: Path = DEFAULT_REPORT_DIRECTORY,
    *,
    review_root: Path = REVIEW_ROOT,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    """Пишет новую версионную metadata-only очередь, не перезаписывая историю."""

    scope = load_scope(scope_path, review_root=review_root)
    records, candidate_sha256 = load_review_candidate(
        candidate_path,
        review_root=review_root,
    )
    report = build_review_queue(
        records,
        scope,
        candidate_path=candidate_path,
        candidate_sha256=candidate_sha256,
    )
    destination = _safe_path_inside(
        report_directory,
        results_root,
        "manual_candidate_report_outside_results_root",
    )
    if destination.exists():
        raise ManualCandidateReviewError("manual_candidate_report_already_exists")
    destination.mkdir(parents=True)
    (destination / "brain_v3_manual_candidate_review_queue.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (destination / "BRAIN_V3_MANUAL_CANDIDATE_REVIEW_QUEUE.md").write_text(
        review_queue_markdown(report),
        encoding="utf-8",
    )
    return report


def render_candidate_for_terminal(
    records: Iterable[dict[str, Any]],
    scope: Mapping[str, Any],
    candidate_id: str,
) -> str:
    """Показывает одну сцену только по явному локальному запросу reviewer."""

    metadata = {item["id"]: item for item in validate_review_records(records, scope)}
    if candidate_id not in metadata:
        raise ManualCandidateReviewError("manual_candidate_id_not_in_queue")
    for record in records:
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
    raise ManualCandidateReviewError("manual_candidate_id_not_in_queue")


def _parser() -> argparse.ArgumentParser:
    """Создаёт явный CLI без действий по умолчанию вне review workflow."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE_PATH)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE_PATH)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    parser.add_argument(
        "--show-id",
        help="Показать одну review-only сцену в локальном терминале без записи в отчёт.",
    )
    return parser


def main() -> None:
    """Создаёт очередь либо показывает одну сцену только при явной команде."""

    args = _parser().parse_args()
    try:
        if args.show_id:
            scope = load_scope(args.scope)
            records, _ = load_review_candidate(args.candidate)
            print(render_candidate_for_terminal(records, scope, args.show_id))
            return
        report = create_review_queue(
            args.scope,
            args.candidate,
            args.report_directory,
        )
    except (ManualCandidateReviewError, OSError, ValueError) as error:
        raise SystemExit(f"Manual candidate review queue остановлена: {error}") from error
    print(
        json.dumps(
            {
                "candidate_records": report["candidate_records"],
                "candidate_sha256": report["candidate_sha256"],
                "review_status": report["review_status"],
                "metadata_only": report["metadata_only"],
                "candidate_is_active": report["candidate_is_active"],
                "training_started": report["training_started"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
