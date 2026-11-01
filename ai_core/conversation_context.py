"""Подготовка небольшого контекста предыдущих сообщений для модели."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


DEFAULT_MAX_MESSAGES = 6
DEFAULT_MAX_CHARACTERS = 1600
_ROLE_LABELS = {"user": "Пользователь", "assistant": "Ассистент"}


def build_context(
    messages: Iterable[Mapping[str, Any]],
    *,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    max_characters: int = DEFAULT_MAX_CHARACTERS,
) -> str:
    """Возвращает ограниченную историю в понятном для модели формате.

    В контекст попадают только последние сообщения с известными ролями.
    Ограничения нужны, чтобы история не вытеснила текущий вопрос из маленького
    окна модели и не превратилась в бесконтрольно растущий текстовый буфер.
    """
    if max_messages < 1:
        raise ValueError("Количество сообщений в контексте должно быть положительным.")
    if max_characters < 1:
        raise ValueError("Размер контекста должен быть положительным.")

    formatted: list[str] = []
    for message in list(messages)[-max_messages:]:
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role", "")).strip()
        content = str(message.get("content", "")).strip()
        label = _ROLE_LABELS.get(role)
        if label is None or not content:
            continue
        # Переносы строк из ответа не должны ломать простой формат ролей.
        compact_content = " ".join(content.split())
        formatted.append(f"{label}: {compact_content}")

    if not formatted:
        return ""

    # Собираем сообщения с конца, чтобы при нехватке места всегда сохранялся
    # самый свежий фрагмент диалога.
    selected: list[str] = []
    used_characters = 0
    for line in reversed(formatted):
        separator_length = 1 if selected else 0
        available = max_characters - used_characters - separator_length
        if available <= 0:
            break
        if len(line) <= available:
            selected.insert(0, line)
            used_characters += separator_length + len(line)
            continue

        if not selected:
            label, separator, content = line.partition(": ")
            if not separator or available <= len(label) + 2:
                selected.insert(0, line[:available])
            else:
                content_length = available - len(label) - 2
                selected.insert(0, f"{label}: {content[-content_length:]}")
            used_characters = max_characters
        break

    return "\n".join(selected)


def _context_prefix(context: str) -> str:
    """Формирует нейтральную разметку перед текущим вопросом."""
    return f"История диалога:\n{context.strip()}\nНовый вопрос:\n"


def build_model_prompt(current_prompt: str, context: str = "") -> str:
    """Объединяет историю и новый вопрос для входа в собственную модель."""
    clean_prompt = current_prompt.strip()
    if not clean_prompt:
        raise ValueError("Текущий вопрос не может быть пустым.")
    clean_context = context.strip()
    if not clean_context:
        return clean_prompt
    return f"{_context_prefix(clean_context)}{clean_prompt}"


def encode_prompt_with_context(
    tokenizer: Any,
    current_prompt: str,
    context: str,
    *,
    max_prompt_length: int,
) -> list[int]:
    """Кодирует prompt, сохраняя текущий вопрос при обрезке контекста.

    Для символьного токенизатора один символ почти равен одному токену, поэтому
    обычная обрезка начала строки могла бы удалить именно новый вопрос. Здесь
    сначала сохраняется хвост текущего вопроса, а оставшееся место занимает
    самый свежий хвост истории.
    """
    if max_prompt_length < 2:
        raise ValueError("Окно prompt должно содержать минимум два токена.")

    prompt_text = build_model_prompt(current_prompt, context)
    prompt_ids = tokenizer.encode(prompt_text, add_bos=True)
    if len(prompt_ids) <= max_prompt_length:
        return prompt_ids

    content_budget = max_prompt_length - 1
    current_ids = tokenizer.encode(current_prompt.strip(), add_bos=False)
    if len(current_ids) >= content_budget:
        return [tokenizer.bos_id] + current_ids[-content_budget:]

    context_budget = content_budget - len(current_ids)
    context_ids = tokenizer.encode(
        _context_prefix(context.strip()),
        add_bos=False,
    )
    return [tokenizer.bos_id] + context_ids[-context_budget:] + current_ids
