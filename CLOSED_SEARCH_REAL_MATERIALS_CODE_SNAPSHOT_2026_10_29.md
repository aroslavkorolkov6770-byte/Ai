# Полный код этапа проверки реальных материалов

29.10.2026. Каждый блок содержит целый файл без пропусков.

## ai_core/site_search.py

SHA-256: 2cd09e34ea554907b174be9117378625d8bd8a2a793115c723d2e090fc0f5fe8

`````python
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
        if result['status'] in {'not_found','unavailable','conflict','processing'} or (result['status']=='clarify' and sessions.get(scope) is previous and not result.get('context_used')):
            sessions.pop(scope,None)
        if result['status']=='answer':
            result=self._linked_evidence(result)
            if result['status']=='unavailable':
                sessions.pop(scope,None)
        if scope in sessions:
            names=explicit_names(lookup_question(question))
            vocabulary={token for item in self.passages if item.source['id'] in sessions[scope]['ids'] for token in item.title_words}
            subjects=[name for name in names if set(terms(name)).issubset(vocabulary)]
            sessions[scope]['subjects']=subjects or ((previous or {}).get('subjects',[]) if result.get('context_used') else [])
        clean, day, invalid = question_day(question, self.clock())
        if not invalid and scope in sessions and result['status'] in {'answer', 'clarify'}:
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
                link={'title':source['title'],'url':source['url']}
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
        return {**result,'evidence':evidence,'links':links}

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
        local_facts, local_focus = question_context(question)
        # «Смена закрыта, после этого нашли ошибку. Кто исправляет?» содержит
        # собственное описание. «Этого» здесь не ссылается на прошлый чат.
        reference_text = local_focus if local_facts else question
        reference = bool(re.search(r"\b(это|этого|этой|там|тогда|него|нее|ней|этом|он|она|они)\b", normal(reference_text))) or (normal(question).startswith("а ") and len(raw_words) <= 3)
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
        words = list(dict.fromkeys(current_words + (session["terms"] if context_used and not topic_followup else [])))
        named = [term for name in explicit_names(focused_question) if name.casefold() not in STOP for term in terms(name)][:16]
        if re.match(r'^а\s+(?:если|при)\b',normal(question)) and not named and not context_used and not selected_source:
            return {'status':'clarify','text':'Уточните, к какому материалу или правилу относится это условие.','links':[],'evidence':[]}
        numbers = re.findall(r"\d+(?:[.,]\d+)*", focused_question)[:16]
        candidates = [item for item in self.passages if item.source["id"] in allowed and (not selected_source or item.source["id"] == selected_source)
                      and active(item.structure or {}, day) and self._usable_passage(item,allowed,day)]
        if context_used and not topic_followup:
            candidates = [item for item in candidates if item.source['id'] in focus_ids]
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
                candidates = [item for item in candidates if re.search(r"\b(?:день|дня|дней|дни|час\w*|минут\w*|недел\w*|месяц\w*|суток|срок\w*)\b", normal(item.text))]
        # Обзор объясняет весь порядок вместе с условиями. Он не требует
        # выяснять частные обстоятельства пользователя, как конкретный случай.
        conditional = None if want_overview else self._conditional_answer(question, candidates, current_words, named, facet, scope, sessions,
                                                session if context_used else None, bool(selected_source or (context_used and not topic_followup)), started)
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
        self._remember(sessions,scope,[best.source['id']],self._query_terms(question)[:20] if not context_used else words[:24],facet=facet)
        # Ограничиваем производную память; она не становится корпусом для обучения.
        if len(sessions) > 1000:
            oldest = min(sessions, key=lambda key: sessions[key]["time"])
            del sessions[oldest]
        return {"status": "answer", "text": answer, "links": links, "evidence": evidence, "context_used": context_used,
                "timing_ms": round((time.perf_counter() - started) * 1000, 2)}

`````

## ai_core/site_search_reasoning.py

SHA-256: 71ccb4b8b36e0b110e95398a07da0b8378594228e09a660c5d9bab9dd89bf5e8

`````python
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

`````

## ai_core/site_search_conditions.py

SHA-256: 14e7f0c6387fb99e67273a839cd8b70708a655779dcdc82ba57824642746ac14

`````python
"""Ограниченный разбор условий с тремя исходами: да, нет, неизвестно.

Правила и формулировки ответа берутся из документа. Этот модуль не дополняет
неизвестные факты и не исполняет выражения из загруженного текста.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from .site_search_reasoning import RELATIONS

GUARD = re.compile(r"\b(?:если|при условии|исключени\w*|в остальных случаях|за исключением|кроме случаев|только при)\b", re.I)
UNKNOWN = re.compile(r"не знаю|не уверен|неизвестн|возможно|наверно|не помню|неясно", re.I)


def guarded(text: str) -> bool:
    return bool(GUARD.search(text) or re.search(r"(?:^|[.!?]\s+)При\s+[^,]+,", text))


def operation_names(text: str) -> frozenset[str]:
    return frozenset(name for name, pattern in RELATIONS if re.search(pattern, text.casefold()))


def paragraphs(text: str) -> list[str]:
    """Сохраняем исключения в том же разделе, в том числе после пустой строки."""
    parts = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    joined = []
    for part in parts:
        if joined and not re.search(r'[.!?:;]$', joined[-1]) and re.match(r'[а-яa-z]', part):
            joined[-1] += ' ' + part
        else:
            joined.append(part)
    parts = joined
    return [' '.join(parts)] if guarded(text) else parts


def normalized(text: str) -> str:
    value = text.casefold().replace('ё', 'е')
    value = re.sub(r"(?<=\d)[ \u00a0](?=\d{3}(?:\D|$))", '', value)
    value = re.sub(r"\bоткрыт\w*", 'вскрыта', value)
    value = re.sub(r"\bхранени\w*", 'хранить', value)
    # Одинаковое событие может быть названо существительным или глаголом.
    # Эти формы нужны только проверке условий, исходную цитату не меняем.
    value = re.sub(r'\b(?:выявлен\w*|обнаружен\w*|нашли|найден\w*)\b', 'выявлена', value)
    value = re.sub(r'\bзакрыт\w*\b', 'закрытие', value)
    value = re.sub(r'\bуже\b', '', value)
    # Явная локальная связь двух частей предложения: событие, после этого факт.
    # Не переносим «этого» между предложениями или из чужого контекста.
    value = re.sub(r'([^.!?,;]+),\s*(до|после) этого\s+([^.!?;]+)',
                   lambda match: match[3] + ' ' + match[2] + ' ' + match[1], value)
    return value


def split_logic(text: str, operator: str) -> list[str]:
    # Скобки разбираются счётчиком, без eval и без угадывания их пропущенных пар.
    depth, start, pieces = 0, 0, []
    for match in re.finditer(r"[()]|\b" + operator + r"\b", text):
        word = match.group()
        if word == '(':
            depth += 1
        elif word == ')':
            depth -= 1
        elif depth == 0:
            pieces.append(text[start:match.start()].strip())
            start = match.end()
        if depth < 0:
            return [text]
    pieces.append(text[start:].strip())
    return pieces


def ungroup(text: str) -> str:
    text = text.strip(' .')
    while text.startswith('(') and text.endswith(')'):
        depth, enclosed = 0, True
        for pos, char in enumerate(text):
            depth += (char == '(') - (char == ')')
            if depth == 0 and pos < len(text) - 1:
                enclosed = False
                break
        if not enclosed:
            break
        text = text[1:-1].strip()
    return text


def atoms(expression: str) -> list[str]:
    text = ungroup(normalized(expression))
    for operator in ('или', 'и'):
        parts = split_logic(text, operator)
        if len(parts) > 1:
            return [atom for part in parts for atom in atoms(part)]
    return [text]


def atomic_observation(atom: str, question: str, tokenize):
    """(упомянуто, истинность): явная неопределённость отменяет прежний факт."""
    text = normalized(question)
    expected = normalized(atom)
    comparator = re.search(r"(не менее|не больше|не более|не меньше|более|больше|менее|меньше|от)\s*(\d+(?:[.,]\d+)?)(?:\s+до\s+(\d+(?:[.,]\d+)?))?", expected)
    # Точка и запятая внутри числа не разделяют факты: 4999.99 — одна сумма.
    fragments = re.split(r"[;!?]|(?<!\d)[.,]|[.,](?!\d)|\b(?:но|и|если)\b", text)
    # Не разбиваем отрицание «не знаю, вскрыта ли упаковка» на достоверные факты.
    uncertain = UNKNOWN.search(text)
    uncertain_tokens = set(tokenize(re.split(r'[.!?]', text[uncertain.start():])[0])) if uncertain else set()
    if comparator:
        subject = set(tokenize(expected[:comparator.start()]))
        unit = re.search(r"руб\w*|₽|кг|килограмм\w*|грамм\w*|\bг\b|дней|дня|день", expected[comparator.end():])
        if not subject or not unit:
            return False, None
        canonical_unit = 'rub' if unit.group().startswith('руб') or unit.group() == '₽' else 'mass' if re.match(r'кг|килограмм|грамм|г$', unit.group()) else 'day'
        observed = []
        for part in fragments:
            if not subject.issubset(set(tokenize(part))):
                continue
            if UNKNOWN.search(part) or re.search(r"\bли\b", part):
                return True, None
            for number, actual_unit in re.findall(r"(\d+(?:[.,]\d+)?)\s*(руб\w*|₽|кг|килограмм\w*|грамм\w*|г\b|дней|дня|день)", part):
                category = 'rub' if actual_unit.startswith('руб') or actual_unit == '₽' else 'mass' if re.match(r'кг|килограмм|грамм|г$', actual_unit) else 'day'
                if category == canonical_unit:
                    value = float(number.replace(',', '.'))
                    if category == 'mass' and actual_unit.startswith(('кг', 'килограмм')):
                        value *= 1000
                    observed.append(value)
        if len(set(observed)) != 1:
            return bool(observed), None
        boundary = float(comparator[2].replace(',', '.'))
        upper = float(comparator[3].replace(',', '.')) if comparator[3] else None
        if canonical_unit == 'mass' and unit.group().startswith(('кг', 'килограмм')):
            boundary *= 1000
            if upper is not None:
                upper *= 1000
        value, operator = observed[0], comparator[1]
        matches = {'не менее': value >= boundary, 'не меньше': value >= boundary,
                   'не более': value <= boundary, 'не больше': value <= boundary,
                   'больше': value > boundary, 'более': value > boundary,
                   'меньше': value < boundary, 'менее': value < boundary,
                   'от': value >= boundary and (upper is None or value <= upper)}
        return True, matches[operator]
    # «Не» хранится отдельно от поисковых токенов, иначе оплачено и не оплачено совпадут.
    negative = bool(re.search(r"\bне\b|\bнет\b|\bбез\b|отсутств", expected))
    clean = re.sub(r"\b(?:не|нет|без|есть)\b|отсутств\w*", ' ', expected)
    wanted = set(tokenize(clean))
    if not wanted:
        return False, None
    if uncertain_tokens and wanted.issubset(uncertain_tokens):
        return True, None
    observations = []
    location = re.search(r'\b(?:в|на)\s+([а-я]+)', expected)
    temporal = re.search(r'\b(до|после)\s+(.+)', expected)
    for part in fragments:
        tokens = set(tokenize(part))
        if temporal:
            # Поисковые стоп-слова не должны уравнивать «до» и «после».
            # Сравниваем только явно указанный момент того же события.
            event = set(tokenize(temporal[2]))
            predicate = set(tokenize(expected[:temporal.start()]))
            moments = [(match[1], set(tokenize(match[2]))) for match in
                       re.finditer(r'\b(до|после)\s+([^,;.!?]+)', part)]
            relevant = [moment for moment, observed in moments if event and event.issubset(observed)]
            if relevant and predicate.issubset(tokens):
                if UNKNOWN.search(part) or re.search(r'\bли\b', part) or len(set(relevant)) != 1:
                    observations.append(None)
                elif re.search(r'\bне\s+(?:до|после)\b', part):
                    # «Не после» не доказывает «до»: возможно, одновременно.
                    observations.append(None)
                else:
                    observations.append(relevant[0] == temporal[1])
                continue
        if location:
            places = re.findall(r'\b(?:в|на)\s+([а-я]+)', part)
            predicate = wanted - set(tokenize(location[0]))
            # Сравниваем место только у того же действия/предмета. Упоминание
            # офиса собеседника ничего не говорит о месте хранения товара.
            if len(set(places)) == 1 and predicate and predicate.issubset(tokens):
                if UNKNOWN.search(part) or re.search(r'\bли\b', part):
                    return True, None
                actual_negative = bool(re.search(r"\bне\b|\bнет\b|\bбез\b|отсутств", part))
                if location[1] == places[0]:
                    observations.append(actual_negative == negative)
                elif not actual_negative:
                    observations.append(negative)
                else:
                    # «Не в офисе» не доказывает нахождение в холодильнике.
                    observations.append(None)
                continue
        if not wanted.issubset(tokens):
            continue
        if UNKNOWN.search(part) or re.search(r'\bли\b', part):
            return True, None
        actual_negative = bool(re.search(r"\bне\b|\bнет\b|\bбез\b|отсутств", part))
        observations.append(actual_negative == negative)
    return (True, observations[0] if len(set(observations)) == 1 else None) if observations else (False, None)


def truth(expression: str, facts: dict[str, bool | None]):
    text = ungroup(normalized(expression))
    for operator in ('или', 'и'):
        parts = split_logic(text, operator)
        if len(parts) > 1:
            values = [truth(part, facts) for part in parts]
            if operator == 'или':
                return True if True in values else None if None in values else False
            return False if False in values else None if None in values else True
    return facts.get(text)


@dataclass(frozen=True)
class Rule:
    quote: str
    body: str
    condition: str | None
    exception: bool = False


def rules(text, sentences, facet, supplies) -> list[Rule]:
    result = []
    for sentence in sentences(text):
        clause = re.sub(r'^Исключение:\s*', '', sentence, flags=re.I)
        exception = clause != sentence
        prefix = re.match(r'^(?:если|при условии,? что|при условии|при)\s+(.+?),(?!\d)\s*(?:то\s+)?(.+)', clause, re.I)
        # В реальных инструкциях результат часто отделён тире, а не запятой.
        # Берём первое тире только после явного «если», сохраняя весь результат.
        dash_prefix = re.match(r'^если\s+([^—;:]+?)\s+(?:—|–|-)\s+(.+)', clause, re.I)
        if dash_prefix and (prefix is None or dash_prefix.start(2) < prefix.start(2)):
            prefix = dash_prefix
        suffix = re.match(r'^(.+?)\s+только если\s+(.+?)[.]?$', clause, re.I)
        if prefix:
            condition, body = prefix.groups()
            if re.search(r'[;:—]|\s-\s', condition):
                condition, body = None, sentence
        elif suffix:
            body, condition = suffix.groups()
            if re.search(r'[;:—]|\s-\s', condition):
                # Цепочка «сначала X; только если Y — Z» не является
                # одним правилом «X только если Y». Сохраняем её для цитирования.
                condition, body = None, sentence
        else:
            condition, body = None, re.sub(r'^В остальных случаях[, ]*', '', clause, flags=re.I)
        if supplies(body, facet):
            result.append(Rule(sentence, body, condition, exception))
    return result


def has_related_exception(items, score):
    """Краткая сноска наследует операцию своего правила, но не соседней операции."""
    best = max((score(item) for item in items), default=-1)
    operations = set().union(*(operation_names(item.body) for item in items if score(item) == best))
    return any(item.condition and item.exception and bool(operation_names(item.body) & operations) for item in items)


def resolve(text, question, tokenize, sentences, facet, supplies, previous=None, matching=None):
    items = rules(text, sentences, facet, supplies)
    # Вопрос о получателе не должен требовать уточнения состояния упаковки,
    # если это состояние относится только к соседнему правилу хранения.
    focus = re.split(r'(?<=[.!])\s+', question.strip())[-1]
    focus_words = set(tokenize(focus))
    matching = matching or (lambda left, right: left == right)
    def score(item):
        body_words = set(tokenize(item.body))
        return sum(any(matching(word, token) for token in body_words) for word in focus_words)
    conditional_score = max((score(item) for item in items if item.condition), default=-1)
    ordinary_score = max((score(item) for item in items if item.condition is None), default=-1)
    if ordinary_score > conditional_score and not has_related_exception(items, score):
        return None
    if conditional_score > 0:
        def operations(item):
            return operation_names(item.body)
        selected_operations = set().union(*(operations(item) for item in items if score(item) >= conditional_score))
        def same_operation(item):
            own = operations(item)
            return not own or not selected_operations or bool(own & selected_operations)
        def temporal_key(item):
            condition = normalized(item.condition or '')
            if not re.search(r'\b(?:до|после)\b', condition):
                return None
            return frozenset(tokenize(re.sub(r'\b(?:до|после)\b', '', condition)))
        temporal_keys = {temporal_key(item) for item in items if score(item) >= conditional_score} - {None}
        # Короткое исключение часто не повторяет название товара. Его нельзя
        # исключать по числу совпавших слов. Но запрет хранения не должен
        # подменять правило возврата из соседней части того же раздела.
        items = [item for item in items if score(item) >= conditional_score or
                 (same_operation(item) and (item.exception or temporal_key(item) in temporal_keys
                                           or re.match(r'^В остальных случаях\b', item.quote, re.I)))]
    conditional = [item for item in items if item.condition]
    if not conditional:
        return None
    if len(items) == 1 and len(conditional[0].condition) > 240:
        # Непрозрачное длинное условие можно процитировать целиком, но нельзя
        # пытаться вычислить его по совпадению одного слова.
        return None
    if len(text) > 20000 or any(item.condition.count('(') != item.condition.count(')') for item in conditional):
        return {'status': 'clarify', 'text': 'Уточните конкретный случай: в этом разделе несколько сложных условий.', 'facts': {}, 'selected': []}
    known = {atom for item in conditional for atom in atoms(item.condition)}
    facts = {atom: value for atom, value in (previous or {}).items() if atom in known and isinstance(value, bool)}
    for atom in known:
        observed, value = atomic_observation(atom, question, tokenize)
        if observed:
            facts[atom] = value
    states = [(item, truth(item.condition, facts)) for item in conditional]
    active = [item for item, state in states if state is True]
    if any(item.exception for item in active):
        active = [item for item in active if item.exception]
    unknown = [item for item, state in states if state is None and (not active or item.exception or not active[0].exception)]
    if unknown:
        missing = list(dict.fromkeys(atom for item in unknown for atom in atoms(item.condition) if facts.get(atom) is None))
        # Противоположные ветки «вскрыта / не вскрыта» требуют одного факта,
        # поэтому не задаём один и тот же вопрос дважды в разных отрицаниях.
        missing = [atom for atom in missing if not (re.search(r'\bне\s+', atom)
                   and re.sub(r'\bне\s+', '', atom) in missing)]
        prompt = '; '.join(missing[:3])
        return {'status': 'clarify', 'text': 'Уточните: ' + prompt + '?' if len(prompt) <= 240 else 'Уточните конкретный случай: в документе несколько условий.',
                'facts': {key: value for key, value in facts.items() if isinstance(value, bool)}, 'selected': []}
    selected = active or [item for item in items if item.condition is None]
    if not selected:
        return {'status': 'clarify', 'text': 'Для указанных условий в документе не найден отдельный порядок. Уточните его у администратора.',
                'facts': {key: value for key, value in facts.items() if isinstance(value, bool)}, 'selected': [], 'inapplicable': True}
    return {'status': 'answer', 'selected': selected, 'facts': {key: value for key, value in facts.items() if isinstance(value, bool)}}


def summary_request(question: str) -> bool:
    value = normalized(question)
    return bool(re.search(r'перескаж\w*|резюме|содержани\w*|выдели\w* главное|основные положения|о чем (?:этот |выбранный )?(?:документ|файл)|кратко.*о чем', value))

`````

## ai_core/site_search_language.py

SHA-256: 8f1e851e3c8560305db4457e93096518324b6f396d5fd662af1b9f0d7ba81a2a

`````python
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


def requested_facet(question: str) -> str | None:
    value = question.casefold()
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
    if facet == 'packaging':
        return bool(re.search(r'пакет\w*|упаков\w*|банка|банку|коробк\w*', value))
    if facet == 'cost':
        return bool(re.search(r"\bруб\w*|₽|цен\w*|стоимост\w*|бесплатн\w*", value)) and not bool(re.search(r"не (?:указан|определ[её]н|извест|установлен)|нет (?:сведений|информации)", value))
    if facet == 'year':
        return bool(re.search(r"\b(?:19|20)\d{2}\b", text))
    if facet == 'recipient':
        return bool(re.search(r"координатор|диспетчер|старш\w* сотрудник|старш\w* смен|администратор|дежурн|менеджер|руководител|кладовщик|оператор|продавец|продавц|библиотекар", value))
    if facet == 'duration':
        return bool(re.search(r"\b(?:день|дня|дней|дни|суток|час\w*|минут\w*|недел\w*|месяц\w*)\b", value)) and bool(re.search(r"\d|\b(?:" + "|".join(NUMBERS) + r")\b", value.replace('ё', 'е')))
    if facet == 'procedure':
        return bool(re.search(r"отправ\w*|переда\w*|заполн\w*|запис\w*|показыв\w*|покаж\w*|сообщ\w*|подат\w*|звон\w*|обратит\w*", value))
    return True

`````

## ai_core/site_documents.py

SHA-256: 523e21836634e6269c6ec66ac20adf601b8973bdae8195d3e328d8a49d54821d

`````python
"""Фоновое извлечение документов. Никакого OCR во время пользовательского поиска."""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from pypdf import PdfReader

from .site_search import plain_text
from .site_search_structure import normalize_markup, table_markdown
from .site_scan_tables import extract_scan
from .site_scan_layout import compose_pages
from .site_scan_tables import describe_table

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".runtime" / "closed-search" / "document-cache"
# Изменение алгоритма извлечения само инвалидирует производный кэш. Иначе
# исправленный OCR мог бы продолжать читать прежний результат тех же байтов PDF.
PARSER_VERSION = "closed-documents-v7-multipage-" + hashlib.sha256(
    Path(__file__).read_bytes() + Path(__file__).with_name('site_scan_tables.py').read_bytes()
    + Path(__file__).with_name('site_scan_layout.py').read_bytes()
    + Path(__file__).with_name('site_scan_quality.py').read_bytes()
).hexdigest()[:16]


def _command(name: str) -> str:
    override = os.environ.get(f"VATES_{name.upper()}")
    if override:
        return override
    found = shutil.which(name)
    if found:
        return found
    if name == "tesseract" and os.name == "nt":
        installed = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR" / "tesseract.exe"
        if installed.is_file():
            return str(installed)
    raise ValueError("document_tool_unavailable")


def _run(arguments: list[str], timeout=60) -> str:
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(arguments, capture_output=True, check=True, timeout=timeout,
                            text=True, encoding="utf-8", errors="replace", **options)
    return result.stdout


def _ocr(image: Path, *, tsv=False) -> str:
    language_directory = ROOT / ".runtime" / "closed-search" / "ocr-tessdata"
    language = ["--tessdata-dir", str(language_directory)] if language_directory.is_dir() else []
    return _run([_command("tesseract"), str(image), "stdout", *language, "-l", "rus+eng", "--psm", "6" if tsv else "3", *(["-c", "tessedit_create_tsv=1"] if tsv else [])])


def _scan_text(image: Path, page=1, metadata=None) -> str:
    text, table = extract_scan(image, lambda path: _ocr(path, tsv=True), _join_visual_lines, page, metadata)
    return text if table else _join_visual_lines(_ocr(image))


def _join_visual_lines(text: str) -> str:
    paragraphs = []
    current = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            # OCR иногда вставляет пустую строку внутри длинной сноски.
            # Её условие и результат должны остаться одним определением.
            if re.match(r'^\[\^?[\w-]+\]:', current) and not re.search(r'[.!?]$', current):
                continue
            if current:
                paragraphs.append(current)
                current = ""
            continue
        if current and (re.search(r"[.!?][»\"']?$", current) or (current.isupper() and len(current) < 100)
                        or re.match(r'^\[\^?[\w-]+\]:', line)):
            paragraphs.append(current)
            current = ""
        current += (" " if current else "") + line
    if current:
        paragraphs.append(current)
    return "\n".join(paragraphs)


def _word_text(archive):
    namespace = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    prefix = '{' + namespace['w'] + '}'

    def xml(name):
        entry = archive.getinfo(name)
        if entry.file_size > 8 * 1024 * 1024:
            raise ValueError('document_xml_too_large')
        return ElementTree.fromstring(archive.read(entry))

    def paragraph(element):
        parts = []
        for node in element.iter():
            if node.tag == prefix + 't':
                parts.append(node.text or '')
            elif node.tag in {prefix + 'br', prefix + 'tab'}:
                parts.append(' ')
            elif node.tag in {prefix + 'footnoteReference', prefix + 'endnoteReference'}:
                kind = 'end-' if node.tag == prefix + 'endnoteReference' else ''
                parts.append('[' + kind + node.get(prefix + 'id', 'missing') + ']')
        text = ''.join(parts)
        style = element.find('./w:pPr/w:pStyle', namespace)
        if style is not None and re.search(r'heading|заголовок', style.get(prefix + 'val', ''), re.I):
            text = '# ' + text
        return text

    body = xml('word/document.xml').find('w:body', namespace)
    if body is None:
        raise ValueError('document_body_missing')
    blocks = []
    for element in body:
        if element.tag == prefix + 'p':
            blocks.append(paragraph(element))
        elif element.tag == prefix + 'tbl':
            # Объединённые ячейки нельзя однозначно приписать одной строке.
            if element.find('.//w:gridSpan', namespace) is not None or element.find('.//w:vMerge', namespace) is not None:
                blocks.append('[STRUCTURE_UNCERTAIN]')
            rows = [[' '.join(paragraph(p) for p in cell.findall('w:p', namespace)).replace('|', '¦')
                     for cell in row.findall('w:tc', namespace)] for row in element.findall('w:tr', namespace)]
            blocks.append(table_markdown(rows))
    for filename, tag, kind in [('word/footnotes.xml', 'footnote', ''), ('word/endnotes.xml', 'endnote', 'end-')]:
        if filename not in archive.namelist():
            continue
        for note in xml(filename).findall('w:' + tag, namespace):
            if note.get(prefix + 'type') in {'separator', 'continuationSeparator'}:
                continue
            identifier = note.get(prefix + 'id', '')
            content = ' '.join(paragraph(p) for p in note.findall('w:p', namespace))
            blocks.append(f'[{kind}{identifier}]: {content}')
    # Старые ЧаВо часто оформлены обычными абзацами «- вопрос? / - ответ»,
    # без Word-стилей заголовков. Сохраняем границу каждой пары до индексации:
    # иначе одно «если» в ответе склеит весь файл в единое условное правило.
    nonempty = [block for block in blocks if block.strip()]
    questions = [position for position, block in enumerate(nonempty[:-1])
                 if re.fullmatch(r'\s*[-–—]\s+[^\n]+\?\s*', block)
                 and re.match(r'\s*[-–—]\s+', nonempty[position + 1])
                 and not nonempty[position + 1].rstrip().endswith('?')]
    if len(questions) >= 2:
        for position in questions:
            nonempty[position] = '# ' + re.sub(r'^\s*[-–—]\s+', '', nonempty[position]).strip()
            nonempty[position + 1] = re.sub(r'^\s*[-–—]\s+', '', nonempty[position + 1])
        blocks = nonempty
    return '\n'.join(blocks)


def extract_document(data_url: str, name: str) -> tuple[str, bool]:
    if not isinstance(data_url, str) or not data_url.startswith("data:") or ";base64," not in data_url:
        raise ValueError("document_data_invalid")
    encoded = data_url.split(";base64,", 1)[1]
    if len(encoded) > 20 * 1024 * 1024:
        raise ValueError("document_too_large")
    data = base64.b64decode(encoded, validate=True)
    if len(data) > 15 * 1024 * 1024:
        raise ValueError("document_too_large")
    extension = Path(name).suffix.casefold()
    fingerprint = hashlib.sha256(PARSER_VERSION.encode() + extension.encode() + data).hexdigest()
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / f"{fingerprint}.json"
    if cache.is_file():
        try:
            previous = json.loads(cache.read_text(encoding="utf-8"))
            text = previous["text"]
            if isinstance(text, str) and hashlib.sha256(text.encode()).hexdigest() == previous["sha256"]:
                return text, bool(previous["complete"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    complete = True
    with tempfile.TemporaryDirectory(prefix="vates-private-document-") as directory:
        root = Path(directory)
        file = root / ("document" + extension)
        file.write_bytes(data)
        if extension in {".txt", ".md", ".html", ".htm"}:
            raw = data.decode("utf-8-sig", errors="strict")
            text = normalize_markup(raw)
            if extension in {'.html', '.htm'} and text == raw:
                text = plain_text(raw)
        elif extension == ".csv":
            raw = data.decode("utf-8-sig", errors="strict")
            dialect = csv.Sniffer().sniff(raw[:8000], delimiters=",;\t")
            reader = csv.DictReader(io.StringIO(raw), dialect=dialect)
            rows = []
            for index, row in enumerate(reader):
                if index >= 5000:
                    complete = False
                    break
                rows.append(" ".join(f"{key}: {value}." for key, value in row.items() if key and value))
            text = "\n".join(rows)
        elif extension == ".docx":
            with zipfile.ZipFile(file) as archive:
                text = _word_text(archive)
        elif extension == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise ValueError("document_pdf_invalid")
            document = PdfReader(file)
            total = len(document.pages)
            maximum = min(total, 40)
            complete = total <= 40
            pages, metadata = [], []
            for page in range(1, maximum + 1):
                page_text = document.pages[page - 1].extract_text() or ""
                objects = document.pages[page - 1].get('/Resources', {}).get('/XObject', {})
                if hasattr(objects, 'get_object'):
                    objects = objects.get_object()
                # У скана бывает старый скрытый OCR-слой: наличие слов ещё не
                # означает, что он сохранил столбцы или правильно прочитал числа.
                large_raster = any(item.get_object().get('/Subtype') == '/Image'
                                   and item.get_object().get('/Width', 0) * item.get_object().get('/Height', 0) > 500000
                                   for item in objects.values())
                if len(re.findall(r"\w+", page_text)) < 8 or large_raster:
                    prefix = root / f"page-{page}"
                    _run([_command("pdftoppm"), "-f", str(page), "-l", str(page), "-r", "220", "-png", "-singlefile", str(file), str(prefix)])
                    page_text = _scan_text(prefix.with_suffix(".png"), page, metadata)
                    pages.append(page_text)
                    continue
                pages.append(_join_visual_lines(page_text))
            text = compose_pages(pages, metadata, describe_table)
        elif extension in {".png", ".jpg", ".jpeg"}:
            metadata = []
            text = compose_pages([_scan_text(file, metadata=metadata)],metadata,describe_table)
        else:
            raise ValueError("document_format_unsupported")
    if not text.strip():
        raise ValueError("document_text_empty")
    # complete обозначает, что прочитаны все страницы, а не уверенность в таблице.
    # STRUCTURE_UNCERTAIN отдельно закрывает весь этот документ при индексации.
    if len(text) > 200000:
        text, complete = text[:200000], False
    # Производный кэш приватный; из web/static он не раздаётся.
    content = {"text": text, "complete": complete, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    # Параллельные снимки могут извлекать один файл одновременно.
    # У каждого задания своё временное имя, итоговая замена атомарна.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CACHE,
                                     prefix=fingerprint + "-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(content, handle, ensure_ascii=False)
    try:
        temporary.replace(cache)
    finally:
        temporary.unlink(missing_ok=True)
    return text, complete

`````

## tests/test_site_search_real_materials.py

SHA-256: 1d481e7f570e25ca3ea70eea9998132393e755759ff710fb0aea518c351893b2

`````python
"""Регрессии, найденные в настоящих ЧаВо, каталоге и инструкциях.

Малые независимые примеры не зависят от наличия личной папки Downloads.
"""
import io
import unittest
import zipfile
from xml.sax.saxutils import escape

from ai_core.site_documents import _word_text
from ai_core.site_search import ClosedSiteIndex, Principal, terms
from ai_core.site_search_conditions import atomic_observation, rules
from ai_core.site_search_language import supplies_facet
from ai_core.site_search_reasoning import lookup_question

ORIGIN = 'http://127.0.0.1:3040'
READER = Principal('reader', 'staff')


def document(text, title='Инструкция', identifier='rules'):
    return {'id': identifier, 'revision': 'v1', 'title': title, 'kind': 'document',
            'url': ORIGIN + '/files/' + identifier, 'access': {'scope': 'workspace'},
            'sections': [{'heading': title, 'text': text}]}


def index_for(*records):
    index = ClosedSiteIndex([ORIGIN])
    index.publish(list(records), index.begin('regression'))
    return index


def word_text(paragraphs):
    buffer = io.BytesIO()
    namespace = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    body = ''.join('<w:p><w:r><w:t>' + escape(text) + '</w:t></w:r></w:p>' for text in paragraphs)
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{namespace}"><w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(buffer) as archive:
        return _word_text(archive)


class RealMaterialRegressionTests(unittest.TestCase):
    def test_question_answer_pairs_keep_only_the_relevant_answer(self):
        text = word_text(['Частые вопросы', '- Как упаковывают книги?', '- Книги кладут в бумажные пакеты.',
                          '- Что делать при повреждении книги?', '- Если книга повреждена, обратитесь к библиотекарю.'])
        result = index_for(document(text)).search('Как упаковывают книги?', READER)
        self.assertEqual(result['status'], 'answer')
        self.assertIn('бумажные пакеты', result['text'])
        self.assertNotIn('библиотекарю', result['text'])
        self.assertNotIn('Частые вопросы', result['text'])

    def test_question_without_answer_does_not_consume_the_next_question(self):
        text = word_text(['- Где хранить книгу?', '- Когда выдавать книгу?', '- По расписанию.'])
        self.assertNotIn('# Где хранить книгу?', text)
        self.assertIn('- Где хранить книгу?', text)

    def test_dash_rule_does_not_cut_off_actions_at_a_later_comma(self):
        text = 'Если ошибка выявлена до закрытия смены — продавец проверяет журнал, затем исправляет запись.'
        parsed = rules(text, ClosedSiteIndex._sentences, None, supplies_facet)
        self.assertEqual(parsed[0].condition, 'ошибка выявлена до закрытия смены')
        self.assertIn('затем исправляет запись', parsed[0].body)

    def test_temporal_opposites_and_uncertainty(self):
        atom = 'ошибка выявлена до закрытия смены'
        examples = [('Ошибка выявлена до закрытия смены.', (True, True)),
                    ('Ошибка выявлена после закрытия смены.', (True, False)),
                    ('Не знаю, ошибка выявлена до закрытия смены или после.', (True, None)),
                    ('Ошибка выявлена не после закрытия смены.', (True, None))]
        for text, expected in examples:
            with self.subTest(text=text):
                self.assertEqual(atomic_observation(atom, text, terms), expected)

    def test_temporal_event_cannot_be_taken_from_an_unrelated_operation(self):
        self.assertEqual(atomic_observation('ошибка выявлена до закрытия смены',
                                           'Ошибка выявлена после проверки склада.', terms), (False, None))

    def test_local_temporal_reference_is_self_contained(self):
        question = 'Смена уже закрыта, после этого нашли ошибку. Кто оформляет исправление?'
        self.assertEqual(lookup_question(question), question)
        self.assertEqual(atomic_observation('ошибка выявлена после закрытия смены', question, terms), (True, True))

    def test_new_named_subject_keeps_priority_over_introductory_fact(self):
        self.assertEqual(lookup_question('Ошибка в чеке. Кто принимает заказ Альтаир?'), 'Кто принимает заказ Альтаир?')

    def test_before_after_returns_the_matching_role_only(self):
        text = '# Действия при ошибке\nЕсли ошибка выявлена до закрытия смены — продавец проверяет журнал и исправляет запись. '
        text += 'Если ошибка выявлена после закрытия смены — администратор оформляет акт.'
        index = index_for(document(text, 'Исправление ошибки'))
        for when, expected, unwanted in [('до', 'продавец', 'администратор'), ('после', 'администратор', 'продавец')]:
            result = index.search(f'Ошибка выявлена {when} закрытия смены. Кто её исправляет?', READER, when)
            self.assertEqual(result['status'], 'answer', result)
            self.assertIn(expected, result['text'])
            self.assertNotIn(unwanted, result['text'])

    def test_price_question_does_not_return_material(self):
        index = index_for(document('Металлический держатель для книг.', 'Держатель Сапсан'))
        for qualifier in ('сейчас', 'сегодня', 'примерно'):
            self.assertEqual(index.search(f'Сколько {qualifier} стоит держатель Сапсан?', READER, qualifier)['status'], 'not_found')

    def test_packaging_enumeration_keeps_all_options(self):
        index = index_for(document('# Варианты упаковки книг\nКниги кладут в бумажные пакеты. Также доступны картонные коробки.'))
        result = index.search('В каких пакетах можно получить книги?', READER)
        self.assertEqual(result['status'], 'answer')
        self.assertIn('бумажные пакеты', result['text'])
        self.assertIn('картонные коробки', result['text'])

    def test_unrelated_product_condition_does_not_override_exact_faq(self):
        faq = document('# Минимальное взвешивание чая\nМинимальное взвешивание чая — 70 г.', 'Частые вопросы')
        item = document('# Весовой чай\nЕсли напиток остыл, его подогревают.', 'Каталог', 'catalog')
        result = index_for(faq, item).search('Сколько минимально можно купить чая вразвес?', READER)
        self.assertEqual(result['status'], 'answer', result)
        self.assertIn('70 г', result['text'])


if __name__ == '__main__':
    unittest.main()

`````

## evaluation/closed_search/original_theory_20261029.json

SHA-256: 8e946bf7f39335841577c84a236a83fb04ff17514b6da8463f7bc32414665304

`````json
{
  "description": "Ожидания составлены по исходной теории пользовательского архива до запуска поиска. Это проверка содержания учебных тем, не юридическая проверка правил и не приёмка сканов.",
  "archive": "C:/Users/KROLL/Downloads/tea-app-main (19).zip",
  "cases": [
    {"id":"original-01","q":"Оплата по QR-коду прошла через терминал. С какой суммой её сравнивать?","sources":["route:route_24"],"has":["сложить","Картой"],"quote":"При оплатах по QR-коду через терминал эта сумма идёт отдельной строкой в сводном чеке — её необходимо сложить с суммой по карте и сравнить с графой «Картой» в базе."},
    {"id":"original-02","q":"Где в базе отражаются оплаты по QR-коду через монитор?","sources":["route:route_24"],"has":["По QR-коду","больше нигде"],"absent":["необходимо сложить"],"quote":"Оплаты по QR-коду через монитор отражаются в базе в графе «По QR-коду» и больше нигде не учитываются."},
    {"id":"original-03","q":"Что делать при расхождении сумм безналичных платежей?","sources":["route:route_24"],"has":["Контрольная лента","История","Возврат"],"quote":"При расхождении снимается отчёт «Контрольная лента» (меню-6-1-1) и открывается раздел «История» в кассе. Каждая безналичная операция сверяется до нахождения ошибки. По ошибочному чеку делается «Возврат» и пробивается правильный чек, после чего сводный чек снимается заново."},
    {"id":"original-04","q":"Ошибка в чеке выявлена после закрытия смены. Кто её исправляет?","sources":["route:route_24","route:route_25"],"has":["администратор","чек коррекции"],"absent":["продавец находит"],"quote":"Если ошибка выявлена после закрытия смены — администратор формирует чек коррекции."},
    {"id":"original-05","q":"Ошибка выявлена до закрытия смены. Что делать продавцу?","sources":["route:route_25"],"has":["История","правильный"],"absent":["администратор формирует"],"quote":"Если ошибка выявлена до закрытия смены — продавец находит её через раздел «История» в кассе и исправляет: отменяет некорректный чек и пробивает правильный."},
    {"id":"original-06","q":"Куда сдавать излишки наличных и как указывать их сумму?","sources":["route:route_25"],"has":["вместе с выручкой","отдельно"],"quote":"Если выявлены излишки — они сдаются вместе с выручкой. Сумма излишков указывается отдельно."},
    {"id":"original-07","q":"В каком порядке готовить несколько напитков с сахаром и без?","sources":["route:route_17"],"has":["чёрный кофе с сахаром","молочный с сахаром","чёрный без сахара","молочный без сахара"],"quote":"Напитки готовятся строго в следующем порядке: сначала чёрный кофе с сахаром, затем молочный с сахаром, после — чёрный без сахара, последним — молочный без сахара."},
    {"id":"original-08","q":"Какую допродажу обязательно предлагать к кофе с собой?","sources":["route:route_18"],"has":["шоколадка","единственная обязательная"],"quote":"К кофе с собой предлагается шоколадка (маленькие плитки 10–25 г: молочный, тёмный, горький) — единственная обязательная допродажа."},
    {"id":"original-09","q":"Для чего нужны сиденья, если все напитки только на вынос?","sources":["route:route_19"],"has":["ожидания заказа"],"quote":"Посадочных мест нет — имеющиеся сиденья предназначены для ожидания заказа."},
    {"id":"original-10","q":"Какой объём напитка выбирать по умолчанию?","sources":["route:route_16"],"has":["по умолчанию не делается ничего"],"quote":"Затем уточняются объём (100, 250 или 450 мл — по умолчанию не делается ничего) и количество сахара."},
    {"id":"original-11","q":"На сколько процентов больше заказанного насыпают чай при взвешивании?","sources":["route:route_15"],"has":["7–12%"],"absent":["скидку 15%"],"quote":"При взвешивании насыпаем в пакет на 7–12% больше заказанного."},
    {"id":"original-12","q":"Почему слабоферментированные чаи хранятся в холодильнике?","sources":["route:route_14"],"has":["замедляет окисление"],"quote":"Низкая температура замедляет окисление."},
    {"id":"original-13","q":"Какая установлена норма расхода салфеток и крышек?","sources":["route:route_21"],"has":["не установлено","здравым смыслом"],"quote":"Норм расхода по трубочкам, крышкам и салфеткам не установлено — руководствуемся здравым смыслом."},
    {"id":"original-14","q":"Какой штраф в рублях за отсутствие допродажи?","status":"not_found","quote":"В материалах нет установленного штрафа в рублях. Не подставлять цены прикассовых товаров."},
    {"id":"original-15","q":"В какой срок администратор должен сформировать чек коррекции?","status":"not_found","quote":"Срок формирования чека коррекции в исходной теории не указан."},
    {"id":"original-16","q":"Сколько минут длится приготовление латте?","status":"not_found","quote":"Время приготовления латте в исходной теории не указано."}
  ],
  "holdout": [
    {"id":"original-h01","q":"QR оплатили через монитор, в какой графе базы искать платёж?","sources":["route:route_24"],"has":["По QR-коду","больше нигде"],"absent":["необходимо сложить"]},
    {"id":"original-h02","q":"Смена уже закрыта, после этого нашли ошибку в чеке. Кто оформляет исправление?","sources":["route:route_24","route:route_25"],"has":["администратор","чек коррекции"],"absent":["продавец находит"]},
    {"id":"original-h03","q":"Крышки и салфетки: сколько положено расходовать по норме?","sources":["route:route_21"],"has":["не установлено","здравым смыслом"]},
    {"id":"original-h04","q":"Сколько минут занимает приготовление капучино?","status":"not_found"}
  ]
}

`````

## evaluation/closed_search/original_theory_probe.py

SHA-256: d8aa2260f6be22ba189d3ef7358947fa66b62603810800aa7cad9239516413c4

`````python
"""Независимые вопросы к неизменённой теории из пользовательского ZIP.

Из архива читаются только два известных файла учебных тем. Профили,
результаты тестов, ответы на тесты и переписка в поисковый набор не попадают.
"""
import argparse
import hashlib
import json
import time
import zipfile
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / '.runtime/closed-search-lab-20261028'
SPEC = Path(__file__).with_name('original_theory_20261029.json')
ORIGIN = 'http://127.0.0.1:3040'


def load_sources(archive):
    """Сохраняем исходный текст и его происхождение, без синтетических добавок."""
    records, hashes = [], {}
    with zipfile.ZipFile(archive) as bundle:
        for name, kind in [('tea_hub_dynamic_route_v2', 'route'),
                           ('tea_hub_dynamic_basics_v2', 'basic')]:
            entry = 'tea-app-main/data/' + name + '.json'
            raw = bundle.read(entry)
            hashes[entry] = hashlib.sha256(raw).hexdigest()
            content = json.loads(raw)
            modules = content if kind == 'route' else [module for group in content for module in group.get('modules', [])]
            for item in modules:
                sections = [{'heading': item.get('h' + str(n)) or item['title'],
                             'text': item['t' + str(n)]} for n in (1, 2, 3) if item.get('t' + str(n))]
                public = {'id': kind + ':' + item['id'], 'title': item['title'], 'sections': sections}
                route = '/tasks?tab=edu&routeId=' if kind == 'route' else '/closed-search-material?basicId='
                records.append({**public, 'revision': hashlib.sha256(json.dumps(public, ensure_ascii=False).encode()).hexdigest(),
                                'url': ORIGIN + route + item['id'], 'kind': 'lesson', 'authority': 'reference',
                                'status': 'published', 'access': {'scope': 'workspace'}})
    return records, hashes


def check(case, result):
    errors = []
    if result['status'] != case.get('status', 'answer'):
        errors.append('status:' + result['status'])
    for phrase in case.get('has', []):
        if phrase.casefold() not in result['text'].casefold():
            errors.append('missing:' + phrase)
    for phrase in case.get('absent', []):
        if phrase.casefold() in result['text'].casefold():
            errors.append('unwanted:' + phrase)
    if case.get('sources') and not any(row['id'] in case['sources'] for row in result['evidence']):
        errors.append('source')
    if result['status'] not in {'answer', 'partial'} and (result['links'] or result['evidence']):
        errors.append('unexpected-evidence')
    if len(result['text']) > 900:
        errors.append('answer-too-long')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='original-theory')
    parser.add_argument('--holdout', action='store_true')
    args = parser.parse_args()
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    records, hashes = load_sources(spec['archive'])
    index = ClosedSiteIndex([ORIGIN])
    index.publish(records, index.begin('original-theory'))
    rows = []
    for case in spec['holdout' if args.holdout else 'cases']:
        result = index.search(case['q'], Principal('closed_lab_a', 'staff'), case['id'])
        errors = check(case, result)
        rows.append({'id': case['id'], 'question': case['q'], 'result': result, 'errors': errors, 'pass': not errors})
    report = {'source': 'unchanged-user-archive-theory', 'source_count': len(records), 'entry_sha256': hashes,
              'spec_sha256': hashlib.sha256(SPEC.read_bytes()).hexdigest(), 'total': len(rows),
              'passed': sum(row['pass'] for row in rows), 'cases': rows}
    destination = LAB / (args.label + '-' + str(time.time_ns()) + '.json')
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(destination), 'total': report['total'], 'passed': report['passed'],
                      'failures': [{'id': row['id'], 'errors': row['errors'], 'text': row['result']['text']}
                                   for row in rows if not row['pass']]}, ensure_ascii=False))
    return 0 if report['total'] == report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise SystemExit('Original theory probe failed: ' + type(error).__name__) from error

`````

## evaluation/closed_search/real_materials_20261029.json

SHA-256: e44b739505c4ad23313092745c20257bd9136bf57c7ec4d5e4878a7525d4b167

`````json
{
  "description":"Вопросы и ожидаемые факты зафиксированы по реальным файлам пользователя до первого запуска поиска. CSV — исторический снимок 18.06.2026, не текущие цены или наличие.",
  "cases":[
    {"id":"real-01","q":"В каких пакетах можно получить весовой чай?","has":["бумажные пакеты","фольгированные","дой-пак","стеклянную банку"],"source_prefix":"real_faq"},
    {"id":"real-02","q":"Почему премиальный чай держат в холодильнике?","has":["окисления","свежим"],"source_prefix":"real_faq"},
    {"id":"real-03","q":"Сколько минимально можно купить чая вразвес?","has":["50 г","погрешность"],"source_prefix":"real_faq"},
    {"id":"real-04","q":"Можно здесь посидеть и попить кофе?","has":["только на вынос","не предусмотрены посадочные места"],"source_prefix":"real_faq"},
    {"id":"real-05","q":"Можно оплатить переводом на карту?","has":["не используем","QR-коду"],"source_prefix":"real_faq"},
    {"id":"real-06","q":"Свечки входят в комплект подставки Хайвэй?","has":["Свечки","не входят"],"source_prefix":"real_products"},
    {"id":"real-07","q":"Из какого материала подставка Хайвэй?","has":["Металлическая"],"absent":["Стеклянная"],"source_prefix":"real_products"},
    {"id":"real-08","q":"Из какого материала подставка Звездочка?","has":["Стеклянная"],"absent":["Металлическая"],"source_prefix":"real_products"},
    {"id":"real-09","q":"Какой состав у медового мусса Dolche Vita с миндалем?","has":["Мед натуральный","дробленого жареного миндаля"],"absent":["грецкого ореха"],"source_prefix":"real_products"},
    {"id":"real-10","q":"Какой состав у медового мусса Dolche Vita с грецким орехом?","has":["дробленого грецкого ореха"],"absent":["жареного миндаля"],"source_prefix":"real_products"},
    {"id":"real-11","q":"Сколько сейчас стоит подставка Хайвэй?","status":"not_found"},
    {"id":"real-12","q":"Есть ли сейчас подставка Звездочка в наличии в магазине?","status":"not_found"}
  ],
  "holdout":[
    {"id":"real-h01","q":"Подставка Хайвэй продаётся со свечками?","has":["не входят"],"source_prefix":"real_products"},
    {"id":"real-h02","q":"Что входит в медовый мусс Dolche Vita с клюквой?","has":["Мёд натуральный, клюква"],"source_prefix":"real_products"},
    {"id":"real-h03","q":"Почему нельзя купить совсем мало чая вразвес?","has":["50 г","погрешность"],"source_prefix":"real_faq"},
    {"id":"real-h04","q":"Какой срок гарантии на подставку Хайвэй?","status":"not_found"}
  ]
}

`````

## evaluation/closed_search/prepare_real_materials.py

SHA-256: e05ea9584ace8337b9c769e5a407be8ac7c5e7e2db9bac6649a5de04117100a5

`````python
"""Подготовка проверяемых локальных копий двух CSV, без изменения оригиналов.

Каждая строка остаётся отдельным разделом. Часть содержит не более 50 товаров
и 70 000 символов, поэтому большой CSV не обрезается общим лимитом документа.
Порядок строк и их исходные значения сохраняются в manifest.json.
"""
import csv
import hashlib
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / '.runtime/closed-search-lab-20261028/real-materials'
DOWNLOADS = Path('C:/Users/KROLL/Downloads')
FILES = ['products_export_18-06-2026.csv', 'products_export_18-06-2026 (1).csv']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    DEST.mkdir(exist_ok=True)
    manifest = {'snapshot_date': '2026-06-18', 'documents': [], 'inputs': [], 'rows': 0,
                'note': 'Локальная проверка архивного снимка; актуальность правил, цен и остатков не подтверждена.'}
    for file_index, name in enumerate(FILES, 1):
        raw = (DOWNLOADS / name).read_bytes()
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), delimiter=';')
        required = {'Код', 'Наименование', 'Описание', 'Скрыт'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Unexpected product columns')
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError('CSV row width mismatch')
        manifest['inputs'].append({'name': name, 'sha256': digest(raw), 'rows': len(rows), 'columns': reader.fieldnames})
        batch, origins, part = [], [], 0

        def save():
            nonlocal batch, origins, part
            if not batch:
                return
            part += 1
            identifier = f'real_products_{file_index}_{part:03d}'
            filename = f'Товары_18-06-2026_выгрузка_{file_index}_часть_{part:03d}.md'
            content = '\n\n'.join(batch).encode('utf-8')
            (DEST / filename).write_bytes(content)
            manifest['documents'].append({'id': identifier, 'name': filename, 'sha256': digest(content),
                                          'input': name, 'rows': origins, 'kind': 'catalog-copy'})
            batch, origins = [], []

        for number, row in enumerate(rows, 2):
            if row['Скрыт'] not in {'Да', 'Нет'}:
                raise ValueError('Unknown hidden-product flag')
            if row['Скрыт'] == 'Да':
                continue
            title = row['Наименование'].strip()
            if not title or '\n' in title or '\r' in title:
                raise ValueError('Invalid product title')
            # Административные поля не становятся ответом покупателю. Полное
            # происхождение и код хранятся в закрытом журнале подготовки.
            description = row['Описание'].strip()
            block = '# ' + title + '\n' + (description or 'Описание товара в выгрузке отсутствует.')
            if len(block) > 70000:
                raise ValueError('One product exceeds the document budget')
            if batch and (len(batch) >= 50 or sum(map(len, batch)) + len(block) + 2 * len(batch) > 70000):
                save()
            batch.append(block)
            origins.append({'row': number, 'code': row['Код'], 'title': title,
                            'description_sha256': digest(description.encode('utf-8'))})
            manifest['rows'] += 1
        save()
    original = DOWNLOADS / 'ЧаВо покупателей.doc'
    converted = DEST.parent / 'ЧаВо покупателей.docx'
    if not converted.exists():
        raise ValueError('Convert the original Word document locally, read-only, before preparing')
    faq = converted.read_bytes()
    (DEST / converted.name).write_bytes(faq)
    manifest['inputs'].append({'name': original.name, 'sha256': digest(original.read_bytes()), 'conversion': 'Word read-only to DOCX; macros disabled'})
    manifest['documents'].append({'id': 'real_faq', 'name': converted.name, 'sha256': digest(faq),
                                  'input': original.name, 'kind': 'word-conversion'})
    (DEST / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'documents': len(manifest['documents']), 'rows': manifest['rows'], 'path': str(DEST)}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, csv.Error) as error:
        raise SystemExit('Real material preparation failed: ' + type(error).__name__) from error

`````

## evaluation/closed_search/real_materials_probe.py

SHA-256: eec64533ca9e899dcc3a251a826cbbc0cbf24ce6d871abec4d62353b6385c038

`````python
"""Проверяет настоящие документы тем же извлечением и поиском, что использует API."""
import argparse
import base64
import hashlib
import json
import time
from pathlib import Path

from ai_core.site_documents import extract_document
from ai_core.site_search import ClosedSiteIndex, Principal
from evaluation.closed_search.original_theory_probe import check, LAB, ORIGIN

SPEC = Path(__file__).with_name('real_materials_20261029.json')
DIRECTORY = LAB / 'real-materials'


def records():
    manifest = json.loads((DIRECTORY / 'manifest.json').read_text(encoding='utf-8'))
    sources = []
    for doc in manifest['documents']:
        raw = (DIRECTORY / doc['name']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != doc['sha256']:
            raise ValueError('Prepared source changed')
        text, complete = extract_document('data:application/octet-stream;base64,' + base64.b64encode(raw).decode(), doc['name'])
        if not complete:
            raise ValueError('Real material is truncated')
        sources.append({'id': 'document:' + doc['id'], 'title': doc['name'], 'revision': doc['sha256'],
                        'url': ORIGIN + '/tasks?tab=docs&documentId=' + doc['id'],
                        'kind': 'document', 'authority': 'reference', 'status': 'published',
                        'access': {'scope': 'workspace'}, 'sections': [{'heading': doc['name'], 'text': text}]})
    return sources, manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--holdout', action='store_true')
    parser.add_argument('--label', default='real-materials')
    args = parser.parse_args()
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    sources, manifest = records()
    start = time.perf_counter()
    index = ClosedSiteIndex([ORIGIN])
    index.publish(sources, index.begin('real-materials'))
    publish_ms = round((time.perf_counter() - start) * 1000, 2)
    rows = []
    for case in spec['holdout' if args.holdout else 'cases']:
        started = time.perf_counter()
        result = index.search(case['q'], Principal('closed_lab_b', 'staff'), case['id'])
        errors = check(case, result)
        if case.get('source_prefix') and not any(item['id'].startswith('document:' + case['source_prefix']) for item in result['evidence']):
            errors.append('source')
        rows.append({'id': case['id'], 'question': case['q'], 'result': result, 'errors': errors,
                     'ms': round((time.perf_counter() - started) * 1000, 2), 'pass': not errors})
    report = {'inputs': manifest['inputs'], 'spec_sha256': hashlib.sha256(SPEC.read_bytes()).hexdigest(),
              'source_count': len(sources), 'product_rows': manifest['rows'], 'passages': len(index.passages),
              'publish_ms': publish_ms, 'total': len(rows), 'passed': sum(row['pass'] for row in rows), 'cases': rows}
    destination = LAB / (args.label + '-' + str(time.time_ns()) + '.json')
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(destination), 'total': report['total'], 'passed': report['passed'],
                      'failures': [{'id': row['id'], 'errors': row['errors'], 'text': row['result']['text']}
                                   for row in rows if not row['pass']]}, ensure_ascii=False))
    return 0 if report['total'] == report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        raise SystemExit('Real material probe failed: ' + type(error).__name__) from error

`````

## evaluation/closed_search/real_materials_http.cjs

SHA-256: a6ed4aee203829fb55ab46567e45b20d186c1daf6bf8a87674a04d8c5d8d35e0

`````javascript
/* Проверка реальных файлов на изолированном сайте. Оригиналы не изменяются. */
const { fs, path, lab, login, storage, call } = require('./lab_client.cjs');
const { waitSnapshot } = require('./scan_http.cjs');
const { createHash } = require('node:crypto');

async function main() {
    const directory = path.join(lab, 'real-materials');
    const manifest = JSON.parse(await fs.readFile(path.join(directory, 'manifest.json'), 'utf8'));
    const specBytes = await fs.readFile(path.join(__dirname, 'real_materials_20261029.json'));
    const spec = JSON.parse(specBytes);
    const cookies = { admin: await login('closed-admin'), a: await login('closed-a'), b: await login('closed-b') };
    if (!process.argv.includes('--queries-only')) {
        const catalog = await storage('tea_hub_urgent_files_v1', cookies.admin);
        for (const doc of manifest.documents) {
            const raw = await fs.readFile(path.join(directory, doc.name));
            if (createHash('sha256').update(raw).digest('hex') !== doc.sha256) throw new Error('Prepared document changed');
            const data = 'data:application/octet-stream;base64,' + raw.toString('base64');
            const entry = { id: doc.id, name: doc.name, hasSeparateData: true,
                section: 'Реальные материалы — проверка архивного снимка', size: raw.length + ' B',
                date: 'Снимок 18.06.2026; актуальность не подтверждена', searchAuthority: 'reference',
                accessPolicy: { version: 1, visibility: 'restricted', userIds: ['closed_lab_b'] } };
            const prior = catalog.find(item => item.id === doc.id);
            if (prior) {
                if (JSON.stringify(prior) !== JSON.stringify(entry) || await storage('file_data_' + doc.id, cookies.admin) !== data) {
                    throw new Error('Existing real-material copy differs');
                }
            } else {
                await storage('file_data_' + doc.id, cookies.admin, data);
                catalog.push(entry);
            }
        }
        await storage('tea_hub_urgent_files_v1', cookies.admin, catalog);
        console.log(JSON.stringify({ event: 'real-materials-uploaded', documents: manifest.documents.length, productRows: manifest.rows }));
    }
    const ready = await waitSnapshot();
    const rows = [];
    const rateLimitWaits = [];
    const prefix = 'real-http-' + Date.now();
    async function ask(item, actor = 'b', source = undefined) {
        let start = performance.now();
        const body = { chatId: prefix + '-' + item.id + '-' + actor,
            messages: [{ role: 'user', content: item.q }], ...(source ? { documentId: source.replace(/^document:/, '') } : {}) };
        let result = await call('/api/ai-chat', body, cookies[actor]);
        if (result.response.status === 429) {
            const seconds = Number(result.response.headers.get('retry-after')) || 300;
            if (seconds < 0 || seconds > 360) throw new Error('Unexpected rate-limit interval');
            rateLimitWaits.push({ id: item.id, actor, seconds });
            console.log(JSON.stringify({ event: 'respecting-rate-limit', id: item.id, seconds }));
            for (let remaining = seconds * 1000 + 100; remaining > 0; remaining -= Math.min(remaining, 30000)) {
                await new Promise(resolve => setTimeout(resolve, Math.min(remaining, 30000)));
            }
            start = performance.now();
            result = await call('/api/ai-chat', body, cookies[actor]);
        }
        const { response, value } = result;
        const answer = value.output_text || '';
        const links = value.sourceLinks || [];
        const errors = [];
        if (response.status !== 200) errors.push('http:' + response.status);
        if (value.search_status !== (item.status || 'answer')) errors.push('status:' + value.search_status);
        for (const phrase of item.has || []) if (!answer.toLowerCase().includes(phrase.toLowerCase())) errors.push('missing:' + phrase);
        for (const phrase of item.absent || []) if (answer.toLowerCase().includes(phrase.toLowerCase())) errors.push('unwanted:' + phrase);
        if (item.source_prefix && !links.some(link => link.url.includes('documentId=' + item.source_prefix))) errors.push('source');
        if (!['answer', 'partial'].includes(value.search_status) && links.length) errors.push('unexpected-links');
        if (answer.length > 900) errors.push('answer-too-long');
        rows.push({ id: item.id, actor, question: item.q, selectedSource: source, answer, links,
            status: value.search_status, ms: Math.round(performance.now() - start), errors, pass: !errors.length });
    }
    // У ЧаВо есть параллельные тексты в исходных уроках. Проверка оригинального
    // файла явно выбирает его; товары проверяются общим поиском всего каталога.
    for (const item of [...spec.cases, ...spec.holdout]) {
        await ask(item, 'b', item.source_prefix === 'real_faq' ? 'document:real_faq' : undefined);
    }
    await ask({ id: 'acl-faq', q: 'Сколько минимально можно купить чая вразвес?', status: 'not_found' }, 'a', 'document:real_faq');
    await ask({ id: 'acl-product', q: 'Свечки входят в комплект подставки Хайвэй?', status: 'not_found' }, 'a');
    const report = { ready, rateLimitWaits, specSHA256: createHash('sha256').update(specBytes).digest('hex'),
        total: rows.length, passed: rows.filter(row => row.pass).length, cases: rows };
    const output = path.join(lab, 'real-materials-http-' + Date.now() + '.json');
    await fs.writeFile(output, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ file: output, total: report.total, passed: report.passed, failures: rows.filter(row => !row.pass) }));
    if (report.total !== report.passed) process.exitCode = 1;
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });

`````

## evaluation/closed_search/verify_lab.cjs

SHA-256: 7794f328c873ec77d1479010af6862a50d965deb7c503a010948e9e256a95038

`````javascript
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
    const linkFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'document_links_20261029.json'), 'utf8'));
    const realManifest = JSON.parse(await fs.readFile(path.join(lab, 'real-materials/manifest.json'), 'utf8'));
    const expectedDocuments = 9 + reasoningFixtures.documents.length + conditionFixtures.documents.length + structureFixtures.documents.length + structureAssets.length + scanFixtures.documents.length + layoutFixtures.documents.length + qualityFixtures.documents.length + multipageFixtures.documents.length + crossFixtures.documents.length + linkFixtures.documents.length + realManifest.documents.length;
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

`````

## evaluation/closed_search/soak_http.cjs

SHA-256: e0b0e3eca912d2d522d92a67517c4c7d663c7353f3b4648a75f94067908cdfd4

`````javascript
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
const includeDocumentLinks = process.argv.includes('--document-links');
const includeRealMaterials = process.argv.includes('--real-materials');
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
        if (includeCrossDocuments || includeDocumentLinks) {
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
        if (includeDocumentLinks) {
            const bytes=await fs.readFile(path.join(__dirname,'document_links_20261029.json'));
            const spec=JSON.parse(bytes.toString('utf8'));
            report.linkSpecSHA256=createHash('sha256').update(bytes).digest('hex');
            base.sources.push(...spec.documents.map(item=>({id:item.id,revision:'cross-v1',kind:'document',authority:'official',title:item.name,
                url:origin+'/materials/'+item.id,access:item.allowedUserIds?{scope:'restricted',users:item.allowedUserIds}:{scope:'workspace'},
                sections:[{heading:item.name,text:item.text}]})));
            for(const group of ['cases','dialogue','holdout'])additionalCases.push(...spec[group].map(item=>({...item,id:'links-'+item.id,
                chat:group==='dialogue'?'links-long':item.chat?'links-'+item.chat:undefined,status:item.status||'answer',has:item.has||[],
                source:undefined,selected:item.source,sources:item.evidence_ids,principalId:'closed_lab_'+(item.user||'a')})));
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
        if (includeRealMaterials) {
            const directory = path.join(lab, 'real-materials');
            const manifestBytes = await fs.readFile(path.join(directory, 'manifest.json'));
            const manifest = JSON.parse(manifestBytes);
            report.realManifestSHA256 = createHash('sha256').update(manifestBytes).digest('hex');
            report.realProductRows = manifest.rows;
            for (const doc of manifest.documents) {
                const bytes = await fs.readFile(path.join(directory, doc.name));
                if (createHash('sha256').update(bytes).digest('hex') !== doc.sha256) throw new Error('Real source changed');
                base.sources.push({ id: 'document:' + doc.id, revision: doc.sha256, kind: 'document', title: doc.name,
                    url: origin + '/tasks?tab=docs&documentId=' + doc.id,
                    access: { scope: 'restricted', users: ['closed_lab_b'] }, sections: [],
                    file_name: doc.name, file_data: 'data:application/octet-stream;base64,' + bytes.toString('base64') });
            }
            const bytes = await fs.readFile(path.join(__dirname, 'real_materials_20261029.json'));
            report.realSpecSHA256 = createHash('sha256').update(bytes).digest('hex');
            const spec = JSON.parse(bytes);
            additionalCases.push(...[...spec.cases, ...spec.holdout].map(item => ({ ...item,
                status: item.status || 'answer', has: item.has || [], source: item.source_prefix,
                selected: item.source_prefix === 'real_faq' ? 'document:real_faq' : undefined,
                principalId: 'closed_lab_b' })));
            additionalCases.push({ id: 'real-private-faq', q: 'Сколько минимально можно купить чая вразвес?',
                selected: 'document:real_faq', principalId: 'closed_lab_a', status: 'not_found', has: [], noLinks: true });
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
        for (const file of ['site_search.py','site_search_language.py','site_search_conditions.py','site_search_structure.py','site_search_reasoning.py','site_search_links.py','site_search_runtime.py','site_search_store.py','site_documents.py','site_scan_tables.py','site_scan_layout.py','site_scan_quality.py','site_search_api.py']) {
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

`````

