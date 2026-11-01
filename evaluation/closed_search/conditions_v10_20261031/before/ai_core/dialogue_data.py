"""Загрузка многоходовых примеров для обучения собственной модели."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .conversation_context import build_context, build_model_prompt
    from .training_data import TrainingPair
except ImportError:
    from conversation_context import build_context, build_model_prompt
    from training_data import TrainingPair


@dataclass(frozen=True)
class DialogueExample:
    """Один пример с предыдущими репликами и новым вопросом."""

    messages: tuple[dict[str, str], ...]
    prompt: str
    completion: str


def _parse_messages(raw_messages: Any, line_number: int) -> tuple[dict[str, str], ...]:
    """Проверяет роли и текст каждой предыдущей реплики."""
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ValueError(
            f"В строке {line_number} поле messages должно быть непустым списком."
        )

    messages: list[dict[str, str]] = []
    for message_index, raw_message in enumerate(raw_messages, start=1):
        if not isinstance(raw_message, dict):
            raise ValueError(
                f"В строке {line_number}, сообщение {message_index} "
                "должно быть JSON-объектом."
            )
        role = raw_message.get("role")
        content = raw_message.get("content")
        if role not in {"user", "assistant"}:
            raise ValueError(
                f"В строке {line_number}, сообщение {message_index} "
                "имеет недопустимую роль."
            )
        if not isinstance(content, str) or not content.strip():
            raise ValueError(
                f"В строке {line_number}, сообщение {message_index} "
                "должно содержать непустой content."
            )
        messages.append({"role": role, "content": content.strip()})
    return tuple(messages)


def load_dialogue_examples(path: str | Path) -> list[DialogueExample]:
    """Строго загружает JSONL с многоходовыми вопросами."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Диалоговый датасет не найден: {source}")

    examples: list[DialogueExample] = []
    with source.open("r", encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Некорректный JSON диалогового датасета в строке "
                    f"{line_number}: {error.msg}."
                ) from error

            if not isinstance(item, dict):
                raise ValueError(f"Строка {line_number} должна быть JSON-объектом.")
            prompt = item.get("prompt")
            completion = item.get("completion")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(
                    f"В строке {line_number} отсутствует непустой prompt."
                )
            if not isinstance(completion, str) or not completion.strip():
                raise ValueError(
                    f"В строке {line_number} отсутствует непустой completion."
                )
            examples.append(
                DialogueExample(
                    messages=_parse_messages(item.get("messages"), line_number),
                    prompt=prompt.strip(),
                    completion=completion.strip(),
                )
            )

    if not examples:
        raise ValueError("Диалоговый датасет не содержит примеров.")
    return examples


def format_dialogue_prompt(example: DialogueExample) -> str:
    """Приводит пример к тому же формату, который получает API."""
    context = build_context(example.messages)
    if not context:
        raise ValueError("У диалогового примера не получилось построить контекст.")
    return build_model_prompt(example.prompt, context)


def dialogue_examples_to_pairs(
    examples: list[DialogueExample],
) -> list[TrainingPair]:
    """Преобразует многоходовые примеры в обычные пары для обучения."""
    return [
        TrainingPair(
            prompt=format_dialogue_prompt(example),
            completion=example.completion,
        )
        for example in examples
    ]


def dialogue_dataset_fingerprint(examples: list[DialogueExample]) -> str:
    """Считает воспроизводимый fingerprint многоходового датасета."""
    canonical = [
        {
            "messages": list(example.messages),
            "prompt": example.prompt,
            "completion": example.completion,
        }
        for example in examples
    ]
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
