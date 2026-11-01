"""Проверки добровольной обратной связи без передачи текста чата."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from ai_core import api_server
from ai_core.memory_store import ConversationStore, FeedbackAlreadySubmittedError


class FeedbackStoreTests(unittest.TestCase):
    """Проверяет состав локального хранилища оценки."""

    def test_store_keeps_only_opaque_id_rating_and_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "feedback.sqlite3"
            store = ConversationStore(database_path)
            store.initialize()
            store.save_response_feedback("opaque-response-id", "helpful")

            with self.assertRaises(FeedbackAlreadySubmittedError):
                store.save_response_feedback("opaque-response-id", "needs_improvement")

            connection = sqlite3.connect(database_path)
            try:
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(response_feedback)")
                }
                row = connection.execute(
                    "SELECT response_id, rating, created_at FROM response_feedback"
                ).fetchone()
            finally:
                connection.close()

        self.assertEqual(columns, {"response_id", "rating", "created_at"})
        self.assertEqual(row[0], "opaque-response-id")
        self.assertEqual(row[1], "helpful")
        self.assertTrue(row[2])
        self.assertNotIn("content", columns)
        self.assertNotIn("conversation_id", columns)
        self.assertNotIn("latitude", columns)


class FeedbackApiTests(unittest.TestCase):
    """Проверяет одноразовый token и API без обращения к внешней сети."""

    def test_live_answer_can_receive_one_local_rating(self) -> None:
        original_store = api_server.conversation_store
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "api-feedback.sqlite3"
                )
                api_server.conversation_store.initialize()

                answer = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(prompt="Который час сейчас?")
                    )
                )

                self.assertIsNotNone(answer.feedback_token)
                self.assertGreaterEqual(len(answer.feedback_token or ""), 100)
                saved = asyncio.run(
                    api_server.save_feedback(
                        api_server.FeedbackRequest(
                            feedback_token=answer.feedback_token or "",
                            rating="helpful",
                        )
                    )
                )
                self.assertEqual(saved.status, "saved")
                self.assertEqual(saved.rating, "helpful")

                with self.assertRaises(HTTPException) as duplicate_error:
                    asyncio.run(
                        api_server.save_feedback(
                            api_server.FeedbackRequest(
                                feedback_token=answer.feedback_token or "",
                                rating="helpful",
                            )
                        )
                    )
                self.assertEqual(duplicate_error.exception.status_code, 409)
            finally:
                api_server.conversation_store = original_store

    def test_tampered_or_expired_feedback_token_is_rejected(self) -> None:
        feedback_token = api_server.create_feedback_token()
        tampered_token = f"{feedback_token[:-1]}0"
        if tampered_token == feedback_token:
            tampered_token = f"{feedback_token[:-1]}1"

        with self.assertRaises(HTTPException) as invalid_error:
            asyncio.run(
                api_server.save_feedback(
                    api_server.FeedbackRequest(
                        feedback_token=tampered_token,
                        rating="needs_improvement",
                    )
                )
            )
        self.assertEqual(invalid_error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
