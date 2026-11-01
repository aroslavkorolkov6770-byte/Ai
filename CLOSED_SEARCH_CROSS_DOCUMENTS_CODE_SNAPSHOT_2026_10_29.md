# Полный код изменений: составные вопросы ВатэсAi

29.10.2026. Полные файлы без сокращений. Отчёт: CLOSED_SEARCH_CROSS_DOCUMENTS_REPORT_2026_10_29.md.

## ai_core/site_search.py

SHA-256: b218fa9dbebde1932b51b204e8fbc890894f7003e1c43f4c9480e4aede97019d

````python
"""Локальный поиск по актуальному закрытому содержимому, без чужой LLM.

Сайт передаёт снимки через доверенный серверный адаптер. Фразы из материалов
являются данными: этот модуль не исполняет код и не следует их инструкциям.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
import threading
from functools import lru_cache
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit
import snowballstemmer
from .site_search_language import SYNONYMS, requested_facet, search_language, supplies_facet
from .site_search_reasoning import authority, compound_parts, conflicting_passages, validate_authority, lookup_question, explicit_names
from .site_search_conditions import guarded, paragraphs, rules, atoms, atomic_observation, resolve, summary_request, operation_names, has_related_exception
from .site_search_structure import compile_sections, active, question_day, ISSUE_TEXT

NO_DATA = "В доступных материалах я не нашёл информации, которая однозначно отвечает на этот вопрос."
NOT_READY = "Материалы сейчас обновляются. Повторите вопрос после завершения обработки."
CONFLICT = "В доступных материалах есть противоречие. Уточните актуальное правило у администратора."
STOP = set("а и но в во на к ко с со из у за по о об от для при про это этот эта эти тот как какой какая какие какое что где когда сколько ли мне меня мой пожалуйста подскажи расскажи объясни скажи нужно надо должен должна следует можно ли бы чтобы чем зачем почему после перед если или есть быть его ее их мы вы я он она они найти найди покажи дай материал материалы информация информации вопрос ответ".split())
FOLLOWUP = set("срок сроки дней дня день время длится ждать ждать-то тогда этом этого этой там него сколько долго еще позже раньше именно такой таких случае делать происходит происходит-то".split())
STOP.update("имеет имеют является являются такой такая такие того уже просто теперь нужно необходимо сделать делать сделай найти найдите после-то".split())
STOP.update("кого кому каких каком какую какого каким чем чём собираюсь взять сначала случаях случае совсем не ли сообщить получается".split())
_stemmers = threading.local()


class PageText(HTMLParser):
    """Извлекает текст, исключая навигацию, скрытые блоки и значения форм."""
    OMIT = {"script", "style", "noscript", "nav", "footer", "form", "textarea", "select", "button", "svg", "iframe", "head"}
    VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "embed", "area", "base", "col", "param", "track"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        hidden = tag in self.OMIT or "hidden" in attributes or attributes.get("aria-hidden") == "true" or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", attributes.get("style") or "", re.I))
        if tag not in self.VOID:
            self.stack.append((tag, hidden))
        if not any(item[1] for item in self.stack) and tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr", "br"}:
            self.parts.append("\n")
        if tag in {"td", "th"} and not any(item[1] for item in self.stack):
            self.parts.append("; ")

    def handle_endtag(self, tag):
        if not any(item[1] for item in self.stack) and tag in {"p", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if not any(item[1] for item in self.stack):
            self.parts.append(data)


def plain_text(value: str) -> str:
    parser = PageText()
    parser.feed(value[:1_000_000])
    result = "".join(parser.parts)
    return "\n".join(re.sub(r"\s+", " ", line).strip(" #*;\t") for line in result.splitlines() if line.strip())


def normal(value: str) -> str:
    return " ".join(re.findall(r"[^\W\d_]+|\d+(?:[.,]\d+)*", value.casefold().replace("ё", "е"), re.UNICODE))


@lru_cache(maxsize=16384)
def stem(word: str) -> str:
    # Snowball — алгоритм обработки окончаний, без обученных языковых весов.
    # У каждого потока свой объект: внутренние курсоры stemmer не разделяются.
    if not re.fullmatch("[а-я]+", word):
        return word
    if not hasattr(_stemmers, "russian"):
        _stemmers.russian = snowballstemmer.stemmer("russian")
    return _stemmers.russian.stemWord(word)


# Это словарь понятий, а не заученные ответы. Он одинаково применяется
# к вопросу и материалам: «гость» и «покупатель» ищут один смысл.
CONCEPTS = {stem(variant): stem(canonical) for canonical, variants in {
    **SYNONYMS,
    "покупатель": ["гость", "гости", "покупатель"],
    "фраза": ["говорить", "сказать", "слова", "фраза"],
    "передача": ["передача", "передать", "передаётся", "передаваться", "направить", "направляться", "отправить", "отправляться"],
}.items() for variant in variants}


def terms(text: str) -> list[str]:
    return [CONCEPTS.get(stem(word), stem(word)) for word in normal(search_language(text)).split()
            if word not in STOP and (len(word) > 1 or word.isdigit() or word == 'г')]


def affinity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if min(len(left), len(right)) < 4 or left.isdigit() or right.isdigit():
        return 0.0
    if left[:4] == right[:4] and abs(len(left) - len(right)) <= 3:
        return 0.86
    if left[:1] != right[:1] or abs(len(left) - len(right)) > 2:
        return 0.0
    ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
    return 0.8 if ratio >= 0.84 else 0.0


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    groups: tuple[str, ...] = ()


def readable(source: dict, principal: Principal, now: float) -> bool:
    """Все ограничения сочетаются; неизвестная политика закрывает источник."""
    if not principal.user_id or principal.role not in {"admin", "staff"}:
        return False
    if source.get("status", "published") != "published":
        return False
    for field in ("expires_at", "valid_from"):
        if source.get(field) is not None and (not isinstance(source[field], (int, float)) or not math.isfinite(source[field])):
            return False
    if source.get("expires_at") is not None and source["expires_at"] <= now:
        return False
    if source.get("valid_from") is not None and source["valid_from"] > now:
        return False
    parents = source.get("parents", [])
    if not isinstance(parents, list) or len(parents) > 4 or any(not isinstance(parent, dict) or not readable(parent, principal, now) for parent in parents):
        return False
    policy = source.get("access")
    if not isinstance(policy, dict) or policy.get("scope") not in {"workspace", "restricted", "admin", "private"}:
        return False
    if set(policy) - {"scope", "users", "roles", "groups", "owner_id"}:
        return False
    for name in ("users", "roles", "groups"):
        if name in policy and (not isinstance(policy[name], list) or any(not isinstance(value, str) or not value for value in policy[name])):
            return False
    if policy["scope"] == "private" and not isinstance(policy.get("owner_id"), str):
        return False
    if policy["scope"] == "restricted" and not any(policy.get(name) for name in ("users", "groups", "roles")):
        return False
    restrictions = source.get("restrictions", [])
    if not isinstance(restrictions, list) or any(not isinstance(item, dict) or item.get("type") not in {"users", "roles"}
        or not isinstance(item.get("values"), list) or not all(isinstance(value, str) for value in item["values"]) for item in restrictions):
        return False
    if principal.role == "admin":
        return True
    scope = policy["scope"]
    if scope == "admin":
        return False
    if scope == "private" and policy.get("owner_id") != principal.user_id:
        return False
    if scope == "restricted" and not (principal.user_id in policy.get("users", []) or set(principal.groups) & set(policy.get("groups", [])) or principal.role in policy.get("roles", [])):
        return False
    for restriction in restrictions:
        if restriction.get("type") == "users" and principal.user_id not in restriction.get("values", []):
            return False
        if restriction.get("type") == "roles" and principal.role not in restriction.get("values", []):
            return False
        if restriction.get("type") not in {"users", "roles"}:
            return False
    return True


@dataclass
class Passage:
    source: dict
    heading: str
    text: str
    words: Counter
    title_words: Counter
    structure: dict | None = None


class ClosedSiteIndex:
    """Индекс одного сайта. Новый снимок сразу инвалидирует прежний."""
    def __init__(self, origins: list[str], clock=time.time, session_ttl: float = 1800):
        self.origins = set(origins)
        self.clock = clock
        self.session_ttl = session_ttl
        self.generation = 0
        self.state = "unavailable"
        self.sources: dict[str, dict] = {}
        self.passages: list[Passage] = []
        self.sessions: dict[tuple, dict] = {}
        self.aliases: dict[str, list[str]] = {}
        self.snapshot = ""
        self.postings: dict[str, set[int]] = {}
        self.passage_positions: dict[int, int] = {}
        self.condition_positions: set[int] = set()
        self.passage_operations: dict[int, frozenset[str]] = {}
        self.token_matches: dict[str, list[tuple[float, str]]] = {}

    def rebuild_lookup(self):
        """Обратный указатель: слово → номера фрагментов; без изменения score."""
        self.postings = {}
        self.passage_positions = {}
        self.condition_positions = set()
        self.passage_operations = {}
        self.token_matches = {}
        for position, passage in enumerate(self.passages):
            self.passage_positions[id(passage)] = position
            self.passage_operations[position] = operation_names(passage.text)
            if guarded(passage.text):
                self.condition_positions.add(position)
            for token in set(passage.words) | set(passage.title_words):
                self.postings.setdefault(token, set()).add(position)

    def _matching_passages(self, word: str, allowed_positions: set[int]):
        # Сходство пары слов вычисляется один раз для словаря, а не заново
        # для каждого фрагмента. Прежние правила опечаток и выбора совпадения сохранены.
        if word not in self.token_matches:
            matches = []
            for token in self.postings:
                similarity = affinity(word, token)
                if similarity:
                    matches.append((similarity, token))
            if len(self.token_matches) >= 256:
                self.token_matches.pop(next(iter(self.token_matches)))
            self.token_matches[word] = matches
        result = {}
        for similarity, token in self.token_matches[word]:
            for position in self.postings[token] & allowed_positions:
                candidate = (similarity, bool(self.passages[position].words[token]), token)
                if candidate > result.get(position, (0.0, False, "")):
                    result[position] = candidate
        return result

    def begin(self, snapshot: str) -> int:
        self.generation += 1
        self.state = "processing"
        self.snapshot = snapshot
        self.sessions.clear()
        return self.generation

    def publish(self, sources: list[dict], generation: int, aliases: dict | None = None):
        if generation != self.generation:
            return False
        seen = set()
        passages = []
        accepted = {}
        try:
            for raw in sources:
                source = dict(raw)
                identifier = source.get("id")
                if not isinstance(identifier, str) or not identifier or identifier in seen:
                    raise ValueError("duplicate_or_invalid_source")
                if not isinstance(source.get("revision"), str) or not source["revision"] or not isinstance(source.get("title"), str):
                    raise ValueError("source_metadata_invalid")
                seen.add(identifier)
                url = urlsplit(source.get("url", ""))
                origin = f"{url.scheme}://{url.netloc}"
                if origin not in self.origins or url.username or url.password or url.scheme not in {"http", "https"}:
                    raise ValueError("source_origin_invalid")
                if any(key.casefold() in {"token", "access_token", "password", "secret", "api_key", "session"} for key, _ in parse_qsl(url.query)):
                    raise ValueError("source_url_contains_credentials")
                if not isinstance(source.get("access"), dict):
                    raise ValueError("source_access_missing")
                validate_authority(source)
                # Даже полностью нечитаемый скан без поисковых фрагментов
                # сохраняет состояние ошибки своего документа.
                source['structure_unavailable'] = any('[STRUCTURE_UNCERTAIN]' in str(section.get('text',''))
                                                      for section in source.get('sections',[]))
                accepted[identifier] = source
                for section in compile_sections(source, plain_text):
                    heading = plain_text(str(section.get("heading", "")))
                    text = plain_text(str(section.get("text", "")))
                    # Мелкие разделы не склеиваем с соседним правилом/товаром.
                    for paragraph in paragraphs(text):
                        # Граница между фрагментами проходит между предложениями.
                        # Условие и действие внутри предложения не разрываются.
                        chunks, chunk = [], ""
                        for sentence in self._sentences(paragraph):
                            if chunk and len(chunk) + len(sentence) > 2000 and not guarded(paragraph) and not section.get('structure', {}).get('linked'):
                                chunks.append(chunk)
                                chunk = ""
                            chunk = (chunk + " " + sentence).strip()
                        if chunk:
                            chunks.append(chunk)
                        for part in chunks:
                            if not part:
                                continue
                            passages.append(Passage(source, heading, part, Counter(terms(part)), Counter(terms(source.get("title", "") + " " + heading + " " + " ".join(source.get("keywords", [])))), section.get('structure')))
            self.sources, self.passages = accepted, passages
            self.rebuild_lookup()
            self.aliases = aliases or {}
            self.state = "ready"
            return True
        except Exception:
            self.state = "failed"
            self.sources, self.passages = {}, []
            raise

    def _query_terms(self, text: str):
        words = terms(text)
        expanded = list(words)
        query = normal(text)
        for canonical, equivalents in self.aliases.items():
            if any(normal(item) in query for item in [canonical, *equivalents]):
                expanded.extend(terms(canonical))
        return list(dict.fromkeys(expanded))

    @staticmethod
    def _sentences(text):
        # Сохраняем отрицание, числа, условия и целые предложения.
        return [item.strip() for item in re.split(r"(?<=[.!?])\s+(?=[А-ЯA-Z])", text) if item.strip()]

    def _remember(self, sessions, scope, identifiers, words, *, facts=None, facet=None, pending=False):
        sessions[scope] = {'time': self.clock(), 'generation': self.generation, 'ids': identifiers,
                           'terms': list(dict.fromkeys(words))[:24], 'condition_facts': facts or {},
                           'facet': facet, 'pending_conditions': pending}
        if len(sessions) > 1000:
            del sessions[min(sessions, key=lambda key: sessions[key]['time'])]

    def _summarize(self, identifier, scope, sessions, started, day):
        source = self.sources[identifier]
        if not source.get('complete', True):
            return {'status': 'unavailable', 'text': 'Документ прочитан не полностью. Пока не могу надёжно пересказать его содержание.', 'links': [], 'evidence': []}
        passages = [item for item in self.passages if item.source['id'] == identifier and active(item.structure or {}, day)]
        if any((item.structure or {}).get('issue') for item in passages):
            return {'status': 'unavailable', 'text': ISSUE_TEXT, 'links': [], 'evidence': []}
        if not passages:
            return {'status': 'not_found', 'text': NO_DATA, 'links': [], 'evidence': []}
        if conflicting_passages(passages, self._sentences, terms):
            return {'status': 'conflict', 'text': CONFLICT, 'links': [], 'evidence': []}
        # Условия и исключения не сокращаем до голого разрешения или числа.
        units = []
        for position, item in enumerate(passages):
            sentences = self._sentences(item.text)
            important = guarded(item.text) or bool(re.search(r'нельзя|запрещ|не допуска|обязател|^(?:При|Если)\b', item.text, re.I))
            quote = item.text if important else ' '.join(sentence for sentence in sentences[:2]
                       if not re.search(r'^контрольная отметка|вымышленн.*(?:испытан|провер)', sentence, re.I))
            if quote and not (quote.isupper() and len(quote) < 120 and not important) and quote not in {unit[2] for unit in units}:
                units.append((position, item, quote, important))
        critical = [unit for unit in units if unit[3]]
        if sum(len(unit[2]) for unit in critical) > 1600:
            return {'status': 'clarify', 'text': 'В документе много условий и исключений. Уточните раздел для краткого содержания.', 'links': [], 'evidence': []}
        selected = list(critical)
        headings = {unit[1].heading for unit in selected}
        for unit in units:
            if unit in selected:
                continue
            if len(selected) >= 6 or sum(len(item[2]) for item in selected) + len(unit[2]) > 1600:
                continue
            selected.append(unit)
            headings.add(unit[1].heading)
        selected.sort(key=lambda unit: unit[0])
        if not selected:
            return {'status': 'clarify', 'text': 'Уточните, какую часть документа нужно кратко пересказать.', 'links': [], 'evidence': []}
        self._remember(sessions, scope, [identifier], terms(source['title']))
        return {'status': 'answer', 'text': '\n'.join(unit[2] for unit in selected),
                'links': [{'title': source['title'], 'url': source['url']}],
                'evidence': [{'id': identifier, 'revision': source['revision'], 'heading': item.heading, 'quote': quote}
                             for _, item, quote, _ in selected], 'context_used': False,
                'timing_ms': round((time.perf_counter() - started) * 1000, 2)}

    def _conditional_answer(self, question, candidates, words, named, facet, scope, sessions, session, scoped, started):
        matches = []
        focus_words = terms(re.split(r'(?<=[.!])\s+', question.strip())[-1])
        action_question = bool(re.search(r'что (?:сделать|делать)|порядок действий|какие действия',question,re.I))
        compare_action_relevance = action_question and not operation_names(question)
        source_scores = {}
        def body_score(text):
            tokens = set(terms(text))
            return sum(any(affinity(word, token) for token in tokens) for word in focus_words)
        # Для названного объекта используем готовый обратный индекс. Проверка
        # тех же слов во всех условиях корпуса повторяла тысячи сравнений.
        positions = {self.passage_positions[id(item)] for item in candidates} & self.condition_positions
        for name in named:
            positions.intersection_update(self._matching_passages(name, positions))
        if not positions:
            return None
        # Уточнение из другого документа не должно перехватывать вопрос,
        # для которого уже есть более точное правило о названном предмете.
        # Сравниваем тела правил, сохраняя тот же уровень официальности.
        relevant = {self.passage_positions[id(item)] for item in candidates} if named and compare_action_relevance else set()
        for name in named:
            relevant.intersection_update(self._matching_passages(name,relevant))
        best_scores = {}
        for position in relevant:
            passage = self.passages[position]
            level = authority(passage.source)
            value = max((body_score(rule.body) for rule in rules(passage.text,self._sentences,facet,supplies_facet)),default=0)
            best_scores[level] = max(best_scores.get(level,0),value)
        for position in sorted(positions):
            item = self.passages[position]
            title_hits = sum(any(affinity(word, token) for token in item.title_words) for word in words)
            if not scoped and not named and title_hits < 2:
                continue
            if not scoped and named and not any(name in item.title_words for name in named):
                continue
            identifier = item.source['id']
            if identifier not in source_scores:
                source_scores[identifier] = max((body_score(rule.body) for passage in candidates if passage.source['id'] == identifier
                                                 for rule in rules(passage.text, self._sentences, facet, supplies_facet)), default=0)
            if named and compare_action_relevance and source_scores[identifier] < best_scores.get(authority(item.source),0):
                continue
            own_rules = rules(item.text, self._sentences, facet, supplies_facet)
            # Основная строка может повторять название товара, а её сноска — нет.
            # Оцениваем связанную пару вместе, сохраняя проверку операции.
            related_exception = has_related_exception(own_rules, lambda rule: body_score(rule.body))
            eligible = own_rules if related_exception else [rule for rule in own_rules if rule.condition]
            if max((body_score(rule.body) for rule in eligible), default=-1) < source_scores[identifier]:
                continue
            previous = (session or {}).get('condition_facts', {}).get(item.source['id'], {})
            decision = resolve(item.text, question, terms, self._sentences, facet, supplies_facet, previous, affinity)
            if decision is not None:
                matches.append((title_hits, item, decision))
        if not matches:
            return None
        if not named and not scoped:
            best_hits = max(row[0] for row in matches)
            matches = [row for row in matches if row[0] == best_hits]
        # Условная строка не отменяет независимо опубликованное общее правило.
        # Сверяем оба файла после проверки доступа, редакции и операции.
        conditional_ids = {item.source['id'] for _,item,_ in matches}
        operations = set().union(*(operation_names(rule.body) for _,item,_ in matches
                                   for rule in rules(item.text,self._sentences,facet,supplies_facet)))
        ordinary_positions = {self.passage_positions[id(item)] for item in candidates} - self.condition_positions
        for name in named:
            ordinary_positions.intersection_update(self._matching_passages(name,ordinary_positions))
        for position in sorted(ordinary_positions):
            item = self.passages[position]
            if item.source['id'] in conditional_ids:
                continue
            vocabulary = set(item.words) | set(item.title_words)
            if named and not all(any(affinity(name,token) for token in vocabulary) for name in named):
                continue
            coverage = sum(any(affinity(word,token) for token in vocabulary) for word in words)
            if not named and not scoped and (not words or coverage / len(words) < .65):
                continue
            own_rules = rules(item.text,self._sentences,facet,supplies_facet)
            # Условные фрагменты уже прошли resolve; второй раз нельзя взять
            # из них безусловную строку и потерять связанное исключение.
            if any(rule.condition for rule in own_rules):
                continue
            selected = [rule for rule in own_rules if operation_names(rule.body) & operations
                        and (scoped or body_score(rule.body) > 0)]
            if selected:
                matches.append((coverage,item,{'status':'answer','selected':selected,'facts':{}}))
        facts = {item.source['id']: decision['facts'] for _, item, decision in matches}
        identifiers = list(dict.fromkeys(item.source['id'] for _, item, _ in matches))
        # Факты хранятся как истинность условий документа, а не текст пользователя.
        topic_words = list(dict.fromkeys(word for _, item, _ in matches for word in terms(item.source['title'])))
        applicable = [row for row in matches if not row[2].get('inapplicable')]
        if applicable:
            matches = applicable
        unresolved = [row for row in matches if row[2]['status'] == 'clarify']
        if unresolved:
            self._remember(sessions, scope, identifiers, topic_words, facts=facts, facet=facet, pending=True)
            return {'status': 'clarify', 'text': unresolved[0][2]['text'], 'links': [], 'evidence': [], 'context_used': bool(session)}
        # Сравниваем факты только реально применимых веток. Их условия уже проверены.
        effective = [Passage(item.source, item.heading, rule.body, Counter(), Counter())
                     for _, item, decision in matches for rule in decision['selected']]
        if conflicting_passages(effective, self._sentences, terms, inherit_heading=True):
            sessions.pop(scope,None)
            return {'status': 'conflict', 'text': CONFLICT, 'links': [], 'evidence': []}
        highest = max(authority(item.source) for _, item, _ in matches)
        matches = [row for row in matches if authority(row[1].source) == highest]
        _, best, decision = matches[0]
        quotes = list(dict.fromkeys(rule.quote for rule in decision['selected']))
        if sum(map(len, quotes)) > 1600:
            return {'status': 'clarify', 'text': 'Уточните конкретную операцию: для неё указано несколько условий.', 'links': [], 'evidence': []}
        # Память содержит зависимости проверки, даже если короткий ответ
        # цитирует один файл. Новое условие должно повторно проверить остальные.
        self._remember(sessions, scope, identifiers, topic_words, facts=facts, facet=facet)
        return {'status': 'answer', 'text': ' '.join(quotes),
                'links': [{'title': best.source['title'], 'url': best.source['url']}],
                'evidence': [{'id': best.source['id'], 'revision': best.source['revision'], 'heading': best.heading, 'quote': quote} for quote in quotes],
                'context_used': bool(session), 'timing_ms': round((time.perf_counter() - started) * 1000, 2)}

    def search(self, question: str, principal: Principal, chat_id: str = "default", selected_source: str | None = None, current_revisions: dict | None = None) -> dict:
        """Каждая часть ответа проходит те же проверки доступа и источников."""
        parts = compound_parts(question) if len(question) <= 12000 and not summary_request(question) else [question]
        if len(parts) == 1:
            return self._search_single(question, principal, chat_id, selected_source, current_revisions)
        if len(parts) > 4:
            return {"status": "clarify", "text": "Разделите вопрос на несколько сообщений, чтобы я проверил каждую часть.", "links": [], "evidence": []}
        started = time.perf_counter()
        scope = (principal.user_id, principal.role, tuple(sorted(principal.groups)), chat_id)
        # Промежуточные результаты не меняют общий чат. Неудачный составной
        # запрос не оставляет ложную тему для следующего сообщения.
        results, contexts = [], []
        for part in parts:
            # Части одного сообщения независимы: результат первой части
            # не является предыдущей репликой пользователя для второй.
            local_sessions = {scope: dict(self.sessions[scope])} if scope in self.sessions else {}
            result = self._search_single(part, principal, chat_id, selected_source, current_revisions, local_sessions)
            if result["status"] in {"conflict", "processing", "unavailable"}:
                self.sessions.pop(scope,None)
                return result
            results.append(result)
            if result['status'] in {'answer','clarify'} and scope in local_sessions:
                contexts.append(dict(local_sessions[scope]))
        answered = [(part, result) for part, result in zip(parts, results) if result["status"] == "answer"]
        if not answered and not any(result['status']=='clarify' for result in results):
            return {"status": "not_found", "text": NO_DATA, "links": [], "evidence": []}
        texts, links, evidence = [], [], []
        for part, result in answered:
            if result["text"] not in texts:
                texts.append(result["text"])
            for link in result["links"]:
                if link not in links:
                    links.append(link)
            for item in result["evidence"]:
                evidence.append({**item, "question": part})
        missing = [part for part, result in zip(parts, results) if result['status'] not in {'answer','clarify'}]
        clarifications = list(dict.fromkeys(result['text'] for result in results if result['status']=='clarify'))
        texts.extend(clarifications)
        if missing:
            texts.append("Не нашёл однозначных данных по вопросу: " + " ".join('«' + part + '»' for part in missing))
        facts = {}
        for context in contexts:
            for identifier, values in context.get('condition_facts',{}).items():
                target=facts.setdefault(identifier,{})
                for atom,value in values.items():
                    target[atom]=value if atom not in target or target[atom]==value else None
        pending=[context for context in contexts if context.get('pending_conditions')]
        facets={context.get('facet') for context in (pending or contexts) if context.get('facet')}
        identifiers=list(dict.fromkeys(identifier for context in contexts for identifier in context.get('ids',[])))
        # Сохраняем также источник ещё не отвеченного условия. В ответе ссылок
        # на него нет, но после уточнения поиск должен вернуться к этому правилу.
        self._remember(sessions=self.sessions,scope=scope,identifiers=identifiers,
                       words=self._query_terms(' '.join(parts)),facts=facts,
                       facet=next(iter(facets)) if len(facets)==1 else None,pending=bool(pending))
        if len(self.sessions) > 1000:
            del self.sessions[min(self.sessions, key=lambda key: self.sessions[key]["time"])]
        return {"status": ('partial' if missing or clarifications else 'answer') if answered else 'clarify', "text": "\n".join(texts), "links": links,
                "evidence": evidence, "context_used": any(result.get("context_used") for result in results),
                "timing_ms": round((time.perf_counter() - started) * 1000, 2)}

    def _search_single(self, question: str, principal: Principal, chat_id: str = "default", selected_source: str | None = None, current_revisions: dict | None = None, session_store: dict | None = None) -> dict:
        sessions = self.sessions if session_store is None else session_store
        scope = (principal.user_id, principal.role, tuple(sorted(principal.groups)), chat_id)
        previous_day = sessions.get(scope, {}).get('as_of')
        result = self._search_impl(question, principal, chat_id, selected_source, current_revisions, session_store)
        clean, day, invalid = question_day(question, self.clock())
        if not invalid and scope in sessions and result['status'] in {'answer', 'clarify'}:
            if clean != question.strip():
                sessions[scope]['as_of'] = day
            elif result.get('context_used') and previous_day and not re.search(r'\bсегодня\b', question, re.I):
                sessions[scope]['as_of'] = previous_day
        return result

    def _search_impl(self, question: str, principal: Principal, chat_id: str = "default", selected_source: str | None = None, current_revisions: dict | None = None, session_store: dict | None = None) -> dict:
        started = time.perf_counter()
        sessions = self.sessions if session_store is None else session_store
        scope = (principal.user_id, principal.role, tuple(sorted(principal.groups)), chat_id)
        if self.state != "ready":
            return {"status": "processing" if self.state == "processing" else "unavailable", "text": NOT_READY if self.state == "processing" else "Не удалось проверить доступные материалы. Повторите вопрос позже.", "links": [], "evidence": []}
        if not question.strip() or len(question) > 12000:
            return {"status": "clarify", "text": "Уточните, что нужно найти в материалах сайта.", "links": [], "evidence": []}
        original_question = question
        question, day, invalid_day = question_day(question, self.clock())
        explicit_day = question != original_question.strip()
        if invalid_day:
            return {'status': 'clarify', 'text': 'Укажите одну корректную дату в формате ДД.ММ.ГГГГ.', 'links': [], 'evidence': []}
        allowed = {key for key, source in self.sources.items() if readable(source, principal, self.clock())}
        if current_revisions is not None:
            if selected_source and selected_source not in current_revisions:
                sessions.pop(scope, None)
                return {"status": "not_found", "text": NO_DATA, "links": [], "evidence": []}
            if any(key not in self.sources or self.sources[key]["revision"] != revision for key, revision in current_revisions.items()):
                sessions.pop(scope, None)
                return {"status": "processing", "text": NOT_READY, "links": [], "evidence": []}
            allowed.intersection_update(current_revisions)
        if selected_source and selected_source not in allowed:
            sessions.pop(scope, None)
            return {"status": "not_found", "text": NO_DATA, "links": [], "evidence": []}
        if selected_source and self.sources[selected_source].get('structure_unavailable'):
            sessions.pop(scope,None)
            return {'status':'unavailable','text':ISSUE_TEXT,'links':[],'evidence':[]}
        session = sessions.get(scope)
        if session and (session.get("generation") != self.generation or self.clock() - session["time"] > self.session_ttl or not set(session["ids"]).issubset(allowed)):
            session = None
            sessions.pop(scope, None)
        # Явный выбор файла сильнее прежней темы разговора.
        if selected_source and session and session['ids'] != [selected_source]:
            session = None
            sessions.pop(scope, None)
        if summary_request(question):
            target = selected_source
            if not target:
                # Название в текущем сообщении сильнее предыдущего документа.
                # Ищем только среди доступных названий, не раскрывая скрытые файлы.
                quoted = re.findall(r'«([^»]+)»|"([^"]+)"', question)
                instruction_words = set(terms('перескажи пересказать кратко краткий содержание резюме файл документ выбранный этот основные положения выдели главное расскажи пожалуйста'))
                title_words = set(terms(' '.join(left or right for left, right in quoted) if quoted else question)) - instruction_words
                if title_words:
                    targets = [key for key in allowed if self.sources[key].get('kind') == 'document'
                               and title_words.issubset(set(terms(self.sources[key]['title'])))]
                    target = targets[0] if len(targets) == 1 else None
                    if target is None:
                        sessions.pop(scope, None)
                elif session and len(session['ids']) == 1:
                    target = session['ids'][0]
            if not target:
                return {'status': 'clarify', 'text': 'Выберите документ, который нужно кратко пересказать.', 'links': [], 'evidence': []}
            return self._summarize(target, scope, sessions, started, day)
        focused_question = lookup_question(question)
        current_words = self._query_terms(focused_question)[:32]
        query_normal = normal(focused_question)
        facet = requested_facet(focused_question)
        hypothetical_amount = bool(re.search(r"можно|допустим|разреш", query_normal) and re.search(r"грамм|\bг\b|килограмм|\bкг\b|литр", query_normal))
        if hypothetical_amount:
            current_words = [word for word in current_words if not word.replace('.', '').replace(',', '').isdigit()]
        want_speech = bool(re.search(r"говорить|сказать|словами|фразы|фразу", query_normal))
        want_steps = bool(re.search(r"что (сделать|делать)|порядок|этапы|шаги|действия", query_normal))
        want_overview = bool(re.search(r"как (устроен|работает)|что такое", query_normal))
        raw_words = [word for word in normal(focused_question).split() if word not in STOP]
        reference = bool(re.search(r"\b(это|этого|этой|там|тогда|него|нее|ней|этом)\b", normal(question))) or (normal(question).startswith("а ") and len(raw_words) <= 3)
        generic = not raw_words or all(word in FOLLOWUP for word in raw_words)
        explicit_subject = bool(explicit_names(focused_question))
        context_used = bool(session and (reference or generic) and not explicit_subject)
        condition_reply = False
        if session and not explicit_subject and (session.get('pending_conditions') or session.get('condition_facts')):
            # Короткий ответ на наше уточнение содержит только недостающий факт.
            condition_reply = any(atomic_observation(atom, question, terms)[0]
                               for item in self.passages if item.source['id'] in session['ids']
                               for rule in rules(item.text, self._sentences, None, supplies_facet) if rule.condition
                               for atom in atoms(rule.condition))
            context_used = context_used or condition_reply
        if context_used and not condition_reply and not generic and not re.search(r"\b(это|этого|этой|там|тогда|него|нее|ней|этом)\b", normal(question)):
            vocabulary = set().union(*(set(passage.words) | set(passage.title_words) for passage in self.passages if passage.source["id"] in session["ids"]))
            # Новая самостоятельная тема после «а» не должна наследовать старый ответ.
            context_used = all(word in {stem(item) for item in FOLLOWUP} or any(affinity(word, token) for token in vocabulary) for word in current_words)
        if context_used and session.get('as_of') and not explicit_day and not re.search(r'\bсегодня\b', question, re.I):
            day = session['as_of']
        if (reference or generic) and not session and not selected_source:
            return {"status": "clarify", "text": "Уточните, о каком материале или вопросе идёт речь.", "links": [], "evidence": []}
        if context_used and generic and len(session["ids"]) > 1 and not selected_source:
            return {"status": "clarify", "text": "Уточните, о каком из упомянутых материалов идёт речь.", "links": [], "evidence": []}
        words = list(dict.fromkeys(current_words + (session["terms"] if context_used else [])))
        named = [term for name in explicit_names(focused_question) if name.casefold() not in STOP for term in terms(name)][:16]
        if re.match(r'^а\s+(?:если|при)\b',normal(question)) and not named and not context_used and not selected_source:
            return {'status':'clarify','text':'Уточните, к какому материалу или правилу относится это условие.','links':[],'evidence':[]}
        numbers = re.findall(r"\d+(?:[.,]\d+)*", focused_question)[:16]
        candidates = [item for item in self.passages if item.source["id"] in allowed and (not selected_source or item.source["id"] == selected_source)
                      and active(item.structure or {}, day)]
        if context_used:
            candidates = [item for item in candidates if item.source['id'] in session['ids']]
        best_coverage = 0
        if not named and not selected_source and any((item.structure or {}).get('issue') for item in candidates):
            positions={self.passage_positions[id(item)] for item in candidates}
            coverage=Counter(position for word in current_words for position in self._matching_passages(word,positions))
            best_coverage=max(coverage.values(),default=0)
        # Не выдаём разрешение из повреждённого раздела, даже если найдено
        # достаточно слов. Скрытые материалы здесь уже исключены проверкой ACL.
        for item in candidates:
            if not (item.structure or {}).get('issue'):
                continue
            vocabulary = set(item.words) | set(item.title_words)
            named_match = named and all(any(affinity(name, token) for token in vocabulary) for name in named)
            covered = sum(any(affinity(word, token) for token in vocabulary) for word in current_words)
            # Общие слова «товар» и «хранится» в повреждённом чужом файле
            # не блокируют более точное совпадение с названным товаром.
            if selected_source or named_match or (not named and current_words and covered >= best_coverage and covered / len(current_words) >= .65):
                return {'status': 'unavailable', 'text': ISSUE_TEXT, 'links': [], 'evidence': []}
        if context_used and session.get('facet') and facet is None:
            facet = session.get('facet')
        candidates = [item for item in candidates if supplies_facet(item.text, facet)]
        requested_operations = operation_names(re.split(r'(?<=[.!])\s+', question.strip())[-1])
        if requested_operations:
            # Совпадения названия и единицы времени недостаточно: срок возврата
            # не отвечает на вопрос о сроке доставки того же заказа.
            candidates = [item for item in candidates if not self.passage_operations[self.passage_positions[id(item)]]
                          or self.passage_operations[self.passage_positions[id(item)]] & requested_operations]
        if context_used:
            candidates = [item for item in candidates if item.source["id"] in session["ids"]]
            if re.search(r"срок|сколько.*дн|как долго|сколько.*хран", query_normal):
                candidates = [item for item in candidates if re.search(r"\b(?:день|дня|дней|дни|час\w*|минут\w*|недел\w*|месяц\w*|суток|срок\w*)\b", normal(item.text))]
        # Обзор объясняет весь порядок вместе с условиями. Он не требует
        # выяснять частные обстоятельства пользователя, как конкретный случай.
        conditional = None if want_overview else self._conditional_answer(question, candidates, current_words, named, facet, scope, sessions,
                                                session if context_used else None, bool(selected_source or context_used), started)
        if conditional is not None:
            return conditional
        if not words or not candidates:
            incomplete = any(not self.sources[key].get("complete", True) for key in allowed)
            return {"status": "unavailable" if incomplete else "not_found", "text": "Материал пока не удалось обработать полностью. Обратитесь к администратору." if incomplete else NO_DATA, "links": [], "evidence": []}
        average = sum(sum(item.words.values()) for item in candidates) / len(candidates) or 1
        # IDF считается только по доступным фрагментам, без влияния чужих документов.
        positions = {self.passage_positions[id(item)] for item in candidates}
        matches = {word: self._matching_passages(word, positions) for word in set(words + named)}
        df = {word: len(matches[word]) for word in words}
        covered_positions = Counter(position for word in words for position in matches[word])
        minimum = 0.65 if len(words) <= 3 else 0.45
        eligible = {position for position, covered in covered_positions.items() if covered / len(words) >= minimum}
        for name in named:
            eligible.intersection_update(matches[name])
        if not hypothetical_amount:
            for number in numbers:
                eligible.intersection_update(self.postings.get(number, set()))
        ranked = []
        for position in eligible:
            item = self.passages[position]
            score, covered, body_covered = 0.0, 0, 0
            for word in words:
                match = matches[word].get(position, (0.0, False, ""))
                similarity, _, token = match
                if not similarity:
                    continue
                covered += 1
                frequency = item.words[token]
                if frequency:
                    body_covered += 1
                title_frequency = item.title_words[token]
                idf = math.log(1 + (len(candidates) - df[word] + 0.5) / (df[word] + 0.5))
                length = sum(item.words.values())
                bm25 = frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / average)) if frequency else 0
                score += idf * similarity * (bm25 + min(1.1, title_frequency * 0.55))
            coverage = covered / len(words)
            if (not body_covered and not (coverage >= 0.65 and covered >= 2 and len(item.text) >= 40)) or coverage < minimum:
                continue
            if context_used and item.source["id"] in session["ids"]:
                score *= 1.2
            # Короткий заголовок-конспект не подменяет содержательную инструкцию.
            score *= 0.72 + min(0.28, len(item.text) / 400)
            if want_steps and re.search(r"действ|порядок|этап|шаг", normal(item.heading)):
                score *= 1.8
            if want_overview and re.search(r"как работает|принцип|основ|устройств", normal(item.heading)):
                score *= 1.7
            if want_speech and "«" in item.text:
                score *= 1.7
            if facet == 'recipient' and re.search(r"кто|получатель|ответственн|контакт", normal(item.heading)):
                score *= 2
            ranked.append((score * (0.6 + coverage), coverage, item))
        ranked.sort(key=lambda row: (-row[0], row[2].source["id"], row[2].heading))
        if not ranked:
            incomplete = any(not self.sources[key].get("complete", True) for key in allowed)
            return {"status": "unavailable" if incomplete else "not_found", "text": "Часть материалов обработана не полностью. Пока не могу подтвердить ответ на этот вопрос." if incomplete else NO_DATA, "links": [], "evidence": []}
        # Совпадение общих слов («самостоятельный ремонт») недостаточно, если
        # другой фрагмент покрывает также конкретный предмет («кофемашина»).
        # Авторитетность применяется только внутри наиболее подходящего круга.
        best_coverage = max(row[1] for row in ranked)
        ranked = [row for row in ranked if row[1] >= best_coverage - 0.12]
        # Сначала проверяем противоречия, включая менее авторитетный источник.
        # Официальная отметка не разрешает молча выбрать одну из разных норм.
        relevant_sentences = lambda text: [sentence for sentence in self._sentences(text) if supplies_facet(sentence, facet)]
        if conflicting_passages([row[2] for row in ranked], relevant_sentences, terms):
            return {"status": "conflict", "text": CONFLICT, "links": [], "evidence": []}
        highest_authority = max(authority(row[2].source) for row in ranked)
        ranked = [row for row in ranked if authority(row[2].source) == highest_authority]
        best_score, _, best = ranked[0]
        if want_steps:
            action_sections = [row for row in ranked if row[2].source["id"] == best.source["id"]
                               and re.search(r"действ|порядок|этап|шаг", normal(row[2].heading))]
            if action_sections:
                best = action_sections[0][2]
        if len(ranked) > 1:
            second = next((row for row in ranked[1:] if row[2].source["id"] != best.source["id"]), None)
            if second and second[0] >= best_score * 0.96 and len(current_words) <= 2 and not context_used and not selected_source:
                titles = list(dict.fromkeys([best.source["title"], second[2].source["title"]]))
                if len(titles) > 1:
                    return {"status": "clarify", "text": "Уточните, какой материал нужен: " + " или ".join(f'«{title}»' for title in titles) + "?", "links": [], "evidence": []}
        sentences = self._sentences(best.text)
        if facet:
            sentences = [sentence for sentence in sentences if supplies_facet(sentence, facet)]
            if not sentences:
                return {"status": "not_found", "text": NO_DATA, "links": [], "evidence": []}
        want_number = bool(re.search(r"сколько|когда|срок|температур|градус|время|процент", normal(question)))
        def sentence_score(sentence):
            token_set = set(terms(sentence))
            match = sum(max((affinity(word, token) for token in token_set), default=0) for word in current_words)
            return match + (0.8 if want_number and re.search(r"\d", sentence) else 0) + (3 if want_speech and "«" in sentence else 0)
        ordered = sorted(enumerate(sentences), key=lambda pair: (-sentence_score(pair[1]), pair[0]))
        selected = ordered[:1]
        if hypothetical_amount or query_normal.startswith('почему'):
            selected = list(enumerate(sentences))
        elif want_steps:
            # Для порядка действий сохраняется последовательность всего раздела.
            selected = list(enumerate(sentences))
        elif want_overview:
            selected = [(position, sentence) for position, sentence in enumerate(sentences)
                        if position < 3 or guarded(sentence)]
            if sum(len(sentence) for _, sentence in selected) > 1600:
                return {'status': 'clarify', 'text': 'Уточните раздел: короткий ответ не вместит все важные условия.', 'links': [], 'evidence': []}
        elif ordered:
            # Добавляем следующее предложение только если оно раскрывает ещё
            # не отвеченную часть вопроса или зависит от выбранного правила.
            covered = {word for word in current_words if any(affinity(word, token) for token in terms(ordered[0][1]))}
            for pair in ordered[1:]:
                extra = {word for word in current_words if any(affinity(word, token) for token in terms(pair[1]))} - covered
                dependent = pair[0] == ordered[0][0] + 1 and bool(re.match(r"(?:Если|При этом|В этом случае|Именно поэтому|Для этого|Исключение|Кроме|Однако|За исключением)\b", pair[1]))
                if extra or dependent:
                    selected.append(pair)
                    break
        selected.sort()
        answer = " ".join(sentence for _, sentence in selected)
        quote = answer
        if re.match(r"како[йеяие]+\s", query_normal) and len(best.source["title"].split()) <= 4 and not set(terms(best.source["title"])) & set(terms(question)):
            answer = best.source["title"] + ". " + answer
        links = [{"title": best.source["title"], "url": best.source["url"]}]
        evidence = [{"id": best.source["id"], "revision": best.source["revision"], "heading": best.heading, "quote": quote}]
        sessions[scope] = {"time": self.clock(), "generation": self.generation, "terms": self._query_terms(question)[:20] if not context_used else words[:24], "ids": [best.source["id"]]}
        # Ограничиваем производную память; она не становится корпусом для обучения.
        if len(sessions) > 1000:
            oldest = min(sessions, key=lambda key: sessions[key]["time"])
            del sessions[oldest]
        return {"status": "answer", "text": answer, "links": links, "evidence": evidence, "context_used": context_used,
                "timing_ms": round((time.perf_counter() - started) * 1000, 2)}
````

## ai_core/site_search_reasoning.py

SHA-256: 2fdc33a2c12a3430e7ac40c8a4d175e2366bfbc8eab553fda28e51193dcbc441

````python
"""Проверяемые операции над вопросами и фактами без генерации новых правил.

Этот слой не заменяет понимание языка: он обрабатывает явные части вопроса,
известные виды утверждений и доверенные метаданные источника.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


QUESTION_START = r"(?:кому|кто|сколько|как|где|когда|что|зачем|почему|можно ли|до какого времени|в какой срок|при какой температуре)"
QUESTION_WORDS = set("кому кто сколько как где когда что зачем почему можно до в при какой какая какие какое а и если для это он она его ее её".split())


def explicit_names(text: str) -> list[str]:
    """Заглавное слово в начале предложения само по себе не является именем."""
    names = []
    for match in re.finditer(r'\b[А-ЯЁA-Z][а-яёa-zА-ЯЁA-Z0-9-]+',text):
        prefix = text[:match.start()].rstrip()
        if not prefix or prefix[-1:] in '.!?':
            continue
        if match.group().casefold() not in QUESTION_WORDS:
            names.append(match.group())
    return list(dict.fromkeys(names))


def question_context(text: str) -> tuple[str,str]:
    """Отделяем факты перед вопросом; их смысл остаётся доступен проверке условий."""
    match = re.search(rf'(?:^|[.!?]\s+)({QUESTION_START})\b',text,re.I)
    if match and match.start(1)>0:
        return text[:match.start(1)].strip(),text[match.start(1):].strip()
    return '',text


def lookup_question(text: str) -> str:
    facts, focus = question_context(text)
    names = explicit_names(facts)
    # «Заказ Аврора оплачен. Сколько дней он хранится?» всё ещё про Аврору.
    if len(names)==1 and not explicit_names(focus):
        focus = focus.rstrip(' ?. ')+' '+names[0]+'?'
    # «Кофемашина сломалась. Кому сообщить?» не содержит предмета во
    # второй фразе: описание случая по-прежнему нужно для поиска.
    focus_tokens = set(re.findall(r'\w+',focus.casefold()))
    generic_actions = set('сообщить сказать обратиться делать сделать долго дней день ждать'.split())
    if facts and not (focus_tokens - QUESTION_WORDS - generic_actions):
        return text
    return focus


def compound_parts(question: str) -> list[str]:
    """Разделяем только явные вопросы, а не обычное перечисление объектов."""
    parts = re.split(rf"(?:\?\s+|[;,]\s*|\s+(?:и|а также)\s+)(?={QUESTION_START}\b)", question.strip(), flags=re.I)
    if len(parts) == 1:
        return parts
    facts, parts[0] = question_context(parts[0])
    # «Кому и в какой срок направить заявку?» имеет общий хвост предложения.
    if re.fullmatch(QUESTION_START, parts[0], re.I):
        tail = re.sub(rf"^{QUESTION_START}\s+", "", parts[1], count=1, flags=re.I)
        parts[0] += " " + tail
    names = explicit_names(question)
    # Единственный явно названный объект относится и к соседней части вопроса.
    # При нескольких объектах ничего не угадываем и не склеиваем их правила.
    if len(names) == 1:
        name = names[0]
        parts = [part if name.casefold() in part.casefold() else part.rstrip(" ?.") + " " + name for part in parts]
    result = []
    fact_names = set(explicit_names(facts))
    for part in parts:
        # Общий факт переносится вместе с вопросом. Факт об одном явно
        # названном товаре не становится фактом о другом товаре.
        shared = facts if not fact_names or fact_names.issubset(set(explicit_names(part))) else ''
        result.append((shared+' '+part.strip(' ,;?.')+'?').strip())
    return result


def authority(source: dict) -> int:
    """Официальность задаёт сервер сайта, а не название или текст файла."""
    return 2 if source.get("authority") == "official" else 1


def validate_authority(source: dict) -> None:
    value = source.get("authority", "reference")
    if value not in {"official", "reference"} or (value == "official" and source.get("kind") != "document"):
        raise ValueError("source_authority_invalid")


NUMBERS = {
    "один": "1", "одна": "1", "одного": "1", "одной": "1",
    "два": "2", "две": "2", "двух": "2", "три": "3", "трех": "3",
    "четыре": "4", "четырех": "4", "пять": "5", "пяти": "5",
    "шесть": "6", "шести": "6", "семь": "7", "семи": "7",
    "восемь": "8", "восьми": "8", "девять": "9", "девяти": "9",
    "десять": "10", "десяти": "10", "одиннадцать": "11", "одиннадцати": "11",
    "двенадцать": "12", "двенадцати": "12",
}
RELATIONS = (
    ("storage", r"хран\w*"), ("processing", r"обработ\w*"),
    ("cancellation", r"отмен\w*|подтвержд\w*"), ("delivery", r"достав\w*"),
    ("repair", r"ремонт\w*|чинить|почин\w*"), ("return", r"возврат\w*|вернуть|возвращ\w*"),
)
NOISE = r"\b(?:срок\w*|длительност\w*|составля\w*|течени\w*|длит\w*|занима\w*|равен|равно|можно|нельзя|разреш\w*|запрещ\w*|допуска\w*|хран\w*|обработ\w*|отмен\w*|подтвержд\w*|достав\w*|ремонт\w*|чинить|почин\w*|возврат\w*|вернуть|возвращ\w*|производ\w*|выполня\w*|осуществля\w*|календарн\w*|рабоч\w*|день|дня|дней|дни|час\w*|минут\w*|суток|сутки)\b"


@dataclass(frozen=True)
class Claim:
    relation: str
    subject: frozenset[str]
    condition: str
    value: tuple


def claims(sentence: str, tokenize) -> list[Claim]:
    """Выделяем срок и разрешение/запрет; условия сохраняем отдельно.

    Разные условия не объявляем противоречием. Сложные исключения и неясные
    числовые диапазоны не сводим к одному числу.
    """
    value = sentence.casefold().replace("ё", "е")
    value = re.sub(r"\b(" + "|".join(NUMBERS) + r")\b", lambda m: NUMBERS[m.group()], value)
    condition = ""
    conditional = re.match(r"(если\b[^,]+|при\b[^,]+),\s*(.+)", value)
    if conditional:
        condition, value = conditional.groups()
    # Уточнения «после уведомления», «в холодильнике» и т. п. тоже существенны.
    qualifier = re.search(r"\b(?:после|до|при|если|в холодильнике|в помещении|на улице)\b.*", value)
    if qualifier:
        condition += " " + qualifier.group()
        value = value[:qualifier.start()]
    # Сохраняем связки и отрицание: поиск отбрасывает их как стоп-слова,
    # но для сравнения условий «оплачен» и «не оплачен» принципиально различны.
    condition = " ".join(condition.split())
    relation = next((name for name, pattern in RELATIONS if re.search(pattern, value)), None)
    if relation is None:
        return []
    quantities = re.findall(r"\b(\d+)\s*(рабоч\w*|календарн\w*)?\s*(день|дня|дней|дни|суток|сутки|час\w*|минут\w*)\b", value)
    # Не упрощаем диапазоны, сроки «до» и составные числительные.
    uncertain_number = bool(re.search(r"\d\s*[-–—]\s*\d|\bне (?:менее|более)|\bот\s+\d|\d\s+\d", value))
    subject_text = re.sub(NOISE, " ", value)
    subject_text = re.sub(r"\d+(?:[.,]\d+)?", " ", subject_text)
    subject = frozenset(tokenize(subject_text))
    if not subject:
        return []
    result = []
    if len(quantities) == 1 and not uncertain_number:
        number, calendar, unit = quantities[0]
        unit = "day" if unit.startswith(("д", "сут")) else "hour" if unit.startswith("час") else "minute"
        calendar = "working" if calendar.startswith("рабоч") else "calendar" if calendar else "unspecified"
        result.append(Claim("duration:" + relation, subject, condition, (int(number), unit, calendar)))
    not_forbidden = bool(re.search(r"\bне запрещ", value))
    forbidden = bool(re.search(r"\bнельзя|\bне (?:разреш|допуска)", value)) or (bool(re.search(r"\bзапрещ", value)) and not not_forbidden)
    permitted = (not_forbidden or bool(re.search(r"\bможно|\bразреш|\bдопуска", value))) and not forbidden
    if re.search(r"\bможно не\b", value):
        permitted = False  # Право не выполнять действие не является разрешением выполнить его.
    if forbidden or permitted:
        result.append(Claim("permission:" + relation, subject, condition, (not forbidden,)))
    return result


def contradictory(left: Claim, right: Claim) -> bool:
    if left.relation != right.relation or left.condition != right.condition:
        return False
    # Строго совпадающий предмет уменьшает риск смешать сходные товары/операции.
    if left.subject != right.subject:
        return False
    return left.value != right.value


def conflicting_passages(passages, sentences, tokenize, inherit_heading=False) -> bool:
    """Сравниваем только уже доступные и подходящие к вопросу фрагменты."""
    found = {}
    templates = {}
    for passage in passages:
        for sentence in sentences(passage.text):
            # Сохраняем прежнюю проверку дословного правила с разными числами.
            normalized = re.sub(r"[^\w\s.,]", " ", sentence.casefold().replace("ё", "е"))
            normalized = " ".join(normalized.split())
            numbers = tuple(re.findall(r"\b\d+(?:[.,]\d+)?\b", normalized))
            # Номера страниц описывают расположение, а не разные значения правила.
            pagination = bool(re.match(r'^(?:страница|стр\.|лист)\s+\d+\b', sentence, re.I))
            if numbers and not pagination and not re.search(r'\bесли\b|\bпри\b|исключени|только если', sentence, re.I):
                template = re.sub(r"\b\d+(?:[.,]\d+)?\b", "#", normalized)
                if template in templates and templates[template] != numbers:
                    return True
                templates[template] = numbers
            extracted = claims(sentence, tokenize)
            # Короткая строка «срок хранения — 2 дня» может называть предмет
            # только в заголовке раздела. Заголовок нужен для сравнения,
            # исходная цитата в ответе остаётся неизменной.
            if not extracted and inherit_heading and not re.search(r'\.(?:md|txt|pdf|docx?)$',passage.heading,re.I):
                relation = next((pattern for _,pattern in RELATIONS if re.search(pattern,sentence,re.I)),None)
                if relation and re.search(relation,passage.heading,re.I):
                    extracted = claims(passage.heading+' '+sentence,tokenize)
            for claim in extracted:
                key = (claim.relation, claim.subject, claim.condition)
                if key in found and contradictory(claim, found[key]):
                    return True
                found[key] = claim
    return False
````

## ai_core/site_search_language.py

SHA-256: cc92c6cc43ab03ebd52ad40dc4ca12947d1a7a8f65967155a2b1169d8b21d22f

````python
"""Небольшой словарь русских поисковых понятий; фактов и готовых ответов здесь нет."""
import re
from .site_search_reasoning import NUMBERS

# Выражения нормализуются только для поиска. Исходный текст ответа сохраняется.
PHRASES = (
    (r"\bкак\s+долго\s+(?:можно\s+)?держать\b", "сколько хранится"),
    (r"\bухожу\s+после\s+смены\b|\bконец\s+рабочего\s+дня\b", "закрытие"),
    (r"\bне\s+(?:работает|включается)\b|\bперестал[аио]?\s+работать\b", "поломка"),
    (r"\b(?:надеть|одеть)\s+на\s+руки\b", "перчатки"),
    (r"\bнаведения?\s+порядка\b", "уборка"),
    (r"\bсовсем\s+немного\b", "минимальный"),
    (r"\bпоздоровал\w*\s+за\s+руку\b", "рукопожатие"),
)

SYNONYMS = {
    "наличные": ["купюра", "купюры", "банкнота", "наличные"],
    "мыть": ["мыть", "вымыть", "помыть", "мытьё"],
    "улица": ["улица", "снаружи"],
    "помещение": ["помещение", "внутри"],
    "взвешивание": ["взвешивание", "взвесить", "взвешивать", "отвесить"],
    "грамм": ["грамм", "граммы", "г"],
    "день": ["день", "дня", "дней", "дни"],
    "человек": ["человек", "родственник", "родственники"],
    "использовать": ["использовать", "использоваться", "пользоваться"],
    "один": ["один", "одним"],
    "ремонт": ["ремонт", "починить", "ремонтировать"],
    "самостоятельный": ["самостоятельный", "самостоятельно", "самому"],
    "поломка": ["поломка", "сломалась", "сломанный", "сломался"],
    "выключить": ["выключить", "выключаются", "обесточивать", "обесточить"],
    "обязательно": ["обязательно", "обязан", "обязаны"],
}


def search_language(text: str) -> str:
    value = text.casefold().replace('ё', 'е')
    for pattern, replacement in PHRASES:
        value = re.sub(pattern, replacement, value)
    # Утрата накопленных прав/баллов — отдельный смысл, не физическое горение.
    if re.search(r"скидк|бонус|балл|накоплен", value):
        value = re.sub(r"\b(?:пропад\w*|исчез\w*|сгора\w*)\b", "истечение", value)
    return value


def requested_facet(question: str) -> str | None:
    value = question.casefold()
    if re.search(r"сколько стоит|стоимость|цена|почём|за какую сумму", value):
        return 'cost'
    if re.search(r"(?:с какого|в каком) год|год (?:начала|основания)", value):
        return 'year'
    if re.search(r"\bкому\b|кто (?:(?:его|её|ее|их) )?(?:выда|принима|обраб|отвечает)", value):
        return 'recipient'
    if re.search(r"как долго|какой срок|сколько.*(?:ждать|дн|хран|длит)|подожд[её]т.*(?:день|дня|недел)", value):
        return 'duration'
    if re.search(r"как (?:отмен|оформ|направ|отправ|перед|получ)", value):
        return 'procedure'
    return None


def supplies_facet(text: str, facet: str | None) -> bool:
    value = text.casefold()
    if facet == 'cost':
        return bool(re.search(r"\bруб\w*|₽|цен\w*|стоимост\w*|бесплатн\w*", value)) and not bool(re.search(r"не (?:указан|определ[её]н|извест|установлен)|нет (?:сведений|информации)", value))
    if facet == 'year':
        return bool(re.search(r"\b(?:19|20)\d{2}\b", text))
    if facet == 'recipient':
        return bool(re.search(r"координатор|диспетчер|старш\w* сотрудник|старш\w* смен|администратор|дежурн|менеджер|руководител|кладовщик|оператор", value))
    if facet == 'duration':
        return bool(re.search(r"\b(?:день|дня|дней|дни|суток|час\w*|минут\w*|недел\w*|месяц\w*)\b", value)) and bool(re.search(r"\d|\b(?:" + "|".join(NUMBERS) + r")\b", value.replace('ё', 'е')))
    if facet == 'procedure':
        return bool(re.search(r"отправ\w*|переда\w*|заполн\w*|запис\w*|показыв\w*|покаж\w*|сообщ\w*|подат\w*|звон\w*|обратит\w*", value))
    return True
````

## tests/test_site_search_cross_documents.py

SHA-256: 1868358ebd653f5def58f919affc6a263db85621a68301c1b029d3b3c9754d3a

````python
"""Составные вопросы и независимые правила проверяются вместе с памятью."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.cross_documents_probe import SPEC, ORIGIN, sources, errors_for

A = Principal('closed_lab_a','staff')
B = Principal('closed_lab_b','staff')


def ask(runtime,text,actor,chat):
    revisions={key:item['revision'] for key,item in runtime.index.sources.items()}
    return runtime.query(text,actor,chat,None,revisions)


class CrossDocumentTests(unittest.TestCase):
    def setUp(self):
        self.spec=json.loads(SPEC.read_text(encoding='utf-8'))
        self.records=sources(self.spec)
        self.index=ClosedSiteIndex([ORIGIN])
        self.index.publish(self.records,self.index.begin('cross-tests'))

    def check_cases(self,key):
        for case in self.spec[key]:
            with self.subTest(case=case['id']):
                result=self.index.search(case['q'],B if case.get('user')=='b' else A,case.get('chat',case['id']))
                self.assertEqual(errors_for(case,result),[],result)

    def test_frozen_main_cases(self):
        self.check_cases('cases')

    def test_frozen_alternate_cases(self):
        self.check_cases('holdout')

    def open_runtime(self):
        folder=tempfile.TemporaryDirectory(prefix='vates-cross-test-')
        self.addCleanup(folder.cleanup)
        path=Path(folder.name)/'index.sqlite3'
        runtime=DurableSearch([ORIGIN],path)
        runtime.publish(self.records,runtime.begin('initial'))
        return runtime,path

    def test_pending_rule_dependencies_survive_restart_and_correction(self):
        runtime,path=self.open_runtime()
        self.assertEqual(ask(runtime,'Сколько хранится товар Халцедон?',A,'chat')['status'],'clarify')
        runtime=DurableSearch([ORIGIN],path)
        result=ask(runtime,'Упаковка не вскрыта.',A,'chat')
        self.assertIn('8 календарных',result['text'])
        runtime=DurableSearch([ORIGIN],path)
        self.assertEqual(ask(runtime,'А если упаковка вскрыта?',A,'chat')['status'],'conflict')
        self.assertEqual(ask(runtime,'А сколько дней?',A,'chat')['status'],'clarify')

    def test_changed_nonquoted_dependency_invalidates_context(self):
        runtime,path=self.open_runtime()
        ask(runtime,'Упаковка не вскрыта. Сколько хранится товар Халцедон?',A,'chat')
        updated=copy.deepcopy(self.records)
        next(item for item in updated if item['id']=='lab_cross_short')['revision']='cross-v2'
        runtime.publish(updated,runtime.begin('revision'))
        runtime=DurableSearch([ORIGIN],path)
        self.assertEqual(ask(runtime,'А если упаковка вскрыта?',A,'chat')['status'],'clarify')

    def test_changed_base_is_rechecked_after_restart(self):
        runtime,path=self.open_runtime()
        ask(runtime,'Сколько хранится товар Халцедон?',A,'chat')
        updated=copy.deepcopy(self.records)
        base=next(item for item in updated if item['id']=='lab_cross_base')
        base['revision']='cross-v2'
        base['sections'][0]['text']='Товар Халцедон хранится 12 календарных дней.'
        runtime.publish(updated,runtime.begin('revision'))
        runtime=DurableSearch([ORIGIN],path)
        result=ask(runtime,'Упаковка не вскрыта. Сколько хранится товар Халцедон?',A,'chat')
        self.assertIn('12 календарных',result['text'])
        self.assertNotIn('8 календарных',result['text'])

    def test_users_and_chats_stay_separate_after_restart(self):
        runtime,path=self.open_runtime()
        ask(runtime,'Сколько хранится товар Танзанит?',B,'same')
        runtime=DurableSearch([ORIGIN],path)
        self.assertEqual(ask(runtime,'А если упаковка вскрыта?',A,'same')['status'],'clarify')
        self.assertEqual(ask(runtime,'А если упаковка вскрыта?',B,'other')['status'],'clarify')
        self.assertEqual(ask(runtime,'А если упаковка вскрыта?',B,'same')['status'],'conflict')

    def test_revoked_dependency_does_not_leak_from_disk(self):
        runtime,path=self.open_runtime()
        ask(runtime,'Сколько хранится товар Танзанит?',B,'same')
        updated=copy.deepcopy(self.records)
        limited=next(item for item in updated if item['id']=='lab_cross_restricted')
        limited['revision']='cross-v2'
        limited['access']={'scope':'admin'}
        runtime.publish(updated,runtime.begin('revoked'))
        runtime=DurableSearch([ORIGIN],path)
        self.assertEqual(ask(runtime,'А если упаковка вскрыта?',B,'same')['status'],'clarify')
        result=ask(runtime,'Упаковка вскрыта. Сколько хранится товар Танзанит?',B,'new')
        self.assertIn('7 календарных',result['text'])
        self.assertEqual({item['id'] for item in result['evidence']},{'lab_cross_public'})

    def test_explicit_new_subject_overrides_old_condition_memory(self):
        self.index.search('Упаковка вскрыта. Сколько дней обрабатывается поставка Содалит?',A,'chat')
        result=self.index.search('Упаковка вскрыта. Сколько хранится товар Танзанит?',A,'chat')
        self.assertIn('7 календарных',result['text'])
        self.assertNotIn('Содалит',result['text'])

    def test_named_fact_does_not_apply_to_other_subject(self):
        result=self.index.search('Упаковка поставки Содалит вскрыта. Сколько дней обрабатывается поставка Содалит и сколько хранится товар Танзанит?',B)
        self.assertEqual(result['status'],'partial',result)
        self.assertIn('2 рабочих',result['text'])
        self.assertIn('Уточните',result['text'])
        self.assertNotIn('3 календарных',result['text'])

    def test_selected_document_keeps_explicit_scope(self):
        result=self.index.search('Упаковка вскрыта. Сколько хранится товар Халцедон?',A,selected_source='lab_cross_base')
        self.assertEqual(result['status'],'answer')
        self.assertIn('8 календарных',result['text'])

    def test_lower_authority_cannot_hide_applicable_conflict(self):
        records=copy.deepcopy(self.records)
        next(item for item in records if item['id']=='lab_cross_short')['authority']='reference'
        self.index.publish(records,self.index.begin('reference'))
        result=self.index.search('Упаковка вскрыта. Сколько хранится товар Халцедон?',A)
        self.assertEqual(result['status'],'conflict')

    def test_unrelated_damaged_table_does_not_capture_lowercase_subject(self):
        damaged=copy.deepcopy(self.records[0])
        damaged.update(id='damaged',title='Коралл',url=ORIGIN+'/materials/damaged')
        damaged['sections']=[{'heading':'Хранение товара Коралл','text':'| Товар | Срок хранения |\n| --- | --- |\n| Коралл | |'}]
        self.index.publish(self.records+[damaged],self.index.begin('damaged'))
        result=self.index.search('Упаковка вскрыта. Как долго можно держать товар халцедон?',A)
        self.assertEqual(result['status'],'conflict')
        self.assertEqual(self.index.search('Сколько хранится товар коралл?',A)['status'],'unavailable')


if __name__=='__main__':
    unittest.main()
````

## evaluation/closed_search/cross_documents_20261029.json

SHA-256: 0d97b6a3450759f3bafa587b15c86e71ee3f835ffe5d66188575c8334c0b0585

````json
{
  "description": "Вымышленные правила, зафиксированные до изменения алгоритма. Разные официальные документы не отменяют друг друга без явной проверенной связи.",
  "documents": [
    {"id":"lab_cross_receiver","name":"Приёмка_Содалит.md","text":"Поставку Содалит принимает координатор склада."},
    {"id":"lab_cross_processing","name":"Обработка_Содалит.md","text":"Если упаковка не вскрыта, поставка Содалит обрабатывается 6 рабочих дней. Если упаковка вскрыта, поставка Содалит обрабатывается 2 рабочих дня."},
    {"id":"lab_cross_base","name":"Хранение_Халцедон.md","text":"Товар Халцедон хранится 8 календарных дней."},
    {"id":"lab_cross_short","name":"Ограничение_Халцедон.md","text":"# Хранение товара Халцедон\nИсключение: если упаковка вскрыта, срок хранения составляет 2 календарных дня."},
    {"id":"lab_cross_public","name":"Хранение_Танзанит.md","text":"Товар Танзанит хранится 7 календарных дней."},
    {"id":"lab_cross_restricted","name":"Ограничение_Танзанит.md","allowedUserIds":["closed_lab_b"],"text":"Если упаковка вскрыта, товар Танзанит хранится 3 календарных дня."},
    {"id":"lab_cross_warehouse","name":"Склад_Азурит.md","text":"Если товар находится на складе, товар Азурит хранится 10 календарных дней."},
    {"id":"lab_cross_shop","name":"Зал_Азурит.md","text":"Если товар находится в зале, товар Азурит хранится 4 календарных дня."},
    {"id":"lab_cross_current","name":"Действующий_Кахолонг.md","text":"Товар Кахолонг хранится 9 календарных дней."},
    {"id":"lab_cross_archived","name":"Старый_Кахолонг.md","status":"archived","text":"Товар Кахолонг хранится 15 календарных дней."},
    {"id":"lab_cross_delivery","name":"Доставка_Кахолонг.md","text":"Товар Кахолонг доставляется 5 рабочих дней."},
    {"id":"lab_cross_other","name":"Приёмка_Танзанит.md","text":"Товар Танзанит принимает диспетчер."}
  ],
  "cases": [
    {"id":"compound-facts","q":"Упаковка вскрыта. Кто принимает поставку Содалит и сколько дней она обрабатывается?","has":["координатор склада","2 рабочих"],"absent":["6 рабочих"],"sources":["lab_cross_receiver","lab_cross_processing"],"chat":"compound-facts"},
    {"id":"compound-correction","q":"А если упаковка не вскрыта?","has":["6 рабочих"],"absent":["2 рабочих"],"sources":["lab_cross_processing"],"chat":"compound-facts"},
    {"id":"compound-missing","q":"Кто принимает поставку Содалит и сколько дней она обрабатывается?","status":"partial","has":["координатор склада","Уточните","упаковка"],"sources":["lab_cross_receiver"],"chat":"missing"},
    {"id":"compound-clarified","q":"Упаковка вскрыта.","has":["2 рабочих"],"absent":["6 рабочих"],"sources":["lab_cross_processing"],"chat":"missing"},
    {"id":"other-user","user":"b","q":"А если упаковка не вскрыта?","status":"clarify","chat":"missing"},
    {"id":"other-chat","q":"А сколько дней?","status":"clarify","chat":"new"},
    {"id":"short-active","q":"Упаковка вскрыта. Сколько хранится товар Халцедон?","status":"conflict","absent":["8 календарных","2 календарных"]},
    {"id":"short-false","q":"Упаковка не вскрыта. Сколько хранится товар Халцедон?","has":["8 календарных"],"absent":["2 календарных"],"sources":["lab_cross_base"]},
    {"id":"short-unknown","q":"Сколько хранится товар Халцедон?","status":"clarify","has":["упаковка"],"chat":"short"},
    {"id":"short-clarified-false","q":"Упаковка не вскрыта.","has":["8 календарных"],"sources":["lab_cross_base"],"chat":"short"},
    {"id":"private-hidden","q":"Упаковка вскрыта. Сколько хранится товар Танзанит?","has":["7 календарных"],"absent":["3 календарных"],"sources":["lab_cross_public"]},
    {"id":"private-visible-conflict","user":"b","q":"Упаковка вскрыта. Сколько хранится товар Танзанит?","status":"conflict"},
    {"id":"private-false","user":"b","q":"Упаковка не вскрыта. Сколько хранится товар Танзанит?","has":["7 календарных"],"sources":["lab_cross_public"]},
    {"id":"warehouse","q":"Товар находится на складе. Сколько хранится товар Азурит?","has":["10 календарных"],"absent":["4 календарных"],"sources":["lab_cross_warehouse"]},
    {"id":"shop","q":"Товар находится в зале. Сколько хранится товар Азурит?","has":["4 календарных"],"absent":["10 календарных"],"sources":["lab_cross_shop"]},
    {"id":"place-unknown","q":"Сколько хранится товар Азурит?","status":"clarify","has":["товар"]},
    {"id":"different-operations","q":"Сколько хранится товар Кахолонг и сколько дней он доставляется?","has":["9 календарных","5 рабочих"],"absent":["15 календарных"],"sources":["lab_cross_current","lab_cross_delivery"],"chat":"operations"},
    {"id":"different-operation-followup","q":"А сколько дней?","status":"clarify","chat":"operations"},
    {"id":"topic-switch","q":"Кто принимает товар Танзанит?","has":["диспетчер"],"sources":["lab_cross_other"],"chat":"compound-facts"},
    {"id":"no-previous-topic","q":"А если упаковка вскрыта?","absent":["Содалит","2 рабочих","6 рабочих"],"allowed_statuses":["clarify","not_found"],"chat":"compound-facts"},
    {"id":"compound-conflict","q":"Упаковка вскрыта. Кто принимает поставку Содалит и сколько хранится товар Халцедон?","status":"conflict","absent":["координатор","8 календарных","2 календарных"],"chat":"conflicted"},
    {"id":"conflict-does-not-seed","q":"А сколько дней?","status":"clarify","chat":"conflicted"}
  ],
  "holdout": [
    {"id":"lowercase-short","q":"Упаковка вскрыта. Как долго можно держать товар халцедон?","status":"conflict"},
    {"id":"reverse-order","q":"Упаковка не вскрыта. Сколько дней обрабатывается поставка Содалит и кто её принимает?","has":["6 рабочих","координатор склада"],"sources":["lab_cross_processing","lab_cross_receiver"]},
    {"id":"false-short-paraphrase","q":"Упаковка не открыта. Какой срок хранения у товара Халцедон?","has":["8 календарных"],"sources":["lab_cross_base"]},
    {"id":"known-false-next-operation","q":"Товар находится на складе. Сколько хранится товар Азурит и сколько дней доставляется товар Кахолонг?","has":["10 календарных","5 рабочих"],"sources":["lab_cross_warehouse","lab_cross_delivery"]},
    {"id":"private-compound","q":"Упаковка вскрыта. Кто принимает товар Танзанит и сколько дней он хранится?","has":["диспетчер","7 календарных"],"absent":["3 календарных"],"sources":["lab_cross_other","lab_cross_public"]},
    {"id":"no-fact-invention","q":"Не знаю, вскрыта ли упаковка. Сколько хранится товар Халцедон?","status":"clarify","absent":["8 календарных","2 календарных"]}
  ]
}
````

## evaluation/closed_search/cross_documents_probe.py

SHA-256: 73e4cc426cf1a09d592301e94ad7660fc357b804277ec015867e797c2ab4cf38

````python
"""Ожидания междокументного ответа фиксируются до изменения поиска."""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from ai_core.site_search import ClosedSiteIndex, Principal

ROOT=Path(__file__).resolve().parents[2]
SPEC=Path(__file__).with_name('cross_documents_20261029.json')
LAB=ROOT/'.runtime/closed-search-lab-20261028'
ORIGIN='http://127.0.0.1:3040'


def sources(spec):
    return [{'id':item['id'],'revision':'cross-v1','kind':'document','authority':'official','title':item['name'],
             'url':ORIGIN+'/materials/'+item['id'],'status':item.get('status','published'),
             'access':{'scope':'restricted','users':item['allowedUserIds']} if item.get('allowedUserIds') else {'scope':'workspace'},
             'sections':[{'heading':item['name'],'text':item['text']}]} for item in spec['documents']]


def errors_for(case,result):
    errors=[]
    if result['status'] not in case.get('allowed_statuses',[case.get('status','answer')]): errors.append('status:'+result['status'])
    def contains(text):
        return bool(re.search((r'(?<!\d)' if text[:1].isdigit() else '')+re.escape(text),result['text'],re.I))
    for text in case.get('has',[]):
        if not contains(text):errors.append('missing:'+text)
    for text in case.get('absent',[]):
        if contains(text):errors.append('unexpected:'+text)
    if 'sources' in case and {e['id'] for e in result['evidence']} != set(case['sources']):errors.append('sources')
    if result['status'] not in {'answer','partial'} and (result['evidence'] or result['links']):errors.append('evidence-on-refusal')
    if result['status']=='answer' and len(case.get('sources',[]))>1 and not all(e.get('question') for e in result['evidence']):errors.append('unattributed-part')
    return errors


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--label',default='cross-documents');parser.add_argument('--holdout',action='store_true');args=parser.parse_args()
    spec=json.loads(SPEC.read_text(encoding='utf-8'))
    index=ClosedSiteIndex([ORIGIN]);index.publish(sources(spec),index.begin('cross'))
    rows=[]
    for case in spec['holdout' if args.holdout else 'cases']:
        result=index.search(case['q'],Principal('closed_lab_'+case.get('user','a'),'staff'),case.get('chat',case['id']))
        errors=errors_for(case,result)
        rows.append({'id':case['id'],'question':case['q'],'result':result,'pass':not errors,'errors':errors})
    file=LAB/(args.label+'-'+str(time.time_ns())+'.json')
    report={'specSHA256':hashlib.sha256(SPEC.read_bytes()).hexdigest(),'passed':sum(r['pass'] for r in rows),'total':len(rows),'cases':rows}
    file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'file':str(file),'passed':report['passed'],'total':report['total'],'failures':[{'id':r['id'],'errors':r['errors'],'text':r['result']['text']} for r in rows if not r['pass']]},ensure_ascii=False))
    if report['passed']!=report['total']:raise SystemExit(1)


if __name__=='__main__':main()
````

## evaluation/closed_search/cross_documents_http.cjs

SHA-256: 60df0973e0cc231aab8821fa5b381267ef57783272f0e25247b7382d0764cf6f

````javascript
/* Реальные загрузка, две сессии сайта и восстановление составного диалога. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const {createHash}=require('node:crypto');
const specPath=path.join(__dirname,'cross_documents_20261029.json');
const pendingPath=path.join(lab,'cross-http-pending.json');

function contains(text,fragment){
    const escaped=fragment.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    return new RegExp((/^\d/.test(fragment)?'(?<!\\d)':'')+escaped,'i').test(text);
}

async function main(){
    const spec=JSON.parse(await fs.readFile(specPath,'utf8'));
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const restored=process.argv.includes('--restored');
    const rows=[],rateLimitWaits=[];
    async function ask(item,chat){
        const actor=item.user||'a';
        let started=performance.now();
        const body={chatId:chat,messages:[{role:'user',content:item.q}]};
        let result=await call('/api/ai-chat',body,cookies[actor]);
        if(result.response.status===429){
            // Не отключаем лимит стенда. Один повтор только после указанного
            // сервером срока; ожидание записывается отдельно от скорости ИИ.
            const seconds=Number(result.response.headers.get('retry-after'))||300;
            if(seconds<0||seconds>360)throw new Error('Unexpected rate-limit interval');
            rateLimitWaits.push({id:item.id,actor,seconds});
            console.log(JSON.stringify({event:'respecting-rate-limit',id:item.id,seconds}));
            for(let remaining=seconds*1000+100;remaining>0;remaining-=Math.min(remaining,30000))await new Promise(resolve=>setTimeout(resolve,Math.min(remaining,30000)));
            started=performance.now();
            result=await call('/api/ai-chat',body,cookies[actor]);
        }
        const {response,value}=result;
        const answer=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(!(item.allowed_statuses||[item.status||'answer']).includes(value.search_status))errors.push('status:'+value.search_status);
        for(const fragment of item.has||[])if(!contains(answer,fragment))errors.push('missing:'+fragment);
        for(const fragment of item.absent||[])if(contains(answer,fragment))errors.push('unexpected:'+fragment);
        const links=value.sourceLinks||[];
        if(item.sources && (links.length!==item.sources.length||item.sources.some(id=>!links.some(link=>link.url.includes(id)))))errors.push('sources');
        if(!['answer','partial'].includes(value.search_status)&&links.length)errors.push('links-on-refusal');
        rows.push({id:item.id,actor,question:item.q,status:value.search_status,answer,links,ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    let ready;
    if(!restored){
        const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
        for(const item of spec.documents){
            const bytes=Buffer.from(item.text,'utf8'),data='data:text/markdown;base64,'+bytes.toString('base64');
            const entry={id:item.id,name:item.name,hasSeparateData:true,section:'Составные вопросы ВатэсAi',size:bytes.length+' B',date:'Локальный тест',searchAuthority:'official',
                ...(item.allowedUserIds?{allowedUserIds:item.allowedUserIds}:{}),...(item.status?{status:item.status}:{})};
            const prior=catalog.find(doc=>doc.id===item.id);
            if(prior){
                if(JSON.stringify(prior)!==JSON.stringify(entry)||await storage('file_data_'+item.id,cookies.admin)!==data)throw new Error('Frozen site fixture changed '+item.id);
            }else{
                await storage('file_data_'+item.id,cookies.admin,data);catalog.push(entry);
            }
        }
        await storage('tea_hub_urgent_files_v1',cookies.admin,catalog);
        ready=await waitSnapshot();
        const prefix='cross-http-'+Date.now();
        for(const item of [...spec.cases,...spec.holdout])await ask(item,prefix+'-'+(item.chat||item.id));
        const saved={conditions:prefix+'-restart-conditions',compound:prefix+'-restart-compound'};
        await ask({id:'seed-condition',user:'b',q:'Сколько хранится товар Танзанит?',status:'clarify',has:['упаковка']},saved.conditions);
        await ask({id:'seed-compound',user:'b',q:'Кто принимает поставку Содалит и сколько дней она обрабатывается?',status:'partial',has:['координатор склада','Уточните'],sources:['lab_cross_receiver']},saved.compound);
        await fs.writeFile(pendingPath,JSON.stringify(saved,null,2));
    }else{
        const saved=JSON.parse(await fs.readFile(pendingPath,'utf8'));
        await ask({id:'restored-other-user',q:'А если упаковка вскрыта?',status:'clarify'},saved.conditions);
        await ask({id:'restored-other-chat',user:'b',q:'А если упаковка вскрыта?',status:'clarify'},saved.conditions+'-other');
        await ask({id:'restored-false',user:'b',q:'Упаковка не вскрыта.',has:['7 календарных'],sources:['lab_cross_public']},saved.conditions);
        await ask({id:'restored-conflict',user:'b',q:'А если упаковка вскрыта?',status:'conflict'},saved.conditions);
        await ask({id:'restored-compound',user:'b',q:'Упаковка вскрыта.',has:['2 рабочих'],absent:['6 рабочих'],sources:['lab_cross_processing']},saved.compound);
        await ask({id:'restored-correction',user:'b',q:'А если упаковка не вскрыта?',has:['6 рабочих'],absent:['2 рабочих'],sources:['lab_cross_processing']},saved.compound);
    }
    const report={createdAt:new Date().toISOString(),restored,ready,rateLimitWaits,specSHA256:createHash('sha256').update(await fs.readFile(specPath)).digest('hex'),
        total:rows.length,passed:rows.filter(row=>row.pass).length,cases:rows};
    const file=path.join(lab,'cross-http-'+(restored?'restored-':'')+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,total:report.total,passed:report.passed,failures:rows.filter(row=>!row.pass)}));
    if(report.passed!==report.total)process.exitCode=1;
}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1;});
````

## evaluation/closed_search/verify_lab.cjs

SHA-256: fc8d6ea532f9165fc90b8990b01472ac7ac5d6cadc778b609dd1a6ef2cdbf531

````javascript
/* Краткая контрольная сводка работающих локальных сервисов без секретов. */
const { fs, path, root, lab, call } = require('./lab_client.cjs');
async function main() {
    require(path.join(lab, 'tea-app-main/node_modules/@next/env')).loadEnvConfig(path.join(lab, 'tea-app-main'));
    const endpoint = process.env.VATES_CLOSED_SEARCH_URL;
    const key = process.env.VATES_CLOSED_SEARCH_KEY;
    const response = await fetch(endpoint + '/status', { headers: { 'X-Vates-Search-Key': key }, signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error('Search status is not available');
    const status = await response.json();
    const unauthenticatedCore = await fetch(endpoint + '/status', { signal: AbortSignal.timeout(5000) });
    const unauthenticatedSite = await call('/api/ai-chat', { messages: [{ role: 'user', content: 'Как хранить чай?' }] });
    const general = await fetch('http://127.0.0.1:8000/health', { signal: AbortSignal.timeout(10000) });
    const catalog = JSON.parse(await fs.readFile(path.join(lab, 'tea-app-main/fixture-data/tea_hub_urgent_files_v1.json'), 'utf8'));
    const expectedSources = require('./export_site.cjs').build().sources.length;
    const reasoningFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'reasoning_20261029.json'), 'utf8'));
    const conditionFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'conditions_20261029.json'), 'utf8'));
    const structureFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'structure_20261029.json'), 'utf8'));
    const structureAssets = JSON.parse(await fs.readFile(path.join(lab, 'structure-assets/manifest.json'), 'utf8'));
    const scanFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'fixtures/scans/spec.json'), 'utf8'));
    const layoutFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'fixtures/layout/spec.json'), 'utf8'));
    const qualityFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'fixtures/quality/spec.json'), 'utf8'));
    const multipageFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'fixtures/multipage/spec.json'), 'utf8'));
    const crossFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'cross_documents_20261029.json'), 'utf8'));
    const expectedDocuments = 9 + reasoningFixtures.documents.length + conditionFixtures.documents.length + structureFixtures.documents.length + structureAssets.length + scanFixtures.documents.length + layoutFixtures.documents.length + qualityFixtures.documents.length + multipageFixtures.documents.length + crossFixtures.documents.length;
    const result = { state: status.state, indexedSources: status.sources, indexedPassages: status.passages,
        restoredFromDisk: status.restored_from_disk, restoreMs: status.restore_ms, persistedContextCount: status.context_count,
        catalogDocuments: catalog.length, coreWithoutKey: unauthenticatedCore.status,
        chatWithoutSession: unauthenticatedSite.response.status, generalApiHealth: general.status,
        expectedSources, expectedDocuments, pass: status.state === 'ready' && status.sources === expectedSources && catalog.length === expectedDocuments
            && unauthenticatedCore.status === 403 && unauthenticatedSite.response.status === 401 && general.status === 200 };
    await fs.writeFile(path.join(lab, 'final-service-status.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
    if (!result.pass) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
````

## evaluation/closed_search/soak_http.cjs

SHA-256: ca2d995bed1e0b6fc80188e952fdd3edc3c50707b46470ca4cc522e8ee4a007a

````javascript
/* Длительная HTTP-нагрузка в отдельном процессе и отдельной SQLite-базе.
   Стенд 3040 и рабочий поиск 8001 не изменяются. Пароли в отчёт не попадают. */
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { randomBytes, createHash } = require('node:crypto');
const net = require('node:net');
const execute = promisify(execFile);
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const origin = 'http://127.0.0.1:3040';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const duration = Number(process.argv[2] || 900);
const port = Number(process.argv[3] || 8016);
const includeStructure = process.argv.includes('--structure');
const includeScans = process.argv.includes('--multipage');
const includeCrossDocuments = process.argv.includes('--cross-documents');
const percentile = (values, fraction) => values.length ? [...values].sort((a, b) => a - b)[Math.max(0, Math.ceil(values.length * fraction) - 1)] : null;
const rounded = value => value === null ? null : Math.round(value * 100) / 100;

function synthetic(number) {
    const code = 'Модуль' + (10000 + number), days = 2 + number % 11;
    return { id: 'bench-' + number, revision: 'v1', kind: 'lesson', title: 'Учебный порядок ' + code,
        url: origin + '/materials/bench-' + number, access: { scope: 'workspace' }, sections: [
            { heading: 'Хранение', text: `Контейнер ${code} хранится ${days} календарных дней. Контейнер ставят на выделенную полку и сохраняют заводскую этикетку.` },
            { heading: 'Получение', text: `Контейнер ${code} выдаёт координатор склада. Сотрудник записывает номер контейнера в журнал выдачи и проверяет упаковку.` },
            { heading: 'Повреждение', text: `Если контейнер ${code} повреждён, сотрудник прекращает использование и сообщает координатору склада. Самостоятельный ремонт контейнера запрещён.` },
            { heading: 'Учёт', text: `Номер контейнера ${code} указывают в карточке движения. Карточка хранится вместе с накладной в выделенном разделе архива.` },
            { heading: 'Учебный характер', text: `Порядок ${code} вымышлен и используется только для измерения поиска. Он не является регламентом компании.` },
        ] };
}

function summarize(rows) {
    const times = rows.map(row => row.ms);
    return { requests: rows.length, passed: rows.filter(row => row.pass).length,
        httpErrors: rows.filter(row => row.http !== 200).length, semanticFailures: rows.filter(row => row.http === 200 && !row.pass).length,
        p50Ms: rounded(percentile(times, .5)), p95Ms: rounded(percentile(times, .95)), p99Ms: rounded(percentile(times, .99)), maxMs: rounded(times.length ? Math.max(...times) : null) };
}

async function main() {
    if (!Number.isFinite(duration) || duration < 5 || duration > 7200 || !Number.isInteger(port) || port < 8016 || port > 8099) throw new Error('Invalid duration or isolated port');
    const shell = process.env.VATES_TEST_POWERSHELL;
    if (!shell) throw new Error('Set VATES_TEST_POWERSHELL to the current PowerShell executable');
    // Не занимаем чужой порт и не присоединяемся к уже существующему процессу.
    await new Promise((resolve, reject) => {
        const probe = net.createServer();
        probe.once('error', reject);
        probe.listen(port, '127.0.0.1', () => probe.close(resolve));
    });
    const directory = path.join(lab, 'soak-http-' + Date.now());
    await fs.mkdir(directory);
    const token = randomBytes(32).toString('hex');
    const config = path.join(directory, 'sites.json');
    await fs.writeFile(config, JSON.stringify({ sites: [{ id: 'soak-local', origins: [origin], token_sha256: createHash('sha256').update(token).digest('hex') }] }));
    const headers = { 'Content-Type': 'application/json', 'X-Vates-Search-Key': token };
    const endpoint = `http://127.0.0.1:${port}/closed-search/soak-local`;
    const output = await fs.open(path.join(directory, 'server.stdout.log'), 'w');
    const errors = await fs.open(path.join(directory, 'server.stderr.log'), 'w');
    const child = spawn(path.join(root, '.venv/Scripts/python.exe'), ['-m', 'uvicorn', 'ai_core.site_search_api:app', '--host', '127.0.0.1', '--port', String(port), '--no-access-log'], {
        cwd: root, windowsHide: true, stdio: ['ignore', output.fd, errors.fd],
        env: { ...process.env, VATES_SEARCH_CONFIG: config, VATES_SEARCH_STORAGE: path.join(directory, 'indexes') },
    });
    let spawnError;
    child.once('error', error => { spawnError = error; });
    const report = { startedAt: new Date().toISOString(), directory, mode: 'authenticated-core-http-single-process-sqlite', requestedSeconds: duration,
        includes: ['HTTP', 'ACL', 'current revisions', 'search', 'SQLite chat context'], excludes: ['site UI', 'site authentication adapter latency', 'OCR per question', 'external Internet'],
        sourceCount: 3000, measurements: [], samples: [], phases: [], failures: [], checks: [] };
    async function request(route, body) {
        const response = await fetch(endpoint + route, { method: body === undefined ? 'GET' : 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30000) });
        return { http: response.status, value: await response.json() };
    }
    async function sample() {
        const command = `$ownedIds=@(${child.pid}) + @(Get-CimInstance Win32_Process -Filter "ParentProcessId = ${child.pid}" | Select-Object -ExpandProperty ProcessId); $ownedProcesses=@(Get-Process -Id $ownedIds -ErrorAction SilentlyContinue); [pscustomobject]@{ processes=$ownedProcesses.Count; rssBytes=($ownedProcesses | Measure-Object WorkingSet64 -Sum).Sum; privateBytes=($ownedProcesses | Measure-Object PrivateMemorySize64 -Sum).Sum; cpuSeconds=($ownedProcesses | Measure-Object CPU -Sum).Sum } | ConvertTo-Json -Compress`;
        try {
            const { stdout } = await execute(shell, ['-NoProfile', '-Command', command], { windowsHide: true, timeout: 15000 });
            const files = await fs.readdir(path.join(directory, 'indexes'));
            let storageBytes = 0;
            for (const file of files) {
                try {
                    storageBytes += (await fs.stat(path.join(directory, 'indexes', file))).size;
                } catch (error) {
                    // Вспомогательный WAL-файл может исчезнуть между readdir и stat.
                    // Это нормальное обслуживание SQLite, а не сбой поискового API.
                    if (error.code !== 'ENOENT') throw error;
                }
            }
            const status = await request('/status');
            report.samples.push({ elapsedSeconds: rounded((performance.now() - started) / 1000), ...JSON.parse(stdout), storageBytes,
                state: status.value.state, contextCount: status.value.context_count, sources: status.value.sources });
        } catch (error) {
            report.samples.push({ elapsedSeconds: rounded((performance.now() - started) / 1000), error: error.name, errorCode: error.code, message: error.message });
        }
    }
    let started = performance.now();
    let monitor;
    let stopMonitor = false;
    try {
        let available = false;
        for (let i = 0; i < 60; i++) {
            if (spawnError) throw spawnError;
            try { available = (await request('/status')).http === 200; } catch {}
            if (available) break;
            await wait(500);
        }
        if (!available) throw new Error('Isolated server did not start');
        const base = JSON.parse(await fs.readFile(path.join(lab, 'probe-snapshot.json'), 'utf8'));
        const additionalCases = [];
        if (includeCrossDocuments) {
            const specPath=path.join(__dirname,'cross_documents_20261029.json');
            const bytes=await fs.readFile(specPath);
            const spec=JSON.parse(bytes.toString('utf8'));
            report.crossSpecSHA256=createHash('sha256').update(bytes).digest('hex');
            base.sources.push(...spec.documents.map(item=>({id:item.id,revision:'cross-v1',kind:'document',authority:'official',title:item.name,
                url:origin+'/materials/'+item.id,status:item.status||'published',
                access:item.allowedUserIds?{scope:'restricted',users:item.allowedUserIds}:{scope:'workspace'},
                sections:[{heading:item.name,text:item.text}]})));
            additionalCases.push(...[...spec.cases,...spec.holdout].map(item=>({...item,id:'cross-'+item.id,
                chat:item.chat?'cross-'+item.chat:undefined,status:item.status||'answer',has:item.has||[],
                principalId:'closed_lab_'+(item.user||'a')})));
        }
        if (includeScans) {
            report.fixtureHashes = {};
            for (const family of ['layout', 'quality', 'multipage']) {
                const folder = path.join(__dirname, 'fixtures', family);
                const spec = JSON.parse(await fs.readFile(path.join(folder, 'spec.json'), 'utf8'));
                for (const item of spec.documents) {
                    const data = await fs.readFile(path.join(folder, item.name));
                    const digest = createHash('sha256').update(data).digest('hex');
                    if (item.sha256 && digest !== item.sha256) throw new Error('Frozen fixture changed: ' + item.id);
                    report.fixtureHashes[item.id] = digest;
                    base.sources.push({id:item.id, revision:'scan-v1', kind:'document', title:item.name,
                        url:origin+'/materials/'+item.id, access:{scope:'workspace'}, sections:[],
                        file_name:item.name, file_data:'data:application/pdf;base64,'+data.toString('base64')});
                }
                additionalCases.push(...[...spec.cases, ...spec.holdout].map(item => ({...item,
                    id:family+'-'+item.id, status:item.status||'answer', has:item.has||[], selected:item.source,
                    noLinks:Boolean(item.status && !['answer','exact_or_refuse'].includes(item.status))})));
            }
        }
        if (includeStructure) {
            for (const filename of ['conditions_20261029.json', 'structure_20261029.json']) {
                const spec = JSON.parse(await fs.readFile(path.join(__dirname, filename), 'utf8'));
                base.sources.push(...spec.documents.map(item => ({ id: item.id, revision: 'v1', kind: 'document',
                    title: item.name, authority: 'official', url: origin + '/materials/' + item.id,
                    access: { scope: 'workspace' }, sections: [{ heading: item.name, text: item.text }] })));
                additionalCases.push(...[...spec.cases, ...spec.holdout].map(item => ({ ...item,
                    id: filename.split('_')[0] + '-' + item.id, status: item.status || 'answer', has: item.has || [],
                    otherUser: item.user === 'b', noLinks: Boolean(item.status && item.status !== 'answer') })));
            }
            const assets = JSON.parse(await fs.readFile(path.join(lab, 'structure-assets/manifest.json'), 'utf8'));
            base.sources.push(...assets.map(item => ({ id: item.id, revision: 'v1', kind: 'document', title: item.name,
                authority: 'official', url: origin + '/materials/' + item.id, access: { scope: 'workspace' },
                file_name: item.name, file_data: item.data, sections: [] })));
            additionalCases.push({ id: 'word-footnote', q: 'Упаковка вскрыта. Сколько хранится товар Берилл?', status: 'answer', has: ['2 календарных'], absent: ['6 календарных'], source: 'lab_struct_docx', selected: includeScans ? 'lab_struct_docx' : undefined },
                { id: 'html-footnote', q: 'Упаковка вскрыта. Сколько хранится товар Селенит?', status: 'answer', has: ['2 календарных'], absent: ['6 календарных'], source: 'lab_struct_html', selected: includeScans ? 'lab_struct_html' : undefined });
        }
        const extra = 3000 - base.sources.length;
        if (extra < 1) throw new Error('Fixture must contain fewer than 3000 sources');
        base.sources.push(...Array.from({ length: extra }, (_, number) => synthetic(number)));
        const revisions = Object.fromEntries(base.sources.map(source => [source.id, source.revision]));
        const published = performance.now();
        if ((await request('/snapshot', base)).http !== 202) throw new Error('Snapshot rejected');
        let ready;
        for (let i = 0; i < 240; i++) {
            ready = (await request('/status')).value;
            if (ready.state === 'ready' || ready.state === 'failed') break;
            await wait(500);
        }
        if (ready.state !== 'ready' || ready.sources !== 3000) throw new Error('Snapshot not ready');
        report.initialStatus = ready;
        report.publishUntilReadyMs = rounded(performance.now() - published);
        const cases = [
            { id: 'storage', dynamic: true, question: code => `Сколько хранится контейнер ${code}?`, status: 'answer', required: days => [days + ' календарных'] },
            { id: 'issuer', dynamic: true, question: code => `Кто выдаёт контейнер ${code}?`, status: 'answer', required: () => ['координатор склада'] },
            { id: 'compound', dynamic: true, question: code => `Кто выдаёт контейнер ${code} и сколько дней он хранится?`, status: 'answer', required: days => ['координатор склада', days + ' календарных'] },
            { id: 'north', q: 'Кому направить заявку Север?', status: 'answer', has: ['старшему'] },
            { id: 'gloves', q: 'Какие перчатки нужны для мытья пола внутри помещения?', status: 'answer', has: ['только резиновые'] },
            { id: 'conflict', q: 'Сколько хранится заказ Кварц?', status: 'conflict', has: ['противоречие'], noLinks: true },
            { id: 'comet', q: 'Как отменить заявку Комета и сколько ждать подтверждения?', status: 'answer', has: ['диспетчеру', '2 рабочих'] },
            { id: 'unknown', q: 'Сколько хранится контейнер НеизвестныйАстероид?', status: 'not_found', has: ['не нашёл'], noLinks: true },
            { id: 'private-staff', q: 'Какая контрольная отметка процедуры Борей?', status: 'not_found', has: ['не нашёл'], noLinks: true },
            { id: 'private-admin', q: 'Какая контрольная отметка процедуры Борей?', role: 'admin', status: 'answer', has: ['8246'] },
            { id: 'follow-seed', q: 'Кому направить заявку Север?', status: 'answer', has: ['старшему'], chat: 'follow' },
            { id: 'follow-answer', q: 'А сколько дней?', status: 'answer', has: ['трёх'], chat: 'follow', context: true },
            { id: 'other-user', q: 'А сколько дней?', status: 'clarify', has: [], chat: 'follow', otherUser: true, noLinks: true },
            { id: 'revision-changed', dynamic: true, question: code => `Сколько хранится контейнер ${code}?`, status: 'processing', required: () => [], changed: true, noLinks: true },
            { id: 'scan', q: 'Какая контрольная отметка у поставки Кристалл?', status: 'answer', has: ['7193'] },
            { id: 'new-chat', q: 'А сколько дней?', status: 'clarify', has: [], noLinks: true },
        ];
        cases.push(...additionalCases);
        report.scenarioCount = cases.length;
        report.structureScenarios = additionalCases.length;
        // Фиксируем состав и код, чтобы долгий прогон нельзя было приписать
        // последующим изменениям алгоритма или другой версии корпуса.
        report.engineHashes = {};
        for (const file of ['site_search.py','site_search_language.py','site_search_conditions.py','site_search_structure.py','site_search_reasoning.py','site_search_runtime.py','site_search_store.py','site_documents.py','site_scan_tables.py','site_scan_layout.py','site_scan_quality.py','site_search_api.py']) {
            report.engineHashes[file] = createHash('sha256').update(await fs.readFile(path.join(root,'ai_core',file))).digest('hex');
        }
        async function ask(client, sequence, phase, save = true) {
            const item = cases[sequence % cases.length];
            const number = (Math.floor(sequence / cases.length) * 37 + client * 191) % extra;
            const code = 'Модуль' + (10000 + number), days = 2 + number % 11;
            const q = item.dynamic ? item.question(code) : item.q;
            // Связанные вопросы делят чат внутри одного цикла. Следующий цикл
            // начинает новую ситуацию, а preflight не оставляет ему свои факты.
            const chat = `${client}-${save ? 'run' : 'preflight'}-${Math.floor(sequence / cases.length)}-${item.chat || 'request-' + sequence}`;
            const before = performance.now();
            let row;
            try {
                const { http, value } = await request('/query', { question: q, chat_id: chat,
                    principal: { user_id: item.principalId || `reader-${client}-${item.otherUser ? 'b' : 'a'}`, role: item.role || 'staff', groups: [] },
                    selected_source: item.selected,
                    current_revisions: item.changed ? { ...revisions, ['bench-' + number]: 'changed-v2' } : revisions });
                const required = item.dynamic ? item.required(days) : item.has;
                const contains = text => new RegExp((/^\d/.test(text)?'(?<!\\d)':'')+text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'i').test(value.text||'');
                const exactOrRefuse = item.status === 'exact_or_refuse';
                const expected = item.allowed_statuses || (exactOrRefuse ? ['answer','unavailable'] : [item.status]);
                const pass = http === 200 && expected.includes(value.status) && (exactOrRefuse && value.status !== 'answer' || required.every(contains))
                    && (item.absent || []).every(text => !contains(text))
                    && (!item.source || value.status !== 'answer' || value.links?.some(link => link.url.includes(item.source)))
                    && (['answer','partial'].includes(value.status) || !value.links?.length)
                    && (!item.sources || value.evidence?.length && item.sources.every(id=>value.evidence.some(e=>e.id===id))
                        && value.evidence.every(e=>item.sources.includes(e.id)))
                    && (!item.noLinks || !value.links?.length) && (item.context === undefined || value.context_used === item.context)
                    && (!item.dynamic || item.changed || JSON.stringify(value.links).includes('bench-' + number));
                row = { case: item.id, client, phase, sequence, elapsedSeconds: rounded((performance.now() - started) / 1000), ms: rounded(performance.now() - before), http, status: value.status, pass };
                if (!pass && report.failures.length < 50) report.failures.push({ ...row, question: q, expectedStatus: item.status, answer: value.text, links: value.links });
            } catch (error) {
                row = { case: item.id, client, phase, sequence, elapsedSeconds: rounded((performance.now() - started) / 1000), ms: rounded(performance.now() - before), http: 0, pass: false, error: error.name };
                if (report.failures.length < 50) report.failures.push(row);
            }
            if (save) report.measurements.push(row);
            return row;
        }
        // Быстрая проверка ожиданий до длительного прогона. Ошибки нельзя скрывать процентилями.
        for (let sequence = 0; sequence < cases.length; sequence++) report.checks.push(await ask(0, sequence, 'preflight', false));
        if (report.checks.some(row => !row.pass)) throw new Error('Preflight quality check failed');
        started = performance.now();
        await sample();
        monitor = (async () => {
            let nextSample = performance.now() + 30000;
            while (!stopMonitor) {
                await wait(500);
                if (!stopMonitor && performance.now() >= nextSample) {
                    await sample();
                    console.log(JSON.stringify({ progressSeconds: Math.round((performance.now() - started) / 1000), ...summarize(report.measurements), latestSample: report.samples.at(-1) }));
                    await fs.writeFile(path.join(directory, 'progress.json'), JSON.stringify({ ...report, measurements: undefined }, null, 2));
                    nextSample = performance.now() + 30000;
                }
            }
        })();
        console.log(JSON.stringify({ event: 'soak-started', directory, durationSeconds: duration, sources: ready.sources, passages: ready.passages, checks: report.checks.length }));
        const phases = [{ name: 'one-client', clients: 1, fraction: .2 }, { name: 'four-clients', clients: 4, fraction: 1 / 3 },
            { name: 'eight-clients', clients: 8, fraction: 1 / 3 }, { name: 'recovery-four', clients: 4, fraction: 2 / 15 }];
        const sequences = Array(8).fill(0);
        let deadline = started;
        for (const phase of phases) {
            deadline += duration * phase.fraction * 1000;
            const phaseStart = performance.now(), first = report.measurements.length;
            await Promise.all(Array.from({ length: phase.clients }, (_, client) => (async () => {
                while (performance.now() < deadline) {
                    await ask(client, sequences[client]++, phase.name);
                    await wait(100);
                }
            })()));
            const elapsed = (performance.now() - phaseStart) / 1000;
            const stats = summarize(report.measurements.slice(first));
            report.phases.push({ name: phase.name, clients: phase.clients, elapsedSeconds: rounded(elapsed), requestsPerSecond: rounded(stats.requests / elapsed), ...stats });
            console.log(JSON.stringify({ event: 'phase-complete', ...report.phases.at(-1) }));
        }
        report.elapsedSeconds = rounded((performance.now() - started) / 1000);
        stopMonitor = true;
        await monitor;
        await sample();
        report.summary = summarize(report.measurements);
        report.finalStatus = (await request('/status')).value;
        report.completed = true;
        if (report.summary.passed !== report.summary.requests) process.exitCode = 1;
    } catch (error) {
        report.completed = false;
        report.error = error.message;
        process.exitCode = 1;
    } finally {
        stopMonitor = true;
        if (monitor) await monitor;
        // Завершаем только процесс, созданный этим прогоном, вместе с его дочерним Python.
        if (child.pid && child.exitCode === null) {
            try { await execute('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, timeout: 10000 }); }
            catch (error) { report.cleanupError = error.message; process.exitCode = 1; }
        }
        await output.close();
        await errors.close();
        report.finishedAt = new Date().toISOString();
        const reportFile = path.join(directory, 'report.json');
        await fs.writeFile(reportFile, JSON.stringify(report, null, 2));
        console.log(JSON.stringify({ report: reportFile, completed: report.completed, summary: report.summary, error: report.error, failures: report.failures.slice(0, 5) }));
    }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
````
