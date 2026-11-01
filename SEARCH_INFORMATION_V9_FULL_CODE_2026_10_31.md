# ВатэсAi: нужная информация внутри документа, полный код

Проект: C:/Users/KROLL/Desktop/Ai. Ниже приведены полные текущие файлы без сокращений. Ядро совпадает с хешами итогового прохода candidate1. Прежние версии сохранены в evaluation/closed_search/information_v9_20261031/before/ai_core.

Результат и ограничения: SEARCH_INFORMATION_V9_REPORT_2026_10_31.md. Известные 100 вопросов: 78/100 вместо 75/100; исправлены средства очистки, упаковка хрупкого прибора и сохранение описания индикатора питания. Все прежние правильные ответы сохранены. Целевой уровень качества ещё не достигнут; интеграция LMS в этом этапе не обновлялась.

## ai_core/site_search.py

Полный путь: C:\Users\KROLL\Desktop\Ai\ai_core\site_search.py.

SHA-256: `2a88ac090a707adae8327802e1cfdacf02f731c72bfc4b63b2754130884d3d9f`.

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
from .site_search_reasoning import authority, compound_parts, conflicting_passages, validate_authority, lookup_question, explicit_names, question_context
from .site_search_conditions import guarded, paragraphs, rules, atoms, atomic_observation, resolve, summary_request, operation_names, has_related_exception
from .site_search_structure import compile_sections, active, question_day, ISSUE_TEXT
from .site_search_links import compile_documents
from .site_search_catalog import build_catalog, validate_catalog, search_catalog
from .site_search_fragments import build_fragments, evidence_links, validate_fragment_url
from .site_search_technical import search_technical
from .site_search_followup import remember_focus, validated_focus, resolve_followup, concise_result, unique_technical_source
from .site_search_followup import explicit_document, document_continuation
from .site_search_ranking import question_weights, supported_positions, requested_action, complete_conditional_quote, body_matches

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
        self.catalog_products = []
        self.fragment_targets, self.fragment_headings = {}, {}

    def rebuild_lookup(self):
        """Обратный указатель: слово → номера фрагментов; без изменения score."""
        self.postings = {}
        self.passage_positions = {}
        self.condition_positions = set()
        self.passage_operations = {}
        self.token_matches = {}
        self.catalog_products = build_catalog(self.passages, terms)
        self.fragment_targets, self.fragment_headings = build_fragments(self.passages, self._sentences)
        for position, passage in enumerate(self.passages):
            self.passage_positions[id(passage)] = position
            self.passage_operations[position] = operation_names(passage.text)
            if guarded(passage.text):
                self.condition_positions.add(position)
            for token in set(passage.words) | set(passage.title_words):
                self.postings.setdefault(token, set()).add(position)

    def _matching_passages(self, word: str, allowed_positions: set[int], body_only=False):
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
                if body_only and not self.passages[position].words[token]:
                    continue
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
                validate_catalog(source)
                validate_fragment_url(source, self.origins)
                # Даже полностью нечитаемый скан без поисковых фрагментов
                # сохраняет состояние ошибки своего документа.
                source['structure_unavailable'] = any('[STRUCTURE_UNCERTAIN]' in str(section.get('text',''))
                                                      for section in source.get('sections',[]))
                accepted[identifier] = source
            compiled = compile_documents(accepted,plain_text)
            for identifier,source in accepted.items():
                for section in compiled[identifier]:
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
        # Вид ответа «перечислить средства» относится к текущему вопросу.
        # Следующее «а погружать его в воду?» не должно наследовать этот фильтр.
        if facet in {'cleaning_agents','forbidden_cleaning_agents'}:facet=None
        dependencies = {dep['id'] for item in self.passages if item.source['id'] in identifiers
                        for dep in (item.structure or {}).get('document_dependencies',[])}
        focus = [identifier for identifier in identifiers if identifier not in dependencies]
        identifiers = list(dict.fromkeys(identifiers+sorted(dependencies)))
        sessions[scope] = {'time': self.clock(), 'generation': self.generation, 'ids': identifiers,
                           'focus_ids': focus or identifiers,
                           'terms': list(dict.fromkeys(words))[:24], 'condition_facts': facts or {},
                           'facet': facet, 'pending_conditions': pending}
        if len(sessions) > 1000:
            del sessions[min(sessions, key=lambda key: sessions[key]['time'])]

    def _usable_passage(self,item,allowed,day):
        structure=item.structure or {}
        dependencies=structure.get('document_dependencies',[])
        valid=all(dep['id'] in allowed and self.sources[dep['id']]['revision']==dep['revision']
                  and self.sources[dep['id']].get('complete',True) and active(dep,day) for dep in dependencies)
        return not valid if structure.get('document_guard') else valid

    def _summarize(self, identifier, scope, sessions, started, day, allowed):
        source = self.sources[identifier]
        if not source.get('complete', True):
            return {'status': 'unavailable', 'text': 'Документ прочитан не полностью. Пока не могу надёжно пересказать его содержание.', 'links': [], 'evidence': []}
        passages = [item for item in self.passages if item.source['id'] == identifier and active(item.structure or {}, day)
                    and self._usable_passage(item,allowed,day)]
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
            important = bool((item.structure or {}).get('document_dependencies')) or guarded(item.text) or bool(re.search(r'нельзя|запрещ|не допуска|обязател|^(?:При|Если)\b', item.text, re.I))
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
        # Условие из описания соседнего товара не должно перехватывать точный
        # ответ ЧаВо только из-за общих слов «чай» и «весовой». Сравниваем
        # покрытие со всеми доступными кандидатами, включая обычные правила.
        full_coverage = None
        if not scoped and not named and words:
            candidate_positions = {self.passage_positions[id(item)] for item in candidates}
            full_coverage = Counter(position for word in words
                                    for position in self._matching_passages(word, candidate_positions))
            strongest_coverage = max(full_coverage.values(), default=0)
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
            if full_coverage is not None and full_coverage[position] < strongest_coverage:
                continue
            title_hits = sum(any(affinity(word, token) for token in item.title_words) for word in words)
            if not scoped and not named and title_hits < 2:
                # Название раздела может быть общим («Действия при ошибке»),
                # а пользователь подробно описывает случай в первой фразе.
                # Допускаем такое правило только при совпадении самого условия
                # и существенной части запроса, не по одному общему глаголу.
                own_rules = rules(item.text, self._sentences, facet, supplies_facet)
                vocabulary = set(item.words) | set(item.title_words)
                coverage = sum(any(affinity(word, token) for token in vocabulary) for word in words)
                observed = any(atomic_observation(atom, question, terms)[0] for rule in own_rules
                               if rule.condition for atom in atoms(rule.condition))
                if not observed or not words or coverage / len(words) < .65 or max((body_score(rule.body) for rule in own_rules), default=0) == 0:
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
            def coverage(row):
                vocabulary=set(row[1].words)|set(row[1].title_words)
                return sum(any(affinity(word,token) for token in vocabulary) for word in words)
            best_coverage=max(coverage(row) for row in matches)
            matches=[row for row in matches if coverage(row)==best_coverage]
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
        technical_contexts=[context.get('technical_focus') for context in contexts if context.get('technical_focus')]
        if technical_contexts and all(value['source']==technical_contexts[0]['source'] and value['property']==technical_contexts[0]['property'] for value in technical_contexts):
            combined={**technical_contexts[0],'subjects':list(dict.fromkeys(subject for value in technical_contexts for subject in value['subjects']))}
            if len(combined['subjects'])<=4:self.sessions[scope]['technical_focus']=combined
        if len(self.sessions) > 1000:
            del self.sessions[min(self.sessions, key=lambda key: self.sessions[key]["time"])]
        return {"status": ('partial' if missing or clarifications else 'answer') if answered else 'clarify', "text": "\n".join(texts), "links": links,
                "evidence": evidence, "context_used": any(result.get("context_used") for result in results),
                "timing_ms": round((time.perf_counter() - started) * 1000, 2)}

    def _search_single(self, question: str, principal: Principal, chat_id: str = "default", selected_source: str | None = None, current_revisions: dict | None = None, session_store: dict | None = None) -> dict:
        sessions = self.sessions if session_store is None else session_store
        scope = (principal.user_id, principal.role, tuple(sorted(principal.groups)), chat_id)
        previous = sessions.get(scope)
        previous_day = (previous or {}).get('as_of')
        result = self._search_impl(question, principal, chat_id, selected_source, current_revisions, session_store)
        document_focus=result.pop('_document_focus',None)
        resolved_question=result.pop('_resolved_question',question)
        result=concise_result(resolved_question,result,self.passages)
        if result.get('status')=='answer' and scope in sessions:
            focus=remember_focus(resolved_question,result,self.passages)
            if focus:sessions[scope]['technical_focus']=focus
        if (result['status']=='not_found' and not document_focus and previous
                and sessions.get(scope) is previous and document_continuation(question)
                and not explicit_names(lookup_question(question))):
            # Не найденная характеристика известного документа не означает
            # переход к другому документу. _search_impl уже проверил права,
            # поколение, срок памяти и редакции; при их смене previous удалён.
            # Переносим только единственный предмет, без прежних поисковых слов.
            ids=previous.get('focus_ids',previous['ids'])
            if len(ids)==1 and self.sources[ids[0]].get('kind')=='document':
                document_focus={'source':ids[0],'revision':self.sources[ids[0]]['revision'],'topic_terms':[]}
        if result['status'] in {'not_found','unavailable','conflict','processing'} or (result['status']=='clarify' and sessions.get(scope) is previous and not result.get('context_used')):
            sessions.pop(scope,None)
        if document_focus and result['status']!='processing':
            # Даже неотвеченный вопрос может явно установить предмет разговора.
            # Сохраняем только проверенные id/редакцию, не произвольный текст.
            if scope not in sessions:self._remember(sessions,scope,[document_focus['source']],[])
            sessions[scope]['document_focus']=document_focus
        if result['status'] in {'answer', 'partial'}:
            result=self._linked_evidence(result)
            if result['status']=='unavailable':
                sessions.pop(scope,None)
        if scope in sessions:
            names=explicit_names(lookup_question(question))
            vocabulary={token for item in self.passages if item.source['id'] in sessions[scope]['ids'] for token in item.title_words}
            subjects=[name for name in names if set(terms(name)).issubset(vocabulary)]
            sessions[scope]['subjects']=subjects or ((previous or {}).get('subjects',[]) if result.get('context_used') else [])
        clean, day, invalid = question_day(question, self.clock())
        if not invalid and scope in sessions and result['status'] in {'answer', 'partial', 'clarify'}:
            if clean != question.strip():
                sessions[scope]['as_of'] = day
            elif result.get('context_used') and previous_day and not re.search(r'\bсегодня\b', question, re.I):
                sessions[scope]['as_of'] = previous_day
        return result

    def _linked_evidence(self,result):
        """Цитата указывает на файл, где она написана, а не на файл со ссылкой."""
        evidence=[]
        links=[]
        for item in result['evidence']:
            passage=next((part for part in self.passages if part.source['id']==item['id'] and part.heading==item['heading']
                          and not (part.structure or {}).get('document_guard') and (part.structure or {}).get('document_origins')),None)
            if passage is None:
                evidence.append(item)
                source=self.sources[item['id']]
                catalog_heading = any(row['heading'] == item['heading'] for row in source.get('catalog_items', []))
                link={'title':item['heading'] if catalog_heading else source['title'],'url':source['url']}
                if link not in links:links.append(link)
                continue
            mapped=[]
            covered=set()
            for origin in passage.structure['document_origins']:
                quotes=[sentence for sentence in self._sentences(item['quote']) if sentence in origin['text']]
                if not quotes:continue
                covered.update(quotes)
                mapped.append({'id':origin['id'],'revision':origin['revision'],'heading':origin['heading'],'quote':' '.join(quotes),
                               'via':{'id':item['id'],'revision':item['revision'],'heading':item['heading']}})
            if not mapped or covered!=set(self._sentences(item['quote'])):
                return {'status':'unavailable','text':ISSUE_TEXT,'links':[],'evidence':[]}
            evidence.extend(mapped)
            for identifier in dict.fromkeys([item['id']]+[entry['id'] for entry in mapped]):
                source=self.sources[identifier]
                link={'title':source['title'],'url':source['url']}
                if link not in links:links.append(link)
        # Меняем только ссылки с подтверждённой цитатой. Переходы к документам
        # с отсылками сохраняем, если у них нет собственного точного фрагмента.
        focused = []
        replaced = set()
        for item in evidence:
            exact = evidence_links(self, item)
            if exact:
                replaced.add(self.sources[item['id']]['url'])
                for link in exact:
                    if link not in focused:
                        focused.append(link)
        focused.extend(link for link in links if link['url'] not in replaced)
        return {**result,'evidence':evidence,'links':focused}

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
        if selected_source and session and session.get('focus_ids',session['ids']) != [selected_source]:
            session = None
            sessions.pop(scope, None)
        document_target=explicit_document(question,{key:self.sources[key] for key in allowed},
                                          [p for p in self.passages if p.source['id'] in allowed],terms)
        saved_document=(session or {}).get('document_focus')
        inherited_document=bool(not document_target and saved_document and document_continuation(question)
                                and not explicit_names(lookup_question(question)))
        if inherited_document:document_target=saved_document['source']
        if document_target and (not selected_source or selected_source==document_target):
            # Внутренний вызов использует выбранный самим поиском источник.
            # Рекурсии нет: явный selected_source уже ограничивает этот вызов.
            if not selected_source:
                result=self._search_impl(question,principal,chat_id,document_target,current_revisions,session_store)
                vocabulary=set(terms(' '.join(item.get('quote','') for item in result.get('evidence',[]))))
                omitted=set(terms(self.sources[document_target]['title']+' срок стоимость сколько какой выдать выдаётся выдача можно должен нужно использование'))
                topic=[word for word in terms(question) if word in vocabulary and word not in omitted and not word.isdigit()][:12]
                result['_document_focus']={'source':document_target,'revision':self.sources[document_target]['revision'],
                                           'topic_terms':topic or ((saved_document or {}).get('topic_terms',[]) if inherited_document else [])}
                if inherited_document:result['context_used']=True
                return result
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
                elif session and len(session.get('focus_ids',session['ids'])) == 1:
                    target = session.get('focus_ids',session['ids'])[0]
            if not target:
                return {'status': 'clarify', 'text': 'Выберите документ, который нужно кратко пересказать.', 'links': [], 'evidence': []}
            return self._summarize(target, scope, sessions, started, day, allowed)
        # Наличие товарного каталога на сайте не должно перехватывать
        # сравнение устройств, однозначно найденных в техническом документе.
        technical_route=None
        if not selected_source:
            technical_route=unique_technical_source(lookup_question(question),[
                item for item in self.passages if item.source['id'] in allowed
                and active(item.structure or {},day) and self._usable_passage(item,allowed,day)])
        catalog = None if technical_route else search_catalog(self, question, allowed, selected_source, day, session, sessions, scope)
        if catalog is not None:
            return catalog
        technical_focus=validated_focus((session or {}).get('technical_focus'),self.sources,self.passages)
        followup=resolve_followup(question,technical_focus)
        if followup and followup.get('clarify'):
            # Этот механизм касается только технических PDF; остальные чаты
            # продолжают использовать прежнюю проверку условий и предметов.
            technical_scope=technical_focus or (selected_source and any(p.source['id']==selected_source and (p.structure or {}).get('local_issue')
                                                                       and not (p.structure or {}).get('rule_sections') for p in self.passages))
            if technical_scope:return {'status':'clarify','text':followup['clarify'],'links':[],'evidence':[],'context_used':bool(technical_focus)}
            followup=None
        focused_question = lookup_question(followup['question'] if followup else question)
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
        local_facts, local_focus = question_context(question)
        # «Смена закрыта, после этого нашли ошибку. Кто исправляет?» содержит
        # собственное описание. «Этого» здесь не ссылается на прошлый чат.
        reference_text = local_focus if local_facts else question
        reference = bool(re.search(r"\b(это|этого|этой|там|тогда|него|нее|ней|этом|он|она|они)\b", normal(reference_text))) or (normal(question).startswith("а ") and len(raw_words) <= 3)
        generic = not raw_words or all(word in FOLLOWUP or word in {'выбрать','добавить','режим'} for word in raw_words)
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
        topic_followup=False
        requested_operations=operation_names(focused_question)
        if session and reference and not explicit_subject and len(session.get('subjects',[]))==1 and requested_operations:
            previous_operations=set().union(*(operation_names(item.text) for item in self.passages if item.source['id'] in session.get('focus_ids',session['ids'])))
            if not previous_operations & requested_operations:
                # «Кто принимает Танзанит?» → «А сколько он хранится?»
                # переносит предмет, но ищет новую операцию во всех доступных файлах.
                focused_question=focused_question.rstrip(' ?. ')+' '+session['subjects'][0]+'?'
                current_words=self._query_terms(focused_question)[:32]
                query_normal=normal(focused_question)
                topic_followup=context_used=True
        if context_used and not topic_followup and not condition_reply and not generic and not re.search(r"\b(это|этого|этой|там|тогда|него|нее|ней|этом|он|она|они)\b", normal(question)):
            vocabulary = set().union(*(set(passage.words) | set(passage.title_words) for passage in self.passages if passage.source["id"] in session["ids"]))
            # Новая самостоятельная тема после «а» не должна наследовать старый ответ.
            context_used = all(word in {stem(item) for item in FOLLOWUP} or any(affinity(word, token) for token in vocabulary) for word in current_words)
        if context_used and session.get('as_of') and not explicit_day and not re.search(r'\bсегодня\b', question, re.I):
            day = session['as_of']
        if (reference or generic) and not explicit_subject and not session and not selected_source:
            return {"status": "clarify", "text": "Уточните, о каком материале или вопросе идёт речь.", "links": [], "evidence": []}
        focus_ids=(session or {}).get('focus_ids',(session or {}).get('ids',[]))
        if context_used and generic and len(focus_ids) > 1 and not selected_source:
            return {"status": "clarify", "text": "Уточните, о каком из упомянутых материалов идёт речь.", "links": [], "evidence": []}
        words = list(dict.fromkeys(current_words + (session["terms"] if context_used and not topic_followup and not saved_document else [])))
        named = [term for name in explicit_names(focused_question) if name.casefold() not in STOP for term in terms(name)][:16]
        if re.match(r'^а\s+(?:если|при)\b',normal(question)) and not named and not context_used and not selected_source:
            return {'status':'clarify','text':'Уточните, к какому материалу или правилу относится это условие.','links':[],'evidence':[]}
        numbers = re.findall(r"\d+(?:[.,]\d+)*", focused_question)[:16]
        candidates = [item for item in self.passages if item.source["id"] in allowed and (not selected_source or item.source["id"] == selected_source)
                      and active(item.structure or {}, day) and self._usable_passage(item,allowed,day)]
        scoped_rules=bool(selected_source and candidates and all((p.structure or {}).get('rule_sections') for p in candidates))
        if scoped_rules:
            # Имя документа выбирает область поиска, но не делает каждый его
            # абзац релевантным. Ранжируем предмет вопроса по телам правил.
            identity=set(terms(self.sources[selected_source]['title']))
            semantic_question=focused_question
            for pattern,replacement in ((r'\bвес(?:ит|ить|ом)?\b','масса'),(r'срок\s+службы','срок эксплуатации'),
                                        (r'подстро\w*','подстройка'),(r'потеря\w*|утрат\w*','утери'),
                                        (r'мегабит\w*','Мбит'),(r'температур\w*','температура')):
                semantic_question=re.sub(pattern,replacement,semantic_question,flags=re.I)
            current_words=[word for word in self._query_terms(semantic_question) if word not in identity]
            if saved_document and not re.match(r'^(?:теперь|отложим)\b',focused_question,re.I) and re.search(r'\bего\b|\bеё\b|\bее\b|^а для\b',focused_question,re.I):
                current_words=list(dict.fromkeys(current_words+saved_document.get('topic_terms',[])))
            words=list(current_words)
            named=[]
            numbers=[]  # Число в условии может сравниваться с границей, а не совпадать с ней.
            if re.search(r'серийн\w*\s+номер|номер\w*\s+(?:у\s+)?моего|MAC.адрес.*моего',question,re.I):
                # Номер раздела или пояснение «где найти номер» не являются
                # конкретным реквизитом. Для ответа нужно поле с самим значением.
                actual=re.compile(r'(?:серийн\w*\s+номер|номер\s+билета|MAC.адрес)\s*[:—=]\s*[\w-]*\d',re.I)
                candidates=[p for p in candidates if actual.search(p.text)]
            codes=re.findall(r'\b[А-ЯA-Z\d]+(?:-[А-ЯA-Z\d]+)+\b',focused_question)
            if len(codes)==1 and codes[0][-1].isdigit():
                code=codes[0].casefold()
                candidates=[p for p in candidates if not re.search(r'\b[А-ЯA-Z\d]+(?:-[А-ЯA-Z\d]+)+\b',p.text)
                            or code in p.text.casefold()]
            action=requested_action(question)
            if action:
                candidates=[p for p in candidates if action.search(p.text)]
            properties=[pattern for trigger,pattern in (
                (r'мощност',r'мощност'), (r'частот',r'частот|\bГц\b'), (r'напряжени',r'напряжени|\d\s*В[~\s,]'),
                (r'\bток\b',r'\bток\w*'), (r'температур',r'температур|темп[.]'),
                (r'влажност',r'влажност|влаж[.]'), (r'масс|\bвес(?:ит|ить|ом)?\b',r'масс|вес'),
                (r'гаранти',r'гаранти'), (r'срок.*\bслуж',r'срок.*(?:\bслуж|эксплуатац)'),
                (r'\bкпд\b',r'\bкпд\b')) if re.search(trigger,focused_question,re.I)]
            if properties:
                candidates=[p for p in candidates if all(re.search(pattern,p.text,re.I) for pattern in properties)]
                for request,evidence in ((r'подстро',r'подстро'),(r'предел|диапазон',r'диапазон|…|\.\.\.|от .* до'),
                                         (r'постоянн',r'постоянн'),(r'переменн',r'переменн'),
                                         (r'хран|перевоз|транспорт',r'хран|транспорт'),
                                         (r'номинальн',r'номинальн')):
                    if re.search(request,focused_question,re.I):
                        candidates=[p for p in candidates if re.search(evidence,p.text,re.I)]
                if re.search(r'температур|влажност',focused_question,re.I) and re.search(r'работ|эксплуат',focused_question,re.I):
                    candidates=[p for p in candidates if re.search(r'раб\b|рабоч|эксплуатац',p.text,re.I)]
                # Вопрос «какое напряжение» не задаёт вход/выход, номинал или диапазон.
                # Уточнение лучше произвольного выбора одной из разных строк.
                qualifier=set(terms('номинальный выходной входной диапазон предел переменный постоянный подстройка'))
                if re.search(r'напряжени|\bток\b',focused_question,re.I) and not qualifier.intersection(current_words):
                    if len({p.heading for p in candidates})>1:
                        return {'status':'clarify','text':'Уточните, какой параметр нужен: входной или выходной, номинальное значение или диапазон.', 'links':[],'evidence':[]}
        technical_source=followup['source'] if followup else selected_source or technical_route
        technical_candidates=[item for item in candidates if item.source['id']==technical_source] if technical_source else []
        technical = search_technical(focused_question, technical_candidates, terms) if technical_source and (followup or not context_used) else None
        if technical is not None:
            if technical['status'] == 'answer':
                self._remember(sessions, scope, [technical_source], current_words)
            if followup:
                technical['context_used']=True
                technical['_resolved_question']=focused_question
            technical['timing_ms'] = round((time.perf_counter() - started) * 1000, 2)
            return technical
        if context_used and not topic_followup:
            candidates = [item for item in candidates if item.source['id'] in focus_ids]
            if session.get('catalog_keys'):
                # Неизвестная характеристика не должна находиться у соседнего
                # товара в том же файле. Новый явно названный предмет сюда не входит.
                product_sections = {(product.source['id'], product.heading) for product in self.catalog_products
                                    if product.key in session['catalog_keys']}
                candidates = [item for item in candidates if (item.source['id'], item.heading) in product_sections]
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
            if (item.structure or {}).get('local_issue'):
                # Геометрически отделённые страницы/таблицы оцениваем по
                # релевантности ниже. Они не становятся доказательством ответа.
                continue
            own_operations=self.passage_operations[self.passage_positions[id(item)]]
            if not selected_source and requested_operations and own_operations and not own_operations & requested_operations:
                # Неисправная ссылка о хранении не запрещает независимый
                # ответ о доставке того же товара.
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
        if context_used and not topic_followup:
            candidates = [item for item in candidates if item.source["id"] in focus_ids]
            if re.search(r"срок|сколько.*дн|как долго|сколько.*хран", query_normal):
                candidates = [item for item in candidates if re.search(r"\b(?:день|дня|дней|дни|час\w*|минут\w*|недел\w*|месяц\w*|суток|срок\w*|год\w*|лет)\b", normal(item.text))]
        # Обзор объясняет весь порядок вместе с условиями. Он не требует
        # выяснять частные обстоятельства пользователя, как конкретный случай.
        conditional = None if want_overview or scoped_rules else self._conditional_answer(question, candidates, current_words, named, facet, scope, sessions,
                                                session if context_used else None, bool(selected_source or (context_used and not topic_followup)), started)
        if conditional is not None:
            return conditional
        if not words or not candidates:
            incomplete = any(not self.sources[key].get("complete", True) for key in allowed)
            return {"status": "unavailable" if incomplete else "not_found", "text": "Материал пока не удалось обработать полностью. Обратитесь к администратору." if incomplete else NO_DATA, "links": [], "evidence": []}
        average = sum(sum(item.words.values()) for item in candidates) / len(candidates) or 1
        # IDF считается только по доступным фрагментам, без влияния чужих документов.
        positions = {self.passage_positions[id(item)] for item in candidates}
        matches = {word: self._matching_passages(word, positions, body_only=scoped_rules) for word in set(words + named)}
        if scoped_rules:
            matches=body_matches(set(words+named),positions,self._matching_passages,terms)
        df = {word: len(matches[word]) for word in words}
        covered_positions = Counter(position for word in words for position in matches[word])
        minimum = (0.10 if properties else 0.30) if scoped_rules else (0.65 if len(words) <= 3 else 0.45)
        eligible = {position for position, covered in covered_positions.items() if covered / len(words) >= minimum}
        weights=question_weights(words,terms) if scoped_rules else {word:1.0 for word in words}
        weighted_coverage=supported_positions(words,matches,weights,minimum) if scoped_rules else {}
        if scoped_rules:
            eligible=set(weighted_coverage)
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
                title_bonus=min(0.15 if scoped_rules else 1.1, title_frequency * 0.55)
                score += idf * similarity * (bm25 + title_bonus) * weights[word]
            coverage = weighted_coverage[position] if scoped_rules else covered / len(words)
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
        if scoped_rules:
            # Совпадение лишнего общего слова не отменяет первое место по
            # предметному весу. Близкие по весу варианты сохраняем для проверки
            # противоречий; авторитетность не обходит эту проверку.
            ranked = [row for row in ranked if row[0] >= ranked[0][0] * 0.70]
        else:
            ranked = [row for row in ranked if row[1] >= best_coverage - 0.12]
        if any((row[2].structure or {}).get('issue') and row[0] >= ranked[0][0] * .98 for row in ranked):
            return {'status': 'unavailable', 'text': ISSUE_TEXT, 'links': [], 'evidence': []}
        ranked = [row for row in ranked if not (row[2].structure or {}).get('issue')]
        if not ranked:
            return {'status': 'unavailable', 'text': ISSUE_TEXT, 'links': [], 'evidence': []}
        # Сначала проверяем противоречия, включая менее авторитетный источник.
        # Официальная отметка не разрешает молча выбрать одну из разных норм.
        relevant_sentences = lambda text: [sentence for sentence in self._sentences(text) if supplies_facet(sentence, facet)]
        if conflicting_passages([row[2] for row in ranked], relevant_sentences, terms):
            return {"status": "conflict", "text": CONFLICT, "links": [], "evidence": []}
        highest_authority = max(authority(row[2].source) for row in ranked)
        ranked = [row for row in ranked if authority(row[2].source) == highest_authority]
        best_score, _, best = ranked[0]
        if scoped_rules and guarded(best.text) and not want_overview and re.search(r'\bесли\b|можно|разреш|действует ли|нужно ли|допуска',question,re.I):
            conditional=self._conditional_answer(question,[best],current_words,[],facet,scope,sessions,
                                                 session if context_used else None,True,started)
            if conditional is not None:
                return complete_conditional_quote(best.text,conditional,
                    rules(best.text,self._sentences,facet,supplies_facet))
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
        if hypothetical_amount or query_normal.startswith('почему') or facet == 'packaging':
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
            premise_words=set(terms(local_facts)) if local_facts and focused_question==question else set()
            supported_premise=bool(premise_words and any(
                affinity(word,token) for word in premise_words for token in terms(ordered[0][1])))
            for pair in ordered[1:]:
                extra = {word for word in current_words if any(affinity(word, token) for token in terms(pair[1]))} - covered
                dependent = pair[0] == ordered[0][0] + 1 and bool(re.match(r"(?:Если|При этом|В этом случае|Именно поэтому|Для этого|Исключение|Кроме|Однако|За исключением|Его|Её|Ее|Их|Они)\b", pair[1]))
                same_subject=not supported_premise or any(
                    affinity(word,token) for word in premise_words for token in terms(pair[1]))
                # Дополнительное совпадение глагола «защитить» не разрешает
                # добавить правило о другом предмете из того же документа.
                # Связанное условие или местоименное продолжение сохраняется.
                if (extra and same_subject) or dependent:
                    selected.append(pair)
                    break
        selected.sort()
        answer = " ".join(sentence for _, sentence in selected)
        information_facet=facet in {'cleaning_agents','forbidden_cleaning_agents'}
        protected_information=guarded(best.text) or bool(re.search(r'\b(?:кроме|однако|в иных случаях)\b',best.text,re.I))
        if information_facet and protected_information:
            # Внешний вид ответа не может отрезать оговорку, даже когда она
            # написана отдельно от перечня средств и не повторяет его слова.
            if len(best.text)>1800:
                return {'status':'clarify','text':'Уточните пункт: в этом разделе несколько связанных условий.', 'links':[],'evidence':[]}
            answer=best.text
        if (best.structure or {}).get('rule_sections'):
            # Раздел уже выделен геометрией/нумерацией. Повторное сокращение
            # до предложения потеряло бы «запрещается», исключение или шаг 1.
            if len(best.text)>1800:
                return {'status':'clarify','text':'Уточните пункт: в этом разделе несколько связанных условий.', 'links':[],'evidence':[]}
            if not information_facet or protected_information:
                answer=best.text
        quote = answer
        if information_facet and not protected_information:
            # Убираем заголовок и распознанный знак предупреждения только
            # из отображения. Дословная исходная цитата остаётся в evidence.
            answer=re.sub(r'^.{0,120}?ПРЕДУПРЕЖДЕНИЕ:\s*','',answer)
            if 'A ПРЕДУПРЕЖДЕНИЕ:' in best.text:
                answer=re.sub(r'(\b(?:используйте|применяйте))\s+A\s+(?=[а-я])',r'\1 ',answer,flags=re.I)
        if re.match(r"како[йеяие]+\s", query_normal) and len(best.source["title"].split()) <= 4 and not set(terms(best.source["title"])) & set(terms(question)):
            answer = best.source["title"] + ". " + answer
        links = [{"title": best.source["title"], "url": best.source["url"]}]
        evidence = [{"id": best.source["id"], "revision": best.source["revision"], "heading": best.heading, "quote": quote}]
        self._remember(sessions,scope,[best.source['id']],self._query_terms(question)[:20] if not context_used else words[:24],facet=facet)
        # Ограничиваем производную память; она не становится корпусом для обучения.
        if len(sessions) > 1000:
            oldest = min(sessions, key=lambda key: sessions[key]["time"])
            del sessions[oldest]
        return {"status": "answer", "text": answer, "links": links, "evidence": evidence, "context_used": context_used,
                "timing_ms": round((time.perf_counter() - started) * 1000, 2)}

````

## ai_core/site_search_language.py

Полный путь: C:\Users\KROLL\Desktop\Ai\ai_core\site_search_language.py.

SHA-256: `9104461eb0a3a3417a0d132f194415e388152c11cd7cad395ec174dedca68c40`.

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
    (r"\bсовсем\s+(?:немного|мало)\b", "минимальный"),
    (r"\bпоздоровал\w*\s+за\s+руку\b", "рукопожатие"),
)

SYNONYMS = {
    "наличные": ["купюра", "купюры", "банкнота", "наличные"],
    "мыть": ["мыть", "вымыть", "помыть", "мытьё"],
    "улица": ["улица", "снаружи"],
    "помещение": ["помещение", "внутри"],
    "взвешивание": ["взвешивание", "взвесить", "взвешивать", "отвесить", "вразвес", "весовой"],
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


def cleaning_agent_statement(text: str, forbidden: bool) -> bool:
    """Средство связано с глаголом применения, а не с «до/после очистки».

    Ищем структуру фразы без перечня конкретных веществ из документов.
    Запрет и отсутствие запрета различаются; исходная цитата не меняется.
    """
    clean=r'(?:чистк\w*|очистк\w*|очищ\w*)'
    predicate=r'(?:использ(?:у|ова)\w*|примен\w*)'
    for original in re.split(r'(?<=[.!?])\s+(?=[А-ЯA-Z])',text):
        sentence=original.casefold().replace('ё','е')
        means=(re.search(predicate+r'[^.!?]{1,400}(?:для|при)\s+'+clean,sentence)
               or re.search(r'(?:для|при)\s+'+clean+r'[^.!?]{0,90}'+predicate+r'\s+\S+',sentence)
               or re.search(r'(?:чистите|очищайте|чистить|очищать)\s+[^.!?]{1,160}(?:средств\w*|\w+(?:кой|ками|ткой|ством|ствами))\b',sentence))
        if not means:continue
        not_banned=bool(re.search(r'не\s+(?:запрещ\w*|возбраня\w*)',sentence))
        banned=not not_banned and bool(re.search(
            r'не\s+(?:использ(?:у|ова)\w*|примен\w*|чист\w*|очища\w*)|'
            r'(?:нельзя|запрещ\w*)[^.!?]{0,100}(?:использ\w*|примен\w*|чист\w*|очища\w*)',sentence))
        if banned==forbidden:return True
    return False


def requested_facet(question: str) -> str | None:
    value = question.casefold()
    if re.search(r'чист\w*|очищ\w*|очист\w*',value) and re.search(r'\bчем\b|каки\w*\s+(?:чистящ\w*\s+)?средств\w*',value):
        forbidden=not re.search(r'не\s+запрещ\w*',value) and re.search(r'запрещ\w*|нельзя|не\s+(?:разреш\w*|допуска\w*)',value)
        return 'forbidden_cleaning_agents' if forbidden else 'cleaning_agents'
    if re.search(r'в каких\s+(?:пакет|упаков)|какую\s+упаков|вариант\w*\s+(?:пакет|упаков)', value):
        return 'packaging'
    if re.search(r"сколько\s+(?:(?:сейчас|сегодня|примерно|будет)\s+)*(?:стоит|стоить)|стоимость|цена|почём|за какую сумму", value):
        return 'cost'
    if re.search(r"(?:с какого|в каком) год|год (?:начала|основания)", value):
        return 'year'
    if re.search(r"\bкому\b|кто (?:(?:его|её|ее|их) )?(?:выда|принима|обраб|отвечает|исправ|оформ)", value):
        return 'recipient'
    if re.search(r"как долго|какой срок|сколько.*(?:ждать|дн|хран|длит)|подожд[её]т.*(?:день|дня|недел)", value):
        return 'duration'
    if re.search(r"как (?:отмен|оформ|направ|отправ|перед|получ)", value):
        return 'procedure'
    return None


def supplies_facet(text: str, facet: str | None) -> bool:
    value = text.casefold()
    if facet in {'cleaning_agents','forbidden_cleaning_agents'}:
        return cleaning_agent_statement(text,facet=='forbidden_cleaning_agents')
    if facet == 'packaging':
        return bool(re.search(r'пакет\w*|упаков\w*|банка|банку|коробк\w*', value))
    if facet == 'cost':
        return bool(re.search(r"\bруб\w*|₽|цен\w*|стоимост\w*|бесплатн\w*", value)) and not bool(re.search(r"не (?:указан|определ[её]н|извест|установлен)|нет (?:сведений|информации)", value))
    if facet == 'year':
        return bool(re.search(r"\b(?:19|20)\d{2}\b", text))
    if facet == 'recipient':
        return bool(re.search(r"координатор|диспетчер|старш\w* сотрудник|старш\w* смен|администратор|дежурн|менеджер|руководител|кладовщик|оператор|продавец|продавц|библиотекар", value))
    if facet == 'duration':
        return bool(re.search(r"\b(?:день|дня|дней|дни|суток|час\w*|минут\w*|недел\w*|месяц\w*|год\w*|лет)\b", value)) and bool(re.search(r"\d|\b(?:" + "|".join(NUMBERS) + r")\b", value.replace('ё', 'е')))
    if facet == 'procedure':
        return bool(re.search(r"отправ\w*|переда\w*|заполн\w*|запис\w*|показыв\w*|покаж\w*|сообщ\w*|подат\w*|звон\w*|обратит\w*", value))
    return True

````

## ai_core/site_search_reasoning.py

Полный путь: C:\Users\KROLL\Desktop\Ai\ai_core\site_search_reasoning.py.

SHA-256: `36cf88b371e7c05d23df7dce5c9239b8f4b5c9243d67b73f61de53f76c5c34ff`.

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
    # Местоимение в текущем сообщении ссылается и на свойства предмета:
    # «Везу стеклянный датчик. Как его защитить?». Нельзя оставить один
    # глагол и потерять материал/состояние. Явный другой предмет сильнее.
    if facts and not explicit_names(focus) and re.search(r'\b(?:его|е[её]|их|он|она|они|это|этого|этой)\b',focus,re.I):
        return text
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
    # Вводная описывает предмет вопроса: «Ошибка в чеке. Кто её исправляет?».
    # Общая просьба о действии или ответственном не становится новой темой.
    # Явное название другого предмета по-прежнему имеет приоритет.
    if facts and not explicit_names(focus) and re.fullmatch(
            r'(?:кто\s+(?:(?:е[её]|его|их|это)\s+)?(?:исправ\w*|оформ\w*|делает|отвечает)(?:\s+(?:исправление|это))?'
            r'|что\s+(?:делать|сделать)(?:\s+(?:сотруднику|продавцу|мне|нам))?)\s*[?.!]*', focus, re.I):
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

## tests/test_site_search_information.py

Полный путь: C:\Users\KROLL\Desktop\Ai\tests\test_site_search_information.py.

SHA-256: `87aa3b2819637443e46fe2151bf22c5615d27f73f7624d114dae640c37a97299`.

````python
"""Тип запрошенных сведений и свойства предмета; вымышленные материалы."""
import unittest
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_reasoning import lookup_question

READER=Principal('reader','staff')

def document(blocks,identifier='document:manual',profile=True,users=None):
    sections=[{'heading':heading,'text':body} for heading,body in blocks]
    if profile:
        sections=[{'heading':'','text':'[PDF_LOCAL_SECTIONS]\n[PDF_RULE_SECTIONS]\n'+
                   '\n\n'.join('# '+heading+'\n'+body for heading,body in blocks)}]
    return {'id':identifier,'revision':'r1','title':'Руководство МН-8','kind':'document',
            'url':'http://localhost/'+identifier.split(':')[-1],
            'access':{'scope':'restricted','users':users or ['reader']},'sections':sections}

def engine(*records):
    index=ClosedSiteIndex(['http://localhost']);index.publish(list(records),index.begin('new'))
    return index

class InformationTests(unittest.TestCase):
    def test_agents_are_not_preparation_steps(self):
        index=engine(document([
            ('Подготовка','Перед очисткой отключите аппарат. После очистки высушите корпус.'),
            ('Средства','Не применяйте раствор аммиака или порошок для чистки корпуса.')]))
        answer=index.search('Чем запрещено чистить МН-8?',READER)
        self.assertEqual(answer['status'],'answer')
        self.assertIn('аммиака',answer['text']);self.assertIn('порошок',answer['text'])
        self.assertNotIn('отключите',answer['text'])

    def test_allowed_and_prohibited_agents_are_distinct(self):
        index=engine(document([
            ('Средства','Для очистки используйте пену. Не используйте масло для чистки корпуса.')]))
        allowed=index.search('Чем можно чистить МН-8?',READER,'allowed')
        banned=index.search('Чем нельзя чистить МН-8?',READER,'banned')
        self.assertEqual(allowed['status'],'answer');self.assertIn('пену',allowed['text'])
        self.assertNotIn('масло',allowed['text']);self.assertIn('масло',banned['text'])
        self.assertNotIn('пену',banned['text'])

    def test_not_forbidden_is_not_a_ban(self):
        index=engine(document([
            ('Средства','Для очистки не запрещено использовать пену.'),
            ('Запрет','Для очистки запрещено использовать масло.')]))
        answer=index.search('Чем не запрещено чистить МН-8?',READER)
        self.assertEqual(answer['status'],'answer');self.assertIn('пену',answer['text'])
        self.assertNotIn('масло',answer['text'])

    def test_no_agent_fact_is_not_replaced_with_preparation(self):
        index=engine(document([('Подготовка','Перед очисткой отключите аппарат.')]))
        answer=index.search('Чем нельзя чистить МН-8?',READER)
        self.assertNotEqual(answer['status'],'answer')

    def test_exception_and_condition_are_preserved(self):
        for tail in ['Исключение: на резиновых деталях раствор не применяют.',
                     'Однако на резиновых деталях раствор не применяют.']:
            with self.subTest(tail=tail):
                index=engine(document([('Средства','Используйте раствор для очистки корпуса. '+tail)]))
                answer=index.search('Чем чистить МН-8?',READER)
                self.assertEqual(answer['status'],'answer');self.assertIn(tail,answer['text'])

    def test_requested_type_does_not_leak_to_next_question(self):
        index=engine(document([
            ('Средства','Не применяйте масло для очистки корпуса.'),
            ('Погружение','Не погружайте аппарат в воду.')]))
        index.search('Чем запрещено чистить МН-8?',READER,'one')
        answer=index.search('А погружать его в воду можно?',READER,'one')
        self.assertEqual(answer['status'],'answer');self.assertIn('Не погружайте',answer['text'])
        self.assertNotIn('масло',answer['text'])

    def test_local_pronoun_preserves_object_description(self):
        text='Везу стеклянный измерительный датчик. Как его защитить?'
        self.assertEqual(lookup_question(text),text)

    def test_explicit_new_object_does_not_inherit_description(self):
        self.assertNotIn('стеклянная',lookup_question('Колба Омега стеклянная. Как защитить Дельту?'))

    def test_packing_uses_object_features_in_same_message(self):
        index=engine(document([('Упаковка',
            'Стеклянные измерительные датчики помещают в жёсткий контейнер с мягкими вставками. '
            'Ткань защищают от влаги с помощью непромокаемого чехла.')],profile=False))
        answer=index.search('Везу стеклянный измерительный датчик. Как его защитить?',READER,'one','document:manual')
        self.assertEqual(answer['status'],'answer');self.assertIn('мягкими вставками',answer['text'])
        self.assertNotIn('Ткань защищают',answer['text'])

    def test_hidden_agents_are_not_retrieved(self):
        index=engine(document([('Подготовка','Перед очисткой отключите аппарат.')]),
                     document([('Средства','Не используйте масло для чистки корпуса.')],
                              'document:hidden',users=['owner']))
        answer=index.search('Чем нельзя чистить МН-8?',READER)
        self.assertNotEqual(answer['status'],'answer');self.assertFalse(answer['evidence'])

    def test_visible_text_cleaned_but_exact_evidence_retained(self):
        original=('Очистка ПРЕДУПРЕЖДЕНИЕ: Не используйте A масло для чистки корпуса. '
                  'A ПРЕДУПРЕЖДЕНИЕ: Не погружайте аппарат в воду.')
        index=engine(document([('Средства',original)]))
        answer=index.search('Чем запрещено чистить МН-8?',READER)
        self.assertEqual(answer['text'],'Не используйте масло для чистки корпуса.')
        self.assertIn('A масло',answer['evidence'][0]['quote'])

    def test_product_letter_without_warning_legend_is_preserved(self):
        index=engine(document([('Средства','Используйте A средство для чистки корпуса.')]))
        answer=index.search('Чем чистить МН-8?',READER)
        self.assertIn('A средство',answer['text'])

if __name__=='__main__':
    unittest.main()

````

## evaluation/closed_search/information_v9_20261031/run.py

Полный путь: C:\Users\KROLL\Desktop\Ai\evaluation\closed_search\information_v9_20261031\run.py.

SHA-256: `a5bbe64d42567d9c4f9449a9a18afe5d2b1549cd8b3e0e3d852d4fd6afe82ca4`.

````python
"""Повторная регрессия прежних 100 вопросов, отдельный журнал каждого запуска."""
import base64
import hashlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
EXAM=HERE.with_name('holdout_20261031')
sys.path.insert(0,str(ROOT))


def digest(file):return hashlib.sha256(file.read_bytes()).hexdigest()


def main():
    label=sys.argv[1]
    if not label.isalnum():raise ValueError('Invalid run label')
    out=HERE/'results'/label
    out.mkdir(exist_ok=False)
    frozen=json.loads((EXAM/'FREEZE.json').read_text(encoding='utf-8'))
    # Эталоны здесь не читаются: сравнение выполняется после записи всех ответов.
    for name in ['questions.json','sources.json']:
        if digest(EXAM/name)!=frozen['files'][name]:raise ValueError('Changed exam input')
    runtime={str(p.relative_to(ROOT)):digest(p) for p in (ROOT/'ai_core').glob('*.py')}
    (out/'START.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'runtime':runtime,
                                            'frozen_inputs':{name:frozen['files'][name] for name in ['questions.json','sources.json','gold.json']}},indent=2),encoding='utf-8')
    from ai_core import site_documents
    from ai_core.site_search import Principal
    from ai_core.site_search_runtime import DurableSearch
    site_documents.CACHE=HERE/'results'/'document-cache'
    sources=json.loads((EXAM/'sources.json').read_text(encoding='utf-8'))
    questions=json.loads((EXAM/'questions.json').read_text(encoding='utf-8'))
    records=[];extraction=[]
    for source in sources:
        file=EXAM/'sources'/source['file']
        if digest(file)!=source['sha256']:raise ValueError('Changed original')
        start=time.perf_counter()
        def progress(value):
            if value.get('pages_read',0)%20==0:print(source['id'],value,flush=True)
        try:
            text,complete=site_documents.extract_document('data:application/octet-stream;base64,'+base64.b64encode(file.read_bytes()).decode(),source['file'],progress=progress)
        except Exception:
            text,complete='',False
            (out/(source['id']+'-error.txt')).write_text(traceback.format_exc(),encoding='utf-8')
        extraction.append({'id':source['id'],'seconds':time.perf_counter()-start,'complete':complete,'chars':len(text)})
        (out/(source['id']+'-extracted.txt')).write_text(text,encoding='utf-8')
        records.append({'id':'document:'+source['id'],'revision':source['sha256'],'title':source['title'],
                        'kind':'document','authority':'official','url':'http://holdout.invalid/doc/'+source['id'],
                        'access':{'scope':'restricted','users':['exam-reader']},'complete':complete,
                        'sections':[{'heading':source['title'],'text':text}]})
        print('extracted',extraction[-1],flush=True)
    (out/'extraction.json').write_text(json.dumps(extraction,indent=2),encoding='utf-8')
    (out/'records.json').write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')
    engine=DurableSearch(['http://holdout.invalid'],out/'exam.sqlite',extraction_version=site_documents.PARSER_VERSION)
    start=time.perf_counter();engine.publish(records,engine.begin(label))
    (out/'index.json').write_text(json.dumps({'seconds':time.perf_counter()-start,'passages':len(engine.index.passages),
        'per_source':{r['id']:{'count':sum(p.source['id']==r['id'] for p in engine.index.passages),
        'largest':max((len(p.text) for p in engine.index.passages if p.source['id']==r['id']),default=0)} for r in records}},indent=2),encoding='utf-8')
    revisions={r['id']:r['revision'] for r in records}
    with (out/'answers.jsonl').open('x',encoding='utf-8') as output:
        for question in questions:
            start=time.perf_counter()
            try:answer=engine.query(question['question'],Principal('exam-reader','staff'),question['chat'],None,revisions)
            except Exception:answer={'status':'error','text':'','evidence':[],'links':[],'error':traceback.format_exc()}
            row={**question,'result':answer,'milliseconds':round((time.perf_counter()-start)*1000,3)}
            output.write(json.dumps(row,ensure_ascii=False)+'\n');output.flush();os.fsync(output.fileno())
            print(question['id'],answer['status'],flush=True)
    changed=[name for name,value in runtime.items() if digest(ROOT/name)!=value]
    (out/'FINISH.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'changed_during_run':changed,
                                             'answers_sha256':digest(out/'answers.jsonl')},indent=2),encoding='utf-8')
    if changed:raise RuntimeError('Runtime changed during run')


if __name__=='__main__':
    try:main()
    except Exception:traceback.print_exc();sys.exit(1)

````

## evaluation/closed_search/information_v9_20261031/diagnose.py

Полный путь: C:\Users\KROLL\Desktop\Ai\evaluation\closed_search\information_v9_20261031\diagnose.py.

SHA-256: `1ca6be626f3ceba26fb0ff484d2e665a28d9579a95a7ff1ee522604546800805`.

````python
"""Трасса выбора внутри документа; вопросы и история остаются исходными."""
import json
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
from ai_core.site_search import ClosedSiteIndex, Principal

def main():
    records=json.loads((HERE.with_name('ranking_v8_20261031')/'results/candidate2/records.json').read_text(encoding='utf-8'))
    questions=json.loads((HERE.with_name('holdout_20261031')/'questions.json').read_text(encoding='utf-8'))
    wanted={int(n) for n in sys.argv[1:]}
    index=ClosedSiteIndex(['http://holdout.invalid']);index.publish(records,index.begin('diagnostic'))
    source=(ROOT/'ai_core/site_search.py').read_text(encoding='utf-8').splitlines()
    probes=[]
    def observe(frame,event,arg):
        if frame.f_code.co_name!='_search_impl':return None
        if event=='line' and source[frame.f_lineno-1].strip()=='best_coverage = max(row[1] for row in ranked)':
            loc=frame.f_locals
            probes.append({'focused':loc['focused_question'],'words':loc['words'],'facet':loc['facet'],
                'selected_source':loc['selected_source'],'ranked':[
                    {'score':s,'coverage':c,'heading':p.heading,'text':p.text} for s,c,p in loc['ranked'][:5]]})
        return observe
    needed={q['chat'] for i,q in enumerate(questions,1) if i in wanted}
    for number,q in enumerate(questions,1):
        if q['chat'] not in needed:continue
        if number in wanted:sys.settrace(observe)
        try:answer=index.search(q['question'],Principal('exam-reader','staff'),q['chat'])
        finally:sys.settrace(None)
        if number in wanted:print(json.dumps({'id':q['id'],'question':q['question'],'answer':answer,'trace':probes},ensure_ascii=False))
        probes.clear()

if __name__=='__main__':
    try:main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)

````

## evaluation/closed_search/information_v9_20261031/postcheck.py

Полный путь: C:\Users\KROLL\Desktop\Ai\evaluation\closed_search\information_v9_20261031\postcheck.py.

SHA-256: `d3d9fda37031a70a436ffba9cc1e8cf3b54c0d40e206e0f15b10d67d1705b2c0`.

````python
"""Отдельные проверки после экзамена; исходная база и 100 ответов не меняются."""
from __future__ import annotations
import copy
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'results' / 'candidate1'
EXAM = HERE.with_name('holdout_20261031')
sys.path.insert(0, str(HERE.parents[2]))
from ai_core.site_documents import PARSER_VERSION
from ai_core.site_search import Principal
from ai_core.site_search_runtime import DurableSearch


def main():
    if not (OUT / 'FINISH.json').is_file():
        raise RuntimeError('First finish all 100 questions')
    db = OUT / 'postcheck.sqlite'
    if db.exists():
        raise RuntimeError('Postcheck already started; preserve its database')
    original = json.loads((OUT / 'records.json').read_text(encoding='utf-8'))
    records = copy.deepcopy(original)
    # Только технический адрес просмотра. Текст и права исходных документов прежние.
    for record in records:
        record['fragment_url'] = 'http://holdout.invalid/closed-search-fragment'
    engine = DurableSearch(['http://holdout.invalid'], db, extraction_version=PARSER_VERSION)
    engine.publish(records, engine.begin('postcheck-initial'))
    revisions = {item['id']: item['revision'] for item in records}
    actor, outsider = Principal('exam-reader', 'staff'), Principal('exam-outsider', 'staff')
    checks = []

    def check(name, passed, **detail):
        checks.append({'name': name, 'passed': bool(passed), **detail})

    statistics = []
    for source in records:
        parts = [p for p in engine.index.passages if p.source['id'] == source['id']]
        statistics.append({'id': source['id'], 'passages': len(parts),
                           'largest_passage': max((len(p.text) for p in parts), default=0),
                           'structure_unavailable': engine.index.sources[source['id']].get('structure_unavailable'),
                           'longer_than_2000': sum(len(p.text)>2000 for p in parts)})
    questions = json.loads((EXAM / 'questions.json').read_text(encoding='utf-8'))
    for position in [0, 20, 40, 60, 80]:
        q = questions[position]
        answer = engine.query(q['question'], outsider, q['chat'], None, revisions)
        check('outsider-' + q['id'], answer['status'] != 'answer' and not answer.get('evidence') and not answer.get('links'), result=answer)

    # Создаём реальный успешный контекст доступного документа и проверяем scope.
    answer = engine.query(questions[80]['question'], actor, 'isolation-shared', None, revisions)
    owner_scope = (actor.user_id, actor.role, (), 'isolation-shared')
    other_scope = (outsider.user_id, outsider.role, (), 'isolation-shared')
    check('owner-context-created', owner_scope in engine.index.sessions, status=answer['status'])
    owner_context = copy.deepcopy(engine.index.sessions.get(owner_scope))
    answer = engine.query('А адрес можно пришить?', outsider, 'isolation-shared', None, revisions)
    check('same-chat-other-user', other_scope not in engine.index.sessions and engine.index.sessions.get(owner_scope)==owner_context
          and not answer.get('evidence') and not answer.get('links') and answer['status']!='answer', result=answer)
    fresh_scope = (actor.user_id, actor.role, (), 'isolation-fresh')
    check('fresh-chat-has-no-inherited-context', fresh_scope not in engine.index.sessions)
    # Новый объект восстанавливает SQLite без OCR; качество реплик не переоцениваем.
    restored = DurableSearch(['http://holdout.invalid'], db, extraction_version=PARSER_VERSION)
    check('restore-index', restored.restored and restored.index.state=='ready' and len(restored.index.passages)==len(engine.index.passages),
          restored=restored.restored, passages=len(restored.index.passages), milliseconds=restored.restore_ms)
    restored_context = restored.index.sessions.get(owner_scope)
    check('restore-owner-context-only', restored_context is not None and other_scope not in restored.index.sessions
          and fresh_scope not in restored.index.sessions
          and restored_context.get('ids')==owner_context.get('ids'),
          restored_ids=restored_context.get('ids') if restored_context else [])
    target = next(item for item in restored.index.fragment_targets.values() if item['id']=='document:post')
    args = (target['id'], target['revision'], target['fragment'])
    check('owner-fragment-opens', restored.fragment(*args, actor, revisions)['status']=='ready')
    hidden = restored.fragment(*args, outsider, revisions)
    check('outsider-fragment-blocked', hidden['status']=='not_found' and 'text' not in hidden, result=hidden)
    new_revisions = {**revisions, 'document:post':'postcheck-revision-2'}
    changed = restored.fragment(*args, actor, new_revisions)
    check('live-revision-blocks-old-link', changed['status']=='changed' and 'text' not in changed, result=changed)
    changed_records = copy.deepcopy(records)
    changed_records[-1]['revision'] = new_revisions['document:post']
    # Смена только метки редакции проверяет закрытие старого адреса, не перенос разделов.
    restored.publish(changed_records, restored.begin('postcheck-revision-2'))
    changed = restored.fragment(*args, actor, new_revisions)
    check('published-revision-blocks-old-link', changed['status']=='changed' and 'text' not in changed, result=changed)
    remaining = [item for item in changed_records if item['id']!='document:post']
    remaining_revisions = {item['id']:item['revision'] for item in remaining}
    restored.publish(remaining, restored.begin('postcheck-deleted'))
    deleted = restored.fragment(*args, actor, remaining_revisions)
    check('deleted-source-blocks-old-link', deleted['status']=='not_found' and 'text' not in deleted, result=deleted)
    check('deleted-source-prunes-context', all('document:post' not in c.get('ids',[]) for c in restored.index.sessions.values()))
    report = {'at':datetime.now(timezone.utc).isoformat(), 'passed':sum(c['passed'] for c in checks),
              'total':len(checks), 'checks':checks, 'passage_statistics':statistics}
    with (OUT / 'postcheck.json').open('x',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)

````

## evaluation/closed_search/information_v9_20261031/grade.cjs

Полный путь: C:\Users\KROLL\Desktop\Ai\evaluation\closed_search\information_v9_20261031\grade.cjs.

SHA-256: `bd6d7caa9fbceafd162af3411dac09a888a1156cf58e6b25425460a7ca984e18`.

````javascript
/* Смысловая оценка повторных 100 вопросов. Не вызывается поисковым ядром.
 * Неизменённый ответ с теми же доказательствами сохраняет прежнюю оценку.
 * Каждое изменённое решение имеет отдельную ручную оценку ниже.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const base = __dirname;
const root = path.resolve(base, '../../..');
const exam = path.join(base, '../holdout_20261031');
const previousDir = path.join(base, '../ranking_v8_20261031/results/candidate2');
const out = path.join(base, 'results/candidate1');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const lines = file => fs.readFileSync(file, 'utf8').trim().split(/\r?\n/).map(JSON.parse);
const signature = row => JSON.stringify({status: row.result.status, text: row.result.text,
  evidence: row.result.evidence, links: row.result.links});
const reviewed = {
  q021: {grade:'correct', reason:'Искомое значение сформулировано прямо: «Горит: Питание включено». Смысл для индикатора питания однозначен, хотя ответ содержит соседние состояния, заголовки и начало FAQ; дефекты подачи отмечены отдельно по прежней методике.', issue:null, presentation:['excess','mixed_columns','service_heading']},
  q073: {grade:'correct', reason:'Перечислены все запрещённые средства и предметы: бензин, растворители, абразивные чистящие средства, металлические предметы и жёсткие щётки. Запрет сохранён; подготовительные действия исключены. В доказательстве осталась исходная цитата до очистки отображения.', issue:null, presentation:[]},
  q095: {grade:'correct', reason:'Сохранены свойства хрупкого измерительного прибора. Названы твёрдая тара и прокладки, исключающие перемещение и повреждение. Правило о защите от влаги больше не подменяет ответ. Две близкие выдержки повторяют часть смысла; одна скобка повреждена в исходном тексте.', issue:null, presentation:['excess','source_punctuation']}
};

function metric(rows) {
  const correct=rows.filter(row=>row.grade==='correct').length;
  return {correct,total:rows.length,percent:100*correct/rows.length};
}
function counts(rows,field) {
  const result={};
  for(const row of rows)result[row[field]||'none']=(result[row[field]||'none']||0)+1;
  return result;
}
function main() {
  const freeze=read(path.join(exam,'FREEZE.json'));
  const firstStart=read(path.join(exam,'results/START.json'));
  const firstFinish=read(path.join(exam,'results/FINISH.json'));
  const previousStart=read(path.join(previousDir,'START.json'));
  const previousFinish=read(path.join(previousDir,'FINISH.json'));
  const start=read(path.join(out,'START.json'));
  const finish=read(path.join(out,'FINISH.json'));
  const errors=[];
  for(const [name,value] of Object.entries(freeze.files))if(hash(path.join(exam,name))!==value)errors.push(name);
  for(const [name,value] of Object.entries(start.runtime))if(hash(path.join(root,name))!==value)errors.push(name);
  for(const [name,value] of Object.entries(previousStart.runtime)) {
    if(hash(path.join(base,'before/ai_core',path.basename(name)))!==value)errors.push('previous snapshot: '+name);
  }
  if(hash(path.join(exam,'FREEZE.json'))!==firstStart.freeze_sha256)errors.push('first freeze');
  if(hash(path.join(exam,'results/baseline.jsonl'))!==firstFinish.baseline_sha256)errors.push('first answers');
  if(hash(path.join(previousDir,'answers.jsonl'))!==previousFinish.answers_sha256)errors.push('v8 answers');
  if(hash(path.join(out,'answers.jsonl'))!==finish.answers_sha256)errors.push('new answers');
  if(finish.answers_sha256!=='283fc358bab7650180296ea48a5cfafe24926d9eeeb706cd91a4f8a1e804519a')errors.push('unreviewed journal');
  if(finish.changed_during_run.length)errors.push('runtime changed during run');
  if(errors.length)throw new Error(errors.join(', '));
  const questions=read(path.join(exam,'questions.json'));
  const gold=read(path.join(exam,'gold.json'));
  const raw=lines(path.join(out,'answers.jsonl'));
  const previousRaw=lines(path.join(previousDir,'answers.jsonl'));
  const previousGrades=read(path.join(previousDir,'judgments.json'));
  const firstGrades=read(path.join(exam,'results/judgments.json'));
  if(raw.length!==100||new Set(raw.map(row=>row.id)).size!==100)throw new Error('Need 100 distinct answers');
  const changed=[];
  const rows=raw.map((row,index)=>{
    const q=questions[index];
    if(row.id!==q.id||row.chat!==q.chat||row.question!==q.question)throw new Error('Question changed: '+row.id);
    const same=signature(row)===signature(previousRaw[index]);
    if(!same&&!reviewed[row.id])throw new Error('Missing manual grade: '+row.id);
    if(!same)changed.push(row.id);
    const grade=same?previousGrades[index]:reviewed[row.id];
    return {...row,...gold[index],...grade,id:row.id,
      previous_grade:previousGrades[index].grade,first_grade:firstGrades[index].grade,
      assessment:same?'unchanged text/status/evidence/links; previous assessment retained':'changed; manually assessed'};
  });
  if(changed.length!==Object.keys(reviewed).length)throw new Error('Manual overrides and changed rows disagree');
  const latency=raw.map(row=>row.milliseconds).sort((a,b)=>a-b);
  const sources=read(path.join(exam,'sources.json'));
  const postcheck=read(path.join(out,'postcheck.json'));
  const summary={
    at:new Date().toISOString(),type:'known regression; no new independent holdout',
    integrity:'frozen inputs, first/v8 journals, before snapshot and final runtime verified',
    answers_sha256:finish.answers_sha256,previous_answers_sha256:previousFinish.answers_sha256,
    overall:metric(rows),answerable:metric(rows.filter(row=>row.action==='answer')),
    insufficient:metric(rows.filter(row=>row.action==='insufficient')),clarify:metric(rows.filter(row=>row.action==='clarify')),
    dialogue_turns:metric(rows.filter(row=>row.dialogue)),
    followup_only:metric(rows.filter(row=>row.dialogue&&((Number(row.id.slice(1))-1)%20)>12)),
    by_source:Object.fromEntries(sources.map(source=>[source.id,metric(rows.filter(row=>row.source===source.id))])),
    grades:counts(rows,'grade'),issues:counts(rows.filter(row=>row.grade!=='correct'),'issue'),
    changed_answers:changed,unchanged_answers:100-changed.length,
    newly_correct:rows.filter(row=>row.grade==='correct'&&row.previous_grade!=='correct').map(row=>row.id),
    lost_correct:rows.filter(row=>row.grade!=='correct'&&row.previous_grade==='correct').map(row=>row.id),
    lost_from_first:rows.filter(row=>row.grade!=='correct'&&row.first_grade==='correct').map(row=>row.id),
    correct_with_presentation_issues:rows.filter(row=>row.grade==='correct'&&row.presentation.length).length,
    statuses:counts(raw.map(row=>row.result),'status'),
    latency_ms:{median:(latency[49]+latency[50])/2,p95:latency[94],max:latency[99],total:latency.reduce((a,b)=>a+b,0)},
    extraction:read(path.join(out,'extraction.json')),index:read(path.join(out,'index.json')),
    postcheck:{passed:postcheck.passed,total:postcheck.total}
  };
  const md=['# Нужная информация внутри документа: повторные 100 вопросов','',
    'Известная регрессия, исходные вопросы и эталоны не изменены. Все 100 вопросов выполнены заново. Для 97 одинаковых ответов сверены текст, статус, полные доказательства и ссылки; прежние оценки сохранены. Три изменённых ответа оценены агентом по прежнему METHOD.md. Это не внешняя комиссия и не новый независимый экзамен.','',
    'Итог: '+summary.overall.correct+'/100 (v8: 75/100, первый прогон: 30/100). SHA-256 итогового журнала: `'+finish.answers_sha256+'`.',''];
  for(const row of rows) {
    md.push('## '+row.id,'','Вопрос: '+row.question,'','Неизменённый эталон: '+row.answer,'',
      'Место в оригинале: '+row.reference,'','Ожидаемое действие: '+row.action+'. Статус движка: '+row.result.status+'.','',
      'Полный ответ Ватэс:','','```text',row.result.text,'```','',
      'Оценка: '+row.grade+'; v8: '+row.previous_grade+'; первый прогон: '+row.first_grade+'. '+row.reason,'',
      'Способ оценки: '+row.assessment+'.','',
      'Подача: '+(row.presentation.join(', ')||'существенных замечаний нет')+'.','',
      '```json',JSON.stringify({evidence:row.result.evidence,links:row.result.links},null,2),'```','');
  }
  fs.writeFileSync(path.join(base,'ALL_100_RESULTS.md'),md.join('\n'));
  fs.writeFileSync(path.join(out,'judgments.json'),JSON.stringify(rows.map(row=>({id:row.id,grade:row.grade,
    reason:row.reason,issue:row.issue,presentation:row.presentation,previous_grade:row.previous_grade,assessment:row.assessment})),null,2)+'\n');
  fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(summary,null,2)+'\n');
  console.log(JSON.stringify(summary,null,2));
}
try{main();}catch(error){console.error(error.stack);process.exitCode=1;}

````

## evaluation/closed_search/information_v9_20261031/package_report.cjs

Полный путь: C:\Users\KROLL\Desktop\Ai\evaluation\closed_search\information_v9_20261031\package_report.cjs.

SHA-256: `a8bc0f91efb55f6bec56669b3bfd703b15922f92f321a4803b423b8ac5f0967e`.

````javascript
/* Полные листинги и контрольные суммы выбора информации внутри документа.
 * Скрипт не меняет ядро, эталоны, первые журналы или рабочую интеграцию.
 */
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');

function main() {
  const here=__dirname;
  const root=path.resolve(here,'../../..');
  const before=path.join(here,'before/ai_core');
  const out=path.join(here,'results/candidate1');
  const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  const relative=file=>path.relative(root,file).replaceAll('\\','/');
  const start=JSON.parse(fs.readFileSync(path.join(out,'START.json'),'utf8'));
  for(const [name,expected] of Object.entries(start.runtime)) {
    if(hash(path.join(root,name))!==expected)throw new Error('Runtime changed: '+name);
  }
  const names=fs.readdirSync(path.join(root,'ai_core')).filter(name=>name.endsWith('.py')).sort();
  const changed=names.filter(name=>!fs.existsSync(path.join(before,name))||hash(path.join(before,name))!==hash(path.join(root,'ai_core',name)));
  const files=changed.map(name=>path.join(root,'ai_core',name));
  files.push(path.join(root,'tests/test_site_search_information.py'));
  for(const name of ['run.py','diagnose.py','postcheck.py','grade.cjs','package_report.cjs'])files.push(path.join(here,name));
  const manifest={
    at:new Date().toISOString(),root,final_run:'results/candidate1',runtime_count:Object.keys(start.runtime).length,
    changed_core:changed.map(name=>({path:'ai_core/'+name,
      before:fs.existsSync(path.join(before,name))?hash(path.join(before,name)):null,
      after:hash(path.join(root,'ai_core',name))})),
    full_code_files:files.map(file=>({path:relative(file),sha256:hash(file)})),
    evidence:['tests-final.txt','candidate1/answers.jsonl','candidate1/judgments.json','candidate1/summary.json','candidate1/postcheck.json']
      .map(name=>({path:'results/'+name,sha256:hash(path.join(here,'results',name))}))
  };
  const md=['# ВатэсAi: нужная информация внутри документа, полный код','',
    'Проект: C:/Users/KROLL/Desktop/Ai. Ниже приведены полные текущие файлы без сокращений. Ядро совпадает с хешами итогового прохода candidate1. Прежние версии сохранены в evaluation/closed_search/information_v9_20261031/before/ai_core.','',
    'Результат и ограничения: SEARCH_INFORMATION_V9_REPORT_2026_10_31.md. Известные 100 вопросов: 78/100 вместо 75/100; исправлены средства очистки, упаковка хрупкого прибора и сохранение описания индикатора питания. Все прежние правильные ответы сохранены. Целевой уровень качества ещё не достигнут; интеграция LMS в этом этапе не обновлялась.',''];
  for(const file of files) {
    md.push('## '+relative(file),'','Полный путь: '+file+'.','','SHA-256: `'+hash(file)+'`.','',
      '````'+(file.endsWith('.py')?'python':'javascript'),fs.readFileSync(file,'utf8'),'````','');
  }
  fs.writeFileSync(path.join(root,'SEARCH_INFORMATION_V9_FULL_CODE_2026_10_31.md'),md.join('\n'));
  fs.writeFileSync(path.join(here,'manifest.json'),JSON.stringify(manifest,null,2)+'\n');
  console.log(JSON.stringify({changed_core:changed,full_files:files.length,runtime_files:manifest.runtime_count}));
}
try{main();}catch(error){console.error(error.stack);process.exitCode=1;}

````
