"""Проверки ограниченной памяти, передаваемой в собственную модель."""

import unittest

from ai_core.conversation_context import (
    build_context,
    build_model_prompt,
    encode_prompt_with_context,
)
from ai_core.tokenizer import CharacterTokenizer


class ConversationContextTests(unittest.TestCase):
    def test_context_formats_only_known_recent_messages(self) -> None:
        messages = [
            {"role": "system", "content": "это не должно попасть в prompt"},
            {"role": "user", "content": "Первый вопрос"},
            {"role": "assistant", "content": "Первый ответ"},
            {"role": "user", "content": "Второй вопрос"},
        ]

        context = build_context(messages, max_messages=2, max_characters=500)

        self.assertNotIn("system", context)
        self.assertNotIn("Первый вопрос", context)
        self.assertIn("Пользователь: Второй вопрос", context)
        self.assertIn("Ассистент: Первый ответ", context)

    def test_context_limit_keeps_newest_message(self) -> None:
        messages = [
            {"role": "user", "content": "старый вопрос " * 20},
            {"role": "assistant", "content": "самый свежий ответ"},
        ]

        context = build_context(messages, max_messages=6, max_characters=40)

        self.assertLessEqual(len(context), 40)
        self.assertIn("Ассистент:", context)
        self.assertTrue(context.endswith("самый свежий ответ"))

    def test_encoding_keeps_current_question_after_context_truncation(self) -> None:
        tokenizer = CharacterTokenizer()
        tokenizer.build(
            [
                "История диалога:\nстарый ответ\nНовый вопрос:\nновый вопрос",
                "новый вопрос",
            ]
        )

        prompt = build_model_prompt("новый вопрос", "Пользователь: старый ответ")
        encoded = encode_prompt_with_context(
            tokenizer,
            "новый вопрос",
            "Пользователь: старый ответ",
            max_prompt_length=20,
        )

        self.assertEqual(prompt.splitlines()[-1], "новый вопрос")
        self.assertLessEqual(len(encoded), 20)
        self.assertTrue(tokenizer.decode(encoded).endswith("новый вопрос"))


if __name__ == "__main__":
    unittest.main()
