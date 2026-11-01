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
