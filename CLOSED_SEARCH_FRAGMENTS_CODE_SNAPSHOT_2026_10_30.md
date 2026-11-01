# Полные листинги адресных ссылок — 30.10.2026


## ai_core/site_search_fragments.py

SHA-256: 06528a3fabd199c0f287e2fceac1ac487b50cdc94531bac970d4ae63d7d1223c

````python
"""Адреса проверенных фрагментов: редакция и доступ проверяются при открытии."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlencode, urlsplit


def validate_fragment_url(source, origins):
    value = source.get('fragment_url')
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError('fragment_url_invalid')
    url = urlsplit(value)
    if (f'{url.scheme}://{url.netloc}' not in origins or url.scheme not in {'http', 'https'}
            or url.username or url.password or url.query or url.fragment
            or url.path != '/closed-search-fragment'):
        raise ValueError('fragment_url_invalid')


def compact(text):
    return re.sub(r'\s+', ' ', text).strip()


def build_fragments(passages):
    """Готовим указатели один раз вместе с индексом, не во время OCR/вопроса."""
    grouped = {}
    for part in passages:
        if not part.source.get('fragment_url'):
            continue
        # Синтетическое правило из нескольких файлов не выдаём за цитату одного.
        if (part.structure or {}).get('document_origins') or (part.structure or {}).get('document_guard'):
            continue
        product = any(row['heading'] == part.heading for row in part.source.get('catalog_items', []))
        group = (part.source['id'], part.heading, '' if product else part.text)
        if group not in grouped:
            grouped[group] = {'source': part.source, 'heading': part.heading, 'parts': [], 'kind': 'product' if product else 'fragment'}
        if part.text not in grouped[group]['parts']:
            grouped[group]['parts'].append(part.text)
    targets, by_heading = {}, {}
    for item in grouped.values():
        source = item['source']
        text = '\n\n'.join(item['parts'])
        token = hashlib.sha256(json.dumps([source['id'], source['revision'], item['heading'], text], ensure_ascii=False).encode()).hexdigest()
        target = {'id': source['id'], 'revision': source['revision'], 'fragment': token,
                  'title': source['title'], 'heading': item['heading'], 'text': text,
                  'kind': item['kind'], 'source_url': source['url']}
        targets[(source['id'], token)] = target
        by_heading.setdefault((source['id'], item['heading']), []).append(target)
    return targets, by_heading


def evidence_links(index, evidence):
    """Точный переход только при буквальном совпадении цитаты с фрагментом."""
    source = index.sources[evidence['id']]
    quote = compact(evidence.get('quote', ''))
    if not quote or not source.get('fragment_url'):
        return []
    targets = index.fragment_headings.get((source['id'], evidence['heading']), [])
    selected = [item for item in targets if quote in compact(item['text'])
                or (item['kind'] == 'product' and quote in compact(item['heading']))]
    if not selected:
        # Составной ответ может цитировать несколько абзацев одного раздела.
        sentences = [compact(value) for value in index._sentences(quote)]
        selected = [item for item in targets if any(value in compact(item['text']) for value in sentences)]
        if not sentences or not all(any(value in compact(item['text']) for item in selected) for value in sentences):
            return []
    links = []
    for item in selected:
        title = item['heading'] or source['title']
        link = {'title': title, 'url': source['fragment_url'] + '?' + urlencode({
            'source': source['id'], 'revision': source['revision'], 'fragment': item['fragment']})}
        if link not in links:
            links.append(link)
    return links


def resolve_fragment(index, source_id, revision, fragment, principal, current_revisions, readable):
    """Ссылка не даёт прав: проверяем живой список сайта и индекс независимо."""
    missing = {'status': 'not_found', 'message': 'Материал недоступен.'}
    source = index.sources.get(source_id)
    if not source or source_id not in current_revisions or not readable(source, principal, index.clock()):
        return missing
    if current_revisions[source_id] != revision:
        return {'status': 'changed', 'message': 'Материал изменился. Повторите вопрос, чтобы получить ссылку на актуальную редакцию.'}
    if index.state != 'ready' or source['revision'] != revision:
        return {'status': 'processing', 'message': 'Материал обновляется. Попробуйте открыть ссылку позже.'}
    target = index.fragment_targets.get((source_id, fragment))
    if not target or source.get('complete') is False:
        return missing
    return {'status': 'ready', **target}

````

## ai_core/site_search.py

SHA-256: f6791a02f1b04bd54e770d0aba8e72ed3ca2c07198ca19def1533fcd0a09de8f

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
        self.fragment_targets, self.fragment_headings = build_fragments(self.passages)
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
        catalog = search_catalog(self, question, allowed, selected_source, day, session, sessions, scope)
        if catalog is not None:
            return catalog
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

````

## ai_core/site_search_api.py

SHA-256: 537011507ee02297ff032a4a1a5df617f4c1a49e27d8f6f485fe0f15682ad1e0

````python
"""Внутренний API поискового ядра. Пользовательские роли подтверждает сервер сайта."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .site_documents import extract_document, PARSER_VERSION
from .site_search import Principal
from .site_search_runtime import DurableSearch

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get("VATES_SEARCH_CONFIG", ROOT / ".runtime" / "closed-search" / "sites.json"))
router = APIRouter(prefix="/closed-search", tags=["closed-site-search"])
indexes: dict[str, DurableSearch] = {}
jobs: set[asyncio.Task] = set()
runtime_lock = asyncio.Lock()
build_slots = asyncio.Semaphore(2)
STORAGE = Path(os.environ.get("VATES_SEARCH_STORAGE", ROOT / ".runtime" / "closed-search" / "indexes"))


async def runtime_for(site: str, host: dict) -> DurableSearch:
    async with runtime_lock:
        current = indexes.get(site)
        if current is None or current.index.origins != set(host["origins"]):
            try:
                # Имя уже проверено authenticate; каждому сайту принадлежит
                # отдельная база. Чтение диска не блокирует цикл HTTP-сервера.
                current = await asyncio.to_thread(DurableSearch, host["origins"], STORAGE / (site + ".sqlite3"), extraction_version=PARSER_VERSION)
                indexes[site] = current
            except Exception:
                logging.getLogger(__name__).error("closed_search_storage_unavailable")
                raise HTTPException(503, "Не удалось открыть сохранённые материалы") from None
        return current


def authenticate(request: Request, site: str) -> dict:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", site):
        raise HTTPException(404, "Сайт недоступен")
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(503, "Закрытый поиск не настроен") from None
    supplied = hashlib.sha256(request.headers.get("X-Vates-Search-Key", "").encode()).hexdigest()
    host = next((item for item in config.get("sites", []) if item.get("id") == site), None)
    if not host or not hmac.compare_digest(supplied, host.get("token_sha256", "")):
        raise HTTPException(403, "Недостаточно прав доступа")
    return host


async def body(request: Request, maximum: int) -> dict:
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > maximum:
            raise HTTPException(413, "Слишком большой запрос")
    try:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Некорректный запрос") from None


@router.post("/{site}/snapshot")
async def snapshot(site: str, request: Request):
    host = authenticate(request, site)
    payload = await body(request, 24 * 1024 * 1024)
    sources = payload.get("sources")
    if not isinstance(sources, list) or len(sources) > 3000 or not all(isinstance(item, dict) for item in sources):
        raise HTTPException(400, "Некорректный снимок материалов")
    runtime = await runtime_for(site, host)
    fingerprint = hashlib.sha256(PARSER_VERSION.encode() + json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    try:
        generation = await asyncio.to_thread(runtime.begin, fingerprint)
    except Exception:
        logging.getLogger(__name__).error("closed_search_snapshot_save_failed")
        raise HTTPException(503, "Не удалось сохранить обновление материалов") from None
    if generation is None:
        current = await asyncio.to_thread(runtime.status)
        return {"state": current["state"], "snapshot": fingerprint}

    def prepare():
        prepared = []
        for raw in sources:
            if runtime.index.generation != generation:
                return None
            source = dict(raw)
            if not isinstance(source.get("id"), str) or len(source["id"]) > 150:
                raise ValueError("source_id_invalid")
            if source.get("file_data"):
                data = source.pop("file_data")
                try:
                    text, complete = extract_document(data, source.get("file_name", ""))
                    source["sections"] = [{"heading": source.get("title", ""), "text": text}]
                    source["complete"] = complete
                except Exception:
                    source["sections"], source["complete"] = [], False
                    logging.getLogger(__name__).warning("closed_search_document_partial")
            if not isinstance(source.get("sections"), list) or len(source["sections"]) > 300:
                raise ValueError("source_sections_invalid")
            prepared.append(source)
        return prepared

    async def build():
        try:
            # Не более двух извлечений одновременно. Устаревшие ожидающие
            # задания прекращаются до OCR, текущий снимок публикуется атомарно.
            async with build_slots:
                prepared = await asyncio.to_thread(prepare)
                if prepared is not None:
                    await asyncio.to_thread(runtime.publish, prepared, generation, payload.get("aliases", {}))
        except Exception:
            await asyncio.to_thread(runtime.fail, generation)
            logging.getLogger(__name__).error("closed_search_snapshot_failed")
    task = asyncio.create_task(build())
    jobs.add(task)
    task.add_done_callback(jobs.discard)
    return JSONResponse({"state": "processing", "snapshot": fingerprint}, status_code=202, headers={"Cache-Control": "no-store"})


@router.get("/{site}/status")
async def status(site: str, request: Request):
    host = authenticate(request, site)
    runtime = await runtime_for(site, host)
    return JSONResponse(await asyncio.to_thread(runtime.status), headers={"Cache-Control": "no-store"})


@router.post("/{site}/query")
async def query(site: str, request: Request):
    host = authenticate(request, site)
    payload = await body(request, 1024 * 1024)
    question = payload.get("question")
    identity = payload.get("principal")
    chat = payload.get("chat_id", "default")
    if not isinstance(question, str) or not isinstance(identity, dict) or not isinstance(identity.get("user_id"), str) or identity.get("role") not in {"admin", "staff"} or not isinstance(chat, str) or len(chat) > 150:
        raise HTTPException(400, "Не удалось проверить контекст пользователя")
    groups = identity.get("groups", [])
    if not isinstance(groups, list) or not all(isinstance(item, str) for item in groups):
        raise HTTPException(400, "Не удалось проверить контекст пользователя")
    runtime = await runtime_for(site, host)
    # Роль и user_id принимаются только от аутентифицированного серверного адаптера.
    selected = payload.get("selected_source")
    if selected is not None and (not isinstance(selected, str) or len(selected) > 150):
        raise HTTPException(400, "Не удалось определить материал")
    revisions = payload.get("current_revisions")
    if not isinstance(revisions, dict) or len(revisions) > 3000 or any(not isinstance(key, str) or not isinstance(value, str) for key, value in revisions.items()):
        raise HTTPException(400, "Не удалось подтвердить актуальность материалов")
    generation = runtime.index.generation
    try:
        result = await asyncio.to_thread(runtime.query, question, Principal(identity["user_id"], identity["role"], tuple(groups)), chat, selected, revisions)
    except Exception:
        logging.getLogger(__name__).error("closed_search_query_failed")
        result = {"status": "unavailable", "text": "Не удалось проверить доступные материалы. Повторите вопрос позже.", "links": [], "evidence": []}
    if runtime.index.generation != generation or runtime.index.state == "processing":
        result = {"status": "processing", "text": "Материалы сейчас обновляются. Повторите вопрос после завершения обработки.", "links": [], "evidence": []}
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post('/{site}/fragment')
async def fragment(site: str, request: Request):
    host = authenticate(request, site)
    payload = await body(request, 1024 * 1024)
    identity, revisions = payload.get('principal'), payload.get('current_revisions')
    fields = [payload.get(name) for name in ('source', 'revision', 'fragment')]
    if (not isinstance(identity, dict) or not isinstance(identity.get('user_id'), str)
            or identity.get('role') not in {'admin', 'staff'}
            or not all(isinstance(value, str) and 0 < len(value) <= 150 for value in fields)
            or not re.fullmatch('[a-f0-9]{64}', fields[2])
            or not isinstance(revisions, dict) or len(revisions) > 3000
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in revisions.items())):
        raise HTTPException(400, 'Не удалось определить материал')
    groups = identity.get('groups', [])
    if not isinstance(groups, list) or not all(isinstance(value, str) for value in groups):
        raise HTTPException(400, 'Не удалось проверить пользователя')
    runtime = await runtime_for(site, host)
    try:
        result = await asyncio.to_thread(runtime.fragment, *fields,
            Principal(identity['user_id'], identity['role'], tuple(groups)), revisions)
    except Exception:
        logging.getLogger(__name__).error('closed_search_fragment_failed')
        result = {'status': 'unavailable', 'message': 'Не удалось открыть материал. Попробуйте позже.'}
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


# Тот же компонент можно запускать отдельно для изолированной приёмки.
app = FastAPI(title="ВатэсAi — закрытый поиск")
app.include_router(router)

````

## ai_core/site_search_runtime.py

SHA-256: 987bd819c0acf6b5b661106193ef535af3cceb9921cdd783f8b7820cb72b4920

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
from .site_search_catalog import saved_context
from .site_search_fragments import resolve_fragment

CONTEXT_TTL = 30 * 24 * 60 * 60
LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def engine_version() -> str:
    digest = hashlib.sha256()
    for name in ("site_search.py", "site_search_language.py", "site_search_reasoning.py", "site_search_conditions.py", "site_search_structure.py", "site_search_links.py", "site_search_catalog.py", "site_search_measurements.py", "site_search_comparison.py", "site_search_fragments.py"):
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
        if facet not in {None, 'cost', 'year', 'recipient', 'duration', 'procedure', 'packaging'}:
            facet = None
        saved = {'condition_facts': facts, 'facet': facet, 'pending_conditions': context.get('pending_conditions') is True}
        saved.update(saved_context(context, self.index.catalog_products, identifiers))
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

    def fragment(self, source_id, revision, fragment, actor, revisions):
        with self.lock:
            state = self.store.state()
            if state and state[:2] != (self.index.generation, self.index.snapshot):
                return {'status': 'processing', 'message': NOT_READY}
            return resolve_fragment(self.index, source_id, revision, fragment, actor, revisions, readable)

    def status(self):
        with self.lock:
            return {"state": self.index.state, "sources": len(self.index.sources), "passages": len(self.index.passages),
                    "snapshot": self.index.snapshot, "restored_from_disk": self.restored,
                    "restore_ms": self.restore_ms, "rebuilt_from_text": self.rebuilt, "context_count": len(self.index.sessions)}

````

## ai_core/site_search_store.py

SHA-256: 9fe5f3884373fbb919de32ab0089725460d74b5aa55a8065d3690d9dc82f714a

````python
"""Атомарное локальное хранение готового индекса и минимального контекста.

В базе нет ключей API, исходных загрузок, полных сообщений или готовых ответов.
JSON является данными: pickle и исполнение сохранённого кода не используются.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

FORMAT_VERSION = 1
SOURCE_FIELDS = {"id", "revision", "title", "url", "kind", "sections", "access", "restrictions",
                 "parents", "status", "expires_at", "valid_from", "authority", "complete", "keywords", "file_name", "structure_unavailable", "catalog_items", "fragment_url"}


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class SearchStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS index_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation INTEGER NOT NULL, snapshot TEXT NOT NULL, state TEXT NOT NULL, payload TEXT, digest TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS chat_context (scope TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def state(self):
        with self.connection() as db:
            row = db.execute("SELECT generation,snapshot,state FROM index_state WHERE singleton=1").fetchone()
        return row

    def begin(self, snapshot: str) -> int:
        # Сначала фиксируется недоступность прежнего индекса. При сбое питания
        # незавершённое обновление не превратится обратно в готовый старый снимок.
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation FROM index_state WHERE singleton=1").fetchone()
            generation = (row[0] if row else 0) + 1
            db.execute("INSERT INTO index_state VALUES(1,?,?,'processing',NULL,NULL) ON CONFLICT(singleton) DO UPDATE SET generation=excluded.generation,snapshot=excluded.snapshot,state='processing',payload=NULL,digest=NULL", (generation, snapshot))
        return generation

    def save_index(self, index, engine: str, extraction_version: str | None = None) -> bool:
        payload = encode({"format": FORMAT_VERSION, "engine": engine, "extraction_version": extraction_version, "origins": sorted(index.origins),
                          "sources": [{key: value for key, value in source.items() if key in SOURCE_FIELDS} for source in index.sources.values()], "aliases": index.aliases,
                          "passages": [{"id": item.source["id"], "heading": item.heading, "text": item.text,
                                        "words": dict(item.words), "title_words": dict(item.title_words), "structure": item.structure} for item in index.passages]})
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.connection() as db:
            changed = db.execute("UPDATE index_state SET state='ready',payload=?,digest=? WHERE singleton=1 AND generation=? AND snapshot=? AND state='processing'",
                                 (payload, digest, index.generation, index.snapshot)).rowcount
        return changed == 1

    def load_index(self):
        with self.connection() as db:
            row = db.execute("SELECT generation,snapshot,state,payload,digest FROM index_state WHERE singleton=1").fetchone()
        if not row or row[2] != "ready":
            return None
        generation, snapshot, _, payload, digest = row
        if not payload or hashlib.sha256(payload.encode()).hexdigest() != digest:
            raise ValueError("stored_index_checksum")
        data = json.loads(payload)
        if data.get("format") != FORMAT_VERSION:
            raise ValueError("stored_index_format")
        return generation, snapshot, data

    def save_context(self, scope: tuple, context: dict | None, generation: int, now: float, ttl: float):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation,state FROM index_state WHERE singleton=1").fetchone()
            if not row or row != (generation, "ready"):
                return False
            if context is None:
                db.execute("DELETE FROM chat_context WHERE scope=?", (encode(scope),))
            else:
                db.execute("INSERT INTO chat_context VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET payload=excluded.payload,updated=excluded.updated",
                           (encode(scope), encode(context), context["time"]))
            db.execute("DELETE FROM chat_context WHERE updated<? OR updated>?", (now - ttl, now + 60))
            # Ограничение одинаково для диска и оперативной памяти.
            db.execute("DELETE FROM chat_context WHERE scope IN (SELECT scope FROM chat_context ORDER BY updated DESC,scope LIMIT -1 OFFSET 1000)")
        return True

    def contexts(self):
        with self.connection() as db:
            rows = db.execute("SELECT scope,payload FROM chat_context ORDER BY updated DESC LIMIT 1000").fetchall()
        result = []
        for scope, payload in rows:
            try:
                decoded = json.loads(scope)
                if len(decoded) != 4 or not isinstance(decoded[2], list):
                    continue
                result.append(((decoded[0], decoded[1], tuple(decoded[2]), decoded[3]), json.loads(payload)))
            except (ValueError, TypeError):
                continue
        return result

    def prune_contexts(self, valid_scopes: set[tuple]):
        encoded = {encode(scope) for scope in valid_scopes}
        with self.connection() as db:
            for (scope,) in db.execute("SELECT scope FROM chat_context").fetchall():
                if scope not in encoded:
                    db.execute("DELETE FROM chat_context WHERE scope=?", (scope,))

    def fail(self, generation: int):
        with self.connection() as db:
            db.execute("UPDATE index_state SET state='failed',payload=NULL,digest=NULL WHERE singleton=1 AND generation=?", (generation,))

````

## tests/test_site_search_fragments.py

SHA-256: 169d2912450d56fc7d863901dc077f6e40d9984235b75dc8bbb3c928f182c8a5

````python
"""Приёмка адресных ссылок: конкретный фрагмент, права, редакция и рестарт."""
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ai_core.site_search import ClosedSiteIndex, Principal, readable
from ai_core.site_search_fragments import evidence_links, resolve_fragment
from ai_core.site_search_runtime import DurableSearch

ORIGIN = 'http://127.0.0.1:3040'
B = Principal('b', 'staff')
A = Principal('a', 'staff')


def document(**extra):
    return {'id': 'document:fixture', 'revision': 'v1', 'title': 'Учебный каталог', 'kind': 'document',
            'url': ORIGIN + '/tasks?tab=docs&documentId=fixture', 'fragment_url': ORIGIN + '/closed-search-fragment',
            'access': {'scope': 'restricted', 'users': ['b']},
            'catalog_items': [{'key': 'one', 'heading': 'Термос Север'}, {'key': 'two', 'heading': 'Термос Юг'}],
            'sections': [{'heading': 'Термос Север', 'text': 'Объём: 500 мл. Материал: сталь.'},
                         {'heading': 'Термос Юг', 'text': 'Объём: 700 мл. Материал: стекло.'}], **extra}


class FragmentTests(unittest.TestCase):
    def setUp(self):
        self.index = ClosedSiteIndex([ORIGIN])
        self.publish(document())

    def publish(self, source):
        self.index.publish([source], self.index.begin('snapshot'))

    def link(self, heading='Термос Север', quote='Объём: 500 мл.'):
        links = evidence_links(self.index, {'id': 'document:fixture', 'heading': heading, 'quote': quote})
        self.assertTrue(links)
        return {key: value[0] for key, value in parse_qs(urlsplit(links[0]['url']).query).items()}

    def open(self, link, actor=B, revisions=None):
        return resolve_fragment(self.index, link['source'], link['revision'], link['fragment'], actor,
                                revisions if revisions is not None else {'document:fixture': 'v1'}, readable)

    def test_comparison_links_open_different_products_in_one_file(self):
        answer = self.index.search('Сравни объем термоса Север и термоса Юг', B)
        self.assertEqual(answer['status'], 'answer')
        self.assertEqual(len(answer['links']), 2)
        self.assertNotEqual(answer['links'][0]['url'], answer['links'][1]['url'])
        first = self.open(self.link())
        self.assertEqual(first['status'], 'ready')
        self.assertIn('500 мл', first['text'])
        self.assertNotIn('700 мл', first['text'])

    def test_private_link_is_not_a_permission(self):
        self.assertEqual(self.open(self.link(), A), {'status': 'not_found', 'message': 'Материал недоступен.'})

    def test_fact_in_product_heading_opens_that_card(self):
        self.publish(document(catalog_items=[{'key': 'one', 'heading': 'Термос Север 500 мл'}],
            sections=[{'heading': 'Термос Север 500 мл', 'text': 'Описание отсутствует.'}]))
        result = self.open(self.link('Термос Север 500 мл', 'Термос Север 500 мл'))
        self.assertEqual(result['heading'], 'Термос Север 500 мл')
        self.assertEqual(result['text'], 'Описание отсутствует.')

    def test_live_revocation_precedes_exporter(self):
        self.assertEqual(self.open(self.link(), revisions={})['status'], 'not_found')

    def test_live_revision_precedes_exporter(self):
        result = self.open(self.link(), revisions={'document:fixture': 'v2'})
        self.assertEqual(result['status'], 'changed')
        self.assertNotIn('text', result)

    def test_new_revision_changes_fragment_address(self):
        old = self.link()
        updated = document(revision='v2')
        updated['sections'][0]['text'] = 'Объём: 600 мл. Материал: сталь.'
        self.publish(updated)
        new = self.link(quote='Объём: 600 мл.')
        self.assertNotEqual(old['fragment'], new['fragment'])
        self.assertEqual(self.open(old, revisions={'document:fixture': 'v2'})['status'], 'changed')
        self.assertIn('600 мл', self.open(new, revisions={'document:fixture': 'v2'})['text'])

    def test_removed_document_returns_no_data(self):
        link = self.link()
        self.index.publish([], self.index.begin('removed'))
        self.assertEqual(self.open(link)['status'], 'not_found')

    def test_archived_document_cannot_be_opened(self):
        link = self.link()
        self.publish(document(status='archived'))
        self.assertEqual(self.open(link)['status'], 'not_found')

    def test_unknown_fragment_is_not_replaced_with_first_section(self):
        link = {**self.link(), 'fragment': '0' * 64}
        self.assertEqual(self.open(link)['status'], 'not_found')

    def test_processing_does_not_serve_old_text(self):
        link = self.link()
        self.index.begin('processing')
        self.assertEqual(self.open(link)['status'], 'processing')

    def test_same_heading_points_to_matching_paragraph(self):
        self.publish(document(catalog_items=[], sections=[{'heading': 'Правила', 'text': 'Приёмка выполняется утром.\n\nВозврат выполняется вечером.'}]))
        value = self.open(self.link('Правила', 'Возврат выполняется вечером.'))
        self.assertIn('вечером', value['text'])
        self.assertNotIn('утром', value['text'])

    def test_no_exact_quote_has_no_fake_fragment(self):
        self.assertEqual(evidence_links(self.index, {'id': 'document:fixture', 'heading': 'Термос Север', 'quote': 'Неподтверждённый факт.'}), [])

    def test_expired_source_is_not_readable(self):
        link = self.link()
        self.publish(document(expires_at=1))
        self.assertEqual(self.open(link)['status'], 'not_found')

    def test_wrong_fragment_origin_rejected(self):
        with self.assertRaises(ValueError):
            self.publish(document(fragment_url='https://unrelated.example/closed-search-fragment'))

    def test_incomplete_source_not_returned_as_exact_text(self):
        link = self.link()
        self.publish(document(complete=False))
        self.assertEqual(self.open(link)['status'], 'not_found')

    def test_same_link_survives_sqlite_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'index.sqlite3'
            runtime = DurableSearch([ORIGIN], database)
            runtime.publish([document()], runtime.begin('first'))
            answer = runtime.query('Какой объем у термоса Север?', B, 'chat', None, {'document:fixture': 'v1'})
            target = {key: value[0] for key, value in parse_qs(urlsplit(answer['links'][0]['url']).query).items()}
            restored = DurableSearch([ORIGIN], database)
            self.assertTrue(restored.restored)
            result = restored.fragment(target['source'], target['revision'], target['fragment'], B, {'document:fixture': 'v1'})
            self.assertEqual(result['status'], 'ready')
            self.assertIn('500 мл', result['text'])


if __name__ == '__main__':
    unittest.main()

````

## evaluation/closed_search/export_site.cjs

SHA-256: 88835fe7326fc65bad36933fa596c1d407d5ca7748f8e1b1eb0836f3d0315c10

````javascript
/* Exports only the approved test site's teaching content. No profiles, answers, histories or credentials. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const site = path.join(root, '.runtime/closed-search-lab-20261028/tea-app-main');
require(path.join(site, 'node_modules/@next/env')).loadEnvConfig(site, true);
const directory = path.resolve(process.env.TEA_DATA_DIR);
const expected = path.join(site, 'fixture-data');
if (directory !== expected || process.env.VATES_SEARCH_LAB !== 'true') throw new Error('Exporter accepts only this isolated fixture-data');
const origin = 'http://127.0.0.1:3040';
const endpoint = process.env.VATES_CLOSED_SEARCH_URL;
const headers = { 'Content-Type': 'application/json', 'X-Vates-Search-Key': process.env.VATES_CLOSED_SEARCH_KEY };
const read = key => { const file = path.join(directory, `${key}.json`); return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : []; };
const revision = value => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');

function access(record) {
    const restrictions = [];
    let policy = { scope: 'workspace' };
    if (record.accessPolicy) {
        const p = record.accessPolicy;
        policy = p.version === 1 && !Object.keys(p).some(key => !['version', 'visibility', 'userIds', 'roles', 'ownerId'].includes(key)) ? { scope: p.visibility,
            ...(p.userIds ? { users: p.userIds } : {}), ...(p.roles ? { roles: p.roles } : {}), ...(p.ownerId ? { owner_id: p.ownerId } : {}) } : { scope: 'invalid' };
    }
    if (record.adminOnly || record.isAdminOnly || record.isPrivate || record.visibility === 'admin') policy = { scope: 'admin' };
    if (record.ownerId) restrictions.push({ type: 'users', values: [record.ownerId] });
    if (record.allowedUserIds) restrictions.push({ type: 'users', values: record.allowedUserIds });
    if (record.allowedRoles) restrictions.push({ type: 'roles', values: record.allowedRoles });
    if (record.permissions !== undefined || record.access !== undefined) policy = { scope: 'invalid' };
    return { access: policy, restrictions };
}
function current(record) {
    return ![record.deleted, record.isDeleted, record.archived, record.isArchived, record.isHidden, record.isObsolete].some(Boolean)
        && record.isActive !== false && record.isCurrent !== false && record.isPublished !== false
        && (record.status === undefined || ['active', 'published', 'current'].includes(record.status));
}
function source(record, id, title, url, kind, sections) {
    return { id, revision: revision(record), title, url: origin + url, kind, sections,
        fragment_url: origin + '/closed-search-fragment',
        authority: record.searchAuthority === undefined ? 'reference' : record.searchAuthority, ...access(record),
        status: current(record) ? 'published' : 'archived',
        ...(record.expiresAt ? { expires_at: Date.parse(record.expiresAt) / 1000 } : {}),
        ...(record.validFrom ? { valid_from: Date.parse(record.validFrom) / 1000 } : {}) };
}
function build() {
    const sources = [];
    for (const record of read('tea_hub_dynamic_route_v2')) {
        const sections = [1, 2, 3].filter(n => record[`t${n}`]).map(n => ({ heading: record[`h${n}`] || record.title, text: record[`t${n}`] }));
        sources.push(source(record, `route:${record.id}`, record.title || 'Урок', `/tasks?tab=edu&routeId=${encodeURIComponent(record.id)}`, 'lesson', sections));
    }
    for (const section of read('tea_hub_dynamic_basics_v2')) for (const module of section.modules || []) {
        // quiz/correct answer fields deliberately excluded, even for the admin search.
        const sections = [1, 2, 3].filter(n => module[`t${n}`]).map(n => ({ heading: module[`h${n}`] || module.title, text: module[`t${n}`] }));
        const item = source(module, `basic:${module.id}`, module.title || section.title,
            `/closed-search-material?basicId=${encodeURIComponent(module.id)}`, 'lesson', sections);
        item.parents = [{ ...access(section), status: current(section) ? 'published' : 'archived' }];
        item.revision = revision([module, section]);
        sources.push(item);
    }
    for (const record of read('tea_hub_urgent_files_v1')) {
        if (record.isDocPlaceholder || record.isTest || record.id?.startsWith('deadline_')) continue;
        const data = record.data || read(`file_data_${record.id}`);
        if (typeof data !== 'string' || !data.startsWith('data:')) continue;
        const item = source(record, `document:${record.id}`, record.name || 'Документ', `/tasks?tab=docs&documentId=${encodeURIComponent(record.id)}`, 'document', []);
        item.file_data = data; item.file_name = record.name; item.revision = revision([record, data]);
        // Тип каталога приходит из проверенного импорта, не из текста файла.
        if (record.searchCatalog !== undefined) item.catalog_items = record.searchCatalog;
        sources.push(item);
    }
    return { sources, aliases: { 'наличные': ['наличка', 'деньги'], 'перчатки': ['перчаток'], 'заваривание': ['заварить', 'заваривать'], 'лояльность': ['бонусная программа'], 'пересменка': ['передача смены'] } };
}
let previous = '';
async function tick() {
    const payload = build();
    const fingerprint = revision(payload);
    const status = await fetch(endpoint + '/status', { headers, signal: AbortSignal.timeout(5000) }).then(r => r.json());
    if (fingerprint !== previous || status.state === 'unavailable' || status.state === 'failed') {
        const response = await fetch(endpoint + '/snapshot', { method: 'POST', headers, body: JSON.stringify(payload), signal: AbortSignal.timeout(15000) });
        if (!response.ok) throw new Error(`snapshot_status_${response.status}`);
        previous = fingerprint;
        console.log(JSON.stringify({ event: 'snapshot_submitted', sources: payload.sources.length }));
    }
}
async function main() {
    do {
        try { await tick(); } catch { console.error(JSON.stringify({ event: 'closed_export_retry' })); }
        if (process.argv.includes('--once')) break;
        await new Promise(resolve => setTimeout(resolve, 2500));
    } while (true);
}
if (require.main === module) main().catch(() => { console.error('closed_export_stopped'); process.exitCode = 1; });
module.exports = { build };

````

## evaluation/closed_search/fragments_http.cjs

SHA-256: e2278e00172682ffddcbbca0fca2608a2b62ff561866d4bee562afc16762a3a4

````javascript
/* Адресные ссылки и смена редакций: только локальный стенд и учебная запись. */
const { fs, path, lab, origin, call, login, storage } = require('./lab_client.cjs');
const { waitSnapshot } = require('./scan_http.cjs');
const ID = 'fragment_edition_fixture';
const text = '# Приёмка поставки Линза\nПоставку Линза принимает координатор.\n\n# Хранение поставки Линза\nПоставка Линза хранится 7 календарных дней.';
const dataUrl = value => 'data:text/markdown;base64,' + Buffer.from(value).toString('base64');
const base = { id: ID, name: 'Учебная_проверка_редакций.md', section: 'Учебные проверки ВатэсAi',
    hasSeparateData: true, size: Buffer.byteLength(text) + ' B', date: 'Учебный файл 30.10.2026',
    searchAuthority: 'reference', accessPolicy: { version: 1, visibility: 'restricted', userIds: ['closed_lab_b'] } };

async function main() {
    const cookies = { admin: await login('closed-admin'), a: await login('closed-a'), b: await login('closed-b') };
    const cases = [], started = Date.now();
    const check = (id, pass, detail = {}) => { cases.push({ id, pass: Boolean(pass), ...detail }); };
    async function setRecord(record) {
        const catalog = await storage('tea_hub_urgent_files_v1', cookies.admin);
        const others = catalog.filter(item => item.id !== ID);
        await storage('tea_hub_urgent_files_v1', cookies.admin, record ? [...others, record] : others);
    }
    const existing = (await storage('tea_hub_urgent_files_v1', cookies.admin)).find(item => item.id === ID);
    if (existing && (JSON.stringify(existing) !== JSON.stringify(base) || await storage('file_data_' + ID, cookies.admin) !== dataUrl(text))) {
        throw new Error('Fixture changed outside this acceptance; refusing overwrite');
    }
    await storage('file_data_' + ID, cookies.admin, dataUrl(text));
    await setRecord(base);
    await fs.mkdir(path.join(lab, 'fragment-assets'), { recursive: true });
    await fs.writeFile(path.join(lab, 'fragment-assets/manifest.json'), JSON.stringify([{ id: ID, name: base.name }]));
    let pairLinks = [], oldLink = '', newLink = '';
    async function ask(question, documentId) {
        const result = await call('/api/ai-chat', { chatId: 'fragment-' + started + '-' + cases.length,
            ...(documentId ? { documentId } : {}), messages: [{ role: 'user', content: question }] }, cookies.b);
        if (!result.response.ok) throw new Error('Question status ' + result.response.status + '; Retry-After ' + result.response.headers.get('retry-after'));
        return result.value;
    }
    async function open(link, actor = 'b') {
        const url = new URL(link, origin);
        if (url.pathname !== '/closed-search-fragment') throw new Error('Missing fragment link: ' + url.pathname);
        const result = await call('/api/closed-search-fragment' + url.search, undefined, cookies[actor]);
        return { http: result.response.status, cache: result.response.headers.get('cache-control'), ...result.value };
    }
    try {
        await waitSnapshot();
        const pair = await ask('Сравни объем чахаев Дао и Феникс');
        pairLinks = pair.sourceLinks || [];
        check('different-links-same-file', pairLinks.length === 2 && pairLinks[0].url !== pairLinks[1].url, { links: pairLinks });
        for (const [i, expected] of ['190 мл', '140 мл'].entries()) {
            const result = await open(pairLinks[i].url);
            const visible = (result.heading || '') + '\n' + (result.text || '');
            check('real-product-' + i, result.http === 200 && visible.includes(expected) && result.kind === 'product'
                && !visible.includes(i ? '190 мл' : '140 мл'), { result });
        }
        const denied = await open(pairLinks[0].url, 'a');
        check('profile-a-denied-real-product', denied.http === 404 && !denied.title && !denied.text, { result: denied });
        const anonymous = await open(pairLinks[0].url, 'anonymous');
        check('anonymous-denied', anonymous.http === 401 && !anonymous.text, { result: anonymous });
        const answer = await ask('Кто принимает поставку Линза?', ID);
        oldLink = answer.sourceLinks?.[0]?.url;
        if (!oldLink) throw new Error('Fixture answer has no link');
        const old = await open(oldLink);
        check('specific-document-fragment', old.http === 200 && old.text.includes('координатор') && !old.text.includes('7 календарных'), { result: old });
        check('no-cache', /no-store/.test(old.cache), { cache: old.cache });
        const invalid = new URL(oldLink, origin); invalid.searchParams.set('fragment', '0'.repeat(64));
        const unknown = await open(invalid.href);
        check('unknown-fragment-not-substituted', unknown.http === 404 && !unknown.text);
        await setRecord({ ...base, accessPolicy: { version: 1, visibility: 'admin' } });
        const revoked = await open(oldLink);
        check('revocation-without-waiting-for-index', revoked.http === 404 && !revoked.title && !revoked.text);
        await setRecord(base);
        await storage('file_data_' + ID, cookies.admin, dataUrl(text.replace('координатор', 'диспетчер')));
        const stale = await open(oldLink);
        check('changed-before-index-ready', stale.http === 409 && !stale.text && !stale.title, { result: stale });
        await waitSnapshot();
        const after = await open(oldLink);
        check('old-link-stays-changed', after.http === 409 && !after.text);
        const revised = await ask('Кто принимает поставку Линза?', ID);
        newLink = revised.sourceLinks?.[0]?.url;
        const current = await open(newLink);
        check('new-answer-new-revision', newLink !== oldLink && current.http === 200 && current.text.includes('диспетчер') && !current.text.includes('координатор'), { answer: revised, result: current });
        await setRecord({ ...base, archived: true });
        const archived = await open(newLink);
        check('archived-blocked', archived.http === 404 && !archived.text);
        await setRecord(null);
        const deleted = await open(newLink);
        check('deleted-blocked', deleted.http === 404 && !deleted.text);
        const unaffected = await open(pairLinks[1].url);
        check('unrelated-product-unchanged', [200, 503].includes(unaffected.http) && (unaffected.http !== 200 || unaffected.heading.includes('140 мл')));
    } finally {
        // Возвращаем только нашу учебную запись. Другие документы не откатываются.
        await storage('file_data_' + ID, cookies.admin, dataUrl(text));
        await setRecord(base);
        await waitSnapshot();
    }
    const restored = await open(oldLink);
    check('fixture-restored', restored.http === 200 && restored.text.includes('координатор'));
    const output = path.join(lab, 'fragments-http-' + started + '.json');
    await fs.writeFile(output, JSON.stringify({ createdAt: new Date().toISOString(), pairLinks, oldLink, newLink,
        total: cases.length, passed: cases.filter(item => item.pass).length, cases }, null, 2));
    console.log(JSON.stringify({ file: output, total: cases.length, passed: cases.filter(item => item.pass).length,
        failures: cases.filter(item => !item.pass) }));
    if (cases.some(item => !item.pass)) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## evaluation/closed_search/fragments_postcheck.cjs

SHA-256: 32bce3e0a9ddfad41e239ebf54905a27f222d70bf43b5de2e8056041d75376d1

````javascript
/* Повторное открытие готовых ссылок после рестарта и проверка других видов источников. */
const { fs, path, lab, origin, call, login } = require('./lab_client.cjs');
async function main() {
    const reportFile = process.argv[2];
    if (!reportFile || path.basename(reportFile) !== reportFile) throw new Error('Pass a report filename inside the lab');
    const prior = JSON.parse(await fs.readFile(path.join(lab, reportFile), 'utf8'));
    const b = await login('closed-b'), a = await login('closed-a');
    const cases = [];
    function check(id, pass, details = {}) { cases.push({ id, pass: Boolean(pass), ...details }); }
    async function open(link, cookie = b) {
        const url = new URL(link, origin);
        return call('/api/closed-search-fragment' + url.search, undefined, cookie);
    }
    for (const link of prior.pairLinks) {
        const result = await open(link.url);
        check('restored-' + link.title, result.response.status === 200 && result.value.heading === link.title);
    }
    const restored = await open(prior.oldLink);
    check('document-link-after-restart', restored.response.status === 200 && restored.value.text.includes('координатор'));
    const stale = await open(prior.newLink);
    check('stale-link-after-restart', stale.response.status === 409 && !stale.value.text);
    // Проверяем и HTML-страницу: отказ API не должен обходиться через SSR.
    for (const [actor, cookie, expected] of [['allowed', b, true], ['denied', a, false], ['anonymous', '', false]]) {
        const response = await fetch(origin + prior.pairLinks[0].url, { headers: cookie ? { Cookie: cookie } : {}, signal: AbortSignal.timeout(30000) });
        const html = await response.text();
        const hasTitle = html.includes('Чахай фарфор');
        check('html-' + actor, hasTitle === expected, { http: response.status, hasTitle, cache: response.headers.get('cache-control') });
    }
    for (const item of [
        { id: 'lesson', q: 'Где в базе отражаются оплаты по QR-коду через монитор?', source: 'route:route_24', has: 'По QR-коду' },
        // На стенде есть ещё скан с другим правилом Нефрита. Без выбора
        // документа конфликт верен; отсылку проверяем в выбранном источнике.
        { id: 'cross-document', q: 'Упаковка вскрыта. Сколько хранится товар Нефрит?', documentId: 'link_base', source: 'document:link_exception', has: '2 календарных' },
    ]) {
        const answer = await call('/api/ai-chat', { chatId: 'fragment-post-' + Date.now(),
            ...(item.documentId ? { documentId: item.documentId } : {}), messages: [{ role: 'user', content: item.q }] }, b);
        const links = answer.value.sourceLinks || [];
        const target = links.find(link => new URL(link.url, origin).searchParams.get('source') === item.source);
        if (!target) { check(item.id, false, { answer: answer.value }); continue; }
        const opened = await open(target.url);
        check(item.id, opened.response.status === 200 && opened.value.text.includes(item.has), { answer: answer.value, fragment: opened.value });
    }
    const conflict = await call('/api/ai-chat', { chatId: 'fragment-conflict-' + Date.now(),
        messages: [{ role: 'user', content: 'Упаковка вскрыта. Сколько хранится товар Нефрит?' }] }, b);
    check('conflicting-fixtures-do-not-create-a-link', conflict.value.search_status === 'conflict' && !conflict.value.sourceLinks?.length);
    const file = path.join(lab, 'fragments-postcheck-' + Date.now() + '.json');
    await fs.writeFile(file, JSON.stringify({ total: cases.length, passed: cases.filter(item => item.pass).length, cases }, null, 2));
    console.log(JSON.stringify({ file, total: cases.length, passed: cases.filter(item => item.pass).length, failures: cases.filter(item => !item.pass) }));
    if (cases.some(item => !item.pass)) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## evaluation/closed_search/verify_lab.cjs

SHA-256: 5044206631faf0051f396e116809354039c35e6d5f741b83e65fa0515bb0f866

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
    const closedOnly = process.argv.includes('--closed-only');
    // Общий чат на 8000 не участвует в отдельном закрытом поиске на 8001.
    // По умолчанию по-прежнему требуем оба сервиса; отдельный режим явный.
    let generalStatus = 0;
    try {
        const general = await fetch('http://127.0.0.1:8000/health', { signal: AbortSignal.timeout(10000) });
        generalStatus = general.status;
    } catch (error) {
        if (!closedOnly) throw error;
    }
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
    let comparisonCount = 0;
    let fragmentCount = 0;
    try {
        fragmentCount = (JSON.parse(await fs.readFile(path.join(lab, 'fragment-assets/manifest.json'), 'utf8'))).length;
    } catch (error) {
        if (error.code !== 'ENOENT') throw error;
    }
    try {
        comparisonCount = (JSON.parse(await fs.readFile(path.join(lab, 'comparison-assets/manifest.json'), 'utf8'))).length;
    } catch (error) {
        if (error.code !== 'ENOENT') throw error;
    }
    const expectedDocuments = 9 + reasoningFixtures.documents.length + conditionFixtures.documents.length + structureFixtures.documents.length + structureAssets.length + scanFixtures.documents.length + layoutFixtures.documents.length + qualityFixtures.documents.length + multipageFixtures.documents.length + crossFixtures.documents.length + linkFixtures.documents.length + realManifest.documents.length + comparisonCount + fragmentCount;
    const result = { state: status.state, indexedSources: status.sources, indexedPassages: status.passages,
        restoredFromDisk: status.restored_from_disk, restoreMs: status.restore_ms, persistedContextCount: status.context_count,
        catalogDocuments: catalog.length, coreWithoutKey: unauthenticatedCore.status,
        chatWithoutSession: unauthenticatedSite.response.status, generalApiHealth: generalStatus, generalApiRequired: !closedOnly,
        expectedSources, expectedDocuments, pass: status.state === 'ready' && status.sources === expectedSources && catalog.length === expectedDocuments
            && unauthenticatedCore.status === 403 && unauthenticatedSite.response.status === 401 && (closedOnly || generalStatus === 200) };
    await fs.writeFile(path.join(lab, 'final-service-status.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
    if (!result.pass) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## evaluation/closed_search/finalize_fragments.cjs

SHA-256: 6f58faed13154b5faaddaf33d538b87b5e158453d8ce2ede59b76daadea0ebab

````javascript
/* Проверяет результаты адресных ссылок и обновляет локальные Markdown-журналы. */
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const site = path.join(lab, 'tea-app-main');
const reportName = 'CLOSED_SEARCH_FRAGMENTS_REPORT_2026_10_30.md';
const codeName = 'CLOSED_SEARCH_FRAGMENTS_CODE_SNAPSHOT_2026_10_30.md';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const read = async file => JSON.parse((await fs.readFile(path.join(lab, file), 'utf8')).replace(/^\uFEFF/, ''));

async function prepend(file, entry) {
    const text = await fs.readFile(file, 'utf8');
    if (text.includes(entry.split('\n')[0])) throw new Error('Stage already recorded: ' + file);
    const offset = text.indexOf('\n');
    if (offset < 0) throw new Error('Missing Markdown heading');
    return { file, text: text.slice(0, offset + 1) + '\n' + entry + '\n\n' + text.slice(offset + 1) };
}

async function main() {
    const http = await read('fragments-http-1793342254955.json');
    const post = await read('fragments-postcheck-1793342490680.json');
    const status = await read('fragments-services.json');
    const manifest = await read('real-materials/manifest.json');
    const unit = await fs.readFile(path.join(lab, 'fragments-unittest.log'), 'utf8');
    const integrity = { originals: [], copies: [], code: [] };
    for (const input of manifest.inputs) {
        const actual = sha(await fs.readFile(path.join('C:/Users/KROLL/Downloads', input.name)));
        integrity.originals.push({ name: input.name, expected: input.sha256, actual, pass: input.sha256 === actual });
    }
    for (const doc of manifest.documents) {
        const actual = sha(await fs.readFile(path.join(lab, 'real-materials', doc.name)));
        integrity.copies.push({ name: doc.name, expected: doc.sha256, actual, pass: doc.sha256 === actual });
    }
    if (http.passed !== http.total || post.passed !== post.total || !status.pass || !status.restoredFromDisk
        || !/Ran 232 tests/.test(unit) || !/\bOK\s*$/.test(unit)
        || !integrity.originals.every(item => item.pass) || !integrity.copies.every(item => item.pass)) {
        throw new Error('Acceptance evidence is incomplete');
    }
    for (const file of ['fragment-product-proof.png', 'fragment-document-proof.png', 'fragment-edition-proof.png']) {
        if (!(await fs.stat(path.join(lab, file))).size) throw new Error('Browser proof missing');
    }
    const report = `# Адресные ссылки из ответов ВатэсAi — 30.10.2026

Основной проект: C:/Users/KROLL/Desktop/Ai. Работа выполнена в собственном
поисковом ядре и отдельном стенде 3040 / API 8001. Рабочая LMS не изменялась.
Обучение весов, публикация и отправка в GitHub не выполнялись.

## Результат для пользователя

Новые ответы с подтверждённой цитатой ведут к отдельной карточке товара или
нужному фрагменту документа/урока. Две позиции из одного файла получают разные
адреса. На странице показаны название, источник и нужный текст; есть возврат
к диалогу и возможность открыть исходный материал целиком. Служебные параметры
не выводятся в тексте страницы. HTML из источника не исполняется.

Пример: из сравнения подставок «Хайвэй» и «Звездочка» первая ссылка открывает
металлическую подставку, вторая — стеклянную. В обеих карточках сохраняется
оговорка, что свечки в комплект не входят. Ссылка «Приёмка поставки Линза»
открывает только правило о координаторе, без соседнего раздела хранения.
Линза — явно учебный документ для проверки смены редакций.

## Доступ и актуальность

Каждый адрес содержит источник, его редакцию и контрольную сумму фрагмента.
Это адрес, не разрешение: сервер сайта заново проверяет текущую сессию,
доступ и редакцию. После получения текста от поискового API выполняется
повторная проверка, чтобы учесть изменение прав во время запроса.

| Ситуация | Проверенное поведение |
|---|---|
| Доступный товар | Только нужная карточка |
| Другой профиль без доступа | Нет текста и названия закрытого материала |
| Нет сессии | API требует вход; страница не раскрывает содержимое |
| Доступ отозван | Старый адрес блокируется сразу, без ожидания индексации |
| Документ изменён | Сообщение «Документ обновлён»; старый фрагмент не показывается |
| Новый вопрос после обработки | Новый ответ и ссылка на новую редакцию |
| Удаление или архивирование | Материал недоступен |
| Неизвестный фрагмент | Нет подстановки первого или похожего раздела |
| Перезапуск API | Существующие ссылки продолжают открываться |
| Отсылка к другому документу | Цитата ведёт к документу с самим исключением |

Указатели строятся один раз при публикации индекса и восстанавливаются из
сохранённого текста. При клике нет OCR и повторного извлечения документа.
Сервер сверяет только выбранный источник; ответ API имеет private, no-store.
Тексты открываются из проверенного индекса, а не из параметров браузерной ссылки.

## Проверки и обнаруженные особенности

- 232/232 локальных теста за 7,015 с, включая 16 новых проверок адресов.
- Основная HTTP-проверка: ${http.passed}/${http.total}; два профиля, отсутствие сессии,
  смена содержимого, отзыв прав, удаление и архивирование учебной записи.
- После настоящего рестарта API: ${post.passed}/${post.total}, включая HTML-страницы
  для Б, А и анонимного посетителя; урок и отсылку между документами.
- Четыре изменённых/новых TS/TSX-файла прошли проверку синтаксиса. Страница и
  маршрут API скомпилированы Next.js и проверены реальными запросами.
  Полная проверка типов всего архивного проекта не заявляется.
- В браузере нажаты обе ссылки сравнения, открыт фрагмент обычного документа,
  проверено сообщение для другой редакции. Ошибок браузера на проверке нет.
- ${integrity.originals.length}/${integrity.originals.length} оригиналов Downloads и ${integrity.copies.length}/${integrity.copies.length} подготовленных копий сохранили SHA-256.

Первая HTTP-попытка обнаружила факт, записанный только в названии товара:
объём чахая. Сопоставление расширено на заголовок именно товарной карточки;
добавлен отдельный локальный тест. Факты из названия не переносятся в выдуманное
описание: карточка показывает исходный заголовок и отсутствие описания.

Первый дополнительный прогон дал 8/9 из-за неверного ожидания теста о Нефрите.
На стенде одновременно есть учебный скан со сроком 1 день и другой учебный
регламент со сроком 2 дня. Общий вопрос правильно дал конфликт. Проверка
отсылки теперь явно выбирает исходный документ, а общий конфликт проверяется
отдельно. Итог 10/10 — исправленная регрессия, не независимая слепая приёмка.

В тестах состояния менялись только у новой записи fragment_edition_fixture.
В конце восстановлены её исходные текст и права. Чужие записи не откатывались.
Система хранит ${status.catalogDocuments} документов / ${status.indexedSources} источников /
${status.indexedPassages} фрагмент. Индекс восстановлен с диска за ${status.restoreMs} мс.
Предыдущие нагрузочные цифры не относятся к этой версии; новый нагрузочный
прогон в этом этапе не выполнялся.

## Границы реализации и следующий этап

Если буквальную цитату нельзя надёжно привязать к фрагменту, сохраняется ссылка
на полный источник. Существующие сообщения истории не переписываются: адресные
ссылки появляются в новых ответах. Старые ссылки на целый файл продолжают
открывать файл. Точная страница и подсветка координат внутри PDF ещё не реализованы:
сейчас открывается извлечённый текст фрагмента с переходом к полному источнику.
При обновлении документа ссылка не угадывает «такой же» абзац в новой редакции.

Следующий этап: связать фрагмент с конкретной страницей PDF и подсветить место
в исходнике; проверить перенос/удаление разделов при новой редакции. Затем —
приёмка на актуальных настоящих регламентах и сканах, когда они доступны.
Выгрузка 18.06.2026 остаётся архивной, её цены и остатки не объявлены текущими.

## Файлы и повторение проверки

План приёмки: evaluation/closed_search/FRAGMENT_ACCEPTANCE_2026_10_30.md.
Локальные тесты: tests/test_site_search_fragments.py и общий набор test_site_search*.py.
HTTP: node evaluation/closed_search/fragments_http.cjs.
После перезапуска: node evaluation/closed_search/fragments_postcheck.cjs <имя отчёта fragments-http из каталога стенда>.
Проверка сервисов: node evaluation/closed_search/verify_lab.cjs --closed-only.
Общий чат 8000 не участвует в этом стенде. Зависимости не менялись.

Отчёты в .runtime/closed-search-lab-20261028:
fragments-http-1793342254955.json;
fragments-postcheck-1793342490680.json;
fragments-postcheck-1793342438552.json (первая проверка 8/9);
fragments-unittest.log; fragments-services.json; fragments-integrity.json.
Снимки: fragment-product-proof.png, fragment-document-proof.png, fragment-edition-proof.png.
Полные листинги: ${codeName}.
`;
    const entry = `## 2026-10-30 — адресные ссылки, права и редакции источников

Новые ответы открывают конкретную карточку или фрагмент, включая факты из
названия товара. Адрес привязан к редакции. Сервер проверяет текущие права
до и после получения текста: отзыв доступа, удаление и архивирование блокируют
старую ссылку; новая редакция требует повторить вопрос. OCR при клике не запускается.

Проверено: 232/232 локальных теста, HTTP 16/16, после рестарта 10/10;
в браузере — две разные карточки, раздел документа и сообщение об обновлении.
Оригиналы и 42 копии сохранены. Стенд: 126 документов / 209 источников / 2 851 фрагмент.
Рабочая LMS, обучение весов и публикация не затронуты.

Отчёт: ${reportName}. Полный код: ${codeName}.
Далее: конкретная страница PDF и подсветка фрагмента, смена положения разделов;
затем актуальные настоящие документы/сканы. История прежних сообщений сохраняет
старые ссылки на целые файлы. Ниже сохранена история этапов.`;
    const siteEntry = `## 2026-10-30 — адресное открытие источника ответа

Только изолированный стенд 3040 / API 8001. Добавлены страница
/closed-search-fragment и закрытый серверный маршрут с проверкой сессии,
доступа и текущей редакции. Переход из ответа открывает конкретный товар/раздел;
изменившийся или закрытый источник не раскрывает старый текст. OCR при клике нет.
В AIAssistant разрешён только новый внутренний путь. Добавлена одна учебная
запись для проверки редакций; исходное состояние восстановлено после испытаний.

Проверено: HTTP 16/16 + 10/10 после рестарта, HTML без утечек для профиля А и
анонимного посетителя; открытие товаров и раздела в браузере. 126 документов.
Отчёт: C:/Users/KROLL/Desktop/Ai/${reportName}.
Запуск не изменён, публикация не выполнялась. Далее — страница и подсветка в PDF.`;
    const writes = [{ file: path.join(root, reportName), text: report }];
    for (const name of ['CLOSED_SEARCH_PLAN_2026_10_28.md', 'README.md', 'PROGRESS_LOG.md', 'DECISIONS.md']) {
        writes.push(await prepend(path.join(root, name), entry));
    }
    for (const name of ['PROJECT_CONTEXT.md', 'TASKS.md', 'HANDOFF.md', 'DEPLOYMENT.md']) {
        writes.push(await prepend(path.join(site, name), siteEntry));
    }
    const files = ['ai_core/site_search_fragments.py', 'ai_core/site_search.py', 'ai_core/site_search_api.py',
        'ai_core/site_search_runtime.py', 'ai_core/site_search_store.py', 'tests/test_site_search_fragments.py',
        'evaluation/closed_search/export_site.cjs', 'evaluation/closed_search/fragments_http.cjs',
        'evaluation/closed_search/fragments_postcheck.cjs', 'evaluation/closed_search/verify_lab.cjs',
        'evaluation/closed_search/finalize_fragments.cjs',
        ...['app/lib/vatesFragment.ts', 'app/api/closed-search-fragment/route.ts', 'app/closed-search-fragment/page.tsx',
            'app/tasks/components/AIAssistant.tsx'].map(file => '.runtime/closed-search-lab-20261028/tea-app-main/' + file)];
    let snapshot = '# Полные листинги адресных ссылок — 30.10.2026\n\n';
    for (const file of files) {
        const bytes = await fs.readFile(path.join(root, file)), hash = sha(bytes);
        integrity.code.push({ file, sha256: hash, bytes: bytes.length });
        const language = file.endsWith('.py') ? 'python' : file.endsWith('.tsx') ? 'tsx' : file.endsWith('.ts') ? 'typescript' : 'javascript';
        snapshot += '\n## ' + file + '\n\nSHA-256: ' + hash + '\n\n````' + language + '\n' + bytes.toString('utf8') + '\n````\n';
    }
    writes.push({ file: path.join(root, codeName), text: snapshot });
    writes.push({ file: path.join(lab, 'fragments-integrity.json'), text: JSON.stringify(integrity, null, 2) });
    for (const output of writes) await fs.writeFile(output.file, output.text, 'utf8');
    console.log(JSON.stringify({ report: reportName, code: codeName, sourceFiles: files.length,
        originals: integrity.originals.length, copies: integrity.copies.length, allUnchanged: true }));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## .runtime/closed-search-lab-20261028/tea-app-main/app/lib/vatesFragment.ts

SHA-256: a7f709ddc4b36e5392bc96e3b9c73343cf20ea9045fd05b7814017afd464a4e2

````typescript
import { createHash } from 'node:crypto';
import { readDataValue } from '@/app/lib/storage/dataStore';
import { isHiddenSystemUser, type SessionUser } from '@/app/lib/serverAuth';
import { canReadClosedRecord } from '@/app/lib/vatesClosedSearch';

type Entry = Record<string, unknown>;
export type FragmentRequest = { source?: string; revision?: string; fragment?: string };
export type FragmentResult = {
    status: 'ready' | 'not_found' | 'changed' | 'processing' | 'unavailable';
    message?: string;
    title?: string;
    heading?: string;
    text?: string;
    kind?: string;
    sourceUrl?: string;
};
const rows = (value: unknown): Entry[] => Array.isArray(value)
    ? value.filter((item): item is Entry => Boolean(item) && typeof item === 'object' && !Array.isArray(item)) : [];
const revisionOf = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const unavailable = (): FragmentResult => ({ status: 'not_found', message: 'Материал недоступен.' });
const changed = (): FragmentResult => ({ status: 'changed', message: 'Материал изменился. Повторите вопрос, чтобы получить ссылку на актуальную редакцию.' });

/** Для открытия ссылки проверяем только нужный источник. Извлечение текста здесь не запускается. */
async function currentRevision(source: string, user: SessionUser): Promise<string | undefined> {
    const split = source.indexOf(':');
    const kind = source.slice(0, split), id = source.slice(split + 1);
    if (!id || !['document', 'route', 'basic'].includes(kind)) return undefined;
    if (kind === 'basic') {
        const matches: Array<[Entry, Entry]> = [];
        for (const section of rows(await readDataValue('tea_hub_dynamic_basics_v2', []))) {
            for (const item of rows(section.modules)) if (item.id === id) matches.push([item, section]);
        }
        if (matches.length !== 1 || !matches[0].every(item => canReadClosedRecord(item, user))) return undefined;
        return revisionOf(matches[0]);
    }
    const key = kind === 'document' ? 'tea_hub_urgent_files_v1' : 'tea_hub_dynamic_route_v2';
    const matches = rows(await readDataValue(key, [])).filter(item => item.id === id);
    if (matches.length !== 1 || !canReadClosedRecord(matches[0], user)) return undefined;
    const item = matches[0];
    if (kind === 'route') return revisionOf(item);
    if (item.isDocPlaceholder || item.isTest || id.startsWith('deadline_')) return undefined;
    const data = item.data || await readDataValue(`file_data_${id}`, '');
    return typeof data === 'string' && data.startsWith('data:') ? revisionOf([item, data]) : undefined;
}

export async function resolveVatesFragment(input: FragmentRequest, user: SessionUser): Promise<FragmentResult> {
    try {
        const { source, revision, fragment } = input;
        if (process.env.VATES_SEARCH_LAB !== 'true' || isHiddenSystemUser(user)
            || typeof source !== 'string' || source.length > 150
            || typeof revision !== 'string' || !/^[a-f0-9]{64}$/.test(revision)
            || typeof fragment !== 'string' || !/^[a-f0-9]{64}$/.test(fragment)) return unavailable();
        // Сначала доступ, затем редакция: запрещённый документ не раскрывает даже своё название.
        const current = await currentRevision(source, user);
        if (!current) return unavailable();
        if (current !== revision) return changed();
        const endpoint = process.env.VATES_CLOSED_SEARCH_URL, key = process.env.VATES_CLOSED_SEARCH_KEY;
        if (!endpoint || !key) throw new Error('fragment_not_configured');
        const response = await fetch(endpoint + '/fragment', {
            method: 'POST', cache: 'no-store', signal: AbortSignal.timeout(15000),
            headers: { 'Content-Type': 'application/json', 'X-Vates-Search-Key': key },
            body: JSON.stringify({ source, revision, fragment,
                principal: { user_id: user.id, role: user.role }, current_revisions: { [source]: current } }),
        });
        if (!response.ok) throw new Error('fragment_request_failed');
        const result = await response.json();
        // Повторная проверка закрывает смену прав/редакции во время запроса к поиску.
        const latest = await currentRevision(source, user);
        if (!latest) return unavailable();
        if (latest !== revision) return changed();
        if (result.status === 'processing') return { status: 'processing', message: 'Материал обновляется. Попробуйте открыть ссылку позже.' };
        if (result.status !== 'ready') return unavailable();
        if (result.id !== source || result.revision !== revision || result.fragment !== fragment
            || typeof result.text !== 'string' || typeof result.heading !== 'string' || typeof result.title !== 'string') {
            throw new Error('fragment_response_invalid');
        }
        const url = new URL(String(result.source_url));
        if (url.origin !== 'http://127.0.0.1:3040' || !['/tasks', '/closed-search-material'].includes(url.pathname)) {
            throw new Error('fragment_source_invalid');
        }
        return { status: 'ready', title: result.title, heading: result.heading, text: result.text,
            kind: result.kind === 'product' ? 'product' : 'fragment', sourceUrl: url.pathname + url.search + url.hash };
    } catch {
        console.error('closed_fragment_unavailable');
        return { status: 'unavailable', message: 'Не удалось открыть материал. Попробуйте позже.' };
    }
}

````

## .runtime/closed-search-lab-20261028/tea-app-main/app/api/closed-search-fragment/route.ts

SHA-256: 74f947808afabde1678c01d15c83b83b32115c4f3de3f9e22eea3f6a9fe14a86

````typescript
import { NextResponse } from 'next/server';
import { getSessionFromCookies } from '@/app/lib/serverAuth';
import { resolveVatesFragment } from '@/app/lib/vatesFragment';
import { assertRateLimit, getClientIdentifier, securityErrorResponse } from '@/app/lib/serverSecurity';

export const dynamic = 'force-dynamic';
const headers = { 'Cache-Control': 'private, no-store', Vary: 'Cookie' };

export async function GET(request: Request) {
    try {
        const user = await getSessionFromCookies();
        if (!user) return NextResponse.json({ message: 'Требуется вход в систему.' }, { status: 401, headers });
        assertRateLimit('closed-fragment', getClientIdentifier(request, user.id), 120, 5 * 60 * 1000);
        const params = new URL(request.url).searchParams;
        const result = await resolveVatesFragment({ source: params.get('source') || '',
            revision: params.get('revision') || '', fragment: params.get('fragment') || '' }, user);
        const status = result.status === 'ready' ? 200 : result.status === 'not_found' ? 404 : result.status === 'changed' ? 409 : 503;
        return NextResponse.json(result, { status, headers });
    } catch (error) {
        const security = securityErrorResponse(error);
        if (security) return security;
        return NextResponse.json({ message: 'Не удалось открыть материал. Попробуйте позже.' }, { status: 503, headers });
    }
}

````

## .runtime/closed-search-lab-20261028/tea-app-main/app/closed-search-fragment/page.tsx

SHA-256: 1f9b21dee6a9b926ad27e2b0c267f36f8f4b72e0dd6af4096067afb2d7bef35e

````tsx
import Link from 'next/link';
import { getSessionFromCookies } from '@/app/lib/serverAuth';
import { resolveVatesFragment, type FragmentRequest, type FragmentResult } from '@/app/lib/vatesFragment';

export const dynamic = 'force-dynamic';

/** Отдельная читаемая карточка: в браузер попадает только проверенный фрагмент. */
export default async function ClosedSearchFragment({ searchParams }: { searchParams: Promise<FragmentRequest> }) {
    let result: FragmentResult = { status: 'unavailable', message: 'Не удалось открыть материал. Попробуйте позже.' };
    let signedIn = false;
    try {
        const user = await getSessionFromCookies();
        signedIn = Boolean(user);
        result = user ? await resolveVatesFragment(await searchParams, user)
            : { status: 'not_found', message: 'Войдите в систему, чтобы открыть материал.' };
    } catch {
        console.error('closed_fragment_page_unavailable');
    }
    return <main style={{ maxWidth: 880, margin: '32px auto', padding: '24px', lineHeight: 1.65, color: '#17383d' }}>
        <nav style={{ marginBottom: 28 }}><Link href={signedIn ? '/tasks?tab=standards' : '/login'}>
            {signedIn ? 'Вернуться к диалогу' : 'Войти в систему'}
        </Link></nav>
        {result.status === 'ready' ? <article style={{ background: '#fff', border: '1px solid #dbe7e9', borderRadius: 20, padding: 28 }}>
            <p style={{ color: '#44767c', marginTop: 0 }}>{result.kind === 'product' ? 'Товар из материалов сайта' : 'Фрагмент документа'}</p>
            <h1 style={{ fontSize: 26, lineHeight: 1.3, overflowWrap: 'anywhere' }}>{result.heading || result.title}</h1>
            {result.heading !== result.title ? <p style={{ color: '#526b70' }}>Источник: {result.title}</p> : null}
            {/* React выводит текст безопасно: HTML и инструкции из файла не исполняются. */}
            <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', margin: '24px 0', fontSize: 17 }}>{result.text}</div>
            <Link href={result.sourceUrl || '/tasks?tab=docs'}>Открыть исходный материал целиком</Link>
        </article> : <section role="status" style={{ padding: 28, background: '#fff', borderRadius: 20 }}>
            <h1 style={{ fontSize: 24 }}>{result.status === 'changed' ? 'Документ обновлён' : 'Материал недоступен'}</h1>
            <p>{result.message}</p>
        </section>}
    </main>;
}

````

## .runtime/closed-search-lab-20261028/tea-app-main/app/tasks/components/AIAssistant.tsx

SHA-256: 139e13b5961a8115e12287bf356fdde7417585c5f003f39546207b3a91178263

````tsx
"use client";
import React, { useState, useEffect, useEffectEvent, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import CustomIcon from '@/app/components/CustomIcon';
import { isClientAdminView } from '@/app/lib/authClient';

interface Message {
    id: string;
    role: 'user' | 'ai';
    content: string;
    timestamp: string;
    sourceLinks?: Array<{ title: string; url: string }>;
}

interface ChatSession {
    id: string;
    title: string;
    messages: Message[];
    updatedAt: number;
    isPinned?: boolean; 
    documentId?: string;
}

const MAX_AI_HISTORY_MESSAGES = 10;
const MAX_AI_HISTORY_MESSAGE_CHARACTERS = 3_500;
const MAX_AI_CURRENT_MESSAGE_CHARACTERS = 12_000;
const MARKDOWN_STAR_CHARACTERS = /[*＊﹡∗⁎⁕✱✲✳]/gu;
const INTERNAL_LMS_LINK = /(?:https?:\/\/(?:www\.)?tea-hub\.ru)?\/tasks\?[^\s<>"'`]+/gu;

const limitAiText = (value: string, maxCharacters: number): string => {
    if (value.length <= maxCharacters) {
        return value;
    }

    // Оставляем конец текста, потому что там находится текущий вопрос
    // пользователя и самые свежие найденные совпадения.
    const headLength = Math.floor(maxCharacters * 0.65);
    const tailLength = maxCharacters - headLength;

    return `${value.slice(0, headLength)}\n\n[Контекст сокращен]\n\n${value.slice(-tailLength)}`;
};

const normalizeSearchValue = (value: unknown) => String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();

const normalizeAiHref = (value: string): string | null => {
    const trimmedValue = value.trim().replace(/[.,!?;:)\]]+$/u, '');

    try {
        const parsedUrl = new URL(trimmedValue, 'https://tea-hub.ru');
        const isInternalHost = parsedUrl.hostname === 'tea-hub.ru' || parsedUrl.hostname === 'www.tea-hub.ru';
        if (!isInternalHost || !['/tasks', '/closed-search-material', '/closed-search-fragment'].includes(parsedUrl.pathname)) {
            return null;
        }

        return `${parsedUrl.pathname}${parsedUrl.search}${parsedUrl.hash}`;
    } catch {
        return null;
    }
};

const hideInternalIdentifiers = (value: string): string => {
    const links: string[] = [];
    const textWithMaskedLinks = value.replace(INTERNAL_LMS_LINK, (link) => {
        const placeholder = `__LMS_LINK_${links.length}__`;
        links.push(link);
        return placeholder;
    });
    const cleanedText = textWithMaskedLinks
        .replace(/\b(?:id|ид|идентификатор)(?:\s+(?:документа|темы|теста|товара|источника|файла))?\s*[:№#-]?\s*[a-z0-9][a-z0-9_-]{2,}/giu, '')
        .replace(/\b(?:file|document|topic|test|product|route|lms|chat|msg)_[a-z0-9_-]{3,}\b/giu, '')
        .replace(/[ \t]{2,}/gu, ' ')
        .replace(/\n[ \t]+/gu, '\n')
        .replace(/\n{3,}/gu, '\n\n');

    return links.reduce((result, link, index) => {
        return result.replace(`__LMS_LINK_${index}__`, link);
    }, cleanedText).trim();
};

const cleanAiMessageText = (value: string): string => hideInternalIdentifiers(
    value
        .replace(/\r\n?/gu, '\n')
        .replace(/\\([*_`~#>])/gu, '$1')
        .replace(/^\s{0,3}#{1,6}\s*/gmu, '')
        .replace(/^\s{0,3}>\s?/gmu, '')
        .replace(MARKDOWN_STAR_CHARACTERS, '')
        .replace(/[`~]/gu, '')
        .replace(/(^|\n)\s*[-+]\s+/gmu, '$1')
        .replace(/\n{3,}/gu, '\n\n')
        .trim(),
);

const prepareAiMessageText = (value: string): string => cleanAiMessageText(
    value.replace(/\[([^\]]+)\]\(([^)\s]+)\)/gu, (...matches: string[]) => {
        const label = matches[1] || '';
        const href = matches[2] || '';
        const safeHref = normalizeAiHref(href);
        return safeHref ? `${label} ${safeHref}` : label;
    }),
);

const getAiLinkLabel = (href: string): string => {
    try {
        const parsedUrl = new URL(href, 'https://tea-hub.ru');
        if (parsedUrl.searchParams.has('routeId')) {
            return 'Открыть урок';
        }

        if (parsedUrl.searchParams.has('testId')) {
            return 'Открыть тест';
        }

        if (parsedUrl.searchParams.has('documentId')) {
            return 'Открыть документ';
        }

        if (parsedUrl.searchParams.has('productId')) {
            return 'Открыть карточку товара';
        }
    } catch {
        return 'Открыть';
    }

    return 'Открыть раздел';
};

const renderAiMessage = (value: string): React.ReactNode => {
    const messageText = prepareAiMessageText(value);
    const linkPattern = /(?:https?:\/\/[^\s<>"'`]+|\/tasks\?[^\s<>"'`]+)/gu;
    const parts: React.ReactNode[] = [];
    let cursor = 0;
    let match: RegExpExecArray | null;
    let linkIndex = 0;

    while ((match = linkPattern.exec(messageText)) !== null) {
        const rawLink = match[0];
        const safeHref = normalizeAiHref(rawLink);
        if (!safeHref) {
            continue;
        }

        if (match.index > cursor) {
            parts.push(messageText.slice(cursor, match.index));
        }

        parts.push(
            <a
                key={`ai-link-${linkIndex}`}
                href={safeHref}
                className="ai-message-link"
            >
                {getAiLinkLabel(safeHref)}
            </a>,
        );
        linkIndex += 1;
        cursor = match.index + rawLink.length;
    }

    if (parts.length === 0) {
        return messageText;
    }

    if (cursor < messageText.length) {
        parts.push(messageText.slice(cursor));
    }

    return parts;
};

const extractAiResponseText = (data: Record<string, unknown>): string => {
    const output = Array.isArray(data.output) ? data.output : [];
    const outputParts = output.flatMap((item) => {
        if (!item || typeof item !== 'object') {
            return [];
        }

        const content = (item as { content?: unknown }).content;
        if (!Array.isArray(content)) {
            return [];
        }

        return content.flatMap((contentItem) => {
            if (!contentItem || typeof contentItem !== 'object') {
                return [];
            }

            const text = (contentItem as { text?: unknown }).text;
            return typeof text === 'string' && text.trim() ? [text.trim()] : [];
        });
    });

    if (outputParts.length > 0) {
        return outputParts.join('\n\n');
    }

    const message = data.message;
    const messageText = message && typeof message === 'object'
        ? (message as { text?: unknown; content?: unknown })
        : {};
    const fallbackValues: unknown[] = [
        data.output_text,
        data.text,
        messageText.text,
        typeof messageText.content === 'string' ? messageText.content : '',
    ];
    const fallbackText = fallbackValues.find((item) => typeof item === 'string' && item.trim());
    return typeof fallbackText === 'string' ? fallbackText.trim() : '';
};

export default function AIAssistant({ userId, isAdmin }: { userId?: string, isAdmin?: boolean }) {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [sessions, setSessions] = useState<ChatSession[]>([]);
    const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
    const [inputValue, setInputValue] = useState("");
    const [isTyping, setIsTyping] = useState(false);
    const [showClearConfirm, setShowClearConfirm] = useState(false);
    const [isMobileHistoryOpen, setIsMobileHistoryOpen] = useState(false);
    const [historyQuery, setHistoryQuery] = useState("");
    const [isHistoryLoaded, setIsHistoryLoaded] = useState(false);
    
    const [currentUserId, setCurrentUserId] = useState<string | null>(null);
    
    const chatContainerRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const handledDocumentRequestRef = useRef('');

    // =========================================================================
    // ОПРЕДЕЛЕНИЕ ПОЛЬЗОВАТЕЛЯ (АДМИН - 100% ПРИОРИТЕТ)
    // =========================================================================
    useEffect(() => {
        const determineUser = () => {
            const storedUserId = localStorage.getItem('current_user_id') || localStorage.getItem('login') || localStorage.getItem('userId');
            const isSystemAccount = localStorage.getItem('is_system_account') === 'true';
            const isAdminView = isAdmin === true || isClientAdminView();

            if (isSystemAccount && isAdminView) {
                const normalizedSystemId = String(userId || storedUserId || 'system').replace(/[^a-zA-Z0-9_-]/g, '_');
                return `system_admin_${normalizedSystemId}`;
            }

            if (isAdminView || userId === 'admin') {
                return 'admin_master'; 
            }

            if (userId && userId !== 'guest' && userId.trim() !== '') {
                return 'emp_' + String(userId).replace(/[^a-zA-Z0-9_-]/g, '_');
            }

            const foundId = storedUserId;
            if (foundId && foundId !== 'guest' && foundId !== 'null') {
                return 'emp_' + String(foundId).replace(/[^a-zA-Z0-9_-]/g, '_');
            }

            let guestId = localStorage.getItem('th_stable_guest_id');
            if (!guestId) {
                guestId = 'guest_' + Math.random().toString(36).substr(2, 9);
                localStorage.setItem('th_stable_guest_id', guestId);
            }
            return guestId;
        };

        const activeUser = determineUser();
        const userIdTimer = window.setTimeout(() => setCurrentUserId(activeUser), 0);

        const loadHistory = async () => {
            let serverDataFound = false;
            try {
                const res = await fetch(`/api/storage?key=th_ai_history_${activeUser}&t=${Date.now()}`);
                if (res.ok) {
                    const data = await res.json();
                    if (Array.isArray(data) && data.length > 0) {
                        setSessions(data);
                        setActiveSessionId(data[0].id);
                        serverDataFound = true;
                    }
                }
            } catch {
                console.warn("Сервер недоступен, читаем из памяти");
            }

            if (!serverDataFound) {
                const savedSessions = localStorage.getItem(`th_ai_history_${activeUser}`);
                if (savedSessions) {
                    try {
                        const parsed = JSON.parse(savedSessions);
                        if (Array.isArray(parsed) && parsed.length > 0) {
                            setSessions(parsed);
                            setActiveSessionId(parsed[0].id);
                        }
                    } catch {}
                }
            }

            setIsHistoryLoaded(true);
        };

        loadHistory();
        return () => window.clearTimeout(userIdTimer);
    }, [userId, isAdmin]); 

    useEffect(() => {
        if (chatContainerRef.current) {
            chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
        }
    }, [sessions, activeSessionId, isTyping]);

    useEffect(() => {
        if (!showClearConfirm) {
            return;
        }

        const previousOverflow = document.body.style.overflow;
        const closeOnEscape = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                setShowClearConfirm(false);
            }
        };

        document.body.style.overflow = 'hidden';
        window.addEventListener('keydown', closeOnEscape);

        return () => {
            document.body.style.overflow = previousOverflow;
            window.removeEventListener('keydown', closeOnEscape);
        };
    }, [showClearConfirm]);

    const saveSessions = (newSessions: ChatSession[]) => {
        setSessions(newSessions);
        if (!currentUserId) return;

        localStorage.setItem(`th_ai_history_${currentUserId}`, JSON.stringify(newSessions));
        
        fetch('/api/storage', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                key: `th_ai_history_${currentUserId}`,
                data: newSessions 
            })
        }).catch(err => console.error("Ошибка сохранения на сервер", err));
    };

    const focusComposer = () => {
        window.setTimeout(() => inputRef.current?.focus(), 0);
    };

    const createNewSession = () => {
        const existingEmptySession = sessions.find((session) => session.messages.length === 0);

        if (existingEmptySession) {
            setActiveSessionId(existingEmptySession.id);
            setHistoryQuery("");
            setIsMobileHistoryOpen(false);
            focusComposer();
            return;
        }

        const newSession: ChatSession = {
            id: `chat_${Date.now()}`,
            title: "Новый диалог",
            messages: [],
            updatedAt: Date.now()
        };
        saveSessions([newSession, ...sessions]);
        setActiveSessionId(newSession.id);
        setHistoryQuery("");
        setIsMobileHistoryOpen(false);
        focusComposer();
    };

    const clearHistory = () => {
        saveSessions([]);
        setActiveSessionId(null);
        setShowClearConfirm(false);
        setIsMobileHistoryOpen(false);
    };

    const deleteSession = (sessionId: string, e: React.MouseEvent) => {
        e.stopPropagation(); 
        const filtered = sessions.filter((s: ChatSession) => s.id !== sessionId);
        saveSessions(filtered);
        
        if (activeSessionId === sessionId) {
            setActiveSessionId(filtered.length > 0 ? sortedSessions.filter(s => s.id !== sessionId)[0]?.id || null : null);
        }
    };

    const togglePin = (sessionId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        const updated = sessions.map(s => s.id === sessionId ? { ...s, isPinned: !s.isPinned } : s);
        saveSessions(updated);
    };

    const sortedSessions = [...sessions].sort((a, b) => {
        if (a.isPinned && !b.isPinned) return -1;
        if (!a.isPinned && b.isPinned) return 1;
        return b.updatedAt - a.updatedAt;
    });

    const normalizedHistoryQuery = normalizeSearchValue(historyQuery);
    const visibleSessions = sortedSessions.filter((session) => {
        if (!normalizedHistoryQuery) {
            return true;
        }

        return normalizeSearchValue(`${session.title} ${session.messages.map((message) => message.content).join(' ')}`).includes(normalizedHistoryQuery);
    });

    const formatMessageCount = (count: number) => {
        const lastTwoDigits = count % 100;
        const lastDigit = count % 10;

        if (lastTwoDigits >= 11 && lastTwoDigits <= 14) {
            return `${count} сообщений`;
        }

        if (lastDigit === 1) {
            return `${count} сообщение`;
        }

        if (lastDigit >= 2 && lastDigit <= 4) {
            return `${count} сообщения`;
        }

        return `${count} сообщений`;
    };

    const handleSendMessage = async (
        text: string,
        options: { documentId?: string; newSession?: boolean } = {},
    ) => {
        if (!text.trim() || !currentUserId || isTyping) return;

        let currentActiveId = options.newSession ? null : activeSessionId;
        let currentSessions = [...sessions];

        if (!currentActiveId || currentSessions.length === 0) {
            const newSession: ChatSession = {
                id: `chat_${Date.now()}`,
                title: text.slice(0, 25) + "...",
                messages: [],
                updatedAt: Date.now(),
                ...(options.documentId ? { documentId: options.documentId } : {}),
            };
            currentSessions = [newSession, ...currentSessions];
            currentActiveId = newSession.id;
            setActiveSessionId(currentActiveId);
        }

        const userMsg: Message = {
            id: `msg_${Date.now()}`,
            role: 'user',
            content: text,
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        };

        const updatedSessions = currentSessions.map((s: ChatSession) => {
            if (s.id === currentActiveId) {
                const newTitle = s.messages.length === 0 ? text.slice(0, 25) + "..." : s.title;
                return {
                    ...s,
                    title: newTitle,
                    messages: [...s.messages, userMsg],
                    updatedAt: Date.now(),
                    ...(options.documentId ? { documentId: options.documentId } : {}),
                };
            }
            return s;
        }); 

        saveSessions(updatedSessions);
        setInputValue("");
        if (inputRef.current) {
            inputRef.current.style.height = 'auto';
        }
        setIsTyping(true);

        try {
            const currentSession = updatedSessions.find((s: ChatSession) => s.id === currentActiveId);
            const activeDocumentId = options.documentId || currentSession?.documentId;

            const recentMessages = currentSession?.messages.slice(-MAX_AI_HISTORY_MESSAGES) || [];
            const apiMessages = recentMessages.map((message, index) => {
                const maxCharacters = index === recentMessages.length - 1
                    ? MAX_AI_CURRENT_MESSAGE_CHARACTERS
                    : MAX_AI_HISTORY_MESSAGE_CHARACTERS;

                return {
                    role: message.role === 'ai' ? 'assistant' : 'user',
                    content: limitAiText(message.content, maxCharacters),
                };
            });

            const response = await fetch('/api/ai-chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    messages: apiMessages,
                    chatId: currentActiveId,
                    ...(activeDocumentId ? { documentId: activeDocumentId } : {}),
                })
            });

            const data = await response.json() as Record<string, unknown>;

            if (data.error) {
                const errStr = JSON.stringify(data.error).toLowerCase();
                if (errStr.includes('quota') || errStr.includes('token') || errStr.includes('токен') || errStr.includes('limit') || errStr.includes('баланс') || errStr.includes('429')) {
                    throw new Error("TOKEN_LIMIT_EXCEEDED");
                }
                throw new Error(JSON.stringify(data.error));
            }

            if (!response.ok) throw new Error(`HTTP ${response.status}: ${JSON.stringify(data)}`);

            let aiText = extractAiResponseText(data);

            if (!aiText) {
                aiText = `СЫРОЙ ОТВЕТ ЯНДЕКСА:\n${JSON.stringify(data, null, 2)}`;
            }

            aiText = prepareAiMessageText(aiText);

            const aiMsg: Message = {
                id: `msg_${Date.now() + 1}`,
                role: 'ai',
                content: aiText,
                sourceLinks: Array.isArray(data.sourceLinks) ? data.sourceLinks : [],
                timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            };

            const finalSessions = updatedSessions.map((s: ChatSession) => 
                s.id === currentActiveId ? { ...s, messages: [...s.messages, aiMsg] } : s
            );
            saveSessions(finalSessions);

        } catch (error: unknown) {
            console.error("ОШИБКА:", error);

            const errorText = error instanceof Error ? error.message : String(error || 'Неизвестная ошибка');
            let displayError = `СИСТЕМНАЯ ОШИБКА:\n\n${errorText}`;
            const errStr = errorText.toLowerCase();
            
            if (errStr.includes('token') || errStr.includes('токен') || errStr.includes('quota') || errStr.includes('429') || errStr.includes('402') || errStr.includes('limit') || errStr.includes('баланс') || errStr.includes('too many requests')) {
                displayError = "Закончились токены, просьба обратиться к администратору.";
            }

            const errorMsg: Message = {
                id: `msg_${Date.now() + 1}`, role: 'ai',
                content: displayError,
                timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            };
            const finalSessions = updatedSessions.map((s: ChatSession) => 
                s.id === currentActiveId ? { ...s, messages: [...s.messages, errorMsg] } : s
            );
            saveSessions(finalSessions);
        } finally {
            setIsTyping(false); 
        }
    };

    const sendDocumentQuestion = useEffectEvent((question: string, documentId: string) => {
        void handleSendMessage(question, { documentId, newSession: true });
    });

    useEffect(() => {
        const documentId = searchParams.get('askDocumentId')?.trim() || '';
        if (!documentId || !currentUserId || !isHistoryLoaded || isTyping) {
            return;
        }

        const documentTitle = searchParams.get('askDocumentTitle')?.trim() || 'выбранный документ';
        const documentSection = searchParams.get('askDocumentSection')?.trim() || 'Основной раздел';
        const documentRequest = searchParams.get('askDocumentRequest')?.trim() || documentId;
        const requestKey = `${currentUserId}:${documentId}:${documentRequest}`;
        if (handledDocumentRequestRef.current === requestKey) {
            return;
        }

        handledDocumentRequestRef.current = requestKey;
        router.replace('/tasks?tab=standards', { scroll: false });
        sendDocumentQuestion(
            `Кратко расскажи, о чем документ «${documentTitle}», и выдели главное. Используй только этот документ. Раздел: «${documentSection}».`,
            documentId,
        );
    }, [currentUserId, isHistoryLoaded, isTyping, router, searchParams]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSendMessage(inputValue);
        }
    };

    const activeSession = sessions.find((s: ChatSession) => s.id === activeSessionId);

    if (!currentUserId) {
        return null;
    }

    return (
        <section className="ai-monolithic-section">
            <header className="vates-page-heading vates-ai-heading">
                <div>
                    <span className="vates-eyebrow">Ватэс AI</span>
                    <h1>AI-ассистент</h1>
                    <p>Быстрые ответы по регламентам, обучению, документам и товарной базе компании.</p>
                </div>
            </header>

            <div className="ai-monolithic-container">
                {isMobileHistoryOpen && (
                    <button
                        type="button"
                        className="ai-mobile-overlay"
                        aria-label="Закрыть историю диалогов"
                        onClick={() => setIsMobileHistoryOpen(false)}
                    />
                )}

                <aside className={`ai-sidebar custom-scroll ${isMobileHistoryOpen ? 'open' : ''}`}>
                    <div className="ai-sidebar-create">
                        <div className="ai-sidebar-heading">
                            <strong>История</strong>
                        </div>

                        <button
                            type="button"
                            className="hover-unified-app ai-new-session-button"
                            onClick={createNewSession}
                        >
                            <CustomIcon name="chat" size={17} color="currentColor" accent="none" />
                            Новый чат
                        </button>

                        {sessions.length > 0 && (
                            <label className="ai-history-search">
                                <span className="ai-history-search-icon" aria-hidden="true" />
                                <input
                                    type="search"
                                    value={historyQuery}
                                    onChange={(event) => setHistoryQuery(event.target.value)}
                                    placeholder="Поиск"
                                    aria-label="Поиск по истории диалогов"
                                />
                            </label>
                        )}
                    </div>

                    <div className="ai-sidebar-history custom-scroll">
                        {visibleSessions.length === 0 ? (
                            <div className="ai-history-empty">
                                <strong>{sessions.length === 0 ? 'Диалогов пока нет' : 'Ничего не найдено'}</strong>
                                <p>{sessions.length === 0 ? 'Задайте первый вопрос — чат появится здесь.' : 'Измените поисковый запрос.'}</p>
                            </div>
                        ) : (
                            visibleSessions.map((session: ChatSession) => (
                                <div key={session.id} className={`ai-session-item ${activeSessionId === session.id ? 'active' : ''}`}>
                                    <button
                                        type="button"
                                        className="ai-session-select"
                                        onClick={() => {
                                            setActiveSessionId(session.id);
                                            setIsMobileHistoryOpen(false);
                                            focusComposer();
                                        }}
                                    >
                                        <span className="ai-session-icon">
                                            <CustomIcon name={session.isPinned ? 'star' : 'chat'} size={16} color="currentColor" accent="none" />
                                        </span>
                                        <span className="ai-session-copy">
                                            <strong className="ai-session-title">{hideInternalIdentifiers(session.title)}</strong>
                                            <small>{formatMessageCount(session.messages.length)} · {new Date(session.updatedAt).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}</small>
                                        </span>
                                    </button>

                                    <div className={`ai-session-actions ${session.isPinned ? 'pinned' : ''}`}>
                                        <button
                                            type="button"
                                            className={`ai-pin-btn ${session.isPinned ? 'active' : ''}`}
                                            onClick={(event) => togglePin(session.id, event)}
                                            title={session.isPinned ? 'Открепить' : 'Закрепить'}
                                            aria-label={session.isPinned ? 'Открепить диалог' : 'Закрепить диалог'}
                                        >
                                            <CustomIcon name="star" size={15} color="currentColor" accent="none" />
                                        </button>
                                        <button
                                            type="button"
                                            className="ai-session-del-btn"
                                            onClick={(event) => deleteSession(session.id, event)}
                                            title="Удалить диалог"
                                            aria-label="Удалить диалог"
                                        >
                                            <CustomIcon name="close" size={15} color="currentColor" accent="none" />
                                        </button>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>

                    {sessions.length > 0 && (
                        <div className="ai-sidebar-clear">
                            <button
                                type="button"
                                className="hover-unified-app ai-clear-history-button"
                                onClick={() => setShowClearConfirm(true)}
                            >
                                Очистить историю
                            </button>
                        </div>
                    )}
                </aside>

                <div className="ai-chat-area">
                    <header className="ai-chat-toolbar">
                        <button
                            type="button"
                            className="ai-mobile-back-button"
                            onClick={() => router.push('/tasks?tab=welcome')}
                            aria-label="Вернуться к разделам"
                        >
                            <CustomIcon name="arrow-left" size={18} color="currentColor" accent="none" />
                        </button>

                        <button type="button" onClick={() => setIsMobileHistoryOpen(true)} className="ai-history-btn">
                            <CustomIcon name="chat" size={16} color="currentColor" accent="none" />
                            История
                        </button>

                        <div className="ai-active-dialog">
                            <strong>{activeSession?.title || 'Новый чат'}</strong>
                        </div>
                    </header>

                    <div className="ai-messages custom-scroll" ref={chatContainerRef}>
                        {activeSession && activeSession.messages.length > 0 && (
                            activeSession.messages.map((message: Message) => (
                                <div key={message.id} className={`ai-message-row ${message.role}`}>
                                    <div className={`ai-avatar ${message.role}`}>
                                        <CustomIcon
                                            name={message.role === 'user' ? 'user' : 'brain'}
                                            size={18}
                                            color="currentColor"
                                            accent="none"
                                        />
                                    </div>
                                    <div className="ai-message-body">
                                        <div className={`ai-bubble ${message.role} ${message.content.includes('СИСТЕМНАЯ ОШИБКА') || message.content.includes('СЫРОЙ ОТВЕТ') ? 'is-error' : ''}`}>
                                            {message.role === 'ai' ? renderAiMessage(message.content) : hideInternalIdentifiers(message.content)}
                                            {/* Два товара могут ссылаться на один файл. Ключ различает обе ссылки внутри сообщения. */}
                                            {message.sourceLinks?.map((link, linkIndex) => (
                                                <p key={`${link.url}:${link.title}:${linkIndex}`}>
                                                    <a href={normalizeAiHref(link.url) || '#'}>{link.title}</a>
                                                </p>
                                            ))}
                                        </div>
                                        <time>{message.timestamp}</time>
                                    </div>
                                </div>
                            ))
                        )}

                        {isTyping && (
                            <div className="ai-message-row ai">
                                <div className="ai-avatar ai">
                                    <CustomIcon name="brain" size={18} color="currentColor" accent="none" />
                                </div>
                                <div className="ai-message-body">
                                    <div className="ai-bubble ai ai-typing-bubble">
                                        <div className="typing-indicator"><span /><span /><span /></div>
                                    </div>
                                    <time>Формирует ответ</time>
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="ai-input-wrapper">
                        <div className="ai-input-box">
                            <textarea
                                ref={inputRef}
                                value={inputValue}
                                onChange={(event) => {
                                    setInputValue(event.target.value);
                                    event.currentTarget.style.height = 'auto';
                                    event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 144)}px`;
                                }}
                                onKeyDown={handleKeyDown}
                                placeholder="Напишите сообщение..."
                                aria-label="Вопрос для AI-ассистента"
                                className="custom-scroll ai-textarea"
                                rows={1}
                            />
                            <button
                                type="button"
                                onClick={() => handleSendMessage(inputValue)}
                                disabled={!inputValue.trim() || isTyping}
                                className="ai-send-btn"
                                aria-label="Отправить вопрос"
                            >
                                <CustomIcon name="send" size={19} color="currentColor" accent="none" />
                                <span>Отправить</span>
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            {showClearConfirm && (
                <div className="ai-confirm-overlay" role="presentation" onClick={() => setShowClearConfirm(false)}>
                    <section className="ai-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="ai-clear-history-title" onClick={(event) => event.stopPropagation()}>
                        <span className="ai-confirm-icon"><CustomIcon name="alert" size={30} color="currentColor" /></span>
                        <span className="vates-eyebrow">Необратимое действие</span>
                        <h2 id="ai-clear-history-title">Удалить всю историю?</h2>
                        <p>Все диалоги AI-ассистента будут удалены. Восстановить их после подтверждения не получится.</p>
                        <div className="ai-confirm-actions">
                            <button type="button" className="vates-button secondary" onClick={() => setShowClearConfirm(false)}>Отмена</button>
                            <button type="button" className="vates-button danger" onClick={clearHistory}>Удалить историю</button>
                        </div>
                    </section>
                </div>
            )}

            <style jsx global>{`
                .ai-monolithic-section { margin: -20px -60px -60px -60px; height: calc(100vh - 100px); display: flex; flex-direction: column; }
                .ai-monolithic-container { display: flex; flex: 1; background: transparent; height: 100%; }
                .ai-sidebar { width: 300px; border-right: 1px solid #1a1a1a; display: flex; flex-direction: column; background: transparent; transition: 0.3s ease; }
                .ai-session-item { position: relative; padding: 14px 15px; margin-bottom: 8px; cursor: pointer; border: 1px solid transparent; transition: all 0.2s ease; display: flex; justify-content: space-between; align-items: center; border-radius: 12px; }
                .ai-session-item.active { background: #1a1a1a; border-color: #333; }
                .ai-session-item:hover { border-color: #ff4d4d !important; box-shadow: 0 0 10px rgba(255, 77, 77, 0.15); background: rgba(255, 77, 77, 0.02); }
                .ai-session-title { color: #aaa; font-size: 14px; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; padding-right: 10px; }
                .ai-session-item.active .ai-session-title { color: #0abab5; }
                
                .ai-session-actions { display: flex; align-items: center; gap: 8px; opacity: 0; transition: opacity 0.2s ease; }
                .ai-session-item:hover .ai-session-actions { opacity: 1; }
                .ai-session-actions.pinned { opacity: 1; }

                .ai-pin-btn { background: transparent; border: none; cursor: pointer; color: #555; transition: all 0.2s ease; padding: 2px; display: flex; align-items: center; justify-content: center; }
                .ai-pin-btn:hover { color: #ffd700; transform: scale(1.15); }
                .ai-pin-btn.active { color: #ffd700; }

                .ai-session-del-btn { background: transparent; border: none; color: #ff4d4d; cursor: pointer; transition: transform 0.1s ease, color 0.2s ease; padding: 2px; display: flex; align-items: center; justify-content: center; }
                .ai-session-del-btn:hover { color: #ff1a1a; }
                .ai-session-del-btn:active { transform: scale(0.85); } 

                .ai-chat-area { flex: 1; display: flex; flex-direction: column; background: transparent; position: relative; }
                .ai-mobile-header { display: none; }
                .ai-messages { flex: 1; overflow-y: auto; padding: 40px; display: flex; flex-direction: column; }
                .ai-empty-state { margin: auto; text-align: center; max-width: 550px; }
                .quick-prompts-container { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; }
                .quick-prompt { background: #111; border: 1px solid #222; padding: 12px 18px; border-radius: 20px; color: #0abab5; font-size: 14px; font-weight: 800; cursor: pointer; transition: 0.2s; }
                .quick-prompt:hover { background: rgba(10,186,181,0.05); border-color: #0abab5; transform: translateY(-2px); }
                .ai-avatar { width: 40px; height: 40px; border-radius: 12px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; font-size: 20px; }
                .ai-avatar.user { background: #0abab5; color: #000; }
                .ai-avatar.ai { background: #161816; border: 1px solid #333; }
                 .ai-bubble { padding: 16px 22px; border-radius: 20px; font-size: 15px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
                 .ai-message-link { color: #45c7c2; font-weight: 800; text-decoration: underline; text-underline-offset: 3px; white-space: nowrap; }
                 .ai-message-link:hover { color: #8ce5e0; }
                 .ai-bubble.user { background: rgba(10,186,181,0.1); border: 1px solid rgba(10,186,181,0.3); color: #fff; border-bottom-right-radius: 6px; }
                .ai-bubble.ai { background: #111; border: 1px solid #222; color: #ddd; border-bottom-left-radius: 6px; }
                .ai-input-wrapper { padding: 20px 40px 30px 40px; background: transparent; }
                .ai-input-box { position: relative; display: flex; align-items: flex-end; background: #111; border: 1px solid #222; border-radius: 24px; padding: 6px; transition: 0.2s; }
                .ai-input-box:focus-within { border-color: #0abab5; box-shadow: 0 0 15px rgba(10,186,181,0.1); }
                .ai-textarea { flex: 1; background: transparent; border: none; color: #fff; padding: 16px 20px; font-size: 16px; outline: none; resize: none; max-height: 150px; min-height: 56px; font-family: inherit; }
                .ai-send-btn { margin: 8px; width: 44px; height: 44px; border-radius: 16px; border: none; display: flex; align-items: center; justify-content: center; transition: 0.2s; }
                .ai-send-btn:not(:disabled) { background: #0abab5; color: #000; cursor: pointer; }
                .ai-send-btn:not(:disabled):hover { transform: scale(1.05); }
                .ai-send-btn:disabled { background: #1a1a1a; color: #555; cursor: not-allowed; }
                .ai-footer-text { text-align: center; font-size: 12px; color: #555; margin-top: 12px; }
                .typing-indicator { display: flex; gap: 4px; padding: 2px 0; }
                .typing-indicator span { width: 8px; height: 8px; background: #0abab5; border-radius: 50%; animation: bounce 1.4s infinite ease-in-out both; }
                .typing-indicator span:nth-child(1) { animation-delay: -0.32s; }
                .typing-indicator span:nth-child(2) { animation-delay: -0.16s; }
                .ai-mobile-overlay { display: none; }
                @media (max-width: 768px) {
                    .ai-monolithic-section { margin: -10px -15px -50px -15px; height: calc(100vh - 70px); }
                    .ai-mobile-header { display: flex; justify-content: space-between; align-items: center; padding: 15px 20px; border-bottom: 1px solid #1a1a1a; background: #0a0a0a; }
                    .ai-history-btn { display: inline-flex; align-items: center; gap: 8px; background: rgba(10,186,181,0.1); color: #0abab5; border: 1px solid rgba(10,186,181,0.3); padding: 8px 16px; border-radius: 10px; font-weight: 800; font-size: 13px; transition: transform 0.16s ease, box-shadow 0.16s ease, background 0.16s ease, border-color 0.16s ease, color 0.16s ease; }
                    .ai-history-btn:hover { transform: translateY(1px) scale(0.985); box-shadow: inset 0 2px 6px rgba(0,0,0,0.18), 0 0 0 1px rgba(10, 186, 181, 0.24); background: rgba(10,186,181,0.14); border-color: rgba(10,186,181,0.45); color: #fff; }
                    .ai-history-btn:active { transform: translateY(2px) scale(0.97); box-shadow: inset 0 3px 8px rgba(0,0,0,0.24); }
                    .ai-sidebar { position: fixed; top: 0; left: -300px; width: 280px !important; height: 100vh; background: #000; z-index: 10006; box-shadow: 10px 0 30px rgba(0,0,0,0.8); }
                    .ai-sidebar.open { left: 0; }
                    .ai-mobile-overlay { display: block; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.7); z-index: 10005; backdrop-filter: blur(4px); }
                    .ai-messages { padding: 20px 15px; }
                    .ai-input-wrapper { padding: 15px; }
                    .quick-prompt { font-size: 13px; padding: 10px 14px; }
                    .ai-bubble { font-size: 14px; padding: 12px 16px; }
                }
            `}</style>
        </section>
    );
}

````
