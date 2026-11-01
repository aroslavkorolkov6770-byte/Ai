"""Локальный поиск подходящего LMS-контекста для единого чата.

ReliableLms умеет выбирать политику и факт после получения контекста. Этот
модуль решает отдельную задачу: по обычной реплике пользователя без
переключателей и скрытых полей найти только достаточно подтверждённый контекст
в локально импортированном наборе.

Если в наборе есть несколько правдоподобных, но разных правил, индекс не
угадывает значение. Он возвращает признак неоднозначности, а API просит
уточнение обычной репликой в том же диалоге.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .lms_dataset import SPLITS, build_context_grouped_splits, context_key
from .reliable_qa import ROOT, normalize


INDEX_FORMAT_VERSION = 1
DEFAULT_LMS_KNOWLEDGE_INDEX = (
    ROOT / "model_artifacts" / "reliable_lms" / "knowledge_index.json"
)

# Слова ниже служат только для отбора кандидатов. Они не определяют ответ:
# итоговый факт по-прежнему выбирает обученная LMS-модель по полному контексту.
STOPWORDS = frozenset(
    {
        "а",
        "без",
        "в",
        "во",
        "вы",
        "где",
        "да",
        "для",
        "до",
        "его",
        "ее",
        "её",
        "и",
        "из",
        "или",
        "как",
        "какая",
        "какие",
        "какой",
        "когда",
        "ли",
        "мне",
        "мой",
        "моя",
        "на",
        "не",
        "нет",
        "но",
        "о",
        "об",
        "он",
        "она",
        "они",
        "по",
        "под",
        "пожалуйста",
        "про",
        "с",
        "со",
        "сколько",
        "там",
        "то",
        "у",
        "что",
        "это",
        "я",
    }
)
COURSE_PATTERN = re.compile(
    r"курс[а-я]*\s*(?:«([^»]+)»|:\s*([^.;\n]+))",
    flags=re.IGNORECASE,
)
WORD_PATTERN = re.compile(r"[a-zа-яё0-9]+", flags=re.IGNORECASE)

# Это небольшой слой смысловых тем, а не список заготовленных диалогов. Он
# нужен, чтобы выражения «что нужно набрать, чтобы зачли обучение?» и «какой
# проходной балл?» попадали в одну группу кандидатов. Конкретный ответ всё
# равно берётся только из найденного LMS-контекста.
SEMANTIC_TOPIC_MARKERS: dict[str, tuple[str, ...]] = {
    "passing_score": (
        "проходн",
        "балл",
        "минимум",
        "минимальн",
        "процент",
        "результат",
        "зач",
        "успешн",
        "заверш",
        "сдать",
    ),
    "attempt_count": (
        "попыт",
        "пересда",
        "повторн",
        "сколько раз",
        "количество раз",
    ),
    "access_period": (
        "доступ",
        "срок",
        "как долго",
        "до какого",
        "до каких",
        "закро",
        "активн",
        "длится",
        "длительн",
    ),
    "certificate": ("сертифик", "удостоверен", "документ о прохождении"),
    "support": ("поддержк", "консультац", "помощ", "куратор"),
    "schedule": ("график", "расписан", "дата", "время", "когда"),
}


def _term_root(term: str) -> str:
    """Сводит словоформы к короткому устойчивому началу слова.

    В русском языке «охрана» и «охране» являются одним названием курса для
    пользователя, хотя в строковом сравнении это разные слова. Здесь нет
    тяжёлого внешнего словаря: достаточно безопасного общего начала слова.
    """
    return term[:5] if len(term) >= 5 else term


def _term_roots(text: str) -> frozenset[str]:
    """Возвращает корни смысловых слов для мягкого поиска названия курса."""
    return frozenset(_term_root(term) for term in _terms(text))


def _has_topic_marker(text: str, marker: str) -> bool:
    """Ищет маркер темы как фразу или начало отдельного слова."""
    normalized = normalize(text)
    if " " in marker:
        return marker in normalized
    return any(word.startswith(marker) for word in WORD_PATTERN.findall(normalized))


def _semantic_topics(text: str) -> frozenset[str]:
    """Определяет тему вопроса без подстановки какого-либо факта."""
    return frozenset(
        topic
        for topic, markers in SEMANTIC_TOPIC_MARKERS.items()
        if any(_has_topic_marker(text, marker) for marker in markers)
    )


def _terms(text: str) -> frozenset[str]:
    """Возвращает нормализованные смысловые слова для безопасного сравнения."""
    words = WORD_PATTERN.findall(normalize(text))
    return frozenset(
        word
        for word in words
        if word not in STOPWORDS and (len(word) >= 3 or word.isdigit())
    )


def _course_titles(text: str) -> frozenset[str]:
    """Извлекает названия курсов из естественной фразы или LMS-контекста."""
    titles: set[str] = set()
    for quoted_title, plain_title in COURSE_PATTERN.findall(text):
        title = normalize(quoted_title or plain_title).strip()
        if title:
            titles.add(title)
    return frozenset(titles)


def _fingerprint(value: str) -> str:
    """Даёт стабильный идентификатор контекста без его изменения."""
    return hashlib.sha256(normalize(value).encode("utf-8")).hexdigest()


def _question_part(text: str) -> str:
    """Отделяет последний вопрос от деталей курса в той же реплике.

    Пользователь может написать: «Курс X, доступ 45 дней. Какой проходной
    балл?». Первая часть нужна поиску кандидата, а для сравнения с обучающими
    вопросами важна только вторая.
    """
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if sentence.strip()
    ]
    questions = [sentence for sentence in sentences if "?" in sentence]
    return questions[-1] if questions else text


@dataclass(frozen=True)
class LmsKnowledgeMatch:
    """Результат безопасного выбора одного контекста из локального индекса."""

    context: str | None
    context_id: str | None
    source: str
    score: float
    candidate_count: int
    needs_clarification: bool
    candidate_courses: tuple[str, ...] = ()
    semantic_topics: tuple[str, ...] = ()


@dataclass(frozen=True)
class _KnowledgeEntry:
    """Один уникальный LMS-контекст и все связанные с ним вопросы."""

    context: str
    context_id: str
    questions: tuple[str, ...]
    task_types: tuple[str, ...]
    course_titles: frozenset[str]
    context_terms: frozenset[str]
    question_terms: frozenset[str]
    context_topics: frozenset[str]
    question_topics: frozenset[str]


def build_knowledge_payload(seed: int = 42) -> dict:
    """Строит воспроизводимый индекс из всех уникальных контекстных групп.

    Строгое разбиение остаётся источником оценки модели. Здесь объединяются все
    уже импортированные строки после удаления точных дублей: это локальная
    справочная база продукта, а не новая оценка качества checkpoint.
    """
    splits, split_report = build_context_grouped_splits(seed=seed)
    rows = [
        row
        for split in SPLITS
        for row in splits[split]
    ]
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(context_key(row), []).append(row)

    entries: list[dict] = []
    for key in sorted(groups):
        group = groups[key]
        context = group[0].context
        entries.append(
            {
                "context": context,
                "context_id": _fingerprint(context),
                "questions": sorted({row.user for row in group}),
                "task_types": sorted({row.task_type for row in group}),
            }
        )

    return {
        "format_version": INDEX_FORMAT_VERSION,
        "source": "full-deduplicated-context-groups",
        "split_seed": seed,
        "source_records": len(rows),
        "context_groups": len(entries),
        "source_report": {
            "raw_records": split_report["raw_records"],
            "duplicate_records_removed": split_report[
                "duplicate_records_removed"
            ],
            "unique_context_user_pairs": split_report[
                "unique_context_user_pairs"
            ],
        },
        "entries": entries,
    }


def write_knowledge_index(
    path: Path = DEFAULT_LMS_KNOWLEDGE_INDEX,
    seed: int = 42,
) -> dict:
    """Создаёт отдельный JSON-артефакт, не изменяя исходные JSONL-файлы."""
    payload = build_knowledge_payload(seed=seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    path.write_text(f"{serialized}\n", encoding="utf-8")
    return payload


class LmsKnowledgeIndex:
    """Индексирует локальные LMS-контексты и отказывается при неоднозначности."""

    def __init__(self, payload: dict) -> None:
        if payload.get("format_version") != INDEX_FORMAT_VERSION:
            raise ValueError("Неизвестный формат LMS-индекса.")
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError("LMS-индекс не содержит записей.")

        entries: list[_KnowledgeEntry] = []
        for position, raw_entry in enumerate(raw_entries, start=1):
            if not isinstance(raw_entry, dict):
                raise ValueError(
                    f"LMS-индекс: запись {position} должна быть объектом."
                )
            context = raw_entry.get("context")
            context_id = raw_entry.get("context_id")
            questions = raw_entry.get("questions")
            task_types = raw_entry.get("task_types")
            if not isinstance(context, str) or not context.strip():
                raise ValueError(
                    f"LMS-индекс: запись {position} не содержит контекст."
                )
            if not isinstance(context_id, str) or len(context_id) != 64:
                raise ValueError(
                    f"LMS-индекс: запись {position} содержит неверный context_id."
                )
            if (
                not isinstance(questions, list)
                or not questions
                or not all(
                    isinstance(question, str) and question.strip()
                    for question in questions
                )
            ):
                raise ValueError(
                    f"LMS-индекс: запись {position} не содержит вопросов."
                )
            if (
                not isinstance(task_types, list)
                or not task_types
                or not all(isinstance(task_type, str) for task_type in task_types)
            ):
                raise ValueError(
                    f"LMS-индекс: запись {position} не содержит типов задач."
                )
            if context_id != _fingerprint(context):
                raise ValueError(
                    f"LMS-индекс: запись {position} не прошла проверку context_id."
                )

            cleaned_questions = tuple(
                sorted({question.strip() for question in questions})
            )
            entries.append(
                _KnowledgeEntry(
                    context=context.strip(),
                    context_id=context_id,
                    questions=cleaned_questions,
                    task_types=tuple(sorted(set(task_types))),
                    course_titles=_course_titles(context),
                    context_terms=_terms(context),
                    question_terms=frozenset(
                        term
                        for question in cleaned_questions
                        for term in _terms(question)
                    ),
                    context_topics=_semantic_topics(context),
                    question_topics=frozenset(
                        topic
                        for question in cleaned_questions
                        for topic in _semantic_topics(question)
                    ),
                )
            )

        self.entries = tuple(entries)
        self.source = str(payload.get("source", "unknown"))
        self.source_records = int(payload.get("source_records", 0))
        self.context_groups = int(payload.get("context_groups", len(entries)))
        if self.context_groups != len(entries):
            raise ValueError(
                "LMS-индекс содержит неверное число контекстных групп."
            )

        self._exact_questions: dict[str, set[int]] = defaultdict(set)
        self._question_terms: dict[str, set[int]] = defaultdict(set)
        self._courses: dict[str, set[int]] = defaultdict(set)
        self._topic_entries: dict[str, set[int]] = defaultdict(set)
        for index, entry in enumerate(self.entries):
            for question in entry.questions:
                normalized_question = normalize(question)
                if normalized_question:
                    self._exact_questions[normalized_question].add(index)
            for term in entry.question_terms:
                self._question_terms[term].add(index)
            for title in entry.course_titles:
                self._courses[title].add(index)
            for topic in entry.context_topics | entry.question_topics:
                self._topic_entries[topic].add(index)

    @classmethod
    def from_path(
        cls,
        path: Path = DEFAULT_LMS_KNOWLEDGE_INDEX,
    ) -> "LmsKnowledgeIndex":
        """Загружает и валидирует ранее созданный индекс."""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"Не удалось прочитать LMS-индекс: {path}."
            ) from error
        if not isinstance(payload, dict):
            raise ValueError("Корень LMS-индекса должен быть объектом.")
        return cls(payload)

    @classmethod
    def from_payload(cls, payload: dict) -> "LmsKnowledgeIndex":
        """Создаёт индекс в тесте без записи файлов на диск."""
        return cls(payload)

    @staticmethod
    def _course_title_matches_text(title: str, text: str) -> bool:
        """Находит название курса и без кавычек, учитывая русские падежи."""
        normalized_text = normalize(text)
        if title in normalized_text:
            return True

        title_terms = _terms(title)
        if not title_terms:
            return False
        title_roots = {_term_root(term) for term in title_terms}
        text_roots = _term_roots(text)
        shared_roots = title_roots & text_roots
        if not shared_roots:
            return False
        if len(title_roots) == 1:
            return True

        # Одно длинное характерное слово («грамотности», «Excel») достаточно
        # для названия из двух слов. Короткое общее слово («труда») само по
        # себе не выбирает курс «Охрана труда».
        longest_root = _term_root(max(title_terms, key=len))
        if longest_root in shared_roots:
            return True
        return len(shared_roots) >= 2

    def _matching_course_titles(self, text: str) -> frozenset[str]:
        """Собирает явные и естественно названные в диалоге курсы."""
        titles = set(_course_titles(text))
        for title in self._courses:
            if self._course_title_matches_text(title, text):
                titles.add(title)
        return frozenset(titles)

    def context_matches_prompt(self, prompt: str, context: str) -> bool:
        """Не переносит старый LMS-контекст на явно названный другой курс."""
        prompt_titles = self._matching_course_titles(prompt)
        if not prompt_titles:
            return True
        return bool(prompt_titles & _course_titles(context))

    def _candidate_indices(
        self,
        prompt: str,
        profile_text: str,
    ) -> tuple[set[int], str]:
        """Собирает кандидатов, не выбирая контекст по одному общему слову."""
        normalized_prompt = normalize(prompt)
        exact = set(self._exact_questions.get(normalized_prompt, set()))
        profile_titles = self._matching_course_titles(profile_text)
        course_candidates: set[int] = set()
        for title in profile_titles:
            course_candidates.update(self._courses.get(title, set()))

        prompt_terms = _terms(prompt)
        term_candidates: set[int] = set()
        for term in prompt_terms:
            term_candidates.update(self._question_terms.get(term, set()))

        prompt_topics = _semantic_topics(prompt)
        topic_candidates: set[int] = set()
        for topic in prompt_topics:
            topic_candidates.update(self._topic_entries.get(topic, set()))

        if course_candidates:
            # Название курса — самый сильный фактический ключ. Внутри него
            # дальше сравниваются вопрос и дополнительные детали диалога.
            # Сначала быстро отсекаем варианты, которые не содержат явно
            # написанных пользователем отличительных данных. Это уменьшает
            # число дорогих строковых сравнений и не выбирает ответ само по
            # себе: окончательная проверка уверенности остаётся в resolve.
            extra_profile_terms = _terms(profile_text) - prompt_terms
            if len(course_candidates) > 1 and extra_profile_terms:
                evidence = {
                    index: len(
                        extra_profile_terms
                        & self.entries[index].context_terms
                    )
                    for index in course_candidates
                }
                max_hits = max(evidence.values(), default=0)
                if max_hits >= 2:
                    floor = max(2, max_hits - 1)
                    narrowed = {
                        index
                        for index, hits in evidence.items()
                        if hits >= floor
                    }
                    if narrowed:
                        course_candidates = narrowed
            return course_candidates, "course"
        if exact:
            return exact, "exact-question"
        if len(prompt_terms) >= 2 and term_candidates:
            # Совпавшие слова вроде «тест» встречаются почти во всех курсах.
            # Если тема понятна, оставляем среди них только её кандидатов.
            narrowed_by_topic = term_candidates & topic_candidates
            if narrowed_by_topic:
                return narrowed_by_topic, "question-terms"
            return term_candidates, "question-terms"
        if topic_candidates:
            topic_name = "-".join(sorted(prompt_topics))
            return topic_candidates, f"semantic-{topic_name}"
        return set(), "none"

    @staticmethod
    def _question_similarity(prompt: str, entry: _KnowledgeEntry) -> float:
        """Берёт лучшую строковую близость среди вопросов одного контекста."""
        normalized_prompt = normalize(prompt)
        return max(
            (
                SequenceMatcher(
                    a=normalized_prompt,
                    b=normalize(question),
                    autojunk=False,
                ).ratio()
                for question in entry.questions
            ),
            default=0.0,
        )

    @staticmethod
    def _overlap_score(
        terms: frozenset[str],
        candidate_terms: frozenset[str],
    ) -> tuple[float, int]:
        """Считает совпадения профиля с контекстом без искусственного порога."""
        if not terms:
            return 0.0, 0
        hits = terms & candidate_terms
        return len(hits) / len(terms), len(hits)

    def _score(
        self,
        prompt: str,
        profile_text: str,
        entry: _KnowledgeEntry,
    ) -> tuple[float, int]:
        """Оценивает кандидата по вопросу, курсу и деталям из реплик пользователя."""
        prompt_terms = _terms(prompt)
        profile_terms = _terms(profile_text)
        question_similarity = self._question_similarity(prompt, entry)
        question_overlap, _ = self._overlap_score(
            prompt_terms,
            entry.question_terms,
        )
        context_overlap, context_hits = self._overlap_score(
            profile_terms,
            entry.context_terms,
        )
        profile_titles = self._matching_course_titles(profile_text)
        course_match = float(bool(profile_titles & entry.course_titles))
        prompt_topics = _semantic_topics(prompt)
        entry_topics = entry.context_topics | entry.question_topics
        topic_match = (
            len(prompt_topics & entry_topics) / len(prompt_topics)
            if prompt_topics
            else 0.0
        )

        # Близость вопроса определяет релевантность; курс и дополнительные
        # данные из реплик помогают выбрать конкретную версию правил.
        score = (
            0.32 * question_similarity
            + 0.18 * question_overlap
            + 0.22 * context_overlap
            + 0.16 * course_match
            + 0.12 * topic_match
        )
        return score, context_hits

    def _candidate_courses(self, candidate_indices: set[int]) -> tuple[str, ...]:
        """Возвращает несколько названий для понятного запроса уточнения."""
        titles = sorted(
            {
                title
                for index in candidate_indices
                for title in self.entries[index].course_titles
            }
        )
        return tuple(titles[:5])

    def resolve(
        self,
        prompt: str,
        previous_user_messages: list[str] | None = None,
    ) -> LmsKnowledgeMatch:
        """Возвращает один доказуемый контекст или безопасную неоднозначность.

        Предыдущие реплики пользователя учитываются как естественно введённые
        детали беседы. Ответы ассистента специально не участвуют в поиске, чтобы
        собственный старый ответ не превратился в доказательство нового факта.
        """
        clean_prompt = " ".join(prompt.split())
        if not clean_prompt:
            return LmsKnowledgeMatch(
                context=None,
                context_id=None,
                source="lms-knowledge-empty-query",
                score=0.0,
                candidate_count=0,
                needs_clarification=True,
            )
        previous = previous_user_messages or []
        profile_text = "\n".join([*previous[-6:], clean_prompt])
        question_text = _question_part(clean_prompt)
        question_topics = _semantic_topics(question_text)
        candidate_indices, selection_source = self._candidate_indices(
            question_text,
            profile_text,
        )
        if not candidate_indices:
            return LmsKnowledgeMatch(
                context=None,
                context_id=None,
                source="lms-knowledge-no-match",
                score=0.0,
                candidate_count=0,
                needs_clarification=True,
                semantic_topics=tuple(sorted(question_topics)),
            )

        ranked: list[tuple[float, int, int]] = []
        for index in candidate_indices:
            score, context_hits = self._score(
                question_text,
                profile_text,
                self.entries[index],
            )
            ranked.append((score, context_hits, index))
        ranked.sort(
            key=lambda item: (
                -item[0],
                -item[1],
                self.entries[item[2]].context_id,
            )
        )
        top_score, top_hits, top_index = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else -1.0
        second_hits = ranked[1][1] if len(ranked) > 1 else -1
        exact_question = normalize(question_text) in self._exact_questions
        profile_titles = self._matching_course_titles(profile_text)
        top_entry = self.entries[top_index]
        topic_is_supported = bool(
            question_topics & (top_entry.context_topics | top_entry.question_topics)
        )

        # Один точный вопрос с единственным контекстом безопасно использовать.
        if len(ranked) == 1 and (
            exact_question
            or top_score >= 0.54
            or top_hits >= 4
            or (topic_is_supported and top_hits >= 2)
        ):
            entry = top_entry
            return LmsKnowledgeMatch(
                context=entry.context,
                context_id=entry.context_id,
                source=f"lms-knowledge-{selection_source}",
                score=top_score,
                candidate_count=1,
                needs_clarification=False,
                candidate_courses=tuple(sorted(entry.course_titles)),
                semantic_topics=tuple(sorted(question_topics)),
            )

        # Когда датасет содержит одну и ту же формулировку с разными правилами,
        # нужен не просто самый похожий текст, а отчётливо более сильный контекст.
        # Четыре совпадения обычно означают название курса плюс несколько
        # видимых характеристик вроде срока и роли. Нужен также заметный
        # отрыв по фактам, а не только по похожей формулировке вопроса.
        decisive_details = top_hits >= 4
        decisive_margin = (
            top_score - second_score >= 0.04
            and top_hits - second_hits >= 2
        )
        has_named_course = bool(profile_titles & top_entry.course_titles)
        question_is_relevant = top_score >= 0.43 and topic_is_supported
        if (
            has_named_course
            and decisive_details
            and decisive_margin
            and question_is_relevant
        ):
            entry = top_entry
            return LmsKnowledgeMatch(
                context=entry.context,
                context_id=entry.context_id,
                source=f"lms-knowledge-{selection_source}",
                score=top_score,
                candidate_count=len(ranked),
                needs_clarification=False,
                candidate_courses=tuple(sorted(entry.course_titles)),
                semantic_topics=tuple(sorted(question_topics)),
            )

        return LmsKnowledgeMatch(
            context=None,
            context_id=None,
            source="lms-knowledge-ambiguous",
            score=top_score,
            candidate_count=len(ranked),
            needs_clarification=True,
            candidate_courses=self._candidate_courses(candidate_indices),
            semantic_topics=tuple(sorted(question_topics)),
        )
