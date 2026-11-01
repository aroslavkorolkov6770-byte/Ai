# Полный код этапа страниц — 29.10.2026

Исходники из C:/Users/KROLL/Desktop/Ai. Все перечисленные файлы приведены целиком, без сокращений. Не содержит секретов и не публиковался.

## ai_core/site_documents.py

SHA256: b8dafdd5cbc9256e5de206c1b42f7d2ce2adecb921c4b5b15f44bf58a704a2e3

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
from .site_scan_tables import describe_table

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".runtime" / "closed-search" / "document-cache"
# Изменение алгоритма извлечения само инвалидирует производный кэш. Иначе
# исправленный OCR мог бы продолжать читать прежний результат тех же байтов PDF.
PARSER_VERSION = "closed-documents-v5-page-layout-" + hashlib.sha256(
    Path(__file__).read_bytes() + Path(__file__).with_name('site_scan_tables.py').read_bytes()
    + Path(__file__).with_name('site_scan_layout.py').read_bytes()
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
````

## ai_core/site_scan_tables.py

SHA256: 0abaa876d34340237c96ba1d6deb6c96f7cb267a27b88c9635d26a425a713bc8

````python
"""Локальное чтение линованных сканов по геометрии, а не порядку слов OCR.

Объединения восстанавливаются только по видимым границам. Модуль не угадывает
пустые значения, не исправляет числа и не выполняется на пути пользовательского
вопроса. Неподдерживаемая геометрия закрывает документ для фактического ответа.
"""
from __future__ import annotations

import csv
import io
import math
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from .site_scan_layout import rectify_page, borderless

UNCERTAIN = '[STRUCTURE_UNCERTAIN]'


@dataclass
class Cell:
    top: int
    left: int
    bottom: int
    right: int
    text: str = ''


def read_image(path):
    # imread не везде поддерживает кириллицу Windows; декодируем байты явно.
    data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if image is None or image.size > 24_000_000:
        raise ValueError('scan_image_invalid_or_large')
    return image


def deskew(image):
    ink = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    lines = cv2.HoughLinesP(ink, 1, np.pi / 1800, threshold=100,
                            minLineLength=max(150, image.shape[1] // 5), maxLineGap=12)
    angles = []
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            if abs(angle) <= 5:
                angles.append(angle)
    if len(angles) < 3:
        return image
    angle = float(np.median(angles))
    if abs(angle) < .08:
        return image
    height, width = image.shape
    transform = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1)
    return cv2.warpAffine(image, transform, (width, height), flags=cv2.INTER_CUBIC, borderValue=255)


def coordinates(mask, axis, length):
    hits = np.flatnonzero(np.count_nonzero(mask, axis=axis) >= length * .12)
    groups = np.split(hits, np.where(np.diff(hits) > 3)[0] + 1)
    return [int(round(float(np.mean(group)))) for group in groups if len(group)]


def grid_cells(horizontal, vertical, xs, ys):
    """Соседние участки без линии образуют ячейку; L-формы запрещены."""
    rows, cols = len(ys) - 1, len(xs) - 1
    if not 2 <= rows <= 80 or not 2 <= cols <= 20 or rows * cols > 500:
        raise ValueError('scan_grid_size')
    parents = list(range(rows * cols))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first, second):
        parents[root(second)] = root(first)

    def boundary(mask, first, second, coordinate, is_horizontal):
        if second - first < 18:
            raise ValueError('scan_cell_too_small')
        area = (mask[max(0, coordinate - 3):coordinate + 4, first + 6:second - 5] if is_horizontal
                else mask[first + 6:second - 5, max(0, coordinate - 3):coordinate + 4])
        coverage = float(np.mean(np.any(area, axis=0 if is_horizontal else 1)))
        if .12 < coverage < .85:
            raise ValueError('scan_broken_border')
        return coverage >= .85

    for row in range(rows):
        for col in range(cols):
            index = row * cols + col
            if row and not boundary(horizontal, xs[col], xs[col + 1], ys[row], True):
                union(index, index - cols)
            if col and not boundary(vertical, ys[row], ys[row + 1], xs[col], False):
                union(index, index - 1)
            if row in {0, rows - 1} and not boundary(horizontal, xs[col], xs[col + 1], ys[0 if row == 0 else -1], True):
                raise ValueError('scan_open_border')
            if col in {0, cols - 1} and not boundary(vertical, ys[row], ys[row + 1], xs[0 if col == 0 else -1], False):
                raise ValueError('scan_open_border')
    groups = {}
    for row in range(rows):
        for col in range(cols):
            groups.setdefault(root(row * cols + col), []).append((row, col))
    cells = []
    for group in groups.values():
        top, bottom = min(r for r, c in group), max(r for r, c in group) + 1
        left, right = min(c for r, c in group), max(c for r, c in group) + 1
        if len(group) != (bottom - top) * (right - left):
            raise ValueError('scan_nonrectangular_cell')
        cells.append(Cell(top, left, bottom, right))
    return cells


def parse_words(tsv):
    words = []
    for entry in csv.DictReader(io.StringIO(tsv), delimiter='\t', quoting=csv.QUOTE_NONE):
        if entry.get('level') != '5' or not entry.get('text', '').strip():
            continue
        words.append({'text': entry['text'].strip(), 'confidence': float(entry['conf']),
                      **{key: int(entry[key]) for key in ('left', 'top', 'width', 'height', 'block_num', 'par_num', 'line_num')}})
    return words


def words_text(words):
    # TSV уже упорядочен Tesseract внутри строк; геометрия определяет ячейку.
    lines = {}
    for word in words:
        key = (word['block_num'], word['par_num'], word['line_num'])
        lines.setdefault(key, []).append(word['text'])
    return '\n'.join(' '.join(line) for line in lines.values())


def reliable(words):
    if not words:
        return False
    # У числа и отрицания ошибка меняет само правило: отдельный строгий порог.
    critical = [word for word in words if re.search(r'\d', word['text']) or word['text'].casefold() in {'не', 'нет'}]
    return (sum(word['confidence'] for word in words) / len(words) >= 75
            and all(word['confidence'] >= 60 for word in words)
            and all(word['confidence'] >= 80 for word in critical))


def describe_table(cells, rows, cols, page, ordinal):
    """Превращаем иерархию заголовков в явные фразы с условиями из таблицы."""
    header_rows = max(cell.bottom for cell in cells if cell.top == 0)
    while any(cell.top < header_rows and cell.bottom == header_rows and cell.right - cell.left > 1 for cell in cells):
        header_rows += 1
        if header_rows > 4:
            raise ValueError('scan_header_depth')
    if header_rows >= rows or any(cell.top < header_rows < cell.bottom for cell in cells):
        raise ValueError('scan_header_boundary')
    headers = []
    for col in range(cols):
        parts = sorted((cell for cell in cells if cell.top < header_rows and cell.left <= col < cell.right), key=lambda c: c.top)
        headers.append([cell.text for cell in parts])
    if len(headers[0]) != 1 or any(re.search(r'\d', text) for text in headers[0]):
        raise ValueError('scan_subject_header')
    by_subject = {}
    for row in range(header_rows, rows):
        line = [next(cell for cell in cells if cell.top <= row < cell.bottom and cell.left <= col < cell.right) for col in range(cols)]
        if any(cell.right - cell.left > 1 for cell in line):
            raise ValueError('scan_body_colspan')
        subject = headers[0][0] + ' ' + line[0].text
        conditions = [cell.text for col, cell in enumerate(line) if headers[col][-1].casefold() in {'условие', 'условия'}]
        if any(not re.match(r'^Если\s+[^,;:]+$', condition, re.I) for condition in conditions):
            raise ValueError('scan_condition_not_explicit')
        starts = [cell.text for col, cell in enumerate(line) if headers[col][-1].casefold() in {'действует с', 'начало действия'}]
        ends = [cell.text for col, cell in enumerate(line) if headers[col][-1].casefold() in {'действует по', 'окончание действия'}]
        if len(starts) > 1 or len(ends) > 1:
            raise ValueError('scan_duplicate_period')
        period = ('Действует' + (' с ' + starts[0] if starts else '') + (' по ' + ends[0] + ' включительно' if ends else '') + '.') if starts or ends else ''
        sentences = by_subject.setdefault((subject, period), [])
        for col in range(1, cols):
            if headers[col][-1].casefold() in {'условие', 'условия', 'действует с', 'начало действия', 'действует по', 'окончание действия'}:
                continue
            predicates = [text for text in headers[col] if re.match(r'^Если\s+', text, re.I)] + conditions
            labels = [text for text in headers[col] if text not in predicates]
            if not labels or len(predicates) > 1:
                raise ValueError('scan_ambiguous_header')
            sentence = subject + '; ' + ' / '.join(labels) + ': ' + line[col].text
            if predicates:
                sentence = predicates[0] + ', ' + sentence[0].lower() + sentence[1:]
            sentences.append(sentence.rstrip('.') + '.')
    return '\n'.join(f'# Страница {page}, таблица {ordinal}: {subject}\n' + (period + '\n' if period else '') + ' '.join(sentences)
                     for (subject, period), sentences in by_subject.items())


def extract_scan(path, ocr_tsv, join_lines, page=1, metadata=None):
    """Возвращает текст и наличие таблицы. Координаты нужны только на этапе OCR."""
    metadata = [] if metadata is None else metadata
    image = deskew(rectify_page(read_image(path)))
    ink = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, max(35, image.shape[1] // 35)), np.uint8))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((max(35, image.shape[0] // 50), 1), np.uint8))
    grid = cv2.bitwise_or(horizontal, vertical)
    contours = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    boxes = sorted((cv2.boundingRect(contour) for contour in contours), key=lambda box: (box[1], box[0]))
    boxes = [box for box in boxes if box[2] > image.shape[1] * .25 and box[3] > 70]
    if not boxes:
        ocr_path = Path(path).with_name(Path(path).stem + '-layout.png')
        cv2.imencode('.png', image)[1].tofile(str(ocr_path))
        words = parse_words(ocr_tsv(ocr_path))
        return borderless(words,image.shape[1],Cell,describe_table,reliable,join_lines,words_text,page,metadata)
    if len(boxes) > 12:
        raise ValueError('scan_tables_limit')
    clean = image.copy()
    clean[cv2.dilate(grid, np.ones((3, 3), np.uint8)) > 0] = 255
    ocr_path = Path(path).with_name(Path(path).stem + '-cells.png')
    cv2.imencode('.png', clean)[1].tofile(str(ocr_path))
    words = parse_words(ocr_tsv(ocr_path))
    blocks, used, issue = [], set(), False
    for ordinal, (x, y, width, height) in enumerate(boxes, 1):
        inside = [(i, word) for i, word in enumerate(words)
                  if x <= word['left'] + word['width'] / 2 <= x + width
                  and y <= word['top'] + word['height'] / 2 <= y + height]
        used.update(index for index, word in inside)
        try:
            xs = [x + offset for offset in coordinates(vertical[y:y + height, x:x + width], 0, height)]
            ys = [y + offset for offset in coordinates(horizontal[y:y + height, x:x + width], 1, width)]
            cells = grid_cells(horizontal, vertical, xs, ys)
            assigned = set()
            for cell in cells:
                entries = [(i, word) for i, word in inside
                           if xs[cell.left] <= word['left'] + word['width'] / 2 <= xs[cell.right]
                           and ys[cell.top] <= word['top'] + word['height'] / 2 <= ys[cell.bottom]]
                if any(word['left'] < xs[cell.left] - 3 or word['left'] + word['width'] > xs[cell.right] + 3 for i, word in entries):
                    raise ValueError('scan_word_crosses_cell')
                selected = [word for i, word in entries]
                if not reliable(selected):
                    raise ValueError('scan_cell_uncertain')
                cell.text = ' '.join(words_text(selected).split())
                assigned.update(i for i, word in entries)
            if assigned != {i for i, word in inside}:
                raise ValueError('scan_unassigned_words')
            table_text = describe_table(cells, len(ys) - 1, len(xs) - 1, page, ordinal)
            metadata.append({'page':page,'cells':cells,'rows':len(ys)-1,'cols':len(xs)-1,'ordinal':ordinal,'text':table_text})
            blocks.append((y, table_text))
        except (ValueError, StopIteration) as error:
            # Сохраняем слова для узнавания темы, но закрываем весь документ:
            # непонятная сноска может ограничивать и соседний корректный раздел.
            issue = True
            logging.getLogger(__name__).info('scan_table_unavailable reason=%s', str(error))
            blocks.append((y, join_lines(words_text([word for i, word in inside]))))
    outside = [(i, word) for i, word in enumerate(words) if i not in used]
    groups = {}
    for i, word in outside:
        key = (word['block_num'], word['par_num'])
        groups.setdefault(key, []).append(word)
    for values in groups.values():
        # Сохраняем отдельно текст до, между и после таблиц, в том числе сноски.
        for region in range(len(boxes) + 1):
            start = boxes[region - 1][1] + boxes[region - 1][3] if region else 0
            end = boxes[region][1] if region < len(boxes) else image.shape[0]
            section = [word for word in values if start <= word['top'] < end]
            if section:
                if not reliable(section):
                    issue = True
                blocks.append((min(word['top'] for word in section), join_lines(words_text(section))))
    result = '\n'.join(text for y, text in sorted(blocks))
    # Область сносок назначается после извлечения всех страниц: только явный
    # печатный номер таблицы разрешает ссылку на сноску соседней страницы.
    return (UNCERTAIN + '\n' if issue else '') + result, True
````

## ai_core/site_scan_layout.py

SHA256: d39bc22394044b27c8334f25ddf45b3a792699d7a97e9caef92fd47ec4eb00f8

````python
"""Геометрия листа, явные колонки без рамок и связи страниц одного документа.

Служебные метаданные создаёт извлекатель. Текст документа не исполняется;
неполная геометрия или несовпадающий перенос не дают разрешения на ответ.
"""
from __future__ import annotations
from copy import deepcopy
import re
import cv2
import numpy as np

UNCERTAIN = '[STRUCTURE_UNCERTAIN]'
CAPTION = re.compile(r'^(?:Продолжение\s+)?таблиц[аы]\s+(\d+)\b', re.I | re.M)
NOTES = re.compile(r'\bсноски\s+к\s+таблице\s+(\d+)\b', re.I)
PENDING = re.compile(r'Продолжение строки на следующей странице[.]?', re.I)
CONTINUED = re.compile(r'\s*\(продолжение\)\s*$', re.I)


def rectify_page(image):
    """Исправляем перспективу только при видимом выпуклом контуре белого листа."""
    mask = cv2.inRange(image, 200, 255)
    contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    height, width = image.shape
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
        if cv2.contourArea(contour) < image.size * .35:
            continue
        polygon = cv2.approxPolyDP(contour, .015 * cv2.arcLength(contour, True), True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        points = polygon.reshape(4, 2).astype(np.float32)
        # Порядок: слева сверху, справа сверху, справа снизу, слева снизу.
        sums, differences = points.sum(axis=1), np.diff(points, axis=1).ravel()
        ordered = np.array([points[np.argmin(sums)], points[np.argmin(differences)],
                            points[np.argmax(sums)], points[np.argmax(differences)]])
        if len(set(map(tuple, ordered))) != 4:
            raise ValueError('scan_page_ambiguous_corners')
        if np.max(np.abs(ordered - np.array([[0,0],[width-1,0],[width-1,height-1],[0,height-1]]))) < 8:
            return image
        # Требуем фон вокруг листа: прямоугольная таблица на белом скане не лист.
        if sum(float(image[y, x]) < 180 for x, y in [(0,0),(width-1,0),(width-1,height-1),(0,height-1)]) < 3:
            return image
        top, right, bottom, left = [float(np.linalg.norm(ordered[i]-ordered[(i+1)%4])) for i in range(4)]
        if min(top,bottom)/max(top,bottom) < .6 or min(left,right)/max(left,right) < .6:
            raise ValueError('scan_page_perspective_extreme')
        out_width, out_height = round(max(top,bottom)), round(max(left,right))
        if min(out_width,out_height) < 300 or out_width*out_height > 24_000_000:
            raise ValueError('scan_page_geometry_size')
        transform = cv2.getPerspectiveTransform(ordered,np.array([[0,0],[out_width-1,0],[out_width-1,out_height-1],[0,out_height-1]],np.float32))
        return cv2.warpPerspective(image,transform,(out_width,out_height),flags=cv2.INTER_CUBIC,borderValue=255)
    return image


def physical_lines(words):
    """Порядок OCR не заменяет координаты: колонки иногда распознаются блоками."""
    lines=[]
    for word in sorted(words,key=lambda w:(w['top']+w['height']/2,w['left'])):
        center=word['top']+word['height']/2
        match=next((line for line in reversed(lines[-3:]) if abs(line[0]-center)<max(8,word['height']*.55)),None)
        if match is None:
            lines.append([center,[word]])
        else:
            match[1].append(word)
    return [sorted(line[1],key=lambda word:word['left']) for line in lines]


def borderless(words, image_width, cell_type, describe, reliable, join_lines, words_text, page, metadata):
    """Явная шапка + выровненные строки. Пустая ячейка никогда не наследуется."""
    lines=physical_lines(words)
    header_index=None; groups=[]
    for index,line in enumerate(lines):
        if not re.fullmatch(r'Товар|Наименование|Изделие|Позиция|Поставка',line[0]['text'],re.I):
            continue
        groups=[[line[0]]]
        for word in line[1:]:
            previous=groups[-1][-1]
            if word['left']-previous['left']-previous['width'] > max(45,word['height']*2):
                groups.append([])
            groups[-1].append(word)
        if len(groups)>=2:
            header_index=index
            break
    if header_index is None:
        plain=join_lines(words_text(words))
        if NOTES.search(plain) or re.search(r'^\[\d+\]:',plain,re.M):
            # Отдельная страница сносок содержит такие же критичные числа и
            # отрицания, как сама таблица. Не обходить порог уверенности OCR.
            return ('' if reliable(words) else UNCERTAIN+'\n')+plain,True
        # Повторяющиеся разнесённые колонки с числами выглядят как таблица,
        # но без явной шапки их связь не считается доказанной.
        separated=sum(any(b['left']-a['left']-a['width']>image_width*.08 for a,b in zip(line,line[1:]))
                      and any(re.search(r'\d',word['text']) for word in line) for line in lines)
        return (UNCERTAIN+'\n'+join_lines(words_text(words)),True) if separated>=2 else (None,False)
    lefts=[group[0]['left'] for group in groups]
    if len(groups)>20:
        return UNCERTAIN+'\n'+join_lines(words_text(words)),True
    bounds=[(max(w['left']+w['width'] for w in groups[col-1])+lefts[col])/2 for col in range(1,len(groups))]
    cells=[cell_type(0,col,1,col+1,' '.join(word['text'] for word in group)) for col,group in enumerate(groups)]
    issue=not all(reliable(group) for group in groups)
    body=[]; end=header_index+1
    for line in lines[header_index+1:]:
        buckets=[[] for _ in groups]
        if line[0]['text'].startswith('['):
            break
        crossing=any(any(word['left']<boundary<word['left']+word['width'] for boundary in bounds) for word in line)
        if crossing:
            # Обычный абзац после таблицы не становится продолжением ячейки.
            break
        for word in line:
            column=sum(word['left']+word['width']/2>boundary for boundary in bounds)
            buckets[column].append(word)
        if not buckets[0]:
            # Поддерживаем перенос значения в той же строке только при малом
            # межстрочном интервале. Это не новая строка с пустым предметом.
            if not body or line[0]['top']-body[-1]['bottom']>line[0]['height']*1.8:
                issue=True;break
            for column,bucket in enumerate(buckets):
                body[-1]['buckets'][column].extend(bucket)
            body[-1]['bottom']=max(word['top']+word['height'] for word in line)
        else:
            body.append({'buckets':buckets,'bottom':max(word['top']+word['height'] for word in line)})
        end+=1
    if not body or len(body)>79 or (len(body)+1)*len(groups)>500:
        issue=True
    for row,item in enumerate(body,1):
        for col,bucket in enumerate(item['buckets']):
            issue |= not reliable(bucket)
            if bucket and abs(bucket[0]['left']-lefts[col]) > max(25,bucket[0]['height']):
                issue=True
            cells.append(cell_type(row,col,row+1,col+1,' '.join(word['text'] for word in bucket)))
    before_words=[w for line in lines[:header_index] for w in line]
    after_words=[w for line in lines[end:] for w in line]
    issue |= any(not reliable(segment) for segment in (before_words,after_words) if segment)
    before=join_lines(words_text(before_words))
    after=join_lines(words_text(after_words))
    if issue:
        return UNCERTAIN+'\n'+join_lines(words_text(words)),True
    text=describe(cells,len(body)+1,len(groups),page,1)
    metadata.append({'page':page,'cells':cells,'rows':len(body)+1,'cols':len(groups),'ordinal':1,'text':text})
    return '\n'.join([before,text,after]),True


def compose_pages(pages, metadata, describe):
    """Объединяем только явный перенос последней строки в соседней странице."""
    output=list(pages); issue=False
    for number, text in enumerate(pages,1):
        pending=bool(PENDING.search(text))
        continuation=bool(re.search(r'Продолжение таблицы\s+\d+',text,re.I))
        tables=[item for item in metadata if item['page']==number]
        if continuation and (number==1 or not PENDING.search(pages[number-2])):
            issue=True
        if not pending:
            continue
        if number>=len(pages):
            issue=True;continue
        next_text=pages[number]
        captions=CAPTION.findall(text); following=CAPTION.findall(next_text)
        later=[item for item in metadata if item['page']==number+1]
        try:
            if len(set(captions))!=1 or set(captions)!=set(following) or not re.search(r'Продолжение таблицы',next_text,re.I):
                raise ValueError('scan_continuation_caption')
            if len(tables)!=1 or len(later)!=1:
                raise ValueError('scan_continuation_count')
            if PENDING.search(next_text):
                # Цепочка из трёх страниц требует другой схемы объединения.
                # Нельзя подтвердить только одну пару и потерять начало строки.
                raise ValueError('scan_continuation_chain')
            first,second=tables[0],later[0]
            a,b=deepcopy(first['cells']),second['cells']
            # Пока продолжение требует одноуровневой шапки и прямых ячеек.
            if first['cols']!=second['cols'] or second['rows']!=2 or any(c.bottom-c.top!=1 or c.right-c.left!=1 for c in a+b):
                raise ValueError('scan_continuation_shape')
            header=lambda cells:[c.text for c in sorted(cells,key=lambda c:c.left) if c.top==0]
            if header(a)!=header(b):
                raise ValueError('scan_continuation_headers')
            parent=sorted([c for c in a if c.top==first['rows']-1],key=lambda c:c.left)
            child=sorted([c for c in b if c.top==1],key=lambda c:c.left)
            if not CONTINUED.search(child[0].text) or CONTINUED.sub('',child[0].text).casefold()!=parent[0].text.casefold():
                raise ValueError('scan_continuation_subject')
            for old,new in zip(parent[1:],child[1:]):
                if re.search(r'[.!?]$',old.text) or not new.text:
                    raise ValueError('scan_continuation_complete_cell')
                old.text=old.text.rstrip()+' '+new.text.lstrip()
            merged=describe(a,first['rows'],first['cols'],number,first['ordinal'])
            merged=merged.replace(f'# Страница {number},',f'# Страницы {number} и {number+1},')
            if first['text'] not in output[number-1] or second['text'] not in output[number]:
                raise ValueError('scan_continuation_text_mismatch')
            output[number-1]=PENDING.sub('',output[number-1].replace(first['text'],merged))
            output[number]=output[number].replace(second['text'],'')
        except ValueError:
            issue=True
    # Номер таблицы явно задаёт область сносок. Без него остаётся область страницы.
    for index,text in enumerate(output):
        ids=set(CAPTION.findall(pages[index]))|set(NOTES.findall(pages[index]))
        if ids and re.search(r'\[(?:p\d+-)?\d+\]',text) and len([t for t in metadata if t['page']==index+1])>1:
            # Один печатный номер не доказывает, к какой из двух таблиц
            # относится сноска. В таком случае её область не угадывается.
            issue=True
        prefix='t'+next(iter(ids)) if len(ids)==1 else 'p'+str(index+1)
        output[index]=re.sub(r'\[(?:p\d+-)?(\d+)\]',lambda match:'['+prefix+'-'+match[1]+']',text)
    return (UNCERTAIN+'\n' if issue else '')+'\n\n'.join(output)
````

## tests/test_site_search_layout.py

SHA256: 3d7604be6bcf3619f77ae1c2c6a0caa4ce23aed4c1ba6a006074b24ea261cdd9

````python
"""Реальные растры и отрицательные проверки связей страниц, строк и сносок."""
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
from ai_core.site_scan_layout import compose_pages, rectify_page, borderless, UNCERTAIN
from ai_core.site_scan_tables import Cell, describe_table, reliable, words_text
from ai_core.site_documents import _join_visual_lines
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.layout_probe import load_sources, ORIGIN


def continued_table(page, subject, action):
    cells = [Cell(0, 0, 1, 1, 'Поставка'), Cell(0, 1, 1, 2, 'Действия'),
             Cell(1, 0, 2, 1, subject), Cell(1, 1, 2, 2, action)]
    text = describe_table(cells, 2, 2, page, 1)
    return {'page': page, 'cells': cells, 'rows': 2, 'cols': 2, 'ordinal': 1, 'text': text}


def continuation():
    first = continued_table(1, 'Тестовая', 'При повреждении уведомите')
    second = continued_table(2, 'Тестовая (продолжение)', 'координатора.')
    return [first, second], ['Таблица 23. Приёмка\n' + first['text'] + '\nПродолжение строки на следующей странице.',
                             'Продолжение таблицы 23\n' + second['text']]


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec, cls.sources, cls.extraction = load_sources()

    def test_real_rasters_frozen_answers_and_warm_cache(self):
        index = ClosedSiteIndex([ORIGIN])
        index.publish(self.sources, index.begin('layout'))
        for case in self.spec['cases']:
            with self.subTest(case=case['id']):
                answer = index.search(case['q'], Principal('a', 'staff'), case['id'], case['source'])
                self.assertEqual(answer['status'], case.get('status', 'answer'))
                for value in case.get('has', []):
                    self.assertIn(value.casefold(), answer['text'].casefold())
                for value in case.get('absent', []):
                    self.assertNotIn(value.casefold(), answer['text'].casefold())
                if answer['status'] == 'answer':
                    self.assertEqual({p['id'] for p in answer['evidence']}, {case['source']})
        with patch('ai_core.site_documents._scan_text', side_effect=AssertionError('OCR on cached request')):
            self.assertEqual(load_sources()[1], self.sources)

    def test_explicit_continuation_keeps_complete_sentence_and_provenance(self):
        tables, pages = continuation()
        result = compose_pages(pages, tables, describe_table)
        self.assertNotIn(UNCERTAIN, result)
        self.assertIn('Страницы 1 и 2', result)
        self.assertIn('При повреждении уведомите координатора.', result)
        self.assertNotIn('уведомите.', result)
        self.assertEqual(tables[0]['cells'][-1].text, 'При повреждении уведомите')

    def test_mismatch_or_absent_page_cannot_produce_partial_answer(self):
        for variant in ['caption', 'header', 'subject', 'complete', 'no-marker', 'last-page', 'text-mismatch', 'chain']:
            with self.subTest(variant=variant):
                tables, pages = continuation()
                if variant == 'caption': pages[1] = pages[1].replace('таблицы 23', 'таблицы 24')
                if variant == 'header': tables[1]['cells'][1].text = 'Срок'
                if variant == 'subject': tables[1]['cells'][2].text = 'Чужая (продолжение)'
                if variant == 'complete': tables[0]['cells'][3].text += '.'
                if variant == 'no-marker': pages[0] = pages[0].split('\nПродолжение строки')[0]
                if variant == 'last-page': pages = pages[:1]
                if variant == 'text-mismatch': tables[1]['text'] += ' отсутствующий текст'
                if variant == 'chain': pages[1] += '\nПродолжение строки на следующей странице.'
                self.assertIn(UNCERTAIN, compose_pages(pages, tables, describe_table))

    def test_caption_for_footnote_is_printed_not_generated(self):
        text = compose_pages(['# Страница 1, таблица 8: Товар\nСрок: 8 дней [1].',
                              'Сноски к таблице 8\n[1]: Исключение: срок 2 дня.'], [], describe_table)
        self.assertIn('[p1-1]', text)
        self.assertIn('[t8-1]:', text)
        # Одинаковые номера без печатной области остаются разными сносками.
        local = compose_pages(['Срок [1].\n[1]: 4 дня.', 'Срок [1].\n[1]: 9 дней.'], [], describe_table)
        self.assertIn('[p1-1]: 4 дня.', local)
        self.assertIn('[p2-1]: 9 дней.', local)

    def test_footnote_after_ocr_blank_line_remains_one_condition(self):
        text = _join_visual_lines('Сноски к таблице 8\n[1]: Исключение: если упаковка вскрыта, товар\n\nхранится 2 дня.')
        self.assertIn('\n[1]: Исключение: если упаковка вскрыта, товар хранится 2 дня.', text)

    def test_standalone_footnote_does_not_bypass_critical_number_confidence(self):
        words=[{'text':text,'confidence':confidence,'left':20+index*100,'top':30,'width':80,'height':20,
                'block_num':1,'par_num':1,'line_num':1}
               for index,(text,confidence) in enumerate([('[1]:',96),('Срок',96),('2',79),('дня.',96)])]
        text,handled=borderless(words,1000,Cell,describe_table,reliable,_join_visual_lines,words_text,1,[])
        self.assertTrue(handled)
        self.assertIn(UNCERTAIN,text)
        words[2]['confidence']=98
        text,handled=borderless(words,1000,Cell,describe_table,reliable,_join_visual_lines,words_text,1,[])
        self.assertNotIn(UNCERTAIN,text)

    def test_one_caption_for_multiple_tables_with_notes_is_ambiguous(self):
        first = continued_table(1, 'Первый', '4 дня [1]')
        second = continued_table(1, 'Второй', '8 дней [1]')
        text = compose_pages(['Таблица 8\n' + first['text'] + '\n' + second['text'] + '\n[1]: 2 дня.'],
                             [first, second], describe_table)
        self.assertIn(UNCERTAIN, text)

    def test_rectification_changes_only_a_visible_sheet(self):
        ordinary = np.full((700, 600), 255, np.uint8)
        cv2.rectangle(ordinary, (40, 150), (540, 500), 0, 3)
        self.assertTrue(np.array_equal(rectify_page(ordinary), ordinary))
        photo = np.full((900, 800), 70, np.uint8)
        cv2.fillConvexPoly(photo, np.array([[140,80],[680,150],[730,770],[70,830]]), 255)
        result = rectify_page(photo)
        self.assertLess(result.size, photo.size)
        self.assertGreater(float(result.mean()), 250)

    def test_changed_bad_revision_invalidates_old_answer_and_context(self):
        good = deepcopy(next(s for s in self.sources if s['id'] == 'lab_layout_continued'))
        bad = deepcopy(next(s for s in self.sources if s['id'] == 'lab_layout_wrong_continued'))
        good.update(id='changing', revision='1')
        bad.update(id='changing', revision='2')
        actor = Principal('a', 'staff')
        question = 'Что сделать при повреждении упаковки поставки Флюорит?'
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory) / 'index.sqlite3'
            runtime = DurableSearch([ORIGIN], location)
            runtime.publish([good], runtime.begin('first'))
            self.assertIn('уведомите старшего смены', runtime.query(question, actor, 'chat', 'changing', {'changing':'1'})['text'])
            runtime = DurableSearch([ORIGIN], location)
            self.assertEqual(runtime.query(question, actor, 'chat', 'changing', {'changing':'2'})['status'], 'processing')
            runtime.publish([bad], runtime.begin('second'))
            runtime = DurableSearch([ORIGIN], location)
            answer = runtime.query(question, actor, 'chat', 'changing', {'changing':'2'})
            self.assertEqual(answer['status'], 'unavailable')
            self.assertNotIn('уведомите старшего смены', answer['text'])


if __name__ == '__main__':
    unittest.main()
````

## evaluation/closed_search/layout_assets.py

SHA256: f2d8c74d849375d228f31e0aca75899ba48820797ef8051be6ab6abb976a9120

````python
"""Новые растровые fixtures: без рамок, перспектива, явное продолжение и сноски."""
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader
from .scan_assets import page

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'evaluation/closed_search/fixtures/layout'
PREVIEWS = ROOT / '.runtime/closed-search-lab-20261028/layout-previews'
FONT = 'C:/Windows/Fonts/arial.ttf'


def plain(title, lines):
    image = Image.new('RGB', (1800, 2100), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT, 34)
    draw.text((90, 70), title, font=font, fill='black')
    for number, text in enumerate(lines):
        draw.text((90, 260 + number * 60), text, font=font, fill='black')
    return image


def borderless(missing=False):
    image = plain('Учебная таблица сроков. Все правила вымышлены.', [])
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT, 34)
    rows = [('Товар', 'Срок хранения', 'Ответственный'),
            ('Амазонит', '' if missing else '6 календарных дней', 'Координатор'),
            ('Кианит', '14 календарных дней', 'Старший сотрудник')]
    for row, values in enumerate(rows):
        for col, text in enumerate(values):
            draw.text(([90, 620, 1220][col], 260 + row * 150), text, font=font, fill='black')
    return image


def photograph(image):
    # Контрольная фотография создаётся проективным преобразованием всего листа.
    corners = [(220, 180), (1780, 320), (1910, 2120), (90, 2260)]
    original = [(0, 0), (1800, 0), (1800, 2100), (0, 2100)]
    matrix, target = [], []
    for (x, y), (u, v) in zip(corners, original):
        matrix += [[x, y, 1, 0, 0, 0, -u*x, -u*y], [0, 0, 0, x, y, 1, -v*x, -v*y]]
        target += [u, v]
    coefficients = np.linalg.solve(np.array(matrix), np.array(target))
    return image.transform((2100, 2400), Image.Transform.PERSPECTIVE, coefficients,
                           resample=Image.Resampling.BICUBIC, fillcolor=(75, 75, 75))


def write(name, images):
    target = OUT / name
    if target.exists():
        raise ValueError('Frozen fixture exists: ' + name)
    pdf = canvas.Canvas(str(target), pagesize=(600, 700), invariant=1)
    for number, image in enumerate(images, 1):
        image.save(PREVIEWS / (target.stem + f'-{number}.png'))
        pdf.drawImage(ImageReader(image), 0, 0, 600, 700)
        pdf.showPage()
    pdf.save()
    assert not any((p.extract_text() or '').strip() for p in PdfReader(target).pages)


def continuation(title, subject, text, note=None):
    return page(title, [600, 1020], [120, 250], [(0,0,1,1,'Поставка'), (0,1,1,1,'Действия'),
               (1,0,1,1,subject), (1,1,1,1,text)], note=note)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    write('Таблица_без_рамок.pdf', [borderless()])
    write('Пустая_ячейка_без_рамок.pdf', [borderless(True)])
    photo = page('Таблица 3. Учебные сроки', [650,970], [120,150,150],
                 [(0,0,1,1,'Товар'),(0,1,1,1,'Срок хранения'),(1,0,1,1,'Альбит'),(1,1,1,1,'9 календарных дней'),
                  (2,0,1,1,'Гелиодор'),(2,1,1,1,'4 календарных дня')])
    write('Фото_с_перспективой.pdf', [photograph(photo)])
    start = continuation('Таблица 7. Приёмка поставок', 'Флюорит',
                         'При повреждении упаковки сделайте фото и уведомите',
                         'Продолжение строки на следующей странице.')
    write('Перенос_строки_Флюорит.pdf', [start, continuation('Продолжение таблицы 7', 'Флюорит (продолжение)', 'старшего смены.')])
    write('Неверный_перенос_Флюорит.pdf', [start, continuation('Продолжение таблицы 9', 'Флюорит (продолжение)', 'старшего смены.')])
    first = page('Таблица 8. Учебное хранение Данбурита', [650,970], [120,150],
                 [(0,0,1,1,'Товар'),(0,1,1,1,'Срок хранения'),(1,0,1,1,'Данбурит'),(1,1,1,1,'8 календарных дней [1]')])
    footnote = ['[1]: Исключение: если упаковка вскрыта, товар Данбурит', 'хранится 2 календарных дня.']
    write('Сноска_на_следующей_странице.pdf', [first, plain('Сноски к таблице 8', footnote)])
    write('Чужая_сноска_на_следующей_странице.pdf', [first, plain('Сноски к таблице 9', footnote)])
    docs = [{'id': 'lab_layout_' + identifier, 'name': name} for identifier, name in [
        ('borderless','Таблица_без_рамок.pdf'),('missing','Пустая_ячейка_без_рамок.pdf'),('photo','Фото_с_перспективой.pdf'),
        ('continued','Перенос_строки_Флюорит.pdf'),('wrong_continued','Неверный_перенос_Флюорит.pdf'),
        ('notes','Сноска_на_следующей_странице.pdf'),('wrong_notes','Чужая_сноска_на_следующей_странице.pdf')]]
    cases = [
        {'id':'borderless-first','source':docs[0]['id'],'q':'Сколько хранится товар Амазонит?','has':['6 календарных'],'absent':['14 календарных','Кианит']},
        {'id':'borderless-next','source':docs[0]['id'],'q':'Сколько хранится товар Кианит?','has':['14 календарных'],'absent':['6 календарных','Амазонит']},
        {'id':'borderless-recipient','source':docs[0]['id'],'q':'Кто отвечает за товар Амазонит?','has':['Координатор'],'absent':['Старший сотрудник']},
        {'id':'photo-first','source':docs[2]['id'],'q':'Сколько хранится товар Альбит?','has':['9 календарных'],'absent':['4 календарных','Гелиодор']},
        {'id':'photo-next','source':docs[2]['id'],'q':'Сколько хранится товар Гелиодор?','has':['4 календарных'],'absent':['9 календарных']},
        {'id':'continued','source':docs[3]['id'],'q':'Что сделать при повреждении упаковки поставки Флюорит?','has':['сделайте фото','уведомите старшего смены'],'absent':['уведомите.']},
        {'id':'note','source':docs[5]['id'],'q':'Упаковка вскрыта. Сколько хранится товар Данбурит?','has':['2 календарных'],'absent':['8 календарных']},
        {'id':'note-base','source':docs[5]['id'],'q':'Упаковка не вскрыта. Сколько хранится товар Данбурит?','has':['8 календарных'],'absent':['2 календарных']},
        {'id':'missing','source':docs[1]['id'],'q':'Сколько хранится товар Амазонит?','status':'unavailable','absent':['14 календарных','6 календарных']},
        {'id':'wrong-continuation','source':docs[4]['id'],'q':'Что сделать при повреждении упаковки поставки Флюорит?','status':'unavailable','absent':['уведомите старшего смены']},
        {'id':'wrong-note','source':docs[6]['id'],'q':'Упаковка вскрыта. Сколько хранится товар Данбурит?','status':'unavailable','absent':['8 календарных','2 календарных']}
    ]
    holdout = [
        {'id':'borderless-paraphrase','source':docs[0]['id'],'q':'Как долго можно держать товар Кианит?','has':['14 календарных'],'absent':['6 календарных']},
        {'id':'photo-paraphrase','source':docs[2]['id'],'q':'Какой срок хранения у Альбита?','has':['9 календарных'],'absent':['4 календарных']},
        {'id':'continued-paraphrase','source':docs[3]['id'],'q':'У поставки Флюорит повреждена упаковка. Что делать?','has':['сделайте фото','уведомите старшего смены']}
    ]
    (OUT/'spec.json').write_text(json.dumps({'documents':docs,'cases':cases,'holdout':holdout},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pdfs':7,'pages':11,'cases':11,'holdout':3,'directory':str(OUT)}))


if __name__ == '__main__':
    main()
````

## evaluation/closed_search/layout_probe.py

SHA256: 49c3b2d24d4fdcea85142e1f2813aa6ab65ee06f55ba93f8c0bc3a8fea87042f

````python
"""Независимые новые сканы; одинаковые названия в плохом/хорошем документе разделены выбором файла."""
import argparse
import base64
import json
import time
from pathlib import Path
from ai_core.site_documents import extract_document
from ai_core.site_search import ClosedSiteIndex, Principal

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'evaluation/closed_search/fixtures/layout'
LAB = ROOT / '.runtime/closed-search-lab-20261028'
ORIGIN = 'http://127.0.0.1:3040'


def load_sources():
    spec = json.loads((ASSETS/'spec.json').read_text(encoding='utf-8'))
    sources, extraction = [], []
    for document in spec['documents']:
        data = (ASSETS/document['name']).read_bytes()
        started = time.perf_counter()
        text, complete = extract_document('data:application/pdf;base64,'+base64.b64encode(data).decode(), document['name'])
        extraction.append({'id':document['id'],'seconds':round(time.perf_counter()-started,3),'complete':complete,'text':text})
        sources.append({'id':document['id'],'revision':'layout-v1','title':document['name'],'kind':'document',
                        'url':ORIGIN+'/materials/'+document['id'],'access':{'scope':'workspace'},'complete':complete,
                        'sections':[{'heading':document['name'],'text':text}]})
    return spec, sources, extraction


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label',default='layout-probe')
    parser.add_argument('--holdout',action='store_true')
    args = parser.parse_args()
    spec,sources,extraction=load_sources()
    index=ClosedSiteIndex([ORIGIN]);index.publish(sources,index.begin('layout'))
    rows=[]
    for case in spec['holdout' if args.holdout else 'cases']:
        answer=index.search(case['q'],Principal('a','staff'),case['id'],case['source'])
        errors=[]
        if answer['status']!=case.get('status','answer'):errors.append('status:'+answer['status'])
        for text in case.get('has',[]):
            if text.casefold() not in answer['text'].casefold():errors.append('missing:'+text)
        for text in case.get('absent',[]):
            if text.casefold() in answer['text'].casefold():errors.append('unexpected:'+text)
        if answer['status']=='answer' and {part['id'] for part in answer['evidence']}!={case['source']}:errors.append('source')
        rows.append({'id':case['id'],'question':case['q'],'pass':not errors,'errors':errors,'answer':answer})
    report={'passed':sum(row['pass'] for row in rows),'total':len(rows),'extraction':extraction,'cases':rows}
    output=LAB/(args.label+'-'+str(time.time_ns())+'.json')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(output),'passed':report['passed'],'total':report['total'],'failures':[row for row in rows if not row['pass']]},ensure_ascii=False))
    if report['passed']!=report['total']:raise SystemExit(1)


if __name__=='__main__':main()
````

## evaluation/closed_search/layout_http.cjs

SHA256: 629da00be876d760e752000dcf59a11b453a2fbc4a3d9d10b5f612f2bd5f8aad

````javascript
/* Сквозная приёмка: настоящие PDF, сайт, авторизация, OCR и собственный поиск. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const assets=path.join(__dirname,'fixtures/layout');
const negative=new Set(['lab_layout_missing','lab_layout_wrong_continued','lab_layout_wrong_notes']);
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(assets,'spec.json'),'utf8'));
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
    for(const item of spec.documents){
        const bytes=await fs.readFile(path.join(assets,item.name));
        const data='data:application/pdf;base64,'+bytes.toString('base64');
        // Намеренно испорченные документы видит только испытательный администратор.
        // Они не должны влиять на обычный поиск сотрудника по тем же названиям.
        const entry={...item,hasSeparateData:true,section:'Проверка страниц ВатэсAi',size:bytes.length+' B',date:'Локальный тест',
            ...(negative.has(item.id)?{adminOnly:true}:{})};
        const prior=catalog.find(doc=>doc.id===item.id);
        if(prior){
            if(JSON.stringify(prior)!==JSON.stringify(entry)||await storage('file_data_'+item.id,cookies.admin)!==data)throw new Error('Layout fixture differs '+item.id);
        }else{
            await storage('file_data_'+item.id,cookies.admin,data);
            catalog.push(entry);
        }
    }
    await storage('tea_hub_urgent_files_v1',cookies.admin,catalog);
    const ready=await waitSnapshot();
    const cases=[...spec.cases,...spec.holdout,
        {id:'summary-borderless',source:'lab_layout_borderless',q:'Кратко перескажи выбранный файл',has:['Амазонит','6 календарных','Кианит','14 календарных']},
        {id:'summary-continued',source:'lab_layout_continued',q:'Кратко перескажи выбранный файл',has:['сделайте фото','уведомите старшего смены'],absent:['уведомите.']},
        {id:'summary-note',source:'lab_layout_notes',q:'Кратко перескажи выбранный файл',has:['8 календарных','2 календарных','если упаковка вскрыта'],absent:['[t8-1]']},
        {id:'a-denied-bad',actor:'a',source:'lab_layout_missing',q:'Сколько хранится товар Амазонит?',status:'not_found',absent:['6 календарных','14 календарных']},
        {id:'b-denied-bad',actor:'b',source:'lab_layout_wrong_notes',q:'Упаковка вскрыта. Сколько хранится товар Данбурит?',status:'not_found',absent:['2 календарных','8 календарных']}];
    const rows=[];
    for(const [position,item] of cases.entries()){
        const actor=item.actor||(negative.has(item.source)?'admin':['a','b'][position%2]),started=performance.now();
        const {response,value}=await call('/api/ai-chat',{chatId:'layout-'+Date.now()+'-'+item.id,documentId:item.source,
            messages:[{role:'user',content:item.q}]},cookies[actor]);
        const answer=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(value.search_status!==(item.status||'answer'))errors.push('status:'+value.search_status);
        for(const text of item.has||[])if(!answer.toLowerCase().includes(text.toLowerCase()))errors.push('missing:'+text);
        for(const text of item.absent||[])if(answer.toLowerCase().includes(text.toLowerCase()))errors.push('unexpected:'+text);
        if(value.search_status==='answer'&&(!(value.sourceLinks||[]).length||value.sourceLinks.some(link=>!link.url.includes(item.source))))errors.push('source');
        if(value.search_status!=='answer'&&value.sourceLinks?.length)errors.push('links-on-refusal');
        rows.push({id:item.id,actor,question:item.q,answer,status:value.search_status,links:value.sourceLinks,ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    const report={createdAt:new Date().toISOString(),ready,total:rows.length,passed:rows.filter(row=>row.pass).length,cases:rows};
    const file=path.join(lab,'layout-http-'+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,total:report.total,passed:report.passed,failures:rows.filter(row=>!row.pass)}));
    if(report.passed!==report.total)process.exitCode=1;
}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1;});
module.exports={assets};
````

## evaluation/closed_search/layout_revisions.cjs

SHA256: 8c66cda939db17a7bd390eba3cf12ab2c433d0d4f5d6128afff04fae5d7a6ce6

````javascript
/* Смена байтов и прав у одного документа не должна сохранять прежние ответы. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const {assets}=require('./layout_http.cjs');
const identifier='lab_layout_revision_probe';
async function main(){
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    if((await storage('tea_hub_urgent_files_v1',cookies.admin)).some(item=>item.id===identifier))throw new Error('Unfinished layout revision fixture exists');
    const rows=[],chat='layout-revision-'+Date.now();
    const entry={id:identifier,name:'Временный_многостраничный_регламент.pdf',section:'Проверка смены редакций',hasSeparateData:true,allowedUserIds:['closed_lab_a']};
    async function change(update){
        const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
        await storage('tea_hub_urgent_files_v1',cookies.admin,update(catalog));
    }
    async function replace(name){
        const data='data:application/pdf;base64,'+(await fs.readFile(path.join(assets,name))).toString('base64');
        await storage('file_data_'+identifier,cookies.admin,data);
    }
    async function ask(id,{actor='a',q='Сколько хранится товар Амазонит?',statuses=['answer'],has=[],absent=[]}={}){
        const {response,value}=await call('/api/ai-chat',{chatId:chat,documentId:identifier,messages:[{role:'user',content:q}]},cookies[actor]);
        const answer=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(!statuses.includes(value.search_status))errors.push('status:'+value.search_status);
        for(const text of has)if(!answer.includes(text))errors.push('missing:'+text);
        for(const text of absent)if(answer.includes(text))errors.push('unexpected:'+text);
        if(value.search_status==='answer'&&(!(value.sourceLinks||[]).length||value.sourceLinks.some(link=>!link.url.includes(identifier))))errors.push('source');
        if(value.search_status!=='answer'&&value.sourceLinks?.length)errors.push('links-on-refusal');
        rows.push({id,actor,question:q,answer,status:value.search_status,links:value.sourceLinks,pass:!errors.length,errors});
        console.log(JSON.stringify({id,pass:!errors.length,status:value.search_status,errors}));
    }
    let completed=false,cleanup=false,failure=null;
    try{
        await replace('Таблица_без_рамок.pdf');await change(catalog=>[...catalog,entry]);await waitSnapshot();
        await ask('borderless-v1',{has:['6 календарных']});
        await ask('b-denied',{actor:'b',statuses:['not_found'],absent:['6 календарных']});
        await replace('Пустая_ячейка_без_рамок.pdf');
        await ask('missing-immediate',{statuses:['processing','unavailable'],absent:['6 календарных','14 календарных']});
        await waitSnapshot();
        await ask('missing-ready',{statuses:['unavailable'],absent:['6 календарных','14 календарных']});
        await replace('Таблица_без_рамок.pdf');await waitSnapshot();
        await ask('borderless-restored',{has:['6 календарных']});
        const q='Что сделать при повреждении упаковки поставки Флюорит?';
        await replace('Перенос_строки_Флюорит.pdf');await waitSnapshot();
        await ask('continued-v1',{q,has:['уведомите старшего смены']});
        await replace('Неверный_перенос_Флюорит.pdf');
        await ask('continued-broken-immediate',{q,statuses:['processing','unavailable'],absent:['уведомите старшего смены']});
        await waitSnapshot();
        await ask('continued-broken-ready',{q,statuses:['unavailable'],absent:['уведомите старшего смены']});
        await replace('Перенос_строки_Флюорит.pdf');await waitSnapshot();
        await ask('continued-restored',{q,has:['уведомите старшего смены']});
        await change(catalog=>catalog.map(item=>item.id===identifier?{...item,allowedUserIds:['closed_lab_b']}:item));
        await ask('a-revoked-immediate',{q,statuses:['processing','not_found'],absent:['уведомите старшего смены']});
        await waitSnapshot();
        await ask('a-revoked-ready',{q,statuses:['not_found'],absent:['уведомите старшего смены']});
        await ask('b-new-access',{actor:'b',q,has:['уведомите старшего смены']});
        await change(catalog=>catalog.filter(item=>item.id!==identifier));
        await ask('deleted-immediate',{actor:'b',q,statuses:['processing','not_found'],absent:['уведомите старшего смены']});
        await waitSnapshot();
        await ask('deleted-ready',{actor:'b',q,statuses:['not_found'],absent:['уведомите старшего смены']});
        const denied=await call('/api/storage?key=file_data_'+identifier,undefined,cookies.b);
        rows.push({id:'deleted-bytes-denied',http:denied.response.status,pass:denied.response.status===403});
        completed=true;
    }catch(error){failure=error.message;throw error;}
    finally{
        try{
            // Только временная запись этого сценария; каталог пользователя сохраняется.
            await change(catalog=>catalog.filter(item=>item.id!==identifier));
            await storage('file_data_'+identifier,cookies.admin,null);await waitSnapshot();cleanup=true;
        }finally{
            const report={createdAt:new Date().toISOString(),completed,cleanup,failure,total:rows.length,passed:rows.filter(row=>row.pass).length,cases:rows};
            const file=path.join(lab,'layout-revisions-'+Date.now()+'.json');
            await fs.writeFile(file,JSON.stringify(report,null,2));
            console.log(JSON.stringify({file,completed,cleanup,total:report.total,passed:report.passed}));
            if(!completed||!cleanup||report.passed!==report.total)process.exitCode=1;
        }
    }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
````

## evaluation/closed_search/layout_performance.py

SHA256: e551c8c7c9c4472a24b4ce38b5793204dad8245995ae7457f9b841cb0688ddf8

````python
"""Отдельный холодный кэш и повторные локальные запросы без OCR на пути ответа."""
import hashlib
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_documents import PARSER_VERSION
from .layout_probe import ROOT, LAB, ORIGIN, load_sources


def main():
    with tempfile.TemporaryDirectory(prefix='vates-layout-cache-check-') as directory:
        with patch('ai_core.site_documents.CACHE',Path(directory)):
            started=time.perf_counter()
            spec,sources,extraction=load_sources()
            cold=time.perf_counter()-started
            with patch('ai_core.site_documents._scan_text',side_effect=AssertionError('OCR repeated')):
                started=time.perf_counter()
                cached=load_sources()[1]
                warm=time.perf_counter()-started
            if sources!=cached: raise AssertionError('Cache content differs')
    index=ClosedSiteIndex([ORIGIN]);index.publish(sources,index.begin('layout-performance'))
    timings,failures=[],[]
    with patch('ai_core.site_documents._scan_text',side_effect=AssertionError('OCR on query')):
        for cycle in range(20):
            for case in spec['cases']+spec['holdout']:
                started=time.perf_counter()
                answer=index.search(case['q'],Principal('a','staff'),str(cycle)+case['id'],case['source'])
                timings.append((time.perf_counter()-started)*1000)
                valid=answer['status']==case.get('status','answer')
                valid &= all(text.casefold() in answer['text'].casefold() for text in case.get('has',[]))
                valid &= all(text.casefold() not in answer['text'].casefold() for text in case.get('absent',[]))
                if not valid:failures.append({'case':case['id'],'cycle':cycle})
    timings.sort()
    report={'mode':'seven-synthetic-pdfs-eleven-raster-pages-local-query-no-http','parser':PARSER_VERSION,
            'coldSeconds':round(cold,3),'warmSeconds':round(warm,4),
            'files':[{key:value for key,value in item.items() if key!='text'} for item in extraction],
            'requests':len(timings),'failures':failures,'queryP50Ms':round(timings[len(timings)//2],2),
            'queryP95Ms':round(timings[int(len(timings)*.95)-1],2),
            'engineHashes':{name:hashlib.sha256((ROOT/'ai_core'/name).read_bytes()).hexdigest() for name in
                            ['site_documents.py','site_scan_tables.py','site_scan_layout.py','site_search_structure.py',
                             'site_search_reasoning.py','site_search_runtime.py','site_search_store.py','site_search_api.py']}}
    output=LAB/('layout-performance-'+str(time.time_ns())+'.json')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(output),**report},ensure_ascii=False))
    if failures:raise SystemExit(1)


if __name__=='__main__':main()
````

## evaluation/closed_search/layout_visual_qa.py

SHA256: 37df0567542b8d8a9df1c9093dc7317817b4630bcfef4a748ae6ebb2c6f26923

````python
"""Рендер всех новых PDF через Poppler и контактные листы для ручной проверки."""
import json
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[2]
ASSETS=ROOT/'evaluation/closed_search/fixtures/layout'
OUT=ROOT/'.runtime/closed-search-lab-20261028/layout-rendered'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    renderer=shutil.which('pdftoppm')
    if not renderer: raise RuntimeError('Poppler is required')
    spec=json.loads((ASSETS/'spec.json').read_text(encoding='utf-8'))
    rendered=[]
    for document in spec['documents']:
        target=OUT/document['id']
        subprocess.run([renderer,'-r','100','-png',str(ASSETS/document['name']),str(target)],
                       check=True,capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
        for path in sorted(OUT.glob(document['id']+'-*.png')):
            rendered.append((document['name'],path))
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',16)
    sheets=[]
    for start in range(0,len(rendered),4):
        sheet=Image.new('RGB',(1240,1580),'#dddddd')
        draw=ImageDraw.Draw(sheet)
        for offset,(name,path) in enumerate(rendered[start:start+4]):
            image=Image.open(path).convert('RGB')
            image.thumbnail((600,740))
            x,y=(offset%2)*620,(offset//2)*790
            sheet.paste(image,(x+10,y+40))
            draw.text((x+10,y+8),name,font=font,fill='black')
        filename=OUT/f'contact-{start//4+1}.png'
        sheet.save(filename);sheets.append(str(filename))
    print(json.dumps({'pages':len(rendered),'sheets':sheets},ensure_ascii=False))


if __name__=='__main__':main()
````

## evaluation/closed_search/verify_lab.cjs

SHA256: 7f9d0160712741543ab758b39492f3798b861ef1a109c12510efe6624abf638e

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
    const expectedDocuments = 9 + reasoningFixtures.documents.length + conditionFixtures.documents.length + structureFixtures.documents.length + structureAssets.length + scanFixtures.documents.length + layoutFixtures.documents.length;
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

## evaluation/closed_search/fixtures/layout/spec.json

SHA256: 542b0daea11838abb0fe2edcfdab04a7a9865487d96de526828d640c95af2137

````json
{
  "documents": [
    {
      "id": "lab_layout_borderless",
      "name": "Таблица_без_рамок.pdf"
    },
    {
      "id": "lab_layout_missing",
      "name": "Пустая_ячейка_без_рамок.pdf"
    },
    {
      "id": "lab_layout_photo",
      "name": "Фото_с_перспективой.pdf"
    },
    {
      "id": "lab_layout_continued",
      "name": "Перенос_строки_Флюорит.pdf"
    },
    {
      "id": "lab_layout_wrong_continued",
      "name": "Неверный_перенос_Флюорит.pdf"
    },
    {
      "id": "lab_layout_notes",
      "name": "Сноска_на_следующей_странице.pdf"
    },
    {
      "id": "lab_layout_wrong_notes",
      "name": "Чужая_сноска_на_следующей_странице.pdf"
    }
  ],
  "cases": [
    {
      "id": "borderless-first",
      "source": "lab_layout_borderless",
      "q": "Сколько хранится товар Амазонит?",
      "has": [
        "6 календарных"
      ],
      "absent": [
        "14 календарных",
        "Кианит"
      ]
    },
    {
      "id": "borderless-next",
      "source": "lab_layout_borderless",
      "q": "Сколько хранится товар Кианит?",
      "has": [
        "14 календарных"
      ],
      "absent": [
        "6 календарных",
        "Амазонит"
      ]
    },
    {
      "id": "borderless-recipient",
      "source": "lab_layout_borderless",
      "q": "Кто отвечает за товар Амазонит?",
      "has": [
        "Координатор"
      ],
      "absent": [
        "Старший сотрудник"
      ]
    },
    {
      "id": "photo-first",
      "source": "lab_layout_photo",
      "q": "Сколько хранится товар Альбит?",
      "has": [
        "9 календарных"
      ],
      "absent": [
        "4 календарных",
        "Гелиодор"
      ]
    },
    {
      "id": "photo-next",
      "source": "lab_layout_photo",
      "q": "Сколько хранится товар Гелиодор?",
      "has": [
        "4 календарных"
      ],
      "absent": [
        "9 календарных"
      ]
    },
    {
      "id": "continued",
      "source": "lab_layout_continued",
      "q": "Что сделать при повреждении упаковки поставки Флюорит?",
      "has": [
        "сделайте фото",
        "уведомите старшего смены"
      ],
      "absent": [
        "уведомите."
      ]
    },
    {
      "id": "note",
      "source": "lab_layout_notes",
      "q": "Упаковка вскрыта. Сколько хранится товар Данбурит?",
      "has": [
        "2 календарных"
      ],
      "absent": [
        "8 календарных"
      ]
    },
    {
      "id": "note-base",
      "source": "lab_layout_notes",
      "q": "Упаковка не вскрыта. Сколько хранится товар Данбурит?",
      "has": [
        "8 календарных"
      ],
      "absent": [
        "2 календарных"
      ]
    },
    {
      "id": "missing",
      "source": "lab_layout_missing",
      "q": "Сколько хранится товар Амазонит?",
      "status": "unavailable",
      "absent": [
        "14 календарных",
        "6 календарных"
      ]
    },
    {
      "id": "wrong-continuation",
      "source": "lab_layout_wrong_continued",
      "q": "Что сделать при повреждении упаковки поставки Флюорит?",
      "status": "unavailable",
      "absent": [
        "уведомите старшего смены"
      ]
    },
    {
      "id": "wrong-note",
      "source": "lab_layout_wrong_notes",
      "q": "Упаковка вскрыта. Сколько хранится товар Данбурит?",
      "status": "unavailable",
      "absent": [
        "8 календарных",
        "2 календарных"
      ]
    }
  ],
  "holdout": [
    {
      "id": "borderless-paraphrase",
      "source": "lab_layout_borderless",
      "q": "Как долго можно держать товар Кианит?",
      "has": [
        "14 календарных"
      ],
      "absent": [
        "6 календарных"
      ]
    },
    {
      "id": "photo-paraphrase",
      "source": "lab_layout_photo",
      "q": "Какой срок хранения у Альбита?",
      "has": [
        "9 календарных"
      ],
      "absent": [
        "4 календарных"
      ]
    },
    {
      "id": "continued-paraphrase",
      "source": "lab_layout_continued",
      "q": "У поставки Флюорит повреждена упаковка. Что делать?",
      "has": [
        "сделайте фото",
        "уведомите старшего смены"
      ]
    }
  ]
}
````
