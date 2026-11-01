# Полные листинги PDF-подсветки — 30.10.2026


## requirements.txt

SHA-256: 5dd61b11d9fbcedeca71ab25169aad64fef961550705ddb53c7099b270e5a21e

````text
# Основной стек проекта собственной нейросети.
torch>=2.2,<3.0
numpy>=2.0,<3.0
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
pydantic>=2.0,<3.0
pypdf==6.21.4
pdfplumber==0.11.9
snowballstemmer==3.1.1
opencv-python-headless==4.13.0.92

````

## ai_core/site_pdf_locations.py

SHA-256: 046e2bd7fedb8829838b944b133ea67a338f75df8da68f3ee7992e4421d98dd2

````python
"""Координаты слов PDF извлекаются в фоне и привязываются к байтам документа."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import tempfile
from pathlib import Path

LOCATION_VERSION = 'pdf-focus-v1-' + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def tokens(text):
    return re.findall(r'[^\W_]+', text.casefold().replace('ё', 'е'))


def extract_pdf_layout(data_url):
    # Ленивый импорт разрывает зависимость поискового ядра от OCR-инструментов.
    import pdfplumber
    from .site_documents import CACHE, _command, _run, _ocr
    from .site_scan_tables import read_image, parse_words
    data = base64.b64decode(data_url.split(';base64,', 1)[1], validate=True)
    if len(data) > 15 * 1024 * 1024 or not data.startswith(b'%PDF-'):
        raise ValueError('pdf_layout_invalid')
    fingerprint = hashlib.sha256(LOCATION_VERSION.encode() + data).hexdigest()
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / ('positions-' + fingerprint + '.json')
    if cache.is_file():
        try:
            saved = json.loads(cache.read_text(encoding='utf-8'))
            body = json.dumps(saved['layout'], ensure_ascii=False, sort_keys=True)
            if saved['digest'] == hashlib.sha256(body.encode()).hexdigest():
                return saved['layout']
        except (OSError, KeyError, ValueError, TypeError):
            pass
    layout = {'sha256': hashlib.sha256(data).hexdigest(), 'pages': [], 'page_count': 0}
    with tempfile.TemporaryDirectory(prefix='vates-pdf-positions-') as directory:
        file = Path(directory) / 'source.pdf'
        file.write_bytes(data)
        with pdfplumber.open(file) as document:
            layout['page_count'] = len(document.pages)
            for number, page in enumerate(document.pages[:40], 1):
                words = []
                try:
                    native = page.extract_words()
                    raster = any(math.prod(image.get('srcsize', (0, 0))) > 500000 for image in page.images)
                    if len(native) >= 8 and not raster and not page.rotation and page.cropbox == page.bbox:
                        for item in native:
                            words.append({'text': item['text'], 'confidence': 100,
                                'box': [item['x0'] / page.width, item['top'] / page.height,
                                        item['x1'] / page.width, item['bottom'] / page.height]})
                    else:
                        # Координаты берём с исходного растра, без deskew/поворота:
                        # иначе рамки не совпадут с изображением в просмотрщике.
                        prefix = Path(directory) / ('page-' + str(number))
                        _run([_command('pdftoppm'), '-cropbox', '-f', str(number), '-l', str(number),
                              '-r', '180', '-png', '-singlefile', str(file), str(prefix)])
                        image = prefix.with_suffix('.png')
                        height, width = read_image(image).shape
                        for item in parse_words(_ocr(image, tsv=True)):
                            words.append({'text': item['text'], 'confidence': item['confidence'],
                                'box': [item['left'] / width, item['top'] / height,
                                        (item['left'] + item['width']) / width, (item['top'] + item['height']) / height]})
                except Exception:
                    # Ошибка геометрии не превращает хороший текст в выдуманную позицию.
                    words = []
                clean = [item for item in words if valid_box(item['box'])]
                layout['pages'].append({'page': number, 'words': clean[:12000]})
    body = json.dumps(layout, ensure_ascii=False, sort_keys=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=CACHE, suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        json.dump({'layout': layout, 'digest': hashlib.sha256(body.encode()).hexdigest()}, handle, ensure_ascii=False)
    try:
        temporary.replace(cache)
    finally:
        temporary.unlink(missing_ok=True)
    return layout


def valid_box(box):
    return (isinstance(box, list) and len(box) == 4
            and all(isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1 for value in box)
            and box[0] < box[2] and box[1] < box[3])


def locate_fragment(layout, text):
    """Только полное последовательное совпадение; повторы не выбираем наугад."""
    if not isinstance(layout, dict):
        return None
    base = {'sha256': layout.get('sha256'), 'page_count': layout.get('page_count'), 'status': 'unlocated'}
    wanted = tokens(text)
    if len(wanted) < 3 or len(wanted) > 1500:
        return base
    stream = []
    for page in layout.get('pages', []):
        for word in page.get('words', []):
            # Пропуск сомнительного слова не должен склеивать два разных куска.
            confidence = word.get('confidence', 0)
            for token in tokens(word.get('text', '')):
                reliable = confidence >= (80 if any(char.isdigit() for char in token) else 60)
                stream.append((token if reliable else '\x00', page['page'], word['box']))
    sequence = [item[0] for item in stream]
    matches = [index for index, value in enumerate(sequence) if value == wanted[0] and sequence[index:index + len(wanted)] == wanted]
    if len(matches) != 1:
        return {**base, 'status': 'ambiguous' if matches else 'unlocated'}
    selected = stream[matches[0]:matches[0] + len(wanted)]
    pages = {}
    for _, page, box in selected:
        rectangles = pages.setdefault(page, [])
        if rectangles and box == rectangles[-1]:
            continue
        # Объединяем соседние слова одной строки, но не соседние столбцы.
        if (rectangles and abs(rectangles[-1][1] - box[1]) < .004
                and abs(rectangles[-1][3] - box[3]) < .004 and 0 <= box[0] - rectangles[-1][2] < .035):
            rectangles[-1] = [rectangles[-1][0], min(rectangles[-1][1], box[1]), box[2], max(rectangles[-1][3], box[3])]
        else:
            rectangles.append(list(box))
    if sum(len(value) for value in pages.values()) > 200:
        return base
    return {**base, 'status': 'located', 'initial_page': min(pages),
            'hits': [{'page': page, 'rects': rects} for page, rects in sorted(pages.items())]}

````

## ai_core/site_search_fragments.py

SHA-256: 5db4481ba2d024f31832737684c20753875e6c5a3d5f92433687609cbcad9c84

````python
"""Адреса проверенных фрагментов: редакция и доступ проверяются при открытии."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlencode, urlsplit
from .site_pdf_locations import locate_fragment


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


def build_fragments(passages, sentences=None):
    """Готовим указатели один раз вместе с индексом, не во время OCR/вопроса."""
    grouped = {}
    for part in passages:
        if not part.source.get('fragment_url'):
            continue
        # Синтетическое правило из нескольких файлов не выдаём за цитату одного.
        if (part.structure or {}).get('document_origins') or (part.structure or {}).get('document_guard'):
            continue
        product = any(row['heading'] == part.heading for row in part.source.get('catalog_items', []))
        # В PDF выделяем предложение с фактом, а не соседние колонтитулы.
        # Сохраняем также старый указатель на абзац: добавление подсветки
        # не должно ломать ранее выданную ссылку на неизменённый документ.
        pieces = [part.text]
        if part.source.get('pdf_layout') and not product and sentences:
            pieces.extend(sentences(part.text))
        for piece in pieces:
            group = (part.source['id'], part.heading, '' if product else piece)
            if group not in grouped:
                grouped[group] = {'source': part.source, 'heading': part.heading, 'parts': [], 'kind': 'product' if product else 'fragment'}
            if piece not in grouped[group]['parts']:
                grouped[group]['parts'].append(piece)
    targets, by_heading = {}, {}
    for item in grouped.values():
        source = item['source']
        text = '\n\n'.join(item['parts'])
        token = hashlib.sha256(json.dumps([source['id'], source['revision'], item['heading'], text], ensure_ascii=False).encode()).hexdigest()
        target = {'id': source['id'], 'revision': source['revision'], 'fragment': token,
                  'title': source['title'], 'heading': item['heading'], 'text': text,
                  'kind': item['kind'], 'source_url': source['url']}
        location = locate_fragment(source.get('pdf_layout'), text)
        if location is not None:
            target['pdf_location'] = location
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
    if selected and source.get('pdf_layout'):
        # Новый ответ ведёт к самой короткой точной цитате. Более широкий
        # старый абзац остаётся доступным по прежнему адресу.
        shortest = min(len(compact(item['text'])) for item in selected)
        selected = [item for item in selected if len(compact(item['text'])) == shortest]
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

SHA-256: f5f3acb23cec4e3a4d98b73a7fbd6b706a6a00b09fa4b4fdcc427710b0054f04

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
        self.fragment_targets, self.fragment_headings = build_fragments(self.passages, self._sentences)
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

SHA-256: bd4be1ed59d8bcdbe276530e8cc76da1e531ba2538fbb7391827b3a3a096aa72

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
from .site_pdf_locations import extract_pdf_layout, LOCATION_VERSION
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
EXTRACTION_VERSION = PARSER_VERSION + '-' + LOCATION_VERSION


async def runtime_for(site: str, host: dict) -> DurableSearch:
    async with runtime_lock:
        current = indexes.get(site)
        if current is None or current.index.origins != set(host["origins"]):
            try:
                # Имя уже проверено authenticate; каждому сайту принадлежит
                # отдельная база. Чтение диска не блокирует цикл HTTP-сервера.
                current = await asyncio.to_thread(DurableSearch, host["origins"], STORAGE / (site + ".sqlite3"), extraction_version=EXTRACTION_VERSION)
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
    fingerprint = hashlib.sha256(EXTRACTION_VERSION.encode() + json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
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
                if str(source.get('file_name', '')).lower().endswith('.pdf'):
                    try:
                        source['pdf_layout'] = extract_pdf_layout(data)
                    except Exception:
                        source['pdf_layout'] = None
                        logging.getLogger(__name__).warning('closed_search_pdf_position_unavailable')
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

## ai_core/site_search_store.py

SHA-256: 66e5735982583f085948f482b09cc44a8c7faedbdb61b5b192bf4abdddf7a660

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
                 "parents", "status", "expires_at", "valid_from", "authority", "complete", "keywords", "file_name", "structure_unavailable", "catalog_items", "fragment_url", "pdf_layout"}


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

## ai_core/site_search_runtime.py

SHA-256: 032ed8ec54ea7170e7b8d84d9c5e82b48bd68e296746470acd0345f254dd03f1

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
    for name in ("site_search.py", "site_search_language.py", "site_search_reasoning.py", "site_search_conditions.py", "site_search_structure.py", "site_search_links.py", "site_search_catalog.py", "site_search_measurements.py", "site_search_comparison.py", "site_search_fragments.py", "site_pdf_locations.py"):
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

## tests/test_site_search_pdf_focus.py

SHA-256: cd62f356036c6b278a6d644c8ccb2a46ee37b564a31b25a7881b9d93a55ab924

````python
"""Проверки страницы и геометрии без догадок о похожих разделах."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from ai_core.site_documents import extract_document
from ai_core.site_pdf_locations import extract_pdf_layout, locate_fragment, valid_box
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from ai_core.site_search_fragments import build_fragments

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'evaluation/closed_search/fixtures/pdf-focus'
ORIGIN = 'http://127.0.0.1:3040'
B = Principal('b', 'staff')


class PdfFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads((ASSETS / 'spec.json').read_text(encoding='utf-8'))
        cls.layouts, cls.urls = {}, {}
        for key in ['base', 'moved', 'removed', 'duplicate', 'scan']:
            cls.urls[key] = 'data:application/pdf;base64,' + base64.b64encode((ASSETS / (key + '.pdf')).read_bytes()).decode()
            cls.layouts[key] = extract_pdf_layout(cls.urls[key])

    def source(self, key):
        text, complete = extract_document(self.urls[key], key + '.pdf')
        return {'id': 'document:pdf', 'revision': key, 'title': 'Учебный PDF', 'kind': 'document',
                'url': ORIGIN + '/tasks?tab=docs&documentId=pdf', 'fragment_url': ORIGIN + '/closed-search-fragment',
                'access': {'scope': 'restricted', 'users': ['b']}, 'complete': complete,
                'sections': [{'heading': 'Учебный PDF', 'text': text}], 'pdf_layout': self.layouts[key]}

    def locate(self, key, text=None):
        return locate_fragment(self.layouts[key], text or self.spec['rule'])

    def test_native_page_two(self):
        self.assertEqual(self.locate('base')['initial_page'], 2)

    def test_box_matches_actual_line_coordinates(self):
        box = self.locate('base')['hits'][0]['rects'][0]
        self.assertAlmostEqual(box[0], 50 / 595, places=3)
        self.assertTrue(.20 < box[1] < .21)
        self.assertTrue(.21 < box[3] < .23)
        self.assertTrue(.57 < box[2] < .60)

    def test_move_changes_page_to_three(self):
        self.assertEqual(self.locate('moved')['initial_page'], 3)
        self.assertNotEqual(self.layouts['base']['sha256'], self.layouts['moved']['sha256'])

    def test_removed_rule_has_no_location(self):
        self.assertEqual(self.locate('removed')['status'], 'unlocated')

    def test_duplicate_rule_not_guessed(self):
        result = self.locate('duplicate')
        self.assertEqual(result['status'], 'ambiguous')
        self.assertNotIn('initial_page', result)

    def test_scan_uses_visible_geometry(self):
        result = self.locate('scan', self.spec['scan_rule'])
        self.assertEqual(result['initial_page'], 2)
        self.assertTrue(all(valid_box(box) for box in result['hits'][0]['rects']))

    def test_unknown_words_not_matched(self):
        self.assertEqual(self.locate('base', 'Поставку Лиственит принимает директор.')['status'], 'unlocated')

    def test_numbers_must_match(self):
        layout = {'sha256': 'a' * 64, 'page_count': 1, 'pages': [{'page': 1, 'words': [
            {'text': value, 'confidence': 100, 'box': [.1, .1, .3, .2]} for value in ['Срок', 'хранения', '17', 'дней']]}]}
        self.assertEqual(locate_fragment(layout, 'Срок хранения 7 дней')['status'], 'unlocated')

    def test_low_confidence_digit_not_silently_accepted(self):
        layout = {'sha256': 'a' * 64, 'page_count': 1, 'pages': [{'page': 1, 'words': [
            {'text': value, 'confidence': confidence, 'box': [.1, .1, .3, .2]}
            for value, confidence in [('Срок', 100), ('хранения', 100), ('7', 65), ('дней', 100)]]}]}
        self.assertEqual(locate_fragment(layout, 'Срок хранения 7 дней')['status'], 'unlocated')

    def test_cache_does_not_render_or_ocr_again(self):
        with patch('ai_core.site_documents._run', side_effect=AssertionError('Unexpected OCR')):
            self.assertEqual(extract_pdf_layout(self.urls['scan']), self.layouts['scan'])

    def test_search_link_targets_sentence_without_header(self):
        index = ClosedSiteIndex([ORIGIN])
        index.publish([self.source('base')], index.begin('base'))
        answer = index.search(self.spec['question'], B)
        self.assertEqual(answer['status'], 'answer')
        token = parse_qs(urlsplit(answer['links'][0]['url']).query)['fragment'][0]
        target = index.fragment_targets[('document:pdf', token)]
        self.assertEqual(target['text'], self.spec['rule'])
        self.assertEqual(target['pdf_location']['initial_page'], 2)

    def test_restart_preserves_location(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'search.sqlite3'
            runtime = DurableSearch([ORIGIN], db)
            runtime.publish([self.source('base')], runtime.begin('base'))
            restored = DurableSearch([ORIGIN], db)
            self.assertTrue(restored.restored)
            answer = restored.query(self.spec['question'], B, 'c', None, {'document:pdf': 'base'})
            token = parse_qs(urlsplit(answer['links'][0]['url']).query)['fragment'][0]
            result = restored.fragment('document:pdf', 'base', token, B, {'document:pdf': 'base'})
            self.assertEqual(result['pdf_location']['initial_page'], 2)
            self.assertEqual(restored.fragment('document:pdf', 'base', token, Principal('a', 'staff'), {'document:pdf': 'base'})['status'], 'not_found')

    def test_old_paragraph_links_remain_available(self):
        index = ClosedSiteIndex([ORIGIN])
        index.publish([self.source('base')], index.begin('base'))
        legacy, _ = build_fragments(index.passages)
        self.assertTrue(legacy)
        for key, target in legacy.items():
            self.assertIn(key, index.fragment_targets)
            self.assertEqual(index.fragment_targets[key]['text'], target['text'])
        answer = index.search(self.spec['question'], B)
        self.assertEqual(len(answer['links']), 1)


if __name__ == '__main__':
    unittest.main()

````

## evaluation/closed_search/pdf_focus_assets.py

SHA-256: dbb525a8be658b2fdeab4e2e59e12fe75879da94348a5c3efc29f599ebca1ff0

````python
"""Учебные PDF для проверки подсветки и переноса раздела, без данных компании."""
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'evaluation/closed_search/fixtures/pdf-focus'
RULE = 'Поставку Лиственит принимает координатор.'
SCAN_RULE = 'Поставку Бериллит принимает диспетчер.'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'spec.json').exists():
        raise ValueError('Frozen PDF focus corpus already exists')
    pdfmetrics.registerFont(TTFont('FocusArial', 'C:/Windows/Fonts/arial.ttf'))
    docs = []
    for key, pages in [
        ('base', ['Общие сведения.', RULE, 'Архивные заметки.']),
        ('moved', ['Общие сведения.', 'Архивные заметки.', RULE]),
        ('removed', ['Общие сведения.', 'Архивные заметки.', 'Приёмка описана в другом документе.']),
        ('duplicate', ['Общие сведения.', RULE, RULE]),
        ('scan', ['Общие сведения.', SCAN_RULE]),
    ]:
        target = OUT / (key + '.pdf')
        pdf = canvas.Canvas(str(target), pagesize=(595, 842), invariant=1)
        for number, text in enumerate(pages, 1):
            if key == 'scan':
                image = Image.new('RGB', (1785, 2526), 'white')
                draw = ImageDraw.Draw(image)
                font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 42)
                draw.text((150, 130), 'Учебный скан ВатэсAi', font=font, fill='black')
                draw.text((150, 230), 'Все правила вымышлены.', font=font, fill='black')
                draw.text((150, 520), text, font=font, fill='black')
                draw.text((150, 2350), 'Страница ' + str(number), font=font, fill='black')
                pdf.drawImage(ImageReader(image), 0, 0, 595, 842)
            else:
                pdf.setFont('FocusArial', 14)
                pdf.drawString(50, 790, 'Учебная проверка PDF ВатэсAi.')
                pdf.drawString(50, 760, 'Все правила вымышлены.')
                pdf.drawString(50, 660, text)
                pdf.drawString(50, 45, 'Страница ' + str(number))
            pdf.showPage()
        pdf.save()
        docs.append({'key': key, 'file': target.name, 'pages': len(pages), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    spec = {'documents': docs, 'question': 'Кто принимает поставку Лиственит?', 'rule': RULE,
            'scan_question': 'Кто принимает поставку Бериллит?', 'scan_rule': SCAN_RULE,
            'expected': {'base_page': 2, 'moved_page': 3, 'scan_page': 2, 'removed': 'not_found',
                         'old_revision': 'changed', 'duplicate_location': 'ambiguous'}}
    (OUT / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'files': len(docs), 'directory': str(OUT)}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('Fixture creation failed: ' + str(error)) from error

````

## evaluation/closed_search/pdf_focus_http.cjs

SHA-256: 8251c4d95d3dd99c1ec7d935b50b47825d5547db4549e91481942d236f99308f

````javascript
/* Проверка PDF только на учебных копиях изолированного стенда. */
const { fs, path, lab, origin, login, storage, call } = require('./lab_client.cjs');
const { waitSnapshot } = require('./scan_http.cjs');
const { createHash } = require('node:crypto');
const assets = path.join(__dirname, 'fixtures/pdf-focus');
const nativeId = 'focus_native', scanId = 'focus_scan';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
async function main() {
    const spec = JSON.parse(await fs.readFile(path.join(assets, 'spec.json'), 'utf8'));
    const cookies = { admin: await login('closed-admin'), b: await login('closed-b'), a: await login('closed-a') };
    const data = {};
    for (const item of spec.documents) {
        const bytes = await fs.readFile(path.join(assets, item.file));
        if (hash(bytes) !== item.sha256) throw new Error('Frozen PDF differs');
        data[item.key] = 'data:application/pdf;base64,' + bytes.toString('base64');
    }
    const catalog = await storage('tea_hub_urgent_files_v1', cookies.admin);
    for (const [id, key, name] of [[nativeId, 'base', 'Учебный_регламент_Лиственит.pdf'], [scanId, 'scan', 'Учебный_скан_Бериллит.pdf']]) {
        const record = { id, name, section: 'Учебная проверка страниц PDF', hasSeparateData: true,
            size: 'Учебный PDF', date: '30.10.2026', searchAuthority: 'reference',
            accessPolicy: { version: 1, visibility: 'restricted', userIds: ['closed_lab_b'] } };
        const existing = catalog.find(item => item.id === id);
        if (existing && JSON.stringify(existing) !== JSON.stringify(record)) throw new Error('Fixture record changed; refusing overwrite');
        if (!existing) { await storage('file_data_' + id, cookies.admin, data[key]); catalog.push(record); }
    }
    await storage('tea_hub_urgent_files_v1', cookies.admin, catalog);
    await fs.mkdir(path.join(lab, 'pdf-focus-assets'), { recursive: true });
    await fs.writeFile(path.join(lab, 'pdf-focus-assets/manifest.json'), JSON.stringify([{ id: nativeId }, { id: scanId }]));
    const mode = process.argv[2];
    if (mode === '--move' || mode === '--restore') {
        await storage('file_data_' + nativeId, cookies.admin, data[mode === '--move' ? 'moved' : 'base']);
        const ready = await waitSnapshot();
        console.log(JSON.stringify({ mode, state: ready.state })); return;
    }
    const cases = [], started = Date.now();
    const check = (id, pass, detail = {}) => cases.push({ id, pass: Boolean(pass), ...detail });
    const urls = {};
    async function ask(id, question) {
        const result = await call('/api/ai-chat', { documentId: id, chatId: 'pdf-focus-' + started + '-' + cases.length,
            messages: [{ role: 'user', content: question }] }, cookies.b);
        if (result.response.status === 429) throw new Error('Rate limited; Retry-After ' + result.response.headers.get('retry-after'));
        if (!result.response.ok) throw new Error('Question failed: ' + result.response.status);
        return result.value;
    }
    async function open(link, actor = 'b') {
        const url = new URL(link, origin);
        const result = await call('/api/closed-search-fragment' + url.search, undefined, cookies[actor]);
        return { http: result.response.status, ...result.value };
    }
    async function bytes(url, actor = 'b') {
        const response = await fetch(origin + url, { headers: cookies[actor] ? { Cookie: cookies[actor] } : {}, signal: AbortSignal.timeout(20000) });
        const value = Buffer.from(await response.arrayBuffer());
        return { status: response.status, sha256: hash(value), isPdf: value.subarray(0, 5).toString() === '%PDF-', cache: response.headers.get('cache-control') };
    }
    await storage('file_data_' + nativeId, cookies.admin, data.base);
    try {
        await waitSnapshot();
        const answer = await ask(nativeId, spec.question);
        urls.base = answer.sourceLinks?.[0]?.url;
        const base = await open(urls.base);
        check('native-page-2', base.pdf?.initialPage === 2 && base.text === spec.rule, { result: base });
        const file = await bytes(base.pdf.url);
        check('original-bytes-and-no-cache', file.status === 200 && file.isPdf && file.sha256 === spec.documents.find(item => item.key === 'base').sha256 && /no-store/.test(file.cache), { file });
        urls.basePdf = base.pdf.url;
        for (const actor of ['a', 'anonymous']) {
            const denied = await bytes(base.pdf.url, actor);
            check('pdf-access-' + actor, denied.status === (actor === 'a' ? 404 : 401) && !denied.isPdf, { result: denied });
        }
        const scanAnswer = await ask(scanId, spec.scan_question);
        urls.scan = scanAnswer.sourceLinks?.[0]?.url;
        const scan = await open(urls.scan);
        check('scan-page-2', scan.pdf?.initialPage === 2 && scan.text === spec.scan_rule, { result: scan });
        const scanFile = await bytes(scan.pdf.url);
        check('scan-bytes', scanFile.isPdf && scanFile.sha256 === spec.documents.find(item => item.key === 'scan').sha256);
        await storage('file_data_' + nativeId, cookies.admin, data.moved);
        const early = await open(urls.base);
        check('move-blocks-old-before-index', early.http === 409 && !early.pdf && !early.text);
        const oldBytes = await bytes(urls.basePdf);
        check('move-blocks-old-pdf-bytes', oldBytes.status === 409 && !oldBytes.isPdf);
        await waitSnapshot();
        const movedAnswer = await ask(nativeId, spec.question);
        urls.moved = movedAnswer.sourceLinks?.[0]?.url;
        const moved = await open(urls.moved);
        check('moved-rule-page-3', moved.pdf?.initialPage === 3 && moved.text === spec.rule && urls.moved !== urls.base, { result: moved });
        const movedFile = await bytes(moved.pdf.url);
        check('moved-bytes', movedFile.sha256 === spec.documents.find(item => item.key === 'moved').sha256);
        await storage('file_data_' + nativeId, cookies.admin, data.removed);
        check('removal-blocks-previous', (await open(urls.moved)).http === 409);
        await waitSnapshot();
        const removedAnswer = await ask(nativeId, spec.question);
        check('removed-rule-not-answered', removedAnswer.search_status === 'not_found' && !removedAnswer.sourceLinks?.length
            && !removedAnswer.output_text.includes('координатор'), { answer: removedAnswer });
        await storage('file_data_' + nativeId, cookies.admin, data.duplicate);
        await waitSnapshot();
        const duplicateAnswer = await ask(nativeId, spec.question);
        const duplicate = await open(duplicateAnswer.sourceLinks?.[0]?.url);
        check('duplicate-not-guessed', duplicate.http === 200 && !duplicate.pdf && duplicate.pdfMessage?.includes('несколько'), { result: duplicate });
    } finally {
        await storage('file_data_' + nativeId, cookies.admin, data.base);
        await waitSnapshot();
    }
    check('base-restored', (await open(urls.base)).pdf?.initialPage === 2);
    const file = path.join(lab, 'pdf-focus-http-' + started + '.json');
    await fs.writeFile(file, JSON.stringify({ specSHA256: hash(await fs.readFile(path.join(assets, 'spec.json'))),
        urls, total: cases.length, passed: cases.filter(item => item.pass).length, cases }, null, 2));
    console.log(JSON.stringify({ file, total: cases.length, passed: cases.filter(item => item.pass).length,
        failures: cases.filter(item => !item.pass) }));
    if (cases.some(item => !item.pass)) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## evaluation/closed_search/pdf_focus_postcheck.cjs

SHA-256: 9ca8719528b727f3141031f89f79c2b4f10ca45968eefb729a971ac7baa0a746

````javascript
/* Проверка сохранённых ссылок и закрытого PDF после настоящего рестарта API. */
const { fs, path, lab, origin, call, login } = require('./lab_client.cjs');
const { createHash } = require('node:crypto');

async function main() {
    const reportFile = process.argv[2];
    if (!reportFile || path.basename(reportFile) !== reportFile) throw new Error('Pass a report filename inside the lab');
    const prior = JSON.parse(await fs.readFile(path.join(lab, reportFile), 'utf8'));
    const b = await login('closed-b'), a = await login('closed-a');
    const cases = [];
    const check = (id, pass, details = {}) => cases.push({ id, pass: Boolean(pass), ...details });
    for (const key of ['base', 'scan']) {
        const url = new URL(prior.urls[key], origin);
        const opened = await call('/api/closed-search-fragment' + url.search, undefined, b);
        check(key + '-location-after-restart', opened.response.status === 200 && opened.value.pdf?.initialPage === 2,
            { page: opened.value.pdf?.initialPage });
        const response = await fetch(origin + opened.value.pdf.url, { headers: { Cookie: b }, signal: AbortSignal.timeout(20000) });
        const bytes = Buffer.from(await response.arrayBuffer());
        check(key + '-original-after-restart', response.status === 200 && bytes.subarray(0, 5).toString() === '%PDF-'
            && createHash('sha256').update(bytes).digest('hex') === opened.value.pdf.sha256);
    }
    const staleUrl = new URL(prior.urls.moved, origin);
    const stale = await call('/api/closed-search-fragment' + staleUrl.search, undefined, b);
    check('stale-revision-blocked-after-restart', stale.response.status === 409 && !stale.value.text && !stale.value.pdf);
    // Проверяем именно HTML-страницу, чтобы закрытый текст не попал в SSR.
    for (const [actor, cookie, expected] of [['allowed', b, true], ['denied', a, false], ['anonymous', '', false]]) {
        const response = await fetch(origin + prior.urls.base, { headers: cookie ? { Cookie: cookie } : {}, signal: AbortSignal.timeout(20000) });
        const html = await response.text();
        check('html-' + actor, html.includes('Поставку Лиственит принимает координатор.') === expected,
            { http: response.status });
    }
    const file = path.join(lab, 'pdf-focus-postcheck-' + Date.now() + '.json');
    const report = { total: cases.length, passed: cases.filter(item => item.pass).length, cases };
    await fs.writeFile(file, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ file, total: report.total, passed: report.passed, failures: cases.filter(item => !item.pass) }));
    if (report.passed !== report.total) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## evaluation/closed_search/verify_lab.cjs

SHA-256: 8668f6a031156a16c6018a756a8d9fbf3b790ee61241837c1f999341fea62434

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
    let pdfFocusCount = 0;
    try {
        pdfFocusCount = (JSON.parse(await fs.readFile(path.join(lab, 'pdf-focus-assets/manifest.json'), 'utf8'))).length;
    } catch (error) {
        if (error.code !== 'ENOENT') throw error;
    }
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
    const expectedDocuments = 9 + reasoningFixtures.documents.length + conditionFixtures.documents.length + structureFixtures.documents.length + structureAssets.length + scanFixtures.documents.length + layoutFixtures.documents.length + qualityFixtures.documents.length + multipageFixtures.documents.length + crossFixtures.documents.length + linkFixtures.documents.length + realManifest.documents.length + comparisonCount + fragmentCount + pdfFocusCount;
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

## evaluation/closed_search/finalize_pdf_focus.cjs

SHA-256: 964568a63fc8b46ade71760dfea0701fadeb3ff4ea06431e8f22fa3fd211baad

````javascript
/* Сохраняет проверенные результаты этапа и полные листинги без секретов. */
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const site = path.join(lab, 'tea-app-main');
const reportName = 'CLOSED_SEARCH_PDF_FOCUS_REPORT_2026_10_30.md';
const codeName = 'CLOSED_SEARCH_PDF_FOCUS_CODE_SNAPSHOT_2026_10_30.md';
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
    const httpName = 'pdf-focus-http-1793344348012.json';
    const postName = 'pdf-focus-postcheck-1793344628156.json';
    const http = await read(httpName), post = await read(postName), status = await read('pdf-focus-services.json');
    const manifest = await read('real-materials/manifest.json'), syntax = await read('pdf-focus-syntax.json');
    const unit = await fs.readFile(path.join(lab, 'pdf-focus-unittest.log'), 'utf8');
    const fixtures = path.join(__dirname, 'fixtures/pdf-focus');
    const specBytes = await fs.readFile(path.join(fixtures, 'spec.json'));
    const spec = JSON.parse(specBytes.toString('utf8'));
    const integrity = { originals: [], copies: [], fixtures: [], code: [] };
    for (const input of manifest.inputs) {
        const actual = sha(await fs.readFile(path.join('C:/Users/KROLL/Downloads', input.name)));
        integrity.originals.push({ name: input.name, expected: input.sha256, actual, pass: input.sha256 === actual });
    }
    for (const doc of manifest.documents) {
        const actual = sha(await fs.readFile(path.join(lab, 'real-materials', doc.name)));
        integrity.copies.push({ name: doc.name, expected: doc.sha256, actual, pass: doc.sha256 === actual });
    }
    for (const doc of spec.documents) {
        const actual = sha(await fs.readFile(path.join(fixtures, doc.file)));
        integrity.fixtures.push({ name: doc.file, expected: doc.sha256, actual, pass: doc.sha256 === actual });
    }
    if (http.passed !== http.total || post.passed !== post.total || !status.pass || !status.restoredFromDisk
        || !/Ran 245 tests/.test(unit) || !/\bOK\s*$/.test(unit) || syntax.some(item => item.errors.length)
        || http.specSHA256 !== sha(specBytes)
        || ![...integrity.originals, ...integrity.copies, ...integrity.fixtures].every(item => item.pass)) {
        throw new Error('Acceptance evidence is incomplete');
    }
    const screenshots = ['native', 'zoom', 'other-page', 'scan', 'stale', 'moved', 'restart'];
    for (const name of screenshots) {
        if (!(await fs.stat(path.join(lab, 'pdf-focus-' + name + '-proof.png'))).size) throw new Error('Browser proof missing: ' + name);
    }
    const report = `# Открытие страницы PDF и подсветка цитаты — 30.10.2026

Основной проект: C:/Users/KROLL/Desktop/Ai. Изменены собственное поисковое ядро
и изолированный стенд 3040 / API 8001. Рабочая LMS не изменялась. Это улучшение
извлечения и поиска источников, не обучение весов нейросети. Публикации не было.

## Результат

Ссылка из нового ответа открывает исходный PDF на странице с подтверждённой
цитатой. Нужная строка выделена жёлтым и сразу видна в области просмотра.
Работают увеличение, переходы между страницами и кнопка возврата к цитате.
На соседних страницах чужая подсветка не появляется.

Координаты извлекаются в фоне: для текстовых PDF — из текстового слоя, для
сканов — OCR по исходному изображению страницы. Позиции сохраняются в кэше
и SQLite вместе с индексом, связаны с контрольной суммой PDF. Нажатие ссылки
не запускает OCR. Добавлена зависимость pdfplumber==0.11.9; существующие
Poppler и Tesseract с русским языком используются повторно.

Новые адреса PDF ведут к отдельному предложению с фактом. Старые адреса
абзацев неизменённой редакции тоже сохранены: они не ломаются из-за появления
более точных ссылок. История ранее отправленных сообщений не переписывается.

## Перенос, удаление и доступ

| Ситуация | Проверенный результат |
|---|---|
| Учебный текстовый PDF | Цитата на странице 2 из 3, рамка на нужной строке |
| Учебный скан | Цитата на странице 2 из 2, рамка на исходном растре |
| Перенос раздела в новую редакцию | Старый адрес сообщает об обновлении, новый ответ открывает страницу 3 |
| Удаление раздела в новой редакции | Старые адреса блокируются, новый вопрос получает отсутствие данных |
| Два одинаковых вхождения | Нет выбора страницы наугад; показаны цитата и предложение открыть исходник |
| Другой профиль без доступа | PDF не выдаётся; код 404, содержимого нет |
| Нет сессии | PDF не выдаётся; код 401 |
| Смена редакции до завершения индексации | Старая ссылка уже заблокирована, прежние байты не выдаются |
| Перезапуск API | Ссылки и координаты восстановлены с диска |

Права проверяются сервером сайта при открытии фрагмента и повторно при
получении байтов PDF. Также проверяются актуальная редакция и SHA-256 файла.
Адрес сам по себе не даёт доступ. Ответ с PDF имеет private, no-store.
HTML-страница также проверена: закрытый текст не попадает в ответ профилю А
или анонимному посетителю. Уже показанный авторизованному пользователю документ
не исчезает сам по себе при последующем изменении; новая загрузка проверяет
редакцию и права заново.

## Доказательства

- 245/245 локальных тестов за 7,942 с, включая 13 новых проверок PDF.
- HTTP: ${http.passed}/${http.total}. Замена байтов выполнялась у одной учебной записи;
  перенос, удаление и повторение цитаты проверены на отдельных PDF.
- После настоящего рестарта API: ${post.passed}/${post.total}; страницы, исходные байты,
  устаревшая редакция и HTML для двух профилей/без сессии.
- Четыре TS/TSX-файла прошли проверку синтаксиса. Next.js скомпилировал маршруты
  и страницу; они проверены реальными запросами и браузером. Полная проверка
  типов всего архивного проекта не заявляется.
- В браузере проверены обычный PDF, скан, масштаб 150%, соседняя страница,
  возврат к цитате, отказ старого адреса и новое положение на странице 3.
  На этой проверке браузер не зарегистрировал ошибок.
- ${integrity.originals.length}/${integrity.originals.length} оригиналов Downloads и ${integrity.copies.length}/${integrity.copies.length} подготовленных копий сохранили SHA-256.
- Все 5 учебных PDF и их заранее записанные ожидания сохранили контрольные суммы.

Сценарии и PDF зафиксированы до основной реализации. Они являются локальной
регрессионной проверкой, а не независимой оценкой на неизвестном корпусе.
Первый HTTP-прогон также дал 14/14; после добавления совместимости старых
ссылок выполнен повторный прогон ${httpName}, результат 14/14.
Отдельный тест подтверждает, что повторное чтение скана использует кэш и не
запускает отрисовку/OCR. В общий журнал тестов намеренно попадают сообщения
проверок восстановления повреждённого хранилища; общий результат OK.

Первая подготовка координат для ранее загруженных PDF заняла больше времени,
чем ожидал скрипт запуска, и он сообщил о неготовности индекса. Процесс не
остановился; обработка завершилась в фоне. Повторный запуск с готовыми данными
прошёл штатно. В конце: ${status.catalogDocuments} документов / ${status.indexedSources} источников /
${status.indexedPassages} фрагмент. Индекс восстановлен с диска за ${status.restoreMs} мс.
Общий чат 8000 не участвует в этом стенде. Новая длительная нагрузочная
проверка в данном этапе не выполнялась; прежние цифры сюда не переносятся.

## Границы и дальнейшая работа

Подсветка ставится только при одном полном совпадении последовательности слов.
Сомнительные OCR-цифры не принимаются автоматически. Сложные таблицы, сноски,
переносы и исправленный OCR-текст могут не совпасть буквально с потоком слов:
тогда остаются текст цитаты и переход к полному исходнику, без выдуманной рамки.
Обрабатываются PDF до 15 МБ и 40 страниц; неполное извлечение не выдаётся за
полный проверенный источник. Для произвольного плохого скана точность не гарантируется.

При переносе не выполняется молчаливое перенаправление старой ссылки в новую
редакцию: пользователь повторяет вопрос. Это не позволяет открыть похожий,
но уже другой раздел. По завершении проверок базовый учебный PDF восстановлен;
две новые учебные записи доступны профилю Б и администратору стенда.

Далее по плану: привязка составных ответов к отдельным ячейкам таблицы и сноскам,
включая переходы между страницами; затем приёмка на актуальных настоящих
регламентах и сканах. Архивная товарная выгрузка 18.06.2026 не объявлена текущей.

## Повторение и артефакты

План приёмки: evaluation/closed_search/PDF_FOCUS_ACCEPTANCE_2026_10_30.md.
Фикстуры: evaluation/closed_search/fixtures/pdf-focus/spec.json и 5 PDF.
Локально: .venv/Scripts/python.exe -m unittest discover -s tests -p "test_site_search*.py".
HTTP: node evaluation/closed_search/pdf_focus_http.cjs.
После рестарта: node evaluation/closed_search/pdf_focus_postcheck.cjs ${httpName}.
Сервисы: node evaluation/closed_search/verify_lab.cjs --closed-only.

Отчёты в .runtime/closed-search-lab-20261028:
${httpName}; ${postName}; pdf-focus-services.json; pdf-focus-syntax.json;
pdf-focus-unittest.log; pdf-focus-integrity.json.
Снимки: ${screenshots.map(name => 'pdf-focus-' + name + '-proof.png').join(', ')}.
Полные листинги: ${codeName}.
`;
    const entry = `## 2026-10-30 — страница PDF, подсветка и перенос разделов

Выполнено: фоновое извлечение координат текстовых PDF/сканов, кэш и SQLite;
открытие страницы с жёлтой подсветкой, масштаб и возврат к цитате. Старые
ссылки неизменённой редакции сохранены. Новая редакция блокирует старые адреса;
перенос даёт новую страницу, удаление — отсутствие ответа. Повторы и
неуверенная геометрия не превращаются в произвольную подсветку.

Проверено: 245/245 локальных тестов, HTTP 14/14, после рестарта 8/8; браузер —
PDF, скан, масштаб, соседняя страница, перенос 2 → 3 и устаревшая ссылка.
128 документов / 211 источников / 2 871 фрагмент. Оригиналы и 42 копии сохранены.
Добавлен pdfplumber==0.11.9. Первая фоновая подготовка превысила ожидание
скрипта запуска, но завершилась; тёплый рестарт штатный. Рабочая LMS не менялась.

Отчёт: ${reportName}. Полные листинги: ${codeName}.
Далее: точная привязка ячеек сложных таблиц и сносок к страницам PDF;
затем проверка на актуальных настоящих регламентах. Ниже история этапов.`;
    const siteEntry = `## 2026-10-30 — исходная страница PDF с подсветкой

Только изолированный стенд 3040 / API 8001. Страница фрагмента показывает
исходный PDF на нужной странице. Защищённый /api/closed-search-pdf повторно
проверяет сессию, права, редакцию и SHA-256. Добавлены подсветка, масштаб и
возврат к цитате; прежний просмотр по dataUrl сохранён. Две новые учебные
записи доступны Б/администратору. После испытаний восстановлена базовая версия.

Проверено: HTTP 14/14, после реального рестарта 8/8; в браузере текстовый PDF,
скан, увеличение и перенос строки со страницы 2 на 3. 128 документов.
Ядру требуется pdfplumber==0.11.9; зависимости сайта не менялись. Координаты
готовятся фоном, первичная обработка может выйти за ожидание скрипта запуска.
Публикация не выполнялась. Отчёт: C:/Users/KROLL/Desktop/Ai/${reportName}.
Далее — ячейки таблиц и сноски, затем проверка актуальных настоящих документов.`;
    const writes = [{ file: path.join(root, reportName), text: report }];
    for (const name of ['CLOSED_SEARCH_PLAN_2026_10_28.md', 'README.md', 'PROGRESS_LOG.md', 'DECISIONS.md']) {
        writes.push(await prepend(path.join(root, name), entry));
    }
    for (const name of ['PROJECT_CONTEXT.md', 'TASKS.md', 'HANDOFF.md', 'DEPLOYMENT.md']) {
        writes.push(await prepend(path.join(site, name), siteEntry));
    }
    const files = ['requirements.txt', 'ai_core/site_pdf_locations.py', 'ai_core/site_search_fragments.py',
        'ai_core/site_search.py', 'ai_core/site_search_api.py', 'ai_core/site_search_store.py', 'ai_core/site_search_runtime.py',
        'tests/test_site_search_pdf_focus.py', 'evaluation/closed_search/pdf_focus_assets.py',
        'evaluation/closed_search/pdf_focus_http.cjs', 'evaluation/closed_search/pdf_focus_postcheck.cjs',
        'evaluation/closed_search/verify_lab.cjs', 'evaluation/closed_search/finalize_pdf_focus.cjs',
        ...['app/lib/vatesFragment.ts', 'app/api/closed-search-pdf/route.ts', 'app/components/PdfDocumentViewer.tsx',
            'app/closed-search-fragment/page.tsx'].map(file => '.runtime/closed-search-lab-20261028/tea-app-main/' + file)];
    let snapshot = '# Полные листинги PDF-подсветки — 30.10.2026\n\n';
    for (const file of files) {
        const bytes = await fs.readFile(path.join(root, file)), digest = sha(bytes);
        integrity.code.push({ file, sha256: digest, bytes: bytes.length });
        const language = file.endsWith('.py') ? 'python' : file.endsWith('.tsx') ? 'tsx'
            : file.endsWith('.ts') ? 'typescript' : file.endsWith('.cjs') ? 'javascript' : 'text';
        snapshot += '\n## ' + file + '\n\nSHA-256: ' + digest + '\n\n````' + language + '\n' + bytes.toString('utf8') + '\n````\n';
    }
    writes.push({ file: path.join(root, codeName), text: snapshot });
    writes.push({ file: path.join(lab, 'pdf-focus-integrity.json'), text: JSON.stringify(integrity, null, 2) });
    for (const output of writes) await fs.writeFile(output.file, output.text, 'utf8');
    console.log(JSON.stringify({ report: reportName, code: codeName, files: files.length,
        originals: integrity.originals.length, copies: integrity.copies.length, fixtures: integrity.fixtures.length, allUnchanged: true }));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## .runtime/closed-search-lab-20261028/tea-app-main/app/lib/vatesFragment.ts

SHA-256: 04d0f6da2cbda9ddf3c6e7bb6b5c9f44476f3dd7544bc3b389563e6fdd3202fe

````typescript
import { createHash } from 'node:crypto';
import { readDataValue } from '@/app/lib/storage/dataStore';
import { isHiddenSystemUser, type SessionUser } from '@/app/lib/serverAuth';
import { canReadClosedRecord } from '@/app/lib/vatesClosedSearch';

type Entry = Record<string, unknown>;
export type FragmentRequest = { source?: string; revision?: string; fragment?: string };
export type PdfFocus = { sha256: string; pageCount: number; initialPage: number;
    hits: Array<{ page: number; rects: number[][] }>; url: string };
export type FragmentResult = {
    status: 'ready' | 'not_found' | 'changed' | 'processing' | 'unavailable';
    message?: string;
    title?: string;
    heading?: string;
    text?: string;
    kind?: string;
    sourceUrl?: string;
    pdf?: PdfFocus;
    pdfMessage?: string;
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
        const location = result.pdf_location;
        let pdf: PdfFocus | undefined;
        if (location?.status === 'located' && /^[a-f0-9]{64}$/.test(location.sha256)
            && Number.isInteger(location.page_count) && location.page_count > 0
            && Number.isInteger(location.initial_page) && location.initial_page > 0
            && Array.isArray(location.hits) && location.hits.length > 0 && location.hits.length <= 40
            && location.hits.every((hit: { page: number; rects: number[][] }) => Number.isInteger(hit.page)
                && hit.page >= 1 && hit.page <= location.page_count && Array.isArray(hit.rects) && hit.rects.length > 0
                && hit.rects.length <= 200 && hit.rects.every(box => Array.isArray(box) && box.length === 4
                    && box.every(value => Number.isFinite(value) && value >= 0 && value <= 1) && box[0] < box[2] && box[1] < box[3]))) {
            const params = new URLSearchParams({ source, revision, fragment });
            pdf = { sha256: location.sha256, pageCount: location.page_count, initialPage: location.initial_page,
                hits: location.hits, url: '/api/closed-search-pdf?' + params.toString() };
        }
        return { status: 'ready', title: result.title, heading: result.heading, text: result.text,
            kind: result.kind === 'product' ? 'product' : 'fragment', sourceUrl: url.pathname + url.search + url.hash,
            ...(pdf ? { pdf } : location ? { pdfMessage: location.status === 'ambiguous'
                ? 'Этот фрагмент встречается в PDF несколько раз. Откройте исходный документ, чтобы выбрать нужное место.'
                : 'Точное место в PDF определить не удалось. Ниже доступен исходный документ.' } : {}) };
    } catch {
        console.error('closed_fragment_unavailable');
        return { status: 'unavailable', message: 'Не удалось открыть материал. Попробуйте позже.' };
    }
}

/** Байты выдаются только после новой проверки прав, редакции и контрольной суммы. */
export async function loadVatesPdf(input: FragmentRequest, user: SessionUser) {
    const result = await resolveVatesFragment(input, user);
    if (result.status !== 'ready' || !result.pdf || !input.source?.startsWith('document:')) return { result };
    const id = input.source.slice('document:'.length);
    const matches = rows(await readDataValue('tea_hub_urgent_files_v1', [])).filter(item => item.id === id);
    if (matches.length !== 1 || !canReadClosedRecord(matches[0], user)) return { result: unavailable() };
    const item = matches[0];
    const data = item.data || await readDataValue(`file_data_${id}`, '');
    if (typeof data !== 'string' || revisionOf([item, data]) !== input.revision) return { result: changed() };
    const separator = data.indexOf(';base64,');
    if (separator < 0) return { result: unavailable() };
    const bytes = Buffer.from(data.slice(separator + 8), 'base64');
    if (bytes.length > 15 * 1024 * 1024 || bytes.subarray(0, 5).toString() !== '%PDF-'
        || createHash('sha256').update(bytes).digest('hex') !== result.pdf.sha256) return { result: changed() };
    const latest = await currentRevision(input.source, user);
    if (!latest) return { result: unavailable() };
    if (latest !== input.revision) return { result: changed() };
    return { result, bytes };
}

````

## .runtime/closed-search-lab-20261028/tea-app-main/app/api/closed-search-pdf/route.ts

SHA-256: d73c5a551dd5dd67623abccf79b3bf25a2d3b0772947c3818b024d5454959c17

````typescript
import { NextResponse } from 'next/server';
import { getSessionFromCookies } from '@/app/lib/serverAuth';
import { loadVatesPdf } from '@/app/lib/vatesFragment';
import { assertRateLimit, getClientIdentifier, securityErrorResponse } from '@/app/lib/serverSecurity';

export const dynamic = 'force-dynamic';
const headers = { 'Cache-Control': 'private, no-store', Vary: 'Cookie', 'X-Content-Type-Options': 'nosniff' };

export async function GET(request: Request) {
    try {
        const user = await getSessionFromCookies();
        if (!user) return NextResponse.json({ message: 'Требуется вход в систему.' }, { status: 401, headers });
        assertRateLimit('closed-pdf', getClientIdentifier(request, user.id), 80, 5 * 60 * 1000);
        const params = new URL(request.url).searchParams;
        const { result, bytes } = await loadVatesPdf({ source: params.get('source') || '',
            revision: params.get('revision') || '', fragment: params.get('fragment') || '' }, user);
        if (!bytes) return NextResponse.json({ message: result.message || 'PDF недоступен.' }, {
            status: result.status === 'changed' ? 409 : ['unavailable', 'processing'].includes(result.status) ? 503 : 404, headers });
        return new Response(new Uint8Array(bytes), { headers: { ...headers, 'Content-Type': 'application/pdf',
            'Content-Disposition': 'inline; filename="source.pdf"' } });
    } catch (error) {
        const security = securityErrorResponse(error);
        if (security) return security;
        console.error('closed_pdf_unavailable');
        return NextResponse.json({ message: 'Не удалось открыть PDF. Попробуйте позже.' }, { status: 503, headers });
    }
}

````

## .runtime/closed-search-lab-20261028/tea-app-main/app/components/PdfDocumentViewer.tsx

SHA-256: 91400ab798db6d86086313b2f2af693348135a6f0131c52adfada5ce92e4248e

````tsx
'use client';

import { useEffect, useRef, useState } from 'react';
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import styles from './PdfDocumentViewer.module.css';

/** Показывает страницу локально: встроенный PDF-плагин браузера не нужен. */
export default function PdfDocumentViewer({ dataUrl, sourceUrl, title, initialPage = 1, highlights }: {
    dataUrl?: string; sourceUrl?: string; title: string; initialPage?: number;
    highlights?: Array<{ page: number; rects: number[][] }>;
}) {
    const hostRef = useRef<HTMLDivElement>(null);
    const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
    const [pageNumber, setPageNumber] = useState(1);
    const [width, setWidth] = useState(700);
    const [zoom, setZoom] = useState(1);
    const [attempt, setAttempt] = useState(0);
    const [busy, setBusy] = useState(true);
    const [error, setError] = useState('');

    useEffect(() => {
        let disposed = false;
        let task: PDFDocumentLoadingTask | undefined;
        hostRef.current?.replaceChildren();

        const load = async () => {
            try {
                // Импорт выполняется только в браузере; закрытый файл не уходит на внешний сервис.
                const pdf = await import('pdfjs-dist');
                if (disposed) return;
                setDocument(null);
                setPageNumber(initialPage);
                setBusy(true);
                setError('');
                pdf.GlobalWorkerOptions.workerSrc = '/pdfjs/5.4.296/pdf.worker.min.mjs';
                let bytes: Uint8Array;
                if (sourceUrl) {
                    // Закрытый маршрут сверяет права и редакцию заново при загрузке байтов.
                    const response = await fetch(sourceUrl, { cache: 'no-store', credentials: 'same-origin' });
                    if (!response.ok) throw new Error(response.status === 409 ? 'pdf_edition_changed' : 'pdf_unavailable');
                    bytes = new Uint8Array(await response.arrayBuffer());
                } else {
                    const separator = (dataUrl || '').indexOf(',');
                    if (!dataUrl || separator < 0 || !/^data:[^,]*;base64$/i.test(dataUrl.slice(0, separator))) {
                        throw new Error('invalid_pdf_data');
                    }
                    bytes = Uint8Array.from(atob(dataUrl.slice(separator + 1)), (value) => value.charCodeAt(0));
                }
                task = pdf.getDocument({
                    data: bytes,
                    cMapUrl: '/pdfjs/5.4.296/cmaps/',
                    cMapPacked: true,
                    standardFontDataUrl: '/pdfjs/5.4.296/standard_fonts/',
                    wasmUrl: '/pdfjs/5.4.296/wasm/',
                    isEvalSupported: false,
                    useWasm: false,
                });
                const loaded = await task.promise;
                if (!disposed) {
                    if (!Number.isInteger(initialPage) || initialPage < 1 || initialPage > loaded.numPages) throw new Error('pdf_page_missing');
                    setDocument(loaded);
                }
            } catch (cause) {
                if (disposed) return;
                setBusy(false);
                setError(cause instanceof Error && cause.message === 'pdf_edition_changed'
                    ? 'Документ обновлён. Повторите вопрос, чтобы открыть актуальную редакцию.'
                    : cause instanceof Error && cause.name === 'PasswordException'
                    ? 'Документ защищён паролем. Скачайте его и откройте в программе для PDF.'
                    : 'Не удалось показать PDF. Попробуйте ещё раз или скачайте файл.');
            }
        };
        void load();
        return () => {
            disposed = true;
            // Закрытие окна освобождает обработчик и память документа, включая незавершённую загрузку.
            if (task) void task.destroy().catch(() => undefined);
        };
    }, [dataUrl, sourceUrl, initialPage, attempt]);

    useEffect(() => {
        const host = hostRef.current;
        if (!host) return;
        const observer = new ResizeObserver(([entry]) => {
            if (entry.contentRect.width > 0) setWidth(Math.floor(entry.contentRect.width));
        });
        observer.observe(host);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        if (!document) return;
        let disposed = false;
        let renderTask: RenderTask | undefined;
        hostRef.current?.replaceChildren();
        const render = async () => {
            try {
                const page = await document.getPage(pageNumber);
                if (disposed) return;
                setBusy(true);
                setError('');
                const natural = page.getViewport({ scale: 1 });
                const viewport = page.getViewport({ scale: Math.max(200, width - 24) / natural.width * zoom });
                // Ограничиваем размер полотна, чтобы большой скан не занял всю память вкладки.
                const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(8_000_000 / (viewport.width * viewport.height)));
                const canvas = window.document.createElement('canvas');
                canvas.width = Math.max(1, Math.floor(viewport.width * ratio));
                canvas.height = Math.max(1, Math.floor(viewport.height * ratio));
                canvas.style.width = `${viewport.width}px`;
                canvas.style.height = `${viewport.height}px`;
                canvas.style.background = '#fff';
                canvas.style.display = 'block';
                canvas.style.margin = '0 auto';
                canvas.setAttribute('role', 'img');
                canvas.setAttribute('aria-label', `${title}, страница ${pageNumber}`);
                renderTask = page.render({ canvas, viewport, transform: [ratio, 0, 0, ratio, 0, 0] });
                await renderTask.promise;
                if (!disposed) {
                    const boxes = highlights?.find(hit => hit.page === pageNumber)?.rects || [];
                    const context = canvas.getContext('2d');
                    if (context && boxes.length) {
                        // Нормированные координаты масштабируются вместе с PDF и DPI экрана.
                        context.save();
                        context.scale(ratio, ratio);
                        context.fillStyle = 'rgba(255, 202, 40, 0.35)';
                        context.strokeStyle = '#b77d00';
                        context.lineWidth = 1;
                        for (const [left, top, right, bottom] of boxes) {
                            context.fillRect(left * viewport.width, top * viewport.height,
                                (right - left) * viewport.width, (bottom - top) * viewport.height);
                            context.strokeRect(left * viewport.width, top * viewport.height,
                                (right - left) * viewport.width, (bottom - top) * viewport.height);
                        }
                        context.restore();
                    }
                    hostRef.current?.replaceChildren(canvas);
                    if (boxes.length && hostRef.current) hostRef.current.scrollTop = Math.max(0, boxes[0][1] * viewport.height - 80);
                    setBusy(false);
                }
            } catch {
                if (!disposed) {
                    setBusy(false);
                    setError('Не удалось показать эту страницу. Попробуйте ещё раз или скачайте файл.');
                }
            }
        };
        void render();
        return () => {
            disposed = true;
            renderTask?.cancel();
        };
    }, [document, pageNumber, width, zoom, title, highlights]);

    return (
        <section className={styles.viewer} aria-label="Просмотр PDF" style={{ width: '100%', height: '100%', minWidth: 0, display: 'flex', flexDirection: 'column', color: 'var(--app-text)', alignSelf: 'stretch' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap', gap: 12, padding: 12 }}>
                <button type="button" disabled={!document || busy || pageNumber <= 1} onClick={() => setPageNumber((page) => page - 1)}>Предыдущая страница</button>
                <span aria-live="polite">{document ? `Страница ${pageNumber} из ${document.numPages}` : error ? 'Предпросмотр недоступен' : 'Открытие PDF'}</span>
                <button type="button" disabled={!document || busy || pageNumber >= document.numPages} onClick={() => setPageNumber((page) => page + 1)}>Следующая страница</button>
                {highlights?.length ? <button type="button" disabled={!document || busy} onClick={() => setPageNumber(initialPage)}>К найденному фрагменту</button> : null}
                <label>Масштаб <select aria-label="Масштаб PDF" value={zoom} onChange={(event) => setZoom(Number(event.target.value))}>
                    <option value={1}>По ширине</option><option value={1.5}>150%</option><option value={2}>200%</option>
                </select></label>
            </div>
            {highlights?.length ? <p role="status" style={{ margin: '0 12px 8px', textAlign: 'center' }}>
                {highlights.some(hit => hit.page === pageNumber) ? 'Найденный фрагмент выделен жёлтым.' : `Найденный фрагмент: ${highlights.map(hit => 'стр. ' + hit.page).join(', ')}.`}
            </p> : null}
            {busy && <p role="status" style={{ textAlign: 'center' }}>Подготовка страницы…</p>}
            {error && <div role="alert" style={{ padding: 20, textAlign: 'center' }}>{error} <button type="button" onClick={() => setAttempt((value) => value + 1)}>Повторить</button></div>}
            <div ref={hostRef} aria-busy={busy} style={{ width: '100%', overflow: 'auto', flex: 1, minHeight: 0, paddingBottom: 12 }} />
        </section>
    );
}

````

## .runtime/closed-search-lab-20261028/tea-app-main/app/closed-search-fragment/page.tsx

SHA-256: 31b316392c71bca389637b10514df5e7a9ab2841a33f839570373a8565b3ef43

````tsx
import Link from 'next/link';
import PdfDocumentViewer from '@/app/components/PdfDocumentViewer';
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
            {result.pdf ? <div style={{ height: 640, margin: '20px 0', border: '1px solid #cbdadc', borderRadius: 12, overflow: 'hidden' }}>
                <PdfDocumentViewer sourceUrl={result.pdf.url} title={result.title || 'Документ'}
                    initialPage={result.pdf.initialPage} highlights={result.pdf.hits} />
            </div> : null}
            {result.pdfMessage ? <p>{result.pdfMessage}</p> : null}
            <Link href={result.sourceUrl || '/tasks?tab=docs'}>Открыть исходный материал целиком</Link>
        </article> : <section role="status" style={{ padding: 28, background: '#fff', borderRadius: 20 }}>
            <h1 style={{ fontSize: 24 }}>{result.status === 'changed' ? 'Документ обновлён' : 'Материал недоступен'}</h1>
            <p>{result.message}</p>
        </section>}
    </main>;
}

````
