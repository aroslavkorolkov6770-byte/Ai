"""Проверяет установленный надёжный режим через настоящий HTTP-сервер."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_core.reliable_qa import ABSTAIN, DEFAULT_ARTIFACT, ReliableQA


def request_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"HTTP-проверка не получила корректный ответ от {url}: {error}") from error


def run(base_url: str, output: Path) -> dict[str, Any]:
    qa = ReliableQA.load(DEFAULT_ARTIFACT)
    allowed_texts = set(qa.answers.values()) | {ABSTAIN}
    health = request_json(f"{base_url}/health")
    checks: list[dict[str, Any]] = []

    def generate(prompt: str, expected: str | None, conversation_id: str | None = None,
                 context_expected: bool = False, max_tokens: int = 64) -> dict[str, Any]:
        response = request_json(
            f"{base_url}/generate",
            {
                "prompt": prompt,
                "conversation_id": conversation_id,
                "response_mode": "reliable",
                "max_tokens": max_tokens,
            },
        )
        passed = (
            response["intent"] == expected
            and response["text"] in allowed_texts
            and bool(response["context_used"]) == context_expected
            and bool(response["needs_clarification"]) == (expected is None)
        )
        checks.append({
            "prompt": prompt,
            "expected": expected,
            "predicted": response["intent"],
            "source": response["source"],
            "context_expected": context_expected,
            "context_used": response["context_used"],
            "complete_answer": response["text"] in allowed_texts,
            "passed": passed,
            "text": response["text"],
        })
        return response

    # max_tokens=1 не должен обрезать каталожный ответ.
    single = generate("Как сделать кнопку в HTML?", "html_button", max_tokens=1)
    if len(single["text"]) <= 40:
        checks[-1]["passed"] = False
        checks[-1]["length_error"] = "Надёжный ответ оказался подозрительно коротким."

    button = generate("Как добавить кнопку в HTML?", "html_button")
    generate("А как сделать её синей?", "css_background", button["conversation_id"], True)

    font = generate("Как изменить размер текста в CSS?", "css_font_size")
    generate("Как сделать синим именно его?", "css_text_color", font["conversation_id"], True)

    timer = generate("Как запустить таймер в JavaScript?", "timer_start")
    generate("Как теперь его остановить?", "timer_cancel", timer["conversation_id"], True)

    storage = generate("Как сохранить данные в localStorage?", "storage_save")
    read = generate(
        "Как теперь прочитать сохранённое значение?",
        "storage_read",
        storage["conversation_id"],
        True,
    )
    generate(
        "Как теперь удалить эту настройку?",
        "storage_remove",
        read["conversation_id"],
        True,
    )

    click = generate("Как создать кнопку в HTML?", "html_button")
    generate("Как повесить обработчик на неё?", "js_click", click["conversation_id"], True)

    changed_topic = generate("Как вставить кнопку в HTML?", "html_button")
    generate(
        "Как добавить изображение в HTML?",
        "html_image",
        changed_topic["conversation_id"],
        False,
    )

    unknown = generate("Какая сегодня погода на Марсе?", None)
    if unknown["text"] != ABSTAIN:
        checks[-1]["passed"] = False
        checks[-1]["clarification_error"] = "Неизвестная тема вернула не стандартное уточнение."

    history = request_json(
        f"{base_url}/conversations/{storage['conversation_id']}/messages"
    )
    memory_passed = len(history["messages"]) == 6
    passed = (
        health.get("default_response_mode") == "reliable"
        and health.get("reliable_model_ready") is True
        and health.get("reliable_dialogue_ready") is True
        and health.get("supported_intents") == 32
        and memory_passed
        and all(check["passed"] for check in checks)
    )
    summary = {
        "api": base_url,
        "checks": len(checks),
        "checks_passed": sum(check["passed"] for check in checks),
        "memory_messages": len(history["messages"]),
        "memory_passed": memory_passed,
        "qa_version": health.get("reliable_model_version"),
        "dialogue_version": health.get("reliable_dialogue_version"),
        "supported_intents": health.get("supported_intents"),
        "live_api_gate_passed": passed,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "live_api_acceptance.json").write_text(
        json.dumps({"summary": summary, "health": health, "checks": checks},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Проверка надёжного режима через HTTP",
        "",
        "Проверка выполнена против реально запущенного локального uvicorn, а не имитации транспорта.",
        "",
        "| Показатель | Значение |",
        "|---|---:|",
        *[f"| {key} | {value} |" for key, value in summary.items()],
        "",
        "## Запросы",
        "",
    ]
    for check in checks:
        lines.extend([
            f"- {check['prompt']}",
            f"  Ожидалось: {check['expected']}; получено: {check['predicted']}; "
            f"контекст: {check['context_used']}; проверка: {check['passed']}.",
            f"  Ответ: {check['text']}",
            "",
        ])
    (output / "LIVE_API_ACCEPTANCE_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not passed:
        raise RuntimeError("Живая HTTP-приёмка не пройдена.")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        run(arguments.base_url.rstrip("/"), arguments.output)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        raise SystemExit(f"HTTP-приёмка остановлена: {error}") from error
