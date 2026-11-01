# Полные исходники изменения v6 — 31.10.2026

Файлы приведены целиком, без пропусков. Основной проект: C:/Users/KROLL/Desktop/Ai. В локальных интеграционных сборках ai_core находится внутри services/vates-ai.

## ai_core/site_pdf_manual.py

````python
"""Таблицы параметров в руководствах с текстовым слоем PDF.

Связи определяются рамками ячеек, а не названием файла или вопросами проверки.
Объединённая ячейка принадлежит строке только при доказанном геометрическом
пересечении. Неоднозначные таблицы сохраняются, но не становятся фактами.
"""
from __future__ import annotations

import re
from pathlib import Path
import pdfplumber

from .site_document_cache import MAX_PDF_PAGES, MAX_PDF_CHARACTERS
from .site_pdf_structure import LOCAL, PROFILE, compact, in_box, lines, numbered_paragraphs

CAPTION = re.compile(r'(Продолжение\s+)?[Тт]аблиц[аы]\s+(\d+(?:\.\d+)+)')
STARS = re.compile(r'\*{1,8}')


def cell_text(words, box):
    return compact(' '.join(row['text'] for row in lines([w for w in words if in_box(w, box)])))


def parameter_columns(table, words):
    """Признаём только однозначный заголовок «Наименование | Значение»."""
    if not table.rows:
        return None
    header = [box for box in table.rows[0].cells if box]
    if len(header) != 2:
        return None
    names = [cell_text(words, box).casefold() for box in header]
    if names != ['наименование', 'значение']:
        return None
    boundaries = sorted({round(box[0], 2) for box in table.cells} | {round(box[2], 2) for box in table.cells})
    if len(boundaries) not in (3, 4):
        return None
    return boundaries


def recognizes_manual(document):
    # Два подтверждения защищают обычные документы от случайного выбора
    # профиля. Наличие картинки само по себе не отменяет хороший текстовый слой.
    found = 0
    for page in document.pages[:16]:
        words = page.extract_words(x_tolerance=1, y_tolerance=2)
        if not CAPTION.search(' '.join(w['text'] for w in words)):
            continue
        for table in page.find_tables():
            if parameter_columns(table, words):
                found += 1
        if found >= 2:
            return True
    return False


def owner(cells, x, y):
    """None в pdfplumber может означать colspan или rowspan, а не пустоту."""
    matching = [box for box in cells if box[0] < x < box[2] and box[1] < y < box[3]]
    return matching[0] if len(matching) == 1 else None


def align_values(key_words, value_words):
    """Возвращает пары из одной ячейки; многозначные строки проверяет по высоте.

    Например, общая подпись «Масса прибора:» относится к двум вариантам.
    Перенос внутри подписи не должен создавать третье значение.
    """
    keys, values = lines(key_words), lines(value_words)
    if not keys or not values:
        return None
    if len(values) == 1:
        return [(compact(' '.join(row['text'] for row in keys)), values[0]['text'])]
    # Обычное длинное значение (список скоростей) тоже переносится по строкам.
    # Без явной вводной строки с двоеточием не пытаемся делить его на варианты.
    colon = next((i for i, row in enumerate(keys) if row['text'].endswith(':')), None)
    if colon is None:
        return [(compact(' '.join(r['text'] for r in keys)), compact(' '.join(r['text'] for r in values)))]
    prefix = compact(' '.join(row['text'] for row in keys[:colon + 1])).rstrip(':')
    labels = keys[colon + 1:]
    if len(labels) < len(values):
        return None
    centers = [(row['top'] + row['bottom']) / 2 for row in values]
    grouped = [[] for _ in values]
    for row in labels:
        center = (row['top'] + row['bottom']) / 2
        distances = [abs(center - value) for value in centers]
        best = min(distances)
        if distances.count(best) != 1 or best > 16:
            return None
        grouped[distances.index(best)].append(row['text'])
    if any(not rows for rows in grouped):
        return None
    return [(prefix + ' — ' + compact(' '.join(rows)), value['text']) for rows, value in zip(grouped, values)]


def read_notes(words, box, page):
    """Определения звёздочек находятся только внутри общей ячейки примечаний."""
    result, current, text, source_rows = {}, None, [], []
    def save():
        if current and text:
            bounds = (min(row['x0'] for row in source_rows), min(row['top'] for row in source_rows),
                      max(row['x1'] for row in source_rows), max(row['bottom'] for row in source_rows))
            result.setdefault(current, []).append({'text': compact(' '.join(text)), 'page': page, 'box': bounds})
    for row in lines([word for word in words if in_box(word, box)]):
        match = re.match(r'^(\*{1,8})(?!\*)\s*(.+)', row['text'])
        if match:
            save()
            current, text = len(match[1]), [match[2]]
            source_rows = [row]
        elif current:
            text.append(row['text'])
            source_rows.append(row)
    save()
    return result


def text_boxes(words):
    return [(row['x0'], row['top'], row['x1'], row['bottom']) for row in lines(words)]


def value_boxes(key_words, value_words, index, count):
    """Подсветка одного варианта внутри многострочной объединённой ячейки."""
    if count == 1:
        return text_boxes(key_words) + text_boxes(value_words)
    keys, values = lines(key_words), lines(value_words)
    colon = next(i for i, row in enumerate(keys) if row['text'].endswith(':'))
    centers = [(row['top'] + row['bottom']) / 2 for row in values]
    selected = keys[:colon + 1] + [row for row in keys[colon + 1:]
                                  if min(range(len(centers)), key=lambda i: abs((row['top'] + row['bottom']) / 2 - centers[i])) == index]
    selected.append(values[index])
    return [(row['x0'], row['top'], row['x1'], row['bottom']) for row in selected]


def bind_notes(text, definitions):
    """Не подменяем отсутствующую/дублирующуюся сноску чужим условием."""
    missing, used = False, []
    def replace(match):
        nonlocal missing
        notes = definitions.get(len(match[0]), [])
        if len(notes) != 1:
            missing = True
            return match[0]
        note = notes[0]
        if note not in used:
            used.append(note)
        return ' (' + note['text'].rstrip('.') + ')'
    return STARS.sub(replace, text), missing, used


def symbol_legend(page, body):
    """Узкий случай отдельного рисунка алфавита индикатора.

    Заголовок должен быть настоящим текстом PDF, без других абзацев, а всё
    изображение — находиться под ним в верхней части листа. Другой нечитаемый
    лист сохраняет общий запрет: на нём могут быть обязательные условия.
    """
    title = compact(' '.join(row['text'] for row in lines(body)))
    return (bool(re.fullmatch(r'Соответствие символов .+ буквам .+ алфавита', title, re.I))
            and len(page.images) == 1 and page.images[0]['bottom'] < page.height * .35)


def read_manual(document, scan_reader=None, progress=None):
    """Один и тот же результат используется индексом и подсветкой PDF."""
    blocks, chains, last_tables = [], {}, {}
    total = len(document.pages)
    for number, page in enumerate(document.pages[:MAX_PDF_PAGES], 1):
        if progress:
            progress({'pages_read': number - 1, 'pages_total': total})
        words = page.extract_words(x_tolerance=1, y_tolerance=2)
        # Колонтитулы вне рабочей области. Номер страницы не становится числом
        # параметра; текст внутри таблиц проверяется отдельно по рамке.
        body = [w for w in words if w['top'] > 45 and w['bottom'] < page.height - 35]
        if len(words) < 8:
            if symbol_legend(page, body):
                blocks.append({'heading': f'Страница {number} — обозначения символов',
                               'text': LOCAL + '\n' + compact(' '.join(w['text'] for w in body)),
                               'kind': 'paragraph', 'page': number, 'words': body})
                continue
            scanned = scan_reader(number) if scan_reader else ''
            blocks.append({'heading': f'Страница {number}', 'text': LOCAL + '\n' + scanned,
                           'kind': 'paragraph', 'page': number, 'words': []})
            continue
        tables = page.find_tables()
        removed = set()
        for table in tables:
            cells = table.cells
            inside = [w for w in body if in_box(w, table.bbox)]
            removed.update(id(w) for w in inside)
            captions = [row for row in lines(body) if table.bbox[1] - 34 < row['top'] < table.bbox[1]
                        and CAPTION.search(row['text'])]
            caption = captions[-1] if captions else None
            match = CAPTION.search(caption['text']) if caption else None
            if caption:
                removed.update(id(w) for w in caption['words'])
            label = caption['text'] if caption else 'Таблица'
            heading = f'Страница {number} — ' + label
            columns = parameter_columns(table, words)
            if not columns or not match:
                blocks.append({'heading': heading, 'text': LOCAL + '\n' + cell_text(body, table.bbox),
                               'kind': 'table', 'page': number, 'rects': [table.bbox], 'row_rects': []})
                continue
            identifier = match[2]
            prior = last_tables.get(identifier)
            # Продолжение имеет то же явное обозначение и соседнюю страницу.
            # Одноимённая таблица из другого раздела не получает старые сноски.
            if match[1] and prior and prior['page'] == number - 1:
                chain = prior['chain']
            else:
                chain = (number, identifier)
            last_tables[identifier] = {'page': number, 'chain': chain}
            definitions = chains.setdefault(chain, {})
            note_boxes = [box for box in cells if box[2] - box[0] > .95 * (table.bbox[2] - table.bbox[0])
                          and re.match(r'ПРИМЕЧАНИ', cell_text(body, box), re.I)]
            for box in note_boxes:
                for key, values in read_notes(body, box, number).items():
                    definitions.setdefault(key, []).extend(values)
            seen = set()
            for row in table.rows[1:]:
                boxes = [box for box in row.cells if box]
                if not boxes or any(box in note_boxes for box in boxes):
                    continue
                y = (max(box[1] for box in boxes) + min(box[3] for box in boxes)) / 2
                owned = [owner(cells, (a + b) / 2, y) for a, b in zip(columns, columns[1:])]
                if None in owned:
                    blocks.append({'heading': heading, 'text': LOCAL + '\n' + ' '.join(cell_text(body, box) for box in boxes),
                                   'kind': 'table', 'page': number, 'rects': boxes, 'row_rects': []})
                    continue
                key_box, value_box = owned[-2:]
                pair = (key_box, value_box)
                if pair in seen:
                    continue
                seen.add(pair)
                group = cell_text(body, owned[0]) if len(owned) == 3 and owned[0] != key_box else ''
                key_words = [w for w in inside if in_box(w, key_box)]
                value_words = [w for w in inside if in_box(w, value_box)]
                pairs = align_values(key_words, value_words)
                if not pairs:
                    pairs = [(cell_text(body, key_box), cell_text(body, value_box))]
                    uncertain = True
                else:
                    uncertain = False
                for index, (key, value) in enumerate(pairs):
                    content = (group + '. ' if group else '') + key + ': ' + value + '.'
                    row_rects = value_boxes(key_words, value_words, index, len(pairs))
                    context_words = [w for w in inside if group and in_box(w, owned[0])]
                    context_words += [w for w in inside if any(in_box(w, box) for box in table.rows[0].cells if box)]
                    blocks.append({'heading': heading, 'text': (LOCAL + '\n' if uncertain else '') + content,
                                   'kind': 'table', 'page': number, 'chain': chain,
                                   'rects': row_rects + text_boxes(context_words),
                                   'row_rects': row_rects, 'note_rects': []})
        remaining = [word for word in body if id(word) not in removed]
        # Густые схемы не превращаем в связный абзац с ложным порядком чтения.
        paragraphs = []
        numbered_paragraphs(lines(remaining), number, paragraphs)
        if re.search(r'Содержание|\.{6,}', ' '.join(w['text'] for w in remaining)):
            # Оглавление перечисляет темы, но не подтверждает их содержание.
            for paragraph in paragraphs:
                paragraph['text'] = LOCAL + '\n' + paragraph['text']
        if len(page.edges) > 150 and len(tables) == 0:
            for paragraph in paragraphs:
                paragraph['text'] = LOCAL + '\n' + paragraph['text']
        blocks.extend(paragraphs)
    # Только после чтения продолжений известны определения сносок таблицы.
    for block in blocks:
        if 'chain' not in block:
            continue
        text, missing, notes = bind_notes(block['text'], chains[block.pop('chain')])
        block['text'] = (LOCAL + '\n' if missing else '') + text
        block['note_sources'] = notes
        for note in notes:
            if note['page'] == block['page']:
                block['note_rects'].append(note['box'])
                block['rects'].append(note['box'])
    if progress:
        progress({'pages_read': min(total, MAX_PDF_PAGES), 'pages_total': total})
    return blocks


def extract_manual_pdf(file: Path, scan_reader, progress=None):
    with pdfplumber.open(file) as document:
        if not recognizes_manual(document):
            return None
        blocks = read_manual(document, scan_reader, progress)
        text = PROFILE + '\n[PDF_PARAMETER_MANUAL]\n' + '\n\n'.join('# ' + block['heading'] + '\n' + block['text'] for block in blocks)
        complete = len(document.pages) <= MAX_PDF_PAGES and len(text) <= MAX_PDF_CHARACTERS
        if not complete:
            text = '[STRUCTURE_UNCERTAIN]\n' + text[:MAX_PDF_CHARACTERS]
        return text, complete

````

## ai_core/site_documents.py

````python
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
from .site_scan_tables import describe_table, Cell
from .site_document_cache import MAX_PDF_PAGES, MAX_PDF_CHARACTERS, read_checkpoint, write_checkpoint
from .site_pdf_structure import extract_technical_pdf
from .site_pdf_manual import extract_manual_pdf

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".runtime" / "closed-search" / "document-cache"
# Изменение алгоритма извлечения само инвалидирует производный кэш. Иначе
# исправленный OCR мог бы продолжать читать прежний результат тех же байтов PDF.
PARSER_VERSION = "closed-documents-v7-multipage-" + hashlib.sha256(
    Path(__file__).read_bytes() + Path(__file__).with_name('site_scan_tables.py').read_bytes()
    + Path(__file__).with_name('site_scan_layout.py').read_bytes()
    + Path(__file__).with_name('site_scan_quality.py').read_bytes()
    + Path(__file__).with_name('site_document_cache.py').read_bytes()
    + Path(__file__).with_name('site_pdf_structure.py').read_bytes()
    + Path(__file__).with_name('site_pdf_manual.py').read_bytes()
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


def extract_document(data_url: str, name: str, *, progress=None) -> tuple[str, bool]:
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
                if progress:
                    progress(previous.get('progress', {}))
                return text, bool(previous["complete"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    complete = True
    details = {}
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
            def read_scan(number):
                prefix = root / f"technical-{number}"
                _run([_command("pdftoppm"), "-f", str(number), "-l", str(number), "-r", "220", "-png", "-singlefile", str(file), str(prefix)])
                return _scan_text(prefix.with_suffix(".png"), number, [])

            def report(values):
                details.update(values)
                if progress:
                    progress(values)

            structured = extract_technical_pdf(file, CACHE, fingerprint, read_scan, report)
            if structured is None:
                structured = extract_manual_pdf(file, read_scan, report)
            if structured is not None:
                text, complete = structured
            else:
                document = PdfReader(file)
                total = len(document.pages)
                maximum = min(total, MAX_PDF_PAGES)
                complete = total <= MAX_PDF_PAGES
                pages, metadata = [], []
                characters = 0
                details = {'pages_read': 0, 'pages_total': total}
                for page in range(1, maximum + 1):
                    # Callback также останавливает старую редакцию до следующего OCR.
                    if progress:
                        progress(details.copy())
                    checkpoint = CACHE / (fingerprint + '-pages') / (str(page) + '.json')
                    saved = read_checkpoint(checkpoint)
                    if isinstance(saved, dict) and isinstance(saved.get('text'), str) and isinstance(saved.get('metadata'), list):
                        pages.append(saved['text'])
                        metadata.extend([{**table, 'cells': [Cell(**cell) for cell in table['cells']]}
                                         for table in saved['metadata']])
                        details['pages_read'] = page
                        characters += len(saved['text'])
                        if characters > MAX_PDF_CHARACTERS:
                            complete = False
                            break
                        continue
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
                        page_metadata = []
                        page_text = _scan_text(prefix.with_suffix(".png"), page, page_metadata)
                    else:
                        page_metadata = []
                        page_text = _join_visual_lines(page_text)
                    pages.append(page_text)
                    metadata.extend(page_metadata)
                    write_checkpoint(checkpoint, {'text': page_text, 'metadata': [
                        {**table, 'cells': [vars(cell) for cell in table['cells']]} for table in page_metadata]})
                    details['pages_read'] = page
                    characters += len(page_text)
                    if characters > MAX_PDF_CHARACTERS:
                        complete = False
                        break
                if progress:
                    progress(details.copy())
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
    text_limit = MAX_PDF_CHARACTERS if extension == '.pdf' else 200000
    if len(text) > text_limit:
        text, complete = text[:text_limit], False
    if extension == '.pdf' and not complete:
        # За пределами прочитанной части могут находиться обязательные исключения.
        text = '[STRUCTURE_UNCERTAIN]\n' + text
    # Производный кэш приватный; из web/static он не раздаётся.
    content = {"text": text, "complete": complete, "progress": details, "sha256": hashlib.sha256(text.encode()).hexdigest()}
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

````

## ai_core/site_pdf_locations.py

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
from .site_document_cache import MAX_PDF_PAGES, read_checkpoint, write_checkpoint

LOCATION_VERSION = 'pdf-focus-v3-regions-' + hashlib.sha256(Path(__file__).read_bytes()
    + Path(__file__).with_name('site_document_cache.py').read_bytes()
    + Path(__file__).with_name('site_pdf_structure.py').read_bytes()
    + Path(__file__).with_name('site_pdf_manual.py').read_bytes()).hexdigest()[:16]


def tokens(text):
    return re.findall(r'[^\W_]+', text.casefold().replace('ё', 'е'))


def extract_pdf_layout(data_url, *, progress=None):
    # Ленивый импорт разрывает зависимость поискового ядра от OCR-инструментов.
    import pdfplumber
    from .site_documents import CACHE, _command, _run, _ocr
    from .site_scan_tables import read_image, parse_words
    from .site_pdf_structure import recognizes, read_native_page
    from .site_pdf_manual import recognizes_manual, read_manual
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
                if progress:
                    progress({'position_pages': len(saved['layout']['pages']), 'positions_total': saved['layout']['page_count']})
                return saved['layout']
        except (OSError, KeyError, ValueError, TypeError):
            pass
    layout = {'sha256': hashlib.sha256(data).hexdigest(), 'pages': [], 'page_count': 0}
    with tempfile.TemporaryDirectory(prefix='vates-pdf-positions-') as directory:
        file = Path(directory) / 'source.pdf'
        file.write_bytes(data)
        with pdfplumber.open(file) as document:
            layout['page_count'] = len(document.pages)
            structured = recognizes(document)
            manual = not structured and recognizes_manual(document)
            manual_regions = {}
            if manual:
                for region in read_manual(document, progress=progress):
                    manual_regions.setdefault(region['page'], []).append(region)
                    # Сноска может быть на следующей странице. Её координаты
                    # нормируются по размеру той страницы, а не текущей строки.
                    region['extra_rects'] = []
                    for note in region.get('note_sources', []):
                        if note['page'] != region['page']:
                            note_page = document.pages[note['page'] - 1]
                            box = note['box']
                            region['extra_rects'].append({'page': note['page'], 'box': [box[0]/note_page.width, box[1]/note_page.height, box[2]/note_page.width, box[3]/note_page.height]})
            for number, page in enumerate(document.pages[:MAX_PDF_PAGES], 1):
                if progress:
                    progress({'position_pages': number - 1, 'positions_total': layout['page_count']})
                checkpoint = CACHE / ('positions-' + fingerprint + '-pages') / (str(number) + '.json')
                saved_page = read_checkpoint(checkpoint)
                if isinstance(saved_page, dict) and saved_page.get('page') == number and isinstance(saved_page.get('words'), list):
                    layout['pages'].append(saved_page)
                    page.close()
                    continue
                words, regions = [], []
                succeeded = True
                try:
                    native = page.extract_words()
                    raster = any(math.prod(image.get('srcsize', (0, 0))) > 500000 for image in page.images)
                    if len(native) >= 8 and (not raster or manual) and not page.rotation and page.cropbox == page.bbox:
                        for item in native:
                            words.append({'text': item['text'], 'confidence': 100,
                                'box': [item['x0'] / page.width, item['top'] / page.height,
                                        item['x1'] / page.width, item['bottom'] / page.height]})
                        if structured:
                            # Используем тот же разбор страницы, что и извлечение
                            # текста. Привязка не зависит от пользовательского вопроса.
                            raw_regions = []
                            read_native_page(page, number, raw_regions)
                            regions = normalize_regions(raw_regions, page.width, page.height)
                        elif manual:
                            regions = normalize_regions(manual_regions.get(number, []), page.width, page.height)
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
                    words, regions = [], []
                    succeeded = False
                clean = [item for item in words if valid_box(item['box'])]
                prepared = {'page': number, 'words': clean[:12000], 'regions': regions}
                layout['pages'].append(prepared)
                # Ошибку инструмента не сохраняем как успешную контрольную точку.
                if succeeded:
                    write_checkpoint(checkpoint, prepared)
                page.close()
    layout['complete'] = len(layout['pages']) == layout['page_count'] and all(page['words'] for page in layout['pages'])
    if progress:
        progress({'position_pages': len(layout['pages']), 'positions_total': layout['page_count']})
    body = json.dumps(layout, ensure_ascii=False, sort_keys=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=CACHE, suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        json.dump({'layout': layout, 'digest': hashlib.sha256(body.encode()).hexdigest()}, handle, ensure_ascii=False)
    try:
        if layout['complete']:
            temporary.replace(cache)
    finally:
        temporary.unlink(missing_ok=True)
    return layout


def valid_box(box):
    return (isinstance(box, list) and len(box) == 4
            and all(isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1 for value in box)
            and box[0] < box[2] and box[1] < box[3])


def normalize_regions(regions, width, height):
    """Сохраняем геометрию в системе координат, которую использует PDF viewer."""
    def box(value):
        return [value[0]/width, value[1]/height, value[2]/width, value[3]/height]
    result = []
    for region in regions:
        if not region.get('text') or '[LOCAL_STRUCTURE_UNCERTAIN]' in region['text']:
            continue
        prepared = {key: region[key] for key in ('text', 'heading', 'kind', 'page')}
        if region['kind'] == 'paragraph':
            prepared['words'] = [{'text': word['text'], 'confidence': 100,
                                  'box': box([word['x0'],word['top'],word['x1'],word['bottom']])}
                                 for word in region.get('words', [])]
            if not prepared['words'] or not all(valid_box(word['box']) for word in prepared['words']):
                continue
        else:
            for name in ('rects', 'row_rects', 'note_rects'):
                prepared[name] = [box(rect) for rect in region.get(name, [])]
            if not prepared['row_rects'] or not all(valid_box(rect) for rect in prepared['rects']):
                continue
            prepared['extra_rects'] = [item for item in region.get('extra_rects', [])
                                       if isinstance(item.get('page'), int) and valid_box(item.get('box'))]
        result.append(prepared)
    return result


def prepare_structured_lookup(layout):
    """Индекс автоматически извлечённых областей: один раз на редакцию PDF."""
    regions, starts = [], {}
    for page in layout.get('pages', []):
        for raw in page.get('regions', []):
            if raw.get('page') != page.get('page') or raw.get('kind') not in {'paragraph','table'}:
                continue
            sequence = tokens(raw.get('text',''))
            if not sequence or len(sequence) > 12000:
                continue
            index = len(regions)
            region = {**raw, 'sequence': sequence}
            # Для абзаца порядок слов берётся из колонок/строк, а не из общего
            # потока страницы. Числа и отрицания по-прежнему обязаны совпасть.
            if raw['kind'] == 'paragraph':
                region['lookup'] = prepare_pdf_lookup({'pages':[{'page':raw['page'],'words':raw.get('words',[])}]})
            regions.append(region)
            for position, token in enumerate(sequence):
                starts.setdefault(token, []).append((index,position))
    return regions, starts


def rectangle_location(base, selected):
    """Объединяем слова одной строки; соседние колонки не закрашиваем целиком."""
    pages = {}
    for page, box in selected:
        if not isinstance(page,int) or page < 1 or page > base['page_count'] or not valid_box(list(box)):
            return base
        rectangles = pages.setdefault(page, [])
        if list(box) in rectangles:
            continue
        if (rectangles and abs(rectangles[-1][1]-box[1]) < .004
                and abs(rectangles[-1][3]-box[3]) < .004 and 0 <= box[0]-rectangles[-1][2] < .035):
            rectangles[-1] = [rectangles[-1][0],min(rectangles[-1][1],box[1]),box[2],max(rectangles[-1][3],box[3])]
        else:
            rectangles.append(list(box))
    if not pages or sum(map(len,pages.values())) > 200:
        return base
    return {**base,'status':'located','initial_page':min(pages),
            'hits':[{'page':page,'rects':rects} for page,rects in sorted(pages.items())]}


def locate_structured(base, wanted, heading, prepared):
    regions, starts = prepared
    matches = []
    physical = re.search(r'\bстраница\s+(\d+)\b',heading or '',re.I)
    for index, position in starts.get(wanted[0], []):
        region = regions[index]
        if region['sequence'][position:position+len(wanted)] != wanted:
            continue
        if physical and region['page'] != int(physical[1]):
            continue
        if index not in matches:
            matches.append(index)
    exact = [index for index in matches if regions[index]['heading'] == heading]
    if exact:
        matches = exact
    if not matches:
        return None
    if len(matches) != 1:
        return {**base,'status':'ambiguous'}
    region = regions[matches[0]]
    if region['kind'] == 'table':
        # Подписанная строка синтезирована из реальных ячеек и сноски. Не ищем
        # каждое число по всей странице: выделяем именно сохранённую строку.
        if not region.get('row_rects'):
            return base
        selected = [(region['page'], rect) for rect in region.get('rects', [])]
        selected.extend((item['page'], item['box']) for item in region.get('extra_rects', []))
        return rectangle_location(base, selected)
    stream, sequence, word_starts = region['lookup']
    positions = [position for position in word_starts.get(wanted[0],[]) if sequence[position:position+len(wanted)] == wanted]
    if len(positions) != 1:
        return {**base,'status':'ambiguous' if positions else 'unlocated'}
    selected = stream[positions[0]:positions[0]+len(wanted)]
    return rectangle_location(base,[(page,box) for _,page,box in selected])


def prepare_pdf_lookup(layout):
    """Словарь координат строится один раз на документ, а не на каждую цитату."""
    stream = []
    for page in layout.get('pages', []):
        for word in page.get('words', []):
            # Пропуск сомнительного слова не должен склеивать два разных куска.
            confidence = word.get('confidence', 0)
            for token in tokens(word.get('text', '')):
                reliable = confidence >= (80 if any(char.isdigit() for char in token) else 60)
                stream.append((token if reliable else '\x00', page['page'], word['box']))
    sequence = [item[0] for item in stream]
    starts = {}
    for index, value in enumerate(sequence):
        starts.setdefault(value, []).append(index)
    return stream, sequence, starts


def locate_fragment(layout, text, *, lookup=None, structured_lookup=None, heading=None):
    """Только полное последовательное совпадение; повторы не выбираем наугад."""
    if not isinstance(layout, dict):
        return None
    base = {'sha256': layout.get('sha256'), 'page_count': layout.get('page_count'), 'status': 'unlocated'}
    wanted = tokens(text)
    if len(wanted) < 3 or len(wanted) > 1500:
        return base
    prepared = structured_lookup if structured_lookup is not None else prepare_structured_lookup(layout)
    structured = locate_structured(base,wanted,heading,prepared)
    if structured is not None:
        return structured
    stream, sequence, starts = lookup if lookup is not None else prepare_pdf_lookup(layout)
    matches = []
    for index in starts.get(wanted[0], []):
        if sequence[index:index + len(wanted)] == wanted:
            matches.append(index)
            if len(matches) > 1:
                break
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

## ai_core/site_search_manual.py

````python
"""Поиск характеристик в геометрически подтверждённых руководствах.

Словарь связывает способы задать вопрос с названием характеристики.
Значений, названий изделий и ответов проверочного набора в нём нет.
Результат всегда содержит исходную цитату с условиями и редакцией.
"""
from __future__ import annotations
import re

NO_DATA = 'В доступных материалах я не нашёл информации, которая однозначно отвечает на этот вопрос.'

RELATIONS = (
    ('purpose', r'для чего|зачем|назначени', r'предназначен\w* для (?!ознакомления)'),
    ('power', r'потребля|потреблен', r'потребляемая мощность'),
    ('poll', r'опраш|опрос', r'время опроса'),
    ('warmup', r'установлен.*режим|прогрев', r'время установления рабочего режима'),
    ('mass', r'масс|вес', r'масса'),
    ('dimensions', r'габарит|размер', r'габаритные размеры'),
    ('protection', r'степен.*защит|защит.*корпус', r'степень защиты корпуса'),
    ('data_bits', r'бит.*данн', r'количество бит данных'),
    ('protocol', r'протокол', r'протокол обмена'),
    ('temperature', r'температур.*(?:работ|эксплуатац|воздух)|(?:работ|эксплуатац).*температур', r'температура окружающего воздуха'),
    ('humidity', r'влажност', r'(?:относительной )?влажности воздуха'),
    ('lifespan', r'срок.*служ|долговеч', r'срок службы'),
    ('repair', r'ремонт|бесплатн.*гарант', r'бесплатный ремонт|ремонт.*бесплатн'),
    ('warranty', r'гарант', r'гарантийный срок'),
    ('storage', r'хран|склад', r'хранить'),
    ('channels', r'(?:сколько|количеств).*канал', r'количество.*канал|по \w+ независимым канал'),
    ('voltage', r'напряжени', r'напряжени'),
)


def relation(question):
    q = question.casefold().replace('ё', 'е')
    return next(((name, evidence) for name, query, evidence in RELATIONS if re.search(query, q)), None)


def evidence(row):
    passage, quote = row
    return {'id': passage.source['id'], 'revision': passage.source['revision'], 'heading': passage.heading, 'quote': quote}


def variant(question):
    match = re.search(r'\bкорпус\w*\s+[«"]?([А-ЯA-Z]{1,3}\d*)\b|\bв\s+(Щ\d+)\b', question)
    return next((part for part in match.groups() if part), None) if match else None


def accepts_variant(quote, wanted):
    # «кроме корпуса Д» не является характеристикой корпуса Д.
    escaped = re.escape(wanted)
    if re.search(r'кроме корпуса\s+' + escaped + r'\b', quote, re.I):
        return False
    return bool(re.search(r'(?<!\w)' + escaped + r'(?!\w)', quote, re.I))


def manual_quotes(passage):
    if re.search(r'таблиц', passage.heading, re.I):
        return [passage.text]
    # Не разрываем условие «при соблюдении ...» и действие из одного
    # предложения. Явные пункты списка сохраняют своё полное ограничение.
    parts = re.split(r'\s*[•]\s*|(?<=[.!?])\s+(?=[А-ЯЁ])', passage.text)
    return [part.strip() for part in parts if len(part.strip()) > 15]


def search_manual(question, candidates):
    empty = {'status': 'not_found', 'text': NO_DATA, 'links': [], 'evidence': []}
    named = re.findall(r'\b[А-ЯA-Z]{2,}\d+(?:[-.]\d+)*\b', question)
    available_text = ' '.join(p.source['title'] + ' ' + p.heading + ' ' + p.text for p in candidates)
    if any(not re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', available_text, re.I) for name in named):
        # Выбранный файл не даёт права отвечать за другое явно названное изделие.
        return empty
    if re.search(r'сейчас|сегодня|серийн|нашей компании|кто из сотрудников', question, re.I):
        return empty
    selected_relation = relation(question)
    if not selected_relation:
        return empty
    kind, pattern = selected_relation
    wanted = variant(question)
    rows, blocked = [], False
    for passage in candidates:
        for quote in manual_quotes(passage):
            if not re.search(pattern, quote, re.I):
                continue
            if wanted and kind in {'dimensions', 'protection'} and not accepts_variant(quote, wanted):
                continue
            if kind == 'mass':
                if re.search(r'без упаков', question, re.I) and not re.search(r'без упаков', quote, re.I):
                    continue
                if re.search(r'\bс упаков', question, re.I) and not re.search(r'\bс упаков', quote, re.I):
                    continue
            if kind == 'voltage':
                if re.search(r'внешн\w* источник', quote, re.I) and not re.search(r'внешн', question, re.I):
                    # Питание внешнего оборудования не подменяет питание прибора.
                    continue
                if re.search(r'питани', question, re.I) and not re.search(r'питани', quote, re.I):
                    continue
                if re.search(r'диапазон', question, re.I) and not re.search(r'диапазон', quote, re.I):
                    continue
                if re.search(r'номинальн', question, re.I) and not re.search(r'номинальн', quote, re.I):
                    continue
            if kind in {'voltage', 'power'}:
                if re.search(r'постоянн', question, re.I) and not re.search(r'постоянн', quote, re.I):
                    continue
                if re.search(r'переменн', question, re.I) and not re.search(r'переменн', quote, re.I):
                    continue
            if kind == 'protection':
                if re.search(r'спереди|лицев', question, re.I) and not re.search(r'лицев', quote, re.I):
                    continue
                if re.search(r'сзади|задн', question, re.I) and not re.search(r'задн', quote, re.I):
                    continue
            if kind == 'temperature' and not re.search(r'транспорт|хран', question, re.I) and re.search(r'транспорт|хран', passage.heading, re.I):
                continue
            if kind == 'temperature' and re.search(r'хран', question, re.I) and not re.search(r'хран', quote, re.I):
                continue
            if kind == 'temperature' and re.search(r'транспорт|перевоз', question, re.I) and not re.search(r'транспорт|перевоз', quote, re.I):
                continue
            if kind == 'purpose' and re.search(r'руководство|кнопк|разъем|интерфейс|датчик предназначен|предназначен\w* для эксплуатации', quote, re.I):
                continue
            if kind == 'dimensions' and not re.search(r'\d+\s*[×хx]\s*\d+\s*[×хx]\s*\d+', quote):
                # Номер рисунка в подписи не является размером устройства.
                continue
            if kind in {'dimensions', 'mass', 'power', 'poll', 'warmup', 'temperature', 'humidity', 'lifespan', 'warranty', 'voltage', 'protection', 'data_bits'} and not re.search(r'\d', quote):
                continue
            if (passage.structure or {}).get('issue'):
                blocked = True
                continue
            rows.append((passage, quote))
    # Одинаковый факт может повторяться; это не конфликт редакции.
    rows = list({quote: (passage, quote) for passage, quote in rows}.values())
    if not rows:
        return ({'status': 'unavailable', 'text': 'Не удалось проверить все условия этого правила. Обратитесь к администратору для уточнения документа.', 'links': [], 'evidence': []}
                if blocked else empty)
    if kind == 'channels':
        numbers = {}
        words = {'одному': '1', 'двум': '2', 'трем': '3', 'четырем': '4'}
        for row in rows:
            match = re.search(r'каналов\s*:\s*(\d+)|по (\w+) независимым канал', row[1], re.I)
            if match:
                value = match[1] or words.get(match[2].casefold().replace('ё', 'е'))
                if value:
                    numbers[value] = row
        if len(numbers) > 1:
            selected = list(numbers.values())
            return {'status': 'conflict', 'text': 'В руководстве есть противоречие: ' + ' '.join(row[1] for row in selected) + ' Уточните верное значение у администратора.',
                    'links': [], 'evidence': [evidence(row) for row in selected]}
    if len(rows) > 1 and kind in {'mass', 'dimensions', 'protection', 'voltage'}:
        return {'status': 'clarify', 'text': 'Уточните исполнение устройства и нужный вариант характеристики.', 'links': [], 'evidence': []}
    # Таблица параметров сильнее случайного упоминания в тексте настройки.
    rows.sort(key=lambda row: (0 if re.search(r'таблиц', row[0].heading, re.I) else 1, len(row[1])))
    passage, quote = rows[0]
    return {'status': 'answer', 'text': quote, 'evidence': [evidence(rows[0])],
            'links': [{'title': passage.source['title'], 'url': passage.source['url']}], 'context_used': False}

````

## ai_core/site_search_structure.py

````python
"""Структура документа: строки таблицы, локальные ссылки и календарные границы.

Разбор выполняется при индексации. Неизвестная или повреждённая связь не даёт
разрешения использовать основное правило без его ограничения.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone, timedelta
from html.parser import HTMLParser

DATE = r'(?:\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})'
REFERENCE = re.compile(r'\b(?:см\.?|согласно|смотри(?:те)?)\s+раздел(?:у|а|е)?\s+(\d+(?:\.\d+)*)', re.I)
NOTE = re.compile(r'\[\^?([\w-]+)\](?!\()')
DEFINITION = re.compile(r'^\[\^?([\w-]+)\]:\s*(.+)$')
ISSUE_TEXT = 'Не удалось проверить все условия этого правила. Обратитесь к администратору для уточнения документа.'


def iso_day(value):
    return (datetime.strptime(value, '%d.%m.%Y').date() if '.' in value else date.fromisoformat(value)).isoformat()


def question_day(question, now):
    """Явная дата меняет только выбор правила, но никогда не отменяет ACL."""
    expression = re.compile(rf'\b(?:по состоянию на|на дату|на)\s+({DATE})', re.I)
    matches = list(expression.finditer(question))
    try:
        dates = {iso_day(match[1]) for match in matches}
        if len(dates) > 1:
            return question, None, True
        current = datetime.fromtimestamp(now, timezone(timedelta(hours=3))).date().isoformat()
        return expression.sub('', question).strip(), next(iter(dates), current), False
    except ValueError:
        return question, None, True


def window(text):
    """Включительные календарные даты; неопознанный срок не игнорируется."""
    pattern = re.compile(rf'(?im)^\s*(?:правило |порядок )?действует\s+(?:с\s+({DATE}))?(?:\s*по\s+({DATE})(?:\s+включительно)?)?\s*\.?\s*$')
    periods, issue = [], False
    for match in pattern.finditer(text):
        try:
            start, end = (iso_day(value) if value else None for value in match.groups())
            if not start and not end or start and end and start > end:
                raise ValueError('date_range')
            periods.append([start, end])
        except ValueError:
            issue = True
    rest = pattern.sub('', text).strip()
    if re.search(r'(?im)^\s*(?:правило |порядок )?действует\b', rest):
        issue = True
    return rest, periods, issue


def active(structure, day):
    return all((not start or start <= day) and (not end or day <= end)
               for start, end in structure.get('periods', []))


class StructuredHTML(HTMLParser):
    """Сохраняет простые таблицы, заголовки и номера сносок; скрытый текст исключён."""
    OMIT = {'script', 'style', 'nav', 'footer', 'form', 'textarea', 'select', 'button', 'svg', 'iframe', 'head'}
    VOID = {'br', 'hr', 'img', 'input', 'meta', 'link', 'source', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.out, self.rows = [], [], []
        self.row, self.cell = None, None
        self.table_depth, self.bad_table = 0, False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = tag in self.OMIT or 'hidden' in attrs or attrs.get('aria-hidden') == 'true' or bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', attrs.get('style', ''), re.I))
        if tag not in self.VOID:
            self.stack.append((tag, hidden))
        if any(item[1] for item in self.stack):
            return
        if tag == 'table':
            self.table_depth += 1
            self.bad_table |= self.table_depth > 1
        elif tag == 'tr' and self.table_depth:
            self.row = []
        elif tag in {'td', 'th'} and self.table_depth:
            self.cell = []
            self.bad_table |= attrs.get('rowspan', '1') != '1' or attrs.get('colspan', '1') != '1'
        elif tag == 'sup':
            self.write('[')
        elif re.fullmatch('h[1-6]', tag):
            self.write('\n# ')
        elif tag in {'p', 'div', 'li', 'br'}:
            self.write('\n')

    def write(self, value):
        if self.cell is not None:
            self.cell.append(value)
        else:
            self.out.append(value)

    def handle_data(self, data):
        if not any(item[1] for item in self.stack):
            self.write(data)

    def handle_endtag(self, tag):
        if not any(item[1] for item in self.stack):
            if tag in {'td', 'th'} and self.cell is not None:
                if self.row is not None:
                    self.row.append(' '.join(''.join(self.cell).split()).replace('|', '¦'))
                self.cell = None
            elif tag == 'tr' and self.row is not None:
                self.rows.append(self.row)
                self.row = None
            elif tag == 'table':
                self.table_depth -= 1
                if not self.table_depth:
                    if self.bad_table:
                        self.out.append('\n[STRUCTURE_UNCERTAIN]\n')
                    self.out.append(table_markdown(self.rows))
                    self.rows, self.bad_table = [], False
            elif tag == 'sup':
                self.write(']')
            elif tag in {'p', 'div', 'li'} or re.fullmatch('h[1-6]', tag):
                self.write('\n')
        for position in range(len(self.stack) - 1, -1, -1):
            if self.stack[position][0] == tag:
                del self.stack[position:]
                break


def table_markdown(rows):
    if not rows:
        return ''
    lines = ['| ' + ' | '.join(row) + ' |' for row in rows]
    lines.insert(1, '| ' + ' | '.join('---' for _ in rows[0]) + ' |')
    return '\n' + '\n'.join(lines) + '\n'


def normalize_markup(text):
    if re.search(r'<(?:table|h[1-6]|sup)\b', text, re.I):
        parser = StructuredHTML()
        parser.feed(text)
        return ''.join(parser.out)
    return text


def table_blocks(text):
    """Одна строка — один фрагмент с названиями колонок; пустые ячейки не наследуются."""
    lines = text.splitlines()
    blocks, ordinary, position = [], [], 0
    while position < len(lines):
        line = lines[position].strip()
        if line.startswith('|') and position + 1 < len(lines) and re.fullmatch(r'[\s|:\-]+', lines[position + 1]) and '-' in lines[position + 1]:
            if ordinary:
                blocks.append(('\n'.join(ordinary), False))
                ordinary = []
            headers = [cell.strip() for cell in line.strip('|').split('|')]
            invalid = not all(headers) or len(set(headers)) != len(headers)
            position += 2
            while position < len(lines) and lines[position].strip().startswith('|'):
                cells = [cell.strip() for cell in lines[position].strip().strip('|').split('|')]
                bad = invalid or len(cells) != len(headers) or not all(cells)
                subject = f'{headers[0]} {cells[0]}' if cells else ''
                fields = list(zip(headers, cells))
                begin = next((value for key, value in fields if key.casefold() in {'действует с', 'начало действия'}), None)
                end = next((value for key, value in fields if key.casefold() in {'действует по', 'окончание действия'}), None)
                row = ' '.join(f'{subject}; {key}: {value}.' for key, value in fields[1:]
                               if key.casefold() not in {'действует с', 'начало действия', 'действует по', 'окончание действия'}) or subject
                if begin or end:
                    row = 'Действует' + (' с ' + begin if begin else '') + (' по ' + end + ' включительно' if end else '') + '.\n' + row
                blocks.append((row, bad))
                position += 1
            continue
        ordinary.append(lines[position])
        position += 1
    if ordinary:
        blocks.append(('\n'.join(ordinary), False))
    return blocks


def compile_sections(source, plain):
    """Разрешает ссылки только внутри одного документа. Дубликаты/циклы закрываются."""
    nodes, notes, initial, numeric_targets = {}, {}, [], {}
    global_periods = []
    # Ошибка геометрии скана может скрывать ограничение соседнего раздела.
    # Она закрывает этот документ, но не делает посторонние материалы неполными.
    global_issue = any('[STRUCTURE_UNCERTAIN]' in str(section.get('text', '')) for section in source.get('sections', []))
    local_scope = any('[PDF_LOCAL_SECTIONS]' in str(section.get('text', '')) for section in source.get('sections', []))
    parameter_manual = any('[PDF_PARAMETER_MANUAL]' in str(section.get('text', '')) for section in source.get('sections', []))
    for section_number, section in enumerate(source.get('sections', [])):
        raw = normalize_markup(str(section.get('text', '')))
        raw = raw.replace('[PDF_LOCAL_SECTIONS]', '').replace('[PDF_PARAMETER_MANUAL]', '')
        raw = re.sub(r'(?im)^(Раздел\s+\d+(?:\.\d+)*[. :—-].*)$', r'# \1', raw)
        heading = plain(str(section.get('heading', '')))
        chunks = re.split(r'(?m)^\s*#{1,6}\s+(.+?)\s*$', raw)
        parts = [(heading, chunks[0])] + [(chunks[index], chunks[index + 1]) for index in range(1, len(chunks), 2)]
        for part_number, (title, body) in enumerate(parts):
            key_match = re.match(r'(?:Раздел\s+)?(\d+(?:\.\d+)*)[.\s]', title, re.I)
            key = key_match[1] if key_match else f'part-{section_number}-{part_number}'
            if local_scope and key_match:
                # В техническом PDF номера пунктов могут повторяться в разных
                # приложениях. Факт сохраняем, но ссылку на неоднозначный номер
                # не разрешаем автоматически.
                key = f'pdf-part-{section_number}-{part_number}'
                numeric_targets.setdefault(key_match[1], []).append(key)
            clean_lines = []
            for line in body.splitlines():
                definition = DEFINITION.match(line.strip())
                if definition:
                    note_key = 'note-' + definition[1]
                    if note_key in notes:
                        notes[note_key]['issue'] = True
                    else:
                        note_text, note_periods, note_issue = window(definition[2])
                        notes[note_key] = {'heading': 'Сноска', 'text': note_text, 'periods': note_periods, 'issue': note_issue}
                    continue
                clean_lines.append(line)
            body, periods, issue = window('\n'.join(clean_lines))
            if not body.strip():
                if periods and part_number == 0:
                    global_periods.extend(periods)
                global_issue |= issue
                continue
            node = {'heading': plain(title), 'text': body, 'periods': periods,
                    'issue': issue or '[STRUCTURE_UNCERTAIN]' in body or '[LOCAL_STRUCTURE_UNCERTAIN]' in body}
            if key in nodes:
                nodes[key]['issue'] = True
            else:
                nodes[key] = node
                initial.append(key)
    if local_scope:
        for number, targets in numeric_targets.items():
            present = [nodes[key] for key in targets if key in nodes]
            if len(present) == 1:
                nodes[number] = present[0]
            elif present:
                nodes[number] = {'heading': 'Неоднозначная ссылка', 'text': '', 'issue': True, 'periods': []}
    nodes.update(notes)
    reverse = {}
    for key in initial:
        match = re.search(r'исключения?\s+к\s+разделу\s+(\d+(?:\.\d+)*)', nodes[key]['heading'], re.I)
        if match:
            reverse.setdefault(match[1], []).append(key)
    referred = set(reverse_key for values in reverse.values() for reverse_key in values)
    for node in nodes.values():
        referred.update(match[1] for match in REFERENCE.finditer(node['text']))
        referred.update('note-' + match[1] for match in NOTE.finditer(node['text']) if match[1] != 'STRUCTURE_UNCERTAIN')

    visited = set()
    def expand(key, trail):
        if key not in nodes or key in trail or len(trail) >= 8:
            return [], [], True, []
        visited.add(key)
        node = nodes[key]
        refs = [match[1] for match in REFERENCE.finditer(node['text'])]
        refs += ['note-' + match[1] for match in NOTE.finditer(node['text']) if match[1] != 'STRUCTURE_UNCERTAIN']
        refs += reverse.get(key, [])
        text = REFERENCE.sub('', node['text'])
        unknown_reference = bool(re.search(r'\bраздел(?:а|у|е|ом)?\s+\d', text, re.I))
        text = re.sub(r'Исключения\s*[.]', '', text, flags=re.I)
        text = NOTE.sub('', text)
        fragments, periods, bad, provenance = [plain(text).strip(' .')], list(node['periods']), node['issue'] or unknown_reference, []
        for ref in dict.fromkeys(refs):
            extra, windows, issue, origins = expand(ref, trail + [key])
            fragments.extend(extra)
            periods.extend(windows)
            bad |= issue
            if ref in nodes:
                provenance.append({'heading': nodes[ref]['heading'], 'quote': plain(nodes[ref]['text'])})
            provenance.extend(origins)
        return fragments, periods, bad, provenance

    result = []
    for key in initial:
        if key in referred:
            continue
        visited.add(key)
        node = nodes[key]
        for row_number, (text, row_bad) in enumerate(table_blocks(node['text'])):
            if not text.strip():
                continue
            # Строка таблицы разрешает только собственные сноски, не соседнюю строку.
            text, row_periods, row_issue = window(text)
            row_key = f'{key}:row:{row_number}'
            nodes[row_key] = {**node, 'text': text, 'periods': node['periods'] + row_periods,
                              'issue': node['issue'] or row_bad or row_issue}
            reverse[row_key] = reverse.get(key, [])
            fragments, periods, issue, provenance = expand(row_key, [key])
            combined = ' '.join(fragment.rstrip('.') + '.' for fragment in fragments if fragment.strip(' .'))
            row_subject = text.split(';', 1)[0] if '|' in node['text'] and ';' in text else ''
            result.append({'heading': node['heading'] + (' — ' + row_subject if row_subject else ''), 'text': combined,
                           'structure': {'periods': global_periods + periods, 'issue': issue or global_issue,
                                         'local_issue': local_scope and not global_issue,
                                         'dependencies': provenance, 'linked': bool(provenance),
                                         **({'parameter_manual': True} if parameter_manual else {})}})
    # Цикл может не иметь корневого раздела. Он должен дать объяснимый отказ,
    # а не исчезнуть из поиска вместе с основным правилом.
    if initial:
        for key in initial:
            if key in visited:
                continue
            node = nodes[key]
            result.append({**node, 'structure': {'issue': True, 'periods': [], 'dependencies': [], 'local_issue': local_scope and not global_issue,
                                               **({'parameter_manual': True} if parameter_manual else {})}})
    return result

````

## ai_core/site_search_technical.py

````python
"""Поиск подтверждённых характеристик в геометрически разобранных PDF.

Это извлечение цитат, а не генерация технических фактов. Словарь описывает
отношения (масса, назначение, подключение), не изделия и не ответы документа.
Доступ и актуальность редакции проверяются вызывающим индексом до этого шага.
"""
from __future__ import annotations

import math
import re

NO_DATA = 'В доступных материалах я не нашёл информации, которая однозначно отвечает на этот вопрос.'

# Одинаковые буквы латиницы/кириллицы встречаются в обозначениях на чертежах.
# Нормализация нужна только для сравнения; цитата сохраняется без изменений.
LOOKALIKES = str.maketrans('ABCEHKMOPTXYабсенкмортху', 'АВСЕНКМОРТХУабсенкмортху')


def normalized(value):
    value = value.upper().translate(LOOKALIKES).casefold().replace('ё', 'е')
    value = re.sub(r'\bисп(?:олнени\w*|[.])\s*(\d+)', r'исп.\1', value)
    value=re.sub(r'\s+', ' ', re.sub(r'[-–—]\s+', '-', value)).replace(',', '.')
    # «БК 20-1»/«БК20-1» и «ИТ-40»/«ИТ 40» — варианты записи
    # обозначения. Дефисы между цифрами номера чертежа сохраняются.
    return re.sub(r'(?<=[а-яa-z])[- ]+(?=\d)', '', value)


FACETS = (
    (r'тепловыдел|выдел.*тепл', r'тепловыдел'),
    (r'кпд|эффективност', r'кпд|коэффициент полезного'),
    (r'q[хx]х|реактивн', r'q[хx]х|реактивн'),
    (r'масс|вес', r'масс|вес'),
    (r'размер|габарит|вхшхг', r'размер|габарит|вхшхг'),
    (r'сечени', r'сечени'),
    (r'клемм|где подключ', r'клемм'),
    (r'автомат.*защит', r'номинал.*защит|автоматическ'),
    (r'срок.*служ|долговеч', r'срок.*служ'),
    (r'сч[её]тчик', r'счетчик'),
    (r'частот', r'частот'),
    (r'заземл', r'заземл'),
    (r'из каких.*част|состоит', r'совокупност|состоит|в.*состав'),
    (r'для каких.*предназнач|назначени|для чего.*нуж|зачем.*нуж', r'предназначен(?!.*размещ)|служит для'),
    (r'где.*(?:размещ|наход|выключ)|размещать', r'размещ|устанавливается|располагается'),
    (r'кабел.*(?:входит|выходит)|сторон.*кабел', r'ввод.*снизу|выход.*сверху|ввод.*сверху|выход.*снизу'),
    (r'заказ', r'заказ'),
    (r'напряж.*(?:отключ)|значени.*напряж', r'напряж.*\d|\d.*напряж'),
    (r'фаз.*оборв|обрыв.*фаз', r'обрыв.*фаз'),
    (r'сообщ.*авари|уведом|оповест', r'sms|смс|сообщени|оповещ'),
    (r'прямо|напрямую', r'напрямую|непосредственно'),
    (r'равноцен', r'равноцен'),
    (r'внешни.*источник', r'внешни.*источник'),
    (r'переключени.*источник|гарантирован.*отлич.*бесперебойн', r'провал.*напряж'),
    (r'исчезнут|пропад.*фидер', r'пропадан|исчезн|отсутств'),
    (r'аккумулятор.*емкост|аккумулятор.*напряж', r'аккумулятор'),
    (r'фазн.*ток', r'фазн.*ток'),
    (r'по току', r'ток'),
    (r'по вводам|количество ввод', r'ввод'),
    (r'емкост', r'емкост'),
    (r'напряжени', r'напряжени'),
    (r'отключает|отключени.*фидер', r'\bотключает|отключени'),
    (r'как выбира', r'зависим|первым|выбор'),
    (r'источники.*подключ|подключ.*источник', r'подключени|подключать|ввод'),
)


def facets(question):
    q = question.casefold().replace('ё', 'е')
    return [evidence for request, evidence in FACETS if re.search(request, q)]


def anchors(question):
    """Обозначение и мощность образуют один предмет, а не набор отдельных чисел."""
    patterns = [r'\d{3,}(?:-\d{2,}){2,}', r'\b[А-ЯA-Z][А-ЯA-Z\d]*(?:\s+\d+)?(?:-\d+(?:[.,]\d+)?)+(?:\s*кВА)?',
                r'\b[А-ЯA-Z]{2,}\s+\d+(?:[.,]\d+)?\s*кВА']
    found = []
    for pattern in patterns:
        for match in re.finditer(pattern, question):
            value = normalized(match[0])
            if any(re.fullmatch(r'\d{3,}(?:-\d{2,}){2,}',other) and other in value for other in found):
                continue
            if value not in found and not any(value in other for other in found):
                found.append(value)
    for variant in re.findall(r'\b(?:исп(?:олнени\w*|[.])\s*\d{1,2}|тип[ау]?\s*\d{1,2})(?![\d-])', question, re.I):
        value = normalized(variant)
        value = re.sub(r'^тип[ау]?', 'тип', value)
        if value not in found:
            found.append(value)
    return found


def units(passage, patterns):
    text = passage.text
    if re.search(r'Наименование[^:]*:|Номинал[^:]*:', text):
        return [text]
    # Не отделяем условие от действия. Разрываем только явные пункты списка.
    chunks = re.split(r'\s*[•]\s*|(?<=[:.])\s+[а-я]\)\s*', text)
    if len(chunks) > 1:
        values = [chunk.strip() for chunk in chunks if len(chunk.strip()) >= 20]
    elif len(text) < 700:
        values = [text]
    else:
        values = [chunk.strip() for chunk in re.split(r'(?<=[.!?])\s+(?=[А-ЯA-Z])', text) if len(chunk.strip()) >= 20]
    if len(patterns) > 1 and all(re.search(pattern,text,re.I) for pattern in patterns) and not any(all(re.search(pattern,value,re.I) for pattern in patterns) for value in values):
        return [text]
    return values


def render_quote(quote, question, patterns):
    """Убираем ненужные колонки из показа; полная цитата остаётся в evidence."""
    if re.search(r'Наименование[^:]*:', quote) and '; ' in quote:
        # Точка с запятой внутри сноски не разделяет столбцы таблицы.
        fields, depth, start = [], 0, 0
        for position, character in enumerate(quote):
            if character=='(':depth+=1
            elif character==')':depth=max(0,depth-1)
            elif character==';' and depth==0:
                fields.append(quote[start:position].strip());start=position+1
        fields.append(quote[start:].strip())
        selected = []
        for field in fields:
            header = field.split(':',1)[0]
            if re.match(r'Наименование', header):
                selected.append(field.split(':',1)[1].strip())
            elif re.match(r'Примечание',header) or not patterns or any(re.search(pattern,header,re.I) for pattern in patterns):
                selected.append(field)
        displayed = '; '.join(selected).rstrip(' .;') + '.'
        # Сноска в колонке размеров может одновременно ограничивать массу.
        # Сохраняем вложенные скобки целиком, даже если колонку не показываем.
        depth, start, notes = 0, 0, []
        for position, char in enumerate(quote):
            if char == '(':
                if depth == 0:
                    start = position + 1
                depth += 1
            elif char == ')' and depth:
                depth -= 1
                if depth == 0:
                    note = quote[start:position]
                    if len(note) > 20 and note not in displayed:
                        notes.append(note.rstrip(' .;') + '.')
        return displayed + (' ' + ' '.join(dict.fromkeys(notes)) if notes else '')
    if re.search(r'повторн.*подключ|обратн.*подключ', question, re.I):
        clauses = [part.strip() for part in quote.split(';') if re.search(r'\bподключает\b', part)]
        if clauses:
            return '; '.join(clauses).rstrip(' .') + '.'
    if re.search(r'отключает.*фидер', question, re.I):
        clauses = [part.strip() for part in quote.split(';') if re.search(r'\bотключает\b', part)]
        if clauses:
            return '; '.join(clauses).rstrip(' .') + '.'
    if len(quote) > 800 and patterns and re.search(r'[•]',quote):
        chunks = re.split(r'\s*[•]\s*',quote)
        relevant = [part for part in chunks[1:] if any(re.search(pattern,part,re.I) for pattern in patterns)
                    or re.search(r'исключени|однако|кроме',part,re.I)]
        if relevant and all(any(re.search(pattern,part,re.I) for part in relevant) for pattern in patterns):
            title = re.split(r'\s+[–—]\s+',chunks[0])[0].rstrip(' .') + '.'
            return title + ' ' + ' '.join(relevant)
    # Убираем самостоятельное постороннее предложение, но не условия,
    # исключения или отсылки. В сомнительном случае оставляем исходный текст.
    sentences=re.split(r'(?<=[.!?])\s+(?=[А-ЯA-Z])',quote)
    conditional=r'\b(?:если|при|кроме|только|однако|иначе|исключени\w*|не допуска\w*|запрещ\w*|см\.)\b'
    if patterns and 1<len(sentences)<=4 and not any(re.search(conditional,item,re.I) for item in sentences):
        selected=[item for item in sentences if any(re.search(pattern,item,re.I) for pattern in patterns)]
        if selected and all(any(re.search(pattern,item,re.I) for item in selected) for pattern in patterns):
            quote=' '.join(selected)
    return quote.replace(' ', '').replace('• ', '')


def search_technical(question, candidates, tokenize):
    """None оставляет обычный поиск; инженерный профиль отвечает только цитатой."""
    if not candidates or not all((p.structure or {}).get('local_issue') for p in candidates):
        return None
    if all((p.structure or {}).get('parameter_manual') for p in candidates):
        from .site_search_manual import search_manual
        return search_manual(question, candidates)
    empty = {'status': 'not_found', 'text': NO_DATA, 'links': [], 'evidence': []}
    if re.search(r'\b(?:сейчас|сегодня|серийный номер|нашей станции|нашей установки)\b', question, re.I):
        # Типовое решение не содержит телеметрию или паспорт конкретного объекта.
        return empty
    def vocabulary(text):
        # Существительное и глагол одной характеристики должны совпадать:
        # «размещать» ищет «размещение», «состоит» ищет «совокупность».
        for pattern, replacement in ((r'размещ\w*|располаг\w*', ' размещение '),
                                     (r'состо\w*|совокупност\w*|состав\w*', ' состав '),
                                     (r'подключ\w*', ' подключение ')):
            text = re.sub(pattern, replacement, text, flags=re.I)
        return set(tokenize(text))
    patterns = facets(question)
    identifiers = anchors(question)
    subjects = [normalized(match[0]) for match in re.finditer(r'\b[А-ЯA-Z]{2,}(?:-[А-ЯA-Z]+)*\b', question)
                if match[0] not in {'КПД', 'АВ', 'ВхШхГ'}]
    comparison = bool(re.search(r'сравни|отлич|различ', question, re.I))
    tokens = vocabulary(question)
    boilerplate = vocabulary('этот этом документ предполагается указанный максимальный минимальный допустимый можно должен нужно части значение параметр работа исполнение сравни отличается')
    tokens -= boilerplate
    candidates = [p for p in candidates if not ((p.structure or {}).get('issue') and re.search(r'— (?:схема|содержание)$',p.heading))]
    parents, parent_passages = {}, {}
    for passage in candidates:
        number = re.match(r'Раздел (\d+(?:\.\d+)+)\b', passage.heading)
        if number:
            lead = re.split(r'\s*[•]\s*|[.!?]\s+', passage.text)[0][:180]
            parents.setdefault((passage.source['id'],number[1]), []).append(lead)
            parent_passages.setdefault((passage.source['id'],number[1]), []).append(passage)
    rows = []
    for passage in candidates:
        chunks = re.split(r'\s*[•]\s*', passage.text)
        lead = chunks[0] if len(chunks) > 1 else ''
        scope_passage = None
        number = re.match(r'Раздел (\d+(?:\.\d+)+)\b', passage.heading)
        if number:
            parts = number[1].split('.')
            for size in range(2,len(parts)):
                inherited = parents.get((passage.source['id'],'.'.join(parts[:size])), [])
                if len(inherited) == 1:
                    lead += ' ' + inherited[0]
                    if re.search(r'черт\.?\s*\d|тип\s*\d|исп[.]\s*\d',inherited[0],re.I):
                        scope_passage = parent_passages[(passage.source['id'],'.'.join(parts[:size]))][0]
        for quote in units(passage, patterns):
            body = quote.casefold().replace('ё', 'е')
            if patterns and not all(re.search(pattern, body, re.I) for pattern in patterns):
                continue
            # Подключение и отключение имеют разные пороги; совпадение общего
            # слова «напряжение» не разрешает поменять действие на обратное.
            if re.search(r'повторн.*подключ|обратн.*подключ', question, re.I) and not re.search(r'\bподключает\b', body):
                continue
            context = passage.heading + ' ' + lead + ' ' + quote
            text = normalized(context)
            if subjects and not all(re.search(r'(?<!\w)' + re.escape(subject) + r'(?=$|[\W\d])', text) for subject in subjects):
                continue
            matched = {identifier for identifier in identifiers if re.search(r'(?<!\w)'+re.escape(identifier)+r'(?!\d)',text)}
            if identifiers and not (matched if comparison else len(matched) == len(identifiers)):
                continue
            words = vocabulary(context)
            distance = 0
            if subjects and patterns:
                subject_positions = [m.start() for subject in subjects for m in re.finditer(re.escape(subject),normalized(quote))]
                relation_positions = [m.start() for pattern in patterns for m in re.finditer(pattern,body,re.I)]
                if subject_positions and relation_positions:
                    distance = min(abs(a-b) for a in subject_positions for b in relation_positions)
            rows.append({'passage': passage, 'quote': quote, 'words': words, 'body_words': vocabulary(quote), 'anchors': matched, 'distance':distance, 'scope':scope_passage})
    if not rows:
        return empty
    frequency = {word: sum(word in row['words'] for row in rows) for word in tokens}
    for row in rows:
        matched = tokens & row['words']
        score = sum((1 + math.log((len(rows)+1)/(frequency[word]+1))) * (1 if word in row['body_words'] else .3) for word in matched)
        # Полное точное обозначение сильнее совпавшего названия соседнего изделия.
        score += 3 * len(row['anchors'])
        if re.search(r'табличн', question, re.I) and 'таблица' in row['passage'].heading.casefold():
            score *= 1.5
        row['score'], row['covered'] = score / (1 + len(row['words']) / 45) / (1 + row['distance']/160), len(matched)
    rows.sort(key=lambda row: (-row['score'], len(row['quote']), row['passage'].heading))
    best = rows[0]
    if (best['passage'].structure or {}).get('issue'):
        return {'status':'unavailable', 'text':'Не удалось проверить все условия этого правила. Обратитесь к администратору для уточнения документа.', 'links':[], 'evidence':[]}
    rows = [row for row in rows if not (row['passage'].structure or {}).get('issue')]
    if best['covered'] < min(2, len(tokens)) or (not patterns and not identifiers and len(subjects) < 2 and best['covered'] / max(1,len(tokens)) < .65):
        return empty
    selected = [best]
    if comparison and len(identifiers) > 1:
        selected = []
        for identifier in identifiers:
            row = next((row for row in rows if identifier in row['anchors']), None)
            if row is None:
                return empty
            if row not in selected:
                selected.append(row)
    elif comparison:
        # Две независимые цитаты нужны только когда они дополняют вопрос.
        covered = tokens & best['words']
        for row in rows[1:]:
            if row['score'] >= best['score'] * .55 and (tokens & row['words']) - covered:
                selected.append(row)
                break
    # Размеры сравниваем только для одного явно названного изделия и варианта.
    # Номера чертежей и разные строки таблиц сами по себе не являются конфликтом.
    if not comparison and subjects and re.search(r'размер|габарит|вхшхг', question, re.I):
        dimensions = {}
        for row in rows:
            if row['score'] < best['score'] * .5:
                continue
            values = set(re.findall(r'\b\d{2,5}\s*[хx×]\s*\d{2,5}\s*[хx×]\s*\d{2,5}\b', row['quote'], re.I))
            if len(values) == 1:
                value = re.sub(r'\s', '', next(iter(values))).replace('x','х').replace('×','х')
                dimensions.setdefault(value, row)
        if len(dimensions) > 1:
            selected = list(dimensions.values())[:2]
            details = '; '.join(row['passage'].heading + ': ' + value + ' мм' for value,row in list(dimensions.items())[:2])
            return {'status': 'conflict', 'text': 'В документе расходятся размеры: ' + details + '. Уточните правильные размеры у администратора.', 'links': [], 'evidence': []}
    evidence = [{'id': row['passage'].source['id'], 'revision': row['passage'].source['revision'],
                 'heading': row['passage'].heading, 'quote': row['quote']} for row in selected]
    rendered = []
    for row in selected:
        shown = render_quote(row['quote'],question,patterns)
        parent = row['scope']
        if parent:
            label = re.split(r'\s*[•]\s*|[.!?]\s+',parent.text)[0].strip(' .')
            codes = anchors(label)
            if codes and not all(code in normalized(row['quote']) for code in codes):
                # Условие под конкретным исполнением нельзя превратить в общее
                # правило. Родительская цитата включается в доказательства.
                shown = label + '. ' + shown
                evidence.append({'id':parent.source['id'],'revision':parent.source['revision'],
                                 'heading':parent.heading,'quote':parent.text})
        rendered.append(shown)
    links = list({row['passage'].source['id']: {'title': row['passage'].source['title'], 'url': row['passage'].source['url']} for row in selected}.values())
    return {'status': 'answer', 'text': '\n\n'.join(rendered), 'evidence': evidence,
            'links': links, 'context_used': False}

````

## ai_core/site_search_followup.py

````python
"""Предмет короткого уточнения берётся из доказанного ответа, а не угадывается.

В памяти остаются только обозначения из документа и известный вид вопроса.
Текст личных сообщений не сохраняется. Проверки прав и редакции выполняет индекс.
"""
from __future__ import annotations

import re
from .site_search_technical import anchors, normalized, render_quote, facets
from .site_search_reasoning import explicit_names

PROPERTIES = (
    ('масса', r'масс|вес'),
    ('габариты', r'габарит|размер|вхшхг'),
    ('сечение', r'сечени'),
    ('тепловыделение', r'тепловыдел|выдел.*тепл'),
    ('гарантия', r'гаранти'),
    ('срок службы', r'срок.*служ|долговеч'),
    ('температура эксплуатации', r'температур.*(?:работ|эксплуатац|окружающ)|(?:работ|эксплуатац).*температур'),
    ('влажность', r'влажност'),
    ('мощность', r'мощност|потребля'),
    ('напряжение', r'напряжени'),
    ('ток', r'\bток\b|по току'),
)


def property_name(question):
    found=[name for name,pattern in PROPERTIES if re.search(pattern,question,re.I)]
    return found[0] if len(found)==1 else None


def short_reference(question):
    text=question.strip()
    return len(text)<=120 and bool(re.match(r'^(?:а|и)\s|^(?:с какого момента|с упаковкой|без упаковки|с кожухом)\b',text,re.I)
                                  or re.search(r'\b(?:его|её|нее|него|этого|этой)\b',text,re.I))


def document_entities(quote):
    # В таблице имя строки — точное обозначение изделия. «Полюс питания»
    # обозначает цепь, поэтому его не превращаем в название всего устройства.
    match=re.search(r'Наименование(?! полюса)[^:;]*:\s*([^;\n]+)',quote,re.I)
    return [match[1].strip().rstrip('.')] if match else []


def remember_focus(question,result,passages):
    if result.get('status')!='answer' or not result.get('evidence'):
        return None
    evidence=result['evidence'];ids={item['id'] for item in evidence}
    if len(ids)!=1:
        return None
    identifier=next(iter(ids))
    available=[item for item in passages if item.source['id']==identifier and (item.structure or {}).get('local_issue')]
    if not available:
        return None
    manual = all((item.structure or {}).get('parameter_manual') for item in available)
    subjects=[]
    for item in evidence:
        for label in document_entities(item['quote']):
            if label not in subjects:subjects.append(label)
    if not subjects:
        # Не сохраняем произвольное имя из сообщения: обозначение должно
        # присутствовать в процитированном тексте или его заголовке.
        proven=normalized(' '.join(item['quote']+' '+item['heading'] for item in evidence))
        labels=anchors(question) or explicit_names(question)
        subjects=[value for value in labels if normalized(value) in proven]
    if not subjects and manual:
        # В однопредметном руководстве название на обложке/в заголовке
        # источника задаёт прибор. Оно не обязано повторяться в каждой строке.
        title = normalized(available[0].source['title'])
        subjects = [match[0] for match in re.finditer(r'\b[А-ЯA-Z]{2,}\d+(?:[-.]\d+)*\b', question)
                    if normalized(match[0]) in title]
    if not subjects or len(subjects)>4 or any(len(value)>140 for value in subjects):
        return None
    return {'source':identifier,'revision':evidence[0]['revision'],'subjects':subjects,'property':property_name(question),
            **({'manual': True} if manual else {})}


def validated_focus(focus,sources,passages):
    """Повторная проверка производной памяти, в том числе после SQLite restore."""
    if not isinstance(focus,dict):return None
    source=sources.get(focus.get('source'))
    if source is None or source['revision']!=focus.get('revision'):return None
    subjects=focus.get('subjects')
    if not isinstance(subjects,list) or not 1<=len(subjects)<=4 or any(not isinstance(s,str) or not 0<len(s)<=140 for s in subjects):return None
    property=focus.get('property')
    if property not in {None,*[name for name,_ in PROPERTIES]}:return None
    text=normalized(source['title']+' '+' '.join(p.heading+' '+p.text for p in passages if p.source['id']==focus['source']))
    if not all(normalized(value) in text for value in subjects):return None
    manual = all((p.structure or {}).get('parameter_manual') for p in passages if p.source['id']==focus['source'])
    return {'source':focus['source'],'revision':source['revision'],'subjects':list(subjects),'property':property,
            **({'manual': True} if manual else {})}


def resolve_followup(question,focus):
    """Возвращает новый поисковый запрос или необходимость короткого уточнения."""
    if not short_reference(question):return None
    current=anchors(question) or explicit_names(question)
    # Явно названное полное изделие начинает новый запрос. Голое «18 кВА»
    # меняет исполнение прежнего изделия и обрабатывается ниже.
    prop=property_name(question)
    if focus and focus.get('manual'):
        from .site_search_manual import variant
        casing = variant(question)
        if casing and len(focus['subjects']) == 1 and (prop or focus['property']):
            return {'question': (prop or focus['property']) + ' ' + focus['subjects'][0] + ' в корпусе ' + casing,
                    'source': focus['source']}
    if current:
        # «А масса ДВГ?» — новый явно названный предмет, без примеси старого.
        # «А РК 18 кВА?» переносит только вид характеристики, не прежнее изделие.
        if not prop and focus and focus['property'] and len(current)==1:
            return {'question':focus['property']+' '+question,'source':focus['source']}
        return None
    variant=re.search(r'(?<!\w)\d+(?:[.,]\d+)?\s*кВА\b',question,re.I)
    relevant=bool(prop or variant or re.search(r'упаков|кожух|с какого момента',question,re.I))
    if not relevant:return None
    if not focus or len(focus['subjects'])!=1:
        return {'clarify':'Уточните, о каком устройстве идёт речь.'}
    subject=focus['subjects'][0]
    if variant:
        changed,count=re.subn(r'\d+(?:[.,]\d+)?\s*кВА\b',variant[0],subject,flags=re.I)
        if count!=1:return {'clarify':'Уточните полное обозначение устройства.'}
        subject=changed
    prop=prop or focus['property']
    if not prop:return {'clarify':'Уточните, какую характеристику нужно сравнить.'}
    extra=re.sub(r'^(?:а|и)\s+','',question.strip(' ?.'),flags=re.I)
    return {'question':prop+' '+subject+' '+extra,'source':focus['source']}


def concise_result(question,result,passages):
    """Меняем только показ; исходная цитата и ссылка остаются полными."""
    if result.get('status')!='answer' or len(result.get('evidence',[]))!=1:return result
    evidence=result['evidence'][0]
    if not any(p.source['id']==evidence['id'] and p.heading==evidence['heading'] and (p.structure or {}).get('local_issue') for p in passages):return result
    # Уже составленный ответ с родительским условием повторно не сокращаем.
    raw=evidence['quote'].strip()
    if result['text'].strip()!=raw:
        source=next(p.source for p in passages if p.source['id']==evidence['id'])
        if result['text'].strip()!=source['title']+'. '+raw:return result
    return {**result,'text':render_quote(raw,question,facets(question))}


def unique_technical_source(question,candidates):
    """Обычный чат тоже находит технический файл, если обозначение однозначно.

    Проверяем все доступные источники, включая обычные документы. Если одно
    обозначение встречается в нескольких файлах, сохраняем обычный поиск с
    приоритетом документов и проверкой противоречий.
    """
    identifiers=anchors(question)
    manual_identifiers = [normalized(match[0]) for match in re.finditer(r'\b[А-ЯA-Z]{2,}\d+(?:[-.]\d+)*\b', question)]
    from .site_search_manual import relation
    manual_query = bool(manual_identifiers and relation(question))
    if not identifiers and manual_query:
        identifiers = manual_identifiers
    if not identifiers or not (facets(question) or manual_query):return None
    documents={}
    for item in candidates:documents.setdefault(item.source['id'],[]).append(item)
    matched=[]
    for identifier,items in documents.items():
        text=normalized(items[0].source['title']+' '+' '.join(item.heading+' '+item.text for item in items))
        if all(re.search(r'(?<!\w)'+re.escape(value)+r'(?!\d)',text) for value in identifiers):matched.append(identifier)
    if len(matched)==1 and all((item.structure or {}).get('local_issue') for item in documents[matched[0]]):return matched[0]
    return None

````

## ai_core/site_search_runtime.py

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
from .site_search_followup import validated_focus

CONTEXT_TTL = 30 * 24 * 60 * 60
LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def engine_version() -> str:
    digest = hashlib.sha256()
    for name in ("site_search.py", "site_search_language.py", "site_search_reasoning.py", "site_search_conditions.py", "site_search_structure.py", "site_search_links.py", "site_search_catalog.py", "site_search_measurements.py", "site_search_comparison.py", "site_search_fragments.py", "site_pdf_locations.py", "site_search_technical.py", "site_search_followup.py"):
        digest.update(Path(__file__).with_name(name).read_bytes())
    digest.update(Path(__file__).with_name('site_search_manual.py').read_bytes())
    return digest.hexdigest()


class DurableSearch:
    def __init__(self, origins: list[str], path: Path, clock=time.time, extraction_version: str | None = None):
        self.lock = threading.RLock()
        self.index = ClosedSiteIndex(origins, clock=clock, session_ttl=CONTEXT_TTL)
        self.store = SearchStore(path)
        self.restored = False
        self.restore_ms = 0.0
        self.rebuilt = False
        self.document_progress = {}
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
        technical=validated_focus(context.get('technical_focus'),self.index.sources,self.index.passages)
        if technical and technical['source'] in identifiers:
            saved['technical_focus']=technical
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
            self.document_progress = {}
            return generation

    def update_document_progress(self, identifier, revision, generation, details):
        """Прогресс относится только к текущей редакции, без текста и личных данных."""
        with self.lock:
            if generation != self.index.generation:
                return False
            self.document_progress[identifier] = {'id': identifier, 'revision': revision, **details}
            return True

    def publish(self, sources: list[dict], generation: int, aliases: dict | None = None):
        with self.lock:
            if generation != self.index.generation:
                return False
            candidate = ClosedSiteIndex(list(self.index.origins), clock=self.index.clock, session_ttl=self.index.session_ttl)
            candidate.generation, candidate.snapshot = generation, self.index.snapshot
        try:
            # Тяжёлая сборка отделена от опубликованного индекса. Статус и приём
            # новой редакции продолжают работать; прежние ответы уже закрыты begin().
            candidate.publish(sources, generation, aliases)
            with self.lock:
                if generation != self.index.generation:
                    return False
                if not self.store.save_index(candidate, engine_version(), self.extraction_version):
                    raise ValueError("index_save_superseded")
                self.index = candidate
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
            if self.index.state == 'ready':
                documents = [{'id': item['id'], 'revision': item['revision'], **item.get('processing', {
                    'state': 'review' if item.get('structure_unavailable') else 'ready' if item.get('complete', True) else 'partial'})}
                    for item in self.index.sources.values() if item.get('kind') == 'document']
            else:
                documents = [dict(item, state='failed') if self.index.state == 'failed' else dict(item)
                             for item in self.document_progress.values()]
            return {"state": self.index.state, "sources": len(self.index.sources), "passages": len(self.index.passages),
                    "snapshot": self.index.snapshot, "restored_from_disk": self.restored,
                    "restore_ms": self.restore_ms, "rebuilt_from_text": self.rebuilt, "context_count": len(self.index.sessions),
                    "documents": documents}

````

## tests/test_site_pdf_manual.py

````python
"""Проверки структуры и поиска на собственных числах, вне экзамена ТРМ1."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from ai_core.site_pdf_manual import owner, align_values, bind_notes, read_notes, symbol_legend, value_boxes
from ai_core.site_pdf_locations import locate_fragment
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch


def word(text, y, x=10):
    return {'text': text, 'x0': x, 'x1': x + 20, 'top': y, 'bottom': y + 8}


BODY = '''[PDF_LOCAL_SECTIONS]
[PDF_PARAMETER_MANUAL]
# Страница 2 — Таблица 1.1
Общие сведения. Масса прибора — без упаковки: 1,7 кг.
# Страница 2 — Таблица 1.1
Общие сведения. Масса прибора — с упаковкой: 2,3 кг.
# Страница 2 — Таблица 1.1
Габаритные размеры прибора — DIN-реечный Д: (31 × 42 × 53) ± 2 мм.
# Страница 2 — Таблица 1.1
Габаритные размеры прибора — щитовой Щ2: (64 × 75 × 86) ± 2 мм.
# Страница 2 — Таблица 1.1
Питание. Диапазон напряжения питания — постоянное: 17...43 В.
# Страница 2 — Таблица 1.1
Питание. Номинальное напряжение питания — постоянное: 28 В.
# Страница 2 — Таблица 1.1
Параметры обмена (Только для модификации с портом связи). Количество бит данных: 7 (Только для ASCII), 8.
# Страница 3 — Монтаж
Рисунок 5. Габаритные размеры корпуса Д и отверстия в щите.
# Страница 3 — Внешнее оборудование
Внешний источник питания постоянного тока должен иметь напряжение в диапазоне от 12 до 16 В.
# Страница 5 — Гарантия
Гарантийный срок эксплуатации — 15 месяцев со дня продажи. При нарушении условий монтажа гарантия не действует.
'''


def source(body=BODY, revision='r1'):
    return {'id': 'document:manual', 'revision': revision, 'title': 'Руководство АБР9', 'kind': 'document',
            'url': 'http://localhost/manual', 'access': {'scope': 'restricted', 'users': ['reader']},
            'sections': [{'heading': '', 'text': body}]}


class ManualGeometryTests(unittest.TestCase):
    def test_only_isolated_native_symbol_caption_is_localized(self):
        page = SimpleNamespace(height=800, images=[{'bottom': 150}])
        body = [word('Соответствие символов индикатора буквам латинского алфавита', 60)]
        self.assertTrue(symbol_legend(page, body))
        self.assertFalse(symbol_legend(page, body + [word('Обязательное условие питания', 180)]))
        page.images[0]['bottom'] = 700
        self.assertFalse(symbol_legend(page, body))

    def test_highlight_excludes_adjacent_variant_value(self):
        keys = [word('Масса:', 0), word('без упаковки', 12), word('с упаковкой', 32)]
        values = [word('1,7', 12, 80), word('2,3', 32, 80)]
        boxes = value_boxes(keys, values, 1, 2)
        self.assertIn((80, 32, 100, 40), boxes)
        self.assertNotIn((80, 12, 100, 20), boxes)

    def test_rowspan_uses_owning_rectangle(self):
        cells = [(0, 0, 10, 40), (10, 0, 20, 20), (10, 20, 20, 40)]
        self.assertEqual(owner(cells, 5, 30), cells[0])
        self.assertEqual(owner(cells, 15, 30), cells[2])

    def test_overlapping_ownership_is_not_guessed(self):
        self.assertIsNone(owner([(0, 0, 20, 20), (5, 5, 15, 15)], 10, 10))
        self.assertIsNone(owner([(0, 0, 20, 20)], 25, 10))

    def test_multiple_values_keep_prefix_and_wrapped_labels(self):
        keys = [word('Защита корпуса:', 0), word('спереди', 12), word('(для корпуса А)', 22), word('сзади', 42), word('(для корпуса Б)', 52)]
        values = [word('IP31', 17), word('IP45', 47)]
        result = align_values(keys, values)
        self.assertEqual(result, [('Защита корпуса — спереди (для корпуса А)', 'IP31'), ('Защита корпуса — сзади (для корпуса Б)', 'IP45')])

    def test_missing_label_or_distant_value_is_uncertain(self):
        self.assertIsNone(align_values([word('Размеры:', 0), word('А', 12)], [word('18', 12), word('19', 32)]))
        self.assertIsNone(align_values([word('Размеры:', 0), word('А', 12), word('Б', 32)], [word('18', 112), word('19', 132)]))

    def test_multiline_single_value_is_not_split_without_colon(self):
        self.assertEqual(align_values([word('Скорости', 0)], [word('9600;', 0), word('19200', 12)]), [('Скорости', '9600; 19200')])

    def test_six_stars_resolve_exactly_without_using_one_star(self):
        definitions = {1: [{'text': 'Для варианта А.', 'page': 2, 'box': [0, 0, 20, 20]}],
                       6: [{'text': 'Только режим X.', 'page': 3, 'box': [0, 0, 20, 20]}]}
        text, missing, used = bind_notes('7******, 8', definitions)
        self.assertFalse(missing)
        self.assertEqual(text, '7 (Только режим X), 8')
        self.assertEqual(used[0]['page'], 3)
        self.assertNotIn('варианта А', text)

    def test_missing_and_duplicate_notes_block_confirmation(self):
        for notes in ({}, {2: [{'text': 'А'}, {'text': 'Б'}]}):
            self.assertTrue(bind_notes('28**', notes)[1])

    def test_note_continuation_stays_with_its_marker(self):
        notes = read_notes([word('ПРИМЕЧАНИЕ', 1), word('* Только режим А', 12), word('при условии Б.', 24), word('** Режим В.', 36)], (0, 0, 100, 50), 4)
        self.assertEqual(notes[1][0]['text'], 'Только режим А при условии Б.')
        self.assertEqual(notes[2][0]['text'], 'Режим В.')

    def test_highlight_can_include_note_from_next_page(self):
        region = {'kind': 'table', 'page': 1, 'heading': 'Страница 1 — Таблица 1.1', 'text': 'Ток: 4 (Только при X).',
                  'rects': [[.1, .2, .8, .3]], 'row_rects': [[.1, .2, .8, .3]],
                  'extra_rects': [{'page': 2, 'box': [.1, .5, .8, .6]}]}
        layout = {'sha256': 'a'*64, 'page_count': 2, 'pages': [{'page': 1, 'words': [], 'regions': [region]}]}
        result = locate_fragment(layout, region['text'], heading=region['heading'])
        self.assertEqual(result['status'], 'located')
        self.assertEqual([hit['page'] for hit in result['hits']], [1, 2])


class ManualSearchTests(unittest.TestCase):
    def setUp(self):
        self.index = ClosedSiteIndex(['http://localhost'])
        self.index.publish([source()], self.index.begin('manual'))

    def ask(self, text, chat='one', user='reader', selected='document:manual'):
        return self.index.search(text, Principal(user, 'staff'), chat, selected)

    def test_device_range_does_not_take_nominal_or_external_supply(self):
        result = self.ask('Какой диапазон постоянного напряжения питания у АБР9?')
        self.assertEqual(result['status'], 'answer')
        self.assertIn('17...43', result['text'])
        self.assertNotIn('28 В', result['text'])

    def test_switch_package_and_case_in_same_chat(self):
        self.assertIn('1,7', self.ask('Какая масса АБР9 без упаковки?')['text'])
        self.assertIn('2,3', self.ask('А с упаковкой?')['text'])
        self.assertIn('31 × 42 × 53', self.ask('А размеры в корпусе Д?')['text'])
        self.assertIn('64 × 75 × 86', self.ask('А в Щ2?')['text'])

    def test_unselected_document_is_found_by_own_name(self):
        self.assertIn('1,7', self.ask('Какая масса АБР9 без упаковки?', selected=None)['text'])

    def test_unknown_variant_does_not_pick_other_case(self):
        self.assertNotEqual(self.ask('Какие размеры АБР9 в корпусе Ж?')['status'], 'answer')

    def test_other_named_device_does_not_borrow_selected_manual(self):
        self.assertEqual(self.ask('Какая масса АБР10 без упаковки?')['status'], 'not_found')

    def test_no_case_requires_clarification(self):
        self.assertEqual(self.ask('Какие размеры АБР9?')['status'], 'clarify')

    def test_other_chat_and_user_do_not_inherit_subject(self):
        self.ask('Какая масса АБР9 без упаковки?')
        self.assertEqual(self.ask('А с упаковкой?', chat='other')['status'], 'clarify')
        self.assertNotEqual(self.ask('Какая масса АБР9 без упаковки?', user='outsider')['status'], 'answer')

    def test_only_own_footnote_is_kept_in_answer(self):
        result = self.ask('Можно ли использовать семь бит данных?')
        self.assertIn('Только для ASCII', result['text'])
        self.assertIn('для модификации с портом связи', result['text'])

    def test_unresolved_geometry_does_not_answer(self):
        self.index.publish([source(BODY.replace('Масса прибора', '[LOCAL_STRUCTURE_UNCERTAIN] Масса прибора'))], self.index.begin('bad'))
        self.assertEqual(self.ask('Какая масса АБР9 без упаковки?')['status'], 'unavailable')

    def test_restart_preserves_focus_and_revision_change_clears_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'search.sqlite3'
            runtime = DurableSearch(['http://localhost'], path)
            runtime.publish([source()], runtime.begin('v1'))
            actor = Principal('reader', 'staff')
            runtime.query('Какая масса АБР9 без упаковки?', actor, 'one', 'document:manual', {'document:manual': 'r1'})
            restored = DurableSearch(['http://localhost'], path)
            self.assertIn('2,3', restored.query('А с упаковкой?', actor, 'one', 'document:manual', {'document:manual': 'r1'})['text'])
            restored.publish([source(revision='r2')], restored.begin('v2'))
            self.assertEqual(restored.query('А с упаковкой?', actor, 'one', 'document:manual', {'document:manual': 'r2'})['status'], 'clarify')


if __name__ == '__main__':
    unittest.main()

````

## tests/test_site_search_large_pdf.py

````python
"""Границы больших документов, возобновление и прогресс редакций без генерации PDF."""
import base64
import json
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from ai_core.site_documents import extract_document
from ai_core.site_document_cache import ExtractionCancelled
from ai_core.site_search_runtime import DurableSearch
from ai_core.site_scan_layout import compose_pages
from ai_core.site_scan_tables import describe_table
from ai_core.site_search import ClosedSiteIndex
from ai_core.site_pdf_locations import locate_fragment, prepare_pdf_lookup

URL = 'data:application/pdf;base64,' + base64.b64encode(b'%PDF-test-double').decode()


class Page(dict):
    calls = []

    def __init__(self, number, text):
        super().__init__()
        self.number, self.text = number, text

    def extract_text(self):
        self.calls.append(self.number)
        return self.text


class LargePdfTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name) / 'cache'
        self.patch = patch('ai_core.site_documents.CACHE', self.cache)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        # Эти тесты моделируют PdfReader, а не реальные байты PDF. Новый
        # геометрический профиль проверяется отдельно на настоящем документе.
        technical = patch('ai_core.site_documents.extract_technical_pdf', return_value=None)
        technical.start()
        self.addCleanup(technical.stop)
        manual = patch('ai_core.site_documents.extract_manual_pdf', return_value=None)
        manual.start()
        self.addCleanup(manual.stop)
        Page.calls = []

    def pages(self, count=92):
        reader = type('Reader', (), {})()
        reader.pages = [Page(n, f'Страница {n}. Здесь находится самостоятельный раздел большого учебного документа для проверки чтения.\n'
                            + ('Содержимое страницы сохраняется полностью без сокращения текста.\n' * 45)) for n in range(1, count+1)]
        return patch('ai_core.site_documents.PdfReader', return_value=reader)

    def test_all_92_pages_and_more_than_200000_characters(self):
        events = []
        with self.pages():
            text, complete = extract_document(URL, 'large.pdf', progress=lambda value: events.append(value.copy()))
        self.assertTrue(complete)
        self.assertGreater(len(text), 200000)
        self.assertIn('Страница 92.', text)
        self.assertEqual(events[-1], {'pages_read': 92, 'pages_total': 92})
        self.assertEqual(Page.calls, list(range(1, 93)))
        with patch('ai_core.site_documents.PdfReader', side_effect=AssertionError('Cache must avoid PDF work')):
            self.assertEqual(extract_document(URL, 'large.pdf'), (text, complete))

    def test_interruption_resumes_and_preserves_identical_text(self):
        def cancel(value):
            if value.get('pages_read') == 41:
                raise ExtractionCancelled()
        with self.pages():
            with self.assertRaises(ExtractionCancelled):
                extract_document(URL, 'large.pdf', progress=cancel)
        self.assertEqual(Page.calls, list(range(1, 42)))
        Page.calls = []
        with self.pages():
            resumed = extract_document(URL, 'large.pdf')
        self.assertEqual(Page.calls, list(range(42, 93)))
        with tempfile.TemporaryDirectory() as other, patch('ai_core.site_documents.CACHE', Path(other)), self.pages():
            self.assertEqual(resumed, extract_document(URL, 'large.pdf'))

    def test_corrupted_page_is_re_read(self):
        def cancel(value):
            if value.get('pages_read') == 2:
                raise ExtractionCancelled()
        with self.pages(3), self.assertRaises(ExtractionCancelled):
            extract_document(URL, 'large.pdf', progress=cancel)
        checkpoint = next(self.cache.glob('*-pages/1.json'))
        saved = json.loads(checkpoint.read_text(encoding='utf-8'))
        saved['value']['text'] = 'Изменённое значение без совпадающего хеша.'
        checkpoint.write_text(json.dumps(saved), encoding='utf-8')
        Page.calls = []
        with self.pages(3):
            result = extract_document(URL, 'large.pdf')
        self.assertEqual(Page.calls, [1, 3])
        self.assertNotIn('Изменённое значение', result[0])

    def test_resource_limit_never_claims_complete(self):
        with self.pages(93), patch('ai_core.site_documents.MAX_PDF_PAGES', 92):
            text, complete = extract_document(URL, 'large.pdf')
        self.assertFalse(complete)
        self.assertTrue(text.startswith('[STRUCTURE_UNCERTAIN]'))
        self.assertNotIn('Страница 93.', text)

    def test_decimal_table_numbers_keep_separate_footnotes(self):
        text = compose_pages(['Таблица 3.1\nСрок [1].\n[1]: 4 дня.',
                              'Таблица 3.2\nСрок [1].\n[1]: 9 дней.'], [], describe_table)
        self.assertNotIn('[STRUCTURE_UNCERTAIN]', text)
        self.assertIn('[t3-1-1]: 4 дня.', text)
        self.assertIn('[t3-2-1]: 9 дней.', text)
        duplicate = compose_pages(['Таблица 3.1\nСрок [1].', 'Таблица 3.1\n[1]: 9 дней.'], [], describe_table)
        self.assertIn('[STRUCTURE_UNCERTAIN]', duplicate)

    def test_old_revision_cannot_update_progress_and_ready_status_survives_restart(self):
        path = Path(self.directory.name) / 'index.sqlite3'
        runtime = DurableSearch(['http://localhost'], path)
        old = runtime.begin('old')
        current = runtime.begin('current')
        self.assertFalse(runtime.update_document_progress('document:a', 'old', old, {'state': 'ready'}))
        self.assertTrue(runtime.update_document_progress('document:a', 'current', current, {'state': 'reading', 'pages_read': 41}))
        self.assertEqual(runtime.status()['documents'][0]['pages_read'], 41)
        source = {'id': 'document:a', 'revision': 'current', 'title': 'Документ', 'url': 'http://localhost/document',
                  'kind': 'document', 'access': {'scope': 'workspace'}, 'sections': [], 'complete': True,
                  'processing': {'state': 'review', 'pages_read': 92, 'pages_total': 92}}
        runtime.publish([source], current)
        self.assertEqual(DurableSearch(['http://localhost'], path).status()['documents'], runtime.status()['documents'])

    def test_status_and_revision_change_do_not_wait_for_heavy_index_build(self):
        runtime = DurableSearch(['http://localhost'], Path(self.directory.name) / 'slow.sqlite3')
        generation = runtime.begin('old')
        entered, release = threading.Event(), threading.Event()
        original = ClosedSiteIndex.publish
        def slow(index, sources, generation, aliases=None):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
            return original(index, sources, generation, aliases)
        with ThreadPoolExecutor(max_workers=2) as executor, patch.object(ClosedSiteIndex, 'publish', slow):
            build = executor.submit(runtime.publish, [], generation)
            try:
                self.assertTrue(entered.wait(1))
                status = executor.submit(runtime.status).result(timeout=1)
                self.assertEqual(status['state'], 'processing')
                current = executor.submit(runtime.begin, 'new').result(timeout=1)
                self.assertGreater(current, generation)
            finally:
                release.set()
            self.assertFalse(build.result(timeout=1))
        self.assertEqual(runtime.index.snapshot, 'new')

    def test_reused_coordinate_index_keeps_duplicate_and_uncertain_behavior(self):
        words = [{'text': word, 'confidence': confidence, 'box': [.1, .1, .3, .2]}
                 for word, confidence in [('Срок', 100), ('хранения', 100), ('7', 100), ('дней', 100),
                                          ('Срок', 100), ('хранения', 100), ('7', 60), ('дней', 100)]]
        layout = {'sha256': 'a'*64, 'page_count': 92, 'pages': [{'page': 92, 'words': words}]}
        lookup = prepare_pdf_lookup(layout)
        for question in ['Срок хранения 7 дней', 'Срок хранения 8 дней', 'Неизвестный фрагмент документа']:
            self.assertEqual(locate_fragment(layout, question), locate_fragment(layout, question, lookup=lookup))
        self.assertEqual(locate_fragment(layout, 'Срок хранения 7 дней', lookup=lookup)['initial_page'], 92)


if __name__ == '__main__':
    unittest.main()

````

## evaluation/closed_search/check_trm1_regression.py

````python
"""Повторная проверка уже известного PDF; это не независимый экзамен.

Рубрика используется только после получения всех ответов. В поисковый API
передаются вопрос, субъект доступа, чат и выбранный файл — без эталона/страницы.
"""
import base64
import hashlib
import json
import re
import time
from pathlib import Path
from ai_core.site_documents import extract_document
from ai_core.site_pdf_locations import extract_pdf_layout, locate_fragment, prepare_pdf_lookup, prepare_structured_lookup
from ai_core.site_search import ClosedSiteIndex, Principal

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / '.runtime/pdf-manual-20261031'


def run():
    specification = ROOT / 'evaluation/closed_search/independent_trm1_20261030.json'
    assert hashlib.sha256(specification.read_bytes()).hexdigest() == '353dc7a3350f5ac771887737725ce7a2f392cbed56a236c44dd4613039443446'
    spec = json.loads(specification.read_text(encoding='utf-8'))
    pdf = ROOT / spec['source']
    data = pdf.read_bytes()
    assert hashlib.sha256(data).hexdigest() == spec['source_sha256']
    data_url = 'data:application/pdf;base64,' + base64.b64encode(data).decode()
    started = time.perf_counter()
    text, complete = extract_document(data_url, pdf.name)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT/'extracted.txt').write_text(text, encoding='utf-8')
    extraction_seconds = time.perf_counter() - started
    layout = extract_pdf_layout(data_url)
    (OUTPUT/'layout.json').write_text(json.dumps(layout, ensure_ascii=False), encoding='utf-8')
    record = {'id': 'document:trm1', 'revision': spec['source_sha256'], 'kind': 'document',
              'title': 'ТРМ1 — руководство 1.39', 'url': 'http://localhost/document/trm1',
              'access': {'scope': 'restricted', 'users': ['reader']}, 'complete': complete,
              'sections': [{'heading': '', 'text': text}]}
    index = ClosedSiteIndex(['http://localhost'])
    index.publish([record], index.begin('regression'))
    results = []
    for case in spec['cases'] + spec['dialogue']:
        answer = index.search(case['question'], Principal('reader', 'staff'), case.get('chat', case['id']), record['id'])
        results.append({'id': case['id'], 'question': case['question'], 'answer': answer})
    (OUTPUT/'answers.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    # Эта рубрика намеренно лежит вне runtime. Проверяем числа и ограничения,
    # а не только статус «answer». Вопросы/ожидания в исходном JSON не меняются.
    expected = {
        'trm-01': ['измерени', 'регулирован', 'температур'],
        'trm-02': [r'21\.\.\.120', 'постоянное'],
        'trm-03': ['10 ВА', 'не более'], 'trm-04': ['8 Вт', 'не более'],
        'trm-05': ['1 с', 'не более'], 'trm-06': ['10 мин', 'не более'],
        'trm-07': ['0,25 кг', 'без упаковки'], 'trm-08': ['0,4 кг', 'с упаковкой'],
        'trm-09': ['90 × 88 × 59', '± 1 мм'], 'trm-10': ['96 × 48 × 100', '± 1 мм'],
        'trm-11': ['IP20', 'для корпуса Д', 'лицевой'],
        'trm-12': ['Modbus RTU', 'Modbus ASCII', 'Только для модификации', 'RS-485'],
        'trm-13': ['7', 'Только для Modbus ASCII', '8'],
        'trm-14': ['минус 40', r'\+55'], 'trm-15': ['80%', r'\+35', 'без конденсации'],
        'trm-16': ['12 лет'], 'trm-17': ['24 месяца', 'со дня продажи'],
        'trm-18': ['бесплатный ремонт или замену', 'гарантийного срока', 'эксплуатации, транспортирования, хранения и монтажа'],
        'trm-19': ['на стеллажах'], 'trm-20': ['двум', 'каналов: 1', 'противоречие'],
    }
    for target, origin in zip(range(1, 9), (7, 8, 9, 10, 17, 17, 14, 15)):
        expected[f'follow-{target:02}'] = expected[f'trm-{origin:02}']
    lookup, structured = prepare_pdf_lookup(layout), prepare_structured_lookup(layout)
    grades = []
    for row in results:
        key, answer = row['id'], row['answer']
        status = 'not_found' if key in {'trm-21', 'trm-22', 'trm-23', 'trm-24'} else 'conflict' if key == 'trm-20' else 'answer'
        missing = [pattern for pattern in expected.get(key, []) if not re.search(pattern, answer['text'], re.I)]
        locations = [locate_fragment(layout, item['quote'], heading=item['heading'], lookup=lookup, structured_lookup=structured)
                     for item in answer.get('evidence', [])]
        passed = answer['status'] == status and not missing
        grades.append({'id': key, 'passed': passed, 'expected_status': status, 'actual_status': answer['status'], 'missing': missing,
                       'locations': [{'status': item['status'], 'page': item.get('initial_page')} for item in locations]})
    summary = {'method': 'known-document regression; no oracle passed to search', 'passed': sum(row['passed'] for row in grades),
               'total': len(grades), 'complete': complete, 'layout_complete': layout['complete'],
               'extraction_seconds': round(extraction_seconds, 3), 'grades': grades,
               'pdf_sha256': spec['source_sha256'], 'spec_sha256': hashlib.sha256(specification.read_bytes()).hexdigest()}
    (OUTPUT/'regression.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in summary.items() if key != 'grades'}, ensure_ascii=False))
    for grade in grades:
        if not grade['passed'] or any(item['status'] != 'located' for item in grade['locations']):
            print(json.dumps(grade, ensure_ascii=False))
    assert summary['passed'] == summary['total'], 'See regression.json for failed answers'


if __name__ == '__main__':
    run()

````

