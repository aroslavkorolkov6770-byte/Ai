"""Метаданный audit разнообразия будущего диалогового корпуса Brain v3.

Этот модуль не генерирует реплики, не обучает модель и не изменяет active
manifest. Он повторно использует строгий audit корпуса, а затем читает только
безопасные метаданные уже принятой dialogue-записи: split, тему, цель, тон,
ожидаемые свойства ответа и число ходов. Тексты реплик в отчёт не попадают.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ai_core.brain_v3_corpus import (
    ALLOWED_RESPONSE_PROPERTIES,
    ALLOWED_SPLITS,
    DEFAULT_MANIFEST,
    BrainV3CorpusError,
    audit_corpus,
    file_sha256,
    load_corpus_manifest,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = ROOT / "data" / "brain_v3" / "dialogue_coverage_policy.v1.json"
FORMAT_VERSION = 1
MULTI_TURN_DIALOGUE_MINIMUM_TURNS = 4

_TAG = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_POLICY_FIELDS = {
    "format_version",
    "policy_id",
    "scope",
    "minimums",
    "required_topics",
    "required_dialogue_goals",
    "required_emotional_tones",
    "required_response_properties",
}
_SCOPE_FIELDS = {"language", "record_type", "layer"}
_MINIMUM_FIELDS = {
    "minimum_unique_dialogue_turns",
    "minimum_dialogue_records",
    "minimum_multi_turn_records",
    "minimum_multi_turn_ratio",
    "minimum_turns_by_split",
}


class DialogueCoverageError(ValueError):
    """Останавливает coverage audit до использования неполной policy или корпуса."""


@dataclass(frozen=True)
class DialogueCoveragePolicy:
    """Проверяемый контракт разнообразия для русских многоходовых диалогов."""

    policy_id: str
    minimum_unique_dialogue_turns: int
    minimum_dialogue_records: int
    minimum_multi_turn_records: int
    minimum_multi_turn_ratio: float
    minimum_turns_by_split: tuple[tuple[str, int], ...]
    required_topics: tuple[tuple[str, int], ...]
    required_dialogue_goals: tuple[tuple[str, int], ...]
    required_emotional_tones: tuple[tuple[str, int], ...]
    required_response_properties: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _DialogueMetadata:
    """Безопасная часть проверенной dialogue-записи без текстов ходов."""

    split: str
    topic: str
    dialogue_goal: str
    emotional_tone: str
    expected_response_properties: tuple[str, ...]
    turn_count: int


def _require_object(value: Any, code: str) -> dict[str, Any]:
    """Проверяет JSON object, не добавляя непроверенное содержимое в ошибку."""
    if not isinstance(value, dict):
        raise DialogueCoverageError(code)
    return value


def _require_tag(value: Any, code: str) -> str:
    """Принимает только короткий стабильный tag policy, а не свободный текст."""
    if not isinstance(value, str) or _TAG.fullmatch(value) is None:
        raise DialogueCoverageError(code)
    return value


def _require_positive_integer(value: Any, code: str) -> int:
    """Принимает положительный integer для минимального порога покрытия."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DialogueCoverageError(code)
    return value


def _require_ratio(value: Any, code: str) -> float:
    """Проверяет процент многоходовых записей без неявного значения по умолчанию."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DialogueCoverageError(code)
    ratio = float(value)
    if not 0 < ratio <= 1:
        raise DialogueCoverageError(code)
    return ratio


def _parse_requirement_map(
    value: Any,
    *,
    code: str,
    allowed_tags: frozenset[str] | None = None,
) -> tuple[tuple[str, int], ...]:
    """Разбирает контролируемую карту ``tag -> minimum dialogue turns``."""
    payload = _require_object(value, code)
    if not payload:
        raise DialogueCoverageError(code)
    requirements: list[tuple[str, int]] = []
    for raw_tag, raw_minimum in payload.items():
        tag = _require_tag(raw_tag, code)
        if allowed_tags is not None and tag not in allowed_tags:
            raise DialogueCoverageError(code)
        requirements.append((tag, _require_positive_integer(raw_minimum, code)))
    return tuple(sorted(requirements))


def _parse_split_minimums(value: Any) -> tuple[tuple[str, int], ...]:
    """Требует явный минимум для каждого закрытого split."""
    payload = _require_object(value, "invalid_minimum_turns_by_split")
    if set(payload) != set(ALLOWED_SPLITS):
        raise DialogueCoverageError("invalid_minimum_turns_by_split")
    return tuple(
        (split, _require_positive_integer(payload[split], "invalid_minimum_turns_by_split"))
        for split in sorted(ALLOWED_SPLITS)
    )


def load_dialogue_coverage_policy(path: Path = DEFAULT_POLICY) -> DialogueCoveragePolicy:
    """Загружает strict JSON policy покрытия без применения скрытых defaults."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Не найдена dialogue coverage policy: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise DialogueCoverageError("dialogue coverage policy содержит некорректный JSON") from error

    policy = _require_object(payload, "dialogue coverage policy должна быть JSON object")
    if set(policy) != _POLICY_FIELDS:
        raise DialogueCoverageError("dialogue coverage policy содержит недопустимые или отсутствующие поля")
    if policy["format_version"] != FORMAT_VERSION:
        raise DialogueCoverageError("неподдерживаемая версия dialogue coverage policy")
    policy_id = _require_tag(policy["policy_id"], "invalid_policy_id")

    scope = _require_object(policy["scope"], "invalid_policy_scope")
    if set(scope) != _SCOPE_FIELDS or scope != {
        "language": "ru",
        "record_type": "dialogue",
        "layer": "dialogue",
    }:
        raise DialogueCoverageError("invalid_policy_scope")

    minimums = _require_object(policy["minimums"], "invalid_policy_minimums")
    if set(minimums) != _MINIMUM_FIELDS:
        raise DialogueCoverageError("invalid_policy_minimums")
    minimum_unique_dialogue_turns = _require_positive_integer(
        minimums["minimum_unique_dialogue_turns"],
        "invalid_minimum_unique_dialogue_turns",
    )
    minimum_dialogue_records = _require_positive_integer(
        minimums["minimum_dialogue_records"],
        "invalid_minimum_dialogue_records",
    )
    minimum_multi_turn_records = _require_positive_integer(
        minimums["minimum_multi_turn_records"],
        "invalid_minimum_multi_turn_records",
    )
    minimum_multi_turn_ratio = _require_ratio(
        minimums["minimum_multi_turn_ratio"],
        "invalid_minimum_multi_turn_ratio",
    )
    minimum_turns_by_split = _parse_split_minimums(minimums["minimum_turns_by_split"])
    if sum(minimum for _, minimum in minimum_turns_by_split) > minimum_unique_dialogue_turns:
        raise DialogueCoverageError("minimum_turns_by_split_exceeds_total")
    if minimum_multi_turn_records > minimum_dialogue_records:
        raise DialogueCoverageError("minimum_multi_turn_records_exceeds_records")

    required_topics = _parse_requirement_map(policy["required_topics"], code="invalid_required_topics")
    required_dialogue_goals = _parse_requirement_map(
        policy["required_dialogue_goals"],
        code="invalid_required_dialogue_goals",
    )
    required_emotional_tones = _parse_requirement_map(
        policy["required_emotional_tones"],
        code="invalid_required_emotional_tones",
    )
    required_response_properties = _parse_requirement_map(
        policy["required_response_properties"],
        code="invalid_required_response_properties",
        allowed_tags=ALLOWED_RESPONSE_PROPERTIES,
    )
    for requirements in (
        required_topics,
        required_dialogue_goals,
        required_emotional_tones,
        required_response_properties,
    ):
        if any(minimum > minimum_unique_dialogue_turns for _, minimum in requirements):
            raise DialogueCoverageError("coverage_requirement_exceeds_total_turns")

    return DialogueCoveragePolicy(
        policy_id=policy_id,
        minimum_unique_dialogue_turns=minimum_unique_dialogue_turns,
        minimum_dialogue_records=minimum_dialogue_records,
        minimum_multi_turn_records=minimum_multi_turn_records,
        minimum_multi_turn_ratio=minimum_multi_turn_ratio,
        minimum_turns_by_split=minimum_turns_by_split,
        required_topics=required_topics,
        required_dialogue_goals=required_dialogue_goals,
        required_emotional_tones=required_emotional_tones,
        required_response_properties=required_response_properties,
    )


def _metadata_from_payload(payload: Any) -> _DialogueMetadata | None:
    """Извлекает только уже проверяемые audit-метаданные одной raw JSONL строки."""
    if not isinstance(payload, dict):
        raise DialogueCoverageError("coverage_metadata_record_not_object")
    if payload.get("record_type") != "dialogue":
        return None
    if payload.get("language") != "ru" or payload.get("layer") != "dialogue":
        raise DialogueCoverageError("coverage_metadata_scope_mismatch")
    split = payload.get("split")
    topic = payload.get("topic")
    dialogue_goal = payload.get("dialogue_goal")
    emotional_tone = payload.get("emotional_tone")
    properties = payload.get("expected_response_properties")
    turns = payload.get("turns")
    if (
        not isinstance(split, str)
        or split not in ALLOWED_SPLITS
        or not isinstance(topic, str)
        or not isinstance(dialogue_goal, str)
        or not isinstance(emotional_tone, str)
        or not isinstance(properties, list)
        or not all(isinstance(item, str) for item in properties)
        or not isinstance(turns, list)
    ):
        raise DialogueCoverageError("coverage_metadata_record_invalid")
    return _DialogueMetadata(
        split=split,
        topic=topic,
        dialogue_goal=dialogue_goal,
        emotional_tone=emotional_tone,
        expected_response_properties=tuple(properties),
        turn_count=len(turns),
    )


def _iter_dialogue_metadata(manifest_path: Path) -> Iterable[_DialogueMetadata]:
    """Потоково собирает metadata после чистого audit и двойной hash-проверки.

    Сначала ``audit_dialogue_coverage`` вызывает полный corpus audit. Этот
    проход читает тот же immutable raw JSONL ещё раз только для metadata. SHA-256
    до и после чтения защищает от подмены источника между двумя проходами.
    """
    manifest = load_corpus_manifest(manifest_path)
    for source in manifest.sources:
        before_checksum = file_sha256(source.raw_path)
        if before_checksum != source.raw_sha256:
            raise DialogueCoverageError("coverage_metadata_source_hash_mismatch")
        try:
            with source.raw_path.open("rb") as raw_source:
                for line_number, raw_line in enumerate(raw_source, start=1):
                    if not raw_line.strip():
                        continue
                    try:
                        payload = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise DialogueCoverageError(
                            f"coverage_metadata_source_invalid_json_line_{line_number}"
                        ) from error
                    metadata = _metadata_from_payload(payload)
                    if metadata is not None:
                        yield metadata
        except OSError as error:
            raise DialogueCoverageError("coverage_metadata_source_read_failed") from error
        after_checksum = file_sha256(source.raw_path)
        if after_checksum != before_checksum or after_checksum != source.raw_sha256:
            raise DialogueCoverageError("coverage_metadata_source_changed_during_read")


def _requirement_counts(requirements: tuple[tuple[str, int], ...]) -> Counter[str]:
    """Создаёт счётчик только для заранее объявленных tags policy."""
    return Counter({tag: 0 for tag, _ in requirements})


def _policy_requirements(policy: DialogueCoveragePolicy) -> dict[str, dict[str, int]]:
    """Переводит immutable policy в JSON-friendly проверяемые карты порогов."""
    return {
        "minimum_turns_by_split": dict(policy.minimum_turns_by_split),
        "required_topics": dict(policy.required_topics),
        "required_dialogue_goals": dict(policy.required_dialogue_goals),
        "required_emotional_tones": dict(policy.required_emotional_tones),
        "required_response_properties": dict(policy.required_response_properties),
    }


def _collect_coverage(policy: DialogueCoveragePolicy, manifest_path: Path) -> dict[str, Any]:
    """Считает покрытие без хранения или публикации текстов диалогов."""
    topic_turns = _requirement_counts(policy.required_topics)
    dialogue_goal_turns = _requirement_counts(policy.required_dialogue_goals)
    emotional_tone_turns = _requirement_counts(policy.required_emotional_tones)
    property_turns = _requirement_counts(policy.required_response_properties)
    turns_by_split: Counter[str] = Counter({split: 0 for split in ALLOWED_SPLITS})

    dialogue_records = 0
    dialogue_turns = 0
    multi_turn_dialogue_records = 0
    unmapped_topic_turns = 0
    unmapped_dialogue_goal_turns = 0
    unmapped_emotional_tone_turns = 0
    unmapped_response_property_turns = 0

    for metadata in _iter_dialogue_metadata(manifest_path):
        dialogue_records += 1
        dialogue_turns += metadata.turn_count
        turns_by_split[metadata.split] += metadata.turn_count
        if metadata.turn_count >= MULTI_TURN_DIALOGUE_MINIMUM_TURNS:
            multi_turn_dialogue_records += 1

        if metadata.topic in topic_turns:
            topic_turns[metadata.topic] += metadata.turn_count
        else:
            unmapped_topic_turns += metadata.turn_count
        if metadata.dialogue_goal in dialogue_goal_turns:
            dialogue_goal_turns[metadata.dialogue_goal] += metadata.turn_count
        else:
            unmapped_dialogue_goal_turns += metadata.turn_count
        if metadata.emotional_tone in emotional_tone_turns:
            emotional_tone_turns[metadata.emotional_tone] += metadata.turn_count
        else:
            unmapped_emotional_tone_turns += metadata.turn_count
        for property_name in metadata.expected_response_properties:
            if property_name in property_turns:
                property_turns[property_name] += metadata.turn_count
            else:
                unmapped_response_property_turns += metadata.turn_count

    multi_turn_dialogue_ratio = multi_turn_dialogue_records / dialogue_records if dialogue_records else 0.0
    return {
        "dialogue_records": dialogue_records,
        "dialogue_turns": dialogue_turns,
        "multi_turn_dialogue_records": multi_turn_dialogue_records,
        "multi_turn_dialogue_ratio": round(multi_turn_dialogue_ratio, 6),
        "dialogue_turns_by_split": dict(sorted(turns_by_split.items())),
        "topic_turns": dict(sorted(topic_turns.items())),
        "dialogue_goal_turns": dict(sorted(dialogue_goal_turns.items())),
        "emotional_tone_turns": dict(sorted(emotional_tone_turns.items())),
        "response_property_turns": dict(sorted(property_turns.items())),
        "unmapped_topic_turns": unmapped_topic_turns,
        "unmapped_dialogue_goal_turns": unmapped_dialogue_goal_turns,
        "unmapped_emotional_tone_turns": unmapped_emotional_tone_turns,
        "unmapped_response_property_turns": unmapped_response_property_turns,
    }


def _coverage_block_reasons(policy: DialogueCoveragePolicy, observed: dict[str, Any]) -> list[str]:
    """Создаёт стабильные коды дефицитов по policy, не раскрывая source tags."""
    reasons: list[str] = []
    if observed["dialogue_turns"] < policy.minimum_unique_dialogue_turns:
        reasons.append("insufficient_unique_dialogue_turns")
    if observed["dialogue_records"] < policy.minimum_dialogue_records:
        reasons.append("insufficient_dialogue_records")
    if observed["multi_turn_dialogue_records"] < policy.minimum_multi_turn_records:
        reasons.append("insufficient_multi_turn_dialogue_records")
    if observed["multi_turn_dialogue_ratio"] < policy.minimum_multi_turn_ratio:
        reasons.append("insufficient_multi_turn_dialogue_ratio")

    requirements = _policy_requirements(policy)
    for split, minimum in requirements["minimum_turns_by_split"].items():
        if observed["dialogue_turns_by_split"][split] < minimum:
            reasons.append(f"insufficient_split_turns:{split}")
    for label, minimum in requirements["required_topics"].items():
        if observed["topic_turns"][label] < minimum:
            reasons.append(f"insufficient_topic_coverage:{label}")
    for label, minimum in requirements["required_dialogue_goals"].items():
        if observed["dialogue_goal_turns"][label] < minimum:
            reasons.append(f"insufficient_dialogue_goal_coverage:{label}")
    for label, minimum in requirements["required_emotional_tones"].items():
        if observed["emotional_tone_turns"][label] < minimum:
            reasons.append(f"insufficient_emotional_tone_coverage:{label}")
    for label, minimum in requirements["required_response_properties"].items():
        if observed["response_property_turns"][label] < minimum:
            reasons.append(f"insufficient_response_property_coverage:{label}")
    return reasons


def _safe_base_audit_summary(base_audit: dict[str, Any]) -> dict[str, Any]:
    """Оставляет в coverage report только уже безопасные числовые метрики audit."""
    keys = (
        "records_total",
        "records_accepted",
        "records_rejected",
        "unique_dialogue_turns",
        "language_characters",
        "cross_split_exact_duplicates",
        "cross_split_near_duplicate_candidates",
        "training_allowed",
        "training_block_reasons",
    )
    return {key: base_audit[key] for key in keys}


def _validated_reused_base_audit(value: Mapping[str, Any]) -> dict[str, Any]:
    """Проверяет безопасный результат audit для одного общего staging-прогона.

    Повторное использование разрешено только вызывающему коду, который получил
    этот audit в том же неизменённом temporary manifest. Это убирает второй
    дорогостоящий schema/duplicate pass, но не отменяет проверку checksum
    manifest и всех raw source во время metadata-прохода ниже.
    """

    required_integer_keys = (
        "format_version",
        "records_total",
        "records_accepted",
        "records_rejected",
        "unique_dialogue_turns",
        "language_characters",
        "cross_split_exact_duplicates",
        "cross_split_near_duplicate_candidates",
    )
    if not isinstance(value, Mapping):
        raise DialogueCoverageError("invalid_reused_base_audit")
    if any(
        not isinstance(value.get(key), int) or isinstance(value.get(key), bool) or value[key] < 0
        for key in required_integer_keys
    ):
        raise DialogueCoverageError("invalid_reused_base_audit")
    if value["format_version"] != FORMAT_VERSION:
        raise DialogueCoverageError("invalid_reused_base_audit")
    if value["records_total"] != value["records_accepted"] + value["records_rejected"]:
        raise DialogueCoverageError("invalid_reused_base_audit")
    if (
        value["cross_split_exact_duplicates"] > value["records_rejected"]
        or value["cross_split_near_duplicate_candidates"] > value["records_rejected"]
    ):
        raise DialogueCoverageError("invalid_reused_base_audit")
    if not isinstance(value.get("training_allowed"), bool):
        raise DialogueCoverageError("invalid_reused_base_audit")
    reasons = value.get("training_block_reasons")
    if (
        not isinstance(reasons, list)
        or not all(isinstance(reason, str) and reason for reason in reasons)
        or value["training_allowed"] == bool(reasons)
    ):
        raise DialogueCoverageError("invalid_reused_base_audit")
    return dict(value)


def audit_dialogue_coverage(
    manifest_path: Path = DEFAULT_MANIFEST,
    policy_path: Path = DEFAULT_POLICY,
    *,
    verified_base_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Проверяет, что чистый corpus не только большой, но и разнообразный.

    ``coverage_allowed`` относится лишь к многоходовому dialogue-слою.
    ``generator_training_ready`` становится true только если одновременно
    пройдены основной corpus quality gate и эта policy. Функция ничего не
    обучает и не активирует. ``verified_base_audit`` допустим только для
    единого read-only staging-прогона, где audit был получен из этого же
    неизменённого temporary manifest непосредственно перед coverage.
    """
    policy = load_dialogue_coverage_policy(policy_path)
    resolved_manifest = manifest_path.expanduser().resolve()
    if not resolved_manifest.is_file():
        raise FileNotFoundError(f"Не найден manifest для dialogue coverage audit: {resolved_manifest}")
    manifest_checksum_before = file_sha256(resolved_manifest)
    if verified_base_audit is None:
        try:
            base_audit = audit_corpus(resolved_manifest)
        except BrainV3CorpusError as error:
            raise DialogueCoverageError("base_corpus_audit_failed") from error
    else:
        base_audit = _validated_reused_base_audit(verified_base_audit)
    if file_sha256(resolved_manifest) != manifest_checksum_before:
        raise DialogueCoverageError("manifest_changed_during_base_audit")

    base_is_clean = (
        base_audit["records_rejected"] == 0
        and base_audit["cross_split_exact_duplicates"] == 0
        and base_audit["cross_split_near_duplicate_candidates"] == 0
    )
    if base_is_clean:
        observed = _collect_coverage(policy, resolved_manifest)
        if file_sha256(resolved_manifest) != manifest_checksum_before:
            raise DialogueCoverageError("manifest_changed_during_coverage_audit")
        coverage_reasons = _coverage_block_reasons(policy, observed)
        coverage_observed = True
    else:
        observed = {
            "dialogue_records": 0,
            "dialogue_turns": 0,
            "multi_turn_dialogue_records": 0,
            "multi_turn_dialogue_ratio": 0.0,
            "dialogue_turns_by_split": {split: 0 for split in sorted(ALLOWED_SPLITS)},
            "topic_turns": {tag: 0 for tag, _ in policy.required_topics},
            "dialogue_goal_turns": {tag: 0 for tag, _ in policy.required_dialogue_goals},
            "emotional_tone_turns": {tag: 0 for tag, _ in policy.required_emotional_tones},
            "response_property_turns": {tag: 0 for tag, _ in policy.required_response_properties},
            "unmapped_topic_turns": 0,
            "unmapped_dialogue_goal_turns": 0,
            "unmapped_emotional_tone_turns": 0,
            "unmapped_response_property_turns": 0,
        }
        coverage_reasons = ["base_corpus_not_clean"]
        coverage_observed = False

    generator_training_block_reasons = [
        *(f"corpus:{reason}" for reason in base_audit["training_block_reasons"]),
        *(f"dialogue_coverage:{reason}" for reason in coverage_reasons),
    ]
    coverage_allowed = not coverage_reasons
    return {
        "format_version": FORMAT_VERSION,
        "policy_id": policy.policy_id,
        "scope": {"language": "ru", "record_type": "dialogue", "layer": "dialogue"},
        "base_corpus_audit": _safe_base_audit_summary(base_audit),
        "coverage_observed": coverage_observed,
        "requirements": {
            "minimum_unique_dialogue_turns": policy.minimum_unique_dialogue_turns,
            "minimum_dialogue_records": policy.minimum_dialogue_records,
            "minimum_multi_turn_records": policy.minimum_multi_turn_records,
            "minimum_multi_turn_ratio": policy.minimum_multi_turn_ratio,
            **_policy_requirements(policy),
        },
        "observed": observed,
        "coverage_allowed": coverage_allowed,
        "coverage_block_reasons": coverage_reasons,
        "generator_training_ready": base_audit["training_allowed"] and coverage_allowed,
        "generator_training_block_reasons": generator_training_block_reasons,
        "training_started": False,
        "active_manifest_updated": False,
    }


def _markdown_requirements(
    title: str,
    requirements: dict[str, int],
    observed: dict[str, int],
) -> list[str]:
    """Формирует Markdown только из declared policy tags и чисел audit."""
    lines = [f"### {title}", ""]
    for label, minimum in requirements.items():
        lines.append(f"- `{label}`: {observed[label]}/{minimum} ходов.")
    lines.append("")
    return lines


def summary_markdown(report: dict[str, Any]) -> str:
    """Формирует читаемый coverage report без текстов или свободных source tags."""
    requirements = report["requirements"]
    observed = report["observed"]
    base = report["base_corpus_audit"]
    coverage_status = "разрешено" if report["coverage_allowed"] else "заблокировано"
    generator_status = "готов" if report["generator_training_ready"] else "не готов"
    coverage_reasons = ", ".join(report["coverage_block_reasons"]) or "нет"
    generator_reasons = ", ".join(report["generator_training_block_reasons"]) or "нет"
    lines = [
        "# Brain v3 — аудит разнообразия dialogue-корпуса",
        "",
        f"Policy: `{report['policy_id']}`.",
        "Проверка работает только с metadata чистого корпуса; реплики и свободные теги источников не выводятся.",
        "",
        "## Базовая чистота корпуса",
        "",
        f"Записей: {base['records_total']}; принято: {base['records_accepted']}; отклонено: {base['records_rejected']}.",
        f"Основной corpus gate: `{base['training_allowed']}`; причины: {', '.join(base['training_block_reasons']) or 'нет'}.",
        "",
        "## Наблюдаемое покрытие",
        "",
        f"Dialogue-записей: {observed['dialogue_records']}/{requirements['minimum_dialogue_records']}.",
        f"Dialogue-ходов: {observed['dialogue_turns']}/{requirements['minimum_unique_dialogue_turns']}.",
        f"Многоходовых записей (от {MULTI_TURN_DIALOGUE_MINIMUM_TURNS} ходов): {observed['multi_turn_dialogue_records']}/{requirements['minimum_multi_turn_records']}.",
        f"Доля многоходовых записей: {observed['multi_turn_dialogue_ratio']:.3f}/{requirements['minimum_multi_turn_ratio']:.3f}.",
        f"Неотображённых в policy topic/goal/tone/property ходов: {observed['unmapped_topic_turns']}/{observed['unmapped_dialogue_goal_turns']}/{observed['unmapped_emotional_tone_turns']}/{observed['unmapped_response_property_turns']}.",
        "",
    ]
    lines.extend(
        _markdown_requirements(
            "Split",
            requirements["minimum_turns_by_split"],
            observed["dialogue_turns_by_split"],
        )
    )
    lines.extend(
        _markdown_requirements(
            "Темы",
            requirements["required_topics"],
            observed["topic_turns"],
        )
    )
    lines.extend(
        _markdown_requirements(
            "Цели диалога",
            requirements["required_dialogue_goals"],
            observed["dialogue_goal_turns"],
        )
    )
    lines.extend(
        _markdown_requirements(
            "Эмоциональные тона",
            requirements["required_emotional_tones"],
            observed["emotional_tone_turns"],
        )
    )
    lines.extend(
        _markdown_requirements(
            "Свойства ответа",
            requirements["required_response_properties"],
            observed["response_property_turns"],
        )
    )
    lines.extend(
        [
            "## Решение",
            "",
            f"Допуск dialogue-слоя по policy: **{coverage_status}**.",
            f"Причины: {coverage_reasons}.",
            f"Готовность запустить генеративное обучение: **{generator_status}**.",
            f"Общие причины блокировки: {generator_reasons}.",
            "",
            "Audit не создаёт данные, не обучает модель, не меняет active manifest и не показывает тексты диалогов.",
            "",
        ]
    )
    return "\n".join(lines)
