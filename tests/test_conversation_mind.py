"""Проверяет новый разговорный слой без сети и свободного декодера."""

from __future__ import annotations

import unittest

import torch

from ai_core.conversation_mind import (
    FORMAT_VERSION,
    LABELS,
    ConversationMind,
    compose_conversation_reply,
    response_pool_size,
)
from ai_core.internet_router import RouterFeatureEncoder, RouterNetwork


def _always_plan_mind(plan: str) -> ConversationMind:
    """Создаёт детерминированную policy-сеть для проверки её границ."""
    encoder = RouterFeatureEncoder.fit(
        ["давай спокойно поговорим", "я выбираю важный вариант"],
        max_features=128,
    )
    config = {
        "vocabulary_size": len(encoder.vocabulary),
        "embedding_dim": 8,
        "hidden_dim": 8,
        "classes": len(LABELS),
    }
    network = RouterNetwork(**config)
    with torch.no_grad():
        for parameter in network.parameters():
            parameter.zero_()
        network.layers[-1].bias[LABELS.index(plan)] = 10.0
    return ConversationMind(
        {
            "format_version": FORMAT_VERSION,
            "model_version": "test-conversation-mind",
            "labels": list(LABELS),
            "model_config": config,
            "model_state": network.state_dict(),
            "vocabulary": encoder.vocabulary,
            "idf": encoder.idf,
            "feature_stopwords": sorted(encoder.stopwords),
            "thresholds": {
                "intent_confidence": 0.90,
                "minimum_margin": 0.0,
                "minimum_coverage": 0.0,
            },
        }
    )


class ConversationMindTests(unittest.TestCase):
    """Поведенческие тесты: разговор остаётся живым, а факты не перехватываются."""

    def test_explicit_greeting_is_compact_and_has_no_legacy_topic(self) -> None:
        mind = _always_plan_mind("RELATIONSHIP")
        reply = mind.reply("Привет, как ты?", [], "greeting-session")

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.intent, "greeting")
        self.assertEqual(reply.verbosity, "short")
        self.assertLess(len(reply.text), 220)
        self.assertNotIn("HTML", reply.text)
        self.assertNotIn("CSS", reply.text)
        self.assertNotIn("JavaScript", reply.text)

    def test_factual_request_is_not_intercepted_even_by_confident_model(self) -> None:
        mind = _always_plan_mind("RELATIONSHIP")

        self.assertIsNone(mind.reply("Что такое фотосинтез?", [], "fact-session"))
        self.assertIsNone(mind.reply("Какая завтра погода?", [], "weather-session"))
        self.assertIsNone(
            mind.reply("Привет, сколько сейчас времени?", [], "time-session")
        )

    def test_personal_question_with_relative_time_stays_dialogue_but_weather_does_not(self) -> None:
        """Слово «сегодня» не должно само отправлять личный совет в сеть."""
        mind = _always_plan_mind("RELATIONSHIP")
        personal_reply = mind.reply(
            "Как ты считаешь, стоит ли мне сегодня поговорить с другом?",
            [],
            "today-advice-session",
        )

        self.assertIsNotNone(personal_reply)
        assert personal_reply is not None
        self.assertEqual(personal_reply.intent, "opinion")
        self.assertIsNone(
            mind.reply(
                "Как ты считаешь, какая сегодня погода в Минске?",
                [],
                "today-weather-session",
            )
        )

    def test_everyday_human_replies_do_not_need_a_formal_question(self) -> None:
        mind = _always_plan_mind("RELATIONSHIP")
        tired_reply = mind.reply(
            "Я сегодня устал после работы", [], "tired-session"
        )
        conversation_reply = mind.reply(
            "Поговори со мной немного", [], "talk-session"
        )

        self.assertIsNotNone(tired_reply)
        self.assertIsNotNone(conversation_reply)
        assert tired_reply is not None
        assert conversation_reply is not None
        self.assertEqual(tired_reply.intent, "support")
        self.assertEqual(conversation_reply.intent, "conversation")

    def test_learned_plan_can_help_without_copying_history(self) -> None:
        mind = _always_plan_mind("GUIDE")
        reply = mind.reply(
            "Я запутался в последовательности дел",
            [{"role": "user", "content": "Личная предыдущая формулировка"}],
            "guide-session",
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.intent, "guide")
        self.assertTrue(reply.context_used)
        self.assertNotIn("Личная предыдущая формулировка", reply.text)

    def test_controlled_pool_is_large_and_avoids_recent_exact_repeat(self) -> None:
        variants = {
            compose_conversation_reply("greeting", "Привет", [], f"session-{number}", 1.0).text
            for number in range(48)
        }
        first = compose_conversation_reply("greeting", "Привет", [], "repeat", 1.0)
        second = compose_conversation_reply(
            "greeting",
            "Привет",
            [{"role": "assistant", "content": first.text}],
            "repeat",
            1.0,
        )

        self.assertGreaterEqual(response_pool_size(), 3_000)
        self.assertGreaterEqual(len(variants), 20)
        self.assertNotEqual(first.text, second.text)

    def test_support_reply_is_belonging_and_has_no_url(self) -> None:
        mind = _always_plan_mind("RELATIONSHIP")
        reply = mind.reply("Мне тревожно и тяжело", [], "support-session")

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.intent, "support")
        self.assertEqual(reply.verbosity, "extended")
        self.assertGreater(len(reply.text), 70)
        self.assertNotIn("http", reply.text.casefold())

    def test_personal_advice_question_stays_in_conversation_layer(self) -> None:
        mind = _always_plan_mind("RELATIONSHIP")
        reply = mind.reply(
            "Как мне спокойно поговорить с другом после ссоры?",
            [],
            "advice-session",
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.intent, "advice")
        self.assertEqual(reply.verbosity, "balanced")
        self.assertRegex(reply.text.casefold(), r"конфликт|спокойн")
        self.assertNotIn("http", reply.text.casefold())
        self.assertNotIn("HTML", reply.text)

    def test_positive_personal_progress_gets_a_human_acknowledgement(self) -> None:
        mind = _always_plan_mind("RELATIONSHIP")
        reply = mind.reply(
            "У меня получилось закончить сложную задачу",
            [],
            "celebration-session",
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.intent, "celebration")
        self.assertGreater(len(reply.text), 20)
        self.assertNotIn("http", reply.text.casefold())

    def test_short_continuation_uses_history_without_repeating_it_verbatim(self) -> None:
        """Короткое «продолжим» получает следующий шаг, а не пустую реплику."""
        mind = _always_plan_mind("RELATIONSHIP")
        previous_message = "Мне нужно решить, как распределить время между работой и учёбой."
        reply = mind.reply(
            "Продолжим",
            [{"role": "user", "content": previous_message}],
            "continuation-session",
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.intent, "continuation")
        self.assertTrue(reply.context_used)
        self.assertEqual(reply.verbosity, "balanced")
        self.assertGreater(len(reply.text), 70)
        self.assertRegex(reply.text.casefold(), r"перегруз|ближайш")
        self.assertNotIn(previous_message, reply.text)
        self.assertNotIn("http", reply.text.casefold())

    def test_current_dialogue_length_preference_controls_the_next_reply(self) -> None:
        """Явная просьба о краткости действует только внутри текущей истории."""
        mind = _always_plan_mind("RELATIONSHIP")
        preference_message = "Мне важно, чтобы ответы были короткими."
        reply = mind.reply(
            "Как мне спокойно поговорить с другом после ссоры?",
            [{"role": "user", "content": preference_message}],
            "short-preference-session",
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.intent, "advice")
        self.assertEqual(reply.verbosity, "short")
        self.assertTrue(reply.context_used)
        self.assertLess(len(reply.text), 300)
        self.assertNotIn(preference_message, reply.text)

    def test_current_prompt_can_override_earlier_short_reply_preference(self) -> None:
        """Просьба объяснить подробнее важнее старой настройки длины ответа."""
        mind = _always_plan_mind("RELATIONSHIP")
        reply = mind.reply(
            "Как мне поговорить с другом после ссоры? Расскажи подробнее.",
            [{"role": "user", "content": "Мне важно, чтобы ответы были короткими."}],
            "detail-overrides-short-session",
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.intent, "advice")
        self.assertEqual(reply.verbosity, "extended")
        self.assertGreater(len(reply.text), 100)

    def test_immediate_self_harm_signal_gets_safety_first_reply(self) -> None:
        mind = _always_plan_mind("RELATIONSHIP")
        reply = mind.reply(
            "Мне очень плохо, я хочу навредить себе",
            [{"role": "user", "content": "Ранее пользователь говорил о стрессе."}],
            "safety-session",
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.intent, "safety_support")
        self.assertEqual(reply.verbosity, "extended")
        self.assertIn("в безопасности", reply.text.casefold())
        self.assertNotIn("http", reply.text.casefold())


if __name__ == "__main__":
    unittest.main()
