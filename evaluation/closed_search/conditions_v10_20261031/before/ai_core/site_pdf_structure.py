"""Геометрический разбор технических PDF с рамками, колонками и таблицами.

Никаких ответов или словарей конкретного документа здесь нет. Рамка чертежа
не является таблицей данных. Неоднозначные области сохраняются отдельно и
не могут служить подтверждением факта.
"""
from __future__ import annotations

import re
from pathlib import Path
import pdfplumber
from pdfplumber.table import TableFinder
from .site_document_cache import MAX_PDF_PAGES, MAX_PDF_CHARACTERS, read_checkpoint, write_checkpoint

LOCAL = '[LOCAL_STRUCTURE_UNCERTAIN]'
PROFILE = '[PDF_LOCAL_SECTIONS]'
HEADER = re.compile(r'наименование|название|масса|размер|сечение|тепловыделение|номинал|количество|характеристик|значение|чертеж|клемма|мощность|напряжение|ток|кпд|тип', re.I)


def compact(text):
    return re.sub(r'\s+', ' ', text or '').strip()


def lines(words):
    rows = []
    for word in sorted(words, key=lambda item: (round(item['top'] / 3), item['x0'])):
        row = next((row for row in reversed(rows[-3:]) if abs(row[0]['top'] - word['top']) < 4), None)
        if row is None:
            rows.append([word])
        else:
            row.append(word)
    return [{'text': compact(' '.join(w['text'] for w in sorted(row, key=lambda item: item['x0']))),
             'words': sorted(row, key=lambda item: item['x0']),
             'x0': min(w['x0'] for w in row), 'x1': max(w['x1'] for w in row),
             'top': min(w['top'] for w in row), 'bottom': max(w['bottom'] for w in row)} for row in rows]


def in_box(word, box):
    x, y = (word['x0'] + word['x1']) / 2, (word['top'] + word['bottom']) / 2
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def footer_box(page, words):
    labels = [word for word in words if re.fullmatch(r'Изм[.]?', word['text'])
              and word['top'] > page.height * .7 and word['x0'] > page.width * .4]
    if not labels:
        return None
    label = labels[0]
    edges = [edge for edge in page.edges if edge['orientation'] == 'h' and edge['x0'] > page.width * .5
             and edge['x1'] > page.width * .95 and label['top'] - page.height * .12 <= edge['top'] <= label['top']]
    if not edges:
        return None
    return (min(label['x0'], min(edge['x0'] for edge in edges)) - 3,
            min(edge['top'] for edge in edges) - 2, page.width, page.height)


def recognizes(document):
    """Профиль включается по повторяющейся инженерной рамке, не по имени файла."""
    count = 0
    for page in document.pages[:4]:
        if page.width > page.height * 1.2 and footer_box(page, page.extract_words()):
            count += 1
    return count >= 2


def ordered_columns(items):
    """Разделяем только доказанный пустой вертикальный промежуток между блоками."""
    if len(items) < 2:
        return [items]
    spans = sorted((item['x0'], item['x1']) for item in items)
    merged = []
    for left, right in spans:
        if merged and left <= merged[-1][1] + 10:
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    cuts = [(a[1] + b[0]) / 2 for a, b in zip(merged, merged[1:]) if b[0] - a[1] >= 12]
    if not cuts:
        return [items]
    groups = [[] for _ in range(len(cuts) + 1)]
    for item in items:
        group = sum(item['x0'] >= cut for cut in cuts)
        groups[group].append(item)
    return [group for group in groups if group]


def table_candidates(page, footer):
    """Дополняем обычный поиск таблиц границами без внешней рамки и штампа."""
    tables = list(page.find_tables())
    edges = [edge for edge in page.edges if edge['x0'] > page.width * .05
             and not (edge['orientation'] == 'v' and edge['height'] > page.height * .85)
             and not (edge['orientation'] == 'h' and edge['width'] > page.width * .9)
             and not (footer and edge['x0'] >= footer[0] and edge['top'] >= footer[1])]
    vertical = [edge for edge in edges if edge['orientation'] == 'v']
    horizontal = [edge for edge in edges if edge['orientation'] == 'h']
    if len(vertical) >= 2 and len(horizontal) >= 2:
        tables.extend(TableFinder(page, {'vertical_strategy': 'explicit', 'horizontal_strategy': 'explicit',
            'explicit_vertical_lines': vertical, 'explicit_horizontal_lines': horizontal,
            'snap_tolerance': 3, 'intersection_tolerance': 4}).tables)
    accepted = []
    for table in tables:
        x0, y0, x1, y1 = table.bbox
        if x1 - x0 > page.width * .9 or y1 - y0 < 20 or x1 - x0 < 120:
            continue
        if footer and x0 >= footer[0] - 3 and y0 >= footer[1] - 3:
            continue
        data = table.extract()
        if not data or len(data[0]) < 2 or len(data[0]) > 12:
            continue
        header = [compact(cell) for cell in data[0]]
        proper = sum(bool(HEADER.search(cell)) for cell in header) >= 2
        numbered = bool(re.fullmatch(r'\d+', header[0]))
        if not proper and not numbered:
            continue
        # Один участок не разбираем дважды разными стратегиями границ.
        if any(abs(item['box'][0]-x0) < 6 and abs(item['box'][1]-y0) < 6 for item in accepted):
            continue
        boundaries = sorted({cell[0] for cell in table.rows[0].cells if cell} | {x1})
        widths = [(point-x0)/(x1-x0) for point in boundaries]
        accepted.append({'box': table.bbox, 'data': data, 'proper': proper, 'widths': widths,
                         'row_boxes': [[cell for cell in row.cells if cell] for row in table.rows],
                         'x0': x0, 'x1': x1, 'top': y0, 'bottom': y1})
    return sorted(accepted, key=lambda item: (round(item['x0'] / 15), item['top']))


def body_words(page, words, footer):
    return [word for word in words if word.get('upright', True) and word['x0'] > page.width * .055
            and word['x1'] < page.width * .985 and word['top'] > page.height * .012
            and not (word['x0'] > page.width * .94 and word['top'] < page.height * .06 and word['text'].isdigit())
            and word['bottom'] < page.height * .975 and not (footer and in_box(word, footer))]


def note_definitions(words, tables, page_number, regions=None):
    """Сноски со звёздочкой и номера читаются в явной области «Примечание»."""
    outside = [word for word in words if not any(in_box(word, table['box']) for table in tables)]
    definitions, excluded = {}, []
    for column in ordered_columns(outside):
        rows = lines(column)
        start = next((i for i, row in enumerate(rows) if re.match(r'^Примечани[ея]\b', row['text'], re.I)), None)
        if start is None:
            continue
        selected = rows[start:]
        # В область примечаний не должны попасть новые таблицы/разделы ниже неё.
        key, value, source_rows = None, [], []
        for row in selected:
            match = re.match(r'^(?:(\d+)\.\s*)?(\*{1,4})\s*[-–—]?\s*(.+)$', row['text'])
            numeric = re.match(r'^(\d+)\.\s+(.+)$', row['text'])
            if match or numeric:
                if key:
                    definitions[key] = compact(' '.join(value))
                    if regions is not None:
                        regions[key] = list(source_rows)
                key = 'star-' + str(len(match[2])) if match else 'note-' + numeric[1]
                value = [match[3] if match else numeric[2]]
                source_rows = [row]
            elif key:
                value.append(row['text'])
                source_rows.append(row)
        if key:
            definitions[key] = compact(' '.join(value))
            if regions is not None:
                regions[key] = list(source_rows)
        if definitions:
            excluded.extend(selected)
    return {key: (f'pdf-{page_number}-{key}', text) for key, text in definitions.items()}, excluded


def numbered_paragraphs(rows, page_number, regions=None):
    """Сохраняем пункт целиком, включая списки, условия и переносы строк."""
    result, current, title = [], [], f'Страница {page_number}'
    source_rows = []

    def record():
        # Координаты идут из тех же строк, из которых собран абзац. Сам текст
        # не меняется: сохранённые ответы и адреса фрагментов остаются прежними.
        if regions is not None:
            regions.append({'heading': title, 'text': compact(' '.join(current)),
                            'kind': 'paragraph', 'page': page_number,
                            'words': [word for item in source_rows for word in item.get('words', [])]})
    for row in rows:
        text = row['text']
        match = re.match(r'^(\d+(?:\.\d+)+)\s+(.+)', text)
        heading = re.match(r'^(\d+)[. ]\s*([А-ЯЁ][^.!?]{2,110})$', text)
        if match or heading:
            if current:
                record()
                result.append('# ' + title + '\n' + compact(' '.join(current)))
            source_rows = []
            if match:
                title = 'Раздел ' + match[1] + ' — страница ' + str(page_number)
                current = [match[2]]
                source_rows = [row]
            else:
                title = f'Страница {page_number} — ' + text
                current = []
        else:
            definition = re.match(r'^([А-ЯЁ][^–—]{1,95})\s+[–—-]\s+(?!\d|±|не более|не менее)', text)
            if current and definition and re.search(r'[.;:]$', current[-1]):
                record()
                result.append('# ' + title + '\n' + compact(' '.join(current)))
                current = []
                source_rows = []
                title = f'Страница {page_number} — ' + definition[1].strip()
            current.append(text)
            source_rows.append(row)
    if current:
        record()
        result.append('# ' + title + '\n' + compact(' '.join(current)))
    return result


def read_native_page(page, page_number, regions=None):
    words = page.extract_words(x_tolerance=2, y_tolerance=3)
    footer = footer_box(page, words)
    words = body_words(page, words, footer)
    tables = table_candidates(page, footer)
    note_regions = {}
    notes, note_rows = note_definitions(words, tables, page_number, note_regions)
    outside = [word for word in words if not any(in_box(word, table['box']) for table in tables)
               and not any(in_box(word, (row['x0']-1, row['top']-1, row['x1']+1, row['bottom']+1)) for row in note_rows)]
    # Схема с проводниками и короткими метками не является связным правилом.
    narrative = sum(len(word['text']) for word in outside if re.fullmatch(r'[А-Яа-яЁё-]{5,}', word['text']))
    if not any(table['proper'] for table in tables) and len(page.edges) > 180 and narrative < sum(len(word['text']) for word in outside) * .45:
        return '# Страница ' + str(page_number) + ' — схема\n' + LOCAL + '\n' + compact(' '.join(word['text'] for word in words))
    blocks = []
    previous = None
    used_outside = set()
    for table in tables:
        data = [[compact(cell) for cell in row] for row in table['data']]
        near = [word for word in outside if table['x0']-3 <= (word['x0']+word['x1'])/2 <= table['x1']+3
                and table['top']-65 <= word['top'] and word['bottom'] <= table['top']+1]
        caption_lines = lines(near)
        caption = compact(' '.join(row['text'] for row in caption_lines))
        explicit_caption = [row['text'] for row in caption_lines if re.match(r'^Таблица\s+\d', row['text'], re.I)]
        if explicit_caption:
            caption = explicit_caption[-1]
        if table['proper']:
            headers, rows = data[0], data[1:]
            header_boxes, row_boxes = table['row_boxes'][0], table['row_boxes'][1:]
            # Отсутствующая черта под заголовком не должна превращать первую
            # строку данных в название колонки. Разделяем только согласованные
            # многострочные ячейки; при сомнении оставляем область непроверенной.
            raw_header = [(cell or '').splitlines() for cell in table['data'][0]]
            if all(len(cell) >= 3 for cell in raw_header) and all(HEADER.search(' '.join(cell[:-1])) for cell in raw_header) and all(not HEADER.search(cell[-1]) for cell in raw_header):
                headers = [compact(' '.join(cell[:-1])) for cell in raw_header]
                rows.insert(0, [compact(cell[-1]) for cell in raw_header])
                row_boxes.insert(0, table['row_boxes'][0])
            if any(re.fullmatch(r'Стр[.]?|Страница', header, re.I) for header in headers):
                blocks.append(f'# Страница {page_number} — содержание\n' + LOCAL + '\n' + compact(str(data)))
                used_outside.update(id(word) for word in near)
                continue
        elif previous and len(data[0]) == len(previous['headers']) and re.fullmatch(r'\d+', data[0][0]) and previous['last'] is not None and int(data[0][0]) == previous['last']+1 and len(table['widths']) == len(previous['widths']) and all(abs(a-b) < .025 for a,b in zip(table['widths'], previous['widths'])):
            # Продолжение в соседней колонке подтверждается номерами строк и
            # пропорциями столбцов; одного похожего текста недостаточно.
            headers, rows, caption = previous['headers'], data, previous['caption']
            header_boxes, row_boxes = previous['header_boxes'], table['row_boxes']
        else:
            blocks.append('# Страница ' + str(page_number) + ' — таблица\n' + LOCAL + '\n' + compact(str(data)))
            continue
        used_outside.update(id(word) for word in near)
        group = ''
        group_boxes = []
        row_numbers = []
        for row_index, row in enumerate(rows):
            cell_boxes = row_boxes[row_index] if row_index < len(row_boxes) else []
            if not any(row):
                continue
            if row[0] and not any(row[1:]):
                group = row[0]
                group_boxes = cell_boxes
                continue
            if re.fullmatch(r'\d+', row[0]):
                row_numbers.append(int(row[0]))
            fields = []
            references = set()
            for header, value in zip(headers, row):
                if not value or header.strip() == '№':
                    continue
                def bind_note(match):
                    references.add('star-'+str(len(match[0])))
                    note = notes.get('star-'+str(len(match[0])))
                    # Ограничение остаётся рядом именно со своим значением:
                    # соседнее число без звёздочки не получает ту же сноску.
                    return ' (' + note[1].rstrip('.') + ')' if note else f'[pdf-{page_number}-missing-star-{len(match[0])}]'
                value = re.sub(r'\*{1,4}', bind_note, value)
                fields.append(header + ': ' + value)
            body = '; '.join(fields) + '.'
            # Без найденного значения не придумываем пустую ячейку или соседнюю строку.
            label = compact(' '.join([caption, group])) or f'Таблица на странице {page_number}'
            blocks.append('# Страница ' + str(page_number) + ' — ' + label + '\n' + body)
            if regions is not None:
                related = [item for key in references for item in note_regions.get(key, [])]
                caption_boxes = [(item['x0'], item['top'], item['x1'], item['bottom']) for item in caption_lines]
                note_boxes = [(item['x0'], item['top'], item['x1'], item['bottom']) for item in related]
                regions.append({'heading': f'Страница {page_number} — ' + label, 'text': body,
                                'kind': 'table', 'page': page_number,
                                'rects': cell_boxes + header_boxes + group_boxes + caption_boxes + note_boxes,
                                'row_rects': cell_boxes, 'note_rects': note_boxes})
        previous = {'headers': headers, 'last': max(row_numbers) if row_numbers else None, 'caption': caption, 'widths': table['widths'], 'header_boxes': header_boxes}
    remaining = [word for word in outside if id(word) not in used_outside]
    # Окончание абзаца в следующей колонке сохраняет тот же заголовок.
    ordered_rows = [row for column in ordered_columns(remaining) for row in lines(column)]
    blocks.extend(numbered_paragraphs(ordered_rows, page_number, regions))
    for identifier, text in notes.values():
        blocks.append(f'[{identifier}]: {text}')
    return '\n\n'.join(blocks)


def extract_technical_pdf(file: Path, cache: Path, fingerprint: str, scan_reader, progress=None):
    """Возвращает None для обычного PDF; прежний извлекатель остаётся доступен."""
    with pdfplumber.open(file) as document:
        if not recognizes(document):
            return None
        pages, total, characters, previous_section = [], len(document.pages), 0, None
        for number, page in enumerate(document.pages[:MAX_PDF_PAGES], 1):
            if progress:
                progress({'pages_read': number-1, 'pages_total': total})
            checkpoint = cache / (fingerprint + '-structured-pages') / (str(number) + '.json')
            saved = read_checkpoint(checkpoint)
            if isinstance(saved, dict) and isinstance(saved.get('text'), str):
                text = saved['text']
            elif len(page.chars) > 30:
                text = read_native_page(page, number)
                write_checkpoint(checkpoint, {'text': text})
            else:
                text = scan_reader(number)
                # Явный лист утверждения изолируется как метаданные. Обычная
                # нечитаемая страница по-прежнему закрывает весь документ.
                if re.search(r'лист\s+утверждения', text, re.I) and re.search(r'согласовано|утверждаю', text, re.I):
                    text = '# Лист утверждения\n' + LOCAL + '\n' + text.replace('[STRUCTURE_UNCERTAIN]', '')
                write_checkpoint(checkpoint, {'text': text})
            if previous_section and re.match(rf'^# Страница {number}\n[•]', text):
                # Список продолжается на следующей странице; переносим только
                # его заголовок, не значения соседнего раздела.
                text = text.replace(f'# Страница {number}\n', f'# Продолжение раздела {previous_section[0]} — страница {number} — {previous_section[1]}\n', 1)
            numbered = list(re.finditer(r'^# Раздел (\d+(?:\.\d+)+)[^\n]*\n([^#]+)', text, re.M))
            previous_section = None
            if numbered:
                last = numbered[-1]
                if '' in last[2] or '•' in last[2]:
                    previous_section = (last[1], re.split(r'[•]', last[2])[0].strip()[:160])
            pages.append(text)
            characters += len(text)
            page.close()
            if characters > MAX_PDF_CHARACTERS:
                break
        complete = len(pages) == total and characters <= MAX_PDF_CHARACTERS
        if progress:
            progress({'pages_read': len(pages), 'pages_total': total})
        text = PROFILE + '\n' + '\n\n'.join(pages)
        if not complete:
            text = '[STRUCTURE_UNCERTAIN]\n' + text[:MAX_PDF_CHARACTERS]
        return text, complete
