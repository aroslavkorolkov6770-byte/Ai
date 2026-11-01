"""Готовит metadata-only очередь семантической проверки OASST1 для Brain v3.

Источник OASST1 уже принят в локальный active corpus по лицензии и техническому
качеству, но его общие метки не доказывают покрытие обычного разговора. Этот
модуль лишь находит *кандидатов* для человеческой проверки по консервативным
русским cue-группам. Он не переименовывает active записи, не создаёт новый
training source и не меняет manifest, API, веса или обучение.

Отчёт намеренно не содержит ни одной реплики. Для проверки сохраняются только
ID, content SHA-256, split, число ходов, candidate labels и статические reason
codes. Текст конкретной записи можно посмотреть только локально в уже
лицензированном raw source во время ручной проверки.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
RAW_ROOT = ROOT / "data" / "brain_v3" / "raw"
RESULTS_ROOT = ROOT / "evaluation" / "results"
SOURCE_ID = "openassistant-oasst1-apache-v1"
DEFAULT_SOURCE_PATH = RAW_ROOT / "openassistant-oasst1-apache-v1.jsonl"
DEFAULT_REPORT_DIRECTORY = RESULTS_ROOT / "oasst1-semantic-review-queue-v0.4"

# Проверяются только диалоги с настоящим follow-up. Двухходовые записи могут
# быть полезны для базовой instruction-настройки, но не показывают связность
# разговора и потому не входят в эту очередь.
MINIMUM_REVIEW_TURNS = 4
REVIEW_ITEMS_PER_CANDIDATE_TOPIC = 12
MAX_ASSISTANT_WORDS_PER_TURN = 180

# OASST1 содержит полезные многоходовые примеры, но часть реплик принадлежит
# историческому прототипу ассистента, обучению кода или старому ограничению без
# web-инструмента. Такие ответы не подходят для приоритета «естественная
# беседа + корректная tool routing» и не должны расходовать ручной review.
ASSISTANT_QUALITY_REJECTION_MARKERS: dict[str, tuple[str, ...]] = {
    "assistant_model_identity": (
        "openassistant",
        "chatgpt",
        "github copilot",
        "stable diffusion",
    ),
    "assistant_stale_web_capability": (
        "не имею доступа к актуальным источникам",
        "не обучена использовать интернет",
        "не имею доступа к интернету",
        "не могу пользоваться интернетом",
    ),
}

# Не всякое уточнение пользователя является проблемой. Этот узкий набор ищет
# только явную формулировку «предыдущий ответ не ответил на запрос». В текущем
# full-dialogue training format такой пример закрепил бы сначала бессмысленную
# assistant-реплику, поэтому его безопаснее исключить до ручного review.
USER_REJECTION_OF_PREVIOUS_ASSISTANT_PATTERNS = (
    r"\b(?:это|такой|такое|данный)\s+не\s+(?:является|был(?:о)?|будет)\s+ответ(?:ом)?\b",
    r"\b(?:ты|вы)\s+не\s+(?:ответил(?:и)?|понял(?:и)?)\b",
    r"\b(?:попробуй|попробуйте)\s+(?:ещ[её]\s+раз|снова)\b",
    r"\b(?:это|ответ)\s+не\s+(?:соответствует|подходит)\b",
    r"\b(?:совсем\s+)?не\s+о\s+том\b",
)
USER_RESTATES_UNMET_REQUEST_PATTERNS = (
    r"\bя\s+(?:же|ведь)\s+(?:просил(?:а)?|попросил(?:а)?)\b",
)

# Порядок одновременно задаёт стабильный primary candidate label при нескольких
# сигналах и делает итоговый JSON воспроизводимым. Метки не являются истиной о
# содержании: любая из них требует ручного решения reviewer.
CANDIDATE_TOPICS = (
    "casual_dialogue",
    "emotional_support",
    "opinion_and_choice",
    "explanation_and_instruction",
    "context_and_memory",
    "correction",
    "uncertainty_and_clarification",
    "web_grounding",
    "privacy_and_memory",
    "style_and_length",
    "safety_boundaries",
)

# Каждая причина — устойчивый технический код, а не совпавшее слово. Поэтому
# report объясняет отбор, но не раскрывает user или assistant текст из source.
CUE_GROUPS: dict[str, tuple[tuple[str, str], ...]] = {
    "casual_dialogue": (
        ("casual_greeting", r"\b(?:привет|здравствуй|добрый\s+(?:день|вечер|утро))\b"),
        ("casual_talk", r"\b(?:поболта|поговори|скучно|расскажи)\w*\b"),
    ),
    "emotional_support": (
        ("emotion_state", r"\b(?:тревож|груст|устал|одинок|страш|пережив|плохо)\w*\b"),
        ("support_request", r"\b(?:поддерж|выговор|успоко)\w*\b"),
    ),
    "opinion_and_choice": (
        ("choice_language", r"\b(?:выбор|выбрать|вариант|решить|решение)\w*\b"),
        ("advice_request", r"\b(?:посовет|мнение)\w*\b"),
    ),
    "explanation_and_instruction": (
        ("explanation_request", r"\b(?:объясн|почему|инструкц|помоги)\w*\b"),
        ("definition_request", r"\bчто\s+такое\b"),
        ("how_to_request", r"\bкак\s+(?:мне|можно|правильно|сделать|настроить|начать)\b"),
    ),
    "context_and_memory": (
        ("conversation_reference", r"\b(?:выше|предыдущ|прошл|контекст)\w*\b"),
        ("memory_request", r"\b(?:помни|запомн)\w*\b"),
    ),
    "correction": (
        ("correction_language", r"\b(?:исправ|ошиб|не\s+так|извини|уточн)\w*\b"),
    ),
    "uncertainty_and_clarification": (
        ("uncertainty_language", r"\b(?:не\s+знаю|не\s+уверен|не\s+понимаю|неясн)\w*\b"),
        ("clarification_request", r"\b(?:уточн|проясн)\w*\b"),
    ),
    "web_grounding": (
        ("time_sensitive_fact", r"\b(?:сегодня|сейчас|погода|новост|актуал|курс|расписан)\w*\b"),
        ("source_request", r"\b(?:интернет|ссылка|источник|официальн)\w*\b"),
    ),
    "privacy_and_memory": (
        ("privacy_language", r"\b(?:личн|приват|конфиденц|данные)\w*\b"),
        ("memory_boundary", r"\b(?:памят|запомн|удали)\w*\b"),
    ),
    "style_and_length": (
        ("length_request", r"\b(?:коротк|подробн|кратк)\w*\b"),
        ("format_request", r"\b(?:простыми|список|формат|пункт)\w*\b"),
    ),
    "safety_boundaries": (
        ("harm_or_illegality", r"\b(?:опасн|вред|незакон|наркот|самоуб|оруж|взлом)\w*\b"),
    ),
}


class OASST1SemanticReviewError(ValueError):
    """Останавливает создание непроверяемой metadata-only очереди."""


def _stable_rank(*parts: str) -> str:
    """Возвращает воспроизводимый rank без global random state."""
    payload = "\u241f".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_path_inside(path: Path, root: Path, code: str) -> Path:
    """Разрешает работать только с заранее заданным локальным каталогом."""
    resolved_path = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise OASST1SemanticReviewError(code) from error
    return resolved_path


def _read_jsonl_source(path: Path, *, raw_root: Path) -> tuple[list[dict[str, Any]], str]:
    """Читает local raw JSONL и SHA-256, не выполняя содержимое как код."""
    source = _safe_path_inside(
        path,
        raw_root,
        "oasst1_semantic_review_source_outside_raw",
    )
    if source.suffix.lower() != ".jsonl" or not source.is_file():
        raise OASST1SemanticReviewError("oasst1_semantic_review_source_not_found")
    try:
        raw = source.read_bytes()
        records = [
            json.loads(line)
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OASST1SemanticReviewError("oasst1_semantic_review_source_invalid") from error
    if not records or not all(isinstance(record, dict) for record in records):
        raise OASST1SemanticReviewError("oasst1_semantic_review_source_invalid")
    return records, hashlib.sha256(raw).hexdigest()


def _normalise_text(value: str) -> str:
    """Нормализует текст только в памяти для поиска cue-групп."""
    return " ".join(value.casefold().split())


def _validate_source_record(record: dict[str, Any]) -> None:
    """Требует базовую dialogue-схему exact active OASST1 source."""
    provenance = record.get("provenance")
    turns = record.get("turns")
    content_hash = record.get("content_sha256")
    if (
        record.get("record_type") != "dialogue"
        or record.get("language") != "ru"
        or record.get("layer") != "dialogue"
        or not isinstance(record.get("id"), str)
        or not isinstance(provenance, dict)
        or provenance.get("source_id") != SOURCE_ID
        or not isinstance(content_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None
        or not isinstance(turns, list)
        or not turns
    ):
        raise OASST1SemanticReviewError("oasst1_semantic_review_record_invalid")
    for index, turn in enumerate(turns):
        expected_role = "user" if index % 2 == 0 else "assistant"
        if (
            not isinstance(turn, dict)
            or turn.get("role") != expected_role
            or not isinstance(turn.get("text"), str)
            or not turn["text"].strip()
        ):
            raise OASST1SemanticReviewError("oasst1_semantic_review_record_invalid")
    if turns[-1].get("role") != "assistant":
        raise OASST1SemanticReviewError("oasst1_semantic_review_record_invalid")


def _candidate_reason_codes(turns: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Находит candidate labels только по user ходам и не возвращает совпавший текст."""
    user_text = "\n".join(
        _normalise_text(turn["text"])
        for turn in turns
        if turn["role"] == "user"
    )
    result: dict[str, list[str]] = {}
    for topic in CANDIDATE_TOPICS:
        codes = [
            reason_code
            for reason_code, expression in CUE_GROUPS[topic]
            if re.search(expression, user_text, re.IGNORECASE) is not None
        ]
        if codes:
            result[topic] = sorted(codes)
    return result


def _assistant_quality_rejection_codes(turns: list[dict[str, Any]]) -> list[str]:
    """Отсеивает очевидно неподходящие assistant-реплики без вывода текста.

    Это не оценка всей беседы и не auto-approval. Фильтр исключает только
    проверяемые случаи, которые уже противоречат цели будущего conversational
    slice: устаревшая identity/capability, code/ASCII блок или крайне длинная
    одиночная реплика вместо удобного для чтения ответа.
    """
    codes: set[str] = set()
    for turn in turns:
        if turn["role"] != "assistant":
            continue
        text = turn["text"]
        normalised = _normalise_text(text)
        if len(normalised.split()) > MAX_ASSISTANT_WORDS_PER_TURN:
            codes.add("assistant_overlong_turn")
        if "```" in text:
            codes.add("assistant_code_or_ascii_block")
        for code, markers in ASSISTANT_QUALITY_REJECTION_MARKERS.items():
            if any(marker in normalised for marker in markers):
                codes.add(code)
    return sorted(codes)


def _user_rejection_of_previous_answer_codes(turns: list[dict[str, Any]]) -> list[str]:
    """Ищет только явный отказ или повтор невыполненной просьбы пользователя.

    Обычные исправления факта или просьбы объяснить подробнее сюда не попадают.
    Фильтр не судит о последующем исправленном ответе: он лишь отмечает, что
    полный dialogue уже содержит вредный для будущего генератора пример ошибки.
    """

    codes: set[str] = set()
    for index, turn in enumerate(turns):
        if index == 0 or turn["role"] != "user":
            continue
        normalised = _normalise_text(turn["text"])
        if any(
            re.search(pattern, normalised, re.IGNORECASE) is not None
            for pattern in USER_REJECTION_OF_PREVIOUS_ASSISTANT_PATTERNS
        ):
            codes.add("user_rejects_previous_assistant_answer")
        if any(
            re.search(pattern, normalised, re.IGNORECASE) is not None
            for pattern in USER_RESTATES_UNMET_REQUEST_PATTERNS
        ):
            codes.add("user_restates_unmet_request")
    return sorted(codes)


def _metadata_candidate(
    record: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Строит неперсонализированную review metadata либо причину исключения."""
    _validate_source_record(record)
    turns = record["turns"]
    if len(turns) < MINIMUM_REVIEW_TURNS:
        return None, []
    quality_rejection_codes = sorted(
        set(
            _assistant_quality_rejection_codes(turns)
            + _user_rejection_of_previous_answer_codes(turns)
        )
    )
    if quality_rejection_codes:
        return None, quality_rejection_codes
    reason_codes = _candidate_reason_codes(turns)
    if not reason_codes:
        return None, []
    labels = [topic for topic in CANDIDATE_TOPICS if topic in reason_codes]
    primary_topic = min(
        labels,
        key=lambda topic: (-len(reason_codes[topic]), CANDIDATE_TOPICS.index(topic)),
    )
    split = record.get("split")
    if split not in {"train", "validation", "test"}:
        raise OASST1SemanticReviewError("oasst1_semantic_review_record_invalid")
    return {
        "id": record["id"],
        "content_sha256": record["content_sha256"],
        "split": split,
        "turn_count": len(turns),
        "primary_candidate_topic": primary_topic,
        "candidate_topic_labels": labels,
        "candidate_reason_codes": {
            topic: reason_codes[topic]
            for topic in labels
        },
        "review_status": "unreviewed",
    }, []


def _coverage_snapshot(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Считает структуру queue только по metadata, без реплик source."""
    splits: Counter[str] = Counter()
    turns: Counter[str] = Counter()
    primary_topics: Counter[str] = Counter()
    all_candidate_topics: Counter[str] = Counter()
    for item in items:
        splits[item["split"]] += 1
        turns[str(item["turn_count"])] += 1
        primary_topics[item["primary_candidate_topic"]] += 1
        all_candidate_topics.update(item["candidate_topic_labels"])
    return {
        "splits": dict(sorted(splits.items())),
        "turn_counts": dict(sorted(turns.items(), key=lambda item: int(item[0]))),
        "primary_candidate_topics": dict(sorted(primary_topics.items())),
        "all_candidate_topics": dict(sorted(all_candidate_topics.items())),
    }


def _best_candidate(
    items: Iterable[dict[str, Any]],
    *,
    topic: str,
) -> dict[str, Any] | None:
    """Выбирает одну наиболее информативную запись для заданной candidate label."""
    ranked = [
        (
            -len(item["candidate_reason_codes"][topic]),
            -item["turn_count"],
            _stable_rank(topic, item["id"]),
            item,
        )
        for item in items
        if topic in item["candidate_topic_labels"]
    ]
    if not ranked:
        return None
    return min(ranked, key=lambda value: value[:3])[3]


def select_semantic_review_queue(
    records: Iterable[dict[str, Any]],
    *,
    items_per_topic: int = REVIEW_ITEMS_PER_CANDIDATE_TOPIC,
) -> dict[str, Any]:
    """Отбирает небольшую стратифицированную очередь без auto-approval.

    Сначала добавляется хотя бы один candidate от каждого доступного split для
    каждой topic. Затем квота конкретной topic дополняется детерминированно
    наиболее информативными многоходовыми записями. Одна запись может покрывать
    несколько candidate labels, но это не превращает labels в утверждение.
    """
    if not isinstance(items_per_topic, int) or items_per_topic <= 0:
        raise OASST1SemanticReviewError("oasst1_semantic_review_items_per_topic_invalid")
    materialized_records = list(records)
    candidates: list[dict[str, Any]] = []
    quality_rejections: Counter[str] = Counter()
    multi_turn_source_records = 0
    for record in materialized_records:
        turns = record.get("turns")
        if isinstance(turns, list) and len(turns) >= MINIMUM_REVIEW_TURNS:
            multi_turn_source_records += 1
        item, rejection_codes = _metadata_candidate(record)
        quality_rejections.update(rejection_codes)
        if item is not None:
            candidates.append(item)
    identifiers = [item["id"] for item in candidates]
    if len(identifiers) != len(set(identifiers)):
        raise OASST1SemanticReviewError("oasst1_semantic_review_duplicate_identifier")
    if not candidates:
        raise OASST1SemanticReviewError("oasst1_semantic_review_no_candidates")

    by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        for topic in item["candidate_topic_labels"]:
            by_topic[topic].append(item)

    selected: dict[str, dict[str, Any]] = {}
    selection_by_topic: dict[str, int] = {}
    eligible_by_topic = {
        topic: len(by_topic.get(topic, []))
        for topic in CANDIDATE_TOPICS
        if by_topic.get(topic)
    }
    for topic in CANDIDATE_TOPICS:
        topic_items = by_topic.get(topic, [])
        if not topic_items:
            continue
        topic_selected: set[str] = set()
        for split in ("train", "validation", "test"):
            candidate = _best_candidate(
                (item for item in topic_items if item["split"] == split),
                topic=topic,
            )
            if candidate is not None:
                selected[candidate["id"]] = candidate
                topic_selected.add(candidate["id"])
        for candidate in sorted(
            topic_items,
            key=lambda item: (
                -len(item["candidate_reason_codes"][topic]),
                -item["turn_count"],
                _stable_rank(topic, item["id"]),
            ),
        ):
            if len(topic_selected) >= items_per_topic:
                break
            selected[candidate["id"]] = candidate
            topic_selected.add(candidate["id"])
        selection_by_topic[topic] = len(topic_selected)

    selected_items = sorted(selected.values(), key=lambda item: item["id"])
    return {
        "format_version": 1,
        "mode": "oasst1_semantic_candidate_review_queue",
        "source_id": SOURCE_ID,
        "minimum_review_turns": MINIMUM_REVIEW_TURNS,
        "items_per_candidate_topic": items_per_topic,
        "source_records": len(materialized_records),
        "multi_turn_source_records": multi_turn_source_records,
        "eligible_candidate_records": len(candidates),
        "eligible_records_by_candidate_topic": eligible_by_topic,
        "excluded_by_quality_reason": dict(sorted(quality_rejections.items())),
        "selected_records": len(selected_items),
        "selected_by_candidate_topic": dict(sorted(selection_by_topic.items())),
        "review_status": "unreviewed",
        "candidate_labels_are_unverified": True,
        "selection_strategy": [
            "only Russian OASST1 dialogue records with at least four turns",
            "conservative user-turn cue groups produce candidate labels and static reason codes",
            "exclude a user explicit rejection or restated unmet request after an assistant answer",
            "one deterministic candidate per available split, then a capped topic quota",
            "human review is required before any semantic relabeling or training use",
        ],
        "coverage": _coverage_snapshot(selected_items),
        "items": selected_items,
        "active_manifest_updated": False,
        "training_started": False,
    }


def review_queue_markdown(report: dict[str, Any]) -> str:
    """Рендерит Markdown без source text, URL и скрытого user content."""
    coverage = report["coverage"]
    lines = [
        "# Brain v3 — OASST1 semantic candidate review queue v0.4",
        "",
        "## Статус",
        "",
        "- Статус всех записей: `unreviewed`.",
        "- Candidate labels получены эвристикой и не являются подтверждённой разметкой.",
        f"- Проверено source-записей: {report['source_records']}.",
        f"- Многоходовых source-записей: {report['multi_turn_source_records']}.",
        f"- Многоходовых semantic candidates: {report['eligible_candidate_records']}.",
        f"- В ручную очередь выбрано: {report['selected_records']}.",
        f"- Исключено до review по проверяемым quality-сигналам: {report['excluded_by_quality_reason']}.",
        "- В отчёте есть только IDs, SHA-256 и reason codes; ни user, ни assistant реплики не сохраняются.",
        "- Active manifest, raw source, API, веса и обучение не меняются.",
        "",
        "## Candidate coverage",
        "",
        f"- Eligible по candidate topic: {report['eligible_records_by_candidate_topic']}.",
        f"- Выбрано по candidate topic: {report['selected_by_candidate_topic']}.",
        f"- Split: {coverage['splits']}.",
        f"- Длины диалогов: {coverage['turn_counts']}.",
        f"- Primary candidate topics: {coverage['primary_candidate_topics']}.",
        f"- Все candidate topics: {coverage['all_candidate_topics']}.",
        "",
        "## Обязательное решение reviewer",
        "",
        "Для каждой выбранной ID нужно локально проверить естественность русского языка, связность follow-up, уместность ответа, отсутствие личных данных, лишних ссылок, ложной памяти и небезопасных советов. Только отдельная человеческая запись `approved` или `rejected` может стать основанием для будущей semantic-разметки. Эта очередь сама ничего не одобряет и не запускает обучение.",
        "",
    ]
    return "\n".join(lines)


def create_semantic_review_queue(
    source_path: Path = DEFAULT_SOURCE_PATH,
    report_directory: Path = DEFAULT_REPORT_DIRECTORY,
    *,
    items_per_topic: int = REVIEW_ITEMS_PER_CANDIDATE_TOPIC,
    raw_root: Path = RAW_ROOT,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    """Создаёт один versioned metadata-only report без перезаписи истории."""
    records, source_sha256 = _read_jsonl_source(source_path, raw_root=raw_root)
    report = select_semantic_review_queue(
        records,
        items_per_topic=items_per_topic,
    )
    report["source_path"] = str(source_path.expanduser().resolve())
    report["source_sha256"] = source_sha256

    destination = _safe_path_inside(
        report_directory,
        results_root,
        "oasst1_semantic_review_report_outside_results",
    )
    if destination.exists():
        raise OASST1SemanticReviewError("oasst1_semantic_review_report_exists")
    destination.mkdir(parents=True)
    (destination / "oasst1_semantic_review_queue.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (destination / "OASST1_SEMANTIC_REVIEW_QUEUE.md").write_text(
        review_queue_markdown(report),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Запускает local-only очередь review без training, promotion или сети."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    parser.add_argument("--items-per-topic", type=int, default=REVIEW_ITEMS_PER_CANDIDATE_TOPIC)
    args = parser.parse_args()
    try:
        report = create_semantic_review_queue(
            args.source,
            args.report_directory,
            items_per_topic=args.items_per_topic,
        )
    except (OASST1SemanticReviewError, OSError, ValueError) as error:
        raise SystemExit(f"OASST1 semantic review queue остановлена: {error}") from error
    print(
        json.dumps(
            {
                "source_sha256": report["source_sha256"],
                "source_records": report["source_records"],
                "eligible_candidate_records": report["eligible_candidate_records"],
                "selected_records": report["selected_records"],
                "review_status": report["review_status"],
                "candidate_labels_are_unverified": report["candidate_labels_are_unverified"],
                "active_manifest_updated": report["active_manifest_updated"],
                "training_started": report["training_started"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
