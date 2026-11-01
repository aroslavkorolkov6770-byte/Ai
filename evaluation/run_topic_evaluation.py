"""Проверяет neural-only ответы на честной test-части выбранной темы."""

from __future__ import annotations

import argparse
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_core.data_quality import TRAINING_TOPIC_CHOICES
from ai_core.dataset_tools import split_dataset
from ai_core.training_corpus import load_training_pairs_for_topic
from ai_core.training_data import TrainingPair


DEFAULT_API_URL = "http://127.0.0.1:8000"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def normalized_text_similarity(actual: str, expected: str) -> float:
    """Считает мягкое сходство двух полных ответов от 0 до 1."""
    normalized_actual = " ".join(actual.casefold().split())
    normalized_expected = " ".join(expected.casefold().split())
    return SequenceMatcher(
        None,
        normalized_actual,
        normalized_expected,
        autojunk=False,
    ).ratio()


def call_api(
    prompt: str,
    api_url: str,
    decoding: str,
    beam_width: int,
) -> dict[str, object]:
    """Отправляет neural-only запрос с выбранным способом декодирования."""
    body = json.dumps(
        {
        "prompt": prompt,
        "response_mode": "experimental",
            "max_tokens": 64,
            "temperature": 0.7,
            "top_k": 1,
            "repetition_penalty": 1.15,
            "no_repeat_ngram_size": 3,
            "decoding": decoding,
            "beam_width": beam_width,
            "length_penalty": 0.7,
            "use_knowledge_base": False,
            "use_conversation_context": False,
        }
    ).encode("utf-8")
    request = Request(
        f"{api_url}/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def load_health(api_url: str) -> dict[str, object]:
    """Читает тему checkpoint, загруженного в текущий API."""
    with urlopen(f"{api_url}/health", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def topic_test_pairs(topic: str, seed: int) -> list[TrainingPair]:
    """Повторяет split обучения и возвращает только его test-часть."""
    topic_pairs = load_training_pairs_for_topic(topic)
    if not topic_pairs:
        raise ValueError(f"Для темы {topic!r} не найдено примеров.")
    return split_dataset(
        topic_pairs,
        validation_ratio=0.1,
        test_ratio=0.1,
        seed=seed,
    ).test


def parse_args() -> argparse.Namespace:
    """Читает параметры воспроизводимой тематической проверки."""
    parser = argparse.ArgumentParser(
        description="Проверка neural-only модели на теме из её test split."
    )
    parser.add_argument("--topic", choices=TRAINING_TOPIC_CHOICES, required=True)
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help="Адрес локального API для проверки кандидата.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--decoding",
        choices=("sample", "beam"),
        default="sample",
        help="Способ декодирования ответа.",
    )
    parser.add_argument(
        "--beam-width",
        type=int,
        default=3,
        help="Количество параллельных beam-веток для decoding=beam.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=0,
        help="Ограничить число test-примеров; 0 означает проверить все.",
    )
    parser.add_argument(
        "--allow-model-topic-mismatch",
        action="store_true",
        help=(
            "Разрешить сравнение универсального checkpoint с test-частью "
            "выбранной темы."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Сверяет тему API и печатает результат на отложенных примерах."""
    arguments = parse_args()
    api_url = arguments.api_url.rstrip("/")
    health = load_health(api_url)
    loaded_topic = str(health.get("training_topic", "all"))
    if (
        loaded_topic != arguments.topic
        and not arguments.allow_model_topic_mismatch
    ):
        raise SystemExit(
            "Тема checkpoint в API не совпадает с проверкой: "
            f"загружено {loaded_topic!r}, запрошено {arguments.topic!r}."
        )
    if loaded_topic != arguments.topic:
        print(
            "Внимание: сравнивается checkpoint темы "
            f"{loaded_topic!r} с test-частью темы {arguments.topic!r}."
        )

    pairs = topic_test_pairs(arguments.topic, arguments.seed)
    if arguments.max_cases < 0:
        raise ValueError("max_cases не может быть отрицательным.")
    if not 2 <= arguments.beam_width <= 5:
        raise ValueError("beam_width должен быть от 2 до 5.")
    if arguments.max_cases:
        pairs = pairs[: arguments.max_cases]
    exact_matches = 0
    similarities: list[float] = []

    for pair in pairs:
        result = call_api(
            pair.prompt,
            api_url,
            arguments.decoding,
            arguments.beam_width,
        )
        actual = str(result.get("text", ""))
        similarities.append(normalized_text_similarity(actual, pair.completion))
        exact_matches += int(actual == pair.completion)
        print(f"Вопрос: {pair.prompt}")
        print(f"Ожидается: {pair.completion}")
        print(f"Нейросеть: {actual}")

    average_similarity = sum(similarities) / max(1, len(similarities))
    print(f"Тема: {arguments.topic}")
    print(f"Декодирование: {arguments.decoding}")
    if arguments.decoding == "beam":
        print(f"Ширина beam: {arguments.beam_width}")
    print(f"Test-примеров: {len(pairs)}")
    print(f"Точных ответов: {exact_matches}/{len(pairs)}")
    print(f"Среднее сходство: {average_similarity:.4f}")


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, OSError, ValueError) as error:
        raise SystemExit(f"Тематическая проверка не запущена: {error}") from error
