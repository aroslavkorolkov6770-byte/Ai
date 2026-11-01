# Полные листинги этапа товарного поиска — 30.10.2026


## ai_core/site_search_catalog.py

SHA-256: c96873b30e1394c181b5105e90e2f8cf6aabcbc72ba11dc15e126512bbdaebec

````python
"""Поиск характеристик конкретного товара в доверенно размеченном каталоге.

Это извлечение опубликованных фактов, а не генерация новых характеристик.
Разметку назначает серверный импортёр; обычный текст не включает этот режим.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

FACETS = {'material', 'composition', 'dimensions', 'volume', 'mass', 'capacity', 'kit', 'price', 'stock'}
MATERIAL = r'металл\w*|стекл\w*|фарфор\w*|керами\w*|глин\w*|бамбук\w*|грушев\w*|дерев\w*|пластик\w*|силикон\w*|сталь\w*|сталь|стал[ьи]'
NUMBER = r'\d+(?:[.,]\d+)?'
DIMENSIONS = re.compile(rf'({NUMBER}(?:\s*[xх×]\s*{NUMBER}){{1,2}})\s*(мм|см|м)\b', re.I)
SKIP = ('из чего сделан сделана изготовлен изготовлена состоит состав материал дерево размер размеры '
        'габариты объем вместимость масса вес рассчитан рассчитана человек комплект комплектация входят '
        'входит свеча свечи свечка свечки продается продаются сколько стоит стоимость цена сейчас сегодня '
        'наличие магазин этот эта это он она оно него нее ней там тогда тот другой вариант товар продукция '
        'пожалуйста уточнить уточняю имеется вместе включен включены есть ли').split()


@dataclass
class Product:
    key: str
    heading: str
    source: dict
    passages: list
    words: frozenset


def validate_catalog(source):
    """Ограничиваем доверенную разметку: ключ и точный заголовок раздела."""
    items = source.get('catalog_items', [])
    if not isinstance(items, list) or len(items) > 500:
        raise ValueError('catalog_metadata_invalid')
    headings = set()
    for item in items:
        if (not isinstance(item, dict) or set(item) != {'key', 'heading'}
                or not isinstance(item['key'], str) or not 1 <= len(item['key']) <= 160
                or not isinstance(item['heading'], str) or not 1 <= len(item['heading']) <= 400
                or item['heading'] in headings):
            raise ValueError('catalog_item_invalid')
        headings.add(item['heading'])


def build_catalog(passages, terms):
    """Группируем все абзацы одной позиции один раз при публикации/рестарте."""
    grouped = {}
    metadata = {}
    for passage in passages:
        source = passage.source
        if source['id'] not in metadata:
            metadata[source['id']] = {item['heading']: item['key'] for item in source.get('catalog_items', [])}
        key = metadata[source['id']].get(passage.heading)
        if key:
            identity = (source['id'], key, passage.heading)
            if identity not in grouped:
                grouped[identity] = Product(key, passage.heading, source, [], frozenset(identity_terms(passage.heading, terms)))
            grouped[identity].passages.append(passage)
    return list(grouped.values())


def identity_terms(text, terms):
    # Название чайной доски не равно сокращению «чай в банке».
    # Термины оборудования нормализуются до склонения, до общего стеммера.
    text = re.sub(r'\bча\s+бан[ьи]\b', 'чабань', text, flags=re.I)
    text = re.sub(r'\bчах(?:ай|ая|аю|аем|ае|аи|аев)\b', 'чахай', text, flags=re.I)
    return terms(text)


def facet_of(question):
    text = question.casefold().replace('ё', 'е')
    if re.search(r'состав|состоит|ингредиент', text):
        return 'composition'
    if re.search(r'материал|из чего|из како\w* дерева', text):
        return 'material'
    if re.search(r'размер|габарит', text):
        return 'dimensions'
    if re.search(r'объем|вместимост', text):
        return 'volume'
    if re.search(r'сколько.*весит|\bвес\b|масса', text):
        return 'mass'
    if re.search(r'сколько.*человек|сколько.*персон', text):
        return 'capacity'
    if re.search(r'комплект|свеч', text):
        return 'kit'
    if re.search(r'цен[ауы]|стоимост|стоит|стоить', text):
        return 'price'
    if re.search(r'наличи|остат[ок]', text):
        return 'stock'
    return None


def dimensions(text):
    # Сравниваем значения одной характеристики в миллиметрах.
    # Порядок осей сохраняется: длину нельзя молча поменять местами с шириной.
    scales = {'мм': Decimal(1), 'см': Decimal(10), 'м': Decimal(1000)}
    return [tuple(Decimal(number.replace(',', '.')) * scales[match[1].lower()]
                  for number in re.findall(NUMBER, match[0])) for match in DIMENSIONS.findall(text)]


def extract(product, facet, sentences):
    """Возвращает короткие дословные фрагменты и обнаруженный конфликт."""
    heading = product.heading
    bodies = list(dict.fromkeys(p.text for p in product.passages))
    texts = [heading, *bodies]
    if facet == 'dimensions':
        values = [value for text in texts for value in dimensions(text)]
        if any(a[:min(len(a), len(b))] != b[:min(len(a), len(b))] for a in values for b in values):
            return [], True
        quotes = [(match.group(), text) for text in texts for match in DIMENSIONS.finditer(text)]
        if quotes:
            # Если размеры согласованы, достаточно самой полной записи.
            quotes = [max(quotes, key=lambda pair: len(re.findall(NUMBER, pair[0])))]
    elif facet in {'volume', 'mass'}:
        units = r'мл|л|литр\w*' if facet == 'volume' else r'кг|г|гр|грамм\w*'
        quotes = [(match.group(), text) for text in texts
                  for match in re.finditer(rf'\b{NUMBER}\s*(?:{units})\b', text, re.I)]
    elif facet == 'capacity':
        quotes = [(match.group(), text) for text in bodies
                  for match in re.finditer(r'на\s+\d+(?:\s*[-–]\s*\d+)?\s+(?:человек|персон\w*)', text, re.I)]
    elif facet == 'material':
        quotes = []
        for text in bodies or [heading]:
            for sentence in sentences(text):
                # Сохраняем целый смысловой оборот, а спорные размеры не добавляем
                # к независимому вопросу о дереве или металле.
                clause = sentence.split(',')[0]
                if re.search(MATERIAL, clause, re.I):
                    quotes.append((clause.rstrip('.'), sentence))
        if not quotes and re.search(MATERIAL, heading, re.I):
            quotes = [(match.group(), heading) for match in re.finditer(MATERIAL, heading, re.I)]
    elif facet == 'composition':
        # В каталоге состав бывает записан без метки «Состав:».
        quotes = [(sentence, sentence) for text in bodies for sentence in sentences(text)
                  if re.search(r'состав|ингредиент|м[её]д|чай|кофе|сахар|молок|орех|фрукт|ягод', sentence, re.I)
                  and 'отсутствует' not in sentence.casefold()]
    elif facet == 'kit':
        quotes = [(sentence, sentence) for text in bodies for sentence in sentences(text)
                  if re.search(r'комплект|свеч', sentence, re.I)]
    else:
        # Архивное описание не доказывает текущую цену и доступность товара.
        return [], False
    return list(dict.fromkeys(quotes)), False


def saved_context(context, products, identifiers):
    """Память содержит ключи доступных позиций, без копии ответа и переписки."""
    keys = context.get('catalog_keys')
    facet = context.get('catalog_facet')
    allowed = {product.key for product in products if product.source['id'] in identifiers}
    if (not isinstance(keys, list) or not 1 <= len(keys) <= 64 or facet not in FACETS
            or any(not isinstance(key, str) or key not in allowed for key in keys)):
        return {}
    return {'catalog_keys': list(dict.fromkeys(keys)), 'catalog_facet': facet}


def search_catalog(index, question, allowed, selected, day, session, sessions, scope):
    from .site_search import terms, affinity, NO_DATA
    from .site_search_structure import active, ISSUE_TEXT
    from .site_search_reasoning import authority

    products = [product for product in index.catalog_products if product.source['id'] in allowed
                and (not selected or product.source['id'] == selected)
                and all(active(p.structure or {}, day) and index._usable_passage(p, allowed, day) for p in product.passages)]
    if not products:
        return None
    facet = facet_of(question)
    previous = (session or {}).get('catalog_keys', [])
    words = set(identity_terms(question, terms)) - set(terms(' '.join(SKIP)))
    context_used = False
    candidates = [product for product in products if words and
                  all(any(affinity(word, token) for token in product.words) for word in words)]
    if previous and not words and facet:
        candidates = [product for product in products if product.key in previous]
        context_used = True
    elif previous and candidates and facet is None:
        # Короткий ответ на уточнение: «с миндалём». Другой явно названный
        # товар ищется заново и не наследует незаданный вопрос о составе.
        narrowed = [product for product in candidates if product.key in previous]
        if narrowed:
            candidates = narrowed
            facet = session.get('catalog_facet')
            context_used = True
        elif re.match(r'^\s*а\s+', question, re.I):
            facet = session.get('catalog_facet')
            context_used = True
    if not facet or not candidates:
        return None
    keys = list(dict.fromkeys(product.key for product in candidates))
    # Размеченный каталог не отменяет более авторитетный документ о том же
    # товаре. Сравниваем опубликованные характеристики, не выбираем удобную
    # версию. Обычные правила/уроки без совпадения предмета сюда не попадают.
    if len(keys) == 1:
        base = candidates[0]
        identity = words if len(words) >= 2 else set(base.words)
        identity -= set(terms(' '.join(SKIP) + ' г гр кг мл л мм см'))
        identity = {word for word in identity if not re.fullmatch(r'[\d.,]+', word)}
        external = {}
        if identity:
            for passage in index.passages:
                source = passage.source
                if (source['id'] not in allowed or source.get('catalog_items')
                        or (selected and source['id'] != selected)
                        or authority(source) <= max(authority(product.source) for product in candidates)
                        or not active(passage.structure or {}, day) or not index._usable_passage(passage, allowed, day)):
                    continue
                vocabulary = set(passage.words) | set(passage.title_words)
                if all(any(affinity(word, token) for token in vocabulary) for word in identity):
                    pair = (source['id'], passage.heading)
                    external.setdefault(pair, Product(base.key, passage.heading, source, [], frozenset())).passages.append(passage)
        candidates.extend(external.values())
    identifiers = list(dict.fromkeys(product.source['id'] for product in candidates))

    def result(status, text, evidence=None):
        return {'status': status, 'text': text, 'evidence': evidence or [], 'links': [], 'context_used': context_used}

    def remember():
        index._remember(sessions, scope, identifiers, words or (session or {}).get('terms', []))
        if len(keys) <= 64:
            sessions[scope].update(catalog_keys=keys, catalog_facet=facet)

    if len(keys) > 1:
        remember()
        names = list(dict.fromkeys(product.heading for product in candidates))
        text = 'Уточните товар: ' + '; '.join('«' + name + '»' for name in names[:4])
        text += '? ' if len(names) <= 4 else '. Есть и другие варианты — напишите полное название.'
        return result('clarify', text.strip())
    if any(not product.source.get('complete', True) or any((p.structure or {}).get('issue') for p in product.passages)
           for product in candidates):
        return result('unavailable', ISSUE_TEXT)
    extracted = [(product, *extract(product, facet, index._sentences)) for product in candidates]
    # Повтор одной строки не создаёт неоднозначности. Различающиеся факты
    # одного ключа не выбираем голосованием или по порядку файлов.
    facts = {tuple(sorted(text.casefold().rstrip('. ') for text, quote in quotes))
             for product, quotes, conflict in extracted if quotes}
    if facet == 'dimensions':
        values = [value for product, quotes, conflict in extracted for text, quote in quotes for value in dimensions(text)]
        different = any(a[:min(len(a), len(b))] != b[:min(len(a), len(b))] for a in values for b in values)
    else:
        different = len(facts) > 1
    if any(conflict for product, quotes, conflict in extracted) or different:
        return result('conflict', 'В доступных материалах есть противоречие в характеристиках товара. Уточните данные у администратора.')
    usable = [(product, quotes) for product, quotes, conflict in extracted if quotes]
    if not usable:
        return result('not_found', NO_DATA)
    product, quotes = max(usable, key=lambda pair: authority(pair[0].source))
    text = '; '.join(dict.fromkeys(text.rstrip('. ') for text, quote in quotes)) + '.'
    if len(text) > 900:
        return result('clarify', 'Уточните, какая именно характеристика товара нужна.')
    remember()
    evidence = [{'id': product.source['id'], 'revision': product.source['revision'], 'heading': product.heading, 'quote': quote}
                for quote in dict.fromkeys(quote for text, quote in quotes)]
    return result('answer', text, evidence)

````

## ai_core/site_search.py

SHA-256: 3553072f8f4857ebc9b6f84b11a9b6767c58f733e0f8ed9e8ac72d28ff70014b

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

    def rebuild_lookup(self):
        """Обратный указатель: слово → номера фрагментов; без изменения score."""
        self.postings = {}
        self.passage_positions = {}
        self.condition_positions = set()
        self.passage_operations = {}
        self.token_matches = {}
        self.catalog_products = build_catalog(self.passages, terms)
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

## ai_core/site_search_runtime.py

SHA-256: 8fed036fefa97d749a800368c60cab29e5720403193f9d072d8b7d3c9f139e27

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

CONTEXT_TTL = 30 * 24 * 60 * 60
LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def engine_version() -> str:
    digest = hashlib.sha256()
    for name in ("site_search.py", "site_search_language.py", "site_search_reasoning.py", "site_search_conditions.py", "site_search_structure.py", "site_search_links.py", "site_search_catalog.py"):
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

    def status(self):
        with self.lock:
            return {"state": self.index.state, "sources": len(self.index.sources), "passages": len(self.index.passages),
                    "snapshot": self.index.snapshot, "restored_from_disk": self.restored,
                    "restore_ms": self.restore_ms, "rebuilt_from_text": self.rebuilt, "context_count": len(self.index.sessions)}

````

## ai_core/site_search_store.py

SHA-256: 0a4aef0d594caacdb3903f0dde753c00b5d3bcfeaeaddcb4c52cacb671157de4

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
                 "parents", "status", "expires_at", "valid_from", "authority", "complete", "keywords", "file_name", "structure_unavailable", "catalog_items"}


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

## tests/test_site_search_catalog.py

SHA-256: 6c73544e72062c830ce0ad37b7096087df1d1fdd22b5ba6f5f02c3e9e56ea14c

````python
"""Товарная идентичность, неоднозначность, происхождение и сохранение контекста."""
import copy
import tempfile
import unittest
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch

ORIGIN = 'http://127.0.0.1:3040'
ACTOR = Principal('buyer', 'staff')


def catalog(identifier='catalog', revision='v1', rows=None, access=None):
    rows = rows or [
        ('stand-a', 'Подставка Север', 'Металлическая подставка. Свечки в комплект не входят.'),
        ('stand-b', 'Подставка Юг', 'Стеклянная подставка. Свечки входят в комплект.'),
    ]
    return {'id': identifier, 'revision': revision, 'title': 'Архив товаров', 'kind': 'document',
            'url': ORIGIN + '/materials/' + identifier, 'authority': 'reference',
            'access': access or {'scope': 'workspace'},
            'catalog_items': [{'key': key, 'heading': title} for key, title, text in rows],
            'sections': [{'heading': title, 'text': text} for key, title, text in rows]}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.index = ClosedSiteIndex([ORIGIN])
        self.publish([catalog()])

    def publish(self, sources):
        self.index.publish(sources, self.index.begin('snapshot'))

    def ask(self, question, chat='chat', actor=ACTOR, **extra):
        return self.index.search(question, actor, chat, **extra)

    def test_same_file_ambiguity_then_selection_keeps_requested_property(self):
        question = self.ask('Из чего сделана подставка?')
        self.assertEqual(question['status'], 'clarify')
        answer = self.ask('Север')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('Металлическая', answer['text'])
        self.assertNotIn('Свечки', answer['text'])
        self.assertEqual(answer['links'][0]['title'], 'Подставка Север')

    def test_same_file_followup_is_not_neighbor_product(self):
        self.ask('Из чего сделана подставка Север?')
        answer = self.ask('А свечки в комплекте?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('не входят', answer['text'])
        self.assertEqual(answer['evidence'][0]['heading'], 'Подставка Север')

    def test_short_explicit_product_change_reuses_property_not_old_product(self):
        self.ask('Из чего сделана подставка Север?')
        answer = self.ask('А Юг?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('Стеклянная', answer['text'])
        self.assertNotIn('Металлическая', answer['text'])

    def test_duplicates_do_not_vote_or_create_two_options(self):
        rows = [('stand-a', 'Подставка Север', 'Металлическая подставка.')]
        self.publish([catalog(rows=rows), catalog(identifier='copy', rows=rows)])
        answer = self.ask('Из чего подставка Север?')
        self.assertEqual(answer['status'], 'answer')
        self.assertEqual(len(answer['links']), 1)
        self.assertEqual(len(answer['evidence']), 1)

    def test_conflicting_duplicate_materials_do_not_choose_majority(self):
        first = catalog(rows=[('a', 'Подставка Север', 'Металлическая подставка.')])
        second = catalog(identifier='other', rows=[('a', 'Подставка Север', 'Стеклянная подставка.')])
        third = copy.deepcopy(first)
        third['id'] = 'third'
        self.publish([first, second, third])
        self.assertEqual(self.ask('Из чего подставка Север?')['status'], 'conflict')

    def test_official_document_conflict_is_not_hidden_by_catalog(self):
        official = catalog(identifier='spec', rows=[('a', 'Подставка Север', 'Керамическая подставка.')])
        del official['catalog_items']
        official['authority'] = 'official'
        self.publish([catalog(), official])
        self.assertEqual(self.ask('Из чего подставка Север?')['status'], 'conflict')

    def test_matching_official_document_is_preferred_as_evidence(self):
        official = catalog(identifier='spec', rows=[('a', 'Подставка Север', 'Металлическая подставка.')])
        del official['catalog_items']
        official['authority'] = 'official'
        self.publish([catalog(), official])
        answer = self.ask('Из чего подставка Север?')
        self.assertEqual(answer['status'], 'answer')
        self.assertEqual(answer['evidence'][0]['id'], 'spec')

    def test_dimension_units_conflict_within_one_product(self):
        self.publish([catalog(rows=[('a', 'Доска Север 540х340 мм', 'Доска из дерева размером 540х340х5 см.')])])
        answer = self.ask('Какие размеры у доски Север?')
        self.assertEqual(answer['status'], 'conflict')
        self.assertEqual(answer['links'], [])

    def test_equivalent_dimensions_are_not_conflict(self):
        self.publish([catalog(rows=[('a', 'Доска Север 540х340 мм', 'Доска размером 54х34х0,5 см.')])])
        self.assertEqual(self.ask('Какие размеры у доски Север?')['status'], 'answer')

    def test_missing_material_does_not_borrow_neighbor(self):
        self.publish([catalog(rows=[('a', 'Подставка Север', 'Описание товара в выгрузке отсутствует.'),
                                    ('b', 'Подставка Юг', 'Стеклянная подставка.')])])
        self.assertEqual(self.ask('Из чего подставка Север?')['status'], 'not_found')

    def test_unimplemented_property_does_not_borrow_neighbor_on_followup(self):
        self.publish([catalog(rows=[('a', 'Подставка Север', 'Металлическая подставка.'),
                                    ('b', 'Подставка Юг', 'Срок гарантии на подставку Юг — 5 лет.')])])
        self.ask('Из чего подставка Север?')
        answer = self.ask('Какой у нее срок гарантии?')
        self.assertNotEqual(answer['status'], 'answer')
        self.assertNotIn('5 лет', answer['text'])

    def test_equivalent_dimensions_across_copies(self):
        self.publish([catalog(rows=[('a', 'Доска Север', 'Размер 540х340 мм.')]),
                      catalog(identifier='copy', rows=[('a', 'Доска Север', 'Размер 54х34 см.')])])
        self.assertEqual(self.ask('Какие размеры у доски Север?')['status'], 'answer')

    def test_unknown_price_does_not_use_dimensions_as_price(self):
        self.publish([catalog(rows=[('a', 'Доска Север 540х340 мм', 'Деревянная доска.')])])
        self.assertEqual(self.ask('Сколько стоит доска Север?')['status'], 'not_found')

    def test_hidden_product_does_not_appear_in_clarification(self):
        public = catalog(rows=[('a', 'Подставка Север', 'Металлическая подставка.')])
        private = catalog(identifier='hidden', rows=[('b', 'Подставка Секрет', 'Стеклянная подставка.')],
                          access={'scope': 'admin'})
        self.publish([public, private])
        answer = self.ask('Из чего сделана подставка?')
        self.assertEqual(answer['status'], 'answer')
        self.assertNotIn('Секрет', str(answer))

    def test_hidden_conflict_does_not_influence_public_answer(self):
        public = catalog(rows=[('a', 'Подставка Север', 'Металлическая подставка.')])
        private = catalog(identifier='hidden', rows=[('a', 'Подставка Север', 'Стеклянная подставка.')],
                          access={'scope': 'admin'})
        self.publish([public, private])
        self.assertEqual(self.ask('Из чего подставка Север?')['status'], 'answer')

    def test_other_user_and_chat_do_not_inherit_product(self):
        self.ask('Из чего подставка Север?')
        for actor, chat in [(Principal('other', 'staff'), 'chat'), (ACTOR, 'other')]:
            answer = self.ask('А свечки в комплекте?', actor=actor, chat=chat)
            self.assertEqual(answer['status'], 'clarify')

    def test_current_manifest_revokes_product_focus(self):
        self.ask('Из чего подставка Север?')
        answer = self.ask('А свечки в комплекте?', current_revisions={})
        self.assertNotEqual(answer['status'], 'answer')
        self.assertEqual(answer['evidence'], [])

    def test_new_revision_does_not_return_old_material(self):
        self.ask('Из чего подставка Север?')
        answer = self.ask('А свечки в комплекте?', current_revisions={'catalog': 'v2'})
        self.assertEqual(answer['status'], 'processing')
        self.publish([catalog(revision='v2', rows=[('a', 'Подставка Север', 'Керамическая подставка.')])])
        self.assertIn('Керамическая', self.ask('Из чего подставка Север?')['text'])

    def test_malformed_catalog_metadata_rejected(self):
        source = catalog()
        source['catalog_items'][0]['key'] = []
        with self.assertRaises(ValueError):
            self.publish([source])

    def test_plain_document_does_not_enable_product_mode(self):
        source = catalog()
        del source['catalog_items']
        self.publish([source])
        self.assertEqual(self.index.catalog_products, [])

    def test_persisted_product_focus_and_pending_choice(self):
        with tempfile.TemporaryDirectory(prefix='vates-catalog-') as directory:
            path = Path(directory) / 'search.sqlite'
            runtime = DurableSearch([ORIGIN], path)
            runtime.publish([catalog()], runtime.begin('initial'))
            ask = lambda text, chat='chat': runtime.query(text, ACTOR, chat, None, {'catalog': 'v1'})
            self.assertEqual(ask('Из чего подставка Север?')['status'], 'answer')
            self.assertEqual(ask('Из чего подставка?', 'pending')['status'], 'clarify')
            runtime = DurableSearch([ORIGIN], path)
            self.assertTrue(runtime.restored)
            self.assertIn('не входят', ask('А свечки в комплекте?')['text'])
            self.assertIn('Стеклянная', ask('Юг', 'pending')['text'])
            other = runtime.query('А свечки в комплекте?', Principal('other', 'staff'), 'chat', None, {'catalog': 'v1'})
            self.assertEqual(other['status'], 'clarify')


if __name__ == '__main__':
    unittest.main()

````

## evaluation/closed_search/catalog_cases_20261030.json

SHA-256: 8cd86fdbd1335ab4c7ff39be1452e0dfd4d1ce0c7634a6d0f2d412432329118f

````json
{
  "note": "Зафиксировано до изменений поиска по оригинальным строкам архивных выгрузок 18.06.2026. Порядок внутри одного chat сохраняется. Это проверка ограниченного набора, не всех товаров.",
  "cases": [
    {"id":"catalog-01","chat":"mousse","q":"Какой состав у медового мусса Dolche Vita?","status":"clarify","has":["Уточните"],"absent":["Код"]},
    {"id":"catalog-02","chat":"mousse","q":"С миндалем","status":"answer","has":["Мед натуральный","жареного миндаля"],"absent":["грецкого ореха"]},
    {"id":"catalog-03","chat":"stand","q":"Из чего сделана подставка Хайвэй?","status":"answer","has":["Металлическая"],"absent":["Стеклянная"]},
    {"id":"catalog-04","chat":"stand","q":"А свечки в комплекте?","status":"answer","has":["не входят"],"absent":["Стеклянная"]},
    {"id":"catalog-05","chat":"stand","q":"Из какого материала подставка Звездочка?","status":"answer","has":["Стеклянная"],"absent":["Металлическая"]},
    {"id":"catalog-06","chat":"stand","q":"А сколько она стоит сейчас?","status":"not_found"},
    {"id":"catalog-07","chat":"size","q":"Какие размеры у Ча Бань Груша?","status":"conflict","has":["противореч"],"absent":["540"]},
    {"id":"catalog-08","chat":"wood","q":"Из какого дерева Ча Бань Груша?","status":"answer","has":["грушевого дерева"],"absent":["540","340"]},
    {"id":"catalog-09","chat":"capacity","q":"На сколько человек рассчитана Ча Бань Груша?","status":"answer","has":["6-8"],"absent":["540","340"]},
    {"id":"catalog-10","chat":"duplicate","q":"Какой состав у медового мусса Dolche Vita с клюквой?","status":"answer","has":["клюква"],"absent":["миндаля"]},
    {"id":"catalog-11","chat":"title","q":"Какой объем у чахая Дао?","status":"answer","has":["190 мл"]},
    {"id":"catalog-12","chat":"unknown","q":"Из какого материала подставка Неизвестная?","status":"not_found"},
    {"id":"catalog-13","chat":"fresh","q":"А свечки в комплекте?","status":"clarify"},
    {"id":"catalog-14","chat":"mousse","q":"Какой состав у медового мусса Dolche Vita с бананом?","status":"answer","has":["сока","банана"],"absent":["миндаля"]}
  ]
}

````

## evaluation/closed_search/catalog_probe.py

SHA-256: b8a84073c1030490bd061f24fab3249c84c845f5a60f36aa3fe96503869fc984

````python
"""Проверяет товарные диалоги на копиях пользовательских выгрузок."""
import argparse
import hashlib
import json
import time
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal
from evaluation.closed_search.real_materials_probe import records, LAB, ORIGIN

SPEC = Path(__file__).with_name('catalog_cases_20261030.json')


def catalog_sources():
    sources, manifest = records()
    documents = {doc['id']: doc for doc in manifest['documents']}
    for source in sources:
        doc = documents[source['id'].removeprefix('document:')]
        if doc['kind'] == 'catalog-copy':
            # Только проверенная подготовка задаёт тип содержимого и ключ товара.
            # Текст файла не может назначить себе права или официальность.
            source['catalog_items'] = [{'key': 'tea-export:' + row['code'], 'heading': row['title']}
                                       for row in doc['rows']]
    return sources


def check(case, result):
    errors = []
    if result['status'] != case['status']:
        errors.append('status:' + result['status'])
    for phrase in case.get('has', []):
        if phrase.casefold() not in result['text'].casefold():
            errors.append('missing:' + phrase)
    for phrase in case.get('absent', []):
        if phrase.casefold() in result['text'].casefold():
            errors.append('unexpected:' + phrase)
    if result['status'] == 'answer' and (not result['links'] or not result['evidence']):
        errors.append('missing-evidence')
    if result['status'] != 'answer' and (result['links'] or result['evidence']):
        errors.append('unexpected-evidence')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='catalog')
    args = parser.parse_args()
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    index = ClosedSiteIndex([ORIGIN])
    index.publish(catalog_sources(), index.begin('catalog'))
    rows = []
    for case in spec['cases']:
        started = time.perf_counter()
        result = index.search(case['q'], Principal('closed_lab_b', 'staff'), case['chat'])
        errors = check(case, result)
        rows.append({**case, 'result': result, 'errors': errors, 'pass': not errors,
                     'ms': round((time.perf_counter() - started) * 1000, 2)})
    report = {'spec_sha256': hashlib.sha256(SPEC.read_bytes()).hexdigest(),
              'total': len(rows), 'passed': sum(row['pass'] for row in rows), 'cases': rows}
    output = LAB / (args.label + '-' + str(time.time_ns()) + '.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(output), 'total': len(rows), 'passed': report['passed'],
                      'failures': [row for row in rows if not row['pass']]}, ensure_ascii=False))
    return 0 if report['total'] == report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        raise SystemExit('Catalog probe failed: ' + type(error).__name__) from error

````

## evaluation/closed_search/catalog_http.cjs

SHA-256: e938272ff84b75025ad397186e520724416337b95ebbc0aaa82ead1b78e3c9d9

````javascript
/* Товарные диалоги на отдельном стенде. Рабочую LMS и оригиналы не изменяет. */
const { fs, path, lab, login, storage, call } = require('./lab_client.cjs');
const { waitSnapshot } = require('./scan_http.cjs');
const { createHash } = require('node:crypto');
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

async function prepare(cookie) {
    const directory = path.join(lab, 'real-materials');
    const manifest = JSON.parse(await fs.readFile(path.join(directory, 'manifest.json'), 'utf8'));
    const documents = await storage('tea_hub_urgent_files_v1', cookie);
    let changed = 0;
    for (const doc of manifest.documents.filter(item => item.kind === 'catalog-copy')) {
        const record = documents.find(item => item.id === doc.id);
        if (!record || record.name !== doc.name) throw new Error('Catalog copy missing');
        const bytes = await fs.readFile(path.join(directory, doc.name));
        const data = await storage('file_data_' + doc.id, cookie);
        if (digest(bytes) !== doc.sha256 || data !== 'data:application/octet-stream;base64,' + bytes.toString('base64')) {
            throw new Error('Catalog copy checksum differs');
        }
        const metadata = doc.rows.map(row => ({ key: 'tea-export:' + row.code, heading: row.title }));
        if (record.searchCatalog !== undefined && JSON.stringify(record.searchCatalog) !== JSON.stringify(metadata)) {
            throw new Error('Existing catalog metadata differs');
        }
        if (!record.searchCatalog) {
            record.searchCatalog = metadata;
            changed += 1;
        }
    }
    if (changed) await storage('tea_hub_urgent_files_v1', cookie, documents);
    return changed;
}

async function main() {
    const cookies = { admin: await login('closed-admin'), a: await login('closed-a'), b: await login('closed-b') };
    const changed = await prepare(cookies.admin);
    const ready = await waitSnapshot();
    const specBytes = await fs.readFile(path.join(__dirname, 'catalog_cases_20261030.json'));
    const spec = JSON.parse(specBytes);
    const prefix = 'catalog-http-' + Date.now();
    const rows = [];
    const waits = [];
    async function ask(item, actor = 'b') {
        const body = { chatId: prefix + '-' + item.chat, messages: [{ role: 'user', content: item.q }] };
        let started = performance.now();
        let result = await call('/api/ai-chat', body, cookies[actor]);
        if (result.response.status === 429) {
            const seconds = Number(result.response.headers.get('retry-after')) || 300;
            if (seconds < 0 || seconds > 360) throw new Error('Unexpected rate limit interval');
            waits.push({ id: item.id, actor, seconds });
            console.log(JSON.stringify({ event: 'respecting-rate-limit', seconds }));
            for (let remaining = seconds * 1000 + 100; remaining > 0; remaining -= Math.min(remaining, 30000)) {
                await new Promise(resolve => setTimeout(resolve, Math.min(remaining, 30000)));
            }
            started = performance.now();
            result = await call('/api/ai-chat', body, cookies[actor]);
        }
        const { response, value } = result;
        const answer = value.output_text || '';
        const links = value.sourceLinks || [];
        const errors = [];
        if (response.status !== 200) errors.push('http:' + response.status);
        if (value.search_status !== item.status) errors.push('status:' + value.search_status);
        for (const phrase of item.has || []) if (!answer.toLowerCase().includes(phrase.toLowerCase())) errors.push('missing:' + phrase);
        for (const phrase of item.absent || []) if (answer.toLowerCase().includes(phrase.toLowerCase())) errors.push('unexpected:' + phrase);
        if (item.status === 'answer' && (!links.length || links.some(link => link.title.startsWith('Товары_')))) errors.push('readable-link');
        if (item.status !== 'answer' && links.length) errors.push('unexpected-links');
        rows.push({ ...item, actor, answer, links, status: value.search_status, errors, pass: !errors.length,
                    ms: Math.round(performance.now() - started) });
    }
    for (const item of spec.cases) await ask(item);
    await ask({ id: 'acl-product', chat: 'denied', q: 'Из чего сделана подставка Хайвэй?', status: 'not_found' }, 'a');
    await ask({ id: 'acl-context', chat: 'stand', q: 'А свечки в комплекте?', status: 'clarify' }, 'a');
    await ask({ id: 'other-chat', chat: 'other', q: 'А свечки в комплекте?', status: 'clarify' });
    const report = { ready, changed, waits, chatPrefix: prefix, specSHA256: digest(specBytes), total: rows.length,
                     passed: rows.filter(row => row.pass).length, cases: rows };
    const output = path.join(lab, 'catalog-http-' + Date.now() + '.json');
    await fs.writeFile(output, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ file: output, total: report.total, passed: report.passed, failures: rows.filter(row => !row.pass) }));
    if (report.passed !== report.total) process.exitCode = 1;
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## evaluation/closed_search/export_site.cjs

SHA-256: 08cf5d6ae4e9eda2863aaa291ace5258aa22d32481450d9dbea999f17bab8aad

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

## evaluation/closed_search/real_materials_http.cjs

SHA-256: 4b69f83c948a0e323cb7ff8045e4473068177a4a08bda9f2a4cab3106fcd99ef

````javascript
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
                accessPolicy: { version: 1, visibility: 'restricted', userIds: ['closed_lab_b'] },
                ...(doc.kind === 'catalog-copy' ? { searchCatalog: doc.rows.map(row => ({ key: 'tea-export:' + row.code, heading: row.title })) } : {}) };
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

````

## evaluation/closed_search/soak_http.cjs

SHA-256: a2f5b44c8ad40fd581857ef18ccb11d38ffd015e381ad409ab54e3b577b4a979

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
                    ...(doc.kind === 'catalog-copy' ? { catalog_items: doc.rows.map(row => ({ key: 'tea-export:' + row.code, heading: row.title })) } : {}),
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
            const catalogBytes = await fs.readFile(path.join(__dirname, 'catalog_cases_20261030.json'));
            report.catalogSpecSHA256 = createHash('sha256').update(catalogBytes).digest('hex');
            additionalCases.push(...JSON.parse(catalogBytes).cases.map(item => ({ ...item, has: item.has || [],
                chat: 'catalog-' + item.chat, principalId: 'closed_lab_b' })));
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
        for (const file of ['site_search.py','site_search_language.py','site_search_conditions.py','site_search_structure.py','site_search_reasoning.py','site_search_links.py','site_search_catalog.py','site_search_runtime.py','site_search_store.py','site_documents.py','site_scan_tables.py','site_scan_layout.py','site_scan_quality.py','site_search_api.py']) {
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

## evaluation/closed_search/wait_site_ready.cjs

SHA-256: f7f18e0ef830cafedb64259a0882e01bba0ff94cbbca2c29adab7008fac1d12d

````javascript
/* Готовность означает работу маршрутов, а не только открытый TCP-порт. */
const origin = 'http://127.0.0.1:3040';
const checks = [
    ['/api/auth/session', undefined],
    ['/api/storage?key=tea_hub_urgent_files_v1', undefined],
    ['/api/ai-chat', { messages: [{ role: 'user', content: 'Проверка готовности' }] }],
];

async function main() {
    const deadline = Date.now() + 90000;
    let last = 'not_started';
    while (Date.now() < deadline) {
        try {
            let ready = true;
            for (const [route, body] of checks) {
                const response = await fetch(origin + route, {
                    method: body ? 'POST' : 'GET', headers: { Origin: origin, 'Content-Type': 'application/json' },
                    ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(15000),
                });
                last = route + ':' + response.status;
                if (response.status !== 401 || !response.headers.get('content-type')?.includes('application/json')) {
                    ready = false;
                    break;
                }
                const payload = await response.json();
                if (!payload || typeof payload !== 'object') {
                    ready = false;
                    break;
                }
            }
            if (ready) {
                console.log('Маршруты входа, материалов и чата готовы; запросы без сессии отклоняются.');
                return;
            }
        } catch (error) {
            last = error.name;
        }
        await new Promise(resolve => setTimeout(resolve, 2000));
    }
    throw new Error('Сайт не готов: ' + last + '. Проверьте site.stderr.log тестового стенда.');
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## evaluation/closed_search/verify_lab.cjs

SHA-256: 642a83a88d3b7bb9018effbd85450d784ef49ddb330546915af888d7506fbd2c

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
    const expectedDocuments = 9 + reasoningFixtures.documents.length + conditionFixtures.documents.length + structureFixtures.documents.length + structureAssets.length + scanFixtures.documents.length + layoutFixtures.documents.length + qualityFixtures.documents.length + multipageFixtures.documents.length + crossFixtures.documents.length + linkFixtures.documents.length + realManifest.documents.length;
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

## scripts/start_closed_search_lab.ps1

SHA-256: f5e2cfc6506c10c2dad44fee2b864b826e937c7de4379585a94226d08a24df51

````powershell
param([switch]$RestartSearch, [switch]$RestartExporter, [switch]$SkipExporter)
$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$labRoot = Join-Path $taskRoot '.runtime\closed-search-lab-20261028'
$siteRoot = Join-Path $labRoot 'tea-app-main'
$pythonPath = Join-Path $taskRoot '.venv\Scripts\python.exe'
$nodePath = (Get-Command node -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath (Join-Path $siteRoot '.env.local'))) { throw 'Сначала требуется настройка изолированного стенда.' }

# Перезапускается только наш процесс закрытого поиска. Рабочая LMS не затрагивается.
$searchListener = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($searchListener) {
    $searchWorker = Get-CimInstance Win32_Process -Filter "ProcessId = $($searchListener.OwningProcess)"
    $searchParent = Get-CimInstance Win32_Process -Filter "ProcessId = $($searchWorker.ParentProcessId)"
    if ($searchWorker.CommandLine -notlike '*ai_core.site_search_api:app*' -or $searchParent.CommandLine -notlike "*$taskRoot\.venv\Scripts\python.exe*ai_core.site_search_api:app*") { throw 'Порт 8001 занят другим процессом.' }
    if ($RestartSearch) {
        Stop-Process -Id $searchWorker.ProcessId,$searchParent.ProcessId -Force
        $searchListener = $null
    }
}
if (-not $searchListener) {
    $searchProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','ai_core.site_search_api:app','--host','127.0.0.1','--port','8001') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $labRoot 'search.stdout.log') -RedirectStandardError (Join-Path $labRoot 'search.stderr.log') -PassThru
    Write-Output "Поиск запущен: $($searchProcess.Id)"
}
$siteListener = Get-NetTCPConnection -LocalPort 3040 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($siteListener) {
    $siteProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($siteListener.OwningProcess)"
    if ($siteProcess.CommandLine -notlike "*$siteRoot*next*") { throw 'Порт 3040 занят другим процессом.' }
} else {
    & $nodePath (Join-Path $siteRoot 'scripts\prepare-pdf-viewer.mjs')
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось подготовить локальный просмотр PDF.' }
    $siteProcess = Start-Process -FilePath $nodePath -ArgumentList @((Join-Path $siteRoot 'node_modules\next\dist\bin\next'),'dev','--hostname','127.0.0.1','--port','3040') -WorkingDirectory $siteRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $labRoot 'site.stdout.log') -RedirectStandardError (Join-Path $labRoot 'site.stderr.log') -PassThru
    Write-Output "Тестовый сайт запущен: $($siteProcess.Id)"
}
$exportScript = Join-Path $taskRoot 'evaluation\closed_search\export_site.cjs'
$exporters = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like "*$exportScript*" })
if ($RestartExporter -and $exporters.Count -gt 0) {
    # Найден именно обработчик этого стенда по полному пути, не чужой Node-процесс.
    $exporters | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    $exporters = @()
}
if (-not $exporters -and -not $SkipExporter) {
    $exportProcess = Start-Process -FilePath $nodePath -ArgumentList @($exportScript) -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $labRoot 'export.stdout.log') -RedirectStandardError (Join-Path $labRoot 'export.stderr.log') -PassThru
    Write-Output "Фоновый обработчик запущен: $($exportProcess.Id)"
}
& $nodePath (Join-Path $taskRoot 'evaluation\closed_search\wait_ready.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Поиск запущен, но ещё не готов к вопросам.' }
& $nodePath (Join-Path $taskRoot 'evaluation\closed_search\wait_site_ready.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Поиск готов, но маршруты тестового сайта ещё не работают.' }
Write-Output 'Адрес стенда: http://127.0.0.1:3040/tasks?tab=standards'

````

## evaluation/closed_search/finalize_catalog.cjs

SHA-256: 77f614719495c23a08efb514f9f79159efbaea2294f787607face55ee84491fa

````javascript
/* Сохраняет проверяемый отчёт и полные листинги этого локального этапа. */
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const site = path.join(lab, 'tea-app-main');
const reportName = 'CLOSED_SEARCH_CATALOG_REPORT_2026_10_30.md';
const snapshotName = 'CLOSED_SEARCH_CATALOG_CODE_SNAPSHOT_2026_10_30.md';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const read = file => fs.readFile(file, 'utf8').then(JSON.parse);
const percentile = (rows, fraction) => [...rows].sort((a, b) => a - b)[Math.ceil(rows.length * fraction) - 1];

async function prepend(file, entry) {
    const text = await fs.readFile(file, 'utf8');
    if (text.includes(entry.split('\n')[0])) throw new Error('Stage already recorded: ' + path.basename(file));
    const firstLine = text.indexOf('\n');
    await fs.writeFile(file, text.slice(0, firstLine + 1) + '\n' + entry + '\n\n' + text.slice(firstLine + 1), 'utf8');
}

async function main() {
    const baseline = await read(path.join(lab, 'catalog-baseline-1793337866785251200.json'));
    const local = await read(path.join(lab, 'catalog-final-1793339063419103600.json'));
    const http = await read(path.join(lab, 'catalog-http-1793339105101.json'));
    const soakDir = path.join(lab, 'soak-http-1793339082490');
    const soak = await read(path.join(soakDir, 'report.json'));
    const post = await read(path.join(soakDir, 'postcheck.json'));
    const services = await read(path.join(lab, 'final-service-status.json'));
    const manifest = await read(path.join(lab, 'real-materials/manifest.json'));
    const integrity = { originals: [], copies: [], code: [], specUnchanged: false };
    for (const input of manifest.inputs) {
        const bytes = await fs.readFile(path.join('C:/Users/KROLL/Downloads', input.name));
        integrity.originals.push({ name: input.name, pass: sha(bytes) === input.sha256 });
    }
    for (const doc of manifest.documents) {
        const bytes = await fs.readFile(path.join(lab, 'real-materials', doc.name));
        integrity.copies.push({ name: doc.name, pass: sha(bytes) === doc.sha256 });
    }
    integrity.specUnchanged = baseline.spec_sha256 === local.spec_sha256 && local.spec_sha256 === http.specSHA256
        && http.specSHA256 === soak.catalogSpecSHA256;
    if (!integrity.specUnchanged || !integrity.originals.every(row => row.pass) || !integrity.copies.every(row => row.pass)
        || local.passed !== local.total || http.passed !== http.total || !post.pass || !services.pass) {
        throw new Error('Stage evidence is incomplete');
    }
    const metrics = http.cases.map(row => row.ms);
    const loadRows = soak.phases.map(phase => `| ${phase.name} | ${phase.requests} | ${phase.p50Ms} | ${phase.p95Ms} |`).join('\n');
    const report = `# Поиск по товарным позициям — 30.10.2026

Продолжение основного плана закрытого поиска. Код: C:/Users/KROLL/Desktop/Ai.
Это изменение алгоритма извлечения и памяти; обучение весов модели не выполнялось.
Рабочая LMS, публикация и GitHub не затронуты.

## Исправленное поведение

Раньше контекст указывал на целый файл с десятками товаров. Теперь доверенный
импорт передаёт код и заголовок каждой позиции; поиск сохраняет конкретный товар.
Повтор одного кода в двух выгрузках не создаёт два варианта ответа. Права, текущая
редакция и срок доступности проверяются до выбора товара и показа уточнений.

| Вопрос | Результат |
|---|---|
| Какой состав у медового мусса Dolche Vita? | Уточнение варианта, без случайного выбора |
| С миндалем | Короткий состав выбранного мусса |
| Из чего сделана подставка Хайвэй? → А свечки в комплекте? | Металл → свечки не входят; соседняя позиция не подставляется |
| Из какого материала подставка Звездочка? | Стекло, с переключением на новый товар |
| Какие размеры у Ча Бань Груша? | Сообщение о противоречии между миллиметрами в названии и сантиметрами в описании |
| Из какого дерева Ча Бань Груша? | Грушевое дерево, без спорных размеров |
| Сколько сейчас стоит подставка? | Архивная запись не выдаётся за текущую цену |

Для совпавшего товара проверяются также более авторитетные документы с явно
названным предметом. Расхождение характеристик приводит к обращению к
администратору; при совпадении фактов ссылка отдаётся на более авторитетный источник.
Размеры вида 540×340 мм и 54×34 см сравниваются в одной единице, без перестановки осей.

Память товара и ожидаемой характеристики сохраняется в SQLite. Она разделена
по пользователю, роли, группам и чату; изменение версии или отзыв прав отменяет
прежний контекст. Исправлено также сохранение признака вопроса об упаковке.

Название ссылки теперь содержит название товара. Ссылка открывает исходную
часть выгрузки с её полным текстом. Отдельная карточка с переходом прямо к
нужному фрагменту пока не реализована.

## Проверки

- Локальные тесты: 193/193, из них 21 новый тест товарного поиска; 7,354 с.
- Фиксированный набор реальных товарных диалогов: ${baseline.passed}/${baseline.total} до изменений, ${local.passed}/${local.total} после.
- Исходная теория: 20/20 при проверке этого этапа. Прежние 16 вопросов реальных
  файлов также включены в финальный нагрузочный прогон с новой разметкой каталога.
- Через сайт с настоящими тестовыми сессиями: ${http.passed}/${http.total}; два профиля,
  отдельный чат, понятные ссылки. Медиана ${percentile(metrics, .5)} мс, p95 ${percentile(metrics, .95)} мс.
- В браузере проверены уточнение варианта, ответ «с миндалём», открытие документа.
  После настоящего перезапуска поиска продолжение «А сколько он весит?» вернуло «50 г.».
- Оригиналы Downloads: ${integrity.originals.length}/${integrity.originals.length} хешей совпали; подготовленные копии: ${integrity.copies.length}/${integrity.copies.length}.
- Вопросы и ожидаемые ответы не менялись между исходным и конечным прогоном.
  SHA-256 набора: ${local.spec_sha256}.

Набор использовался при исправлениях, поэтому 14/14 — регрессия, а не слепая
проверка способности понимать любые формулировки или весь каталог.

## Нагрузка финальной версии

Отдельный процесс на 8016, отдельная база, 3 000 источников / 16 997 фрагментов.
Перед нагрузкой прошли ${soak.checks.length}/${soak.checks.length} сценариев. Нагрузка запрошена на 60 секунд;
${soak.summary.passed}/${soak.summary.requests} запросов прошли проверки, ошибок HTTP и ответов нет.

| Режим | Запросы | Медиана, мс | p95, мс |
|---|---:|---:|---:|
${loadRows}

Общий p95: ${soak.summary.p95Ms} мс; максимум: ${soak.summary.maxMs} мс.
SQLite quick_check: ${post.quickCheck}; сохранено ${post.contexts} контекстов;
контрольная сумма базы и ${Object.keys(post.unchangedCode).length} хешей кода совпали.
Память процессов: ${post.rssMiB.join('–')} MiB RSS. Это короткий замер одного
процесса API, без браузера и без OCR на каждом вопросе; не многочасовая приёмка.
Предыдущий промежуточный прогон 451/451 сохранён отдельно и не подменяет финальный.

## Запуск стенда

При первом запуске были ответы 404. Производный .next сохранён под другим
именем. Затем обнаружены пустые папки pdf-parse и sharp; зависимости тестовой
копии восстановлены командой npm ci --ignore-scripts --no-audit --no-fund
по package-lock.json. Исходники сайта не переносились и не заменялись.

Скрипт scripts/start_closed_search_lab.ps1 теперь проверяет готовность маршрутов
входа, документов и чата, а не только поискового индекса. Проверки требуют JSON
и отказ 401 без сессии; ошибка сборки больше не считается успешным запуском.

Финальное состояние: ${services.indexedSources} источников, ${services.indexedPassages} фрагмента,
${services.catalogDocuments} документа; индекс восстановлен с диска за ${services.restoreMs} мс.
Закрытый API 8001 и сайт 3040 работают. Общий чат 8000 в этом этапе не запущен;
он не используется этим стендом. Проверка выполнена с явным --closed-only;
обычный режим verify_lab.cjs по-прежнему требует также общий API.

## Ограничения и следующий шаг

Товарный режим включается только серверной разметкой catalog_items. Он поддерживает
ограниченный набор характеристик и русских формулировок. Не реализованы произвольные
сравнения товаров, все варианты диаметра/длины/массы упаковки, автоматическое
выделение любого состава из рекламного текста и полная проверка всех 1 827 позиций.
Совпадения фактов в разных свободных формулировках пока сравниваются осторожно:
возможен лишний отказ вместо самостоятельного разрешения противоречия.

Выгрузка от 18.06.2026 остаётся архивным снимком. Её наличие не подтверждает
актуальные цены, остатки, официальность или медицинские рекламные утверждения.
Исходные описания сохранены, исправленные размеры не выдумывались.

Далее: сравнение двух товаров по конкретной характеристике, различение размеров
товара и упаковки, затем независимые новые вопросы. После этого — адресная ссылка
на нужную позицию и приёмка на настоящих актуальных документах/сканах.

## Воспроизводимость

Команды запускаются из C:/Users/KROLL/Desktop/Ai; для проверок OPENBLAS_NUM_THREADS=1
и OMP_NUM_THREADS=1. Файлы проверки: evaluation/closed_search/catalog_probe.py,
catalog_http.cjs, catalog_cases_20261030.json; полный тестовый набор:
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -p 'test_site_search*.py'.

Основные результаты в .runtime/closed-search-lab-20261028:
catalog-baseline-1793337866785251200.json; catalog-final-1793339063419103600.json;
catalog-http-1793339105101.json; soak-http-1793339082490/report.json и postcheck.json;
final-service-status.json; catalog-integrity.json.

Полные листинги всех изменённых файлов кода: ${snapshotName}.
Снимки интерфейса: catalog-dialogue-proof.png и catalog-restart-proof.png.
`;
    await fs.writeFile(path.join(root, reportName), report, 'utf8');
    const entry = `## 2026-10-30 — конкретный товар и уточнения в диалоге

Добавлена разметка 2 004 товарных строк с 1 827 ключами. Поиск различает варианты,
сохраняет выбранный товар после рестарта, не смешивает позиции одного файла,
проверяет дубли и противоречия размеров/характеристик, использует понятные подписи
ссылок. Конфликт с официальным документом не скрывается каталогом.

Проверено: 193/193 локальных теста; реальные диалоги 8/14 → 14/14; HTTP 17/17;
нагрузка ${soak.summary.passed}/${soak.summary.requests} на 3 000 источников. Исходные файлы не изменены.
Сайт 3040 и API закрытого поиска 8001 работают; общий чат 8000 не участвует.
Восстановлены зависимости тестового сайта и добавлена проверка готовности его маршрутов.

Отчёт: ${reportName}. Полные листинги: ${snapshotName}.
Следующий этап: сравнение двух товаров, размеры товара/упаковки и независимые
вопросы. Ссылка пока ведёт в файл выгрузки; адресная карточка остаётся отдельной задачей.
Закрытый поиск остаётся основным приоритетом. Обучение весов, рабочая LMS и
публикация не выполнялись. Ниже сохранена история прежних этапов.`;
    for (const name of ['CLOSED_SEARCH_PLAN_2026_10_28.md', 'README.md', 'PROGRESS_LOG.md', 'DECISIONS.md']) {
        await prepend(path.join(root, name), entry);
    }
    const siteEntry = `## 2026-10-30 — проверка товарных диалогов ВатэсAi

Это изолированный стенд, а не рабочая интеграция LMS. Для 41 копии товарных
выгрузок добавлена серверная разметка searchCatalog; содержимое и доступ не менялись.
Экспортёр передаёт ключи товаров собственному API 8001. Проверки HTTP 17/17,
в браузере — уточнение, состав, открытие файла и память после рестарта.
Зависимости восстановлены по package-lock.json; запуск из основного Ai через
scripts/start_closed_search_lab.ps1 проверяет также маршруты сайта.
Подробности: C:/Users/KROLL/Desktop/Ai/${reportName}.
Далее — сравнения товаров и переход к нужной позиции. Публикация не выполнялась.`;
    for (const name of ['PROJECT_CONTEXT.md', 'TASKS.md', 'HANDOFF.md', 'DEPLOYMENT.md']) {
        await prepend(path.join(site, name), siteEntry);
    }
    const files = ['ai_core/site_search_catalog.py', 'ai_core/site_search.py', 'ai_core/site_search_runtime.py',
        'ai_core/site_search_store.py', 'tests/test_site_search_catalog.py',
        'evaluation/closed_search/catalog_cases_20261030.json', 'evaluation/closed_search/catalog_probe.py',
        'evaluation/closed_search/catalog_http.cjs', 'evaluation/closed_search/export_site.cjs',
        'evaluation/closed_search/real_materials_http.cjs', 'evaluation/closed_search/soak_http.cjs',
        'evaluation/closed_search/wait_site_ready.cjs', 'evaluation/closed_search/verify_lab.cjs',
        'scripts/start_closed_search_lab.ps1', 'evaluation/closed_search/finalize_catalog.cjs'];
    let snapshot = '# Полные листинги этапа товарного поиска — 30.10.2026\n\n';
    for (const file of files) {
        const bytes = await fs.readFile(path.join(root, file));
        const hash = sha(bytes);
        integrity.code.push({ file, sha256: hash, bytes: bytes.length });
        const language = file.endsWith('.py') ? 'python' : file.endsWith('.json') ? 'json' : file.endsWith('.ps1') ? 'powershell' : 'javascript';
        snapshot += '\n## ' + file + '\n\nSHA-256: ' + hash + '\n\n````' + language + '\n' + bytes.toString('utf8') + '\n````\n';
    }
    await fs.writeFile(path.join(root, snapshotName), snapshot, 'utf8');
    await fs.writeFile(path.join(lab, 'catalog-integrity.json'), JSON.stringify(integrity, null, 2));
    console.log(JSON.stringify({ report: reportName, snapshot: snapshotName, sourceFiles: files.length,
                                originalsUnchanged: true, copiesUnchanged: true, specUnchanged: integrity.specUnchanged }));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });

````
