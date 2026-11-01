"""Проверки постоянной локальной идентичности ВатэсAi."""

from __future__ import annotations

import unittest

from ai_core.assistant_identity import ASSISTANT_NAME, assistant_identity_reply


class AssistantIdentityTests(unittest.TestCase):
    """Не даёт изменить имя или creator-ответ случайной маршрутизацией."""

    def test_answers_assistant_name(self) -> None:
        reply = assistant_identity_reply("Как тебя зовут?")

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.intent, "assistant_name")
        self.assertEqual(reply.text, f"Меня зовут {ASSISTANT_NAME}.")

    def test_answers_creator_question_as_clearly_marked_rumour(self) -> None:
        reply = assistant_identity_reply("Кто твой создатель?")

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.intent, "assistant_creator")
        self.assertIn("не знаю", reply.text.casefold())
        self.assertIn("по слухам", reply.text.casefold())
        self.assertIn("Гелендваген", reply.text)

    def test_does_not_intercept_question_about_someone_else(self) -> None:
        self.assertIsNone(assistant_identity_reply("Кто создал OpenAI?"))


if __name__ == "__main__":
    unittest.main()
