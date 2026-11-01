"""Интеграционная проверка отдельного LMS-режима локального API."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from ai_core import api_server
from ai_core.memory_store import ConversationStore


class LmsAPITests(unittest.TestCase):
    def test_lms_mode_requires_context_and_returns_trained_policy(self) -> None:
        original_store = api_server.conversation_store
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "lms-api.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.load_reliable_artifacts()

                response = asyncio.run(api_server.generate(api_server.GenerateRequest(
                    prompt="Какой проходной балл?",
                    response_mode="lms",
                    lms_context=(
                        "В тесте для успешного завершения нужно набрать не менее 80%."
                    ),
                )))
                self.assertEqual(response.model, "lms-context-policy-v2.0")
                self.assertEqual(response.response_mode, "lms")
                self.assertEqual(response.intent, "direct_context_answer")
                self.assertTrue(response.context_used)
                self.assertIn("80%", response.text)
                self.assertNotIn("\ufffd", response.text)
            finally:
                api_server.conversation_store = original_store


if __name__ == "__main__":
    unittest.main()
