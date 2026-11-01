"""Строит metadata-only план авторских диалогов для Brain v3.

Модуль не создаёт реплики, не скачивает датасеты и не запускает обучение.
Его задача скромнее и важнее: превратить уже проверенный coverage report в
проверяемые числовые квоты для следующего собственного или лицензированного
dialogue-кандидата. В результат не копируются тексты, свободные source-теги,
идентификаторы диалогов или персональные данные.
"""

from __future__ import annotations

import math
from itertools import product
from typing import Any

from ai_core.brain_v3_dialogue_coverage import DialogueCoveragePolicy


AUTHORING_BLUEPRINT_FORMAT_VERSION = 1
AUTHORING_BLUEPRINT_ID = "brain_v3_dialogue_authoring_blueprint_v1"
SPLIT_ORDER = ("train", "validation", "test")
MAX_SPLIT_STRUCTURE_ATTEMPTS = 10_000
PREFERRED_DIALOGUE_TURNS_PER_RECORD = 4
DIALOGUE_TURN_STEP = 2


class DialogueAuthoringError(ValueError):
    """Останавливает построение плана до создания непроверяемой квоты."""


def _require_object(value: Any, code: str) -> dict[str, Any]:
    """Принимает только JSON object и не выводит непроверенное содержимое."""
    if not isinstance(value, dict):
        raise DialogueAuthoringError(code)
    return value


def _require_non_negative_integer(value: Any, code: str) -> int:
    """Проверяет счётчик без скрытого приведения строк, bool или float."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DialogueAuthoringError(code)
    return value


def _require_positive_integer(value: Any, code: str) -> int:
    """Проверяет положительный policy-порог."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DialogueAuthoringError(code)
    return value


def _require_count_map(
    value: Any,
    labels: tuple[str, ...],
    code: str,
) -> dict[str, int]:
    """Требует полный безопасный набор числовых метрик для известных labels."""
    payload = _require_object(value, code)
    if set(payload) != set(labels):
        raise DialogueAuthoringError(code)
    return {label: _require_non_negative_integer(payload[label], code) for label in labels}


def _policy_requirement_payload(policy: DialogueCoveragePolicy) -> dict[str, Any]:
    """Переводит immutable policy в точный JSON-совместимый контракт отчёта."""
    return {
        "minimum_unique_dialogue_turns": policy.minimum_unique_dialogue_turns,
        "minimum_dialogue_records": policy.minimum_dialogue_records,
        "minimum_multi_turn_records": policy.minimum_multi_turn_records,
        "minimum_multi_turn_ratio": policy.minimum_multi_turn_ratio,
        "minimum_turns_by_split": dict(policy.minimum_turns_by_split),
        "required_topics": dict(policy.required_topics),
        "required_dialogue_goals": dict(policy.required_dialogue_goals),
        "required_emotional_tones": dict(policy.required_emotional_tones),
        "required_response_properties": dict(policy.required_response_properties),
    }


def _safe_coverage_state(
    coverage_report: dict[str, Any],
    policy: DialogueCoveragePolicy,
) -> dict[str, Any]:
    """Читает только проверенные числовые поля coverage report.

    План намеренно не сохраняет весь входной JSON. Так дополнительное поле,
    случайно добавленное внешним инструментом, не сможет попасть в blueprint
    или Markdown-отчёт.
    """
    if coverage_report.get("format_version") != 1:
        raise DialogueAuthoringError("unsupported_coverage_report_format")
    if coverage_report.get("policy_id") != policy.policy_id:
        raise DialogueAuthoringError("coverage_report_policy_mismatch")
    if coverage_report.get("coverage_observed") is not True:
        raise DialogueAuthoringError("coverage_report_not_clean")

    requirements = _require_object(
        coverage_report.get("requirements"),
        "coverage_report_requirements_invalid",
    )
    expected_requirements = _policy_requirement_payload(policy)
    if set(requirements) != set(expected_requirements):
        raise DialogueAuthoringError("coverage_report_requirements_invalid")
    for field, expected_value in expected_requirements.items():
        if requirements[field] != expected_value:
            raise DialogueAuthoringError("coverage_report_requirements_mismatch")

    base_audit = _require_object(
        coverage_report.get("base_corpus_audit"),
        "coverage_report_base_audit_invalid",
    )
    base_records = _require_non_negative_integer(
        base_audit.get("records_accepted"),
        "coverage_report_base_audit_invalid",
    )
    base_language_characters = _require_non_negative_integer(
        base_audit.get("language_characters"),
        "coverage_report_base_audit_invalid",
    )
    base_dialogue_turns = _require_non_negative_integer(
        base_audit.get("unique_dialogue_turns"),
        "coverage_report_base_audit_invalid",
    )
    if _require_non_negative_integer(
        base_audit.get("records_rejected"),
        "coverage_report_base_audit_invalid",
    ) != 0:
        raise DialogueAuthoringError("coverage_report_base_corpus_not_clean")

    observed = _require_object(
        coverage_report.get("observed"),
        "coverage_report_observed_invalid",
    )
    topic_labels = tuple(sorted(dict(policy.required_topics)))
    goal_labels = tuple(sorted(dict(policy.required_dialogue_goals)))
    tone_labels = tuple(sorted(dict(policy.required_emotional_tones)))
    property_labels = tuple(sorted(dict(policy.required_response_properties)))

    dialogue_records = _require_non_negative_integer(
        observed.get("dialogue_records"),
        "coverage_report_observed_invalid",
    )
    dialogue_turns = _require_non_negative_integer(
        observed.get("dialogue_turns"),
        "coverage_report_observed_invalid",
    )
    multi_turn_records = _require_non_negative_integer(
        observed.get("multi_turn_dialogue_records"),
        "coverage_report_observed_invalid",
    )
    if dialogue_turns != base_dialogue_turns:
        raise DialogueAuthoringError("coverage_report_turn_count_mismatch")
    if multi_turn_records > dialogue_records:
        raise DialogueAuthoringError("coverage_report_multi_turn_count_invalid")
    if dialogue_turns < 2 * dialogue_records:
        raise DialogueAuthoringError("coverage_report_dialogue_turn_count_invalid")

    turns_by_split = _require_count_map(
        observed.get("dialogue_turns_by_split"),
        SPLIT_ORDER,
        "coverage_report_split_counts_invalid",
    )
    if sum(turns_by_split.values()) != dialogue_turns:
        raise DialogueAuthoringError("coverage_report_split_turn_count_mismatch")
    if any(turn_count % 2 for turn_count in turns_by_split.values()):
        raise DialogueAuthoringError("coverage_report_split_turn_count_invalid")

    return {
        "base_records_accepted": base_records,
        "base_language_characters": base_language_characters,
        "dialogue_records": dialogue_records,
        "dialogue_turns": dialogue_turns,
        "multi_turn_dialogue_records": multi_turn_records,
        "dialogue_turns_by_split": turns_by_split,
        "topic_turns": _require_count_map(
            observed.get("topic_turns"),
            topic_labels,
            "coverage_report_topic_counts_invalid",
        ),
        "dialogue_goal_turns": _require_count_map(
            observed.get("dialogue_goal_turns"),
            goal_labels,
            "coverage_report_goal_counts_invalid",
        ),
        "emotional_tone_turns": _require_count_map(
            observed.get("emotional_tone_turns"),
            tone_labels,
            "coverage_report_tone_counts_invalid",
        ),
        "response_property_turns": _require_count_map(
            observed.get("response_property_turns"),
            property_labels,
            "coverage_report_property_counts_invalid",
        ),
    }


def _allocate_integer(
    total: int,
    weights: dict[str, int],
    order: tuple[str, ...],
) -> dict[str, int]:
    """Делит целую квоту по largest remainder без потери или добавления ходов."""
    if total < 0 or set(weights) != set(order):
        raise DialogueAuthoringError("invalid_quota_allocation")
    if total == 0:
        return {label: 0 for label in order}
    if any(weight < 0 for weight in weights.values()):
        raise DialogueAuthoringError("invalid_quota_allocation")
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        raise DialogueAuthoringError("invalid_quota_allocation")

    allocation = {
        label: (total * weights[label]) // weight_sum
        for label in order
    }
    remainders = {
        label: (total * weights[label]) % weight_sum
        for label in order
    }
    remaining = total - sum(allocation.values())
    order_position = {label: index for index, label in enumerate(order)}
    for label in sorted(
        order,
        key=lambda item: (-remainders[item], order_position[item]),
    )[:remaining]:
        allocation[label] += 1
    return allocation


def _minimum_additional_counts(
    requirements: dict[str, int],
    observed: dict[str, int],
) -> dict[str, int]:
    """Возвращает только дефицит известных policy tags."""
    if set(requirements) != set(observed):
        raise DialogueAuthoringError("coverage_requirement_labels_mismatch")
    return {
        label: max(0, requirements[label] - observed[label])
        for label in sorted(requirements)
    }


def _even_dialogue_turn_quota(value: int) -> int:
    """Поднимает требуемый вклад label до достижимого чётного числа ходов.

    В authoring contract каждая новая запись начинается с ``user`` и
    заканчивается ``assistant``, поэтому она содержит 2, 4 или 6 ходов. Один
    topic, goal и tone принадлежат записи целиком: их вклад в каждую из этих
    coverage dimension всегда чётный. Нельзя обещать закрыть нечётный дефицит
    вроде 1 713 ходов ровно 1 713 новыми ходами без нарушения контракта ролей.
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DialogueAuthoringError("invalid_even_dialogue_turn_quota")
    return value if value % DIALOGUE_TURN_STEP == 0 else value + 1


def _minimum_feasible_single_value_counts(
    requirements: dict[str, int],
    observed: dict[str, int],
) -> dict[str, int]:
    """Возвращает достижимые minimums для topic/goal/tone без ложной точности."""
    return {
        label: _even_dialogue_turn_quota(value)
        for label, value in _minimum_additional_counts(requirements, observed).items()
    }


def _single_value_quota(
    *,
    requirements: dict[str, int],
    observed: dict[str, int],
    additional_turns: int,
) -> dict[str, Any]:
    """Планирует topic/goal/tone с учётом чётной длины каждой записи."""
    if additional_turns % DIALOGUE_TURN_STEP:
        raise DialogueAuthoringError("single_value_quota_turn_count_must_be_even")
    policy_minimum_additions = _minimum_additional_counts(requirements, observed)
    minimum_additions = _minimum_feasible_single_value_counts(requirements, observed)
    minimum_total = sum(minimum_additions.values())
    if additional_turns < minimum_total:
        raise DialogueAuthoringError("insufficient_turns_for_single_value_coverage")

    labels = tuple(sorted(requirements))
    extra_quota = _allocate_integer(
        (additional_turns - minimum_total) // DIALOGUE_TURN_STEP,
        {label: requirements[label] for label in labels},
        labels,
    )
    new_batch_quota = {
        label: minimum_additions[label] + DIALOGUE_TURN_STEP * extra_quota[label]
        for label in labels
    }
    return {
        "current_turns": {label: observed[label] for label in labels},
        "policy_minimum_turns": {label: requirements[label] for label in labels},
        "policy_minimum_additional_turns": policy_minimum_additions,
        "minimum_additional_turns": minimum_additions,
        "parity_adjustment_turns": {
            label: minimum_additions[label] - policy_minimum_additions[label]
            for label in labels
        },
        "new_batch_turn_quota": new_batch_quota,
        "target_turns_after_batch": {
            label: observed[label] + new_batch_quota[label]
            for label in labels
        },
    }


def _property_quota(
    *,
    requirements: dict[str, int],
    observed: dict[str, int],
    additional_turns: int,
) -> dict[str, Any]:
    """Планирует минимум свойств ответа без ложного требования один tag на ход."""
    minimum_additions = _minimum_additional_counts(requirements, observed)
    required_assignments = sum(minimum_additions.values())
    average = required_assignments / additional_turns if additional_turns else 0.0
    return {
        "current_turns": {label: observed[label] for label in sorted(observed)},
        "policy_minimum_turns": {label: requirements[label] for label in sorted(requirements)},
        "minimum_additional_turns": minimum_additions,
        "minimum_additional_label_assignments": required_assignments,
        "minimum_average_labels_per_new_turn": round(average, 6),
    }


def _structure_plan(
    *,
    current_records: int,
    current_turns: int,
    current_multi_turn_records: int,
    policy: DialogueCoveragePolicy,
    minimum_additional_turns: int,
    minimum_new_records: int = 0,
) -> dict[str, int]:
    """Подбирает mix двух-, четырёх- и шестиходовых диалогов.

    Используется только чётное число ходов: каждая запись начинается с user и
    заканчивается assistant. Небольшая доля шестиходовых сценариев не даёт
    свести будущий набор к копиям одного четырёхходового шаблона.
    """
    if minimum_new_records < 0:
        raise DialogueAuthoringError("dialogue_structure_plan_not_found")
    preferred_records_for_variety = math.ceil(
        minimum_additional_turns / PREFERRED_DIALOGUE_TURNS_PER_RECORD
    )
    new_records = max(
        0,
        policy.minimum_dialogue_records - current_records,
        minimum_new_records,
        preferred_records_for_variety,
    )
    for _ in range(1_000_000):
        target_records = current_records + new_records
        required_total_multi = max(
            policy.minimum_multi_turn_records,
            math.ceil(policy.minimum_multi_turn_ratio * target_records),
        )
        required_new_multi = max(0, required_total_multi - current_multi_turn_records)

        new_turns = max(
            minimum_additional_turns,
            2 * new_records,
            2 * new_records + 2 * required_new_multi,
        )
        if new_turns % 2:
            new_turns += 1
        extra_turn_pairs = (new_turns - 2 * new_records) // 2

        if (
            required_new_multi <= new_records
            and required_new_multi <= extra_turn_pairs <= 2 * new_records
        ):
            break
        new_records += 1
    else:
        raise DialogueAuthoringError("dialogue_structure_plan_not_found")

    minimum_six_turn_records = max(0, extra_turn_pairs - new_records)
    maximum_six_turn_records = min(
        extra_turn_pairs // 2,
        extra_turn_pairs - required_new_multi,
    )
    if minimum_six_turn_records > maximum_six_turn_records:
        raise DialogueAuthoringError("dialogue_structure_plan_not_found")

    preferred_six_turn_records = extra_turn_pairs // 8
    if extra_turn_pairs >= 8 and maximum_six_turn_records:
        preferred_six_turn_records = max(1, preferred_six_turn_records)
    six_turn_records = min(
        maximum_six_turn_records,
        max(minimum_six_turn_records, preferred_six_turn_records),
    )
    four_turn_records = extra_turn_pairs - 2 * six_turn_records
    two_turn_records = new_records - four_turn_records - six_turn_records
    if min(two_turn_records, four_turn_records, six_turn_records) < 0:
        raise DialogueAuthoringError("dialogue_structure_plan_not_found")
    if four_turn_records + six_turn_records < required_new_multi:
        raise DialogueAuthoringError("dialogue_structure_plan_not_found")

    return {
        "new_dialogue_records": new_records,
        "new_dialogue_turns": new_turns,
        "two_turn_dialogue_records": two_turn_records,
        "four_turn_dialogue_records": four_turn_records,
        "six_turn_dialogue_records": six_turn_records,
        "new_multi_turn_dialogue_records": four_turn_records + six_turn_records,
    }


def _bounded_allocate_integer(
    total: int,
    weights: dict[str, int],
    capacities: dict[str, int],
    order: tuple[str, ...],
) -> dict[str, int]:
    """Делит целую квоту пропорционально, но никогда не превышает capacity split."""
    if (
        total < 0
        or set(weights) != set(order)
        or set(capacities) != set(order)
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in [*weights.values(), *capacities.values()]
        )
        or total > sum(capacities.values())
    ):
        raise DialogueAuthoringError("dialogue_split_structure_not_found")
    if total == 0:
        return {label: 0 for label in order}

    safe_weights = {label: max(1, weights[label]) for label in order}
    provisional = _allocate_integer(total, safe_weights, order)
    allocation = {
        label: min(provisional[label], capacities[label])
        for label in order
    }
    remaining = total - sum(allocation.values())
    order_position = {label: index for index, label in enumerate(order)}

    while remaining:
        candidates = [
            label
            for label in order
            if allocation[label] < capacities[label]
        ]
        if not candidates:
            raise DialogueAuthoringError("dialogue_split_structure_not_found")
        chosen = max(
            candidates,
            key=lambda label: (
                safe_weights[label] / (allocation[label] + 1),
                -order_position[label],
            ),
        )
        allocation[chosen] += 1
        remaining -= 1
    return allocation


def _six_turn_distribution(
    six_turn_records: int,
    pair_capacities: dict[str, int],
    two_turn_records: int,
) -> dict[str, int]:
    """Размещает шестиходовые диалоги с учётом capacity и остатка для 2/4 ходов.

    Шестиходовая запись занимает три пары ходов. Перебираются только восемь
    вариантов чётности для трёх split, поэтому алгоритм не зависит от размера
    корпуса и не превращается в поиск по сырому тексту.
    """
    if (
        six_turn_records < 0
        or two_turn_records < 0
        or set(pair_capacities) != set(SPLIT_ORDER)
        or any(value < 0 for value in pair_capacities.values())
    ):
        raise DialogueAuthoringError("dialogue_split_structure_not_found")

    maximums = {
        split: pair_capacities[split] // 3
        for split in SPLIT_ORDER
    }
    if six_turn_records > sum(maximums.values()):
        raise DialogueAuthoringError("dialogue_split_structure_not_found")

    desired = _bounded_allocate_integer(
        six_turn_records,
        pair_capacities,
        maximums,
        SPLIT_ORDER,
    )
    best: tuple[tuple[int, tuple[int, ...]], dict[str, int]] | None = None

    for parity_values in product((0, 1), repeat=len(SPLIT_ORDER)):
        bases = dict(zip(SPLIT_ORDER, parity_values, strict=True))
        if any(bases[split] > maximums[split] for split in SPLIT_ORDER):
            continue
        remaining_six = six_turn_records - sum(bases.values())
        if remaining_six < 0 or remaining_six % 2:
            continue
        half_capacities = {
            split: (maximums[split] - bases[split]) // 2
            for split in SPLIT_ORDER
        }
        if remaining_six // 2 > sum(half_capacities.values()):
            continue

        half_distribution = _bounded_allocate_integer(
            remaining_six // 2,
            pair_capacities,
            half_capacities,
            SPLIT_ORDER,
        )
        distribution = {
            split: bases[split] + 2 * half_distribution[split]
            for split in SPLIT_ORDER
        }
        remaining_pairs = {
            split: pair_capacities[split] - 3 * distribution[split]
            for split in SPLIT_ORDER
        }
        odd_split_count = sum(value % 2 for value in remaining_pairs.values())
        if odd_split_count > two_turn_records:
            continue

        score = (
            sum(abs(distribution[split] - desired[split]) for split in SPLIT_ORDER),
            tuple(distribution[split] for split in SPLIT_ORDER),
        )
        if best is None or score < best[0]:
            best = (score, distribution)

    if best is None:
        raise DialogueAuthoringError("dialogue_split_structure_not_found")
    return best[1]


def _split_quotas(
    *,
    current_turns_by_split: dict[str, int],
    policy: DialogueCoveragePolicy,
    structure: dict[str, int],
) -> dict[str, dict[str, Any]]:
    """Разносит целевой structural mix по train, validation и test без утечки."""
    policy_minimums = dict(policy.minimum_turns_by_split)
    split_additions = {
        split: max(0, policy_minimums[split] - current_turns_by_split[split])
        for split in SPLIT_ORDER
    }
    if any(turn_count % 2 for turn_count in split_additions.values()):
        raise DialogueAuthoringError("dialogue_split_delta_must_be_even")

    assigned_turns = sum(split_additions.values())
    extra_turns = structure["new_dialogue_turns"] - assigned_turns
    if extra_turns < 0 or extra_turns % 2:
        raise DialogueAuthoringError("dialogue_split_plan_not_found")
    if extra_turns:
        extra_pairs = _allocate_integer(
            extra_turns // 2,
            {split: policy_minimums[split] for split in SPLIT_ORDER},
            SPLIT_ORDER,
        )
        for split in SPLIT_ORDER:
            split_additions[split] += 2 * extra_pairs[split]

    pair_capacities = {
        split: split_additions[split] // 2
        for split in SPLIT_ORDER
    }
    six_turn_distribution = _six_turn_distribution(
        structure["six_turn_dialogue_records"],
        pair_capacities,
        structure["two_turn_dialogue_records"],
    )
    remaining_pair_capacities = {
        split: pair_capacities[split] - 3 * six_turn_distribution[split]
        for split in SPLIT_ORDER
    }
    four_turn_distribution = _bounded_allocate_integer(
        structure["four_turn_dialogue_records"],
        remaining_pair_capacities,
        {
            split: remaining_pair_capacities[split] // 2
            for split in SPLIT_ORDER
        },
        SPLIT_ORDER,
    )

    quotas: dict[str, dict[str, Any]] = {}
    for split in SPLIT_ORDER:
        remaining_pairs = (
            remaining_pair_capacities[split]
            - 2 * four_turn_distribution[split]
        )
        if remaining_pairs < 0:
            raise DialogueAuthoringError("dialogue_split_structure_not_found")
        two_turn_records = remaining_pairs
        quotas[split] = {
            "current_turns": current_turns_by_split[split],
            "policy_minimum_turns": policy_minimums[split],
            "additional_turns": split_additions[split],
            "target_turns_after_batch": (
                current_turns_by_split[split] + split_additions[split]
            ),
            "two_turn_dialogue_records": two_turn_records,
            "four_turn_dialogue_records": four_turn_distribution[split],
            "six_turn_dialogue_records": six_turn_distribution[split],
            "new_dialogue_records": (
                two_turn_records
                + four_turn_distribution[split]
                + six_turn_distribution[split]
            ),
        }

    if sum(item["new_dialogue_records"] for item in quotas.values()) != structure[
        "new_dialogue_records"
    ]:
        raise DialogueAuthoringError("dialogue_split_record_count_mismatch")
    if sum(item["additional_turns"] for item in quotas.values()) != structure[
        "new_dialogue_turns"
    ]:
        raise DialogueAuthoringError("dialogue_split_turn_count_mismatch")
    if sum(item["two_turn_dialogue_records"] for item in quotas.values()) != structure[
        "two_turn_dialogue_records"
    ]:
        raise DialogueAuthoringError("dialogue_split_record_count_mismatch")
    return quotas


def build_dialogue_authoring_blueprint(
    coverage_report: dict[str, Any],
    policy: DialogueCoveragePolicy,
) -> dict[str, Any]:
    """Строит проверяемый план следующего data-collection batch.

    Blueprint не является candidate JSONL и не делает source пригодным для
    обучения. Он лишь фиксирует, сколько новых оригинальных или проверенно
    лицензированных ходов надо подготовить, чтобы следующий кандидат мог
    претендовать на закрытие policy.
    """
    state = _safe_coverage_state(coverage_report, policy)
    topic_requirements = dict(policy.required_topics)
    goal_requirements = dict(policy.required_dialogue_goals)
    tone_requirements = dict(policy.required_emotional_tones)
    property_requirements = dict(policy.required_response_properties)
    split_requirements = dict(policy.minimum_turns_by_split)

    category_minimum_additions = max(
        sum(
            _minimum_feasible_single_value_counts(
                topic_requirements,
                state["topic_turns"],
            ).values()
        ),
        sum(
            _minimum_feasible_single_value_counts(
                goal_requirements,
                state["dialogue_goal_turns"],
            ).values()
        ),
        sum(
            _minimum_feasible_single_value_counts(
                tone_requirements,
                state["emotional_tone_turns"],
            ).values()
        ),
    )
    split_minimum_additions = sum(
        max(0, split_requirements[split] - state["dialogue_turns_by_split"][split])
        for split in SPLIT_ORDER
    )
    minimum_additional_turns = max(
        0,
        policy.minimum_unique_dialogue_turns - state["dialogue_turns"],
        category_minimum_additions,
        split_minimum_additions,
    )

    minimum_new_records = 0
    for _ in range(MAX_SPLIT_STRUCTURE_ATTEMPTS):
        structure = _structure_plan(
            current_records=state["dialogue_records"],
            current_turns=state["dialogue_turns"],
            current_multi_turn_records=state["multi_turn_dialogue_records"],
            policy=policy,
            minimum_additional_turns=minimum_additional_turns,
            minimum_new_records=minimum_new_records,
        )
        try:
            split_quotas = _split_quotas(
                current_turns_by_split=state["dialogue_turns_by_split"],
                policy=policy,
                structure=structure,
            )
            break
        except DialogueAuthoringError as error:
            if str(error) != "dialogue_split_structure_not_found":
                raise
            minimum_new_records = structure["new_dialogue_records"] + 1
    else:
        raise DialogueAuthoringError("dialogue_split_structure_not_found")
    topic_quota = _single_value_quota(
        requirements=topic_requirements,
        observed=state["topic_turns"],
        additional_turns=structure["new_dialogue_turns"],
    )
    goal_quota = _single_value_quota(
        requirements=goal_requirements,
        observed=state["dialogue_goal_turns"],
        additional_turns=structure["new_dialogue_turns"],
    )
    tone_quota = _single_value_quota(
        requirements=tone_requirements,
        observed=state["emotional_tone_turns"],
        additional_turns=structure["new_dialogue_turns"],
    )
    property_quota = _property_quota(
        requirements=property_requirements,
        observed=state["response_property_turns"],
        additional_turns=structure["new_dialogue_turns"],
    )

    target_records = state["dialogue_records"] + structure["new_dialogue_records"]
    target_turns = state["dialogue_turns"] + structure["new_dialogue_turns"]
    target_multi_turn_records = (
        state["multi_turn_dialogue_records"]
        + structure["new_multi_turn_dialogue_records"]
    )
    target_multi_turn_ratio = (
        target_multi_turn_records / target_records if target_records else 0.0
    )

    return {
        "format_version": AUTHORING_BLUEPRINT_FORMAT_VERSION,
        "blueprint_id": AUTHORING_BLUEPRINT_ID,
        "policy_id": policy.policy_id,
        "scope": {
            "language": "ru",
            "record_type": "dialogue",
            "layer": "dialogue",
        },
        "base_corpus_snapshot": {
            "records_accepted": state["base_records_accepted"],
            "language_characters": state["base_language_characters"],
            "dialogue_records": state["dialogue_records"],
            "dialogue_turns": state["dialogue_turns"],
            "multi_turn_dialogue_records": state["multi_turn_dialogue_records"],
        },
        "target_after_next_batch": {
            "dialogue_records": target_records,
            "dialogue_turns": target_turns,
            "multi_turn_dialogue_records": target_multi_turn_records,
            "multi_turn_dialogue_ratio": round(target_multi_turn_ratio, 6),
        },
        "new_batch": {
            "dialogue_records": structure["new_dialogue_records"],
            "dialogue_turns": structure["new_dialogue_turns"],
            "dialogue_records_by_turn_count": {
                "2": structure["two_turn_dialogue_records"],
                "4": structure["four_turn_dialogue_records"],
                "6": structure["six_turn_dialogue_records"],
            },
            "multi_turn_dialogue_records": structure["new_multi_turn_dialogue_records"],
            "split_quotas": split_quotas,
            "topic_coverage": topic_quota,
            "dialogue_goal_coverage": goal_quota,
            "emotional_tone_coverage": tone_quota,
        "response_property_coverage": property_quota,
        },
        "planning_boundary": {
            "raw_dialogues_created": False,
            "candidate_created": False,
            "active_manifest_updated": False,
            "training_started": False,
            "next_required_action": (
                "prepare_owned_or_verified_dialogue_candidate_in_staging"
            ),
        },
    }


def _quota_lines(title: str, quota: dict[str, Any], include_targets: bool) -> list[str]:
    """Рендерит только policy labels и числа одной metadata coverage dimension."""
    lines = [f"### {title}", ""]
    for label in quota["policy_minimum_turns"]:
        additional = quota["new_batch_turn_quota"][label] if include_targets else quota[
            "minimum_additional_turns"
        ][label]
        lines.append(f"- {label}: подготовить {additional} ходов.")
    lines.append("")
    return lines


def authoring_blueprint_markdown(blueprint: dict[str, Any]) -> str:
    """Создаёт читаемый Markdown без реплик, source tags и идентификаторов."""
    batch = _require_object(blueprint.get("new_batch"), "blueprint_invalid")
    snapshot = _require_object(blueprint.get("base_corpus_snapshot"), "blueprint_invalid")
    target = _require_object(blueprint.get("target_after_next_batch"), "blueprint_invalid")
    split_quotas = _require_object(batch.get("split_quotas"), "blueprint_invalid")
    structure = _require_object(batch.get("dialogue_records_by_turn_count"), "blueprint_invalid")
    properties = _require_object(
        batch.get("response_property_coverage"),
        "blueprint_invalid",
    )

    lines = [
        "# Brain v3 — blueprint следующего dialogue-кандидата",
        "",
        "Этот документ содержит только квоты и policy labels. Он не создаёт реплики, не является датасетом, не меняет manifest и не запускает обучение.",
        "",
        "## Текущее проверенное состояние",
        "",
        f"- Чистых записей active corpus: {snapshot['records_accepted']}.",
        f"- Языковых символов: {snapshot['language_characters']}.",
        f"- Dialogue-записей и ходов: {snapshot['dialogue_records']} и {snapshot['dialogue_turns']}.",
        "",
        "## Следующий обязательный batch",
        "",
        f"- Подготовить новых dialogue-записей: {batch['dialogue_records']}.",
        f"- Подготовить новых dialogue-ходов: {batch['dialogue_turns']}.",
        f"- Структура: двухходовых {structure['2']}, четырёхходовых {structure['4']}, шестиходовых {structure['6']}.",
        f"- После чистого принятия кандидат доведёт слой до {target['dialogue_records']} записей и {target['dialogue_turns']} ходов.",
        f"- Многоходовых записей после batch будет {target['multi_turn_dialogue_records']} при доле {target['multi_turn_dialogue_ratio']:.3f}.",
        "",
        "## Split-квоты",
        "",
        "| Split | Новых ходов | Двухходовых | Четырёхходовых | Шестиходовых |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for split in SPLIT_ORDER:
        quota = _require_object(split_quotas.get(split), "blueprint_invalid")
        lines.append(
            "| {split} | {turns} | {two} | {four} | {six} |".format(
                split=split,
                turns=quota["additional_turns"],
                two=quota["two_turn_dialogue_records"],
                four=quota["four_turn_dialogue_records"],
                six=quota["six_turn_dialogue_records"],
            )
        )
    lines.extend(
        [
            "",
        "## Смысловые квоты",
        "",
        "Квоты topic, goal и tone учитывают, что каждая новая запись имеет только чётное число ходов. Если policy-дефицит нечётный, план добавляет ровно один необходимый ход до достижимой чётной квоты; это не является искусственным наращиванием данных.",
        "",
        ]
    )
    lines.extend(
        _quota_lines(
            "Темы",
            _require_object(batch.get("topic_coverage"), "blueprint_invalid"),
            True,
        )
    )
    lines.extend(
        _quota_lines(
            "Цели диалога",
            _require_object(batch.get("dialogue_goal_coverage"), "blueprint_invalid"),
            True,
        )
    )
    lines.extend(
        _quota_lines(
            "Эмоциональные тона",
            _require_object(batch.get("emotional_tone_coverage"), "blueprint_invalid"),
            True,
        )
    )
    lines.extend(
        [
            "### Свойства ответа",
            "",
            f"- Минимум дополнительных присваиваний properties: {properties['minimum_additional_label_assignments']}.",
            f"- Минимальная средняя плотность properties: {properties['minimum_average_labels_per_new_turn']:.3f} на новый ход.",
        ]
    )
    for label, additional in properties["minimum_additional_turns"].items():
        lines.append(f"- {label}: не менее {additional} ходов.")
    lines.extend(
        [
            "",
            "## Граница применения",
            "",
            "Каждый будущий материал всё равно должен быть оригинальным или иметь проверенную разрешённую лицензию, пройти staging audit, cross-source preflight, ручную выборочную проверку и только затем отдельное решение о local promotion.",
            "Blueprint не создаёт похожие фразы для количества и не разрешает генеративное обучение сам по себе.",
            "",
        ]
    )
    return "\n".join(lines)
