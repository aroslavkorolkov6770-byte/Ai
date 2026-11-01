"""Создаёт локальный индекс LMS-знаний для автоматического режима чата."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_core.lms_knowledge import DEFAULT_LMS_KNOWLEDGE_INDEX, write_knowledge_index


def main() -> None:
    """Строит индекс из проверенных JSONL и выводит короткий аудит результата."""
    payload = write_knowledge_index()
    print(f"Индекс сохранён: {DEFAULT_LMS_KNOWLEDGE_INDEX}")
    print(f"Контекстных групп: {payload['context_groups']}")
    print(f"Уникальных пар вопрос+контекст: {payload['source_records']}")


if __name__ == "__main__":
    main()
