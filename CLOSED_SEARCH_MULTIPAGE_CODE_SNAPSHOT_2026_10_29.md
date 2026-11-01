# Полные листинги: многостраничные таблицы ВатэсAi

29.10.2026. Полный код изменённых файлов и замороженный эталон. Без сокращений.

## ai_core/site_scan_layout.py

SHA256: 89f2570621965aea5d35bd103b90177ed39ca0348548e7c81cf5bcda7862d02a

````py
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
TABLE_PENDING = re.compile(r'Продолжение таблицы на следующей странице[.]?', re.I)
CONTINUATION = re.compile(r'^Продолжение\s+таблицы\s+\d+\b', re.I | re.M)
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


def _pending_kind(text):
    """Маркер строки нельзя путать с переносом целой таблицы с новыми строками."""
    row, table = bool(PENDING.search(text)), bool(TABLE_PENDING.search(text))
    if row and table:
        raise ValueError('scan_continuation_ambiguous_marker')
    return 'row' if row else 'table' if table else None


def _simple_matrix(table):
    """Для переносов подтверждаем полную прямоугольную сетку и одну строку шапки."""
    cells, rows, cols = table['cells'], table['rows'], table['cols']
    if rows < 2 or cols < 2 or rows > 80 or cols > 20 or len(cells) != rows * cols:
        raise ValueError('scan_continuation_shape')
    positions = {(c.top, c.left): c for c in cells}
    if len(positions) != len(cells) or any(c.bottom-c.top != 1 or c.right-c.left != 1 or not c.text.strip() for c in cells):
        raise ValueError('scan_continuation_cells')
    if set(positions) != {(r,c) for r in range(rows) for c in range(cols)}:
        raise ValueError('scan_continuation_grid')
    return [[deepcopy(positions[r,c]) for c in range(cols)] for r in range(rows)]


def _merge_chain(pages, by_page, start, describe):
    """Собираем копию всей цепочки. При ошибке ни одна страница не заменяется."""
    captions = CAPTION.findall(pages[start])
    if len(captions) != 1 or CONTINUATION.search(pages[start]):
        raise ValueError('scan_continuation_start')
    number = captions[0]
    chain, matrix = [], None
    index, previous_kind = start, None
    while True:
        tables = by_page.get(index+1, [])
        if len(tables) != 1 or CAPTION.findall(pages[index]) != [number]:
            raise ValueError('scan_continuation_caption')
        table = tables[0]
        if not table['text'] or pages[index].count(table['text']) != 1:
            raise ValueError('scan_continuation_text_mismatch')
        incoming = _simple_matrix(table)
        kind = _pending_kind(pages[index])
        if matrix is None:
            matrix = incoming
            if any(CONTINUED.search(row[0].text) for row in matrix[1:]):
                raise ValueError('scan_continuation_orphan_row')
        else:
            if not CONTINUATION.search(pages[index]):
                raise ValueError('scan_continuation_missing_caption')
            normalize = lambda value: ' '.join(value.split()).casefold()
            if [normalize(c.text) for c in matrix[0]] != [normalize(c.text) for c in incoming[0]]:
                raise ValueError('scan_continuation_headers')
            extra = incoming[1:]
            if previous_kind == 'row':
                parent, child = matrix[-1], extra[0]
                if not CONTINUED.search(child[0].text) or normalize(CONTINUED.sub('',child[0].text)) != normalize(parent[0].text):
                    raise ValueError('scan_continuation_subject')
                for old, new in zip(parent[1:], child[1:]):
                    if re.search(r'[.!?]$',old.text.rstrip()):
                        raise ValueError('scan_continuation_complete_cell')
                    old.text = old.text.rstrip() + ' ' + new.text.lstrip()
                    # Последний фрагмент должен завершать фразу. Маркер следующего
                    # переноса относится к последней строке, не ко всем строкам листа.
                    if (len(extra)>1 or kind != 'row') and not re.search(r'[.!?](?:\s*\[\d+\])*$',new.text.rstrip()):
                        raise ValueError('scan_continuation_incomplete_end')
                extra = extra[1:]
            subjects = {normalize(row[0].text) for row in matrix[1:]}
            for row in extra:
                subject = normalize(row[0].text)
                if CONTINUED.search(row[0].text) or subject in subjects:
                    raise ValueError('scan_continuation_duplicate_row')
                subjects.add(subject)
                matrix.append(row)
        chain.append((index,table))
        if len(matrix)>80 or len(matrix)*len(matrix[0])>500:
            raise ValueError('scan_continuation_budget')
        if not kind:
            break
        previous_kind = kind
        index += 1
        if index >= len(pages):
            raise ValueError('scan_continuation_missing_page')
    for row, cells in enumerate(matrix):
        for cell in cells:
            cell.top, cell.bottom = row, row+1
    merged = describe([c for row in matrix for c in row],len(matrix),len(matrix[0]),start+1,chain[0][1]['ordinal'])
    label = f'{start+1} и {index+1}' if index == start+1 else f'{start+1}-{index+1}'
    merged = merged.replace(f'# Страница {start+1},',f'# Страницы {label},')
    return chain, merged


def compose_pages(pages, metadata, describe):
    """Связываем соседние страницы только по явным печатным признакам."""
    output, consumed, issue = list(pages), set(), False
    by_page = {}
    for table in metadata:
        by_page.setdefault(table['page'],[]).append(table)
    roots = set()
    for index, text in enumerate(pages):
        if index in consumed:
            continue
        try:
            if CONTINUATION.search(text):
                raise ValueError('scan_continuation_orphan_page')
            captions = CAPTION.findall(text)
            # Повторный независимый номер таблицы делает область её сносок неясной.
            if any(number in roots for number in captions):
                raise ValueError('scan_duplicate_table_number')
            roots.update(captions)
            if not _pending_kind(text):
                continue
            chain, merged = _merge_chain(pages,by_page,index,describe)
            for position, table in chain:
                value = output[position].replace(table['text'],merged if position == index else '')
                output[position] = TABLE_PENDING.sub('',PENDING.sub('',value))
                consumed.add(position)
        except ValueError:
            issue = True
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

## ai_core/site_documents.py

SHA256: 2bc880d19051c93439c3bc788197e017cbb96a2c5b9cba431cd749ee3e800d53

````py
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
PARSER_VERSION = "closed-documents-v7-multipage-" + hashlib.sha256(
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

## ai_core/site_search.py

SHA256: 90b13a9b64e6e0dd4e3d747836d075c27c80d6b0ae645c98f8646779b35405ad

````py
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
from .site_search_conditions import guarded, paragraphs, rules, atoms, atomic_observation, resolve, summary_request, operation_names, has_related_exception
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
            title_hits = sum(any(affinity(word, token) for token in item.title_words) for word in words)
            if not scoped and not named and title_hits < 2:
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

## ai_core/site_search_conditions.py

SHA256: d9b2263003a578313014b9af1112a12399d1a26d8984e5b82cf00e3095f4ae72

````py
"""Ограниченный разбор условий с тремя исходами: да, нет, неизвестно.

Правила и формулировки ответа берутся из документа. Этот модуль не дополняет
неизвестные факты и не исполняет выражения из загруженного текста.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from .site_search_reasoning import RELATIONS

GUARD = re.compile(r"\b(?:если|при условии|исключени\w*|в остальных случаях|за исключением|кроме случаев|только при)\b", re.I)
UNKNOWN = re.compile(r"не знаю|не уверен|неизвестн|возможно|наверно|не помню|неясно", re.I)


def guarded(text: str) -> bool:
    return bool(GUARD.search(text) or re.search(r"(?:^|[.!?]\s+)При\s+[^,]+,", text))


def operation_names(text: str) -> frozenset[str]:
    return frozenset(name for name, pattern in RELATIONS if re.search(pattern, text.casefold()))


def paragraphs(text: str) -> list[str]:
    """Сохраняем исключения в том же разделе, в том числе после пустой строки."""
    parts = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    joined = []
    for part in parts:
        if joined and not re.search(r'[.!?:;]$', joined[-1]) and re.match(r'[а-яa-z]', part):
            joined[-1] += ' ' + part
        else:
            joined.append(part)
    parts = joined
    return [' '.join(parts)] if guarded(text) else parts


def normalized(text: str) -> str:
    value = text.casefold().replace('ё', 'е')
    value = re.sub(r"(?<=\d)[ \u00a0](?=\d{3}(?:\D|$))", '', value)
    value = re.sub(r"\bоткрыт\w*", 'вскрыта', value)
    value = re.sub(r"\bхранени\w*", 'хранить', value)
    return value


def split_logic(text: str, operator: str) -> list[str]:
    # Скобки разбираются счётчиком, без eval и без угадывания их пропущенных пар.
    depth, start, pieces = 0, 0, []
    for match in re.finditer(r"[()]|\b" + operator + r"\b", text):
        word = match.group()
        if word == '(':
            depth += 1
        elif word == ')':
            depth -= 1
        elif depth == 0:
            pieces.append(text[start:match.start()].strip())
            start = match.end()
        if depth < 0:
            return [text]
    pieces.append(text[start:].strip())
    return pieces


def ungroup(text: str) -> str:
    text = text.strip(' .')
    while text.startswith('(') and text.endswith(')'):
        depth, enclosed = 0, True
        for pos, char in enumerate(text):
            depth += (char == '(') - (char == ')')
            if depth == 0 and pos < len(text) - 1:
                enclosed = False
                break
        if not enclosed:
            break
        text = text[1:-1].strip()
    return text


def atoms(expression: str) -> list[str]:
    text = ungroup(normalized(expression))
    for operator in ('или', 'и'):
        parts = split_logic(text, operator)
        if len(parts) > 1:
            return [atom for part in parts for atom in atoms(part)]
    return [text]


def atomic_observation(atom: str, question: str, tokenize):
    """(упомянуто, истинность): явная неопределённость отменяет прежний факт."""
    text = normalized(question)
    expected = normalized(atom)
    comparator = re.search(r"(не менее|не больше|не более|не меньше|более|больше|менее|меньше|от)\s*(\d+(?:[.,]\d+)?)(?:\s+до\s+(\d+(?:[.,]\d+)?))?", expected)
    # Точка и запятая внутри числа не разделяют факты: 4999.99 — одна сумма.
    fragments = re.split(r"[;!?]|(?<!\d)[.,]|[.,](?!\d)|\b(?:но|и|если)\b", text)
    # Не разбиваем отрицание «не знаю, вскрыта ли упаковка» на достоверные факты.
    uncertain = UNKNOWN.search(text)
    uncertain_tokens = set(tokenize(re.split(r'[.!?]', text[uncertain.start():])[0])) if uncertain else set()
    if comparator:
        subject = set(tokenize(expected[:comparator.start()]))
        unit = re.search(r"руб\w*|₽|кг|килограмм\w*|грамм\w*|\bг\b|дней|дня|день", expected[comparator.end():])
        if not subject or not unit:
            return False, None
        canonical_unit = 'rub' if unit.group().startswith('руб') or unit.group() == '₽' else 'mass' if re.match(r'кг|килограмм|грамм|г$', unit.group()) else 'day'
        observed = []
        for part in fragments:
            if not subject.issubset(set(tokenize(part))):
                continue
            if UNKNOWN.search(part) or re.search(r"\bли\b", part):
                return True, None
            for number, actual_unit in re.findall(r"(\d+(?:[.,]\d+)?)\s*(руб\w*|₽|кг|килограмм\w*|грамм\w*|г\b|дней|дня|день)", part):
                category = 'rub' if actual_unit.startswith('руб') or actual_unit == '₽' else 'mass' if re.match(r'кг|килограмм|грамм|г$', actual_unit) else 'day'
                if category == canonical_unit:
                    value = float(number.replace(',', '.'))
                    if category == 'mass' and actual_unit.startswith(('кг', 'килограмм')):
                        value *= 1000
                    observed.append(value)
        if len(set(observed)) != 1:
            return bool(observed), None
        boundary = float(comparator[2].replace(',', '.'))
        upper = float(comparator[3].replace(',', '.')) if comparator[3] else None
        if canonical_unit == 'mass' and unit.group().startswith(('кг', 'килограмм')):
            boundary *= 1000
            if upper is not None:
                upper *= 1000
        value, operator = observed[0], comparator[1]
        matches = {'не менее': value >= boundary, 'не меньше': value >= boundary,
                   'не более': value <= boundary, 'не больше': value <= boundary,
                   'больше': value > boundary, 'более': value > boundary,
                   'меньше': value < boundary, 'менее': value < boundary,
                   'от': value >= boundary and (upper is None or value <= upper)}
        return True, matches[operator]
    # «Не» хранится отдельно от поисковых токенов, иначе оплачено и не оплачено совпадут.
    negative = bool(re.search(r"\bне\b|\bнет\b|\bбез\b|отсутств", expected))
    clean = re.sub(r"\b(?:не|нет|без|есть)\b|отсутств\w*", ' ', expected)
    wanted = set(tokenize(clean))
    if not wanted:
        return False, None
    if uncertain_tokens and wanted.issubset(uncertain_tokens):
        return True, None
    observations = []
    location = re.search(r'\b(?:в|на)\s+([а-я]+)', expected)
    for part in fragments:
        tokens = set(tokenize(part))
        if location:
            places = re.findall(r'\b(?:в|на)\s+([а-я]+)', part)
            predicate = wanted - set(tokenize(location[0]))
            # Сравниваем место только у того же действия/предмета. Упоминание
            # офиса собеседника ничего не говорит о месте хранения товара.
            if len(set(places)) == 1 and predicate and predicate.issubset(tokens):
                if UNKNOWN.search(part) or re.search(r'\bли\b', part):
                    return True, None
                actual_negative = bool(re.search(r"\bне\b|\bнет\b|\bбез\b|отсутств", part))
                if location[1] == places[0]:
                    observations.append(actual_negative == negative)
                elif not actual_negative:
                    observations.append(negative)
                else:
                    # «Не в офисе» не доказывает нахождение в холодильнике.
                    observations.append(None)
                continue
        if not wanted.issubset(tokens):
            continue
        if UNKNOWN.search(part) or re.search(r'\bли\b', part):
            return True, None
        actual_negative = bool(re.search(r"\bне\b|\bнет\b|\bбез\b|отсутств", part))
        observations.append(actual_negative == negative)
    return (True, observations[0] if len(set(observations)) == 1 else None) if observations else (False, None)


def truth(expression: str, facts: dict[str, bool | None]):
    text = ungroup(normalized(expression))
    for operator in ('или', 'и'):
        parts = split_logic(text, operator)
        if len(parts) > 1:
            values = [truth(part, facts) for part in parts]
            if operator == 'или':
                return True if True in values else None if None in values else False
            return False if False in values else None if None in values else True
    return facts.get(text)


@dataclass(frozen=True)
class Rule:
    quote: str
    body: str
    condition: str | None
    exception: bool = False


def rules(text, sentences, facet, supplies) -> list[Rule]:
    result = []
    for sentence in sentences(text):
        clause = re.sub(r'^Исключение:\s*', '', sentence, flags=re.I)
        exception = clause != sentence
        prefix = re.match(r'^(?:если|при условии,? что|при условии|при)\s+(.+?),(?!\d)\s*(?:то\s+)?(.+)', clause, re.I)
        suffix = re.match(r'^(.+?)\s+только если\s+(.+?)[.]?$', clause, re.I)
        if prefix:
            condition, body = prefix.groups()
            if re.search(r'[;:—]|\s-\s', condition):
                condition, body = None, sentence
        elif suffix:
            body, condition = suffix.groups()
            if re.search(r'[;:—]|\s-\s', condition):
                # Цепочка «сначала X; только если Y — Z» не является
                # одним правилом «X только если Y». Сохраняем её для цитирования.
                condition, body = None, sentence
        else:
            condition, body = None, re.sub(r'^В остальных случаях[, ]*', '', clause, flags=re.I)
        if supplies(body, facet):
            result.append(Rule(sentence, body, condition, exception))
    return result


def has_related_exception(items, score):
    """Краткая сноска наследует операцию своего правила, но не соседней операции."""
    best = max((score(item) for item in items), default=-1)
    operations = set().union(*(operation_names(item.body) for item in items if score(item) == best))
    return any(item.condition and item.exception and bool(operation_names(item.body) & operations) for item in items)


def resolve(text, question, tokenize, sentences, facet, supplies, previous=None, matching=None):
    items = rules(text, sentences, facet, supplies)
    # Вопрос о получателе не должен требовать уточнения состояния упаковки,
    # если это состояние относится только к соседнему правилу хранения.
    focus = re.split(r'(?<=[.!])\s+', question.strip())[-1]
    focus_words = set(tokenize(focus))
    matching = matching or (lambda left, right: left == right)
    def score(item):
        body_words = set(tokenize(item.body))
        return sum(any(matching(word, token) for token in body_words) for word in focus_words)
    conditional_score = max((score(item) for item in items if item.condition), default=-1)
    ordinary_score = max((score(item) for item in items if item.condition is None), default=-1)
    if ordinary_score > conditional_score and not has_related_exception(items, score):
        return None
    if conditional_score > 0:
        def operations(item):
            return operation_names(item.body)
        selected_operations = set().union(*(operations(item) for item in items if score(item) >= conditional_score))
        def same_operation(item):
            own = operations(item)
            return not own or not selected_operations or bool(own & selected_operations)
        # Короткое исключение часто не повторяет название товара. Его нельзя
        # исключать по числу совпавших слов. Но запрет хранения не должен
        # подменять правило возврата из соседней части того же раздела.
        items = [item for item in items if score(item) >= conditional_score or
                 (same_operation(item) and (item.exception or re.match(r'^В остальных случаях\b', item.quote, re.I)))]
    conditional = [item for item in items if item.condition]
    if not conditional:
        return None
    if len(items) == 1 and len(conditional[0].condition) > 240:
        # Непрозрачное длинное условие можно процитировать целиком, но нельзя
        # пытаться вычислить его по совпадению одного слова.
        return None
    if len(text) > 20000 or any(item.condition.count('(') != item.condition.count(')') for item in conditional):
        return {'status': 'clarify', 'text': 'Уточните конкретный случай: в этом разделе несколько сложных условий.', 'facts': {}, 'selected': []}
    known = {atom for item in conditional for atom in atoms(item.condition)}
    facts = {atom: value for atom, value in (previous or {}).items() if atom in known and isinstance(value, bool)}
    for atom in known:
        observed, value = atomic_observation(atom, question, tokenize)
        if observed:
            facts[atom] = value
    states = [(item, truth(item.condition, facts)) for item in conditional]
    active = [item for item, state in states if state is True]
    if any(item.exception for item in active):
        active = [item for item in active if item.exception]
    unknown = [item for item, state in states if state is None and (not active or item.exception or not active[0].exception)]
    if unknown:
        missing = list(dict.fromkeys(atom for item in unknown for atom in atoms(item.condition) if facts.get(atom) is None))
        # Противоположные ветки «вскрыта / не вскрыта» требуют одного факта,
        # поэтому не задаём один и тот же вопрос дважды в разных отрицаниях.
        missing = [atom for atom in missing if not (re.search(r'\bне\s+', atom)
                   and re.sub(r'\bне\s+', '', atom) in missing)]
        prompt = '; '.join(missing[:3])
        return {'status': 'clarify', 'text': 'Уточните: ' + prompt + '?' if len(prompt) <= 240 else 'Уточните конкретный случай: в документе несколько условий.',
                'facts': {key: value for key, value in facts.items() if isinstance(value, bool)}, 'selected': []}
    selected = active or [item for item in items if item.condition is None]
    if not selected:
        return {'status': 'clarify', 'text': 'Для указанных условий в документе не найден отдельный порядок. Уточните его у администратора.',
                'facts': {key: value for key, value in facts.items() if isinstance(value, bool)}, 'selected': [], 'inapplicable': True}
    return {'status': 'answer', 'selected': selected, 'facts': {key: value for key, value in facts.items() if isinstance(value, bool)}}


def summary_request(question: str) -> bool:
    value = normalized(question)
    return bool(re.search(r'перескаж\w*|резюме|содержани\w*|выдели\w* главное|основные положения|о чем (?:этот |выбранный )?(?:документ|файл)|кратко.*о чем', value))

````

## evaluation/closed_search/multipage_assets.py

SHA256: f0d9b334911d73ade443b498a5beac2bf8d97564e9a3ad93c62a07d0ba048e51

````py
"""Замороженные растры: длинные переносы и сноски. Создаются до правок ядра."""
import hashlib
import json
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader
from .scan_assets import page
from .layout_assets import plain

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'evaluation/closed_search/fixtures/multipage'
ROW = 'Продолжение строки на следующей странице.'
TABLE = 'Продолжение таблицы на следующей странице.'


def table(title, rows, footer=None, header='Действия'):
    cells = [(0, 0, 1, 1, 'Поставка'), (0, 1, 1, 1, header)]
    cells.extend((r, c, 1, 1, text) for r, values in enumerate(rows, 1) for c, text in enumerate(values))
    return page(title, [600, 1020], [120] + [260] * len(rows), cells, note=footer)


def chain(wrong=False, missing=False, revised=False):
    pages = [table('Таблица 31. Приёмка', [('Родонит', 'При повреждении упаковки сделайте фото и')], ROW),
             table('Продолжение таблицы 31', [('Родонит (продолжение)', 'уведомите координатора, затем')], ROW),
             table('Продолжение таблицы 32' if wrong else 'Продолжение таблицы 31',
                   [('Родонит (продолжение)', 'запишите номер поставки в электронный журнал.' if revised else 'запишите номер поставки в бумажный журнал.')])]
    return pages[:2] if missing else pages


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'spec.json').exists():
        raise ValueError('Frozen corpus already exists')
    documents = []
    def write(key, name, pages, negative=False):
        target = OUT / name
        if target.exists():
            raise ValueError('Frozen PDF already exists')
        pdf = canvas.Canvas(str(target), pagesize=(600, 700), invariant=1)
        for image in pages:
            pdf.drawImage(ImageReader(image), 0, 0, 600, 700)
            pdf.showPage()
        pdf.save()
        assert not any(p.extract_text() for p in PdfReader(target).pages)
        documents.append({'id':'lab_multi_' + key, 'name':name, 'pages':len(pages),
                          'negative':negative, 'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    write('chain', 'Три_страницы_Родонит.pdf', chain())
    write('revision', 'Новая_редакция_Родонит.pdf', chain(revised=True), True)
    write('wrong', 'Чужое_продолжение.pdf', chain(wrong=True), True)
    write('missing', 'Без_последней_страницы.pdf', chain(missing=True), True)
    write('rows', 'Четыре_страницы_сроков.pdf', [
        table(('Таблица 41. Хранение' if i == 0 else 'Продолжение таблицы 41'),
              [(name, days + ' календарных дней')], TABLE if i < 3 else None, 'Срок хранения')
        for i, (name, days) in enumerate([('Гелиотроп', '11'), ('Пренит', '16'), ('Пироп', '21'), ('Рутил', '26')])])
    base = table('Таблица 51. Хранение', [('Авантюрин', '12 календарных дней [1] [2]')], header='Срок хранения')
    first = plain('Сноски к таблице 51', ['[1]: Исключение: если упаковка вскрыта,', 'товар хранится 3 календарных дня.'])
    second = plain('Сноски к таблице 51', ['[2]: Исключение: если этикетка повреждена,', 'товар хранится 2 календарных дня.'])
    write('notes', 'Две_сноски_на_трёх_страницах.pdf', [base, first, second])
    write('wrong_notes', 'Неверная_область_сноски.pdf', [base, first,
        plain('Сноски к таблице 52', ['[2]: Исключение: если этикетка повреждена,', 'товар хранится 2 календарных дня.'])], True)
    def case(key, source, q, has=(), absent=(), status='answer'):
        return {'id':key,'source':'lab_multi_'+source,'q':q,'has':list(has),'absent':list(absent),'status':status}
    question = 'Что сделать при повреждении упаковки поставки Родонит?'
    cases = [case('chain','chain',question,['сделайте фото','уведомите координатора','запишите номер поставки в бумажный журнал']),
             case('revision','revision',question,['электронный журнал'],['бумажный журнал']),
             case('wrong','wrong',question,absent=['сделайте фото'],status='unavailable'),
             case('missing','missing',question,absent=['уведомите координатора'],status='unavailable')]
    for key, name, days in [('first','Гелиотроп','11'),('second','Пренит','16'),('third','Пироп','21'),('fourth','Рутил','26')]:
        cases.append(case(key,'rows','Сколько хранится поставка '+name+'?',[days+' календарных']))
    cases += [case('note-base','notes','Упаковка не вскрыта, этикетка не повреждена. Сколько хранится поставка Авантюрин?',['12 календарных'],['3 календарных','2 календарных']),
              case('note-first','notes','Упаковка вскрыта, этикетка не повреждена. Сколько хранится поставка Авантюрин?',['3 календарных'],['12 календарных','2 календарных']),
              case('note-second','notes','Упаковка не вскрыта, этикетка повреждена. Сколько хранится поставка Авантюрин?',['2 календарных'],['12 календарных','3 календарных']),
              case('note-missing','wrong_notes','Упаковка не вскрыта, этикетка не повреждена. Сколько хранится поставка Авантюрин?',status='unavailable')]
    holdout = [case('chain-paraphrase','chain','У поставки Родонит повреждена упаковка. Что делать?',['уведомите координатора','бумажный журнал']),
               case('last-paraphrase','rows','Как долго можно держать поставку Рутил?',['26 календарных']),
               case('both-exceptions','notes','Упаковка вскрыта, этикетка повреждена. Сколько хранится поставка Авантюрин?',status='conflict')]
    (OUT/'spec.json').write_text(json.dumps({'documents':documents,'cases':cases,'holdout':holdout},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pdfs':len(documents),'pages':sum(d['pages'] for d in documents),'cases':len(cases),'holdout':len(holdout)}))


if __name__ == '__main__':
    main()

````

## evaluation/closed_search/multipage_probe.py

SHA256: af96963a83f26d65f86b54606921f11c621d41e4c4ac4b6ab3533deb12395367

````py
"""Независимый набор ожиданий для многостраничных PDF."""
import argparse
import base64
import hashlib
import json
import time
from ai_core.site_documents import extract_document, PARSER_VERSION
from ai_core.site_search import ClosedSiteIndex, Principal
from .quality_probe import ROOT, LAB, ORIGIN, errors_for

ASSETS = ROOT/'evaluation/closed_search/fixtures/multipage'


def load_sources():
    spec = json.loads((ASSETS/'spec.json').read_text(encoding='utf-8'))
    sources, extraction = [], []
    for item in spec['documents']:
        data = (ASSETS/item['name']).read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise AssertionError('Frozen fixture changed: '+item['id'])
        start = time.perf_counter()
        text, complete = extract_document('data:application/pdf;base64,'+base64.b64encode(data).decode(), item['name'])
        extraction.append({'id':item['id'],'text':text,'complete':complete,'seconds':round(time.perf_counter()-start,3)})
        sources.append({'id':item['id'],'revision':'multipage-v1','title':item['name'],'kind':'document',
                        'url':ORIGIN+'/materials/'+item['id'],'access':{'scope':'workspace'},'complete':complete,
                        'sections':[{'heading':item['name'],'text':text}]})
    return spec, sources, extraction


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label',default='multipage-probe')
    parser.add_argument('--holdout',action='store_true')
    args = parser.parse_args()
    spec, sources, extraction = load_sources()
    index = ClosedSiteIndex([ORIGIN]); index.publish(sources,index.begin('multipage'))
    rows = []
    for case in spec['holdout' if args.holdout else 'cases']:
        answer = index.search(case['q'],Principal('a','staff'),case['id'],case['source'])
        errors = errors_for(case,answer)
        rows.append({'id':case['id'],'pass':not errors,'errors':errors,'answer':answer})
    report = {'parser':PARSER_VERSION,'passed':sum(r['pass'] for r in rows),'total':len(rows),'cases':rows,'extraction':extraction}
    file = LAB/(args.label+'-'+str(time.time_ns())+'.json')
    file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'file':str(file),'passed':report['passed'],'total':report['total'],'failures':[r for r in rows if not r['pass']]},ensure_ascii=False))
    if report['passed'] != report['total']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

````

## evaluation/closed_search/multipage_http.cjs

SHA256: 0ea9bd636637e2ffda4b382a093f31c943c93d88a17ab8fab870847936ca9ff9

````cjs
/* Многостраничные PDF проходят реальную загрузку, фоновые задачи и API сайта. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const {createHash}=require('node:crypto');
const assets=path.join(__dirname,'fixtures/multipage');

function contains(answer,text){
    const escaped=text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    return new RegExp((/^\d/.test(text)?'(?<!\\d)':'')+escaped,'i').test(answer);
}

async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(assets,'spec.json'),'utf8'));
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const negative=new Set(spec.documents.filter(d=>d.negative).map(d=>d.id));
    const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
    for(const item of spec.documents){
        const bytes=await fs.readFile(path.join(assets,item.name));
        if(createHash('sha256').update(bytes).digest('hex')!==item.sha256)throw new Error('Frozen fixture changed '+item.id);
        const data='data:application/pdf;base64,'+bytes.toString('base64');
        const entry={id:item.id,name:item.name,hasSeparateData:true,section:'Многостраничные таблицы ВатэсAi',size:bytes.length+' B',date:'Локальный тест',
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
        {id:'summary-chain',source:'lab_multi_chain',q:'Кратко перескажи выбранный файл',has:['сделайте фото','уведомите координатора','бумажный журнал']},
        {id:'summary-rows',source:'lab_multi_rows',q:'Кратко перескажи выбранный файл',has:['11 календарных','16 календарных','21 календарных','26 календарных']},
        {id:'summary-notes',source:'lab_multi_notes',q:'Кратко перескажи выбранный файл',has:['12 календарных','3 календарных','2 календарных','если упаковка','если этикетка']},
        {id:'summary-incomplete',source:'lab_multi_missing',q:'Кратко перескажи выбранный файл',status:'unavailable'},
        {id:'hidden-a',actor:'a',source:'lab_multi_revision',q:spec.cases[0].q,status:'not_found'},
        {id:'hidden-b',actor:'b',source:'lab_multi_wrong',q:spec.cases[0].q,status:'not_found'},
        {...spec.cases[0],id:'automatic-source',actor:'a',automatic:true}];
    const rows=[];
    for(const [position,item] of cases.entries()){
        const actor=item.actor||(negative.has(item.source)?'admin':['a','b','admin'][position%3]);
        const started=performance.now();
        const {response,value}=await call('/api/ai-chat',{chatId:'multipage-'+Date.now()+'-'+item.id,
            ...(item.automatic?{}:{documentId:item.source}),messages:[{role:'user',content:item.q}]},cookies[actor]);
        const answer=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(value.search_status!==(item.status||'answer'))errors.push('status:'+value.search_status);
        if(value.search_status==='answer'){
            for(const text of item.has||[])if(!contains(answer,text))errors.push('missing:'+text);
            if(!value.sourceLinks?.length||value.sourceLinks.some(link=>!link.url.includes(item.source)))errors.push('source');
        }else if(value.sourceLinks?.length)errors.push('links-on-refusal');
        for(const text of item.absent||[])if(contains(answer,text))errors.push('unexpected:'+text);
        rows.push({id:item.id,actor,question:item.q,status:value.search_status,answer,links:value.sourceLinks,ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    const report={createdAt:new Date().toISOString(),ready,total:rows.length,passed:rows.filter(r=>r.pass).length,cases:rows};
    const file=path.join(lab,'multipage-http-'+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,total:report.total,passed:report.passed,failures:rows.filter(r=>!r.pass)}));
    if(report.passed!==report.total)process.exitCode=1;
}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1;});
module.exports={assets};

````

## evaluation/closed_search/multipage_revisions.cjs

SHA256: a184858d7f6936b1fc18cf42578b1b8e32cddd3b8133d93c724b25c395cf3b82

````cjs
/* Изменяется только временный документ этого прогона; очистка выполняется в finally. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {assets}=require('./multipage_http.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const id='lab_multipage_revision_probe';
async function main(){
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const initial=await storage('tea_hub_urgent_files_v1',cookies.admin);
    const unfinished=initial.find(d=>d.id===id);
    if(unfinished && process.argv.includes('--recover')){
        // После аварийного завершения разрешено убрать только собственный
        // временный объект с известным именем и байтами контрольного PDF.
        if(unfinished.name!=='Временная_многостраничная_таблица.pdf'||unfinished.section!=='Проверка редакций'
            ||unfinished.allowedUserIds?.length!==1||!['closed_lab_a','closed_lab_b'].includes(unfinished.allowedUserIds[0]))throw new Error('Unexpected recovery object');
        const data=await storage('file_data_'+id,cookies.admin);
        const spec=JSON.parse(await fs.readFile(path.join(assets,'spec.json'),'utf8'));
        let recognized=false;
        for(const item of spec.documents){
            if(data==='data:application/pdf;base64,'+(await fs.readFile(path.join(assets,item.name))).toString('base64'))recognized=true;
        }
        if(!recognized)throw new Error('Unknown bytes: recovery refused');
        await storage('tea_hub_urgent_files_v1',cookies.admin,initial.filter(d=>d.id!==id));
        await storage('file_data_'+id,cookies.admin,null);await waitSnapshot();
        console.log(JSON.stringify({recovered:true,id}));return;
    }
    if(unfinished)throw new Error('Unfinished multipage fixture exists');
    const rows=[],chat='multipage-revision-'+Date.now();
    async function catalog(change){await storage('tea_hub_urgent_files_v1',cookies.admin,change(await storage('tea_hub_urgent_files_v1',cookies.admin)));}
    async function replace(name){await storage('file_data_'+id,cookies.admin,'data:application/pdf;base64,'+(await fs.readFile(path.join(assets,name))).toString('base64'));}
    async function ask(name,statuses,tail='',actor='a'){
        const {response,value}=await call('/api/ai-chat',{chatId:chat,documentId:id,
            messages:[{role:'user',content:'Что сделать при повреждении упаковки поставки Родонит?'}]},cookies[actor]);
        const text=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(!statuses.includes(value.search_status))errors.push('status:'+value.search_status);
        if(value.search_status!=='answer'&&(value.sourceLinks?.length||text.includes('журнал')))errors.push('stale-answer');
        if(value.search_status==='answer'){
            if(!tail||!text.includes(tail)||text.includes(tail.includes('бумажный')?'электронный журнал':'бумажный журнал'))errors.push('wrong-tail');
            if(!value.sourceLinks?.length||value.sourceLinks.some(link=>!link.url.includes(id)))errors.push('wrong-source');
        }
        rows.push({id:name,actor,status:value.search_status,answer:text,pass:!errors.length,errors});
    }
    let completed=false,cleanup=false,failure=null;
    try{
        await replace('Три_страницы_Родонит.pdf');
        await catalog(items=>[...items,{id,name:'Временная_многостраничная_таблица.pdf',section:'Проверка редакций',hasSeparateData:true,allowedUserIds:['closed_lab_a']}]);
        await waitSnapshot();await ask('v1',['answer'],'бумажный журнал');await ask('b-denied',['not_found'],'','b');
        await replace('Новая_редакция_Родонит.pdf');
        await ask('v2-immediate',['processing','answer'],'электронный журнал');
        await waitSnapshot();await ask('v2-ready',['answer'],'электронный журнал');
        await replace('Без_последней_страницы.pdf');
        await ask('incomplete-immediate',['processing','unavailable']);
        await waitSnapshot();await ask('incomplete-ready',['unavailable']);
        await replace('Три_страницы_Родонит.pdf');await waitSnapshot();await ask('restored',['answer'],'бумажный журнал');
        await catalog(items=>items.map(item=>item.id===id?{...item,allowedUserIds:['closed_lab_b']}:item));
        await ask('a-revoked',['processing','not_found']);await waitSnapshot();
        await ask('b-allowed',['answer'],'бумажный журнал','b');
        await catalog(items=>items.filter(item=>item.id!==id));
        await ask('removed',['processing','not_found'],'','b');await waitSnapshot();await ask('removed-ready',['not_found'],'','b');
        completed=true;
    }catch(error){failure=error.message;throw error;}
    finally{
        try{await catalog(items=>items.filter(item=>item.id!==id));await storage('file_data_'+id,cookies.admin,null);await waitSnapshot();cleanup=true;}
        finally{
            const report={createdAt:new Date().toISOString(),completed,cleanup,failure,total:rows.length,passed:rows.filter(r=>r.pass).length,cases:rows};
            const file=path.join(lab,'multipage-revisions-'+Date.now()+'.json');await fs.writeFile(file,JSON.stringify(report,null,2));
            console.log(JSON.stringify({file,completed,cleanup,total:report.total,passed:report.passed,failures:rows.filter(r=>!r.pass)}));
            if(!completed||!cleanup||report.total!==report.passed)process.exitCode=1;
        }
    }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});

````

## evaluation/closed_search/quality_probe.py

SHA256: bb3b461cd7e151b22fc8e3c7d7289c861bec7e904b0b37c8e7bd0bd22ebe4a29

````py
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
        # Запрещённые «2 дня» не должны совпадать с правильными «12 днями».
        pattern=(r'(?<!\d)' if text[:1].isdigit() else '')+re.escape(text.casefold())
        if re.search(pattern,answer['text'].casefold()):errors.append('unexpected:'+text)
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

## evaluation/closed_search/layout_visual_qa.py

SHA256: eb9b4cfbffdb2e1da7f8aa95ce58f45eb121111b3ac0cae92781b16f8e4e110d

````py
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
    parser.add_argument('--fixture',choices=['layout','quality','multipage'],default='layout')
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

## evaluation/closed_search/soak_http.cjs

SHA256: 24e837ed10e0ce0a767857f77ab13644bea1fa2301a0c7c677505141fcbdd7d0

````cjs
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
        for (const file of ['site_search.py','site_search_conditions.py','site_search_structure.py','site_search_reasoning.py','site_search_runtime.py','site_search_store.py','site_documents.py','site_scan_tables.py','site_scan_layout.py','site_scan_quality.py','site_search_api.py']) {
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
                    principal: { user_id: `reader-${client}-${item.otherUser ? 'b' : 'a'}`, role: item.role || 'staff', groups: [] },
                    selected_source: item.selected,
                    current_revisions: item.changed ? { ...revisions, ['bench-' + number]: 'changed-v2' } : revisions });
                const required = item.dynamic ? item.required(days) : item.has;
                const contains = text => new RegExp((/^\d/.test(text)?'(?<!\\d)':'')+text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'i').test(value.text||'');
                const exactOrRefuse = item.status === 'exact_or_refuse';
                const expected = exactOrRefuse ? ['answer','unavailable'] : [item.status];
                const pass = http === 200 && expected.includes(value.status) && (exactOrRefuse && value.status !== 'answer' || required.every(contains))
                    && (item.absent || []).every(text => !contains(text))
                    && (!item.source || value.status !== 'answer' || value.links?.some(link => link.url.includes(item.source)))
                    && (value.status === 'answer' || !value.links?.length)
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

## evaluation/closed_search/verify_lab.cjs

SHA256: af8f702cf641233d2560ebaac9aed2cc5c3ee7d5a1ca3524e454a96cd4a3e603

````cjs
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
    const multipageFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'fixtures/multipage/spec.json'), 'utf8'));
    const expectedDocuments = 9 + reasoningFixtures.documents.length + conditionFixtures.documents.length + structureFixtures.documents.length + structureAssets.length + scanFixtures.documents.length + layoutFixtures.documents.length + qualityFixtures.documents.length + multipageFixtures.documents.length;
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

## tests/test_site_search_multipage.py

SHA256: c45a745dd8f45e4e99aa3322706e953b96f5a2e7d86058694812b35ca4477dc3

````py
"""Проверки целой цепочки, отказов, сносок и изоляции редакций."""
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ai_core.site_scan_layout import compose_pages, UNCERTAIN
from ai_core.site_scan_tables import describe_table, Cell
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.multipage_probe import load_sources, ORIGIN, errors_for


def chain(values, row_marker=True):
    tables, pages = [], []
    for number, value in enumerate(values,1):
        subject = 'Поставка' if number == 1 or not row_marker else 'Поставка (продолжение)'
        if not row_marker:
            subject += str(number)
        cells = [Cell(0,0,1,1,'Товар'),Cell(0,1,1,2,'Действия'),Cell(1,0,2,1,subject),Cell(1,1,2,2,value)]
        text = describe_table(cells,2,2,number,1)
        tables.append({'page':number,'cells':cells,'rows':2,'cols':2,'ordinal':1,'text':text})
        caption = 'Таблица 9' if number == 1 else 'Продолжение таблицы 9'
        marker = ('\nПродолжение '+('строки' if row_marker else 'таблицы')+' на следующей странице.') if number < len(values) else ''
        pages.append(caption+'\n'+text+marker)
    return tables, pages


class MultipageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec, cls.sources, cls.extraction = load_sources()

    def test_frozen_main_and_holdout(self):
        index = ClosedSiteIndex([ORIGIN]); index.publish(self.sources,index.begin('multipage'))
        for case in self.spec['cases'] + self.spec['holdout']:
            with self.subTest(case=case['id']):
                answer = index.search(case['q'],Principal('a','staff'),case['id'],case['source'])
                self.assertEqual(errors_for(case,answer),[])

    def test_four_page_row_keeps_all_fragments_and_original_cells(self):
        tables, pages = chain(['Сделайте фото и','сообщите координатору, затем','сохраните номер и','заполните журнал.'])
        before = deepcopy(tables)
        text = compose_pages(pages,tables,describe_table)
        self.assertNotIn(UNCERTAIN,text)
        self.assertIn('Сделайте фото и сообщите координатору, затем сохраните номер и заполните журнал.',text)
        self.assertIn('Страницы 1-4',text)
        self.assertEqual(tables,before)

    def test_repeated_headers_do_not_become_body_rows(self):
        tables, pages = chain(['Сделайте фото.','Заполните журнал.','Позвоните координатору.'],False)
        text = compose_pages(pages,tables,describe_table)
        self.assertNotIn(UNCERTAIN,text)
        for subject in ['Поставка1','Поставка2','Поставка3']:
            self.assertIn(subject,text)
        self.assertEqual(text.count('Действия:'),3)

    def test_chain_failures_are_atomic(self):
        for fault in ['header','missing','shape','truncated','subject','two-markers','duplicate-caption','orphan']:
            with self.subTest(fault=fault):
                tables,pages = chain(['Сделайте фото и','сообщите координатору, затем','заполните журнал.'])
                if fault == 'header': tables[2]['cells'][1].text='Другая колонка'
                if fault == 'missing': pages=pages[:2]
                if fault == 'shape': tables[2]['cells'].pop()
                if fault == 'truncated': tables[2]['cells'][-1].text='заполните'
                if fault == 'subject': tables[2]['cells'][2].text='Чужой (продолжение)'
                if fault == 'two-markers': pages[1]+='\nПродолжение таблицы на следующей странице.'
                if fault == 'duplicate-caption': pages[2]+='\nТаблица 9'
                if fault == 'orphan': pages[0]=pages[0].replace('Продолжение строки на следующей странице.','')
                result=compose_pages(pages,tables,describe_table)
                self.assertIn(UNCERTAIN,result)
                self.assertNotIn('Сделайте фото и сообщите координатору, затем заполните журнал.',result)

    def test_duplicate_table_numbers_cannot_share_footnotes(self):
        text = compose_pages(['Таблица 1\nПравило [1].','Таблица 1\n[1]: Другое правило.'],[],describe_table)
        self.assertIn(UNCERTAIN,text)

    def test_footnote_scope_cycles_and_duplicate_definitions(self):
        for suffix in ['[2]: Исключение: если упаковка вскрыта, товар хранится 2 дня.\n[2]: Товар хранится 9 дней.',
                       '[2]: См. [1].', '[3]: Исключение: товар хранится 2 дня.']:
            source={'id':'notes','revision':'1','kind':'document','title':'Сроки Образца','url':ORIGIN+'/materials/notes',
                    'access':{'scope':'workspace'},'sections':[{'heading':'Образец','text':'Товар Образец хранится 8 дней [1].\n[1]: См. [2].\n'+suffix}]}
            index=ClosedSiteIndex([ORIGIN]);index.publish([source],index.begin('notes'))
            self.assertEqual(index.search('Сколько хранится товар Образец?',Principal('a','staff'),selected_source='notes')['status'],'unavailable')

    def test_warm_cache_and_query_never_repeat_ocr(self):
        with patch('ai_core.site_documents._scan_text',side_effect=AssertionError('OCR repeated')):
            self.assertEqual(load_sources()[1],self.sources)
            index=ClosedSiteIndex([ORIGIN]);index.publish(self.sources,index.begin('warm'))
            case=self.spec['cases'][0]
            self.assertEqual(errors_for(case,index.search(case['q'],Principal('a','staff'),selected_source=case['source'])),[])

    def test_unselected_question_is_not_intercepted_by_other_rule(self):
        original = deepcopy(self.sources[0])
        unrelated = {'id':'storage','revision':'1','kind':'document','title':'Родонит. Хранение',
                     'url':ORIGIN+'/materials/storage','access':{'scope':'workspace'},
                     'sections':[{'heading':'Поставка Родонит','text':'Поставка Родонит хранится 11 дней. Исключение: если упаковка вскрыта, товар хранится 3 дня.'}]}
        index=ClosedSiteIndex([ORIGIN]);index.publish([original,unrelated],index.begin('mixed'))
        answer=index.search(self.spec['cases'][0]['q'],Principal('a','staff'))
        self.assertEqual(errors_for(self.spec['cases'][0],answer),[])
        self.assertEqual(index.search('Сколько хранится поставка Родонит?',Principal('a','staff'),'storage')['status'],'clarify')

    def test_revision_restart_and_rights_do_not_restore_old_tail(self):
        good=deepcopy(self.sources[0]);new=deepcopy(self.sources[1]);bad=deepcopy(self.sources[3])
        for source, revision in [(good,'1'),(new,'2'),(bad,'3')]:
            source.update(id='changing',revision=revision,access={'scope':'restricted','users':['a']})
        q=self.spec['cases'][0]['q'];actor=Principal('a','staff')
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'index.sqlite3'
            runtime=DurableSearch([ORIGIN],file);runtime.publish([good],runtime.begin('first'))
            self.assertIn('бумажный журнал',runtime.query(q,actor,'chat','changing',{'changing':'1'})['text'])
            runtime=DurableSearch([ORIGIN],file)
            self.assertEqual(runtime.query(q,actor,'chat','changing',{'changing':'2'})['status'],'processing')
            runtime.publish([new],runtime.begin('second'));runtime=DurableSearch([ORIGIN],file)
            answer=runtime.query(q,actor,'chat','changing',{'changing':'2'})
            self.assertIn('электронный журнал',answer['text']);self.assertNotIn('бумажный журнал',answer['text'])
            self.assertEqual(runtime.query(q,Principal('b','staff'),'chat','changing',{'changing':'2'})['status'],'not_found')
            runtime.publish([bad],runtime.begin('third'));runtime=DurableSearch([ORIGIN],file)
            self.assertEqual(runtime.query(q,actor,'chat','changing',{'changing':'3'})['status'],'unavailable')


if __name__ == '__main__':
    unittest.main()

````

## evaluation/closed_search/fixtures/multipage/spec.json

SHA256: b6ee6c78eff31bdcb4edf39b2271e830d8d69733a8462b9f57d654d8c2ec7c51

````json
{
  "documents": [
    {
      "id": "lab_multi_chain",
      "name": "Три_страницы_Родонит.pdf",
      "pages": 3,
      "negative": false,
      "sha256": "c0cd53803aa1de8051865ed5ccc61e77bbf7ac54d2e5594409de8f47809031d1"
    },
    {
      "id": "lab_multi_revision",
      "name": "Новая_редакция_Родонит.pdf",
      "pages": 3,
      "negative": true,
      "sha256": "ad9664790429f2a645102f7b9e6476bffa4f159190336d1f095a7d702784358b"
    },
    {
      "id": "lab_multi_wrong",
      "name": "Чужое_продолжение.pdf",
      "pages": 3,
      "negative": true,
      "sha256": "cbd31ccb5fe5750886ac103a6b57897c27db149820c15089a04c3b39eb1cf480"
    },
    {
      "id": "lab_multi_missing",
      "name": "Без_последней_страницы.pdf",
      "pages": 2,
      "negative": true,
      "sha256": "914433e61e841309a5feec8e651f9eb9435f3b747684ddb249fe48dae8906ccb"
    },
    {
      "id": "lab_multi_rows",
      "name": "Четыре_страницы_сроков.pdf",
      "pages": 4,
      "negative": false,
      "sha256": "e0e03d7c1584186c738b4836685863c946de1245a42a93e5ec33751b87976c52"
    },
    {
      "id": "lab_multi_notes",
      "name": "Две_сноски_на_трёх_страницах.pdf",
      "pages": 3,
      "negative": false,
      "sha256": "bf5cc74596ec73dec0a7f70e0ffc197e40929febec08f04f541b9815e6801a94"
    },
    {
      "id": "lab_multi_wrong_notes",
      "name": "Неверная_область_сноски.pdf",
      "pages": 3,
      "negative": true,
      "sha256": "5d3a4b343276e63aa535527f4e6e39fad335f898b62a970bb33f6217093fed76"
    }
  ],
  "cases": [
    {
      "id": "chain",
      "source": "lab_multi_chain",
      "q": "Что сделать при повреждении упаковки поставки Родонит?",
      "has": [
        "сделайте фото",
        "уведомите координатора",
        "запишите номер поставки в бумажный журнал"
      ],
      "absent": [],
      "status": "answer"
    },
    {
      "id": "revision",
      "source": "lab_multi_revision",
      "q": "Что сделать при повреждении упаковки поставки Родонит?",
      "has": [
        "электронный журнал"
      ],
      "absent": [
        "бумажный журнал"
      ],
      "status": "answer"
    },
    {
      "id": "wrong",
      "source": "lab_multi_wrong",
      "q": "Что сделать при повреждении упаковки поставки Родонит?",
      "has": [],
      "absent": [
        "сделайте фото"
      ],
      "status": "unavailable"
    },
    {
      "id": "missing",
      "source": "lab_multi_missing",
      "q": "Что сделать при повреждении упаковки поставки Родонит?",
      "has": [],
      "absent": [
        "уведомите координатора"
      ],
      "status": "unavailable"
    },
    {
      "id": "first",
      "source": "lab_multi_rows",
      "q": "Сколько хранится поставка Гелиотроп?",
      "has": [
        "11 календарных"
      ],
      "absent": [],
      "status": "answer"
    },
    {
      "id": "second",
      "source": "lab_multi_rows",
      "q": "Сколько хранится поставка Пренит?",
      "has": [
        "16 календарных"
      ],
      "absent": [],
      "status": "answer"
    },
    {
      "id": "third",
      "source": "lab_multi_rows",
      "q": "Сколько хранится поставка Пироп?",
      "has": [
        "21 календарных"
      ],
      "absent": [],
      "status": "answer"
    },
    {
      "id": "fourth",
      "source": "lab_multi_rows",
      "q": "Сколько хранится поставка Рутил?",
      "has": [
        "26 календарных"
      ],
      "absent": [],
      "status": "answer"
    },
    {
      "id": "note-base",
      "source": "lab_multi_notes",
      "q": "Упаковка не вскрыта, этикетка не повреждена. Сколько хранится поставка Авантюрин?",
      "has": [
        "12 календарных"
      ],
      "absent": [
        "3 календарных",
        "2 календарных"
      ],
      "status": "answer"
    },
    {
      "id": "note-first",
      "source": "lab_multi_notes",
      "q": "Упаковка вскрыта, этикетка не повреждена. Сколько хранится поставка Авантюрин?",
      "has": [
        "3 календарных"
      ],
      "absent": [
        "12 календарных",
        "2 календарных"
      ],
      "status": "answer"
    },
    {
      "id": "note-second",
      "source": "lab_multi_notes",
      "q": "Упаковка не вскрыта, этикетка повреждена. Сколько хранится поставка Авантюрин?",
      "has": [
        "2 календарных"
      ],
      "absent": [
        "12 календарных",
        "3 календарных"
      ],
      "status": "answer"
    },
    {
      "id": "note-missing",
      "source": "lab_multi_wrong_notes",
      "q": "Упаковка не вскрыта, этикетка не повреждена. Сколько хранится поставка Авантюрин?",
      "has": [],
      "absent": [],
      "status": "unavailable"
    }
  ],
  "holdout": [
    {
      "id": "chain-paraphrase",
      "source": "lab_multi_chain",
      "q": "У поставки Родонит повреждена упаковка. Что делать?",
      "has": [
        "уведомите координатора",
        "бумажный журнал"
      ],
      "absent": [],
      "status": "answer"
    },
    {
      "id": "last-paraphrase",
      "source": "lab_multi_rows",
      "q": "Как долго можно держать поставку Рутил?",
      "has": [
        "26 календарных"
      ],
      "absent": [],
      "status": "answer"
    },
    {
      "id": "both-exceptions",
      "source": "lab_multi_notes",
      "q": "Упаковка вскрыта, этикетка повреждена. Сколько хранится поставка Авантюрин?",
      "has": [],
      "absent": [],
      "status": "conflict"
    }
  ]
}
````
