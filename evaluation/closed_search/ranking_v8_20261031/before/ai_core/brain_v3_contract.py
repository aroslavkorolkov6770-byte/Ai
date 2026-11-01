"""Контракт независимой оценки будущего генеративного мозга.

Файл с acceptance-сценариями намеренно хранит свойства хорошего ответа, а не
готовый эталонный текст. Так тест не превращается в ещё один набор шаблонов,
которые модель может просто выучить. Этот модуль не запускает модель, не читает
пользовательские чаты и не обращается к сети: он валидирует структуру закрытого
набора качества до каждого нового эксперимента.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .internet_router import normalize_route_text


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BRAIN_V3_CONTRACT = ROOT / "evaluation" / "brain_v3_acceptance.json"
FORMAT_VERSION = 2
CONTRACT_TIERS = frozenset({"baseline", "expanded"})
MINIMUM_BASELINE_SCENARIOS = 24
MINIMUM_EXPANDED_SCENARIOS = 200
MINIMUM_EXPANDED_SCENARIOS_PER_DOMAIN = 12
MINIMUM_EXPANDED_HISTORY_SCENARIOS = 48
ALLOWED_DOMAINS = frozenset(
    {
        "casual_dialogue",
        "emotional_support",
        "opinion_and_choice",
        "explanation_and_instruction",
        "context_and_memory",
        "correction",
        "uncertainty_and_clarification",
        "tool_routing",
        "web_grounding",
        "privacy_and_memory",
        "style_and_length",
        "safety_boundaries",
    }
)
ALLOWED_ROUTES = frozenset(
    {
        "direct_dialogue",
        "structured_reasoning",
        "local_explanation",
        "clarify",
        "memory_lookup",
        "tool_time",
        "tool_weather",
        "tool_web",
        "assistant_identity",
        "refuse_or_safe_redirect",
    }
)
ALLOWED_LENGTHS = frozenset({"short", "balanced", "extended"})
ALLOWED_PROPERTIES = frozenset(
    {
        "relevant",
        "natural_tone",
        "brief",
        "context_retained",
        "no_repeat",
        "ask_when_missing",
        "fact_grounded",
        "no_url_without_request",
        "no_unfounded_memory",
        "no_legacy_topic",
        "privacy_respected",
        "acknowledges_correction",
        "appropriate_empathy",
        "clear_next_step",
        "no_fake_certainty",
        "safe_boundary",
        "source_on_request",
        "uses_requested_style",
        "tool_result_returned",
        "no_action_promise",
    }
)
TOOL_RESULT_ROUTES = frozenset({"tool_time", "tool_weather", "tool_web"})
MAX_PROMPT_LENGTH = 1_000
MAX_HISTORY_MESSAGES = 20


class BrainV3ContractError(ValueError):
    """Останавливает оценку до запуска модели при повреждённом контракте."""


@dataclass(frozen=True)
class ContractMessage:
    """Одна тестовая историческая реплика без скрытых служебных полей."""

    role: str
    content: str


@dataclass(frozen=True)
class BrainV3Scenario:
    """Проверяемый сценарий без заранее записанного текста ответа."""

    identifier: str
    domain: str
    prompt: str
    history: tuple[ContractMessage, ...]
    expected_route: str
    expected_length: str
    properties: frozenset[str]


@dataclass(frozen=True)
class BrainV3Contract:
    """Полностью проверенный набор независимых требований к кандидату."""

    version: int
    name: str
    language: str
    tier: str
    scenarios: tuple[BrainV3Scenario, ...]


def _fingerprint(text: str) -> str:
    """Строит ключ дедупликации, не выводя сам текст в итоговом отчёте."""
    return hashlib.sha256(normalize_route_text(text).encode("utf-8")).hexdigest()


def _require_string(payload: dict[str, Any], field: str, maximum: int = 200) -> str:
    """Проверяет обязательную короткую строку контракта."""
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise BrainV3ContractError(f"Поле {field} должно быть непустой строкой до {maximum} символов.")
    return value.strip()


def _parse_history(raw_history: Any, identifier: str) -> tuple[ContractMessage, ...]:
    """Разрешает только текстовые user/assistant сообщения в тестовой истории."""
    if raw_history is None:
        return ()
    if not isinstance(raw_history, list) or len(raw_history) > MAX_HISTORY_MESSAGES:
        raise BrainV3ContractError(
            f"Сценарий {identifier}: history должен быть массивом до {MAX_HISTORY_MESSAGES} сообщений."
        )
    history: list[ContractMessage] = []
    for position, message in enumerate(raw_history, start=1):
        if not isinstance(message, dict):
            raise BrainV3ContractError(
                f"Сценарий {identifier}: history[{position}] должен быть объектом."
            )
        if set(message) != {"role", "content"}:
            raise BrainV3ContractError(
                f"Сценарий {identifier}: history[{position}] содержит недопустимые поля."
            )
        role = _require_string(message, "role", maximum=20)
        content = _require_string(message, "content", maximum=MAX_PROMPT_LENGTH)
        if role not in {"user", "assistant"}:
            raise BrainV3ContractError(
                f"Сценарий {identifier}: history[{position}] имеет неизвестную роль."
            )
        history.append(ContractMessage(role=role, content=content))
    return tuple(history)


def _parse_scenario(payload: Any) -> BrainV3Scenario:
    """Строго разбирает один сценарий и отсекает скрытые подсказки ответа."""
    if not isinstance(payload, dict):
        raise BrainV3ContractError("Каждый сценарий должен быть JSON-объектом.")
    allowed_fields = {"id", "domain", "prompt", "history", "expected"}
    if set(payload) != allowed_fields:
        raise BrainV3ContractError("Сценарий содержит недопустимые или отсутствующие поля.")
    identifier = _require_string(payload, "id", maximum=80)
    domain = _require_string(payload, "domain", maximum=80)
    prompt = _require_string(payload, "prompt", maximum=MAX_PROMPT_LENGTH)
    if domain not in ALLOWED_DOMAINS:
        raise BrainV3ContractError(f"Сценарий {identifier}: неизвестный domain {domain}.")
    expected = payload.get("expected")
    if not isinstance(expected, dict) or set(expected) != {"route", "length", "properties"}:
        raise BrainV3ContractError(
            f"Сценарий {identifier}: expected должен содержать route, length и properties."
        )
    route = _require_string(expected, "route", maximum=80)
    length = _require_string(expected, "length", maximum=20)
    raw_properties = expected.get("properties")
    if route not in ALLOWED_ROUTES or length not in ALLOWED_LENGTHS:
        raise BrainV3ContractError(f"Сценарий {identifier}: неизвестный ожидаемый маршрут или длина.")
    if not isinstance(raw_properties, list) or not raw_properties:
        raise BrainV3ContractError(f"Сценарий {identifier}: properties должен быть непустым массивом.")
    properties = frozenset(_require_string({"property": item}, "property", 80) for item in raw_properties)
    if len(properties) != len(raw_properties) or not properties <= ALLOWED_PROPERTIES:
        raise BrainV3ContractError(
            f"Сценарий {identifier}: properties содержит повтор или неизвестное свойство."
        )
    # Меняющийся факт должен прийти из конкретного инструмента, а не быть
    # сгенерированным предположением. Два свойства добавлены после отклонения
    # promise-only сценария: они применимы только к weather/time/web route и
    # не позволяют объявить промежуточную фразу «проверю» хорошим ответом.
    if "tool_result_returned" in properties and route not in TOOL_RESULT_ROUTES:
        raise BrainV3ContractError(
            f"Сценарий {identifier}: tool_result_returned допустим только для tool route."
        )
    if "no_action_promise" in properties and (
        route not in TOOL_RESULT_ROUTES or "tool_result_returned" not in properties
    ):
        raise BrainV3ContractError(
            f"Сценарий {identifier}: no_action_promise требует tool_result_returned на tool route."
        )
    return BrainV3Scenario(
        identifier=identifier,
        domain=domain,
        prompt=prompt,
        history=_parse_history(payload.get("history"), identifier),
        expected_route=route,
        expected_length=length,
        properties=properties,
    )


def load_brain_v3_contract(path: Path = DEFAULT_BRAIN_V3_CONTRACT) -> BrainV3Contract:
    """Загружает контракт, не смешивая его с train-корпусом или чатом."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Не найден Brain v3 contract: {resolved}")
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise BrainV3ContractError("Brain v3 contract должен быть JSON-объектом.")
    required = {
        "format_version",
        "name",
        "language",
        "contract_tier",
        "evaluation_only",
        "training_eligible",
        "scenarios",
    }
    if set(raw) != required:
        raise BrainV3ContractError("Brain v3 contract содержит недопустимые или отсутствующие поля.")
    if raw.get("format_version") != FORMAT_VERSION:
        raise BrainV3ContractError("Неизвестная версия Brain v3 contract.")
    if raw.get("evaluation_only") is not True or raw.get("training_eligible") is not False:
        raise BrainV3ContractError("Контракт должен быть явно закрыт для обучения.")
    name = _require_string(raw, "name", maximum=120)
    language = _require_string(raw, "language", maximum=40)
    tier = _require_string(raw, "contract_tier", maximum=40)
    if tier not in CONTRACT_TIERS:
        raise BrainV3ContractError("Brain v3 contract содержит неизвестный contract_tier.")
    raw_scenarios = raw.get("scenarios")
    minimum_scenarios = (
        MINIMUM_EXPANDED_SCENARIOS if tier == "expanded" else MINIMUM_BASELINE_SCENARIOS
    )
    if not isinstance(raw_scenarios, list) or len(raw_scenarios) < minimum_scenarios:
        raise BrainV3ContractError(
            f"Контракт tier={tier} должен содержать минимум {minimum_scenarios} независимых сценариев."
        )
    scenarios = tuple(_parse_scenario(item) for item in raw_scenarios)
    identifiers = [scenario.identifier for scenario in scenarios]
    if len(identifiers) != len(set(identifiers)):
        raise BrainV3ContractError("В Brain v3 contract повторяются id сценариев.")
    fingerprints = [_fingerprint(scenario.prompt) for scenario in scenarios]
    if len(fingerprints) != len(set(fingerprints)):
        raise BrainV3ContractError("В Brain v3 contract повторяются нормализованные prompts.")
    domains = {scenario.domain for scenario in scenarios}
    missing_domains = sorted(ALLOWED_DOMAINS - domains)
    if missing_domains:
        raise BrainV3ContractError(
            f"Brain v3 contract не покрывает домены: {', '.join(missing_domains)}."
        )
    if tier == "expanded":
        domain_counts = Counter(scenario.domain for scenario in scenarios)
        sparse_domains = sorted(
            domain
            for domain, count in domain_counts.items()
            if count < MINIMUM_EXPANDED_SCENARIOS_PER_DOMAIN
        )
        if sparse_domains:
            raise BrainV3ContractError(
                "Расширенный Brain v3 contract должен иметь минимум "
                f"{MINIMUM_EXPANDED_SCENARIOS_PER_DOMAIN} сценариев на domain: "
                f"{', '.join(sparse_domains)}."
            )
        history_scenarios = sum(bool(scenario.history) for scenario in scenarios)
        if history_scenarios < MINIMUM_EXPANDED_HISTORY_SCENARIOS:
            raise BrainV3ContractError(
                "Расширенный Brain v3 contract должен иметь минимум "
                f"{MINIMUM_EXPANDED_HISTORY_SCENARIOS} сценариев с историей."
            )
    return BrainV3Contract(
        version=FORMAT_VERSION,
        name=name,
        language=language,
        tier=tier,
        scenarios=scenarios,
    )


def contract_summary(contract: BrainV3Contract) -> dict[str, Any]:
    """Возвращает отчёт без вывода закрытых prompts и истории сценариев."""
    domains = Counter(scenario.domain for scenario in contract.scenarios)
    routes = Counter(scenario.expected_route for scenario in contract.scenarios)
    lengths = Counter(scenario.expected_length for scenario in contract.scenarios)
    properties = Counter(
        property_name
        for scenario in contract.scenarios
        for property_name in scenario.properties
    )
    history_scenarios = sum(bool(scenario.history) for scenario in contract.scenarios)
    return {
        "format_version": contract.version,
        "name": contract.name,
        "language": contract.language,
        "contract_tier": contract.tier,
        "scenarios": len(contract.scenarios),
        "scenarios_with_history": history_scenarios,
        "domains": dict(sorted(domains.items())),
        "expected_routes": dict(sorted(routes.items())),
        "expected_lengths": dict(sorted(lengths.items())),
        "properties": dict(sorted(properties.items())),
        "evaluation_only": True,
        "training_eligible": False,
    }


def summary_markdown(summary: dict[str, Any]) -> str:
    """Формирует Markdown без передачи тестовых формулировок в журнал."""
    rows = [
        "# Brain v3 — контракт качества",
        "",
        f"Сценариев: **{summary['scenarios']}**.",
        f"Сценариев с историей: **{summary['scenarios_with_history']}**.",
        f"Уровень контракта: `{summary['contract_tier']}`.",
        "",
        "## Граница использования",
        "",
        "Этот набор используется только для независимой оценки. Он не является "
        "датасетом обучения и не содержит эталонных текстов ответа.",
        "",
        "## Покрытие доменов",
        "",
    ]
    rows.extend(f"- `{name}`: {count}." for name, count in summary["domains"].items())
    rows.extend(["", "## Ожидаемые маршруты", ""])
    rows.extend(
        f"- `{name}`: {count}." for name, count in summary["expected_routes"].items()
    )
    return "\n".join(rows) + "\n"
