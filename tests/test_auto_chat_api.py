"""Интеграция единого автоматического маршрута браузерного чата."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

from ai_core import api_server
from ai_core.conversation_mind import ConversationMindReply
from ai_core.internet_search import WebAnswer
from ai_core.lms_knowledge import LmsKnowledgeIndex
from ai_core.memory_store import ConversationStore
from ai_core.reliable_qa import normalize


def entry(context: str) -> dict:
    """Формирует минимальный контекст для изолированной API-проверки."""
    return {
        "context": context,
        "context_id": hashlib.sha256(
            normalize(context).encode("utf-8")
        ).hexdigest(),
        "questions": ["Какой проходной балл?"],
        "task_types": ["direct_context_answer"],
    }


def test_index(entries: list[dict]) -> LmsKnowledgeIndex:
    """Создаёт компактный индекс без обращения к пользовательскому датасету."""
    return LmsKnowledgeIndex.from_payload(
        {
            "format_version": 1,
            "source": "test",
            "source_records": len(entries),
            "context_groups": len(entries),
            "entries": entries,
        }
    )


class _FakeInternetSearch:
    """Исключает реальную сеть при проверке автоматического маршрута."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def answer(self, query: str, *arguments: object) -> WebAnswer:
        del arguments
        self.queries.append(query)
        return WebAnswer(
            text="Короткий ответ из открытого источника.",
            results=(),
        )


class _AlwaysSearchWebDialoguePolicy:
    """Заглушка policy, показывающая точку интеграции без реальной сети."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def should_search(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return True


class _FixedConversationMind:
    """Заглушка нового мозга для проверки приоритета над старым fallback."""

    version = "test-conversation-mind"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def reply(
        self,
        prompt: str,
        messages: list[dict[str, object]],
        conversation_id: str,
    ) -> ConversationMindReply | None:
        del messages
        self.calls.append((prompt, conversation_id))
        if prompt != "Привет":
            return None
        return ConversationMindReply(
            text="Новый разговорный слой ответил коротко и по делу.",
            intent="greeting",
            confidence=1.0,
            context_used=False,
            verbosity="short",
        )


class _HistoryRecordingConversationMind:
    """Проверяет передачу текущей истории в новый разговорный слой."""

    version = "test-history-conversation-mind"

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, object]], str]] = []

    def reply(
        self,
        prompt: str,
        messages: list[dict[str, object]],
        conversation_id: str,
    ) -> ConversationMindReply | None:
        self.calls.append((prompt, messages, conversation_id))
        if prompt != "Продолжим":
            return None
        return ConversationMindReply(
            text="Продолжаю от уже названной настройки этого разговора.",
            intent="continuation",
            confidence=1.0,
            context_used=bool(messages),
            verbosity="short",
        )


class AutomaticChatAPITests(unittest.TestCase):
    def test_auto_route_prefers_new_conversation_mind_for_normal_dialogue(self) -> None:
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        original_mind = api_server.conversation_mind
        original_mind_error = api_server.conversation_mind_load_error
        fake_search = _FakeInternetSearch()
        fake_mind = _FixedConversationMind()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "conversation-mind.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                api_server.conversation_mind = fake_mind
                api_server.conversation_mind_load_error = None

                response = asyncio.run(
                    api_server.generate(api_server.GenerateRequest(prompt="Привет"))
                )

                self.assertEqual(response.source, "conversation-mind")
                self.assertEqual(response.model, fake_mind.version)
                self.assertEqual(response.intent, "greeting")
                self.assertEqual(fake_search.queries, [])
                self.assertEqual(len(fake_mind.calls), 1)
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search
                api_server.conversation_mind = original_mind
                api_server.conversation_mind_load_error = original_mind_error

    def test_auto_route_passes_current_history_to_conversation_mind(self) -> None:
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        original_mind = api_server.conversation_mind
        original_mind_error = api_server.conversation_mind_load_error
        fake_search = _FakeInternetSearch()
        fake_mind = _HistoryRecordingConversationMind()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "conversation-history.sqlite3"
                )
                api_server.conversation_store.initialize()
                conversation_id = api_server.conversation_store.create_conversation()
                api_server.conversation_store.add_message(
                    conversation_id,
                    "user",
                    "Мне важно, чтобы ответы были короткими.",
                )
                api_server.conversation_store.add_message(
                    conversation_id,
                    "assistant",
                    "Принял настройку длины ответа.",
                )
                api_server.internet_search = fake_search
                api_server.conversation_mind = fake_mind
                api_server.conversation_mind_load_error = None

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Продолжим",
                            conversation_id=conversation_id,
                        )
                    )
                )

                self.assertEqual(response.source, "conversation-mind")
                self.assertEqual(response.intent, "continuation")
                self.assertTrue(response.context_used)
                self.assertEqual(fake_search.queries, [])
                self.assertEqual(len(fake_mind.calls), 1)
                prompt, history, received_conversation_id = fake_mind.calls[0]
                self.assertEqual(prompt, "Продолжим")
                self.assertEqual(received_conversation_id, conversation_id)
                self.assertEqual(
                    [message["content"] for message in history],
                    [
                        "Мне важно, чтобы ответы были короткими.",
                        "Принял настройку длины ответа.",
                    ],
                )
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search
                api_server.conversation_mind = original_mind
                api_server.conversation_mind_load_error = original_mind_error

    def test_auto_route_answers_normal_greeting_without_search_or_legacy_topic(self) -> None:
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        fake_search = _FakeInternetSearch()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "normal-dialogue.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(prompt="Привет, как ты?")
                    )
                )

                self.assertEqual(response.response_mode, "auto")
                self.assertEqual(response.source, "conversation-direct")
                self.assertFalse(response.needs_clarification)
                self.assertEqual(fake_search.queries, [])
                self.assertNotIn("HTML", response.text)
                self.assertNotIn("CSS", response.text)
                self.assertNotIn("JavaScript", response.text)
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search

    def test_auto_route_uses_local_qa_and_ignores_lms_index(self) -> None:
        original_store = api_server.conversation_store
        original_index = api_server.lms_knowledge
        original_search = api_server.internet_search
        fake_search = _FakeInternetSearch()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "auto-chat.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.load_reliable_artifacts()
                api_server.internet_search = fake_search
                web_response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Как сделать кнопку в HTML?"
                        )
                    )
                )
                self.assertEqual(web_response.response_mode, "auto")
                self.assertEqual(web_response.intent, "html_button")
                self.assertFalse(web_response.needs_clarification)

                # Даже заполненный старый индекс не участвует в response_mode=auto.
                api_server.lms_knowledge = test_index(
                    [
                        entry(
                            (
                                "Курс «Охрана труда». Роль: специалист. "
                                "Доступ к материалам действует 45 дней. "
                                "Для успешного завершения нужно набрать не менее 80%. "
                                "На итоговый тест даётся 3 попытки."
                            )
                        ),
                        entry(
                            (
                                "Курс «Охрана труда». Роль: руководитель. "
                                "Доступ к материалам действует 30 дней. "
                                "Для успешного завершения нужно набрать не менее 70%. "
                                "На итоговый тест даётся 2 попытки."
                            )
                        ),
                    ]
                )

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Какой проходной балл?"
                        )
                    )
                )

                self.assertEqual(response.response_mode, "auto")
                self.assertEqual(response.source, "internet-search-auto")
                self.assertFalse(response.context_used)
                self.assertEqual(fake_search.queries, ["Какой проходной балл?"])
                self.assertNotIn("LMS", response.text)
                self.assertNotIn("80%", response.text)
            finally:
                api_server.conversation_store = original_store
                api_server.lms_knowledge = original_index
                api_server.internet_search = original_search

    def test_auto_route_can_use_verified_web_dialogue_policy(self) -> None:
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        original_router = api_server.internet_router
        original_policy = api_server.web_dialogue_policy
        fake_search = _FakeInternetSearch()
        fake_policy = _AlwaysSearchWebDialoguePolicy()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "auto-policy.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                # Отключаем прежний learned-router: поиск должен быть вызван
                # именно новым policy-слоем, а не совпадением эвристики.
                api_server.internet_router = None
                api_server.web_dialogue_policy = fake_policy

                prompt = "Мне интересно влияние городской среды на здоровье"
                response = asyncio.run(
                    api_server.generate(api_server.GenerateRequest(prompt=prompt))
                )

                self.assertEqual(response.source, "internet-search-auto-policy")
                self.assertEqual(fake_policy.prompts, [prompt])
                self.assertEqual(fake_search.queries, [prompt])
                self.assertFalse(response.needs_clarification)
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search
                api_server.internet_router = original_router
                api_server.web_dialogue_policy = original_policy

    def test_auto_route_does_not_show_lms_clarification_for_conflicting_rules(self) -> None:
        original_store = api_server.conversation_store
        original_index = api_server.lms_knowledge
        original_search = api_server.internet_search
        fake_search = _FakeInternetSearch()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "auto-chat-ambiguous.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.load_reliable_artifacts()
                api_server.internet_search = fake_search
                api_server.lms_knowledge = test_index(
                    [
                        entry(
                            (
                                "Курс «Охрана труда». "
                                "Для завершения нужно набрать не менее 80%."
                            )
                        ),
                        entry(
                            (
                                "Курс «Охрана труда». "
                                "Для завершения нужно набрать не менее 70%."
                            )
                        ),
                    ]
                )

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Какой проходной балл?"
                        )
                    )
                )

                self.assertEqual(response.response_mode, "auto")
                self.assertEqual(response.source, "internet-search-auto")
                self.assertFalse(response.needs_clarification)
                self.assertEqual(fake_search.queries, ["Какой проходной балл?"])
                self.assertNotIn("80%", response.text)
                self.assertNotIn("70%", response.text)
                self.assertNotIn("LMS", response.text)
            finally:
                api_server.conversation_store = original_store
                api_server.lms_knowledge = original_index
                api_server.internet_search = original_search

    def test_identity_route_precedes_internet_and_conversation_models(self) -> None:
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        original_mind = api_server.conversation_mind
        original_mind_error = api_server.conversation_mind_load_error
        fake_search = _FakeInternetSearch()
        fake_mind = _FixedConversationMind()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "identity.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                api_server.conversation_mind = fake_mind
                api_server.conversation_mind_load_error = None

                name_response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Как тебя зовут?",
                            use_internet=True,
                        )
                    )
                )
                creator_response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Кто создал ВатэсAi?",
                            use_internet=True,
                        )
                    )
                )

                self.assertEqual(name_response.source, "assistant-identity")
                self.assertEqual(name_response.intent, "assistant_name")
                self.assertIn("ВатэсAi", name_response.text)
                self.assertEqual(creator_response.source, "assistant-identity")
                self.assertEqual(creator_response.intent, "assistant_creator")
                self.assertIn("по слухам", creator_response.text.casefold())
                self.assertEqual(fake_search.queries, [])
                self.assertEqual(fake_mind.calls, [])
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search
                api_server.conversation_mind = original_mind
                api_server.conversation_mind_load_error = original_mind_error


if __name__ == "__main__":
    unittest.main()
