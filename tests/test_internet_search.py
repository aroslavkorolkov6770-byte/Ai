"""Проверки безопасного разбора результатов контролируемого поиска."""

import unittest

from ai_core.internet_search import (
    InternetSearchClient,
    SearchResult,
    _geocoding_location_candidates,
    _weather_location,
    extract_readable_text,
    format_grounded_web_answer,
    format_search_results,
    is_weather_followup,
    is_weather_question,
    parse_bing_search_results,
    parse_search_results,
    pickup_point_hours_clarification,
    pickup_point_hours_followup_query,
    source_reliability_score,
    should_search_after_local_clarification,
    should_search_automatically,
    weather_day_from_messages,
    weather_location_clarification,
    weather_location_followup,
    weather_location_from_messages,
)


class InternetSearchTests(unittest.TestCase):
    def test_parser_extracts_redirected_url_and_html_text(self) -> None:
        document = """
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage">
            Пример <b>страницы</b>
        </a>
        <a class="result__snippet">Описание &amp; дополнительный текст</a>
        """

        results = parse_search_results(document)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Пример страницы")
        self.assertEqual(results[0].url, "https://example.com/page")
        self.assertEqual(results[0].snippet, "Описание & дополнительный текст")

    def test_parser_discards_non_http_links(self) -> None:
        document = """
        <a class="result__a" href="javascript:alert(1)">Опасная ссылка</a>
        <a class="result__snippet">Текст</a>
        """

        self.assertEqual(parse_search_results(document), [])

    def test_bing_parser_extracts_result_blocks(self) -> None:
        document = """
        <li class="b_algo">
            <h2><a href="https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLm9yZy9kb2Nz">Документация</a></h2>
            <p>Описание <strong>страницы</strong>.</p>
        </li>
        """

        results = parse_bing_search_results(document)

        self.assertEqual(
            results,
            [
                SearchResult(
                    title="Документация",
                    url="https://example.org/docs",
                    snippet="Описание страницы.",
                )
            ],
        )

    def test_formatter_returns_plain_text(self) -> None:
        results = parse_search_results(
            '<a class="result__a" href="https://example.com">Заголовок</a>'
            '<a class="result__snippet">Короткое описание</a>'
        )

        formatted = format_search_results(results)

        self.assertIn("1. Заголовок", formatted)
        self.assertIn("https://example.com", formatted)
        self.assertIn("Короткое описание", formatted)

    def test_search_rejects_too_short_query_before_network_call(self) -> None:
        client = InternetSearchClient()

        with self.assertRaises(ValueError):
            client.search("a")

    def test_automatic_search_detects_external_or_current_question(self) -> None:
        self.assertTrue(should_search_automatically("Какая погода в Минске сегодня?"))
        self.assertTrue(should_search_automatically("Найди актуальные новости по теме."))
        self.assertFalse(should_search_automatically("Как сделать кнопку в HTML?"))

    def test_relative_time_in_personal_conversation_does_not_trigger_web(self) -> None:
        self.assertFalse(should_search_automatically("Я сегодня устал после работы."))
        self.assertFalse(
            should_search_automatically(
                "Как ты считаешь, стоит ли мне сегодня поговорить с другом?"
            )
        )
        self.assertTrue(should_search_automatically("Какие сегодня новости в Минске?"))
        self.assertTrue(should_search_automatically("Кто сегодня выиграл матч?"))

    def test_weather_intent_covers_conditions_and_measurements(self) -> None:
        questions = (
            "Будет ли завтра дождь в Минске?",
            "Когда начнётся снег в Гомеле?",
            "Какой ветер в Бресте?",
            "Какая влажность сейчас?",
            "Какое давление сегодня?",
            "Когда восход в Витебске?",
            "Будет ли солнечно в Минске завтра?",
            "Что надеть завтра в Минске?",
        )

        self.assertTrue(all(is_weather_question(question) for question in questions))
        self.assertEqual(
            _weather_location("Будет ли солнечно в Минске завтра?"),
            "Минске",
        )
        self.assertEqual(_weather_location("Что надеть завтра в Минске?"), "Минске")

    def test_weather_intent_does_not_capture_ordinary_conversation(self) -> None:
        ordinary_messages = (
            "Мне холодно после фильма.",
            "Как подобрать одежду на собеседование?",
            "Во сколько начинается концерт?",
            "Расскажи про солнечную систему.",
            "Какая температура тела считается нормальной?",
            "Какое давление у человека считается нормальным?",
        )

        self.assertTrue(
            all(not is_weather_question(message) for message in ordinary_messages)
        )
        self.assertFalse(
            is_weather_followup("Во сколько начинается концерт?", True)
        )
        self.assertFalse(is_weather_followup("Вечером пойду в кино.", True))
        self.assertTrue(is_weather_followup("А до скольки?", True))

    def test_weather_context_stops_after_user_changes_topic(self) -> None:
        messages = [
            {
                "role": "user",
                "content": "Какая завтра погода в Старом Осколе?",
            },
            {"role": "assistant", "content": "Прогноз готов."},
            {"role": "user", "content": "Во сколько начинается концерт?"},
        ]

        self.assertIsNone(weather_location_from_messages(messages))
        self.assertIsNone(weather_day_from_messages(messages))

    def test_weather_clothing_question_uses_forecast_without_source_link(self) -> None:
        class WeatherClient(InternetSearchClient):
            def _fetch_document(
                self,
                url: str,
                max_response_bytes: int | None = None,
            ) -> str:
                del max_response_bytes
                if "geocoding-api.open-meteo.com" in url:
                    return '{"results":[{"name":"Минск","latitude":53.9,"longitude":27.56}]}'
                return (
                    '{"current":{"temperature_2m":12,"apparent_temperature":10,'
                    '"wind_speed_10m":15,"precipitation":1.2,"weather_code":61},'
                    '"daily":{"temperature_2m_max":[14,14],'
                    '"temperature_2m_min":[8,8],"weather_code":[61,61],'
                    '"precipitation_probability_max":[70,70],'
                    '"wind_speed_10m_max":[18,28]}}'
                )

        client = WeatherClient()
        tomorrow = client.answer("Что надеть завтра в Минске?")
        current = client.answer("Что надеть сейчас в Минске?")

        self.assertIn("Завтра в Минске", tomorrow.text)
        self.assertIn("ветровка", tomorrow.text)
        self.assertIn("зонт", tomorrow.text)
        self.assertNotIn("https://open-meteo.com/", tomorrow.text)
        self.assertIn("Сейчас в Минске", current.text)
        self.assertIn("ветровка", current.text)
        self.assertIn("зонт", current.text)

    def test_general_question_can_search_after_local_clarification(self) -> None:
        self.assertTrue(should_search_after_local_clarification("Кто основал компанию?"))
        self.assertTrue(should_search_after_local_clarification("Какой проходной балл по курсу?"))
        self.assertTrue(should_search_after_local_clarification("Расскажи о космосе"))
        self.assertTrue(should_search_after_local_clarification("Объясни принцип работы радуги"))

    def test_pickup_point_hours_requires_specific_location_before_search(self) -> None:
        prompt = "Узнай, работает ли пункт выдачи сегодня. Не придумывай, если источник не отвечает."
        clarification = pickup_point_hours_clarification(prompt)

        self.assertEqual(
            clarification,
            "Уточни, какой именно пункт выдачи и в каком городе или по какому адресу.",
        )
        self.assertEqual(
            pickup_point_hours_clarification(
                "До скольки сегодня работает ПВЗ в Минске?"
            ),
            clarification,
        )
        self.assertIsNone(
            pickup_point_hours_clarification(
                "Работает ли сегодня пункт выдачи в Минске на улице Примерной, 5?"
            )
        )
        self.assertIsNone(
            pickup_point_hours_clarification(
                "До скольки сегодня работает ПВЗ «Маяк» в Минске?"
            )
        )
        self.assertEqual(
            pickup_point_hours_followup_query(
                "Это пункт выдачи в Минске на улице Примерной, 5.",
                [
                    {"role": "user", "content": prompt},
                    {
                        "role": "assistant",
                        "content": "Уточни, какой именно пункт выдачи и в каком городе или по какому адресу.",
                    },
                ],
            ),
            "Узнай, работает ли пункт выдачи сегодня. Не придумывай, если источник не отвечает. Это пункт выдачи в Минске на улице Примерной, 5.",
        )
        self.assertIsNone(
            pickup_point_hours_followup_query(
                "Это пункт выдачи в Минске на улице Примерной, 5.",
                [
                    {"role": "user", "content": prompt},
                    {
                        "role": "assistant",
                        "content": "Уточни, какой именно пункт выдачи и в каком городе или по какому адресу.",
                    },
                    {"role": "user", "content": "Спасибо."},
                ],
            )
        )

    def test_weather_requires_location_before_a_precise_forecast(self) -> None:
        prompt = "Какая завтра погода?"
        clarification = weather_location_clarification(prompt)

        self.assertEqual(
            clarification,
            "Чтобы назвать погоду, укажите город, село или другой населённый пункт. "
            "Например: «Какая завтра погода в Старом Осколе?»",
        )
        self.assertIsNone(
            weather_location_clarification(prompt, location_hint="Минске")
        )
        self.assertIsNone(
            weather_location_clarification(
                prompt,
                coordinates_hint=(53.9, 27.56),
            )
        )
        self.assertEqual(
            weather_location_followup(
                "В Минске.",
                [
                    {"role": "user", "content": prompt},
                    {
                        "role": "assistant",
                        "content": clarification,
                    },
                ],
            ),
            (prompt, "Минске"),
        )
        self.assertIsNone(
            weather_location_followup(
                "Минск",
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "Другой ответ."},
                ],
            )
        )

    def test_preferred_official_source_wins_tie_without_extra_text(self) -> None:
        promotional = SearchResult(
            title="Рекламный агрегатор",
            url="https://promo.example.org/weather",
            snippet="",
        )
        official = SearchResult(
            title="Официальный источник",
            url="https://www.noaa.gov/weather",
            snippet="",
        )

        answer = format_grounded_web_answer(
            "Какая температура в городе?",
            [promotional, official],
            {
                promotional.url: "Температура в городе составит 19 градусов по данным рекламы.",
                official.url: "Температура в городе составит 19 градусов по официальному прогнозу.",
            },
        )

        self.assertGreater(
            source_reliability_score(official.url),
            source_reliability_score(promotional.url),
        )
        self.assertIn("официальному прогнозу", answer)
        self.assertNotIn("данным рекламы", answer)

    def test_grounded_answer_uses_page_fact_without_showing_source_by_default(self) -> None:
        result = SearchResult(
            title="Официальная страница",
            url="https://example.org/weather",
            snippet="Короткое описание страницы.",
        )
        document = extract_readable_text(
            "<html><body><script>ignore()</script>"
            "<p>Погода в Минске сегодня: переменная облачность и дождь.</p>"
            "</body></html>"
        )

        answer = format_grounded_web_answer(
            "Какая погода в Минске сегодня?",
            [result],
            {result.url: document},
        )

        self.assertIn("Погода в Минске сегодня", answer)
        self.assertNotIn("Официальная страница", answer)
        self.assertNotIn("https://example.org/weather", answer)
        self.assertNotIn("ignore", answer)

    def test_grounded_answer_rejects_a_page_with_only_one_weak_keyword_overlap(self) -> None:
        result = SearchResult(
            title="Страница города",
            url="https://example.org/city",
            snippet="Новости города.",
        )
        answer = format_grounded_web_answer(
            "Какая температура в городе?",
            [result],
            {
                result.url: (
                    "В городе открыли новый музей современного искусства. "
                    "Посетителям доступна постоянная экспозиция."
                )
            },
        )

        self.assertIn("не смог получить проверяемые детали", answer)
        self.assertNotIn("музей", answer.casefold())
        self.assertNotIn(result.url, answer)

    def test_grounded_answer_shows_source_only_when_requested(self) -> None:
        result = SearchResult(
            title="Официальная страница",
            url="https://example.org/weather",
            snippet="Короткое описание страницы.",
        )
        answer = format_grounded_web_answer(
            "Какая погода в Минске? Покажи источник.",
            [result],
            {result.url: "Погода в Минске: переменная облачность."},
            show_sources=True,
        )

        self.assertIn("Официальная страница", answer)
        self.assertIn("https://example.org/weather", answer)

    def test_grounded_answer_does_not_choose_page_heading_over_fact(self) -> None:
        result = SearchResult(
            title="Погода в Минске сегодня",
            url="https://example.org/minsk",
            snippet="Погода в Минске сегодня.",
        )
        answer = format_grounded_web_answer(
            "Какая погода в Минске сегодня?",
            [result],
            {
                result.url: (
                    "Погода в Минске сегодня\n"
                    "Сегодня температура воздуха достигнет 19 градусов, "
                    "ожидается дождь."
                )
            },
        )

        self.assertIn("температура воздуха достигнет 19 градусов", answer)

    def test_weather_question_uses_structured_source_instead_of_page_heading(self) -> None:
        class WeatherClient(InternetSearchClient):
            def __init__(self) -> None:
                super().__init__()
                self.geocoding_requests = 0

            def _fetch_document(
                self,
                url: str,
                max_response_bytes: int | None = None,
            ) -> str:
                del max_response_bytes
                if "geocoding-api.open-meteo.com" in url:
                    self.geocoding_requests += 1
                    if self.geocoding_requests == 1:
                        # «Минске» — естественная форма из вопроса; API ждёт
                        # именительный падеж, поэтому клиент пробует «Минск».
                        return '{"generationtime_ms":1.0}'
                    return (
                        '{"results":[{"name":"Минск","latitude":53.9,'
                        '"longitude":27.56}]}'
                    )
                return (
                    '{"current":{"temperature_2m":19,"apparent_temperature":16,'
                    '"wind_speed_10m":22,"precipitation":1.2,"weather_code":61},'
                    '"daily":{"temperature_2m_max":[20],"temperature_2m_min":[11],'
                    '"precipitation_probability_max":[73]}}'
                )

        client = WeatherClient()
        answer = client.answer("Какая погода в Минске сегодня?")

        self.assertEqual(client.geocoding_requests, 2)
        self.assertIn("Сейчас в Минск", answer.text)
        self.assertIn("температура 19 °C", answer.text)
        self.assertIn("от 11 °C до 20 °C", answer.text)
        self.assertNotIn("Open-Meteo", answer.text)
        self.assertNotIn("https://open-meteo.com/", answer.text)

    def test_weather_shows_source_only_after_explicit_request(self) -> None:
        class WeatherClient(InternetSearchClient):
            def _fetch_document(
                self,
                url: str,
                max_response_bytes: int | None = None,
            ) -> str:
                del max_response_bytes
                if "geocoding-api.open-meteo.com" in url:
                    return '{"results":[{"name":"Минск","latitude":53.9,"longitude":27.56}]}'
                return (
                    '{"current":{"temperature_2m":19,"apparent_temperature":16,'
                    '"wind_speed_10m":22,"precipitation":1.2,"weather_code":61},'
                    '"daily":{"temperature_2m_max":[20],"temperature_2m_min":[11],'
                    '"weather_code":[61],"precipitation_probability_max":[73]}}'
                )

        answer = WeatherClient().answer(
            "Какая погода в Минске сегодня? Покажи источник."
        )

        self.assertIn("Источник: Open-Meteo", answer.text)
        self.assertIn("https://open-meteo.com/", answer.text)

    def test_weather_question_with_natural_word_order_and_case_gets_tomorrow(self) -> None:
        class WeatherClient(InternetSearchClient):
            def __init__(self) -> None:
                super().__init__()
                self.geocoding_requests = 0

            def _fetch_document(
                self,
                url: str,
                max_response_bytes: int | None = None,
            ) -> str:
                del max_response_bytes
                if "geocoding-api.open-meteo.com" in url:
                    self.geocoding_requests += 1
                    if self.geocoding_requests == 1:
                        return '{"generationtime_ms":1.0}'
                    return (
                        '{"results":[{"name":"Старый Оскол","latitude":51.3,'
                        '"longitude":37.8}]}'
                    )
                return (
                    '{"current":{"temperature_2m":17,"apparent_temperature":16,'
                    '"wind_speed_10m":11,"precipitation":0,"weather_code":2},'
                    '"daily":{"temperature_2m_max":[18,21],'
                    '"temperature_2m_min":[9,12],"weather_code":[2,61],'
                    '"precipitation_probability_max":[12,84]}}'
                )

        question = "Какая завтра погода в Старом Осколе?"
        self.assertEqual(_weather_location(question), "Старом Осколе")
        self.assertEqual(
            _geocoding_location_candidates("Старом Осколе"),
            ("Старом Осколе", "Старый Оскол"),
        )

        answer = WeatherClient().answer(question)

        self.assertIn("Завтра в Старом Осколе", answer.text)
        self.assertIn("слабый дождь", answer.text)
        self.assertIn("от 12 °C до 21 °C", answer.text)
        self.assertIn("вероятность осадков 84%", answer.text)
        self.assertNotIn("Сейчас в", answer.text)

    def test_weather_route_recognizes_rain_question_without_word_weather(self) -> None:
        class WeatherClient(InternetSearchClient):
            def __init__(self) -> None:
                super().__init__()
                self.search_called = False

            def search(self, query: str, max_results: int | None = None) -> list[SearchResult]:
                del query, max_results
                self.search_called = True
                return []

            def _fetch_document(
                self,
                url: str,
                max_response_bytes: int | None = None,
            ) -> str:
                del max_response_bytes
                if "geocoding-api.open-meteo.com" in url:
                    return (
                        '{"results":[{"name":"Старый Оскол","latitude":51.3,'
                        '"longitude":37.8}]}'
                    )
                return (
                    '{"current":{"temperature_2m":17,"apparent_temperature":16,'
                    '"relative_humidity_2m":72,"pressure_msl":1014,"visibility":10000,'
                    '"wind_speed_10m":11,"precipitation":0,"weather_code":2},'
                    '"daily":{"temperature_2m_max":[18,21],'
                    '"temperature_2m_min":[9,12],"weather_code":[2,61],'
                    '"precipitation_probability_max":[12,84]}}'
                )

        question = "Будет ли завтра дождь в Старом Осколе?"
        answer = WeatherClient().answer(question)

        self.assertTrue(is_weather_question(question))
        self.assertEqual(_weather_location(question), "Старом Осколе")
        self.assertIn("Завтра в Старом Осколе: слабый дождь", answer.text)
        self.assertIn("вероятность осадков 84%", answer.text)

    def test_weather_route_returns_only_requested_current_measurement_or_sun_time(self) -> None:
        class WeatherClient(InternetSearchClient):
            def _fetch_document(
                self,
                url: str,
                max_response_bytes: int | None = None,
            ) -> str:
                del max_response_bytes
                if "geocoding-api.open-meteo.com" in url:
                    return '{"results":[{"name":"Минск","latitude":53.9,"longitude":27.56}]}'
                return (
                    '{"current":{"temperature_2m":19,"apparent_temperature":17,'
                    '"relative_humidity_2m":70,"pressure_msl":1013,"visibility":10000,'
                    '"wind_speed_10m":14,"precipitation":0,"weather_code":2},'
                    '"daily":{"time":["2026-09-11"],"temperature_2m_max":[20],'
                    '"temperature_2m_min":[11],"weather_code":[2],'
                    '"precipitation_probability_max":[12],'
                    '"sunrise":["2026-09-11T07:01"],"sunset":["2026-09-11T19:32"]}}'
                )

        client = WeatherClient()
        temperature = client.answer("Какая температура в Минске?")
        sunrise = client.answer("Когда восход в Минске?")
        sunset_time_only = client.answer(
            "Во сколько сегодня закат в Минске? Напиши только время."
        )

        self.assertIn("Сейчас в Минске: переменная облачность; температура 19 °C", temperature.text)
        self.assertNotIn("от 11 °C до 20 °C", temperature.text)
        self.assertEqual(
            sunrise.text,
            "Сегодня в Минске: восход в 07:01, закат в 19:32.",
        )
        self.assertEqual(sunset_time_only.text, "19:32")
        self.assertNotIn("Open-Meteo", sunset_time_only.text)

    def test_weather_location_reads_place_from_context_before_astronomy_question(self) -> None:
        question = "Я буду в Калуге. Во сколько сегодня закат?"

        self.assertTrue(is_weather_question(question))
        self.assertEqual(_weather_location(question), "Калуге")
        self.assertEqual(
            _geocoding_location_candidates("Калуге"),
            ("Калуге", "Калуга", "Калуг"),
        )

    def test_weather_route_uses_ephemeral_coordinates_without_geocoding(self) -> None:
        class WeatherClient(InternetSearchClient):
            def __init__(self) -> None:
                super().__init__()
                self.requested_urls: list[str] = []

            def _fetch_document(
                self,
                url: str,
                max_response_bytes: int | None = None,
            ) -> str:
                del max_response_bytes
                self.requested_urls.append(url)
                if "geocoding-api.open-meteo.com" in url:
                    raise AssertionError("Координатный запрос не должен вызывать геокодер.")
                return (
                    '{"current":{"temperature_2m":17,"apparent_temperature":16,'
                    '"relative_humidity_2m":72,"pressure_msl":1014,"visibility":10000,'
                    '"wind_speed_10m":11,"precipitation":0,"weather_code":2},'
                    '"daily":{"time":["2026-09-11","2026-09-12"],'
                    '"temperature_2m_max":[18,21],"temperature_2m_min":[9,12],'
                    '"weather_code":[2,61],"precipitation_probability_max":[12,84]},'
                    '"hourly":{"time":["2026-09-12T10:00","2026-09-12T12:00"],'
                    '"relative_humidity_2m":[65,61],"pressure_msl":[1012,1011],'
                    '"visibility":[8000,9000],"weather_code":[3,61],'
                    '"precipitation":[0,0.2]}}'
                )

        client = WeatherClient()
        answer = client.answer(
            "Какая влажность завтра?",
            coordinates_hint=(53.9, 27.56),
        )

        self.assertEqual(len(client.requested_urls), 1)
        self.assertIn("Завтра по вашей геопозиции около 12:00: влажность 61%.", answer.text)
        self.assertNotIn("53.9", answer.text)

    def test_weather_question_without_city_never_uses_general_web_search(self) -> None:
        class NoSearchWeatherClient(InternetSearchClient):
            def search(self, query: str, max_results: int | None = None) -> list[SearchResult]:
                del query, max_results
                self.fail("Вопрос о погоде без города не должен идти в общий поиск.")

        answer = NoSearchWeatherClient().answer("Какая завтра погода?")

        self.assertIn("укажите город, село", answer.text)
        self.assertIn("Старом Осколе", answer.text)

    def test_weather_followup_returns_hourly_rain_period_for_previous_city(self) -> None:
        class WeatherClient(InternetSearchClient):
            def _fetch_document(
                self,
                url: str,
                max_response_bytes: int | None = None,
            ) -> str:
                del max_response_bytes
                if "geocoding-api.open-meteo.com" in url:
                    return (
                        '{"results":[{"name":"Старый Оскол","latitude":51.3,'
                        '"longitude":37.8}]}'
                    )
                return (
                    '{"current":{"temperature_2m":17,"apparent_temperature":16,'
                    '"wind_speed_10m":11,"precipitation":0,"weather_code":2},'
                    '"daily":{"time":["2026-09-10","2026-09-11"],'
                    '"temperature_2m_max":[18,21],"temperature_2m_min":[9,12],'
                    '"weather_code":[2,61],"precipitation_probability_max":[12,84]},'
                    '"hourly":{"time":["2026-09-11T00:00","2026-09-11T01:00",'
                    '"2026-09-11T02:00","2026-09-11T03:00","2026-09-11T04:00",'
                    '"2026-09-11T05:00","2026-09-11T06:00"],'
                    '"weather_code":[3,61,61,3,3,80,3],'
                    '"precipitation":[0,0.2,0.1,0,0,0.8,0]}}'
                )

        answer = WeatherClient().answer(
            "А со скольки до скольки стоит дождь?",
            location_hint="Старом Осколе",
            day_hint="tomorrow",
            force_weather=True,
        )

        self.assertEqual(
            answer.text,
            "Завтра в Старом Осколе: дождь ожидается с 01:00 до 03:00, затем с 05:00 до 06:00.",
        )


if __name__ == "__main__":
    unittest.main()
