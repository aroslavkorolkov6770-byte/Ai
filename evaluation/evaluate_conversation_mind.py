"""Проверяет поведение conversation-mind через настоящий маршрут API.

Сценарии написаны вручную и не входят в обучающий архив. Они не проверяют
совпадение с заранее заученной фразой: важнее, что ответ попал в нужный
маршрут, остаётся компактным и не прячет ссылки или старую тему обучения.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from ai_core import api_server
from ai_core.conversation_mind import ConversationMind
from ai_core.internet_search import WebAnswer
from ai_core.memory_store import ConversationStore


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evaluation" / "conversation_mind_acceptance.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "results" / "conversation-mind-v2-acceptance"
FORBIDDEN_CONVERSATION_FRAGMENTS = (
    "html",
    "css",
    "javascript",
    "я учусь помогать",
    "http://",
    "https://",
)


class _FixedInternetSearch:
    """Изолирует проверку маршрута от реальных сайтов и их доступности."""

    def answer(self, query: str, *arguments: object) -> WebAnswer:
        del arguments
        return WebAnswer(text=f"Проверенный краткий факт по запросу: {query}", results=())


def _load_cases(path: Path) -> list[dict[str, Any]]:
    """Читает маленький независимый набор сценариев с жёсткой валидацией."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Acceptance-набор conversation-mind должен быть непустым массивом.")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Каждый acceptance-сценарий должен быть объектом.")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
            raise ValueError("Acceptance-сценарий имеет пустой или повторный id.")
        seen.add(identifier)
        for field in ("prompt", "expected_source", "expected_intent"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"Acceptance-сценарий {identifier} не содержит {field}.")
        for field in ("min_chars", "max_chars"):
            if not isinstance(item.get(field), int) or item[field] < 0:
                raise ValueError(f"Acceptance-сценарий {identifier} содержит неверный {field}.")
        if item["min_chars"] > item["max_chars"]:
            raise ValueError(f"Acceptance-сценарий {identifier} имеет неверный диапазон длины.")
        previous_messages = item.get("previous_messages", [])
        if not isinstance(previous_messages, list):
            raise ValueError(f"Acceptance-сценарий {identifier} содержит неверную историю.")
        if "expected_context_used" in item and not isinstance(item["expected_context_used"], bool):
            raise ValueError(f"Acceptance-сценарий {identifier} содержит неверный context flag.")
        cases.append(item)
    return cases


def _validate_history(messages: list[Any]) -> list[dict[str, str]]:
    """Не позволяет acceptance-файлу передать в SQLite служебные поля."""
    validated: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("История acceptance должна состоять из объектов.")
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
            raise ValueError("История acceptance содержит некорректную реплику.")
        validated.append({"role": role, "content": content.strip()})
    return validated


def _has_forbidden_fragment(text: str) -> bool:
    """Проверяет, что разговорный ответ не выдаёт ссылку и старую специализацию."""
    normalised = text.casefold()
    return any(fragment in normalised for fragment in FORBIDDEN_CONVERSATION_FRAGMENTS)


def evaluate(
    cases_path: Path = DEFAULT_CASES,
    output: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    """Прогоняет acceptance через API и сохраняет только структурированный отчёт."""
    if output.exists():
        raise ValueError(f"Каталог результата уже существует: {output}")
    cases = _load_cases(cases_path)
    original_store = api_server.conversation_store
    original_search = api_server.internet_search
    original_router = api_server.internet_router
    original_policy = api_server.web_dialogue_policy
    original_mind = api_server.conversation_mind
    original_mind_error = api_server.conversation_mind_load_error
    try:
        with tempfile.TemporaryDirectory() as directory:
            api_server.conversation_store = ConversationStore(
                Path(directory) / "conversation-mind-acceptance.sqlite3"
            )
            api_server.conversation_store.initialize()
            api_server.internet_search = _FixedInternetSearch()
            api_server.internet_router = None
            api_server.web_dialogue_policy = None
            api_server.conversation_mind = ConversationMind.load()
            api_server.conversation_mind_load_error = None
            if api_server.reliable_qa is None:
                api_server.load_reliable_artifacts()

            results: list[dict[str, Any]] = []
            for case in cases:
                conversation_id = api_server.conversation_store.create_conversation()
                for message in _validate_history(case.get("previous_messages", [])):
                    api_server.conversation_store.add_message(
                        conversation_id,
                        message["role"],
                        message["content"],
                    )
                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt=case["prompt"],
                            conversation_id=conversation_id,
                        )
                    )
                )
                forbidden = (
                    _has_forbidden_fragment(response.text)
                    if response.source == "conversation-mind"
                    else False
                )
                context_matches = (
                    "expected_context_used" not in case
                    or response.context_used == case["expected_context_used"]
                )
                passed = (
                    response.source == case["expected_source"]
                    and response.intent == case["expected_intent"]
                    and case["min_chars"] <= len(response.text) <= case["max_chars"]
                    and not forbidden
                    and context_matches
                )
                results.append(
                    {
                        "id": case["id"],
                        "passed": passed,
                        "source": response.source,
                        "intent": response.intent,
                        "chars": len(response.text),
                        "context_used": response.context_used,
                        "forbidden_fragment_present": forbidden,
                    }
                )

            # Разные диалоги не должны получать одну-единственную заученную
            # фразу на приветствие. Текст самих ответов в отчёт не попадает.
            greeting_variants: set[str] = set()
            for _ in range(16):
                response = asyncio.run(
                    api_server.generate(api_server.GenerateRequest(prompt="Привет"))
                )
                greeting_variants.add(response.text)
            diversity_passed = len(greeting_variants) >= 10
            results.append(
                {
                    "id": "greeting_diversity",
                    "passed": diversity_passed,
                    "unique_responses": len(greeting_variants),
                    "required_minimum": 10,
                }
            )
    finally:
        api_server.conversation_store = original_store
        api_server.internet_search = original_search
        api_server.internet_router = original_router
        api_server.web_dialogue_policy = original_policy
        api_server.conversation_mind = original_mind
        api_server.conversation_mind_load_error = original_mind_error

    passed = sum(bool(result["passed"]) for result in results)
    report = {
        "model_version": "conversation-mind-v2.0",
        "cases": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
    if report["failed"]:
        raise RuntimeError(json.dumps(report, ensure_ascii=False))
    output.mkdir(parents=True)
    (output / "conversation_mind_acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "CONVERSATION_MIND_ACCEPTANCE_REPORT.md").write_text(
        "# Проверка conversation-mind v2.0\n\n"
        f"Пройдено сценариев: **{passed}/{len(results)}**.\n\n"
        "Проверка использовала локальный checkpoint, изолированный поиск и "
        "не передавала модели готовые ответы из пользовательского архива.\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Обрабатывает CLI-параметры evaluator-а."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        report = evaluate(args.cases, args.output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (FileNotFoundError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        raise SystemExit(f"Проверка conversation-mind остановлена: {error}") from error


if __name__ == "__main__":
    main()
