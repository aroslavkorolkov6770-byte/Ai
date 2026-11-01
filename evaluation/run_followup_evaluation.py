"""Проверяет качество продолжения веб-диалога с контекстом и без него."""

from __future__ import annotations

import argparse
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FOLLOWUP_DATASET_PATH = PROJECT_ROOT / "training_data" / "web_followup_eval.jsonl"
DEFAULT_API_URL = "http://127.0.0.1:8000"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def normalized_text_similarity(actual: str, expected: str) -> float:
    """Считает мягкое сходство двух ответов от 0 до 1."""
    normalized_actual = " ".join(actual.casefold().split())
    normalized_expected = " ".join(expected.casefold().split())
    return SequenceMatcher(
        None,
        normalized_actual,
        normalized_expected,
        autojunk=False,
    ).ratio()


def load_followup_cases() -> list[dict[str, Any]]:
    """Читает отдельный evaluation-файл и проверяет его структуру."""
    if not FOLLOWUP_DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Файл отложенной проверки не найден: {FOLLOWUP_DATASET_PATH}"
        )

    cases: list[dict[str, Any]] = []
    with FOLLOWUP_DATASET_PATH.open("r", encoding="utf-8") as dataset_file:
        for line_number, raw_line in enumerate(dataset_file, start=1):
            if not raw_line.strip():
                continue
            try:
                item = json.loads(raw_line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Некорректный JSON в follow-up evaluation, строка {line_number}."
                ) from error
            if not isinstance(item, dict):
                raise ValueError(
                    f"Строка {line_number} follow-up evaluation должна быть объектом."
                )
            messages = item.get("messages")
            prompt = item.get("prompt")
            completion = item.get("completion")
            if (
                not isinstance(messages, list)
                or len(messages) != 2
                or not isinstance(prompt, str)
                or not prompt.strip()
                or not isinstance(completion, str)
                or not completion.strip()
            ):
                raise ValueError(
                    "Каждый follow-up пример должен содержать два сообщения, "
                    "prompt и completion: строка "
                    f"{line_number}."
                )
            for message in messages:
                if not isinstance(message, dict):
                    raise ValueError(
                        f"Сообщение в строке {line_number} должно быть объектом."
                    )
                if message.get("role") not in {"user", "assistant"}:
                    raise ValueError(
                        f"Недопустимая роль в строке {line_number}."
                    )
                if not isinstance(message.get("content"), str):
                    raise ValueError(
                        f"Содержимое сообщения в строке {line_number} должно быть текстом."
                    )
            cases.append(item)

    if not cases:
        raise ValueError("Отложенный follow-up набор не должен быть пустым.")
    return cases


def call_api(
    prompt: str,
    api_url: str,
    conversation_id: str | None = None,
    use_conversation_context: bool = False,
) -> dict[str, Any]:
    """Отправляет один neural-only запрос с выбранным режимом контекста."""
    request_body: dict[str, Any] = {
        "prompt": prompt,
        "response_mode": "experimental",
        "max_tokens": 64,
        "temperature": 0.7,
        "top_k": 1,
        "repetition_penalty": 1.15,
        "no_repeat_ngram_size": 3,
        "decoding": "sample",
        "use_knowledge_base": False,
        "use_conversation_context": use_conversation_context,
    }
    if conversation_id is not None:
        request_body["conversation_id"] = conversation_id
    body = json.dumps(request_body).encode("utf-8")
    request = Request(
        f"{api_url}/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("API вернул не объект JSON.")
    return result


def evaluate_case(
    case: dict[str, Any],
    api_url: str,
    use_context: bool,
) -> tuple[str, str, bool]:
    """Прогоняет начальный вопрос и возвращает ответ на follow-up."""
    messages = case["messages"]
    initial_message = messages[0]
    if not isinstance(initial_message, dict):
        raise ValueError("Начальное сообщение имеет неверный формат.")
    initial_result = call_api(
        str(initial_message["content"]),
        api_url,
        use_conversation_context=False,
    )
    conversation_id = initial_result.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("API не вернул conversation_id для follow-up проверки.")
    followup_result = call_api(
        str(case["prompt"]),
        api_url,
        conversation_id=conversation_id,
        use_conversation_context=use_context,
    )
    response_text = followup_result.get("text")
    if not isinstance(response_text, str):
        raise ValueError("API не вернул текст follow-up ответа.")
    return (
        response_text,
        conversation_id,
        bool(followup_result.get("context_used", False)),
    )


def load_health(api_url: str) -> dict[str, Any]:
    """Читает состояние API перед началом длительной проверки."""
    with urlopen(f"{api_url}/health", timeout=10) as response:
        health = json.loads(response.read().decode("utf-8"))
    if not isinstance(health, dict):
        raise ValueError("/health вернул не объект JSON.")
    return health


def parse_args() -> argparse.Namespace:
    """Читает параметры запуска отложенной проверки."""
    parser = argparse.ArgumentParser(
        description="Проверка follow-up ответов с контекстом и без него."
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help="Адрес локального API для проверки.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=0,
        help="Ограничить число примеров; 0 означает проверить все.",
    )
    return parser.parse_args()


def main() -> None:
    """Сравнивает два режима на одном и том же наборе новых диалогов."""
    arguments = parse_args()
    if arguments.max_cases < 0:
        raise ValueError("max_cases не может быть отрицательным.")
    api_url = arguments.api_url.rstrip("/")
    health = load_health(api_url)
    cases = load_followup_cases()
    if arguments.max_cases:
        cases = cases[: arguments.max_cases]

    context_exact = 0
    control_exact = 0
    context_scores: list[float] = []
    control_scores: list[float] = []
    context_used_count = 0
    improved_count = 0
    equal_count = 0
    worse_count = 0

    print(f"Модель: {health.get('model_version', 'unknown')}")
    print(f"Примеров: {len(cases)}")
    for index, case in enumerate(cases, start=1):
        expected = str(case["completion"])
        context_actual, _, context_used = evaluate_case(
            case,
            api_url,
            use_context=True,
        )
        control_actual, _, _ = evaluate_case(
            case,
            api_url,
            use_context=False,
        )
        context_score = normalized_text_similarity(context_actual, expected)
        control_score = normalized_text_similarity(control_actual, expected)
        context_scores.append(context_score)
        control_scores.append(control_score)
        context_exact += int(context_actual == expected)
        control_exact += int(control_actual == expected)
        context_used_count += int(context_used)
        if context_score > control_score:
            improved_count += 1
        elif context_score == control_score:
            equal_count += 1
        else:
            worse_count += 1

        print(f"\n#{index}. Follow-up: {case['prompt']}")
        print(f"Ожидается: {expected}")
        print(f"С контекстом: {context_actual}")
        print(f"Без контекста: {control_actual}")
        print(
            f"Сходство: {context_score:.4f} / {control_score:.4f}; "
            f"context_used={context_used}"
        )

    context_average = sum(context_scores) / max(1, len(context_scores))
    control_average = sum(control_scores) / max(1, len(control_scores))
    print("\nИтог")
    print(f"С контекстом: точных {context_exact}/{len(cases)}, " f"сходство {context_average:.4f}")
    print(f"Без контекста: точных {control_exact}/{len(cases)}, " f"сходство {control_average:.4f}")
    print(f"context_used=true: {context_used_count}/{len(cases)}")
    print(
        "Изменение по случаям: "
        f"лучше {improved_count}, равно {equal_count}, хуже {worse_count}"
    )


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, OSError, ValueError) as error:
        raise SystemExit(f"Follow-up проверка не запущена: {error}") from error
