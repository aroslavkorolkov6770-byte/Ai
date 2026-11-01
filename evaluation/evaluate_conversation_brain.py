"""Проверяет разговорный маршрут API на независимых ручных сценариях.

Сценарии не участвуют в обучении. Они проверяют именно пользовательское
поведение: обычный диалог не уходит в старую узкую тему, а вопрос о факте не
перехватывается разговорным ответом.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from ai_core import api_server
from ai_core.conversation_brain import ConversationBrain
from ai_core.internet_search import WebAnswer
from ai_core.memory_store import ConversationStore


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evaluation" / "conversation_brain_acceptance.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "results" / "conversation-brain-v1-acceptance"
FORBIDDEN_LEGACY_FRAGMENT = "Сейчас моя основная область"


class _FixedInternetSearch:
    """Исключает внешнюю сеть из acceptance и фиксирует пройденный маршрут."""

    def answer(self, query: str, *arguments: object) -> WebAnswer:
        del query, arguments
        return WebAnswer(text="Проверенный краткий факт.", results=())


def _load_cases(path: Path) -> list[dict[str, Any]]:
    """Читает и строго проверяет небольшой независимый набор сценариев."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Acceptance-набор conversation-brain должен быть непустым массивом.")
    cases: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Каждый acceptance-сценарий должен быть объектом.")
        required = ("id", "prompt", "expected_source", "expected_intent", "must_contain")
        if any(not isinstance(item.get(field), str) or not item[field].strip() for field in required):
            raise ValueError("Acceptance-сценарий не содержит обязательное текстовое поле.")
        previous_messages = item.get("previous_messages", [])
        if not isinstance(previous_messages, list):
            raise ValueError("previous_messages должен быть массивом.")
        cases.append(item)
    return cases


def evaluate(cases_path: Path = DEFAULT_CASES, output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    """Прогоняет cases через настоящий API-маршрут без сетевых запросов."""
    if output.exists():
        raise ValueError(f"Каталог результата уже существует: {output}")
    cases = _load_cases(cases_path)
    original_store = api_server.conversation_store
    original_search = api_server.internet_search
    original_router = api_server.internet_router
    original_brain = api_server.conversation_brain
    original_brain_error = api_server.conversation_brain_load_error
    try:
        with tempfile.TemporaryDirectory() as directory:
            api_server.conversation_store = ConversationStore(
                Path(directory) / "conversation-brain-acceptance.sqlite3"
            )
            api_server.conversation_store.initialize()
            api_server.internet_search = _FixedInternetSearch()
            api_server.internet_router = None
            api_server.conversation_brain = ConversationBrain.load()
            api_server.conversation_brain_load_error = None
            if api_server.reliable_qa is None:
                api_server.load_reliable_artifacts()

            results: list[dict[str, Any]] = []
            for case in cases:
                conversation_id = api_server.conversation_store.create_conversation()
                for message in case.get("previous_messages", []):
                    if not isinstance(message, dict):
                        raise ValueError("previous_messages содержит не объект.")
                    role = message.get("role")
                    content = message.get("content")
                    if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
                        raise ValueError("previous_messages содержит некорректную реплику.")
                    api_server.conversation_store.add_message(conversation_id, role, content)
                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt=case["prompt"],
                            conversation_id=conversation_id,
                        )
                    )
                )
                passed = (
                    response.source == case["expected_source"]
                    and response.intent == case["expected_intent"]
                    and case["must_contain"] in response.text
                    and FORBIDDEN_LEGACY_FRAGMENT not in response.text
                )
                results.append(
                    {
                        "id": case["id"],
                        "passed": passed,
                        "source": response.source,
                        "intent": response.intent,
                        "context_used": response.context_used,
                        "legacy_fragment_present": FORBIDDEN_LEGACY_FRAGMENT in response.text,
                    }
                )
    finally:
        api_server.conversation_store = original_store
        api_server.internet_search = original_search
        api_server.internet_router = original_router
        api_server.conversation_brain = original_brain
        api_server.conversation_brain_load_error = original_brain_error

    passed = sum(1 for result in results if result["passed"])
    report = {
        "model_version": "conversation-brain-v1.0",
        "cases": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
    if report["failed"]:
        raise RuntimeError(json.dumps(report, ensure_ascii=False))
    output.mkdir(parents=True)
    (output / "conversation_brain_acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "CONVERSATION_BRAIN_ACCEPTANCE_REPORT.md").write_text(
        "# Проверка conversation-brain v1.0\n\n"
        f"Пройдено сценариев: **{passed}/{len(results)}**.\n\n"
        "Проверка не использовала внешний интернет и не передавала модели "
        "готовые ответы из пользовательского архива.\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Запускает evaluator и выводит только результаты сценариев."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        report = evaluate(args.cases, args.output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (FileNotFoundError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        raise SystemExit(f"Проверка conversation-brain остановлена: {error}") from error


if __name__ == "__main__":
    main()
