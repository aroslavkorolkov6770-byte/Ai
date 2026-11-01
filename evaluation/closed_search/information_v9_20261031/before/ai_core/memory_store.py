"""Локальное хранилище диалогов на SQLite."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class FeedbackAlreadySubmittedError(ValueError):
    """Сообщает, что для одного ответа уже есть добровольная оценка."""


class ConversationStore:
    """Хранит сообщения локально и не отправляет их во внешние сервисы."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Фиксирует транзакцию и всегда освобождает файл SQLite в Windows."""
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat()

    def initialize(self) -> None:
        """Создаёт таблицы при первом запуске приложения."""
        try:
            with self._connection() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS conversations (
                        id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id TEXT NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, id);

                    CREATE TABLE IF NOT EXISTS conversation_lms_contexts (
                        conversation_id TEXT PRIMARY KEY,
                        context TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                    );

                    -- Отдельная таблица не содержит текстов вопросов, ответов,
                    -- идентификатора диалога или координат. В ней хранится
                    -- только добровольная оценка одного технического ответа.
                    CREATE TABLE IF NOT EXISTS response_feedback (
                        response_id TEXT PRIMARY KEY,
                        rating TEXT NOT NULL CHECK (
                            rating IN ('helpful', 'needs_improvement')
                        ),
                        created_at TEXT NOT NULL
                    );
                    """
                )
        except sqlite3.Error as error:
            raise RuntimeError("Не удалось инициализировать память диалогов.") from error

    def create_conversation(self) -> str:
        """Создаёт новый диалог и возвращает его идентификатор."""
        conversation_id = str(uuid.uuid4())
        timestamp = self._timestamp()
        try:
            with self._connection() as connection:
                connection.execute(
                    "INSERT INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
                    (conversation_id, timestamp, timestamp),
                )
        except sqlite3.Error as error:
            raise RuntimeError("Не удалось создать диалог.") from error
        return conversation_id

    @staticmethod
    def _ensure_conversation(
        connection: sqlite3.Connection,
        conversation_id: str,
    ) -> None:
        """Создаёт диалог для переданного идентификатора, если его ещё нет."""
        timestamp = ConversationStore._timestamp()
        connection.execute(
            """
            INSERT OR IGNORE INTO conversations (id, created_at, updated_at)
            VALUES (?, ?, ?)
            """,
            (conversation_id, timestamp, timestamp),
        )

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        """Добавляет одно сообщение пользователя или ассистента."""
        if role not in {"user", "assistant"}:
            raise ValueError("Роль сообщения должна быть user или assistant.")
        if not content.strip():
            raise ValueError("Нельзя сохранить пустое сообщение.")

        timestamp = self._timestamp()
        try:
            with self._connection() as connection:
                self._ensure_conversation(connection, conversation_id)
                connection.execute(
                    """
                    INSERT INTO messages (conversation_id, role, content, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (conversation_id, role, content.strip(), timestamp),
                )
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (timestamp, conversation_id),
                )
        except sqlite3.Error as error:
            raise RuntimeError("Не удалось сохранить сообщение.") from error

    def save_response_feedback(self, response_id: str, rating: str) -> None:
        """Сохраняет одну добровольную оценку без текста переписки.

        ``response_id`` — случайный технический идентификатор, проверенный API
        по подписи. Он не связан в этой таблице с диалогом, вопросом, ответом
        или местоположением пользователя.
        """
        clean_response_id = response_id.strip()
        if not clean_response_id:
            raise ValueError("Нельзя сохранить оценку без идентификатора ответа.")
        if rating not in {"helpful", "needs_improvement"}:
            raise ValueError("Неизвестный тип оценки ответа.")

        try:
            with self._connection() as connection:
                try:
                    connection.execute(
                        """
                        INSERT INTO response_feedback (response_id, rating, created_at)
                        VALUES (?, ?, ?)
                        """,
                        (clean_response_id, rating, self._timestamp()),
                    )
                except sqlite3.IntegrityError as error:
                    raise FeedbackAlreadySubmittedError(
                        "Для этого ответа оценка уже сохранена."
                    ) from error
        except FeedbackAlreadySubmittedError:
            raise
        except sqlite3.Error as error:
            raise RuntimeError("Не удалось сохранить оценку ответа.") from error

    def set_lms_context(self, conversation_id: str, context: str) -> None:
        """Сохраняет подтверждённый LMS-контекст текущего диалога локально.

        Контекст попадает сюда только после безопасного автоматического выбора
        или явного API-запроса. Благодаря этому короткий follow-up вроде
        «А сколько попыток?» не заставляет пользователя заново описывать курс.
        """
        clean_context = context.strip()
        if not clean_context:
            raise ValueError("Нельзя сохранить пустой LMS-контекст.")
        timestamp = self._timestamp()
        try:
            with self._connection() as connection:
                self._ensure_conversation(connection, conversation_id)
                connection.execute(
                    """
                    INSERT INTO conversation_lms_contexts (
                        conversation_id,
                        context,
                        updated_at
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(conversation_id) DO UPDATE SET
                        context = excluded.context,
                        updated_at = excluded.updated_at
                    """,
                    (conversation_id, clean_context, timestamp),
                )
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (timestamp, conversation_id),
                )
        except sqlite3.Error as error:
            raise RuntimeError("Не удалось сохранить LMS-контекст диалога.") from error

    def get_lms_context(self, conversation_id: str) -> str | None:
        """Возвращает последний подтверждённый LMS-контекст или None."""
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT context
                    FROM conversation_lms_contexts
                    WHERE conversation_id = ?
                    """,
                    (conversation_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise RuntimeError("Не удалось прочитать LMS-контекст диалога.") from error
        return str(row["context"]) if row is not None else None

    def list_messages(self, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Возвращает последние сообщения диалога в порядке отображения."""
        if limit < 1 or limit > 1000:
            raise ValueError("Лимит сообщений должен быть от 1 до 1000.")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT role, content, created_at
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (conversation_id, limit),
                ).fetchall()
        except sqlite3.Error as error:
            raise RuntimeError("Не удалось прочитать сообщения.") from error

        return [dict(row) for row in reversed(rows)]
