# Полный код: длинные диалоги и ссылки ВатэсAi

29.10.2026. Полные файлы без сокращений. Отчёт: CLOSED_SEARCH_DOCUMENT_LINKS_REPORT_2026_10_29.md.

## ai_core/site_search.py

SHA-256: 28c5958aa286eacd29464f5bcafb9007484248e25f4a4d4f0bf496c6fadc605c

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
        reference = bool(re.search(r"\b(это|этого|этой|там|тогда|него|нее|ней|этом|он|она|они)\b", normal(question))) or (normal(question).startswith("а ") and len(raw_words) <= 3)
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
        self._remember(sessions,scope,[best.source['id']],self._query_terms(question)[:20] if not context_used else words[:24],facet=facet)
        # Ограничиваем производную память; она не становится корпусом для обучения.
        if len(sessions) > 1000:
            oldest = min(sessions, key=lambda key: sessions[key]["time"])
            del sessions[oldest]
        return {"status": "answer", "text": answer, "links": links, "evidence": evidence, "context_used": context_used,
                "timing_ms": round((time.perf_counter() - started) * 1000, 2)}
````

## ai_core/site_search_links.py

SHA-256: 0c6be86c8af6bfef06c6ef2829d3f1a1e2742e17332b580c076ddfb7cd7c3327

````python
"""Явные ссылки между зарегистрированными документами, без сетевых запросов.

Граф собирается при индексации. Доступ к каждому его узлу повторно проверяет
поиск для конкретного пользователя. Загруженный текст остаётся данными.
"""
from __future__ import annotations

import copy
import re
import uuid
from collections import defaultdict
from urllib.parse import urljoin,urlsplit,parse_qsl,urlencode,unquote
from .site_search_structure import compile_sections, normalize_markup

EDITION = re.compile(r'(?im)^\s*Редакция:\s*([\w-]+(?:\.[\w-]+)*)\s*\.?\s*$')
LINK = re.compile(r'\b(?:см\.?|смотри(?:те)?|согласно)\s+документ(?:у)?\s+(?:«([^»\n]{1,200})»|"([^"\n]{1,200})")\s*,?\s*раздел(?:у)?\s+(\d+(?:\.\d+)*)\s*(?:,\s*редакция\s+([\w-]+(?:\.[\w-]+)*))?\.?',re.I)
LINK_HINT = re.compile(r'\b(?:см\.?|смотри(?:те)?|согласно)\s+документ(?:у)?\b',re.I)
MARKDOWN_LINK = re.compile(r'\b(?:см\.?|смотри(?:те)?|согласно)\s+\[[^\]\n]{1,200}\]\(([^\s)]+)\)\s*(?:,\s*редакция\s+([\w-]+(?:\.[\w-]+)*))?\.?',re.I)


def url_key(value):
    parsed=urlsplit(value)
    return (parsed.scheme.casefold(),parsed.netloc.casefold(),parsed.path,urlencode(sorted(parse_qsl(parsed.query))))


def title_key(value):
    return ' '.join(value.casefold().replace('ё','е').split())


def compile_documents(sources,plain):
    """Разрешаем только точное название и единственный раздел нужной редакции."""
    titles=defaultdict(list)
    editions={}
    compiled={}
    references={}
    urls=defaultdict(list)
    for identifier,source in sources.items():
        if source.get('kind')=='document' and source.get('status','published')=='published':
            urls[url_key(source['url'])].append(identifier)
    # Входной текст не может выдать себя за внутренний указатель графа.
    marker_prefix='VATESDOCREF'+uuid.uuid4().hex+'Z'
    marker_pattern=re.compile(re.escape(marker_prefix)+r'(\d+)X')
    for identifier,source in sources.items():
        if source.get('kind')=='document' and source.get('status','published')=='published':
            titles[title_key(source['title'])].append(identifier)
        prepared=copy.deepcopy(source)
        labels=set()
        refs=[]
        for section in prepared.get('sections',[]):
            raw=normalize_markup(str(section.get('text','')))
            labels.update(match[1] for match in EDITION.finditer(raw))
            raw=EDITION.sub('',raw)
            def replace(match):
                # Непохожий на сноску маркер переживает локальный разбор раздела.
                marker=marker_prefix+str(len(refs))+'X'
                refs.append((match[1] or match[2],match[3],match[4]))
                return marker
            raw=LINK.sub(replace,raw)
            def replace_url(match):
                # URL — указатель на уже зарегистрированный источник, не
                # команда скачать сайт. Подпись ссылки не задаёт права доступа.
                try:
                    url=urljoin(source['url'],match[1])
                    target=urls.get(url_key(url),[])
                    section=re.fullmatch(r'(?:section-|раздел-)?(\d+(?:\.\d+)*)',unquote(urlsplit(url).fragment),re.I)
                except ValueError:
                    return '[STRUCTURE_UNCERTAIN]'
                if len(target)!=1 or not section:
                    return '[STRUCTURE_UNCERTAIN]'
                marker=marker_prefix+str(len(refs))+'X'
                refs.append((sources[target[0]]['title'],section[1],match[2]))
                return marker
            raw=MARKDOWN_LINK.sub(replace_url,raw)
            if LINK_HINT.search(raw):
                raw+='\n[STRUCTURE_UNCERTAIN]'
            section['text']=raw
        editions[identifier]=next(iter(labels)) if len(labels)==1 else None
        compiled[identifier]=compile_sections(prepared,plain)
        references[identifier]=refs

    def expand(identifier,position,trail,budget):
        key=(identifier,position)
        item=compiled[identifier][position]
        structure=copy.deepcopy(item.get('structure') or {})
        markers=list(marker_pattern.finditer(item['text']))
        own=marker_pattern.sub('',item['text']).strip(' .')
        own=(own+'.') if own else ''
        origin={'id':identifier,'revision':sources[identifier]['revision'],'heading':item['heading'],'text':own}
        budget[0]+=1
        if key in trail or len(trail)>=8 or budget[0]>64 or len(markers)>16:
            return own,structure,[],[origin],True
        texts=[own]
        dependencies=[]
        origins=[origin] if own else []
        bad=bool(structure.get('issue'))
        for marker in markers:
            title,section,edition=references[identifier][int(marker[1])]
            targets=titles.get(title_key(title),[])
            if len(targets)!=1:
                bad=True
                continue
            target=targets[0]
            candidates=[i for i,part in enumerate(compiled[target])
                        if re.match(r'^(?:Раздел\s+)?'+re.escape(section)+r'(?:[.\s]|$)',part['heading'],re.I)
                        and not re.match(r'^(?:Раздел\s+)?'+re.escape(section)+r'\.\d',part['heading'],re.I)]
            if len(candidates)!=1 or edition and editions[target]!=edition:
                bad=True
                continue
            text,extra,children,quotes,failed=expand(target,candidates[0],trail+[key],budget)
            dependencies.append({'id':target,'revision':sources[target]['revision'],'periods':extra.get('periods',[])})
            dependencies.extend(children)
            texts.append(text)
            origins.extend(quotes)
            bad|=failed
            if budget[0]>64 or sum(map(len,texts))>32000:
                bad=True
                break
        return ' '.join(text for text in texts if text),structure,dependencies,origins,bad

    result={}
    for identifier,items in compiled.items():
        rows=[]
        for position,item in enumerate(items):
            if not marker_pattern.search(item['text']):
                rows.append(item)
                continue
            text,structure,dependencies,origins,bad=expand(identifier,position,[],[0])
            public=marker_pattern.sub('',item['text']).strip(' .')
            # Ошибочная цепочка не публикует даже частично извлечённые детали.
            if bad:
                rows.append({**item,'text':public or item['heading'],'structure':{**structure,'issue':True,'linked':True}})
                continue
            unique={}
            for value in dependencies:
                if value['id'] not in unique:
                    unique[value['id']]=copy.deepcopy(value)
                else:
                    unique[value['id']]['periods'].extend(value['periods'])
            shared={**structure,'linked':True,'document_dependencies':list(unique.values()),'document_origins':origins}
            rows.append({**item,'text':text,'structure':shared})
            # Запасной фрагмент содержит только общедоступный текст корневого
            # файла. Скрытые слова не участвуют даже в ранжировании запроса.
            rows.append({**item,'text':public or item['heading'],'structure':{**structure,'issue':True,'linked':True,
                         'document_guard':True,'document_dependencies':list(unique.values())}})
        result[identifier]=rows
    return result
````

## ai_core/site_search_runtime.py

SHA-256: 536bb18ce40ef1f6f156b6c80e069a7d5822b4a4b40d5a8b551ed8f78c5617d1

````python
"""Согласует поиск, смену снимков и сохранение в пределах одного процесса."""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

from .site_search import ClosedSiteIndex, Passage, Principal, readable, NOT_READY
from .site_search_store import SearchStore
from .site_search_conditions import rules, atoms
from .site_search_structure import iso_day
from .site_search_language import supplies_facet

CONTEXT_TTL = 30 * 24 * 60 * 60
LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def engine_version() -> str:
    digest = hashlib.sha256()
    for name in ("site_search.py", "site_search_language.py", "site_search_reasoning.py", "site_search_conditions.py", "site_search_structure.py", "site_search_links.py"):
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


class DurableSearch:
    def __init__(self, origins: list[str], path: Path, clock=time.time, extraction_version: str | None = None):
        self.lock = threading.RLock()
        self.index = ClosedSiteIndex(origins, clock=clock, session_ttl=CONTEXT_TTL)
        self.store = SearchStore(path)
        self.restored = False
        self.restore_ms = 0.0
        self.rebuilt = False
        self.extraction_version = extraction_version
        self._restore()

    def _restore(self):
        started = time.perf_counter()
        try:
            saved = self.store.load_index()
            if saved is None:
                return
            generation, snapshot, data = saved
            # После обновления OCR старый извлечённый текст нельзя объявлять
            # актуальным: ждём фонового повторного чтения исходных документов.
            if self.extraction_version is not None and data.get('extraction_version') != self.extraction_version:
                self.index.state = 'unavailable'
                return
            if sorted(self.index.origins) != data.get("origins"):
                self.store.prune_contexts(set())
                raise ValueError("stored_index_origins")
            sources = data["sources"]
            self.index.generation, self.index.snapshot = generation, snapshot
            if data.get("engine") != engine_version():
                # После изменения алгоритма пересобираем поисковые признаки
                # из уже извлечённого текста. OCR повторно не выполняется.
                generation = self.store.begin(snapshot)
                self.index.generation = generation
                self.index.publish(sources, generation, data.get("aliases"))
                if not self.store.save_index(self.index, engine_version(), self.extraction_version):
                    raise ValueError("stored_index_superseded")
                self.rebuilt = True
            else:
                # Метаданные проходят прежнюю валидацию, токены берём с диска.
                self.index.publish([{**item, "sections": []} for item in sources], generation, data.get("aliases"))
                self.index.sources = {item["id"]: item for item in sources}
                passages = []
                for item in data["passages"]:
                    for field in ("words", "title_words"):
                        if not isinstance(item[field], dict) or any(not isinstance(word, str) or not isinstance(count, int) or count <= 0 for word, count in item[field].items()):
                            raise ValueError("stored_index_terms")
                    structure = item.get('structure')
                    if structure is not None and (not isinstance(structure, dict) or not isinstance(structure.get('periods', []), list)):
                        raise ValueError('stored_index_structure')
                    passages.append(Passage(self.index.sources[item["id"]], item["heading"], item["text"], Counter(item["words"]), Counter(item["title_words"]), structure))
                self.index.passages = passages
                self.index.rebuild_lookup()
            self._restore_contexts()
            self.restored = True
        except Exception:
            # Повреждение базы/неподходящий формат не означает разрешение
            # использовать частично прочитанные данные. Экспортёр пришлёт снимок.
            self.index.state = "unavailable"
            self.index.sources, self.index.passages, self.index.sessions = {}, [], {}
            LOGGER.warning("closed_search_restore_failed")
        finally:
            self.restore_ms = round((time.perf_counter() - started) * 1000, 2)

    def _restore_contexts(self):
        sessions = {}
        for scope, context in self.store.contexts():
            try:
                references = context["references"]
                actor = Principal(scope[0], scope[1], scope[2])
                now = self.index.clock()
                valid = isinstance(references, dict) and bool(references) and 0 <= now - context["time"] <= CONTEXT_TTL
                valid = valid and isinstance(context["terms"], list) and len(context["terms"]) <= 24 and all(isinstance(word, str) for word in context["terms"])
                valid = valid and all(identifier in self.index.sources and self.index.sources[identifier]["revision"] == revision
                                      and readable(self.index.sources[identifier], actor, now) for identifier, revision in references.items())
                if valid:
                    sessions[scope] = {"time": context["time"], "generation": self.index.generation,
                                       "terms": context["terms"], "ids": list(references),
                                       **self._condition_context(context, list(references))}
            except (KeyError, TypeError, ValueError):
                continue
        self.index.sessions = sessions
        self.store.prune_contexts(set(sessions))

    def _condition_context(self, context, identifiers):
        """Сохраняем только истинность коротких условий доступного документа."""
        incoming = context.get('condition_facts', {})
        facts = {}
        if isinstance(incoming, dict):
            for identifier in identifiers:
                raw = incoming.get(identifier, {})
                if not isinstance(raw, dict) or not raw:
                    continue
                allowed = {atom for item in self.index.passages if item.source['id'] == identifier
                           for rule in rules(item.text, self.index._sentences, None, supplies_facet) if rule.condition
                           for atom in atoms(rule.condition) if len(atom) <= 240}
                facts[identifier] = {key: value for key, value in list(raw.items())[:64]
                                     if key in allowed and isinstance(value, bool)}
        facet = context.get('facet')
        if facet not in {None, 'cost', 'year', 'recipient', 'duration', 'procedure'}:
            facet = None
        saved = {'condition_facts': facts, 'facet': facet, 'pending_conditions': context.get('pending_conditions') is True}
        focus=context.get('focus_ids',identifiers)
        if isinstance(focus,list) and focus and all(identifier in identifiers for identifier in focus):
            saved['focus_ids']=list(dict.fromkeys(focus))
        subjects=context.get('subjects',[])
        if isinstance(subjects,list) and len(subjects)<=4 and all(isinstance(value,str) and len(value)<=80 for value in subjects):
            saved['subjects']=subjects
        if isinstance(context.get('as_of'), str):
            try:
                saved['as_of'] = iso_day(context['as_of'])
            except ValueError:
                pass
        return saved

    def begin(self, snapshot: str):
        with self.lock:
            if self.index.snapshot == snapshot and self.index.state in {"ready", "processing"}:
                return None
            generation = self.store.begin(snapshot)
            self.index.begin(snapshot)
            self.index.generation = generation
            self.restored = False
            return generation

    def publish(self, sources: list[dict], generation: int, aliases: dict | None = None):
        with self.lock:
            if generation != self.index.generation:
                return False
            try:
                self.index.publish(sources, generation, aliases)
                if not self.store.save_index(self.index, engine_version(), self.extraction_version):
                    raise ValueError("index_save_superseded")
                self._restore_contexts()
                return True
            except Exception:
                self.fail(generation)
                raise

    def fail(self, generation: int):
        with self.lock:
            if generation == self.index.generation:
                self.index.state = "failed"
                self.index.sources, self.index.passages, self.index.sessions = {}, [], {}
            self.store.fail(generation)

    def query(self, question: str, actor: Principal, chat: str, selected: str | None, revisions: dict):
        with self.lock:
            state = self.store.state()
            if state and state[:2] != (self.index.generation, self.index.snapshot):
                return {"status": "processing", "text": NOT_READY, "links": [], "evidence": []}
            result = self.index.search(question, actor, chat, selected, revisions)
            scope = (actor.user_id, actor.role, tuple(sorted(actor.groups)), chat)
            context = self.index.sessions.get(scope)
            persisted = None
            if context:
                # Не переносим в базу случайные личные слова из вопроса:
                # сохраняются только термины, присутствующие в найденных материалах.
                vocabulary = set().union(*(set(item.words) | set(item.title_words) for item in self.index.passages if item.source["id"] in context["ids"]))
                persisted = {"time": context["time"], "terms": [word for word in context["terms"] if word in vocabulary],
                             "references": {identifier: self.index.sources[identifier]["revision"] for identifier in context["ids"]},
                             **self._condition_context(context, context['ids'])}
            try:
                stored = self.store.save_context(scope, persisted, self.index.generation, self.index.clock(), CONTEXT_TTL)
                if result["status"] in {"answer", "partial"} and not stored:
                    return {"status": "processing", "text": NOT_READY, "links": [], "evidence": []}
            except Exception:
                LOGGER.error("closed_search_context_save_failed")
                self.index.sessions.pop(scope, None)
                return {"status": "unavailable", "text": "Не удалось сохранить контекст разговора. Повторите вопрос позже.", "links": [], "evidence": []}
            return result

    def status(self):
        with self.lock:
            return {"state": self.index.state, "sources": len(self.index.sources), "passages": len(self.index.passages),
                    "snapshot": self.index.snapshot, "restored_from_disk": self.restored,
                    "restore_ms": self.restore_ms, "rebuilt_from_text": self.rebuilt, "context_count": len(self.index.sessions)}
````

## tests/test_site_search_document_links.py

SHA-256: afb9cec64e3ed693907138eeec3cf05f647f59ab95a21d93d09cfff2c7557709

````python
"""Граф ссылок, редакции, ACL и сто последовательных вопросов после рестартов."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex,Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.document_links_probe import SPEC,ORIGIN,records,checks

A=Principal('closed_lab_a','staff')
B=Principal('closed_lab_b','staff')


class DocumentLinkTests(unittest.TestCase):
    def setUp(self):
        self.spec=json.loads(SPEC.read_text(encoding='utf-8'))
        self.records=records(self.spec)
        self.index=self.make(self.records)

    def make(self,items):
        index=ClosedSiteIndex([ORIGIN])
        index.publish(items,index.begin('links'))
        return index

    def ask(self,q,actor=A,chat='main',selected=None):
        return self.index.search(q,actor,chat,selected)

    def test_frozen_cases_and_alternates(self):
        for case in self.spec['cases']+self.spec['holdout']:
            with self.subTest(case=case['id']):
                result=self.ask(case['q'],B if case.get('user')=='b' else A,case.get('chat',case['id']),case.get('source'))
                self.assertEqual(checks(case,result),[],result)

    def test_one_hundred_turns_with_restarts_and_other_users(self):
        with tempfile.TemporaryDirectory(prefix='vates-links-') as folder:
            db=Path(folder)/'index.sqlite3'
            runtime=DurableSearch([ORIGIN],db)
            runtime.publish(self.records,runtime.begin('long'))
            manifest={item['id']:item['revision'] for item in self.records}
            for cycle in range(5):
                for position,case in enumerate(self.spec['dialogue']):
                    with self.subTest(cycle=cycle,id=case['id']):
                        if position in {1,9,11,17}:
                            runtime=DurableSearch([ORIGIN],db)
                        result=runtime.query(case['q'],A,'long',None,manifest)
                        self.assertEqual(checks(case,result),[],result)
                        if position==16:
                            for actor,chat in [(B,'long'),(A,'other')]:
                                result=runtime.query('А если упаковка вскрыта?',actor,chat,None,manifest)
                                self.assertEqual(result['status'],'clarify')

    def test_revision_and_acl_changes_invalidate_linked_memory(self):
        with tempfile.TemporaryDirectory(prefix='vates-links-revision-') as folder:
            db=Path(folder)/'index.sqlite3'
            runtime=DurableSearch([ORIGIN],db)
            runtime.publish(self.records,runtime.begin('v1'))
            manifest={item['id']:item['revision'] for item in self.records}
            runtime.query('Сколько хранится товар Нефрит?',A,'same',None,manifest)
            changed=copy.deepcopy(self.records)
            target=next(item for item in changed if item['id']=='link_exception')
            target['revision']='new-bytes'
            target['sections'][0]['text']=target['sections'][0]['text'].replace('2 календарных','5 календарных')
            manifest[target['id']]=target['revision']
            self.assertEqual(runtime.query('Упаковка вскрыта.',A,'same',None,manifest)['status'],'processing')
            runtime.publish(changed,runtime.begin('v2'))
            runtime=DurableSearch([ORIGIN],db)
            self.assertEqual(runtime.query('А если упаковка вскрыта?',A,'same',None,manifest)['status'],'clarify')
            result=runtime.query('Упаковка вскрыта. Сколько хранится товар Нефрит?',A,'same',None,manifest)
            self.assertIn('5 календарных',result['text'])
            target['access']={'scope':'admin'}
            target['revision']='revoked'
            manifest[target['id']]='revoked'
            runtime.publish(changed,runtime.begin('revoked'))
            runtime=DurableSearch([ORIGIN],db)
            result=runtime.query('Упаковка вскрыта. Сколько хранится товар Нефрит?',A,'same',None,manifest)
            self.assertEqual(result['status'],'unavailable')
            self.assertEqual(result['evidence'],[])

    def test_missing_from_current_manifest_cannot_use_dependency(self):
        manifest={item['id']:item['revision'] for item in self.records if item['id']!='link_exception'}
        result=self.index.search('Упаковка вскрыта. Сколько хранится товар Нефрит?',A,current_revisions=manifest)
        self.assertEqual(result['status'],'unavailable')

    def test_ambiguous_title_and_missing_section_fail_closed(self):
        for mode in ['duplicate','section','deleted','archived']:
            with self.subTest(mode=mode):
                data=copy.deepcopy(self.records)
                target=next(item for item in data if item['id']=='link_exception')
                if mode=='duplicate':data.append({**target,'id':'duplicate','url':ORIGIN+'/materials/duplicate'})
                if mode=='section':target['sections'][0]['text']=target['sections'][0]['text'].replace('# 2.','# 5.')
                if mode=='deleted':data.remove(target)
                if mode=='archived':target['status']='archived'
                result=self.make(data).search('Упаковка вскрыта. Сколько хранится товар Нефрит?',A)
                self.assertEqual(result['status'],'unavailable')

    def test_future_dependency_is_not_used_before_effective_date(self):
        data=copy.deepcopy(self.records)
        target=next(item for item in data if item['id']=='link_exception')
        target['sections'][0]['text']=target['sections'][0]['text'].replace('# 2. Хранение товара Нефрит','# 2. Хранение товара Нефрит\nДействует с 01.01.2099')
        index=self.make(data)
        result=index.search('Упаковка вскрыта. Сколько хранится товар Нефрит на 29.10.2026?',A)
        self.assertEqual(result['status'],'unavailable')
        result=index.search('Упаковка вскрыта. Сколько хранится товар Нефрит на 02.01.2099?',A)
        self.assertEqual(result['status'],'answer')
        self.assertIn('2 календарных',result['text'])

    def test_two_sections_of_same_dependency_keep_both_periods(self):
        data=copy.deepcopy(self.records)
        root=next(item for item in data if item['id']=='link_base')
        root['sections'][0]['text']+=' См. документ «Исключения_Нефрит.md», раздел 3, редакция 3.'
        target=next(item for item in data if item['id']=='link_exception')
        target['sections'][0]['text']+='\n# 3. Дополнение\nДействует с 01.01.2099\nТовар Нефрит держат на полке.'
        index=self.make(data)
        result=index.search('Упаковка вскрыта. Сколько хранится товар Нефрит на 29.10.2026?',A)
        self.assertEqual(result['status'],'unavailable')

    def test_transitive_private_dependency_does_not_leak_summary(self):
        data=copy.deepcopy(self.records)
        next(item for item in data if item['id']=='link_chain_end')['access']={'scope':'admin'}
        result=self.make(data).search('Кратко перескажи выбранный файл',A,selected_source='link_chain_base')
        self.assertEqual(result['status'],'unavailable')
        self.assertNotIn('бумажный',str(result))

    def test_unversioned_link_uses_only_unique_registered_document(self):
        data=copy.deepcopy(self.records)
        root=next(item for item in data if item['id']=='link_base')
        root['sections'][0]['text']=root['sections'][0]['text'].replace(', редакция 3','')
        result=self.make(data).search('Упаковка вскрыта. Сколько хранится товар Нефрит?',A)
        self.assertEqual(result['status'],'answer')
        self.assertIn('2 календарных',result['text'])

    def test_malformed_reference_and_literal_internal_word_are_data(self):
        data=copy.deepcopy(self.records)
        root=next(item for item in data if item['id']=='link_base')
        root['sections'][0]['text']='Товар Нефрит хранится 8 дней. См. документ неизвестный. VATESDOCREF99X'
        result=self.make(data).search('Сколько хранится товар Нефрит?',A)
        self.assertEqual(result['status'],'unavailable')

    def test_independent_same_subject_is_conflict_including_lowercase(self):
        data=copy.deepcopy(self.records)
        other={**data[0],'id':'other-rule','title':'Режим товара','url':ORIGIN+'/materials/other-rule',
               'sections':[{'heading':'Нефрит','text':'Если упаковка не вскрыта, товар Нефрит хранится 5 календарных дней. Если упаковка вскрыта, товар Нефрит хранится 1 календарный день.'}]}
        index=self.make(data+[other])
        for subject in ['Нефрит','нефрит']:
            result=index.search('Упаковка не вскрыта. Сколько хранится товар '+subject+'?',A)
            self.assertEqual(result['status'],'conflict')
        result=index.search('Упаковка вскрыта. Сколько хранится товар Нефрит?',A,selected_source='link_base')
        self.assertEqual(result['status'],'answer')
        self.assertIn('2 календарных',result['text'])

    def test_markdown_url_is_resolved_only_inside_registered_sources(self):
        for address,expected in [(ORIGIN+'/materials/link_exception#section-2','answer'),
                                 ('/materials/link_exception#2','answer'),
                                 ('https://external.invalid/materials/link_exception#section-2','unavailable'),
                                 ('http://[invalid#section-2','unavailable')]:
            with self.subTest(address=address):
                data=copy.deepcopy(self.records)
                root=next(item for item in data if item['id']=='link_base')
                root['sections'][0]['text']='Товар Нефрит хранится 8 календарных дней. См. [Уточнение]('+address+'), редакция 3.'
                result=self.make(data).search('Упаковка вскрыта. Сколько хранится товар Нефрит?',A,selected_source='link_base')
                self.assertEqual(result['status'],expected)
                if expected=='answer':self.assertIn('2 календарных',result['text'])

    def test_graph_is_not_recompiled_for_each_question(self):
        with patch('ai_core.site_search.compile_documents',side_effect=AssertionError('graph rebuilt during query')):
            result=self.ask('Упаковка вскрыта. Сколько хранится товар Нефрит?',selected='link_base')
            self.assertEqual(result['status'],'answer')

    def test_excessive_outgoing_links_fail_only_their_document(self):
        data=copy.deepcopy(self.records)
        root=next(item for item in data if item['id']=='link_base')
        root['sections'][0]['text']+=' См. документ «Исключения_Нефрит.md», раздел 2, редакция 3.'*17
        index=self.make(data)
        self.assertEqual(index.search('Сколько хранится товар Нефрит?',A)['status'],'unavailable')
        self.assertIn('диспетчер',index.search('Кто принимает товар Танзанит?',A)['text'])

    def test_broken_storage_reference_does_not_block_delivery(self):
        delivery={**self.records[0],'id':'delivery-rubin','title':'Доставка Рубин','url':ORIGIN+'/materials/delivery-rubin',
                  'sections':[{'heading':'Доставка','text':'Доставка заказа Рубин стоит 300 рублей.'}]}
        index=self.make(self.records+[delivery])
        self.assertIn('300 рублей',index.search('Сколько стоит доставка заказа Рубин?',A)['text'])
        self.assertEqual(index.search('Сколько хранится товар Рубин?',A)['status'],'unavailable')


if __name__=='__main__':unittest.main()
````

## evaluation/closed_search/document_links_20261029.json

SHA-256: 5619b2e435930d8601b53393663b39a99c292359c19a4956f15d3c21f9c5fa06

````json
{
  "description":"Синтетические ожидания зафиксированы до изменения алгоритма. Ссылка должна точно указывать зарегистрированный документ, раздел и редакцию; никакой загрузки внешнего URL.",
  "documents":[
    {"id":"link_base","name":"Порядок_Нефрит.md","text":"Редакция: 1\n# 1. Хранение товара Нефрит\nТовар Нефрит хранится 8 календарных дней. См. документ «Исключения_Нефрит.md», раздел 2, редакция 3."},
    {"id":"link_exception","name":"Исключения_Нефрит.md","text":"Редакция: 3\n# 2. Хранение товара Нефрит\nИсключение: если упаковка вскрыта, товар Нефрит хранится 2 календарных дня.\n# 9. Посторонняя операция\nДоставка товара Нефрит занимает 90 рабочих дней."},
    {"id":"link_private_base","name":"Порядок_Морион.md","text":"Редакция: 1\n# 1. Хранение товара Морион\nТовар Морион хранится 7 календарных дней. См. документ «Ограничения_Морион.md», раздел 4, редакция 2."},
    {"id":"link_private","name":"Ограничения_Морион.md","allowedUserIds":["closed_lab_b"],"text":"Редакция: 2\n# 4. Хранение товара Морион\nИсключение: если упаковка вскрыта, товар Морион хранится 3 календарных дня."},
    {"id":"link_missing","name":"Хранение_Рубин.md","text":"Товар Рубин хранится 12 календарных дней. См. документ «Неизвестный_Рубин.md», раздел 2, редакция 1."},
    {"id":"link_wrong_version","name":"Хранение_Алмаз.md","text":"Товар Алмаз хранится 9 календарных дней. См. документ «Ограничения_Алмаз.md», раздел 2, редакция 1."},
    {"id":"link_version_target","name":"Ограничения_Алмаз.md","text":"Редакция: 2\n# 2. Хранение товара Алмаз\nИсключение: если упаковка вскрыта, товар Алмаз хранится 4 календарных дня."},
    {"id":"link_cycle_a","name":"Порядок_Оникс.md","text":"Редакция: 1\n# 1. Хранение товара Оникс\nТовар Оникс хранится 11 календарных дней. См. документ «Уточнение_Оникс.md», раздел 2, редакция 1."},
    {"id":"link_cycle_b","name":"Уточнение_Оникс.md","text":"Редакция: 1\n# 2. Хранение товара Оникс\nСм. документ «Порядок_Оникс.md», раздел 1, редакция 1."},
    {"id":"link_chain_base","name":"Порядок_Диопсид.md","text":"Редакция: 1\n# 1. Приёмка\nПоставку Диопсид принимает диспетчер. См. документ «Продолжение_Диопсид.md», раздел 2, редакция 1."},
    {"id":"link_chain_mid","name":"Продолжение_Диопсид.md","text":"Редакция: 1\n# 2. Действия\nПри повреждении упаковки поставки Диопсид сделайте фото. См. документ «Завершение_Диопсид.md», раздел 3, редакция 1."},
    {"id":"link_chain_end","name":"Завершение_Диопсид.md","text":"Редакция: 1\n# 3. Завершение\nНомер поставки Диопсид запишите в бумажный журнал."}
  ],
  "cases":[
    {"id":"unknown","q":"Сколько хранится товар Нефрит?","status":"clarify","has":["упаковка"],"chat":"linked"},
    {"id":"closed","q":"Упаковка не вскрыта.","has":["8 календарных"],"absent":["2 календарных","90 рабочих","См. документ"],"chat":"linked","evidence_ids":["link_base"]},
    {"id":"opened","q":"А если упаковка вскрыта?","has":["2 календарных"],"absent":["8 календарных","90 рабочих"],"chat":"linked","evidence_ids":["link_exception"]},
    {"id":"direct-open","q":"Упаковка вскрыта. Сколько хранится товар Нефрит?","has":["2 календарных"],"absent":["8 календарных"],"evidence_ids":["link_exception"]},
    {"id":"hidden-dependency","q":"Упаковка вскрыта. Сколько хранится товар Морион?","status":"unavailable","absent":["7 календарных","3 календарных","Ограничения_Морион"]},
    {"id":"visible-dependency","user":"b","q":"Упаковка вскрыта. Сколько хранится товар Морион?","has":["3 календарных"],"absent":["7 календарных"],"evidence_ids":["link_private"]},
    {"id":"missing","q":"Сколько хранится товар Рубин?","status":"unavailable","absent":["12 календарных"]},
    {"id":"wrong-edition","q":"Упаковка вскрыта. Сколько хранится товар Алмаз?","status":"unavailable","absent":["9 календарных","4 календарных"]},
    {"id":"cycle","q":"Сколько хранится товар Оникс?","status":"unavailable","absent":["11 календарных"]},
    {"id":"chain-summary","q":"Кратко перескажи выбранный файл","source":"link_chain_base","has":["диспетчер","сделайте фото","бумажный журнал"],"absent":["См. документ"],"evidence_ids":["link_chain_base","link_chain_mid","link_chain_end"]}
  ],
  "dialogue":[
    {"id":"d01","q":"Кто принимает поставку Содалит и сколько дней она обрабатывается?","status":"partial","has":["координатор","Уточните"]},
    {"id":"d02","q":"Упаковка вскрыта.","has":["2 рабочих"],"absent":["6 рабочих"]},
    {"id":"d03","q":"А если упаковка не вскрыта?","has":["6 рабочих"],"absent":["2 рабочих"]},
    {"id":"d04","q":"Сколько хранится товар Кахолонг и сколько дней он доставляется?","has":["9 календарных","5 рабочих"],"absent":["Содалит"]},
    {"id":"d05","q":"А сколько дней?","status":"clarify"},
    {"id":"d06","q":"Что написано о ракетном топливе?","status":"not_found"},
    {"id":"d07","q":"А сколько дней?","status":"clarify"},
    {"id":"d08","q":"Сколько дней обрабатывается поставка Содалит?","status":"clarify","has":["упаковка"]},
    {"id":"d09","q":"Не знаю, вскрыта ли упаковка.","status":"clarify"},
    {"id":"d10","q":"Упаковка не вскрыта.","has":["6 рабочих"]},
    {"id":"d11","q":"Кто принимает товар Танзанит?","has":["диспетчер"]},
    {"id":"d12","q":"А сколько он хранится?","has":["7 календарных"],"absent":["Содалит"]},
    {"id":"d13","q":"Сколько хранится товар Халцедон?","status":"clarify"},
    {"id":"d14","q":"Упаковка не вскрыта.","has":["8 календарных"]},
    {"id":"d15","q":"А если упаковка вскрыта?","status":"conflict"},
    {"id":"d16","q":"А сколько дней?","status":"clarify"},
    {"id":"d17","q":"Сколько хранится товар Нефрит?","status":"clarify"},
    {"id":"d18","q":"Упаковка вскрыта.","has":["2 календарных"],"absent":["8 календарных"]},
    {"id":"d19","q":"Кто принимает поставку Содалит?","has":["координатор"]},
    {"id":"d20","q":"А если упаковка вскрыта?","status":"clarify","absent":["Нефрит","2 календарных"]}
  ],
  "holdout":[
    {"id":"lowercase","q":"Упаковка не открыта. Как долго можно держать товар нефрит?","has":["8 календарных"],"absent":["2 календарных"]},
    {"id":"compound","q":"Упаковка вскрыта. Кто принимает поставку Содалит и сколько хранится товар Нефрит?","has":["координатор","2 календарных"],"absent":["8 календарных"]},
    {"id":"unknown-again","q":"Не знаю, вскрыта ли упаковка. Сколько хранится товар Нефрит?","status":"clarify"},
    {"id":"selected-hidden","q":"Кратко перескажи выбранный файл","source":"link_private_base","status":"unavailable","absent":["3 календарных","7 календарных","Ограничения_Морион"]}
  ]
}
````

## evaluation/closed_search/document_links_probe.py

SHA-256: e896c308e8c3f1f86395452bb4a7e03ceaaad8dd6de039fa69402f9b18a7da0d

````python
"""Замороженные междокументные связи и диалог из двадцати сообщений."""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from ai_core.site_search import ClosedSiteIndex, Principal
from evaluation.closed_search.cross_documents_probe import sources, ORIGIN, LAB, SPEC as CROSS_SPEC

SPEC=Path(__file__).with_name('document_links_20261029.json')


def records(spec):
    return sources(spec)+sources(json.loads(CROSS_SPEC.read_text(encoding='utf-8')))


def checks(case,result):
    errors=[]
    if result['status']!=case.get('status','answer'):errors.append('status:'+result['status'])
    for key,expected in [('has',True),('absent',False)]:
        for text in case.get(key,[]):
            present=bool(re.search((r'(?<!\d)' if text[:1].isdigit() else '')+re.escape(text),result['text'],re.I))
            if present!=expected:errors.append(key+':'+text)
    if 'evidence_ids' in case and {item['id'] for item in result['evidence']}!=set(case['evidence_ids']):errors.append('evidence-origin')
    if result['status'] not in {'answer','partial'} and (result['links'] or result['evidence']):errors.append('leaked-evidence')
    return errors


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--label',default='links');parser.add_argument('--holdout',action='store_true');args=parser.parse_args()
    spec=json.loads(SPEC.read_text(encoding='utf-8'))
    index=ClosedSiteIndex([ORIGIN]);index.publish(records(spec),index.begin('links'))
    rows=[]
    groups=['holdout'] if args.holdout else ['cases','dialogue']
    for group in groups:
        for case in spec[group]:
            result=index.search(case['q'],Principal('closed_lab_'+case.get('user','a'),'staff'),
                                'long' if group=='dialogue' else case.get('chat',case['id']),case.get('source'))
            errors=checks(case,result)
            rows.append({'id':case['id'],'question':case['q'],'result':result,'pass':not errors,'errors':errors})
    report={'specSHA256':hashlib.sha256(SPEC.read_bytes()).hexdigest(),'total':len(rows),'passed':sum(r['pass'] for r in rows),'cases':rows}
    file=LAB/(args.label+'-'+str(time.time_ns())+'.json');file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'file':str(file),'total':report['total'],'passed':report['passed'],'failures':[{'id':r['id'],'errors':r['errors'],'text':r['result']['text']} for r in rows if not r['pass']]},ensure_ascii=False))
    if report['passed']!=report['total']:raise SystemExit(1)


if __name__=='__main__':main()
````

## evaluation/closed_search/document_links_http.cjs

SHA-256: 46bc4b881c34d27a5f0fe261e28e795b151f9647a5e49237ac13cd609b87f224

````javascript
/* Тестовый сайт: ссылки, длинный диалог, перезапуск и живые редакции файлов. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const {createHash}=require('node:crypto');
const specPath=path.join(__dirname,'document_links_20261029.json');
const pendingPath=path.join(lab,'links-http-pending.json');
const encode=text=>'data:text/markdown;base64,'+Buffer.from(text,'utf8').toString('base64');

async function main(){
    const spec=JSON.parse(await fs.readFile(specPath,'utf8'));
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const mode=process.argv.includes('--restored')?'restored':process.argv.includes('--revisions')?'revisions':'main';
    const rows=[],rateLimitWaits=[];
    let cleanup=true,ready;
    const prefix='links-http-'+Date.now();
    async function ask(item,chat=prefix+'-'+item.id,actor=item.user||'b'){
        const body={chatId:chat,...(item.source?{documentId:item.source}:{}),messages:[{role:'user',content:item.q}]};
        let started=performance.now(),result=await call('/api/ai-chat',body,cookies[actor]);
        if(result.response.status===429){
            const seconds=Number(result.response.headers.get('retry-after'))||300;
            if(seconds<0||seconds>360)throw new Error('Unexpected rate-limit interval');
            rateLimitWaits.push({id:item.id,actor,seconds});console.log(JSON.stringify({event:'rate-limit-wait',seconds}));
            for(let remaining=seconds*1000+100;remaining>0;remaining-=Math.min(remaining,30000))await new Promise(resolve=>setTimeout(resolve,Math.min(remaining,30000)));
            started=performance.now();result=await call('/api/ai-chat',body,cookies[actor]);
        }
        const {response,value}=result,answer=value.output_text||'',errors=[];
        const contains=text=>new RegExp((/^\d/.test(text)?'(?<!\\d)':'')+text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'i').test(answer);
        if(response.status!==200)errors.push('http:'+response.status);
        if(!(item.allowed_statuses||[item.status||'answer']).includes(value.search_status))errors.push('status:'+value.search_status);
        for(const text of item.has||[])if(!contains(text))errors.push('missing:'+text);
        for(const text of item.absent||[])if(contains(text))errors.push('unexpected:'+text);
        const links=value.sourceLinks||[];
        if(!['answer','partial'].includes(value.search_status)&&links.length)errors.push('links-on-refusal');
        if(item.evidence_ids && value.search_status==='answer' && item.evidence_ids.some(id=>!links.some(link=>link.url.includes(id))))errors.push('wrong-sources');
        rows.push({id:item.id,actor,question:item.q,status:value.search_status,answer,links,ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    async function catalog(change){await storage('tea_hub_urgent_files_v1',cookies.admin,change(await storage('tea_hub_urgent_files_v1',cookies.admin)));}
    if(mode==='main'){
        const entries=await storage('tea_hub_urgent_files_v1',cookies.admin);
        for(const item of spec.documents){
            const entry={id:item.id,name:item.name,hasSeparateData:true,section:'Ссылки между документами ВатэсAi',searchAuthority:'official',
                size:Buffer.byteLength(item.text)+' B',date:'Локальный тест',...(item.allowedUserIds?{allowedUserIds:item.allowedUserIds}:{})};
            const prior=entries.find(doc=>doc.id===item.id),data=encode(item.text);
            if(prior){if(JSON.stringify(prior)!==JSON.stringify(entry)||await storage('file_data_'+item.id,cookies.admin)!==data)throw new Error('Frozen fixture changed '+item.id);}
            else{await storage('file_data_'+item.id,cookies.admin,data);entries.push(entry);}
        }
        await storage('tea_hub_urgent_files_v1',cookies.admin,entries);ready=await waitSnapshot();
        for(const item of [...spec.cases,...spec.holdout]){
            // В полном каталоге уже есть старый контрольный скан о Нефрите
            // с другим сроком. Проверка ссылки явно выбирает свой корневой файл.
            const linked=['unknown','closed','opened','direct-open','lowercase','unknown-again'].includes(item.id);
            const scenario=item.id==='compound'?{id:item.id,q:item.q,status:'conflict'}:{...item,...(linked?{source:'link_base'}:{})};
            await ask(scenario,prefix+'-'+(item.chat||item.id),['hidden-dependency','selected-hidden'].includes(item.id)?'a':item.user||'b');
        }
        for(const item of spec.dialogue)await ask({...item,...(['d17','d18'].includes(item.id)?{source:'link_base'}:{})},prefix+'-long','a');
        await ask({id:'global-independent-conflict',q:'Упаковка вскрыта. Сколько хранится товар Нефрит?',status:'conflict'});
        await ask({id:'lowercase-independent-conflict',q:'Упаковка не вскрыта. Сколько хранится товар нефрит?',status:'conflict'});
        const saved={linked:prefix+'-restore-link',topic:prefix+'-restore-topic'};
        await ask({id:'seed-linked',q:'Сколько хранится товар Нефрит?',source:'link_base',status:'clarify'},saved.linked,'b');
        await ask({id:'seed-topic',q:'Кто принимает товар Танзанит?',has:['диспетчер']},saved.topic,'a');
        await fs.writeFile(pendingPath,JSON.stringify(saved,null,2));
    }else if(mode==='restored'){
        const saved=JSON.parse(await fs.readFile(pendingPath,'utf8'));
        await ask({id:'restored-closed',q:'Упаковка не вскрыта.',has:['8 календарных'],absent:['2 календарных']},saved.linked,'b');
        await ask({id:'restored-open',q:'А если упаковка вскрыта?',has:['2 календарных'],absent:['8 календарных']},saved.linked,'b');
        await ask({id:'other-user',q:'А если упаковка вскрыта?',status:'clarify'},saved.linked,'a');
        await ask({id:'other-chat',q:'А если упаковка вскрыта?',status:'clarify'},saved.linked+'-other','b');
        await ask({id:'restored-new-operation',q:'А сколько он хранится?',has:['7 календарных']},saved.topic,'a');
    }else{
        const rootId='link_live_root',targetId='link_live_target';
        const rootName='Правило_Линкорит_проверка.md',targetName='Ограничение_Линкорит_проверка.md';
        const existing=await storage('tea_hub_urgent_files_v1',cookies.admin);
        if(existing.some(item=>[rootId,targetId].includes(item.id)))throw new Error('Unfinished revision fixture exists');
        const rootText=edition=>'Товар Линкорит хранится 9 календарных дней. См. документ «'+targetName+'», раздел 2, редакция '+edition+'.';
        const targetText=(edition,days)=>'Редакция: '+edition+'\n# 2. Хранение товара Линкорит\nИсключение: если упаковка вскрыта, товар Линкорит хранится '+days+' календарных дня.';
        const question='Упаковка вскрыта. Сколько хранится товар Линкорит?';
        cleanup=false;
        try{
            await storage('file_data_'+rootId,cookies.admin,encode(rootText(1)));
            await storage('file_data_'+targetId,cookies.admin,encode(targetText(1,2)));
            await catalog(items=>[...items,{id:rootId,name:rootName,section:'Проверка редакций ссылок',hasSeparateData:true,searchAuthority:'official'},
                {id:targetId,name:targetName,section:'Проверка редакций ссылок',hasSeparateData:true,searchAuthority:'official',allowedUserIds:['closed_lab_b']}]);
            await waitSnapshot();
            await ask({id:'v1',q:question,has:['2 календарных'],absent:['9 календарных']});
            await ask({id:'hidden',q:question,status:'unavailable',absent:['2 календарных',targetName]},prefix+'-hidden','a');
            await storage('file_data_'+targetId,cookies.admin,encode(targetText(2,4)));
            await ask({id:'edition-immediate',q:question,allowed_statuses:['processing','unavailable'],absent:['2 календарных','4 календарных']});
            await waitSnapshot();await ask({id:'wrong-edition',q:question,status:'unavailable'});
            await storage('file_data_'+rootId,cookies.admin,encode(rootText(2)));await waitSnapshot();
            await ask({id:'matched-edition',q:question,has:['4 календарных'],absent:['2 календарных','9 календарных']});
            await storage('file_data_'+rootId,cookies.admin,encode('Товар Линкорит хранится 9 календарных дней. См. [Уточнение](/tasks?tab=docs&documentId='+targetId+'#section-2), редакция 2.'));
            await waitSnapshot();
            await ask({id:'matched-markdown-link',q:question,has:['4 календарных'],absent:['2 календарных','9 календарных']});
            await ask({id:'base-condition',q:'Упаковка не вскрыта. Сколько хранится товар Линкорит?',has:['9 календарных'],absent:['4 календарных']});
            await catalog(items=>items.map(item=>item.id===targetId?{id:targetId,name:targetName,section:'Проверка редакций ссылок',hasSeparateData:true,searchAuthority:'official',adminOnly:true}:item));
            await ask({id:'revoked-immediate',q:question,allowed_statuses:['processing','unavailable'],absent:['4 календарных']});
            await waitSnapshot();await ask({id:'revoked',q:question,status:'unavailable'});
            await catalog(items=>items.filter(item=>item.id!==targetId));await waitSnapshot();
            await ask({id:'deleted-target',q:question,status:'unavailable'});
        }finally{
            await catalog(items=>items.filter(item=>![rootId,targetId].includes(item.id)));
            await storage('file_data_'+rootId,cookies.admin,null);await storage('file_data_'+targetId,cookies.admin,null);
            await waitSnapshot();cleanup=true;
        }
    }
    const report={createdAt:new Date().toISOString(),mode,ready,cleanup,rateLimitWaits,
        specSHA256:createHash('sha256').update(await fs.readFile(specPath)).digest('hex'),total:rows.length,passed:rows.filter(r=>r.pass).length,cases:rows};
    const file=path.join(lab,'links-http-'+mode+'-'+Date.now()+'.json');await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,total:report.total,passed:report.passed,cleanup,failures:rows.filter(r=>!r.pass)}));
    if(report.total!==report.passed||!cleanup)process.exitCode=1;
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
````

## evaluation/closed_search/verify_lab.cjs

SHA-256: 4e2eb4d7730fd9102489737ee8acb926ea52a630f80c81966e10172387af3398

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
    const linkFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'document_links_20261029.json'), 'utf8'));
    const expectedDocuments = 9 + reasoningFixtures.documents.length + conditionFixtures.documents.length + structureFixtures.documents.length + structureAssets.length + scanFixtures.documents.length + layoutFixtures.documents.length + qualityFixtures.documents.length + multipageFixtures.documents.length + crossFixtures.documents.length + linkFixtures.documents.length;
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

SHA-256: bacb73d68922ea2cf936f5abd7e0885fd23405a77e0fa3ce19b47764e42f539c

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
const includeDocumentLinks = process.argv.includes('--document-links');
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
````
