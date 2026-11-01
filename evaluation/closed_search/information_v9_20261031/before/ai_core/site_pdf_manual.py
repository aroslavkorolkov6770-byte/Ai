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
