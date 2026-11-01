# Полный код этапа качества сканов — 29.10.2026

Исходники из C:/Users/KROLL/Desktop/Ai. Все 14 файлов приведены целиком. Секретов нет, публикация не выполнялась.

## ai_core/site_documents.py

SHA256: cd57ce9bce246c318ecd2917edab7c937e67e38141f5fa819b99cf88b95cc33a

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
PARSER_VERSION = "closed-documents-v6-scan-quality-" + hashlib.sha256(
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

SHA256: c305f34b8ec2972182e34511bf935693d0d96a3737c479d3c789035f8a167499

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
from .site_scan_quality import normalize_lighting, clipped_content, unrecognized_ink, critical_key, critical_consistent

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
    critical = [word for word in words if critical_key(word['text']) is not None]
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
            # Уверенность OCR относится только к найденным буквам: цифра
            # может исчезнуть целиком. Единица измерения без количества
            # в числовой колонке не является полноценным значением.
            numeric_label=re.search(r'срок|количество|цен[аы]|масса|температура', ' '.join(labels), re.I)
            unit=re.search(r'\b(?:дней|дня|часов|часа|минут|минуты|рублей|рубля|штук|штуки|кг|грамм)\b|°',line[col].text,re.I)
            quantity=re.search(r'\d|\b(?:один|одн\w*|дв[аеу]\w*|тр[иеё]\w*|четыр\w*|пят\w*|шест\w*|сем\w*|восем\w*|девят\w*|десят\w*|сорок\w*|сто|сот\w*|полтор\w*|полутор\w*|нескольк\w*|пар[ау])\b',line[col].text,re.I)
            if numeric_label and unit and not quantity:
                raise ValueError('scan_quantity_missing')
            sentence = subject + '; ' + ' / '.join(labels) + ': ' + line[col].text
            if predicates:
                sentence = predicates[0] + ', ' + sentence[0].lower() + sentence[1:]
            sentences.append(sentence.rstrip('.') + '.')
    return '\n'.join(f'# Страница {page}, таблица {ordinal}: {subject}\n' + (period + '\n' if period else '') + ' '.join(sentences)
                     for (subject, period), sentences in by_subject.items())


def read_checked_words(image,path,ocr_tsv):
    """Не выбираем удобный результат: расхождение двух чтений закрывает ответ."""
    cv2.imencode('.png',image)[1].tofile(str(path))
    words=parse_words(ocr_tsv(path))
    binary=cv2.threshold(image,0,255,cv2.THRESH_BINARY|cv2.THRESH_OTSU)[1]
    alternative=Path(path).with_name(Path(path).stem+'-binary.png')
    cv2.imencode('.png',binary)[1].tofile(str(alternative))
    checked=parse_words(ocr_tsv(alternative))
    return words,critical_consistent(words,checked)


def extract_scan(path, ocr_tsv, join_lines, page=1, metadata=None):
    """Возвращает текст и наличие таблицы. Координаты нужны только на этапе OCR."""
    metadata = [] if metadata is None else metadata
    image = normalize_lighting(rectify_page(read_image(path)))
    # Пустой белый оборот листа не является повреждённым правилом. Для
    # серой/размытой страницы этот короткий путь не применяется.
    if float(image.mean())>250 and float(image.std())<.5:
        return '',True
    clipped = clipped_content(image)
    image = deskew(image)
    ink = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, max(35, image.shape[1] // 35)), np.uint8))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((max(35, image.shape[0] // 50), 1), np.uint8))
    grid = cv2.bitwise_or(horizontal, vertical)
    contours = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    boxes = sorted((cv2.boundingRect(contour) for contour in contours), key=lambda box: (box[1], box[0]))
    boxes = [box for box in boxes if box[2] > image.shape[1] * .25 and box[3] > 70]
    if not boxes:
        ocr_path = Path(path).with_name(Path(path).stem + '-layout.png')
        words,consistent = read_checked_words(image,ocr_path,ocr_tsv)
        text,handled=borderless(words,image.shape[1],Cell,describe_table,reliable,join_lines,words_text,page,metadata)
        issue=clipped or not consistent or not reliable(words) or unrecognized_ink(image,words)
        # Обычный абзац проходит те же проверки, что и ячейка. Прежний
        # повторный OCR без confidence больше не обходит сомнительное «не».
        if not handled:
            text=join_lines(words_text(words))
        return (UNCERTAIN+'\n' if issue else '')+(text or ''),True
    if len(boxes) > 12:
        raise ValueError('scan_tables_limit')
    clean = image.copy()
    clean[cv2.dilate(grid, np.ones((3, 3), np.uint8)) > 0] = 255
    ocr_path = Path(path).with_name(Path(path).stem + '-cells.png')
    words,consistent = read_checked_words(clean,ocr_path,ocr_tsv)
    blocks, used, issue = [], set(), clipped or not consistent or unrecognized_ink(clean,words)
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

## ai_core/site_scan_quality.py

SHA256: 687c114c3e42e0adb7d8e286c6a9c842082fbf59ca75dd0851a415d836c7cb5f

````python
"""Проверка видимых потерь скана. Не восстанавливает цифры и запрещающие слова."""
from __future__ import annotations
import cv2
import numpy as np
import re


def critical_key(text):
    key=text.casefold().strip('.,:;!?()[]{}«»\"\'')
    return key if re.search(r'\d',key) or re.fullmatch(r'не|нет|ни|нельзя|без|запрещ\w*',key) else None


def critical_consistent(first,second):
    """Два вида одного изображения не должны давать разные цифры/отрицания."""
    for source,target in ((first,second),(second,first)):
        for word in source:
            key=critical_key(word['text'])
            if key is None:
                continue
            if word['confidence']<80:
                return False
            matched=False
            for other in target:
                if critical_key(other['text'])!=key:
                    continue
                w=max(0,min(word['left']+word['width'],other['left']+other['width'])-max(word['left'],other['left']))
                h=max(0,min(word['top']+word['height'],other['top']+other['height'])-max(word['top'],other['top']))
                if w*h>=.25*min(word['width']*word['height'],other['width']*other['height']):
                    matched=True
                    break
            if not matched:
                return False
    return True


def normalize_lighting(image):
    """Выравниваем плавную тень по оценке бумаги, сохраняя исходные штрихи."""
    background=cv2.morphologyEx(image,cv2.MORPH_CLOSE,np.ones((61,61),np.uint8))
    low,high=np.percentile(background,[5,95])
    if high-low<50 or low>190:
        return image
    # Деление на яркость бумаги не дорисовывает отсутствующие символы.
    return cv2.divide(image,np.maximum(background,1),scale=255)


def ink_mask(image):
    return cv2.threshold(image,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)[1]


def clipped_content(image):
    """Штрихи текста/таблицы упираются в край: начало или конец строки потеряны."""
    height,width=image.shape
    margin=max(3,round(min(height,width)*.002))
    count,labels,stats,centroids=cv2.connectedComponentsWithStats(ink_mask(image),8)
    for x,y,w,h,area in stats[1:]:
        if area<12 or h<6 or w<2:
            continue
        # Тонкая рамка самого снимка не является словом. Текст, соединённый
        # с обрезанной сеткой таблицы, наоборот, должен закрывать ответ.
        if h>height*.85 or w>width*.98 and h<margin*3:
            continue
        if x<=margin or y<=margin or x+w>=width-margin or y+h>=height-margin:
            return True
    return False


def unrecognized_ink(image,words):
    """OCR может пропустить символ целиком, не дав ему низкую уверенность."""
    ink=ink_mask(image)
    coverage=np.zeros(image.shape,np.uint8)
    height,width=image.shape
    for word in words:
        x,y=word['left'],word['top']
        coverage[max(0,y-2):min(height,y+word['height']+2),max(0,x-2):min(width,x+word['width']+2)]=1
    count,labels,stats,centroids=cv2.connectedComponentsWithStats(ink,8)
    for label,(x,y,w,h,area) in enumerate(stats[1:],1):
        if area<15 or h<7 or w<2 or w/max(h,1)>20 or h/max(w,1)>20:
            continue
        component=labels[y:y+h,x:x+w]==label
        missing=np.count_nonzero(component & (coverage[y:y+h,x:x+w]==0))
        if missing/area>.55:
            return True
    return False
````

## ai_core/site_search.py

SHA256: c8cacffd7fd2d479806c6c3768f9e85b36f80f10d528edf152c39a33b8b341ad

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
from .site_search_reasoning import authority, compound_parts, conflicting_passages, validate_authority
from .site_search_conditions import guarded, paragraphs, rules, atoms, atomic_observation, resolve, summary_request, operation_names
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
        source_scores = {}
        def body_score(text):
            tokens = set(terms(text))
            return sum(any(affinity(word, token) for token in tokens) for word in focus_words)
        # Для названного объекта используем готовый обратный индекс. Проверка
        # тех же слов во всех условиях корпуса повторяла тысячи сравнений.
        positions = {self.passage_positions[id(item)] for item in candidates} & self.condition_positions
        for name in named:
            positions.intersection_update(self._matching_passages(name, positions))
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
            own_rules = rules(item.text, self._sentences, facet, supplies_facet)
            if max((body_score(rule.body) for rule in own_rules if rule.condition), default=-1) < source_scores[identifier]:
                continue
            previous = (session or {}).get('condition_facts', {}).get(item.source['id'], {})
            decision = resolve(item.text, question, terms, self._sentences, facet, supplies_facet, previous, affinity)
            if decision is not None:
                matches.append((title_hits, item, decision))
        if not matches:
            return None
        best_hits = max(row[0] for row in matches)
        matches = [row for row in matches if row[0] == best_hits]
        if any(not row[2].get('inapplicable') for row in matches):
            matches = [row for row in matches if not row[2].get('inapplicable')]
        facts = {item.source['id']: decision['facts'] for _, item, decision in matches}
        identifiers = list(dict.fromkeys(item.source['id'] for _, item, _ in matches))
        # Факты хранятся как истинность условий документа, а не текст пользователя.
        topic_words = list(dict.fromkeys(word for _, item, _ in matches for word in terms(item.source['title'])))
        unresolved = [row for row in matches if row[2]['status'] == 'clarify']
        if unresolved:
            self._remember(sessions, scope, identifiers, topic_words, facts=facts, facet=facet, pending=True)
            return {'status': 'clarify', 'text': unresolved[0][2]['text'], 'links': [], 'evidence': [], 'context_used': bool(session)}
        # Сравниваем факты только реально применимых веток. Их условия уже проверены.
        effective = [Passage(item.source, item.heading, rule.body, Counter(), Counter())
                     for _, item, decision in matches for rule in decision['selected']]
        if conflicting_passages(effective, self._sentences, terms):
            return {'status': 'conflict', 'text': CONFLICT, 'links': [], 'evidence': []}
        highest = max(authority(item.source) for _, item, _ in matches)
        matches = [row for row in matches if authority(row[1].source) == highest]
        _, best, decision = matches[0]
        quotes = list(dict.fromkeys(rule.quote for rule in decision['selected']))
        if sum(map(len, quotes)) > 1600:
            return {'status': 'clarify', 'text': 'Уточните конкретную операцию: для неё указано несколько условий.', 'links': [], 'evidence': []}
        self._remember(sessions, scope, [best.source['id']], terms(best.source['title']),
                       facts={best.source['id']: decision['facts']}, facet=facet)
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
        local_sessions = {scope: dict(self.sessions[scope])} if scope in self.sessions else {}
        results = []
        for part in parts:
            result = self._search_single(part, principal, chat_id, selected_source, current_revisions, local_sessions)
            if result["status"] in {"conflict", "processing", "unavailable"}:
                return result
            results.append(result)
        answered = [(part, result) for part, result in zip(parts, results) if result["status"] == "answer"]
        if not answered:
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
        missing = [part for part, result in zip(parts, results) if result["status"] != "answer"]
        if missing:
            texts.append("Не нашёл однозначных данных по вопросу: " + " ".join('«' + part + '»' for part in missing))
        self.sessions[scope] = {"time": self.clock(), "generation": self.generation,
                                "terms": self._query_terms(" ".join(part for part, _ in answered))[:24],
                                "ids": list(dict.fromkeys(item["id"] for item in evidence))}
        if len(self.sessions) > 1000:
            del self.sessions[min(self.sessions, key=lambda key: self.sessions[key]["time"])]
        return {"status": "partial" if missing else "answer", "text": "\n".join(texts), "links": links,
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
        current_words = self._query_terms(question)[:32]
        query_normal = normal(question)
        facet = requested_facet(question)
        hypothetical_amount = bool(re.search(r"можно|допустим|разреш", query_normal) and re.search(r"грамм|\bг\b|килограмм|\bкг\b|литр", query_normal))
        if hypothetical_amount:
            current_words = [word for word in current_words if not word.replace('.', '').replace(',', '').isdigit()]
        want_speech = bool(re.search(r"говорить|сказать|словами|фразы|фразу", query_normal))
        want_steps = bool(re.search(r"что (сделать|делать)|порядок|этапы|шаги|действия", query_normal))
        want_overview = bool(re.search(r"как (устроен|работает)|что такое", query_normal))
        raw_words = [word for word in normal(question).split() if word not in STOP]
        reference = bool(re.search(r"\b(это|этого|этой|там|тогда|него|нее|ней|этом)\b", normal(question))) or (normal(question).startswith("а ") and len(raw_words) <= 3)
        generic = not raw_words or all(word in FOLLOWUP for word in raw_words)
        context_used = bool(session and (reference or generic))
        condition_reply = False
        if session and (session.get('pending_conditions') or session.get('condition_facts')):
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
        named = [term for match in re.finditer(r"[А-ЯЁA-Z][а-яёa-zА-ЯЁA-Z0-9-]+", question.strip())
                 if match.start() > 0 and match.group().casefold() not in STOP for term in terms(match.group())][:16]
        numbers = re.findall(r"\d+(?:[.,]\d+)*", question)[:16]
        candidates = [item for item in self.passages if item.source["id"] in allowed and (not selected_source or item.source["id"] == selected_source)
                      and active(item.structure or {}, day)]
        if context_used:
            candidates = [item for item in candidates if item.source['id'] in session['ids']]
        # Не выдаём разрешение из повреждённого раздела, даже если найдено
        # достаточно слов. Скрытые материалы здесь уже исключены проверкой ACL.
        for item in candidates:
            if not (item.structure or {}).get('issue'):
                continue
            vocabulary = set(item.words) | set(item.title_words)
            named_match = named and all(any(affinity(name, token) for token in vocabulary) for name in named)
            covered = sum(any(affinity(word, token) for token in vocabulary) for word in current_words)
            if selected_source or named_match or (not named and current_words and covered / len(current_words) >= .65):
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

## ai_core/site_search_store.py

SHA256: 3d1b26f67921a94bbd80cd9668610e9058a76c7e64ac915d87935f9b11e8dc85

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
                 "parents", "status", "expires_at", "valid_from", "authority", "complete", "keywords", "file_name", "structure_unavailable"}


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

## tests/test_site_search_quality.py

SHA256: 12daffac208f81bc18c06609543934fbe57b809bbd674293f24f27b01294d3a8

````python
"""Искажение не должно превращать запрет в разрешение или терять число срока."""
import tempfile
import io
import base64
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
from ai_core.site_scan_quality import normalize_lighting, clipped_content, unrecognized_ink, critical_consistent
from ai_core.site_scan_tables import Cell, describe_table
from ai_core.site_documents import extract_document
from pypdf import PdfReader, PdfWriter
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.quality_probe import load_sources, errors_for, ORIGIN, ASSETS


def word(text, left=20, top=20, width=30, height=20, confidence=98):
    return dict(text=text,left=left,top=top,width=width,height=height,confidence=confidence)


class QualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec,cls.sources,cls.extraction=load_sources()

    def test_real_degradations_keep_facts_or_refuse(self):
        index=ClosedSiteIndex([ORIGIN]);index.publish(self.sources,index.begin('quality'))
        for case in self.spec['cases']:
            with self.subTest(case=case['id']):
                answer=index.search(case['q'],Principal('a','staff'),case['id'],case['source'])
                self.assertEqual(errors_for(case,answer),[])

    def test_light_normalization_preserves_white_page_and_recovers_shadow(self):
        clean=np.full((500,600),255,np.uint8)
        cv2.putText(clean,'13', (180,250),cv2.FONT_HERSHEY_SIMPLEX,2,0,3)
        self.assertTrue(np.array_equal(normalize_lighting(clean),clean))
        shaded=np.uint8(clean.astype(float)*np.linspace(.28,1,600))
        corrected=normalize_lighting(shaded)
        self.assertGreater(float(corrected[:100].mean()),250)
        self.assertLess(float(corrected[clean==0].mean()),5)

    def test_ink_at_page_edge_is_not_accepted_as_complete_line(self):
        image=np.full((400,600),255,np.uint8)
        cv2.putText(image,'Never store', (30,90),cv2.FONT_HERSHEY_SIMPLEX,1,0,2)
        self.assertFalse(clipped_content(image))
        cv2.rectangle(image,(0,180),(9,200),0,-1)
        self.assertTrue(clipped_content(image))

    def test_missing_recognition_of_visible_ink_is_not_high_confidence(self):
        image=np.full((200,300),255,np.uint8)
        cv2.rectangle(image,(20,20),(28,38),0,-1)
        self.assertTrue(unrecognized_ink(image,[]))
        self.assertFalse(unrecognized_ink(image,[word('1',20,20,10,20)]))

    def test_disagreement_missing_negation_and_low_number_confidence_are_refused(self):
        for left,right in [([word('13')],[word('18')]),([word('Не')],[]),
                           ([word('13')],[word('13',confidence=79)]),([word('не')],[word('ни')])]:
            with self.subTest(left=left,right=right):
                self.assertFalse(critical_consistent(left,right))
        self.assertTrue(critical_consistent([word('13,')],[word('13')]))
        # Совпадение цифры из другой строки не подтверждает исходную ячейку.
        self.assertFalse(critical_consistent([word('13')],[word('13',top=100)]))

    def test_numeric_column_cannot_keep_unit_after_quantity_disappears(self):
        def cells(value):
            return [Cell(0,0,1,1,'Товар'),Cell(0,1,1,2,'Срок хранения'),
                    Cell(1,0,2,1,'Пример'),Cell(1,1,2,2,value)]
        for value in ['календарных дней','‚алендарных дней','рабочих часов']:
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'quantity_missing'):
                describe_table(cells(value),2,2,1,1)
        for value in ['13 календарных дней','три рабочих дня','бессрочно']:
            self.assertIn(value,describe_table(cells(value),2,2,1,1))

    def test_empty_unreadable_scan_retains_status_after_restart_without_acl_leak(self):
        source={'id':'unreadable','revision':'1','title':'Закрытый скан','kind':'document',
                'url':ORIGIN+'/materials/unreadable','access':{'scope':'restricted','users':['a']},
                'complete':True,'sections':[{'heading':'Скан','text':'[STRUCTURE_UNCERTAIN]'}]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'index.sqlite3'
            runtime=DurableSearch([ORIGIN],path)
            runtime.publish([source],runtime.begin('unreadable'))
            reopened=DurableSearch([ORIGIN],path)
            self.assertTrue(reopened.restored)
            for question in ['Сколько дней?','Кратко перескажи выбранный файл']:
                self.assertEqual(reopened.query(question,Principal('a','staff'),'chat','unreadable',{'unreadable':'1'})['status'],'unavailable')
                denied=reopened.query(question,Principal('b','staff'),'chat','unreadable',{'unreadable':'1'})
                self.assertEqual(denied['status'],'not_found')
                self.assertEqual(denied['evidence'],[])
            self.assertEqual(reopened.query('Как доставляется заказ Вега?',Principal('a','staff'),'other',None,{'unreadable':'1'})['status'],'not_found')

    def test_quality_checks_do_not_run_ocr_again_on_cached_reads_or_questions(self):
        index=ClosedSiteIndex([ORIGIN]);index.publish(self.sources,index.begin('quality-cache'))
        with patch('ai_core.site_documents._scan_text',side_effect=AssertionError('Repeated OCR')):
            self.assertEqual(load_sources()[1],self.sources)
            for case in self.spec['cases']:
                answer=index.search(case['q'],Principal('a','staff'),'cached-'+case['id'],case['source'])
                self.assertEqual(errors_for(case,answer),[])

    def test_blank_reverse_side_does_not_close_readable_front_page(self):
        writer=PdfWriter(clone_from=PdfReader(ASSETS/'Чёткие_сроки.pdf'))
        writer.add_blank_page(width=600,height=700)
        buffer=io.BytesIO();writer.write(buffer)
        text,complete=extract_document('data:application/pdf;base64,'+base64.b64encode(buffer.getvalue()).decode(),'blank-back.pdf')
        self.assertTrue(complete)
        self.assertIn('13 календарных дней',text)
        self.assertNotIn('[STRUCTURE_UNCERTAIN]',text)


if __name__=='__main__':unittest.main()
````

## evaluation/closed_search/quality_assets.py

SHA256: 17619d697f29dd706561fb22e2e28aa1828bb1565c52a69c878d527480e60a7f

````python
"""Независимые ухудшенные растры. Эталон задаётся по исходному правилу, не OCR."""
import io
import json
import hashlib
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader
from .scan_assets import page

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'evaluation/closed_search/fixtures/quality'
PREVIEWS = ROOT/'.runtime/closed-search-lab-20261028/quality-previews'


def shadow(image):
    values = np.asarray(image).astype(np.float32)
    gradient = np.linspace(.28, 1, image.width).reshape(1, -1, 1)
    return Image.fromarray(np.uint8(np.clip(values*gradient, 0, 255)))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    if any(OUT.glob('*.pdf')) or (OUT/'spec.json').exists():
        raise RuntimeError('Frozen quality fixtures already exist')
    table = page('Учебные сроки хранения', [650, 970], [120, 150, 150],
                 [(0,0,1,1,'Товар'), (0,1,1,1,'Срок хранения'),
                  (1,0,1,1,'Селенит'), (1,1,1,1,'13 календарных дней'),
                  (2,0,1,1,'Лабрадорит'), (2,1,1,1,'18 календарных дней')])
    plain = Image.new('RGB', (1800, 2100), 'white')
    draw = ImageDraw.Draw(plain)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 36)
    draw.text((90,70), 'Учебный документ. Все правила вымышлены.', font=font, fill='black')
    draw.text((90,300), 'Не храните товар Иолит рядом с водой.', font=font, fill='black')
    compressed = io.BytesIO()
    table.resize((900,1050),Image.Resampling.LANCZOS).save(compressed,format='JPEG',quality=35)
    compressed = Image.open(io.BytesIO(compressed.getvalue())).convert('RGB').resize(table.size)
    local_blur = table.copy()
    # Ухудшена именно область двух цифр, остальные буквы остаются чёткими.
    box = (760, 400, 825, 458)
    local_blur.paste(table.crop(box).filter(ImageFilter.GaussianBlur(4.5)),box)
    partial_negation = plain.copy()
    draw = ImageDraw.Draw(partial_negation)
    draw.rectangle((90,315,138,330),fill=(170,170,170))
    cut = 90 + round(ImageDraw.Draw(plain).textlength('Не ',font=font))
    variants = [
        ('clean','Чёткие_сроки.pdf',table,'answer','Сколько хранится товар Селенит?',['13 календарных'],['18 календарных']),
        ('soft','Лёгкое_размытие.pdf',table.filter(ImageFilter.GaussianBlur(1.1)),'answer','Сколько хранится товар Селенит?',['13 календарных'],['18 календарных']),
        ('shadow','Тень_на_таблице.pdf',shadow(table),'answer','Сколько хранится товар Лабрадорит?',['18 календарных'],['13 календарных']),
        ('jpeg','Сжатая_таблица.pdf',compressed,'answer','Сколько хранится товар Селенит?',['13 календарных'],['18 календарных']),
        ('blur','Сильное_размытие.pdf',table.filter(ImageFilter.GaussianBlur(7)),'unavailable','Сколько хранится товар Селенит?',[],['13 календарных','18 календарных']),
        ('crop','Обрезанный_столбец.pdf',table.crop((0,0,1030,2100)),'unavailable','Сколько хранится товар Селенит?',[],['13 календарных','18 календарных']),
        ('number','Нечёткие_цифры.pdf',local_blur,'exact_or_refuse','Сколько хранится товар Селенит?',['13 календарных'],['3 календарных','18 календарных']),
        ('negation','Чёткий_запрет.pdf',plain,'answer','Можно ли хранить товар Иолит рядом с водой?',['Не храните'],[]),
        ('negation_shadow','Тень_на_запрете.pdf',shadow(plain),'answer','Можно ли хранить товар Иолит рядом с водой?',['Не храните'],[]),
        ('negation_crop','Обрезанное_отрицание.pdf',plain.crop((cut,0,1800,2100)),'unavailable','Можно ли хранить товар Иолит рядом с водой?',[],[]),
        ('negation_spot','Повреждённое_отрицание.pdf',partial_negation,'exact_or_refuse','Можно ли хранить товар Иолит рядом с водой?',['Не храните'],[]),
    ]
    documents,cases=[],[]
    for key,name,image,status,question,has,absent in variants:
        image.save(PREVIEWS/(key+'.png'))
        target=OUT/name
        pdf=canvas.Canvas(str(target),pagesize=(image.width/3,image.height/3),invariant=1)
        pdf.drawImage(ImageReader(image),0,0,image.width/3,image.height/3)
        pdf.showPage();pdf.save()
        if any((p.extract_text() or '').strip() for p in PdfReader(target).pages):
            raise AssertionError('Fixture is not raster-only')
        identifier='lab_quality_'+key
        documents.append({'id':identifier,'name':name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                          'degradation':key,'negative':status!='answer'})
        cases.append({'id':key,'source':identifier,'q':question,'status':status,'has':has,'absent':absent})
    # Запрещённая подстрока "3 календарных" входит в корректное "13";
    # числовые подмены проверяются отдельным набором разрешённых чисел.
    for case in cases:
        if case['id']=='number':
            case['absent']=['18 календарных']
            case['numbers']=['13']
    spec={'documents':documents,'cases':cases,'holdout':[
        {'id':'shadow-paraphrase','source':'lab_quality_shadow','q':'Как долго хранить Лабрадорит?','status':'answer','has':['18 календарных'],'absent':['13 календарных']},
        {'id':'crop-paraphrase','source':'lab_quality_crop','q':'Какой срок хранения у Селенита?','status':'unavailable'},
        {'id':'negation-paraphrase','source':'lab_quality_negation_shadow','q':'Товар Иолит можно держать около воды?','status':'answer','has':['Не храните']} ]}
    (OUT/'spec.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pdfs':len(documents),'cases':len(cases),'holdout':3,'directory':str(OUT)},ensure_ascii=False))


if __name__=='__main__':main()
````

## evaluation/closed_search/quality_probe.py

SHA256: 2adbf6c4d85825b47f31e4cc08c6c1857bc8079c76bb8372c34601a292a89fb2

````python
"""Раздельно считаем правильный ответ, безопасный отказ и опасную подмену факта."""
import argparse
import base64
import hashlib
import json
import re
import time
from pathlib import Path
from ai_core.site_documents import extract_document, PARSER_VERSION
from ai_core.site_search import ClosedSiteIndex, Principal

ROOT=Path(__file__).resolve().parents[2]
ASSETS=ROOT/'evaluation/closed_search/fixtures/quality'
LAB=ROOT/'.runtime/closed-search-lab-20261028'
ORIGIN='http://127.0.0.1:3040'


def load_sources():
    spec=json.loads((ASSETS/'spec.json').read_text(encoding='utf-8'))
    sources,extraction=[],[]
    for item in spec['documents']:
        data=(ASSETS/item['name']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=item['sha256']:
            raise AssertionError('Frozen fixture changed: '+item['id'])
        started=time.perf_counter()
        text,complete=extract_document('data:application/pdf;base64,'+base64.b64encode(data).decode(),item['name'])
        extraction.append({'id':item['id'],'text':text,'complete':complete,'seconds':round(time.perf_counter()-started,3)})
        sources.append({'id':item['id'],'revision':'quality-v1','title':item['name'],'kind':'document',
                        'url':ORIGIN+'/materials/'+item['id'],'access':{'scope':'workspace'},'complete':complete,
                        'sections':[{'heading':item['name'],'text':text}]})
    return spec,sources,extraction


def errors_for(case,answer):
    status=answer['status'];expected=case.get('status','answer')
    allowed=['answer','unavailable'] if expected=='exact_or_refuse' else [expected]
    errors=[]
    if status not in allowed:errors.append('status:'+status)
    if status=='answer':
        for text in case.get('has',[]):
            if text.casefold() not in answer['text'].casefold():errors.append('missing:'+text)
        if case.get('numbers') and set(re.findall(r'\b\d+\b',answer['text']))!=set(case['numbers']):errors.append('wrong-number')
        if {e['id'] for e in answer['evidence']}!={case['source']}:errors.append('source')
    for text in case.get('absent',[]):
        if text.casefold() in answer['text'].casefold():errors.append('unexpected:'+text)
    if status!='answer' and (answer.get('links') or answer.get('evidence')):errors.append('evidence-on-refusal')
    return errors


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--label',default='quality-probe');parser.add_argument('--holdout',action='store_true')
    args=parser.parse_args()
    spec,sources,extraction=load_sources()
    index=ClosedSiteIndex([ORIGIN]);index.publish(sources,index.begin('quality'))
    rows=[]
    for case in spec['holdout' if args.holdout else 'cases']:
        answer=index.search(case['q'],Principal('a','staff'),case['id'],case['source'])
        errors=errors_for(case,answer)
        rows.append({'id':case['id'],'question':case['q'],'pass':not errors,'errors':errors,'answer':answer})
    report={'parser':PARSER_VERSION,'passed':sum(r['pass'] for r in rows),'total':len(rows),'cases':rows,'extraction':extraction}
    file=LAB/(args.label+'-'+str(time.time_ns())+'.json')
    file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'file':str(file),'passed':report['passed'],'total':report['total'],'failures':[r for r in rows if not r['pass']]},ensure_ascii=False))
    if report['passed']!=report['total']:raise SystemExit(1)


if __name__=='__main__':main()
````

## evaluation/closed_search/quality_http.cjs

SHA256: d378ca11126ac0dd5e7690bdcf8d750b1a931c6bbacff8b031edf51a56ae9385

````javascript
/* Читаемые и повреждённые PDF проходят настоящий маршрут сайта и собственное ядро. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const {createHash}=require('node:crypto');
const assets=path.join(__dirname,'fixtures/quality');
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(assets,'spec.json'),'utf8'));
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const negative=new Set(spec.documents.filter(d=>d.negative).map(d=>d.id));
    const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
    for(const item of spec.documents){
        const bytes=await fs.readFile(path.join(assets,item.name));
        if(createHash('sha256').update(bytes).digest('hex')!==item.sha256)throw new Error('Frozen fixture changed '+item.id);
        const data='data:application/pdf;base64,'+bytes.toString('base64');
        const entry={id:item.id,name:item.name,hasSeparateData:true,section:'Качество сканов ВатэсAi',size:bytes.length+' B',date:'Локальный тест',
            ...(item.negative?{adminOnly:true}:{})};
        const prior=catalog.find(d=>d.id===item.id);
        if(prior){
            if(JSON.stringify(prior)!==JSON.stringify(entry)||await storage('file_data_'+item.id,cookies.admin)!==data)throw new Error('Site fixture changed '+item.id);
        }else{
            await storage('file_data_'+item.id,cookies.admin,data);catalog.push(entry);
        }
    }
    await storage('tea_hub_urgent_files_v1',cookies.admin,catalog);
    const ready=await waitSnapshot();
    const cases=[...spec.cases,...spec.holdout,
        {id:'summary-clear',source:'lab_quality_clean',q:'Кратко перескажи выбранный файл',status:'answer',has:['13 календарных','18 календарных']},
        {id:'summary-cropped',source:'lab_quality_negation_crop',q:'Кратко перескажи выбранный файл',status:'unavailable'},
        {id:'hidden-a',actor:'a',source:'lab_quality_negation_crop',q:'Можно ли хранить товар Иолит рядом с водой?',status:'not_found'},
        {id:'hidden-b',actor:'b',source:'lab_quality_number',q:'Сколько хранится товар Селенит?',status:'not_found'}];
    const rows=[];
    for(const [position,item] of cases.entries()){
        const actor=item.actor||(negative.has(item.source)?'admin':['a','b','admin'][position%3]);
        const started=performance.now();
        const {response,value}=await call('/api/ai-chat',{chatId:'quality-'+Date.now()+'-'+item.id,documentId:item.source,messages:[{role:'user',content:item.q}]},cookies[actor]);
        const answer=value.output_text||'',errors=[],allowed=item.status==='exact_or_refuse'?['answer','unavailable']:[item.status];
        if(response.status!==200)errors.push('http:'+response.status);
        if(!allowed.includes(value.search_status))errors.push('status:'+value.search_status);
        if(value.search_status==='answer'){
            for(const text of item.has||[])if(!answer.toLowerCase().includes(text.toLowerCase()))errors.push('missing:'+text);
            if(item.numbers&&JSON.stringify([...new Set(answer.match(/\b\d+\b/g)||[])].sort())!==JSON.stringify([...item.numbers].sort()))errors.push('wrong-number');
            if(!value.sourceLinks?.length||value.sourceLinks.some(link=>!link.url.includes(item.source)))errors.push('source');
        }else if(value.sourceLinks?.length)errors.push('links-on-refusal');
        for(const text of item.absent||[])if(answer.toLowerCase().includes(text.toLowerCase()))errors.push('unexpected:'+text);
        rows.push({id:item.id,actor,question:item.q,status:value.search_status,answer,links:value.sourceLinks,ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    const report={createdAt:new Date().toISOString(),ready,total:rows.length,passed:rows.filter(r=>r.pass).length,cases:rows};
    const file=path.join(lab,'quality-http-'+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,total:report.total,passed:report.passed,failures:rows.filter(r=>!r.pass)}));
    if(report.passed!==report.total)process.exitCode=1;
}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1;});
module.exports={assets};
````

## evaluation/closed_search/quality_revisions.cjs

SHA256: 41fd76d854d985dde7293333b43710c54d864147e8ddef0c71b98ce9fbdc0dca

````javascript
/* Повреждение новой редакции не разрешает ни старое значение, ни потерянное «не». */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {assets}=require('./quality_http.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const id='lab_quality_revision_probe';
async function main(){
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    if((await storage('tea_hub_urgent_files_v1',cookies.admin)).some(d=>d.id===id))throw new Error('Unfinished quality revision fixture exists');
    const rows=[],chat='quality-revision-'+Date.now();
    async function catalog(change){await storage('tea_hub_urgent_files_v1',cookies.admin,change(await storage('tea_hub_urgent_files_v1',cookies.admin)));}
    async function replace(name){await storage('file_data_'+id,cookies.admin,'data:application/pdf;base64,'+(await fs.readFile(path.join(assets,name))).toString('base64'));}
    async function ask(name,statuses,has=[],actor='a'){
        const {response,value}=await call('/api/ai-chat',{chatId:chat,documentId:id,messages:[{role:'user',content:'Можно ли хранить товар Иолит рядом с водой?'}]},cookies[actor]);
        const text=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(!statuses.includes(value.search_status))errors.push('status:'+value.search_status);
        for(const part of has)if(!text.includes(part))errors.push('missing:'+part);
        if(value.search_status!=='answer'&&(value.sourceLinks?.length||text.toLowerCase().includes('храните товар')))errors.push('unsafe-refusal');
        if(value.search_status==='answer'&&(!text.includes('Не храните')||!value.sourceLinks?.length||value.sourceLinks.some(link=>!link.url.includes(id))))errors.push('wrong-answer');
        rows.push({id:name,actor,status:value.search_status,answer:text,pass:!errors.length,errors});
    }
    let completed=false,cleanup=false,failure=null;
    try{
        await replace('Чёткий_запрет.pdf');
        await catalog(items=>[...items,{id,name:'Временный_запрет_качество.pdf',section:'Проверка редакций',hasSeparateData:true,allowedUserIds:['closed_lab_a']}]);
        await waitSnapshot();await ask('clear',['answer'],['Не храните']);
        await ask('b-denied',['not_found'],[],'b');
        await replace('Обрезанное_отрицание.pdf');
        await ask('cropped-immediate',['processing','unavailable']);
        await waitSnapshot();await ask('cropped-ready',['unavailable']);
        await replace('Тень_на_запрете.pdf');await waitSnapshot();await ask('shadow-restored',['answer'],['Не храните']);
        await catalog(items=>items.map(item=>item.id===id?{...item,allowedUserIds:['closed_lab_b']}:item));
        await ask('a-revoked-immediate',['processing','not_found']);
        await waitSnapshot();await ask('b-allowed',['answer'],['Не храните'],'b');
        await catalog(items=>items.filter(item=>item.id!==id));
        await ask('removed',['processing','not_found'],[],'b');
        await waitSnapshot();await ask('removed-ready',['not_found'],[],'b');
        const denied=await call('/api/storage?key=file_data_'+id,undefined,cookies.b);
        rows.push({id:'orphan-bytes-denied',pass:denied.response.status===403,http:denied.response.status});
        completed=true;
    }catch(error){failure=error.message;throw error;}
    finally{
        try{
            await catalog(items=>items.filter(item=>item.id!==id));
            await storage('file_data_'+id,cookies.admin,null);await waitSnapshot();cleanup=true;
        }finally{
            const report={createdAt:new Date().toISOString(),completed,cleanup,failure,total:rows.length,passed:rows.filter(r=>r.pass).length,cases:rows};
            const file=path.join(lab,'quality-revisions-'+Date.now()+'.json');
            await fs.writeFile(file,JSON.stringify(report,null,2));
            console.log(JSON.stringify({file,completed,cleanup,total:report.total,passed:report.passed,failures:rows.filter(r=>!r.pass)}));
            if(!completed||!cleanup||report.total!==report.passed)process.exitCode=1;
        }
    }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
````

## evaluation/closed_search/quality_performance.py

SHA256: 15386a0de82dfaae4bd462203d2391ef922334f639a50a4166a6c1a03bbfd0e6

````python
"""Цена контроля качества в фоне; на пути 280 запросов OCR запрещён проверкой."""
import hashlib
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_documents import PARSER_VERSION
from .quality_probe import ROOT, LAB, ORIGIN, load_sources, errors_for


def main():
    with tempfile.TemporaryDirectory(prefix='vates-quality-cache-') as directory:
        with patch('ai_core.site_documents.CACHE',Path(directory)):
            started=time.perf_counter();spec,sources,extraction=load_sources();cold=time.perf_counter()-started
            with patch('ai_core.site_documents._scan_text',side_effect=AssertionError('Repeated OCR')):
                started=time.perf_counter();cached=load_sources()[1];warm=time.perf_counter()-started
            if sources!=cached:raise AssertionError('Cache differs')
    index=ClosedSiteIndex([ORIGIN]);index.publish(sources,index.begin('quality-performance'))
    timings,failures=[],[]
    with patch('ai_core.site_documents._scan_text',side_effect=AssertionError('OCR on query')):
        for cycle in range(20):
            for case in spec['cases']+spec['holdout']:
                started=time.perf_counter()
                answer=index.search(case['q'],Principal('a','staff'),str(cycle)+case['id'],case['source'])
                timings.append((time.perf_counter()-started)*1000)
                errors=errors_for(case,answer)
                if errors:failures.append({'case':case['id'],'cycle':cycle,'errors':errors})
    timings.sort()
    names=['site_documents.py','site_scan_tables.py','site_scan_layout.py','site_scan_quality.py','site_search.py',
           'site_search_structure.py','site_search_reasoning.py','site_search_runtime.py','site_search_store.py','site_search_api.py']
    report={'mode':'eleven-synthetic-pdfs-eleven-pages-local-query-no-http','parser':PARSER_VERSION,
            'coldSeconds':round(cold,3),'warmSeconds':round(warm,4),'requests':len(timings),'failures':failures,
            'queryP50Ms':round(timings[len(timings)//2],2),'queryP95Ms':round(timings[int(len(timings)*.95)-1],2),
            'extraction':[{k:v for k,v in item.items() if k!='text'} for item in extraction],
            'engineHashes':{name:hashlib.sha256((ROOT/'ai_core'/name).read_bytes()).hexdigest() for name in names}}
    output=LAB/('quality-performance-'+str(time.time_ns())+'.json')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'file':str(output),**report},ensure_ascii=False))
    if failures:raise SystemExit(1)


if __name__=='__main__':main()
````

## evaluation/closed_search/layout_visual_qa.py

SHA256: 7e0188d7604b7bf974ed0789764e591ec9a5d3186ad89381d629bb0a57c57859

````python
"""Рендер всех новых PDF через Poppler и контактные листы для ручной проверки."""
import json
import argparse
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[2]
ASSETS=ROOT/'evaluation/closed_search/fixtures/layout'
OUT=ROOT/'.runtime/closed-search-lab-20261028/layout-rendered'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--fixture',choices=['layout','quality'],default='layout')
    args=parser.parse_args()
    assets=ROOT/'evaluation/closed_search/fixtures'/args.fixture
    output=ROOT/'.runtime/closed-search-lab-20261028'/(args.fixture+'-rendered')
    output.mkdir(parents=True,exist_ok=True)
    renderer=shutil.which('pdftoppm')
    if not renderer: raise RuntimeError('Poppler is required')
    spec=json.loads((assets/'spec.json').read_text(encoding='utf-8'))
    rendered=[]
    for document in spec['documents']:
        target=output/document['id']
        subprocess.run([renderer,'-r','100','-png',str(assets/document['name']),str(target)],
                       check=True,capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
        for path in sorted(output.glob(document['id']+'-*.png')):
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
        filename=output/f'contact-{start//4+1}.png'
        sheet.save(filename);sheets.append(str(filename))
    print(json.dumps({'pages':len(rendered),'sheets':sheets},ensure_ascii=False))


if __name__=='__main__':main()
````

## evaluation/closed_search/verify_lab.cjs

SHA256: 3cc522f4e9055ef8611a7406cd4bfd40fd3b434fb59e6a5a01ff60c26db5022a

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
    const expectedDocuments = 9 + reasoningFixtures.documents.length + conditionFixtures.documents.length + structureFixtures.documents.length + structureAssets.length + scanFixtures.documents.length + layoutFixtures.documents.length + qualityFixtures.documents.length;
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

## evaluation/closed_search/fixtures/quality/spec.json

SHA256: 0c65a7e9f96ec4cd2042aac178e6a5af3d0086548dd17e0719152cdd960e8363

````json
{
  "documents": [
    {
      "id": "lab_quality_clean",
      "name": "Чёткие_сроки.pdf",
      "sha256": "416833302d302cfe254891df20e8732d34a375d0de048b393c0bf2b9d2bbf813",
      "degradation": "clean",
      "negative": false
    },
    {
      "id": "lab_quality_soft",
      "name": "Лёгкое_размытие.pdf",
      "sha256": "75bcef6c55d91e857dde2769bb80bb05365ad55cddea7f0e38462ae0b74fd89b",
      "degradation": "soft",
      "negative": false
    },
    {
      "id": "lab_quality_shadow",
      "name": "Тень_на_таблице.pdf",
      "sha256": "752cf2482884da67a518740344cd5c1d2395a03833062a2adb70bfdb5889904a",
      "degradation": "shadow",
      "negative": false
    },
    {
      "id": "lab_quality_jpeg",
      "name": "Сжатая_таблица.pdf",
      "sha256": "aa6c6545404980028014aeabeeb64aa189bf532da537f3293f0c33adabe6f595",
      "degradation": "jpeg",
      "negative": false
    },
    {
      "id": "lab_quality_blur",
      "name": "Сильное_размытие.pdf",
      "sha256": "cabf9eb942fb1e3aa92248488c9d3dfa93021eb09780f19c735303f5c9b2dc76",
      "degradation": "blur",
      "negative": true
    },
    {
      "id": "lab_quality_crop",
      "name": "Обрезанный_столбец.pdf",
      "sha256": "4bf8179427f9985075b58ca05186ff0ccb000dee5fad76899abca61389b4868a",
      "degradation": "crop",
      "negative": true
    },
    {
      "id": "lab_quality_number",
      "name": "Нечёткие_цифры.pdf",
      "sha256": "5b100f4f560a6fbe8e950f1ea0c0f42d0f211d73f69cdfc493a648110687d498",
      "degradation": "number",
      "negative": true
    },
    {
      "id": "lab_quality_negation",
      "name": "Чёткий_запрет.pdf",
      "sha256": "3b62e945eb57020f57f46baef96d238bd1bd9503676f0b6cc14e90c61362a637",
      "degradation": "negation",
      "negative": false
    },
    {
      "id": "lab_quality_negation_shadow",
      "name": "Тень_на_запрете.pdf",
      "sha256": "c852a752c8e8fa5a0cec11f00eb0e4328c969b4c13717122f59ee12329af47a5",
      "degradation": "negation_shadow",
      "negative": false
    },
    {
      "id": "lab_quality_negation_crop",
      "name": "Обрезанное_отрицание.pdf",
      "sha256": "092305810a2ee4fede9bd628e91ca073c44ec8b42722a6712de3492d3299f011",
      "degradation": "negation_crop",
      "negative": true
    },
    {
      "id": "lab_quality_negation_spot",
      "name": "Повреждённое_отрицание.pdf",
      "sha256": "226dd8cc69d78e336bc35640924feaa5be93544acea1f03f79a322b220b9a27d",
      "degradation": "negation_spot",
      "negative": true
    }
  ],
  "cases": [
    {
      "id": "clean",
      "source": "lab_quality_clean",
      "q": "Сколько хранится товар Селенит?",
      "status": "answer",
      "has": [
        "13 календарных"
      ],
      "absent": [
        "18 календарных"
      ]
    },
    {
      "id": "soft",
      "source": "lab_quality_soft",
      "q": "Сколько хранится товар Селенит?",
      "status": "answer",
      "has": [
        "13 календарных"
      ],
      "absent": [
        "18 календарных"
      ]
    },
    {
      "id": "shadow",
      "source": "lab_quality_shadow",
      "q": "Сколько хранится товар Лабрадорит?",
      "status": "answer",
      "has": [
        "18 календарных"
      ],
      "absent": [
        "13 календарных"
      ]
    },
    {
      "id": "jpeg",
      "source": "lab_quality_jpeg",
      "q": "Сколько хранится товар Селенит?",
      "status": "answer",
      "has": [
        "13 календарных"
      ],
      "absent": [
        "18 календарных"
      ]
    },
    {
      "id": "blur",
      "source": "lab_quality_blur",
      "q": "Сколько хранится товар Селенит?",
      "status": "unavailable",
      "has": [],
      "absent": [
        "13 календарных",
        "18 календарных"
      ]
    },
    {
      "id": "crop",
      "source": "lab_quality_crop",
      "q": "Сколько хранится товар Селенит?",
      "status": "unavailable",
      "has": [],
      "absent": [
        "13 календарных",
        "18 календарных"
      ]
    },
    {
      "id": "number",
      "source": "lab_quality_number",
      "q": "Сколько хранится товар Селенит?",
      "status": "exact_or_refuse",
      "has": [
        "13 календарных"
      ],
      "absent": [
        "18 календарных"
      ],
      "numbers": [
        "13"
      ]
    },
    {
      "id": "negation",
      "source": "lab_quality_negation",
      "q": "Можно ли хранить товар Иолит рядом с водой?",
      "status": "answer",
      "has": [
        "Не храните"
      ],
      "absent": []
    },
    {
      "id": "negation_shadow",
      "source": "lab_quality_negation_shadow",
      "q": "Можно ли хранить товар Иолит рядом с водой?",
      "status": "answer",
      "has": [
        "Не храните"
      ],
      "absent": []
    },
    {
      "id": "negation_crop",
      "source": "lab_quality_negation_crop",
      "q": "Можно ли хранить товар Иолит рядом с водой?",
      "status": "unavailable",
      "has": [],
      "absent": []
    },
    {
      "id": "negation_spot",
      "source": "lab_quality_negation_spot",
      "q": "Можно ли хранить товар Иолит рядом с водой?",
      "status": "exact_or_refuse",
      "has": [
        "Не храните"
      ],
      "absent": []
    }
  ],
  "holdout": [
    {
      "id": "shadow-paraphrase",
      "source": "lab_quality_shadow",
      "q": "Как долго хранить Лабрадорит?",
      "status": "answer",
      "has": [
        "18 календарных"
      ],
      "absent": [
        "13 календарных"
      ]
    },
    {
      "id": "crop-paraphrase",
      "source": "lab_quality_crop",
      "q": "Какой срок хранения у Селенита?",
      "status": "unavailable"
    },
    {
      "id": "negation-paraphrase",
      "source": "lab_quality_negation_shadow",
      "q": "Товар Иолит можно держать около воды?",
      "status": "answer",
      "has": [
        "Не храните"
      ]
    }
  ]
}
````
