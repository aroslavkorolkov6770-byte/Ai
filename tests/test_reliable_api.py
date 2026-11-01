"""Интеграционная проверка надёжного режима через HTTP API."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from ai_core import api_server
from ai_core.memory_store import ConversationStore
from ai_core.reliable_qa import ABSTAIN


class ReliableAPITests(unittest.TestCase):
    def test_complete_answers_memory_and_followup(self) -> None:
        original_store = api_server.conversation_store
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "api.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.load_reliable_artifacts()
                first = asyncio.run(api_server.generate(api_server.GenerateRequest(
                    prompt="Как сделать кнопку в HTML?",
                    response_mode="reliable",
                    max_tokens=1,
                )))
                self.assertEqual(first.intent, "html_button")
                self.assertGreater(len(first.text), 40)
                self.assertFalse(first.needs_clarification)

                followup = asyncio.run(api_server.generate(api_server.GenerateRequest(
                    prompt="А как сделать её синей?",
                    conversation_id=first.conversation_id,
                    response_mode="reliable",
                )))
                self.assertEqual(followup.intent, "css_background")
                self.assertTrue(followup.context_used)

                history = asyncio.run(api_server.get_messages(first.conversation_id))
                self.assertEqual(len(history["messages"]), 4)

                unknown = asyncio.run(api_server.generate(api_server.GenerateRequest(
                    prompt="Какая сегодня погода на Марсе?",
                    response_mode="reliable",
                )))
                self.assertEqual(unknown.text, ABSTAIN)
                self.assertTrue(unknown.needs_clarification)
            finally:
                api_server.conversation_store = original_store


if __name__ == "__main__":
    unittest.main()
