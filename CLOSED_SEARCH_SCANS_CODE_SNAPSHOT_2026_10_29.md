# Полные листинги этапа: сканы и редакции — 29.10.2026
Снимок полных изменённых файлов из C:/Users/KROLL/Desktop/Ai. Это копии для чтения и переноса; рабочие файлы находятся по указанным путям. Ожидания и исходные PDF сохранены отдельно в evaluation/closed_search/fixtures/scans.

## ai_core/site_scan_tables.py

~~~python
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


def extract_scan(path, ocr_tsv, join_lines, page=1):
    """Возвращает текст и наличие таблицы. Координаты нужны только на этапе OCR."""
    image = deskew(read_image(path))
    ink = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, max(35, image.shape[1] // 35)), np.uint8))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((max(35, image.shape[0] // 50), 1), np.uint8))
    grid = cv2.bitwise_or(horizontal, vertical)
    contours = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    boxes = sorted((cv2.boundingRect(contour) for contour in contours), key=lambda box: (box[1], box[0]))
    boxes = [box for box in boxes if box[2] > image.shape[1] * .25 and box[3] > 70]
    if not boxes:
        return None, False
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
            blocks.append((y, describe_table(cells, len(ys) - 1, len(xs) - 1, page, ordinal)))
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
    # Номера сносок локальны странице: одинаковый [1] на двух страницах не смешивается.
    result = re.sub(r'\[(\d+)\]', lambda match: f'[p{page}-{match[1]}]', result)
    return (UNCERTAIN + '\n' if issue else '') + result, True

~~~

## ai_core/site_documents.py

~~~python
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

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".runtime" / "closed-search" / "document-cache"
# Изменение алгоритма извлечения само инвалидирует производный кэш. Иначе
# исправленный OCR мог бы продолжать читать прежний результат тех же байтов PDF.
PARSER_VERSION = "closed-documents-v4-scan-grid-" + hashlib.sha256(
    Path(__file__).read_bytes() + Path(__file__).with_name('site_scan_tables.py').read_bytes()
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


def _scan_text(image: Path, page=1) -> str:
    text, table = extract_scan(image, lambda path: _ocr(path, tsv=True), _join_visual_lines, page)
    return text if table else _join_visual_lines(_ocr(image))


def _join_visual_lines(text: str) -> str:
    paragraphs = []
    current = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if current:
                paragraphs.append(current)
                current = ""
            continue
        if current and (re.search(r"[.!?][»\"']?$", current) or (current.isupper() and len(current) < 100)):
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
            pages = []
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
                    page_text = _scan_text(prefix.with_suffix(".png"), page)
                    if page_text.strip():
                        pages.append(page_text)
                    continue
                if page_text.strip():
                    pages.append(_join_visual_lines(page_text))
            text = "\n\n".join(pages)
        elif extension in {".png", ".jpg", ".jpeg"}:
            text = _scan_text(file)
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

~~~

## ai_core/site_search_structure.py

~~~python
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
    nodes, notes, initial = {}, {}, []
    global_periods = []
    # Ошибка геометрии скана может скрывать ограничение соседнего раздела.
    # Она закрывает этот документ, но не делает посторонние материалы неполными.
    global_issue = any('[STRUCTURE_UNCERTAIN]' in str(section.get('text', '')) for section in source.get('sections', []))
    for section_number, section in enumerate(source.get('sections', [])):
        raw = normalize_markup(str(section.get('text', '')))
        raw = re.sub(r'(?im)^(Раздел\s+\d+(?:\.\d+)*[. :—-].*)$', r'# \1', raw)
        heading = plain(str(section.get('heading', '')))
        chunks = re.split(r'(?m)^\s*#{1,6}\s+(.+?)\s*$', raw)
        parts = [(heading, chunks[0])] + [(chunks[index], chunks[index + 1]) for index in range(1, len(chunks), 2)]
        for part_number, (title, body) in enumerate(parts):
            key_match = re.match(r'(?:Раздел\s+)?(\d+(?:\.\d+)*)[.\s]', title, re.I)
            key = key_match[1] if key_match else f'part-{section_number}-{part_number}'
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
            node = {'heading': plain(title), 'text': body, 'periods': periods, 'issue': issue or '[STRUCTURE_UNCERTAIN]' in body}
            if key in nodes:
                nodes[key]['issue'] = True
            else:
                nodes[key] = node
                initial.append(key)
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
                                         'dependencies': provenance, 'linked': bool(provenance)}})
    # Цикл может не иметь корневого раздела. Он должен дать объяснимый отказ,
    # а не исчезнуть из поиска вместе с основным правилом.
    if initial:
        for key in initial:
            if key in visited:
                continue
            node = nodes[key]
            result.append({**node, 'structure': {'issue': True, 'periods': [], 'dependencies': []}})
    return result

~~~

## ai_core/site_search_reasoning.py

~~~python
"""Проверяемые операции над вопросами и фактами без генерации новых правил.

Этот слой не заменяет понимание языка: он обрабатывает явные части вопроса,
известные виды утверждений и доверенные метаданные источника.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


QUESTION_START = r"(?:кому|кто|сколько|как|где|когда|что|зачем|почему|можно ли|до какого времени|в какой срок|при какой температуре)"
QUESTION_WORDS = set("кому кто сколько как где когда что зачем почему можно до в при какой какая какие какое а и если для это он она его ее её".split())


def compound_parts(question: str) -> list[str]:
    """Разделяем только явные вопросы, а не обычное перечисление объектов."""
    parts = re.split(rf"(?:\?\s+|[;,]\s*|\s+(?:и|а также)\s+)(?={QUESTION_START}\b)", question.strip(), flags=re.I)
    if len(parts) == 1:
        return parts
    # «Кому и в какой срок направить заявку?» имеет общий хвост предложения.
    if re.fullmatch(QUESTION_START, parts[0], re.I):
        tail = re.sub(rf"^{QUESTION_START}\s+", "", parts[1], count=1, flags=re.I)
        parts[0] += " " + tail
    names = list(dict.fromkeys(match.group() for match in re.finditer(r"\b[А-ЯЁA-Z][а-яёa-zА-ЯЁA-Z0-9-]+", question)
                               if match.group().casefold() not in QUESTION_WORDS))
    # Единственный явно названный объект относится и к соседней части вопроса.
    # При нескольких объектах ничего не угадываем и не склеиваем их правила.
    if len(names) == 1:
        name = names[0]
        parts = [part if name.casefold() in part.casefold() else part.rstrip(" ?.") + " " + name for part in parts]
    return [part.strip(" ,;?.") + "?" for part in parts]


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


def conflicting_passages(passages, sentences, tokenize) -> bool:
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
            for claim in claims(sentence, tokenize):
                key = (claim.relation, claim.subject, claim.condition)
                if key in found and contradictory(claim, found[key]):
                    return True
                found[key] = claim
    return False

~~~

## ai_core/site_search_runtime.py

~~~python
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
    for name in ("site_search.py", "site_search_language.py", "site_search_reasoning.py", "site_search_conditions.py", "site_search_structure.py"):
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

~~~

## ai_core/site_search_store.py

~~~python
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
                 "parents", "status", "expires_at", "valid_from", "authority", "complete", "keywords", "file_name"}


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

~~~

## ai_core/site_search_api.py

~~~python
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


# Тот же компонент можно запускать отдельно для изолированной приёмки.
app = FastAPI(title="ВатэсAi — закрытый поиск")
app.include_router(router)

~~~

## tests/test_site_search_scans.py

~~~python
"""Геометрия, реальные растры и отсутствие старых правил при смене редакции."""
import json
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
from ai_core.site_scan_tables import Cell, grid_cells, describe_table, reliable, parse_words
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.scan_probe import ASSETS, extracted_sources, ORIGIN


class ScanTests(unittest.TestCase):
    def test_merged_headers_and_body_rows_use_visible_spans(self):
        cells = [Cell(0, 0, 2, 1, 'Товар'), Cell(0, 1, 1, 3, 'Срок хранения'),
                 Cell(1, 1, 2, 2, 'Если упаковка не вскрыта'), Cell(1, 2, 2, 3, 'Если упаковка вскрыта'),
                 Cell(2, 0, 3, 1, 'Пример'), Cell(2, 1, 3, 2, '8 дней'), Cell(2, 2, 3, 3, '2 дня')]
        text = describe_table(cells, 3, 3, 2, 1)
        self.assertIn('Если упаковка вскрыта, товар Пример; Срок хранения: 2 дня.', text)
        self.assertIn('Страница 2', text)
        self.assertEqual(text.count('8 дней'), 1)

    def test_broken_partial_border_does_not_turn_into_merge(self):
        horizontal, vertical = np.zeros((221, 221), np.uint8), np.zeros((221, 221), np.uint8)
        for y in (10, 110, 210):
            cv2.line(horizontal, (10, y), (210, y), 255, 2)
        for x in (10, 110, 210):
            cv2.line(vertical, (x, 10), (x, 210), 255, 2)
        horizontal[108:113, 50:90] = 0
        with self.assertRaisesRegex(ValueError, 'broken_border'):
            grid_cells(horizontal, vertical, [10, 110, 210], [10, 110, 210])

    def test_low_confidence_numbers_negation_and_empty_cell_are_refused(self):
        self.assertFalse(reliable([]))
        for text in ['13', 'не']:
            self.assertFalse(reliable([{'text': text, 'confidence': 79}, {'text': 'дней', 'confidence': 99}]))
        self.assertTrue(reliable([{'text': '13', 'confidence': 95}, {'text': 'дней', 'confidence': 99}]))

    def test_body_horizontal_merge_is_not_guessed(self):
        cells = [Cell(0, 0, 1, 1, 'Товар'), Cell(0, 1, 1, 2, 'Срок'), Cell(0, 2, 1, 3, 'Цена'),
                 Cell(1, 0, 2, 1, 'Пример'), Cell(1, 1, 2, 3, '5')]
        with self.assertRaisesRegex(ValueError, 'body_colspan'):
            describe_table(cells, 2, 3, 1, 1)

    def test_real_scans_frozen_expectations_and_cached_extraction(self):
        spec = json.loads((ASSETS / 'spec.json').read_text(encoding='utf-8'))
        sources, extraction = extracted_sources(spec)
        index = ClosedSiteIndex([ORIGIN])
        index.publish(sources, index.begin('scans'))
        for item in spec['cases']:
            with self.subTest(case=item['id']):
                answer = index.search(item['q'], Principal('a', 'staff'), item['id'])
                self.assertEqual(answer['status'], item.get('status', 'answer'))
                for value in item.get('has', []):
                    self.assertIn(value.casefold(), answer['text'].casefold())
                for value in item.get('absent', []):
                    self.assertNotIn(value.casefold(), answer['text'].casefold())
        self.assertTrue(next(item for item in extraction if item['id'] == 'lab_scan_damaged')['complete'])
        self.assertTrue(all(item.structure['issue'] for item in index.passages if item.source['id'] == 'lab_scan_damaged'))
        # Скан прочитан до конца, но таблица неразрешима. Это не меняет
        # отсутствие ответа по совершенно другой теме на общую ошибку OCR.
        self.assertEqual(index.search('Как доставляется заказ Вега?', Principal('a', 'staff'), 'unrelated')['status'], 'not_found')
        summary = index.search('Кратко перескажи выбранный файл', Principal('a', 'staff'), 'summary', 'lab_scan_matrix')
        self.assertEqual(summary['status'], 'answer')
        self.assertIn('8 календарных', summary['text'])
        self.assertIn('Если упаковка вскрыта', summary['text'])
        with patch('ai_core.site_documents._scan_text', side_effect=AssertionError('OCR on warm cache')):
            self.assertEqual(extracted_sources(spec)[0], sources)

    def test_table_calendar_columns_are_preserved_as_periods(self):
        cells = [Cell(0, n, 1, n + 1, value) for n, value in enumerate(['Товар', 'Срок хранения', 'Действует с', 'Действует по'])]
        cells += [Cell(1, n, 2, n + 1, value) for n, value in enumerate(['Пример', '6 дней', '01.11.2026', '30.11.2026'])]
        text = describe_table(cells, 2, 4, 1, 1)
        self.assertIn('Действует с 01.11.2026 по 30.11.2026 включительно.', text)

    def test_pagination_is_not_conflict_but_changed_rule_is(self):
        from ai_core.site_search_reasoning import conflicting_passages
        from ai_core.site_search import terms
        from types import SimpleNamespace
        pages = [SimpleNamespace(text='Страница 1. Товар Пример хранится 4 дня.'),
                 SimpleNamespace(text='Страница 2. Товар Пример хранится 4 дня.')]
        self.assertFalse(conflicting_passages(pages, ClosedSiteIndex._sentences, terms))
        pages[1].text = 'Страница 2. Товар Пример хранится 8 дней.'
        self.assertTrue(conflicting_passages(pages, ClosedSiteIndex._sentences, terms))

    def test_extraction_upgrade_never_restores_old_flattened_index(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'index.sqlite3'
            old = DurableSearch([ORIGIN], path, extraction_version='ocr-v1')
            old.publish([], old.begin('first'))
            changed = DurableSearch([ORIGIN], path, extraction_version='ocr-v2')
            self.assertEqual(changed.status()['state'], 'unavailable')
            self.assertFalse(changed.restored)
            changed.publish([], changed.begin('fresh'))
            reopened = DurableSearch([ORIGIN], path, extraction_version='ocr-v2')
            self.assertEqual(reopened.status()['state'], 'ready')
            self.assertTrue(reopened.restored)

    def test_real_scanned_revision_replaces_saved_context_after_restart(self):
        from ai_core.site_documents import extract_document
        actor = Principal('revision-a', 'staff')
        def source(version):
            data = (ASSETS / f'Редакция_Родонит_v{version}.pdf').read_bytes()
            text, complete = extract_document('data:application/pdf;base64,' + base64.b64encode(data).decode(), 'rule.pdf')
            return {'id': 'revision', 'revision': str(version), 'title': 'Регламент Родонит', 'kind': 'document',
                    'url': ORIGIN + '/materials/revision', 'access': {'scope': 'workspace'},
                    'complete': complete, 'sections': [{'heading': 'Правило', 'text': text}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'revision.sqlite3'
            runtime = DurableSearch([ORIGIN], path)
            runtime.publish([source(1)], runtime.begin('v1'))
            old = runtime.query('Упаковка вскрыта. Сколько хранится товар Родонит?', actor, 'chat', None, {'revision': '1'})
            self.assertIn('1 календарный', old['text'])
            runtime = DurableSearch([ORIGIN], path)
            pending = runtime.query('А сколько дней?', actor, 'chat', None, {'revision': '2'})
            self.assertEqual(pending['status'], 'processing')
            runtime.publish([source(2)], runtime.begin('v2'))
            runtime = DurableSearch([ORIGIN], path)
            self.assertEqual(runtime.query('А сколько дней?', actor, 'chat', None, {'revision': '2'})['status'], 'clarify')
            updated = runtime.query('Упаковка вскрыта. Сколько хранится товар Родонит?', actor, 'chat', None, {'revision': '2'})
            self.assertIn('3 календарных', updated['text'])
            self.assertNotIn('1 календарный', updated['text'])
            self.assertEqual({item['revision'] for item in updated['evidence']}, {'2'})

    def test_hidden_old_text_layer_does_not_replace_visible_scan(self):
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        from ai_core.site_documents import extract_document
        writer = PdfWriter(clone_from=PdfReader(ASSETS / 'Редакция_Родонит_v2.pdf'))
        page = writer.pages[0]
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        resources = page['/Resources']
        resources[NameObject('/Font')] = DictionaryObject({NameObject('/Stale'): writer._add_object(font)})
        stream = DecodedStreamObject()
        stream.set_data(page.get_contents().get_data() + b'\nBT /Stale 12 Tf 3 Tr 10 650 Td (OLD HIDDEN SCAN OCR WRONG DATA NUMBER 999 days should never be the source for an answer) Tj ET\n')
        page[NameObject('/Contents')] = writer._add_object(stream)
        buffer = io.BytesIO()
        writer.write(buffer)
        self.assertIn('999', PdfReader(io.BytesIO(buffer.getvalue())).pages[0].extract_text())
        text, complete = extract_document('data:application/pdf;base64,' + base64.b64encode(buffer.getvalue()).decode(), 'hidden.pdf')
        self.assertTrue(complete)
        self.assertIn('11 календарных', text)
        self.assertNotIn('999', text)


if __name__ == '__main__':
    unittest.main()

~~~

## evaluation/closed_search/scan_assets.py

~~~python
"""Оригинальные растровые контрольные PDF, без текстового слоя и правил компании.

Запускать bundled Python: Pillow и reportlab нужны только для изготовления тестов.
Ожидания задаются здесь до изменения распознавания, а не выводятся из ответа OCR.
"""
import json
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'evaluation/closed_search/fixtures/scans'
PREVIEWS = ROOT / '.runtime/closed-search-lab-20261028/scan-assets'
FONT = 'C:/Windows/Fonts/arial.ttf'


def page(title, columns, heights, cells, *, angle=0, note=None, damaged=False):
    image = Image.new('RGB', (1800, 2100), 'white')
    draw = ImageDraw.Draw(image)
    font, small = ImageFont.truetype(FONT, 34), ImageFont.truetype(FONT, 30)
    draw.text((90, 70), title, font=font, fill='black')
    draw.text((90, 125), 'Учебный документ. Все правила вымышлены.', font=small, fill='black')
    xs, ys = [90], [260]
    for width in columns:
        xs.append(xs[-1] + width)
    for height in heights:
        ys.append(ys[-1] + height)
    for row, col, rowspan, colspan, text in cells:
        left, top, right, bottom = xs[col], ys[row], xs[col + colspan], ys[row + rowspan]
        draw.rectangle((left, top, right, bottom), outline='black', width=3)
        lines, current = [], ''
        for word in text.split():
            candidate = (current + ' ' + word).strip()
            if draw.textlength(candidate, font=font) > right - left - 40 and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
        for offset, line in enumerate(lines):
            draw.text((left + 20, top + 18 + offset * 43), line, font=font, fill='black')
    if note:
        for offset, line in enumerate(textwrap.wrap(note, 76)):
            draw.text((90, ys[-1] + 60 + offset * 44), line, font=small, fill='black')
    if damaged:
        # Физически пустая ячейка: нельзя достраивать число по соседней строке.
        draw.rectangle((xs[1] + 6, ys[2] + 6, xs[2] - 6, ys[3] - 6), fill='white')
    image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor='white')
    return image.filter(ImageFilter.GaussianBlur(0.25))


def headers():
    return [(0, 0, 2, 1, 'Товар'), (0, 1, 1, 2, 'Срок хранения'),
            (1, 1, 1, 1, 'Если упаковка не вскрыта'), (1, 2, 1, 1, 'Если упаковка вскрыта')]


def matrix(rows, title, angle=0, damaged=False):
    cells = headers()
    for row, values in enumerate(rows, 2):
        cells.extend((row, col, 1, 1, text) for col, text in enumerate(values))
    return page(title, [480, 570, 570], [110, 150] + [150] * len(rows), cells, angle=angle, damaged=damaged)


def write_pdf(name, pages):
    target = OUT / name
    if target.exists():
        raise ValueError('Do not overwrite frozen scan fixtures: ' + name)
    pdf = canvas.Canvas(str(target), pagesize=(600, 700), invariant=1)
    for number, image in enumerate(pages, 1):
        image.save(PREVIEWS / (target.stem + f'-page-{number}.png'))
        pdf.drawImage(ImageReader(image), 0, 0, 600, 700)
        pdf.showPage()
    pdf.save()
    if any((p.extract_text() or '').strip() for p in PdfReader(target).pages):
        raise ValueError('Fixture must be image-only')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    write_pdf('Сложная_таблица_Аметрин.pdf', [
        matrix([['Аметрин', '8 календарных дней', '2 календарных дня'], ['Турмалин', '12 календарных дней', '3 календарных дня']], 'Сроки хранения. Страница 1', angle=0.65),
        matrix([['Нефрит', '5 календарных дней', '1 календарный день']], 'Сроки хранения. Страница 2', angle=-0.45)])
    cells = [(0, 0, 1, 1, 'Товар'), (0, 1, 1, 1, 'Условие'), (0, 2, 1, 1, 'Срок хранения'),
             (1, 0, 2, 1, 'Хризолит'), (1, 1, 1, 1, 'Если упаковка не вскрыта'), (1, 2, 1, 1, '10 календарных дней'),
             (2, 1, 1, 1, 'Если упаковка вскрыта'), (2, 2, 1, 1, '4 календарных дня')]
    write_pdf('Объединённые_строки_Хризолит.pdf', [page('Хранение Хризолита', [450, 620, 550], [120, 180, 180], cells, angle=0.4)])
    cells = [(0, 0, 1, 1, 'Товар'), (0, 1, 1, 1, 'Срок хранения'),
             (1, 0, 1, 1, 'Родонит'), (1, 1, 1, 1, '7 календарных дней [1]')]
    write_pdf('Редакция_Родонит_v1.pdf', [page('Регламент Родонит. Редакция 1', [600, 1020], [120, 160], cells,
        note='[1]: Исключение: если упаковка вскрыта, товар Родонит хранится 1 календарный день.')])
    cells[-1] = (1, 1, 1, 1, '11 календарных дней [1]')
    write_pdf('Редакция_Родонит_v2.pdf', [page('Регламент Родонит. Редакция 2', [600, 1020], [120, 160], cells,
        note='[1]: Исключение: если упаковка вскрыта, товар Родонит хранится 3 календарных дня.')])
    write_pdf('Повреждённая_таблица_Коралл.pdf', [matrix([['Коралл', '9 календарных дней', '2 календарных дня']], 'Повреждённая таблица', damaged=True)])
    spec = {'documents': [
        {'id': 'lab_scan_matrix', 'name': 'Сложная_таблица_Аметрин.pdf'},
        {'id': 'lab_scan_rowspan', 'name': 'Объединённые_строки_Хризолит.pdf'},
        {'id': 'lab_scan_note', 'name': 'Редакция_Родонит_v2.pdf'},
        {'id': 'lab_scan_damaged', 'name': 'Повреждённая_таблица_Коралл.pdf'}], 'cases': [
        {'id': 'matrix-closed', 'source': 'lab_scan_matrix', 'q': 'Упаковка не вскрыта. Сколько хранится товар Аметрин?', 'has': ['8 календарных'], 'absent': ['2 календарных', '12 календарных']},
        {'id': 'matrix-open', 'source': 'lab_scan_matrix', 'q': 'Упаковка вскрыта. Сколько хранится товар Аметрин?', 'has': ['2 календарных'], 'absent': ['8 календарных']},
        {'id': 'matrix-neighbour', 'source': 'lab_scan_matrix', 'q': 'Упаковка вскрыта. Сколько хранится товар Турмалин?', 'has': ['3 календарных'], 'absent': ['2 календарных', '12 календарных']},
        {'id': 'matrix-page-two', 'source': 'lab_scan_matrix', 'q': 'Упаковка вскрыта. Сколько хранится товар Нефрит?', 'has': ['1 календарный'], 'absent': ['5 календарных']},
        {'id': 'matrix-clarify', 'source': 'lab_scan_matrix', 'q': 'Сколько хранится товар Аметрин?', 'status': 'clarify', 'has': ['упаковка'], 'absent': ['8 календарных', '2 календарных']},
        {'id': 'rowspan-closed', 'source': 'lab_scan_rowspan', 'q': 'Упаковка не вскрыта. Сколько хранится товар Хризолит?', 'has': ['10 календарных'], 'absent': ['4 календарных']},
        {'id': 'rowspan-open', 'source': 'lab_scan_rowspan', 'q': 'Упаковка вскрыта. Сколько хранится товар Хризолит?', 'has': ['4 календарных'], 'absent': ['10 календарных']},
        {'id': 'note-current', 'source': 'lab_scan_note', 'q': 'Упаковка вскрыта. Сколько хранится товар Родонит?', 'has': ['3 календарных'], 'absent': ['11 календарных', '1 календарный']},
        {'id': 'damaged', 'source': 'lab_scan_damaged', 'q': 'Упаковка не вскрыта. Сколько хранится товар Коралл?', 'status': 'unavailable', 'absent': ['9 календарных', '2 календарных']}
    ], 'holdout': [
        {'id': 'paraphrase', 'source': 'lab_scan_matrix', 'q': 'Товар Аметрин уже открыли. Упаковка вскрыта. Как долго его хранить?', 'has': ['2 календарных'], 'absent': ['8 календарных']},
        {'id': 'second-closed', 'source': 'lab_scan_matrix', 'q': 'Упаковка не вскрыта. Сколько дней можно хранить Нефрит?', 'has': ['5 календарных'], 'absent': ['1 календарный']}
    ]}
    (OUT / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'directory': str(OUT), 'pdfs': 5, 'pages': 6, 'cases': 9, 'holdout': 2}))


if __name__ == '__main__':
    main()

~~~

## evaluation/closed_search/scan_probe.py

~~~python
"""Замороженные сканы → настоящее OCR → поиск, без подстановки готового текста."""
import argparse
import base64
import json
import time
from pathlib import Path
from ai_core.site_documents import extract_document
from ai_core.site_search import ClosedSiteIndex, Principal

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / '.runtime/closed-search-lab-20261028'
ASSETS = ROOT / 'evaluation/closed_search/fixtures/scans'
ORIGIN = 'http://127.0.0.1:3040'


def extracted_sources(spec):
    sources, extraction = [], []
    for item in spec['documents']:
        data = (ASSETS / item['name']).read_bytes()
        started = time.perf_counter()
        text, complete = extract_document('data:application/pdf;base64,' + base64.b64encode(data).decode(), item['name'])
        extraction.append({'id': item['id'], 'complete': complete, 'seconds': round(time.perf_counter() - started, 2), 'text': text})
        sources.append({'id': item['id'], 'revision': 'scan-v1', 'title': item['name'], 'kind': 'document',
                        'url': ORIGIN + '/materials/' + item['id'], 'access': {'scope': 'workspace'},
                        'complete': complete, 'sections': [{'heading': item['name'], 'text': text}]})
    return sources, extraction


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='scan-probe')
    parser.add_argument('--holdout', action='store_true')
    args = parser.parse_args()
    spec = json.loads((ASSETS / 'spec.json').read_text(encoding='utf-8'))
    sources, extraction = extracted_sources(spec)
    index = ClosedSiteIndex([ORIGIN])
    index.publish(sources, index.begin('scan-test'))
    rows = []
    for item in spec['holdout' if args.holdout else 'cases']:
        answer = index.search(item['q'], Principal('a', 'staff'), item['id'])
        errors = []
        if answer['status'] != item.get('status', 'answer'):
            errors.append('status:' + answer['status'])
        for text in item.get('has', []):
            if text.casefold() not in answer['text'].casefold():
                errors.append('missing:' + text)
        for text in item.get('absent', []):
            if text.casefold() in answer['text'].casefold():
                errors.append('unexpected:' + text)
        if answer['status'] == 'answer' and {part['id'] for part in answer['evidence']} != {item['source']}:
            errors.append('source')
        rows.append({'id': item['id'], 'question': item['q'], 'pass': not errors, 'errors': errors, 'answer': answer})
    report = {'passed': sum(row['pass'] for row in rows), 'total': len(rows), 'extraction': extraction, 'cases': rows}
    path = LAB / (args.label + '-' + str(time.time_ns()) + '.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(path), 'passed': report['passed'], 'total': report['total'], 'failures': [row for row in rows if not row['pass']]}, ensure_ascii=False))
    if report['passed'] != report['total']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

~~~

## evaluation/closed_search/scan_diagnose.py

~~~python
"""Локальная диагностика только собственных вымышленных растровых файлов."""
import logging
import tempfile
from pathlib import Path
from ai_core import site_scan_tables as scans
from ai_core.site_documents import _command, _run, _ocr, _join_visual_lines
from .scan_probe import ASSETS


def main():
    logging.basicConfig(level=logging.INFO)
    original = scans.reliable
    def inspect(words):
        result = original(words)
        if not result:
            print('UNRELIABLE', [(word['text'], word['confidence']) for word in words])
        return result
    scans.reliable = inspect
    with tempfile.TemporaryDirectory() as directory:
        for name in ['Сложная_таблица_Аметрин.pdf', 'Объединённые_строки_Хризолит.pdf', 'Редакция_Родонит_v2.pdf']:
            prefix = Path(directory) / 'page'
            _run([_command('pdftoppm'), '-f', '1', '-l', '1', '-r', '220', '-png', '-singlefile', str(ASSETS / name), str(prefix)])
            result = scans.extract_scan(prefix.with_suffix('.png'), lambda path: _ocr(path, tsv=True), _join_visual_lines)
            print(name, result)


if __name__ == '__main__':
    main()

~~~

## evaluation/closed_search/scan_performance.py

~~~python
"""Время фонового чтения пяти страниц и кэша; не имитация длительной нагрузки."""
import hashlib
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_documents import PARSER_VERSION
from .scan_probe import ASSETS, LAB, ROOT, ORIGIN, extracted_sources


def main():
    spec = json.loads((ASSETS / 'spec.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='vates-scan-cache-check-') as directory:
        with patch('ai_core.site_documents.CACHE', Path(directory)):
            started = time.perf_counter()
            sources, extraction = extracted_sources(spec)
            cold = time.perf_counter() - started
            with patch('ai_core.site_documents._scan_text', side_effect=AssertionError('OCR repeated')):
                started = time.perf_counter()
                cached, _ = extracted_sources(spec)
                warm = time.perf_counter() - started
            if sources != cached:
                raise AssertionError('Cache content differs')
    index = ClosedSiteIndex([ORIGIN])
    index.publish(sources, index.begin('performance'))
    timings, failures = [], []
    # Во время вопроса запрещён запуск OCR даже после удаления временного кэша.
    with patch('ai_core.site_documents._scan_text', side_effect=AssertionError('OCR on query')):
        for cycle in range(20):
            for case in spec['cases'] + spec['holdout']:
                started = time.perf_counter()
                answer = index.search(case['q'], Principal('a', 'staff'), str(cycle) + case['id'])
                timings.append((time.perf_counter() - started) * 1000)
                valid = answer['status'] == case.get('status', 'answer')
                valid &= all(text.casefold() in answer['text'].casefold() for text in case.get('has', []))
                valid &= all(text.casefold() not in answer['text'].casefold() for text in case.get('absent', []))
                if not valid:
                    failures.append({'case': case['id'], 'cycle': cycle})
    timings.sort()
    report = {'mode': 'four-synthetic-pdfs-five-raster-pages-local-query-no-http', 'parser': PARSER_VERSION,
              'coldSeconds': round(cold, 3), 'warmSeconds': round(warm, 4),
              'files': [{key: value for key, value in item.items() if key != 'text'} for item in extraction],
              'requests': len(timings), 'failures': failures, 'queryP50Ms': round(timings[len(timings)//2], 2),
              'queryP95Ms': round(timings[int(len(timings)*.95)-1], 2),
              'engineHashes': {name: hashlib.sha256((ROOT / 'ai_core' / name).read_bytes()).hexdigest() for name in
                               ['site_documents.py','site_scan_tables.py','site_search_structure.py','site_search_reasoning.py','site_search_runtime.py','site_search_store.py','site_search_api.py']}}
    output = LAB / ('scan-performance-' + str(time.time_ns()) + '.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(output), **report}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

~~~

## evaluation/closed_search/scan_http.cjs

~~~javascript
/* Настоящие PDF загружаются только в изолированный каталог стенда. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {build}=require('./export_site.cjs');
const assets=path.join(__dirname,'fixtures/scans');
async function waitSnapshot(){
    const payload=build();
    const headers={'Content-Type':'application/json','X-Vates-Search-Key':process.env.VATES_CLOSED_SEARCH_KEY};
    const endpoint=process.env.VATES_CLOSED_SEARCH_URL;
    const response=await fetch(endpoint+'/snapshot',{method:'POST',headers,body:JSON.stringify(payload),signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw new Error('Snapshot submit failed '+response.status);
    const expected=(await response.json()).snapshot, deadline=Date.now()+90000;
    while(Date.now()<deadline){
        const status=await fetch(endpoint+'/status',{headers,signal:AbortSignal.timeout(5000)}).then(r=>r.json());
        if(status.state==='ready'&&status.snapshot===expected)return status;
        if(status.state==='failed')throw new Error('Snapshot extraction failed');
        await new Promise(resolve=>setTimeout(resolve,500));
    }
    throw new Error('Snapshot not ready before deadline');
}
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(assets,'spec.json'),'utf8'));
    const admin=await login('closed-admin');
    const catalog=await storage('tea_hub_urgent_files_v1',admin);
    for(const item of spec.documents){
        const bytes=await fs.readFile(path.join(assets,item.name));
        const data='data:application/pdf;base64,'+bytes.toString('base64');
        const entry={...item,hasSeparateData:true,section:'Проверка сканов ВатэсAi',size:bytes.length+' B',date:'Локальный тест'};
        const prior=catalog.find(doc=>doc.id===entry.id);
        if(prior){
            if(JSON.stringify(prior)!==JSON.stringify(entry)||await storage('file_data_'+entry.id,admin)!==data)throw new Error('Scan fixture differs '+entry.id);
        }else{
            await storage('file_data_'+entry.id,admin,data);
            catalog.push(entry);
        }
    }
    await storage('tea_hub_urgent_files_v1',admin,catalog);
    const ready=await waitSnapshot();
    const cookies={a:await login('closed-a'),b:await login('closed-b'),admin};
    const cases=[...spec.cases,...spec.holdout,
        {id:'summary-matrix',source:'lab_scan_matrix',documentId:'lab_scan_matrix',q:'Кратко перескажи выбранный файл',has:['Аметрин','8 календарных','2 календарных','Если упаковка']},
        {id:'summary-note',source:'lab_scan_note',documentId:'lab_scan_note',q:'Кратко перескажи выбранный файл',has:['11 календарных','3 календарных'],absent:['[p1-1]']},
        {id:'summary-damaged',documentId:'lab_scan_damaged',q:'Кратко перескажи выбранный файл',status:'unavailable'}];
    const rows=[];
    for(const [position,item] of cases.entries()){
        const actor=['a','b','admin'][position%3],started=performance.now();
        const {response,value}=await call('/api/ai-chat',{chatId:'scan-'+Date.now()+'-'+item.id,
            ...(item.documentId?{documentId:item.documentId}:{}),messages:[{role:'user',content:item.q}]},cookies[actor]);
        const answer=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(value.search_status!==(item.status||'answer'))errors.push('status:'+value.search_status);
        for(const text of item.has||[])if(!answer.toLowerCase().includes(text.toLowerCase()))errors.push('missing:'+text);
        for(const text of item.absent||[])if(answer.toLowerCase().includes(text.toLowerCase()))errors.push('unexpected:'+text);
        if((item.status||'answer')==='answer'&&!(value.sourceLinks||[]).some(link=>link.url.includes(item.source)))errors.push('source');
        if(value.search_status!=='answer'&&value.sourceLinks?.length)errors.push('unnecessary-links');
        rows.push({id:item.id,actor,question:item.q,answer,status:value.search_status,links:value.sourceLinks,ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    const report={createdAt:new Date().toISOString(),ready,total:rows.length,passed:rows.filter(row=>row.pass).length,cases:rows};
    const file=path.join(lab,'scan-http-'+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,total:report.total,passed:report.passed,failures:rows.filter(row=>!row.pass)}));
    if(report.passed!==report.total)process.exitCode=1;
}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1;});
module.exports={waitSnapshot,assets};

~~~

## evaluation/closed_search/scan_revisions.cjs

~~~javascript
/* Смена реальных PDF/прав: каждый ответ идёт через авторизацию сайта. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot,assets}=require('./scan_http.cjs');
const identifier='lab_scan_revision_probe';
async function main(){
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const initial=await storage('tea_hub_urgent_files_v1',cookies.admin);
    if(initial.some(item=>item.id===identifier))throw new Error('Unfinished temporary revision fixture exists');
    const rows=[],chat='scan-revision-'+Date.now();
    const v1='data:application/pdf;base64,'+(await fs.readFile(path.join(assets,'Редакция_Родонит_v1.pdf'))).toString('base64');
    const v2='data:application/pdf;base64,'+(await fs.readFile(path.join(assets,'Редакция_Родонит_v2.pdf'))).toString('base64');
    const entry={id:identifier,name:'Временный_регламент_Родонит.pdf',section:'Проверка смены редакций',hasSeparateData:true,allowedUserIds:['closed_lab_a']};
    async function change(update){
        const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
        await storage('tea_hub_urgent_files_v1',cookies.admin,update(catalog));
    }
    async function bytes(data){await storage('file_data_'+identifier,cookies.admin,data);}
    async function ask(id,{actor='a',q='Упаковка вскрыта. Сколько хранится товар Родонит?',statuses=['answer'],has=[],absent=[],selected=true,chatId=chat}={}){
        const {response,value}=await call('/api/ai-chat',{chatId,...(selected?{documentId:identifier}:{}),messages:[{role:'user',content:q}]},cookies[actor]);
        const answer=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(!statuses.includes(value.search_status))errors.push('status:'+value.search_status);
        for(const text of has)if(!answer.includes(text))errors.push('missing:'+text);
        for(const text of absent)if(answer.includes(text))errors.push('unexpected:'+text);
        if(value.search_status==='answer'&&!(value.sourceLinks||[]).some(link=>link.url.includes(identifier)))errors.push('wrong-source');
        if(value.search_status!=='answer'&&value.sourceLinks?.length)errors.push('links-on-refusal');
        rows.push({id,actor,question:q,answer,status:value.search_status,links:value.sourceLinks,pass:!errors.length,errors});
        console.log(JSON.stringify({id,pass:!errors.length,status:value.search_status,errors}));
    }
    let completed=false,cleanup=false,failure=null;
    try{
        await bytes(v1); await change(catalog=>[...catalog,entry]); await waitSnapshot();
        await ask('v1-exception',{has:['1 календарный'],absent:['3 календарных','7 календарных']});
        await ask('v1-base',{q:'Упаковка не вскрыта. Сколько хранится товар Родонит?',has:['7 календарных'],absent:['1 календарный','11 календарных']});
        await ask('v1-b-denied',{actor:'b',statuses:['not_found'],absent:['7 календарных','1 календарный']});
        await bytes(v2);
        await ask('immediate-replacement',{statuses:['processing','answer'],absent:['7 календарных','1 календарный']});
        await waitSnapshot();
        await ask('v2-old-chat',{has:['3 календарных'],absent:['7 календарных','1 календарный','11 календарных']});
        await ask('v2-base',{q:'Упаковка не вскрыта. Сколько хранится товар Родонит?',has:['11 календарных'],absent:['7 календарных']});
        await ask('v2-summary',{q:'Кратко перескажи выбранный файл',has:['11 календарных','3 календарных'],absent:['7 календарных','1 календарный']});
        // Повреждённая новая загрузка не даёт права вернуться к прежнему PDF.
        await bytes('data:application/pdf;base64,'+Buffer.from('%PDF-broken-test-file').toString('base64'));
        await ask('broken-immediate',{statuses:['processing','unavailable'],absent:['11 календарных','3 календарных']});
        await waitSnapshot();
        await ask('broken-ready',{statuses:['unavailable'],absent:['11 календарных','3 календарных']});
        await bytes(v2); await waitSnapshot();
        await ask('repaired-same-bytes',{has:['3 календарных'],absent:['1 календарный']});
        // Быстрые последовательные снимки: окончательная редакция всегда v2.
        await bytes(v1);
        const {build}=require('./export_site.cjs');
        const response=await fetch(process.env.VATES_CLOSED_SEARCH_URL+'/snapshot',{method:'POST',headers:{'Content-Type':'application/json','X-Vates-Search-Key':process.env.VATES_CLOSED_SEARCH_KEY},body:JSON.stringify(build()),signal:AbortSignal.timeout(15000)});
        if(!response.ok)throw new Error('Race snapshot submit failed');
        await bytes(v2); await waitSnapshot();
        await ask('rapid-revisions-final',{has:['3 календарных'],absent:['1 календарный','7 календарных']});
        await change(catalog=>catalog.map(item=>item.id===identifier?{...item,allowedUserIds:['closed_lab_b']}:item));
        await ask('revoked-a-immediately',{statuses:['processing','not_found'],absent:['11 календарных','3 календарных']});
        await waitSnapshot();
        await ask('revoked-a-after-index',{statuses:['not_found'],absent:['11 календарных','3 календарных']});
        await ask('b-new-access',{actor:'b',has:['3 календарных'],absent:['1 календарный']});
        await change(catalog=>catalog.map(item=>item.id===identifier?{...item,isArchived:true}:item));
        await ask('archived-immediate',{actor:'b',statuses:['processing','not_found'],absent:['3 календарных']});
        await waitSnapshot();
        await ask('archived-after-index',{actor:'b',statuses:['not_found'],absent:['3 календарных']});
        await change(catalog=>catalog.filter(item=>item.id!==identifier));
        await ask('removed-old-chat',{actor:'b',statuses:['processing','not_found'],absent:['3 календарных','11 календарных']});
        const denied=await call('/api/storage?key=file_data_'+identifier,undefined,cookies.b);
        rows.push({id:'orphan-bytes-denied',pass:denied.response.status===403,http:denied.response.status});
        completed=true;
    }catch(error){failure=error.message;throw error;}
    finally{
        try{
            // Удаляем только созданную этим сценарием временную запись.
            await change(catalog=>catalog.filter(item=>item.id!==identifier));
            await bytes(null); await waitSnapshot(); cleanup=true;
        }finally{
            const report={createdAt:new Date().toISOString(),completed,cleanup,failure,total:rows.length,passed:rows.filter(row=>row.pass).length,cases:rows};
            const file=path.join(lab,'scan-revisions-'+Date.now()+'.json');
            await fs.writeFile(file,JSON.stringify(report,null,2));
            console.log(JSON.stringify({file,completed,cleanup,total:report.total,passed:report.passed}));
            if(!completed||!cleanup||report.passed!==report.total)process.exitCode=1;
        }
    }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});

~~~

## evaluation/closed_search/verify_lab.cjs

~~~javascript
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
    const expectedDocuments = 9 + reasoningFixtures.documents.length + conditionFixtures.documents.length + structureFixtures.documents.length + structureAssets.length + scanFixtures.documents.length;
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

~~~

## evaluation/closed_search/acceptance.cjs

~~~javascript
/* Сквозные функциональные проверки собственного поиска через API тестового сайта. */
const { fs, path, lab, call, login, storage } = require('./lab_client.cjs');
const noData = 'В доступных материалах я не нашёл информации, которая однозначно отвечает на этот вопрос.';
async function main() {
    const cookies = { a: await login('closed-a'), b: await login('closed-b'), admin: await login('closed-admin') };
    const catalog = await storage('tea_hub_urgent_files_v1', cookies.admin);
    const doc = suffix => catalog.find(item => item.name.endsWith(suffix)).id;
    const cases = [
        { id: 'docx-deadline', q: 'Сколько обрабатывается заявка Север?', has: ['трёх', 'рабочих'], link: doc('заявок.docx') },
        { id: 'docx-contact', q: 'Кому направить заявку Север?', has: ['старшему'], link: doc('заявок.docx') },
        { id: 'txt-temperature', q: 'При какой температуре хранить образцы Лира?', has: ['16', '18'], link: doc('образцов.txt') },
        { id: 'txt-duration', q: 'Сколько хранить открытый образец Лира?', has: ['9', 'календарных'], link: doc('образцов.txt') },
        { id: 'txt-negation', q: 'Можно ли хранить образцы Лира рядом с бытовой химией?', has: ['нельзя'], link: doc('образцов.txt') },
        { id: 'md-calendar', q: 'Когда проходит инвентаризация склада Кедр?', has: ['2', '17'], link: doc('инвентаризации.md') },
        { id: 'md-time', q: 'До какого времени заполнить форму Кедр-учёт?', has: ['18:30'], link: doc('инвентаризации.md') },
        { id: 'csv-value', q: 'Сколько этикеток нужно для набора Янтарь?', has: ['2', 'этикеток'], link: doc('упаковки.csv') },
        { id: 'ocr-action', q: 'Что делать при повреждении упаковки поставки Кристалл?', has: ['фото', 'старшего'], link: doc('скан.pdf') },
        { id: 'ocr-checklist', q: 'Что сверить при приёмке поставки Кристалл?', has: ['накладную', 'число мест'], link: doc('скан.pdf') },
        { id: 'ocr-marker', q: 'Какая контрольная отметка у поставки Кристалл?', has: ['7193'], link: doc('скан.pdf') },
        { id: 'expired-doc', q: 'Какой срок процедуры Спектр?', status: 'not_found', exact: noData },
        { id: 'conflicting-documents', q: 'Какой срок процесса Орион?', status: 'conflict', has: ['противоречие', 'администратора'] },
        { id: 'admin-private', user: 'admin', q: 'Какая контрольная отметка процедуры Борей?', has: ['8246'], link: 'policy-admin' },
        { id: 'staff-private', q: 'Какая контрольная отметка процедуры Борей?', status: 'not_found', exact: noData },
        { id: 'staff-private-selected', q: 'Какая контрольная отметка процедуры Борей?', documentId: 'policy-admin', status: 'not_found', exact: noData },
        { id: 'unknown-rule', q: 'Сколько часов длится доставка заказа Вега?', status: 'not_found', exact: noData },
        { id: 'outside-knowledge', q: 'Какая сегодня погода в Минске?', status: 'not_found', exact: noData },
        { id: 'topic-policy', q: 'Как настроить VPN?', exact: 'Я не могу ответить на данный вопрос.' },
        { id: 'followup-start', user: 'a', chat: 'memory-a', q: 'Как направить заявку Север?', has: ['старшему'], link: doc('заявок.docx') },
        { id: 'followup-duration', user: 'a', chat: 'memory-a', q: 'А сколько дней?', has: ['трёх'], context: true },
        { id: 'new-chat', user: 'a', chat: 'memory-fresh', q: 'А сколько дней?', status: 'clarify' },
        { id: 'other-user', user: 'b', chat: 'memory-a', q: 'А сколько дней?', status: 'clarify' },
        { id: 'topic-shift', user: 'a', chat: 'memory-a', q: 'А как хранить образцы Лира?', has: ['16', '18'], link: doc('образцов.txt'), context: false },
        { id: 'typo', q: 'Сколько обрабатываются заяки Север?', has: ['трёх'], link: doc('заявок.docx') },
        { id: 'paraphrase', q: 'Какие перчатки нужны для мытья пола внутри помещения?', has: ['только резиновые'], absent: ['улицы'], link: 'route_1' },
        { id: 'actions-facet', q: 'Что сделать при открытии смены?', has: ['осмотр', 'охраны'], absent: ['20.01'], link: 'route_4' },
        { id: 'loyalty-principle', q: 'Как устроена программа лояльности?', has: ['1000', '12%'], link: 'route_8' },
        { id: 'speak-facet', q: 'Что говорить гостю при приветствии?', has: ['сударь', 'сударыня'], absent: ['5 секунд'], link: 'route_9' },
        { id: 'identify-tea', q: 'Какой чай имеет вкус семечек?', has: ['Лунцзин', 'семечки'], link: 'm3_1' },
    ];
    const results = [];
    for (const [caseIndex, item] of cases.entries()) {
        // Независимые вопросы распределяем между двумя сотрудниками.
        // Явные профили сценариев доступа и продолжения диалога сохраняются.
        const user = item.user || (caseIndex % 2 ? 'b' : 'a');
        const started = performance.now();
        const result = await call('/api/ai-chat', { chatId: item.chat || 'acceptance-' + item.id,
            ...(item.documentId ? { documentId: item.documentId } : {}), messages: [{ role: 'user', content: item.q }] }, cookies[user]);
        const value = result.value;
        const answer = value.output_text || '';
        const errors = [];
        if (result.response.status !== 200) errors.push('http:' + result.response.status);
        if (item.status && value.search_status !== item.status) errors.push('status:' + value.search_status);
        if (!item.status && !item.exact && value.search_status !== 'answer') errors.push('not-answer');
        if (item.exact && answer !== item.exact) errors.push('exact-text');
        for (const word of item.has || []) if (!answer.toLowerCase().includes(word.toLowerCase())) errors.push('missing:' + word);
        for (const word of item.absent || []) if (answer.toLowerCase().includes(word.toLowerCase())) errors.push('unwanted:' + word);
        if (item.link && !(value.sourceLinks || []).some(link => link.url.includes(item.link))) errors.push('source');
        if (['not_found', 'clarify', 'conflict'].includes(value.search_status) && value.sourceLinks?.length) errors.push('unnecessary-links');
        if (item.context !== undefined && value.context_used !== item.context) errors.push('context');
        if ((value.sourceLinks || []).some(link => !link.url.startsWith('/tasks?') && !link.url.startsWith('/closed-search-material?'))) errors.push('external-link');
        const row = { id: item.id, question: item.q, user, status: value.search_status,
            answer, links: value.sourceLinks || [], elapsedMs: Math.round(performance.now() - started), pass: !errors.length, errors };
        results.push(row); console.log(JSON.stringify(row));
    }
    // ACL проверяется также на выдаче каталога и самих байтов, а не только в ответах ИИ.
    for (const user of ['a', 'b']) {
        const visible = await storage('tea_hub_urgent_files_v1', cookies[user]);
        const hidden = await call('/api/storage?key=file_data_policy-admin', undefined, cookies[user]);
        const preview = await call('/api/document-preview', { fileId: 'policy-admin', fileName: 'Служебная_проверка_доступа.txt' }, cookies[user]);
        const pass = !visible.some(item => item.id === 'policy-admin' || item.id === 'policy-archive')
            && hidden.response.status === 403 && preview.response.status === 404;
        const row = { id: 'document-access-' + user, pass, catalogVisible: visible.length,
            fileHttpStatus: hidden.response.status, previewHttpStatus: preview.response.status };
        results.push(row); console.log(JSON.stringify(row));
    }
    const times = results.filter(row => row.elapsedMs !== undefined).map(row => row.elapsedMs).sort((a, b) => a - b);
    const report = { mode: 'authenticated-test-site-to-own-search', createdAt: new Date().toISOString(),
        passed: results.filter(row => row.pass).length, total: results.length,
        medianMs: times[Math.floor(times.length / 2)], p95Ms: times[Math.ceil(times.length * .95) - 1], cases: results };
    const output = path.join(lab, `acceptance-${Date.now()}.json`);
    await fs.writeFile(output, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ report: output, passed: report.passed, total: report.total, medianMs: report.medianMs, p95Ms: report.p95Ms }));
    if (report.passed !== report.total) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });

~~~

## requirements.txt

~~~text
# Основной стек проекта собственной нейросети.
torch>=2.2,<3.0
numpy>=2.0,<3.0
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
pydantic>=2.0,<3.0
pypdf==6.21.4
snowballstemmer==3.1.1
opencv-python-headless==4.13.0.92

~~~
