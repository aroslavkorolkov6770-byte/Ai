"""Проверяет автоматический интернет-маршрут единого чата."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from ai_core import api_server
from ai_core.internet_search import WebAnswer
from ai_core.memory_store import ConversationStore


class _FakeInternetSearch:
    """Заменяет сеть в тесте и сохраняет отправленный поисковый запрос."""

    def __init__(self, answer_text: str | None = None) -> None:
        self.queries: list[str] = []
        self.location_hints: list[str | None] = []
        self.day_hints: list[str | None] = []
        self.forced_weather: list[bool] = []
        self.coordinates_hints: list[tuple[float, float] | None] = []
        self.weather_question_hints: list[str | None] = []
        self.answer_text = answer_text or (
            "По найденным открытым источникам:\n"
            "1. В Минске сегодня переменная облачность.\n"
            "Источники:\n1. Пример\nhttps://example.org/weather"
        )

    def answer(
        self,
        query: str,
        location_hint: str | None = None,
        day_hint: str | None = None,
        force_weather: bool = False,
        coordinates_hint: tuple[float, float] | None = None,
        weather_question_hint: str | None = None,
    ) -> WebAnswer:
        self.queries.append(query)
        self.location_hints.append(location_hint)
        self.day_hints.append(day_hint)
        self.forced_weather.append(force_weather)
        self.coordinates_hints.append(coordinates_hint)
        self.weather_question_hints.append(weather_question_hint)
        return WebAnswer(
            text=self.answer_text,
            results=(),
        )


class _FakeInternetRouter:
    """Имитирует положительное решение обученного маршрутизатора."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def should_use_web(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return True


class AutomaticInternetAPITests(unittest.TestCase):
    def test_auto_route_uses_learned_router_without_explicit_marker(self) -> None:
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        original_router = api_server.internet_router
        fake_search = _FakeInternetSearch()
        fake_router = _FakeInternetRouter()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "learned-router.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                api_server.internet_router = fake_router

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Кто выиграл вчерашний матч между «Севером» и «Маяком»?"
                        )
                    )
                )

                self.assertEqual(response.source, "internet-search-auto")
                self.assertEqual(
                    fake_router.prompts,
                    ["Кто выиграл вчерашний матч между «Севером» и «Маяком»?"],
                )
                self.assertEqual(
                    fake_search.queries,
                    ["Кто выиграл вчерашний матч между «Севером» и «Маяком»?"],
                )
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search
                api_server.internet_router = original_router

    def test_auto_web_route_returns_tool_result_not_action_promise(self) -> None:
        """Web-route передаёт человеку финальный факт, а не текст действия."""
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        original_router = api_server.internet_router
        fake_search = _FakeInternetSearch(answer_text="Проверенный краткий вывод.")
        fake_router = _FakeInternetRouter()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "web-final-result.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                api_server.internet_router = fake_router

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Кто выиграл вчерашний матч между «Севером» и «Маяком»?"
                        )
                    )
                )

                self.assertEqual(response.source, "internet-search-auto")
                self.assertEqual(response.text, "Проверенный краткий вывод.")
                self.assertNotRegex(
                    response.text.casefold(),
                    r"уточню|покажу.*после|проверю",
                )
                self.assertEqual(
                    fake_search.queries,
                    ["Кто выиграл вчерашний матч между «Севером» и «Маяком»?"],
                )
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search
                api_server.internet_router = original_router

    def test_ambiguous_pickup_point_hours_question_clarifies_without_web_search(self) -> None:
        """Без конкретного ПВЗ нельзя заявлять об ошибке источника или искать."""
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        original_router = api_server.internet_router
        fake_search = _FakeInternetSearch()
        fake_router = _FakeInternetRouter()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "pickup-point-clarification.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                api_server.internet_router = fake_router

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Узнай, работает ли пункт выдачи сегодня. Не придумывай, если источник не отвечает."
                        )
                    )
                )

                self.assertEqual(response.source, "web-context-clarification")
                self.assertTrue(response.needs_clarification)
                self.assertEqual(
                    response.text,
                    "Уточни, какой именно пункт выдачи и в каком городе или по какому адресу.",
                )
                self.assertEqual(fake_search.queries, [])
                self.assertEqual(fake_router.prompts, [])
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search
                api_server.internet_router = original_router

    def test_vague_current_fact_clarifies_before_router_or_web_search(self) -> None:
        """Неопределённый матч не превращается в случайный поиск по сети."""
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        original_router = api_server.internet_router
        fake_search = _FakeInternetSearch()
        fake_router = _FakeInternetRouter()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "generic-clarification.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                api_server.internet_router = fake_router

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Кто выиграл вчерашний матч?"
                        )
                    )
                )

                self.assertEqual(response.source, "context-clarification")
                self.assertEqual(
                    response.text,
                    "Какой матч: команды, турнир или вид спорта?",
                )
                self.assertTrue(response.needs_clarification)
                self.assertEqual(fake_search.queries, [])
                self.assertEqual(fake_router.prompts, [])
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search
                api_server.internet_router = original_router

    def test_current_fact_followup_uses_web_only_after_its_own_clarification(self) -> None:
        """Деталь во втором ходе дополняет только непосредственно заданный вопрос."""
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        original_router = api_server.internet_router
        fake_search = _FakeInternetSearch(answer_text="Проверенный результат матча.")
        fake_router = _FakeInternetRouter()
        initial_prompt = "Кто выиграл вчерашний матч?"
        clarification = "Какой матч: команды, турнир или вид спорта?"
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "generic-followup.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                api_server.internet_router = fake_router
                conversation_id = api_server.conversation_store.create_conversation()
                api_server.conversation_store.add_message(
                    conversation_id,
                    "user",
                    initial_prompt,
                )
                api_server.conversation_store.add_message(
                    conversation_id,
                    "assistant",
                    clarification,
                )

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Матч «Север» — «Маяк» в финале Кубка.",
                            conversation_id=conversation_id,
                        )
                    )
                )

                self.assertEqual(response.source, "internet-search-auto")
                self.assertEqual(response.text, "Проверенный результат матча.")
                self.assertEqual(
                    fake_search.queries,
                    [
                        "Кто выиграл вчерашний матч? Матч «Север» — «Маяк» в финале Кубка."
                    ],
                )
                self.assertEqual(fake_router.prompts, [])
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search
                api_server.internet_router = original_router

    def test_weather_without_location_clarifies_before_web_search(self) -> None:
        """Погода без города не использует сеть и не придумывает место."""
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        original_router = api_server.internet_router
        fake_search = _FakeInternetSearch()
        fake_router = _FakeInternetRouter()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "weather-clarification.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                api_server.internet_router = fake_router

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(prompt="Какая завтра погода?")
                    )
                )

                self.assertEqual(response.source, "weather-context-clarification")
                self.assertTrue(response.needs_clarification)
                self.assertEqual(fake_search.queries, [])
                self.assertEqual(fake_router.prompts, [])
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search
                api_server.internet_router = original_router

    def test_weather_location_followup_uses_the_immediate_weather_question(self) -> None:
        """Город во втором ходе продолжает только последнее weather-уточнение."""
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        fake_search = _FakeInternetSearch(answer_text="Проверенный прогноз.")
        initial_prompt = "Какая завтра погода?"
        clarification = (
            "Чтобы назвать погоду, укажите город, село или другой населённый пункт. "
            "Например: «Какая завтра погода в Старом Осколе?»"
        )
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "weather-location-followup.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                conversation_id = api_server.conversation_store.create_conversation()
                api_server.conversation_store.add_message(
                    conversation_id,
                    "user",
                    initial_prompt,
                )
                api_server.conversation_store.add_message(
                    conversation_id,
                    "assistant",
                    clarification,
                )

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="В Минске.",
                            conversation_id=conversation_id,
                        )
                    )
                )

                self.assertEqual(response.source, "internet-search-auto")
                self.assertEqual(response.text, "Проверенный прогноз.")
                self.assertEqual(fake_search.queries, [initial_prompt])
                self.assertEqual(fake_search.location_hints, ["Минске"])
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search

    def test_pickup_point_followup_uses_web_after_user_supplies_location(self) -> None:
        """Следующее сообщение с городом и адресом продолжает тот же web-intent."""
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        fake_search = _FakeInternetSearch(answer_text="Проверенный график работы.")
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "pickup-point-followup.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                conversation_id = api_server.conversation_store.create_conversation()
                api_server.conversation_store.add_message(
                    conversation_id,
                    "user",
                    "Узнай, работает ли пункт выдачи сегодня. Не придумывай, если источник не отвечает.",
                )
                api_server.conversation_store.add_message(
                    conversation_id,
                    "assistant",
                    "Уточни, какой именно пункт выдачи и в каком городе или по какому адресу.",
                )

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Это пункт выдачи в Минске на улице Примерной, 5.",
                            conversation_id=conversation_id,
                        )
                    )
                )

                self.assertEqual(response.source, "internet-search-auto")
                self.assertEqual(response.text, "Проверенный график работы.")
                self.assertEqual(
                    fake_search.queries,
                    [
                        "Узнай, работает ли пункт выдачи сегодня. Не придумывай, если источник не отвечает. Это пункт выдачи в Минске на улице Примерной, 5."
                    ],
                )
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search

    def test_specific_pickup_point_question_uses_web_without_router_guess(self) -> None:
        """Полный адрес сразу даёт route достаточно данных для безопасного поиска."""
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        original_router = api_server.internet_router
        fake_search = _FakeInternetSearch(answer_text="Проверенный график работы.")
        fake_router = _FakeInternetRouter()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "pickup-point-direct.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                api_server.internet_router = fake_router

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="До скольки сегодня работает пункт выдачи в Минске на улице Примерной, 5?"
                        )
                    )
                )

                self.assertEqual(response.source, "internet-search-auto")
                self.assertEqual(response.text, "Проверенный график работы.")
                self.assertEqual(
                    fake_search.queries,
                    [
                        "До скольки сегодня работает пункт выдачи в Минске на улице Примерной, 5?"
                    ],
                )
                self.assertEqual(fake_router.prompts, [])
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search
                api_server.internet_router = original_router

    def test_browser_location_requires_complete_coordinate_pair(self) -> None:
        with self.assertRaises(ValidationError):
            api_server.GenerateRequest(
                prompt="Какая погода?",
                latitude=53.9,
            )

    def test_auto_weather_detail_followup_uses_weather_route(self) -> None:
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        fake_search = _FakeInternetSearch()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "auto-weather-detail.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                conversation_id = api_server.conversation_store.create_conversation()
                api_server.conversation_store.add_message(
                    conversation_id,
                    "user",
                    "Какая завтра погода в Старом Осколе?",
                )
                api_server.conversation_store.add_message(
                    conversation_id,
                    "assistant",
                    "Завтрашний прогноз без ссылок.",
                )

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="А со скольки до скольки стоит дождь?",
                            conversation_id=conversation_id,
                        )
                    )
                )

                self.assertEqual(response.source, "internet-search-auto")
                self.assertEqual(
                    fake_search.queries,
                    ["А со скольки до скольки стоит дождь?"],
                )
                self.assertEqual(fake_search.location_hints, ["Старом Осколе"])
                self.assertEqual(fake_search.day_hints, ["tomorrow"])
                self.assertEqual(fake_search.forced_weather, [False])
                self.assertEqual(fake_search.coordinates_hints, [None])
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search

    def test_auto_weather_followup_reuses_city_from_current_dialogue(self) -> None:
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        fake_search = _FakeInternetSearch()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "auto-weather-followup.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                conversation_id = api_server.conversation_store.create_conversation()
                api_server.conversation_store.add_message(
                    conversation_id,
                    "user",
                    "Какая погода в Старом Осколе сегодня?",
                )
                api_server.conversation_store.add_message(
                    conversation_id,
                    "assistant",
                    "Предыдущий проверенный ответ о погоде.",
                )

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Какая завтра погода?",
                            conversation_id=conversation_id,
                        )
                    )
                )

                self.assertEqual(response.source, "internet-search-auto")
                self.assertEqual(fake_search.queries, ["Какая завтра погода?"])
                self.assertEqual(fake_search.location_hints, ["Старом Осколе"])
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search

    def test_weather_compact_time_followup_returns_tool_result_not_action_promise(self) -> None:
        """Короткое ограничение формата сохраняет weather-intent и live-ответ."""
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        fake_search = _FakeInternetSearch(answer_text="19:32")
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "weather-compact-time.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                conversation_id = api_server.conversation_store.create_conversation()
                api_server.conversation_store.add_message(
                    conversation_id,
                    "user",
                    "Я буду в Калуге. Во сколько сегодня закат?",
                )
                api_server.conversation_store.add_message(
                    conversation_id,
                    "assistant",
                    "Сегодня в Калуге закат в 19:32.",
                )

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="И напиши только время.",
                            conversation_id=conversation_id,
                        )
                    )
                )

                self.assertEqual(response.source, "internet-search-auto")
                self.assertEqual(response.text, "19:32")
                self.assertNotIn("провер", response.text.casefold())
                self.assertEqual(fake_search.queries, ["И напиши только время."])
                self.assertEqual(fake_search.location_hints, ["Калуге"])
                self.assertEqual(fake_search.day_hints, ["today"])
                self.assertEqual(fake_search.forced_weather, [True])
                self.assertEqual(
                    fake_search.weather_question_hints,
                    ["Я буду в Калуге. Во сколько сегодня закат?"],
                )
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search

    def test_non_weather_question_does_not_reuse_weather_context(self) -> None:
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        original_router = api_server.internet_router
        original_policy = api_server.web_dialogue_policy
        fake_search = _FakeInternetSearch()
        fake_router = _FakeInternetRouter()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "non-weather-after-forecast.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search
                api_server.internet_router = fake_router
                api_server.web_dialogue_policy = None
                conversation_id = api_server.conversation_store.create_conversation()
                api_server.conversation_store.add_message(
                    conversation_id,
                    "user",
                    "Какая завтра погода в Старом Осколе?",
                )
                api_server.conversation_store.add_message(
                    conversation_id,
                    "assistant",
                    "Завтрашний прогноз без ссылок.",
                )

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Во сколько начинается концерт?",
                            conversation_id=conversation_id,
                        )
                    )
                )

                self.assertEqual(response.source, "internet-search-auto")
                self.assertEqual(
                    fake_search.queries,
                    ["Во сколько начинается концерт?"],
                )
                self.assertEqual(fake_search.location_hints, [None])
                self.assertEqual(fake_search.day_hints, [None])
                self.assertEqual(fake_search.forced_weather, [False])
                self.assertEqual(fake_search.coordinates_hints, [None])
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search
                api_server.internet_router = original_router
                api_server.web_dialogue_policy = original_policy

    def test_auto_weather_without_city_uses_browser_coordinates_only_for_request(self) -> None:
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        fake_search = _FakeInternetSearch()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "auto-weather-location.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Будет ли завтра дождь?",
                            latitude=53.9,
                            longitude=27.56,
                            location_accuracy_m=42,
                        )
                    )
                )
                saved_messages = api_server.conversation_store.list_messages(
                    response.conversation_id
                )

                self.assertEqual(response.source, "internet-search-auto")
                self.assertEqual(fake_search.queries, ["Будет ли завтра дождь?"])
                self.assertEqual(fake_search.location_hints, [None])
                self.assertEqual(fake_search.day_hints, [None])
                self.assertEqual(fake_search.forced_weather, [False])
                self.assertEqual(fake_search.coordinates_hints, [(53.9, 27.56)])
                self.assertNotIn("53.9", " ".join(message["content"] for message in saved_messages))
                self.assertNotIn("27.56", " ".join(message["content"] for message in saved_messages))
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search

    def test_auto_route_answers_current_calendar_without_network(self) -> None:
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        fake_search = _FakeInternetSearch()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "auto-temporal.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(prompt="Который час сейчас?")
                    )
                )

                self.assertEqual(response.source, "local-temporal-context")
                self.assertEqual(response.model, "local-temporal-context-v1")
                self.assertEqual(response.intent, "current_temporal_context")
                self.assertRegex(response.text, r"^Сейчас \d{2}:\d{2} по минскому времени\.$")
                self.assertEqual(fake_search.queries, [])
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search

    def test_auto_route_uses_source_grounded_answer_for_current_question(self) -> None:
        original_store = api_server.conversation_store
        original_search = api_server.internet_search
        fake_search = _FakeInternetSearch()
        with tempfile.TemporaryDirectory() as directory:
            try:
                api_server.conversation_store = ConversationStore(
                    Path(directory) / "auto-internet.sqlite3"
                )
                api_server.conversation_store.initialize()
                api_server.internet_search = fake_search

                response = asyncio.run(
                    api_server.generate(
                        api_server.GenerateRequest(
                            prompt="Какая погода в Минске сегодня?"
                        )
                    )
                )

                self.assertEqual(response.source, "internet-search-auto")
                self.assertEqual(response.model, "source-grounded-web-v1")
                self.assertEqual(response.intent, "internet_answer")
                self.assertIn("переменная облачность", response.text)
                self.assertEqual(fake_search.queries, ["Какая погода в Минске сегодня?"])
            finally:
                api_server.conversation_store = original_store
                api_server.internet_search = original_search


if __name__ == "__main__":
    unittest.main()
