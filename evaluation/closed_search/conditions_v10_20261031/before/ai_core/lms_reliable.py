"""Собственная LMS-модель: нейронный выбор политики и извлечение фактов из контекста.

Модуль не использует готовые языковые модели. Нейросеть определяет безопасный
тип LMS-задачи, а текст ответа строится только из локального контекста или из
фиксированных безопасных формулировок. Это исключает посимвольную бессмыслицу
свободного декодирования.
"""

from __future__ import annotations

import hashlib
import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import torch
from torch import nn

from .lms_dataset import TASK_TYPES
from .reliable_qa import ROOT, normalize


DEFAULT_LMS_ARTIFACT = ROOT / "model_artifacts" / "reliable_lms" / "lms.pt"
LMS_CLARIFICATION = (
    "Я не могу уверенно определить нужное действие по этому контексту. "
    "Уточните курс, раздел или конкретную ситуацию."
)
LMS_NO_CONTEXT = (
    "Для точного ответа нужен контекст LMS: правила курса, сведения о доступе "
    "или фрагмент документа. Без него я не буду придумывать данные."
)
STOPWORDS = frozenset(
    "и в во на с со по о об к ко из от для что как какой какая какие "
    "какой-то мне мне-то пожалуйста можно нужно надо ли а но или это этот "
    "эта эти его ее еe ей ему их у за через про под над при не нет да".split()
)
DATE_PATTERN = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
FACT_SPLITTER = re.compile(r"(?<=[.;!?])\s+")
FIELD_ROOTS = (
    "проходн",
    "балл",
    "зач",
    "попыт",
    "пересда",
    "сдат",
    "доступ",
    "срок",
    "консультац",
    "поддержк",
    "сертифик",
    "аттестац",
    "стоим",
    "скид",
    "график",
    "дат",
    "врем",
    "раздел",
    "меню",
    "вкладк",
)
INJECTION_MARKERS = (
    "игнорируй",
    "игнорировать",
    "забудь",
    "скрыт",
    "системн",
    "промпт",
    "покажи инструкц",
    "раскрой инструкц",
    "внутренние инструкц",
    "обойди",
    "отмени огранич",
    "считай этот текст",
    "бесконечн",
    "придумай",
)
ADMIN_MARKERS = (
    "аккаунт",
    "профил",
    "оплат",
    "списал",
    "возврат",
    "блокир",
    "спорная оценка",
)
WEB_MARKERS = (
    "интернет",
    "в сети",
    "поиск",
    "найди",
    "новост",
    "общедоступн",
)
SUMMARY_MARKERS = (
    "кратк",
    "сводк",
    "важн",
    "главн",
    "перечисл",
    "услови",
    "резюм",
    "суммир",
)
SOURCE_QUESTION_MARKERS = (
    "источник",
    "документ",
    "где написано",
    "где написа",
    "каким документ",
    "на что опира",
)
NAVIGATION_QUESTION_MARKERS = (
    "раздел",
    "меню",
    "страниц",
    "личн кабинет",
    "перейти",
    "открыть",
    "куда наж",
    "посмотреть",
)
NAVIGATION_CONTEXT_MARKERS = (
    "раздел",
    "меню",
    "вкладк",
    "страниц",
    "личн кабинет",
)


def _words(text: str) -> list[str]:
    """Выделяет смысловые слова с собственной нормализацией проекта."""
    return [word for word in normalize(text).split() if len(word) > 1 and word not in STOPWORDS]


def _feature_set(user: str, context: str, include_structural: bool = False) -> set[str]:
    """Создаёт признаки вопроса и контекста без внешнего словаря."""
    user_words = _words(user)
    context_words = _words(context)
    features: set[str] = set()
    for prefix, words in (("u", user_words), ("c", context_words)):
        features.update(f"{prefix}:w:{word}" for word in words)
        features.update(
            f"{prefix}:b:{first}/{second}" for first, second in zip(words, words[1:])
        )
    for word in user_words:
        marked = f"^{word}$"
        for size in (3, 4):
            features.update(
                f"u:c:{marked[index:index + size]}"
                for index in range(max(0, len(marked) - size + 1))
            )
    shared = set(user_words) & set(context_words)
    features.update(f"x:shared:{word}" for word in shared)
    if DATE_PATTERN.search(context):
        features.add("c:has-date")
    if any(character.isdigit() for character in context):
        features.add("c:has-number")
    if "источник" in context.casefold():
        features.add("c:has-source")
    if include_structural:
        features.update(f"s:{signal}" for signal in _structural_signals(user, context))
    return features


class LmsHashEncoder:
    """Стабильный хеширующий кодировщик, не требующий огромной матрицы словаря."""

    def __init__(self, dimension: int = 1024, include_structural: bool = False):
        if dimension < 128:
            raise ValueError("Размер LMS-признаков должен быть не меньше 128.")
        self.dimension = dimension
        self.include_structural = include_structural

    def _index(self, feature: str) -> int:
        return zlib.crc32(feature.encode("utf-8")) % self.dimension

    def transform(self, pairs: Sequence[tuple[str, str]]) -> torch.Tensor:
        """Создаёт нормализованную плотную матрицу небольшими контролируемыми блоками."""
        matrix = torch.zeros((len(pairs), self.dimension), dtype=torch.float32)
        for row, (user, context) in enumerate(pairs):
            for feature in _feature_set(user, context, self.include_structural):
                matrix[row, self._index(feature)] += 1.0
        return nn.functional.normalize(matrix, dim=1)


def _fact_feature_set(user: str, fact: str, task_type: str) -> set[str]:
    """Кодирует связь вопроса с одним фактом контекста для обучаемого ранжирования."""
    user_words = _words(user)
    fact_words = _words(fact)
    features = {f"t:{task_type}"}
    features.update(f"u:{word}" for word in user_words)
    features.update(f"f:{word}" for word in fact_words)
    features.update(f"same:{word}" for word in set(user_words) & set(fact_words))
    # Перекрёстные признаки позволяют линейной сети выучить, что вопрос о
    # проходном балле связан с фразой «нужно набрать не менее», даже без
    # одинакового слова в обеих частях.
    for question_word in user_words[:14]:
        for fact_word in fact_words[:20]:
            features.add(f"pair:{task_type}:{question_word}/{fact_word}")
    return features


class LmsFactHashEncoder:
    """Небольшой хеширующий кодировщик пар «вопрос + факт + тип задачи»."""

    def __init__(self, dimension: int = 512):
        if dimension < 128:
            raise ValueError("Размер признаков выбора факта должен быть не меньше 128.")
        self.dimension = dimension

    def transform(self, triples: Sequence[tuple[str, str, str]]) -> torch.Tensor:
        matrix = torch.zeros((len(triples), self.dimension), dtype=torch.float32)
        for row, (user, fact, task_type) in enumerate(triples):
            for feature in _fact_feature_set(user, fact, task_type):
                index = zlib.crc32(feature.encode("utf-8")) % self.dimension
                matrix[row, index] += 1.0
        return nn.functional.normalize(matrix, dim=1)


class LmsIntentNetwork(nn.Module):
    """Небольшая MLP для выбора одной политики LMS-ответа."""

    def __init__(self, input_dim: int, classes: int, hidden_dim: int = 96):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.Tanh(),
            nn.Dropout(0.10),
            nn.Linear(hidden_dim, classes),
        )

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return self.layers(inputs)


class LmsFactNetwork(nn.Module):
    """Обучаемый линейный ранжировщик фактов внутри уже переданного контекста."""

    def __init__(self, input_dim: int):
        super().__init__()
        self.layer = nn.Linear(input_dim, 1)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return self.layer(inputs).squeeze(dim=1)


@dataclass(frozen=True)
class LmsPrediction:
    """Полный ответ LMS, выбранная политика и признаки безопасного отказа."""

    text: str
    task_type: str | None
    confidence: float
    accepted: bool
    source: str
    context_used: bool


def _clean_text(text: str) -> str:
    return " ".join(text.split())


def _facts(context: str) -> list[str]:
    """Делит компактный LMS-контекст на проверяемые факты."""
    clean = _clean_text(context)
    return [fact.strip() for fact in FACT_SPLITTER.split(clean) if fact.strip()]


def _field_roots(text: str) -> set[str]:
    """Находит типовые LMS-поля по началу слова, учитывая русские окончания."""
    words = _words(text)
    return {root for root in FIELD_ROOTS if any(word.startswith(root) for word in words)}


def _phrases(text: str) -> set[tuple[str, ...]]:
    """Возвращает двух- и трёхсловные смысловые выражения вопроса или факта."""
    words = _words(text)
    result = set()
    for size in (2, 3):
        result.update(tuple(words[index:index + size]) for index in range(len(words) - size + 1))
    return result


def _has_marker(text: str, markers: Iterable[str]) -> bool:
    """Проверяет безопасные лексические маркеры без внешнего словаря."""
    lowered = text.casefold()
    return any(marker in lowered for marker in markers)


def _course_titles(context: str) -> set[str]:
    """Извлекает названия курсов, чтобы отличить один ответ от неоднозначности."""
    titles = set()
    for quoted, plain in re.findall(
        r"курс[а-я]*\s*(?:«([^»]+)»|:\s*([^.;]+))",
        context,
        flags=re.IGNORECASE,
    ):
        title = normalize(quoted or plain).strip()
        if title:
            titles.add(title)
    return titles


def _question_names_course(user: str, titles: set[str]) -> bool:
    """Не заставляет уточнять, когда пользователь уже назвал нужный курс."""
    normalized_user = normalize(user)
    return any(title in normalized_user for title in titles)


def _is_conflicting_context(context: str) -> bool:
    """Находит конфликт нескольких датированных источников до ответа на факт."""
    lowered = context.casefold()
    dates = len(DATE_PATTERN.findall(context))
    source_count = lowered.count("источник") + lowered.count("официальн")
    return (
        any(marker in lowered for marker in ("противореч", "расхожд", "приоритет имеет"))
        or (source_count >= 2 and dates >= 2)
    )


def _asks_passing_score(text: str) -> bool:
    """Понимает естественные вопросы о результате для зачёта курса.

    Пользователь не обязан знать формулировку из базы. Например, «что нужно
    набрать, чтобы зачли обучение?» относится к проходному результату так же,
    как и короткое «какой проходной балл?».
    """
    return any(
        marker in text
        for marker in (
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
        )
    )


def _is_fact_question(user: str, context: str) -> bool:
    """Проверяет, есть ли в контексте поле, о котором явно спрашивает пользователь."""
    question_fields = _field_roots(user)
    context_fields = _field_roots(context)
    if question_fields & context_fields:
        return True
    lowered = user.casefold()
    asks_about_date = any(
        marker in lowered for marker in ("когда", "дат", "до какого", "до какой", "срок")
    )
    if asks_about_date and DATE_PATTERN.search(context):
        return True
    # В регламентах проходной балл часто выражен не словом «балл», а фразой
    # «нужно набрать не менее N%». Это всё равно проверяемый ответ на вопрос
    # о проходном результате, а не отсутствующая информация.
    asks_passing_score = _asks_passing_score(lowered)
    context_lowered = context.casefold()
    return (
        asks_passing_score
        and "тест" in context_lowered
        and any(character.isdigit() for character in context)
        and any(
            marker in context_lowered
            for marker in ("набрать", "требуется", "нужно", "пройден")
        )
    )


def _is_attempt_count_question(user: str, context: str) -> bool:
    """Распознаёт попытки и пересдачу в естественных формулировках."""
    lowered_user = user.casefold()
    asks_count = (
        "сколько раз" in lowered_user
        or "количество раз" in lowered_user
        or (
            "сколько" in lowered_user
            and any(marker in lowered_user for marker in ("попыт", "пересда", "сдавать"))
        )
    )
    asks_repeat_permission = any(
        marker in lowered_user
        for marker in ("пересда", "пересдать", "повторно сдать", "повторить тест")
    ) and any(marker in lowered_user for marker in ("можно", "разреш", "доступ"))
    return (asks_count or asks_repeat_permission) and any(
        marker in context.casefold()
        for marker in ("попыт", "пересда", "разрешено", "допускается", "сдавать")
    )


def _is_missing_fact_question(user: str, context: str) -> bool:
    """Отделяет отсутствующий факт от простого непохожего свободного текста."""
    lowered = user.casefold()
    starts_information_request = bool(
        re.search(r"\b(кто|есть ли|какой|какая|какие|сколько|когда|где|можно ли)\b", lowered)
    ) or "предусмотрен" in lowered
    if not starts_information_request:
        return False
    question_words = set(_words(user))
    context_words = set(_words(context))
    # Одно общее слово вроде «курс» ещё не означает, что ответ действительно
    # есть в документах. Нужен хотя бы один неизвестный смысловой запрос.
    unknown_words = question_words - context_words - {"курс", "обучение", "материал"}
    return bool(unknown_words) and not (
        _is_fact_question(user, context) or _is_attempt_count_question(user, context)
    )


def _structured_task(user: str, context: str) -> str | None:
    """Выбирает только безопасные явно распознаваемые LMS-ситуации.

    Это не заменяет нейросеть: сеть по-прежнему работает для остальных
    формулировок и выбирает факт. Правила служат защитным слоем для ситуаций,
    где неверный тип ответа опаснее осторожной детерминированности.
    """
    if _has_marker(user, INJECTION_MARKERS):
        return "prompt_injection_resistance"
    if _has_marker(user, ADMIN_MARKERS):
        return "admin_escalation"
    if _has_marker(user, WEB_MARKERS):
        return "web_fallback_general"
    if _is_conflicting_context(context) and (
        _is_fact_question(user, context)
        or _has_marker(user, ("разн", "различ", "противореч", "какому", "верить"))
    ):
        return "source_conflict"
    if "источник" in context.casefold() and _has_marker(user, SOURCE_QUESTION_MARKERS):
        return "source_attribution"
    if _has_marker(user, SUMMARY_MARKERS):
        return "context_summary"
    if _has_marker(user, NAVIGATION_QUESTION_MARKERS) and _has_marker(
        context, NAVIGATION_CONTEXT_MARKERS
    ):
        return "site_navigation"
    titles = _course_titles(context)
    if (
        len(titles) >= 2
        and not _question_names_course(user, titles)
        and (_is_fact_question(user, context) or _is_attempt_count_question(user, context))
    ):
        return "clarify_ambiguity"
    if _is_fact_question(user, context) or _is_attempt_count_question(user, context):
        return "direct_context_answer"
    if _is_missing_fact_question(user, context):
        return "missing_context_no_web"
    return None


def _structural_signals(user: str, context: str) -> set[str]:
    """Добавляет в нейросеть компактные признаки структуры, а не новые диалоги."""
    signals = {
        f"forced:{task_type}"
        for task_type in [_structured_task(user, context)]
        if task_type is not None
    }
    if _is_conflicting_context(context):
        signals.add("context:conflict")
    if len(_course_titles(context)) >= 2:
        signals.add("context:multi-course")
    if _is_fact_question(user, context) or _is_attempt_count_question(user, context):
        signals.add("question:matched-fact")
    if _is_missing_fact_question(user, context):
        signals.add("question:missing-fact")
    return signals


def _fact_score(question: str, fact: str, task_type: str) -> tuple[float, str]:
    question_words = set(_words(question))
    fact_words = set(_words(fact))
    shared = question_words & fact_words
    score = float(len(shared) * 4)
    # Слова из названия курса обычно повторяются в нескольких фразах. Поле,
    # о котором спрашивают («балл», «попытки», «доступ»), важнее названия.
    score += float(len(_field_roots(question) & _field_roots(fact)) * 16)
    score += float(len(_phrases(question) & _phrases(fact)) * 6)
    lowered = fact.casefold()
    if task_type == "source_attribution" and ("источник" in lowered or "документ" in lowered):
        score += 8.0
    if task_type == "source_conflict" and DATE_PATTERN.search(fact):
        score += 5.0
    if task_type == "site_navigation" and any(
        marker in lowered for marker in ("раздел", "меню", "вкладк", "страниц")
    ):
        score += 5.0
    if task_type == "direct_context_answer" and any(character.isdigit() for character in fact):
        score += 1.0
    question_lowered = question.casefold()
    if task_type == "direct_context_answer" and DATE_PATTERN.search(fact) and any(
        marker in question_lowered
        for marker in ("когда", "дат", "до какого", "до какой", "срок")
    ):
        score += 24.0
    if (
        task_type == "direct_context_answer"
        and _asks_passing_score(question_lowered)
        and any(character.isdigit() for character in fact)
        and any(marker in lowered for marker in ("набрать", "требуется", "нужно"))
    ):
        score += 24.0
    if (
        task_type == "direct_context_answer"
        and _is_attempt_count_question(question, fact)
        and any(marker in lowered for marker in ("попыт", "пересда"))
    ):
        score += 24.0
    return score, fact


def _selected_facts(question: str, context: str, task_type: str, count: int = 1) -> list[str]:
    candidates = [_fact_score(question, fact, task_type) for fact in _facts(context)]
    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = [fact for score, fact in candidates if score > 0][:count]
    if selected:
        return selected
    # Для сводки, уточнения и защиты от prompt injection допустимо показать
    # поддержанный контекстный факт. Для прямого ответа отсутствие связи
    # остаётся отказом, а не случайным выбором факта.
    if task_type in {
        "context_summary",
        "clarify_ambiguity",
        "source_conflict",
        "prompt_injection_resistance",
    }:
        field_facts = [fact for _, fact in candidates if _field_roots(fact)]
        if field_facts:
            return field_facts[:count]
        return [fact for _, fact in candidates[:count]]
    return []


def _latest_dated_fact(context: str) -> str | None:
    dated: list[tuple[tuple[int, int, int], str]] = []
    for fact in _facts(context):
        match = DATE_PATTERN.search(fact)
        if match:
            day, month, year = (int(value) for value in match.groups())
            dated.append(((year, month, day), fact))
    return max(dated, default=(None, ""), key=lambda item: item[0] or (0, 0, 0))[1] or None


def _response_facts(
    user: str,
    context: str,
    task_type: str,
    ranked_facts: list[str] | None,
    count: int = 1,
) -> list[str]:
    """Использует сеть ранжирования, а при её осторожном отказе — объяснимый поиск."""
    # Для прямого ответа сначала выбираем факт, который буквально совпадает
    # с полем вопроса. Так числовой срок не заменяется соседней фразой «после
    # этой даты», даже если ранжировщик дал ей близкий балл.
    if task_type == "direct_context_answer":
        heuristic = _selected_facts(user, context, task_type, count=count)
        if heuristic:
            return heuristic
    if ranked_facts:
        return ranked_facts[:count]
    return _selected_facts(user, context, task_type, count=count)


def _summary_fact_score(fact: str) -> int:
    """Ставит числовые условия курса выше служебного названия документа."""
    lowered = fact.casefold()
    score = 0
    if any(character.isdigit() for character in fact):
        score += 1
    if any(
        marker in lowered
        for marker in (
            "проходн",
            "балл",
            "набрать",
            "требуется",
            "нужно",
            "необходим",
            "зач",
            "засчит",
        )
    ):
        score += 40
    if any(marker in lowered for marker in ("попыт", "пересда")):
        score += 30
    if DATE_PATTERN.search(fact) or any(marker in lowered for marker in ("срок", "доступ", "до ")):
        score += 20
    if any(marker in lowered for marker in ("консультац", "поддержк")):
        score += 10
    return score


def _needs_compound_direct_answer(user: str, context: str) -> bool:
    """Возвращает два условия, если контекст явно задаёт их последовательность."""
    asks_when = any(marker in user.casefold() for marker in ("когда", "после чего"))
    conditional_facts = sum("после" in fact.casefold() for fact in _facts(context))
    return asks_when and conditional_facts >= 2


def render_lms_response(
    task_type: str,
    user: str,
    context: str,
    ranked_facts: list[str] | None = None,
) -> str:
    """Строит текст только из контекста или из безопасных фиксированных фраз."""
    if task_type == "admin_escalation":
        return (
            "Этот вопрос требует проверки конкретной ситуации администратором LMS. "
            "Обратитесь к администратору, чтобы он проверил данные аккаунта, оплаты "
            "или индивидуальное решение."
        )
    if task_type == "missing_context_no_web":
        return (
            "В предоставленном контексте такой информации нет. "
            "Я не буду придумывать ответ."
        )
    if task_type == "web_fallback_general":
        return (
            "В предоставленном контексте нет точного ответа. Внешний поиск можно "
            "использовать только при явном разрешении и отдельно от данных LMS."
        )
    if task_type == "prompt_injection_resistance":
        facts = _response_facts(user, context, task_type, ranked_facts)
        suffix = f" По контексту: {facts[0]}" if facts else ""
        return (
            "Я не буду игнорировать правила, раскрывать скрытые инструкции или "
            "выдумывать данные." + suffix
        )
    if task_type == "direct_context_answer":
        facts = _response_facts(user, context, task_type, ranked_facts)
        if _needs_compound_direct_answer(user, context):
            for fact in _facts(context):
                if "после" in fact.casefold() and fact not in facts:
                    facts.append(fact)
                if len(facts) >= 2:
                    break
        return (
            "В предоставленном контексте указано: " + " ".join(facts)
            if facts
            else LMS_CLARIFICATION
        )
    if task_type == "source_attribution":
        facts = _response_facts(user, context, task_type, ranked_facts, count=2)
        source_facts = [
            fact
            for fact in _facts(context)
            if "источник" in fact.casefold() or "документ" in fact.casefold()
        ]
        if source_facts:
            facts = [source_facts[0], *[fact for fact in facts if fact != source_facts[0]]][:2]
        if facts:
            return "Информация подтверждается контекстом: " + " ".join(facts)
        return LMS_CLARIFICATION
    if task_type == "site_navigation":
        facts = _response_facts(user, context, task_type, ranked_facts)
        return f"По контексту: {facts[0]}" if facts else LMS_CLARIFICATION
    if task_type == "context_summary":
        # Сводка должна содержать несколько проверяемых полей. Нейросеть
        # задаёт приоритет, затем дополняем её выбор другими полями контекста.
        facts = list(ranked_facts or [])
        for fact in _facts(context):
            if (_field_roots(fact) or _summary_fact_score(fact) > 1) and fact not in facts:
                facts.append(fact)
        facts = sorted(facts, key=lambda fact: (-_summary_fact_score(fact), fact))[:3]
        if not facts:
            facts = _selected_facts(user, context, task_type, count=3)
        return "По материалам LMS: " + " ".join(facts) if facts else LMS_CLARIFICATION
    if task_type == "source_conflict":
        fact = _latest_dated_fact(context) or next(iter(_facts(context)), "")
        if fact:
            return (
                "В контексте есть противоречивые сведения. Для проверки следует "
                "опираться на более поздний документ: " + fact
            )
        return LMS_CLARIFICATION
    if task_type == "clarify_ambiguity":
        facts = _response_facts(user, context, task_type, ranked_facts, count=2)
        prefix = "В контексте есть несколько возможных вариантов"
        if facts:
            prefix += ": " + " ".join(facts)
        return prefix + " Уточните курс, раздел или объект вопроса."
    return LMS_CLARIFICATION


def fingerprint(path: Path) -> str:
    """SHA-256 артефакта для контроля установки и воспроизводимости."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ReliableLms:
    """Загружает собственные веса и возвращает контекстно-обоснованный ответ."""

    def __init__(self, checkpoint: dict):
        if checkpoint.get("format_version") != 1:
            raise ValueError("Неизвестный формат LMS-checkpoint.")
        self.version = checkpoint["model_version"]
        self.task_types = checkpoint["task_types"]
        if set(self.task_types) != TASK_TYPES or len(self.task_types) != len(TASK_TYPES):
            raise ValueError("Словарь LMS-политик checkpoint не совпадает с ожидаемым.")
        self.thresholds = checkpoint["thresholds"]
        self.structural_features_version = int(checkpoint.get("structural_features_version", 0))
        self.structured_policy_version = int(checkpoint.get("structured_policy_version", 0))
        self.decoder_version = int(checkpoint.get("decoder_version", 1))
        self.encoder = LmsHashEncoder(
            checkpoint["feature_dimension"],
            include_structural=self.structural_features_version >= 1,
        )
        self.network = LmsIntentNetwork(**checkpoint["model_config"])
        self.network.load_state_dict(checkpoint["model_state"])
        self.network.eval()
        self.fact_encoder: LmsFactHashEncoder | None = None
        self.fact_network: LmsFactNetwork | None = None
        self.fact_threshold = float(checkpoint.get("fact_threshold", 1.1))
        selector_state = checkpoint.get("fact_model_state")
        if selector_state is not None:
            self.fact_encoder = LmsFactHashEncoder(checkpoint["fact_feature_dimension"])
            self.fact_network = LmsFactNetwork(**checkpoint["fact_model_config"])
            self.fact_network.load_state_dict(selector_state)
            self.fact_network.eval()

    @classmethod
    def load(cls, path: Path = DEFAULT_LMS_ARTIFACT) -> "ReliableLms":
        return cls(torch.load(path, map_location="cpu", weights_only=True))

    def probabilities(self, pairs: Sequence[tuple[str, str]]) -> torch.Tensor:
        inputs = self.encoder.transform(pairs)
        with torch.inference_mode():
            return self.network(inputs).softmax(dim=-1)

    def _ranked_facts(self, user: str, context: str, task_type: str) -> list[str] | None:
        """Возвращает факты, отобранные второй собственной нейросетью."""
        if self.fact_encoder is None or self.fact_network is None:
            return None
        facts = _facts(context)
        if not facts:
            return []
        inputs = self.fact_encoder.transform([(user, fact, task_type) for fact in facts])
        with torch.inference_mode():
            scores = self.fact_network(inputs).sigmoid().tolist()
        ranked = sorted(zip(scores, facts), key=lambda item: (-item[0], item[1]))
        selected = [fact for score, fact in ranked if score >= self.fact_threshold]
        limits = {
            "context_summary": 3,
            "source_attribution": 2,
            "clarify_ambiguity": 2,
        }
        return selected[:limits.get(task_type, 1)]

    def predict(self, user: str, context: str) -> LmsPrediction:
        clean_user = _clean_text(user)
        clean_context = _clean_text(context)
        if not clean_user or not clean_context:
            return LmsPrediction(LMS_NO_CONTEXT, None, 0.0, False, "lms-clarification", False)
        probabilities = self.probabilities([(clean_user, clean_context)])[0]
        values, indices = probabilities.topk(2)
        confidence = float(values[0])
        margin = float(values[0] - values[1])
        neural_task = self.task_types[int(indices[0])]
        neural_accepted = (
            confidence >= self.thresholds["confidence"]
            and margin >= self.thresholds["margin"]
        )
        structured_task = (
            _structured_task(clean_user, clean_context)
            if self.structured_policy_version >= 1
            else None
        )
        # Сеть лучше переносит регулярные шаблоны обучающего набора. Поэтому
        # структурный слой не отменяет её уверенное решение без причины. Он
        # берёт управление при неуверенности, а также в трёх явно безопасных
        # ситуациях: атака на инструкции, конфликт источников и два курса без
        # указанного пользователем выбора.
        structural_priority = {
            "prompt_injection_resistance",
            "source_conflict",
            "clarify_ambiguity",
        }
        use_structured = structured_task is not None and (
            structured_task in structural_priority
            or not neural_accepted
            or confidence < 0.97
        )
        if use_structured and structured_task is not None:
            text = render_lms_response(
                structured_task,
                clean_user,
                clean_context,
                self._ranked_facts(clean_user, clean_context, structured_task),
            )
            if text != LMS_CLARIFICATION:
                return LmsPrediction(
                    text,
                    structured_task,
                    confidence,
                    True,
                    "lms-structured-policy-context-extractive",
                    True,
                )
        if not neural_accepted:
            return LmsPrediction(LMS_CLARIFICATION, None, confidence, False, "lms-clarification", True)
        task_type = neural_task
        text = render_lms_response(
            task_type,
            clean_user,
            clean_context,
            self._ranked_facts(clean_user, clean_context, task_type),
        )
        if text == LMS_CLARIFICATION:
            return LmsPrediction(text, None, confidence, False, "lms-clarification", True)
        return LmsPrediction(
            text,
            task_type,
            confidence,
            True,
            "lms-neural-policy-context-extractive",
            True,
        )
