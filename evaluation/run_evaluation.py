"""Запускает проверку гибридного API и отдельно проверяет нейросеть."""

from __future__ import annotations

import json
import sys
from difflib import SequenceMatcher
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = PROJECT_ROOT / "evaluation" / "evaluation_cases.jsonl"
UNSEEN_PATH = PROJECT_ROOT / "evaluation" / "unseen_questions.jsonl"
API_URL = "http://127.0.0.1:8000/generate"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def call_api(prompt: str, use_knowledge_base: bool) -> dict[str, object]:
    """Отправляет один запрос стандартной библиотекой Python."""
    body = json.dumps(
        {
        "prompt": prompt,
        "response_mode": "experimental",
            "max_tokens": 64,
            "temperature": 0.7,
            "top_k": 1,
            "repetition_penalty": 1.15,
            "no_repeat_ngram_size": 3,
            "use_knowledge_base": use_knowledge_base,
        }
    ).encode("utf-8")
    request = Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def load_cases() -> list[dict[str, str]]:
    """Загружает контрольные вопросы и ожидаемые ответы."""
    cases: list[dict[str, str]] = []
    with CASES_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                cases.append(json.loads(line))
    return cases


def load_unseen_questions() -> list[str]:
    """Загружает вопросы, которых нет в обучающем датасете."""
    questions: list[str] = []
    with UNSEEN_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                questions.append(str(json.loads(line)["prompt"]))
    return questions


def load_paraphrases() -> list[dict[str, str]]:
    """Загружает вопросы с небольшим изменением формулировки."""
    cases: list[dict[str, str]] = []
    path = PROJECT_ROOT / "evaluation" / "paraphrase_questions.jsonl"
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                cases.append(json.loads(line))
    return cases


def repetition_ratio(text: str, ngram_size: int = 3) -> float:
    """Считает долю повторившихся символьных n-грамм в ответе."""
    characters = list(text)
    if len(characters) < ngram_size:
        return 0.0
    ngrams = [
        tuple(characters[index:index + ngram_size])
        for index in range(len(characters) - ngram_size + 1)
    ]
    return 1.0 - len(set(ngrams)) / len(ngrams)


def normalized_text_similarity(actual: str, expected: str) -> float:
    """Считает мягкое посимвольное сходство полного ответа от 0 до 1."""
    normalized_actual = " ".join(actual.casefold().split())
    normalized_expected = " ".join(expected.casefold().split())
    return SequenceMatcher(
        None,
        normalized_actual,
        normalized_expected,
        autojunk=False,
    ).ratio()


def main() -> None:
    """Печатает две отдельные проверки: baseline и neural-only."""
    cases = load_cases()
    baseline_passed = 0
    neural_exact = 0
    neural_sources: dict[str, int] = {}
    neural_repetition_ratios: list[float] = []
    neural_text_similarities: list[float] = []

    for case in cases:
        baseline = call_api(case["prompt"], use_knowledge_base=True)
        if (
            baseline.get("text") == case["completion"]
            and baseline.get("source") == "local-dataset-exact-match"
        ):
            baseline_passed += 1

        neural = call_api(case["prompt"], use_knowledge_base=False)
        neural_repetition_ratios.append(repetition_ratio(str(neural.get("text", ""))))
        neural_text_similarities.append(
            normalized_text_similarity(
                str(neural.get("text", "")),
                case["completion"],
            )
        )
        if neural.get("text") == case["completion"]:
            neural_exact += 1
        source = str(neural.get("source", "unknown"))
        neural_sources[source] = neural_sources.get(source, 0) + 1
        print(f"Вопрос: {case['prompt']}")
        print(f"Нейросеть: {neural.get('text', '')}")

    print(f"Baseline: {baseline_passed}/{len(cases)} точных ответов")
    print(f"Neural-only exact на известных вопросах: {neural_exact}/{len(cases)}")
    print(f"Neural-only источники: {neural_sources}")
    average_repetition = sum(neural_repetition_ratios) / max(1, len(neural_repetition_ratios))
    print(f"Средняя доля повторяющихся 3-грамм neural-only: {average_repetition:.4f}")
    average_similarity = sum(neural_text_similarities) / max(
        1,
        len(neural_text_similarities),
    )
    print(f"Среднее сходство neural-only с правильным ответом: {average_similarity:.4f}")
    paraphrase_passed = 0
    print("Похожие вопросы:")
    for case in load_paraphrases():
        result = call_api(case["prompt"], use_knowledge_base=True)
        if result.get("text") == case["completion"] and result.get("source") in {
            "local-dataset-exact-match",
            "local-dataset-similar-match",
        }:
            paraphrase_passed += 1
        print(
            f"Вопрос: {case['prompt']} | "
            f"Источник: {result.get('source')} | "
            f"Сходство: {result.get('match_score')}"
        )
    print(f"Похожие вопросы: {paraphrase_passed}/{len(load_paraphrases())} точных ответов")
    print("Вопросы вне датасета:")
    for prompt in load_unseen_questions():
        neural = call_api(prompt, use_knowledge_base=False)
        print(f"Вопрос: {prompt}")
        print(f"Нейросеть: {neural.get('text', '')}")


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, OSError, ValueError) as error:
        raise SystemExit(f"Оценка не запущена: {error}") from error
