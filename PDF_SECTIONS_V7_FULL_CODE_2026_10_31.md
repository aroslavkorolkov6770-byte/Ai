# Разделы PDF и уточнения: полный код

Проект: C:/Users/KROLL/Desktop/Ai. Полные текущие исходники изменённых файлов, нового теста и проверочных скриптов. Содержимое не сокращено. Для ядра проверено совпадение с хешами итогового прогона verified. Снимок прежнего ядра сохранён в evaluation/closed_search/pdf_sections_v7_20261031/before/ai_core.

Отчёт и ограничения: PDF_SECTIONS_V7_REPORT_2026_10_31.md. Текущий результат 72/100 не является приёмкой универсального качества; рабочая LMS автоматически не обновлялась.

## ai_core/site_documents.py

Полный путь: C:\Users\KROLL\Desktop\Ai\ai_core\site_documents.py.

SHA-256: `0fa4aab8b6594115c75c0f1135d6fe0ad3330344be0944bde6f5d3ddb6b74fbf`.

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
from .site_pdf_sections import extract_sectioned_pdf

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
    + Path(__file__).with_name('site_pdf_sections.py').read_bytes()
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
            if structured is None:
                structured = extract_sectioned_pdf(file, report)
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

## ai_core/site_pdf_sections.py

Полный путь: C:\Users\KROLL\Desktop\Ai\ai_core\site_pdf_sections.py.

SHA-256: `cedbb5d666c82b4e25fe88d78276b8b3d52f111da9f6639ea7713cf386e9f51b`.

````python
"""Общие текстовые PDF: геометрия колонок, строки таблиц и область запрета.

Профиль не угадывает отсутствующие ячейки и не заменяет обработчик сканов.
Каждый поисковый раздел содержит законченное правило вместе с его вводной.
Названия документов и вопросы контрольного набора здесь не используются.
"""
from __future__ import annotations

import re
from pathlib import Path
import pdfplumber
from .site_document_cache import MAX_PDF_PAGES, MAX_PDF_CHARACTERS
from .site_pdf_manual import cell_text, owner
from .site_pdf_structure import in_box, lines

MARKER = '[PDF_RULE_SECTIONS]'
CONTROL = re.compile(r'запрещается|запрещено|не допускается|не осуществляется|обязан\w*|имеют право', re.I)
NUMBERED = re.compile(r'^(\d+(?:\.\d+)+)\.?\s*([А-Яа-яA-Za-z].+)')
DEPENDENT = re.compile(r'^(?:Если|В остальных|В этом случае|При этом|Кроме|Однако|Исключени|За исключением)', re.I)


def reading_lines(words, width):
    """Две колонки признаём только по устойчивому пустому промежутку."""
    rows = lines(words)
    middle = width / 2
    crossing = [row for row in rows if any(w['x0'] < middle-3 and w['x1'] > middle+3 for w in row['words'])]
    left = [w for w in words if w['x1'] <= middle+3]
    right = [w for w in words if w['x0'] >= middle-3]
    pairs = sum(any(w['x1'] < middle-4 for w in row['words']) and any(w['x0'] > middle+4 for w in row['words']) for row in rows)
    if len(left)>15 and len(right)>15 and pairs>6 and len(crossing)<max(3,len(rows)*.18):
        # Общие заголовки над колонками читаются сначала, затем обе колонки.
        header_ids = {id(w) for row in crossing for w in row['words']}
        top = min((row['top'] for row in rows if row not in crossing
                   and any(w['x1']<middle-4 for w in row['words'])
                   and any(w['x0']>middle+4 for w in row['words'])),default=0)
        headers = [row for row in crossing if row['top']<=top+12]
        remaining_cross = [row for row in crossing if row not in headers]
        if remaining_cross:
            return rows  # Не выбираем произвольный порядок пересекающихся блоков.
        return headers + lines([w for w in left if id(w) not in header_ids]) + lines([w for w in right if id(w) not in header_ids])
    return rows


def table_sections(table, words, page_number):
    """Значения объединённых ячеек получаем по рамке, не заполнением None."""
    xs = sorted({round(cell[0],2) for cell in table.cells} | {round(cell[2],2) for cell in table.cells})
    if not 3<=len(xs)<=7 or len(table.rows)<3:
        return []
    count=len(xs)-1
    first=table.rows[0]
    header=[cell_text(words,box) if box else '' for box in first.cells]
    variants = count>2 and any(re.search(r'параметр|характеристик|наименование',value,re.I) for value in header)
    blocks=[]
    seen=set()
    for row in table.rows[1:] if variants else table.rows:
        boxes=[box for box in row.cells if box]
        if not boxes: continue
        y=(max(box[1] for box in boxes)+min(box[3] for box in boxes))/2
        cells=[owner(table.cells,(a+b)/2,y) for a,b in zip(xs,xs[1:])]
        if any(box is None for box in cells):
            blocks.append(('Таблица', '[LOCAL_STRUCTURE_UNCERTAIN] '+ ' '.join(cell_text(words,b) for b in boxes)))
            continue
        if len(set(cells))==1:
            continue  # Полоса-заголовок таблицы не является строкой значений.
        key_index = 1 if variants and count>=4 else 0
        key=cell_text(words,cells[key_index])
        group=cell_text(words,cells[0]) if key_index and cells[0]!=cells[key_index] else ''
        if not key: continue
        for col in range(key_index+1,count):
            value=cell_text(words,cells[col])
            if cells[col]==cells[key_index] or not value: continue
            label=header[col] if variants and col<len(header) else ''
            if variants and all(cells[i]==cells[col] for i in range(key_index+1,count)):
                label=' / '.join(header[i] for i in range(key_index+1,count) if header[i])
            content=(group+'. ' if group else '')+key+(': '+value)+'.'
            if label:content=label+' — '+content
            signature=(key,group,label,value)
            if signature in seen:continue
            seen.add(signature)
            blocks.append((f'Страница {page_number} — '+key,content))
    return blocks


def section_blocks(pages):
    """Разбиваем пункты, сохраняя вводные условия, запреты и списки шагов."""
    blocks=[]
    current=[]
    heading='Текст'
    page_start=1
    controllers={}
    number=''
    list_intro=False
    section_title=''

    def save():
        nonlocal current,list_intro
        if not current:return
        body=' '.join(current).strip()
        inherited=[text for key,text in controllers.items() if number.startswith(key+'.')]
        if inherited:body=' '.join(inherited)+' '+body
        blocks.append((f'Страница {page_start} — '+heading,body))
        current=[]
        list_intro=False

    for page_number, rows in pages:
        for line in rows:
            text=re.sub(r'\s+',' ',line).strip()
            if not text or re.fullmatch(r'\d+\s*(?:/\s*[A-ZА-Я]+)?',text):continue
            if re.search(r'Руководство (?:по эксплуатации|пользователя)$',text) and len(text)<80:continue
            match=NUMBERED.match(text)
            bullet=bool(re.match(r'^[•●]\s*',text))
            text=re.sub(r'^[•●]\s*','',text)
            title=bool(re.match(r'^\d\s*[А-ЯA-Z]',text) or (text.isupper() and 15<len(text)<95 and not re.search(r'[).;]$',text)))
            if match:
                save()
                number=match[1];heading='Пункт '+number
                section_title=match[2]
                current=[match[2]];page_start=page_number
                controllers={key:value for key,value in controllers.items() if number.startswith(key+'.')}
                # Двоеточие вводит общий перечень: сохраняем исходную вводную
                # у каждого дочернего пункта, даже без слова «запрещается».
                # Так «Если дверь закрыта:» не превращается в общее правило.
                if match[2].endswith(':'):
                    controllers[number]=match[2]
                list_intro=match[2].endswith(':')
                continue
            if title and not CONTROL.search(text):
                save();heading=text;number='';section_title=text;controllers={};page_start=page_number
                continue
            is_step=bool(re.match(r'^\d+[.)]\s+',text))
            list_item=bool(re.match(r'^[-–—]\s+',text))
            previous=' '.join(current)
            # В узкой колонке название пункта может занимать несколько строк.
            # Учитываем продолжение заголовка до первого требования списка.
            if number and not bullet and previous==section_title and len(previous+' '+text)<120 and not re.search(r'[.!?:]$',previous) and re.match(r'^[а-яa-z]',text):
                section_title=previous+' '+text
            atomic_safety=bool(re.search(r'безопасност|предосторожност|общие требования',section_title,re.I))
            if bullet and atomic_safety and current and not (current[-1].endswith(':') and CONTROL.search(current[-1])):
                # Самостоятельные требования безопасности разделены маркерами.
                # Продолжение того же маркера (например, обращение в сервис)
                # остаётся вместе с условием. Процедуры и перечни запретов целы.
                save();page_start=page_number
            if list_intro and re.search(r'[.!?]$',previous) and not (is_step or list_item or bullet) and not DEPENDENT.match(text):
                list_intro=False
            # Шаги процедуры и список после двоеточия остаются одним блоком.
            if current and not number and (bullet or (re.search(r'[.!?]$',previous) and re.match(r'^[А-ЯA-Z]',text))) and not DEPENDENT.match(text) and not list_intro and not is_step:
                save();page_start=page_number
            if is_step or (list_item and current):list_intro=True
            if text.endswith(':'):list_intro=True
            if current and current[-1].endswith('-') and re.match(r'^[а-яa-z]',text):
                current[-1]=current[-1][:-1]+text
            else:current.append(text)
    save()
    return blocks


def extract_sectioned_pdf(file: Path, progress=None):
    """Только настоящий текстовый PDF; при скане остаётся прежний OCR-путь."""
    try:
        document=pdfplumber.open(file)
    except Exception:
        return None
    with document:
        pages=[];tables=[]
        total=len(document.pages)
        for number,page in enumerate(document.pages[:MAX_PDF_PAGES],1):
            words=page.extract_words(x_tolerance=3,y_tolerance=2)
            large_image=any((im['x1']-im['x0'])*(im['bottom']-im['top']) > page.width*page.height*.65 for im in page.images)
            if large_image and len(words)<80:return None
            removed=set()
            for table in page.find_tables():
                blocks=table_sections(table,words,number)
                if blocks:
                    tables.extend(blocks)
                    removed.update(id(w) for w in words if in_box(w,table.bbox))
            body=[w for w in words if id(w) not in removed and 16<w['top']<page.height-18]
            rows=reading_lines(body,page.width)
            pages.append((number,[row['text'] for row in rows]))
            if progress:progress({'pages_read':number,'pages_total':total})
        blocks=section_blocks(pages)+tables
        if not blocks:return None
        text='[PDF_LOCAL_SECTIONS]\n'+MARKER+'\n'+'\n\n'.join('# '+heading+'\n'+body for heading,body in blocks)
        complete=total<=MAX_PDF_PAGES and len(text)<=MAX_PDF_CHARACTERS
        if not complete:text='[STRUCTURE_UNCERTAIN]\n'+text[:MAX_PDF_CHARACTERS]
        return text,complete

````

## ai_core/site_search.py

Полный путь: C:\Users\KROLL\Desktop\Ai\ai_core\site_search.py.

SHA-256: `23e5e7931675986e32e23511483918dcf4f677e746d61caa1667dc093ea02163`.

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
from .site_search_technical import search_technical
from .site_search_followup import remember_focus, validated_focus, resolve_followup, concise_result, unique_technical_source
from .site_search_followup import explicit_document, document_continuation

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
        technical_contexts=[context.get('technical_focus') for context in contexts if context.get('technical_focus')]
        if technical_contexts and all(value['source']==technical_contexts[0]['source'] and value['property']==technical_contexts[0]['property'] for value in technical_contexts):
            combined={**technical_contexts[0],'subjects':list(dict.fromkeys(subject for value in technical_contexts for subject in value['subjects']))}
            if len(combined['subjects'])<=4:self.sessions[scope]['technical_focus']=combined
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
        document_focus=result.pop('_document_focus',None)
        resolved_question=result.pop('_resolved_question',question)
        result=concise_result(resolved_question,result,self.passages)
        if result.get('status')=='answer' and scope in sessions:
            focus=remember_focus(resolved_question,result,self.passages)
            if focus:sessions[scope]['technical_focus']=focus
        if result['status'] in {'not_found','unavailable','conflict','processing'} or (result['status']=='clarify' and sessions.get(scope) is previous and not result.get('context_used')):
            sessions.pop(scope,None)
        if document_focus and result['status']!='processing':
            # Даже неотвеченный вопрос может явно установить предмет разговора.
            # Сохраняем только проверенные id/редакцию, не произвольный текст.
            if scope not in sessions:self._remember(sessions,scope,[document_focus['source']],[])
            sessions[scope]['document_focus']=document_focus
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
        document_target=explicit_document(question,{key:self.sources[key] for key in allowed},
                                          [p for p in self.passages if p.source['id'] in allowed],terms)
        saved_document=(session or {}).get('document_focus')
        inherited_document=bool(not document_target and saved_document and document_continuation(question)
                                and not explicit_names(lookup_question(question)))
        if inherited_document:document_target=saved_document['source']
        if document_target and (not selected_source or selected_source==document_target):
            # Внутренний вызов использует выбранный самим поиском источник.
            # Рекурсии нет: явный selected_source уже ограничивает этот вызов.
            if not selected_source:
                result=self._search_impl(question,principal,chat_id,document_target,current_revisions,session_store)
                vocabulary=set(terms(' '.join(item.get('quote','') for item in result.get('evidence',[]))))
                omitted=set(terms(self.sources[document_target]['title']+' срок стоимость сколько какой выдать выдаётся выдача можно должен нужно использование'))
                topic=[word for word in terms(question) if word in vocabulary and word not in omitted and not word.isdigit()][:12]
                result['_document_focus']={'source':document_target,'revision':self.sources[document_target]['revision'],
                                           'topic_terms':topic or ((saved_document or {}).get('topic_terms',[]) if inherited_document else [])}
                if inherited_document:result['context_used']=True
                return result
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
        # Наличие товарного каталога на сайте не должно перехватывать
        # сравнение устройств, однозначно найденных в техническом документе.
        technical_route=None
        if not selected_source:
            technical_route=unique_technical_source(lookup_question(question),[
                item for item in self.passages if item.source['id'] in allowed
                and active(item.structure or {},day) and self._usable_passage(item,allowed,day)])
        catalog = None if technical_route else search_catalog(self, question, allowed, selected_source, day, session, sessions, scope)
        if catalog is not None:
            return catalog
        technical_focus=validated_focus((session or {}).get('technical_focus'),self.sources,self.passages)
        followup=resolve_followup(question,technical_focus)
        if followup and followup.get('clarify'):
            # Этот механизм касается только технических PDF; остальные чаты
            # продолжают использовать прежнюю проверку условий и предметов.
            technical_scope=technical_focus or (selected_source and any(p.source['id']==selected_source and (p.structure or {}).get('local_issue')
                                                                       and not (p.structure or {}).get('rule_sections') for p in self.passages))
            if technical_scope:return {'status':'clarify','text':followup['clarify'],'links':[],'evidence':[],'context_used':bool(technical_focus)}
            followup=None
        focused_question = lookup_question(followup['question'] if followup else question)
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
        generic = not raw_words or all(word in FOLLOWUP or word in {'выбрать','добавить','режим'} for word in raw_words)
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
        words = list(dict.fromkeys(current_words + (session["terms"] if context_used and not topic_followup and not saved_document else [])))
        named = [term for name in explicit_names(focused_question) if name.casefold() not in STOP for term in terms(name)][:16]
        if re.match(r'^а\s+(?:если|при)\b',normal(question)) and not named and not context_used and not selected_source:
            return {'status':'clarify','text':'Уточните, к какому материалу или правилу относится это условие.','links':[],'evidence':[]}
        numbers = re.findall(r"\d+(?:[.,]\d+)*", focused_question)[:16]
        candidates = [item for item in self.passages if item.source["id"] in allowed and (not selected_source or item.source["id"] == selected_source)
                      and active(item.structure or {}, day) and self._usable_passage(item,allowed,day)]
        scoped_rules=bool(selected_source and candidates and all((p.structure or {}).get('rule_sections') for p in candidates))
        if scoped_rules:
            # Имя документа выбирает область поиска, но не делает каждый его
            # абзац релевантным. Ранжируем предмет вопроса по телам правил.
            identity=set(terms(self.sources[selected_source]['title']))
            semantic_question=focused_question
            for pattern,replacement in ((r'\bвес(?:ит|ить|ом)?\b','масса'),(r'срок\s+службы','срок эксплуатации'),
                                        (r'подстро\w*','подстройка'),(r'потеря\w*|утрат\w*','утери'),
                                        (r'мегабит\w*','Мбит'),(r'температур\w*','температура')):
                semantic_question=re.sub(pattern,replacement,semantic_question,flags=re.I)
            current_words=[word for word in self._query_terms(semantic_question) if word not in identity]
            if saved_document and not re.match(r'^(?:теперь|отложим)\b',focused_question,re.I) and re.search(r'\bего\b|\bеё\b|\bее\b|^а для\b',focused_question,re.I):
                current_words=list(dict.fromkeys(current_words+saved_document.get('topic_terms',[])))
            words=list(current_words)
            named=[]
            numbers=[]  # Число в условии может сравниваться с границей, а не совпадать с ней.
            if re.search(r'серийн\w*\s+номер|номер\w*\s+(?:у\s+)?моего|MAC.адрес.*моего',question,re.I):
                # Номер раздела или пояснение «где найти номер» не являются
                # конкретным реквизитом. Для ответа нужно поле с самим значением.
                actual=re.compile(r'(?:серийн\w*\s+номер|номер\s+билета|MAC.адрес)\s*[:—=]\s*[\w-]*\d',re.I)
                candidates=[p for p in candidates if actual.search(p.text)]
            codes=re.findall(r'\b[А-ЯA-Z\d]+(?:-[А-ЯA-Z\d]+)+\b',focused_question)
            if len(codes)==1 and codes[0][-1].isdigit():
                code=codes[0].casefold()
                candidates=[p for p in candidates if not re.search(r'\b[А-ЯA-Z\d]+(?:-[А-ЯA-Z\d]+)+\b',p.text)
                            or code in p.text.casefold()]
            properties=[pattern for trigger,pattern in (
                (r'мощност',r'мощност'), (r'частот',r'частот|\bГц\b'), (r'напряжени',r'напряжени|\d\s*В[~\s,]'),
                (r'\bток\b',r'\bток\w*'), (r'температур',r'температур|темп[.]'),
                (r'влажност',r'влажност|влаж[.]'), (r'масс|\bвес(?:ит|ить|ом)?\b',r'масс|вес'),
                (r'гаранти',r'гаранти'), (r'срок.*\bслуж',r'срок.*(?:\bслуж|эксплуатац)'),
                (r'\bкпд\b',r'\bкпд\b')) if re.search(trigger,focused_question,re.I)]
            if properties:
                candidates=[p for p in candidates if all(re.search(pattern,p.text,re.I) for pattern in properties)]
                for request,evidence in ((r'подстро',r'подстро'),(r'предел|диапазон',r'диапазон|…|\.\.\.|от .* до'),
                                         (r'постоянн',r'постоянн'),(r'переменн',r'переменн'),
                                         (r'хран|перевоз|транспорт',r'хран|транспорт'),
                                         (r'номинальн',r'номинальн')):
                    if re.search(request,focused_question,re.I):
                        candidates=[p for p in candidates if re.search(evidence,p.text,re.I)]
                if re.search(r'температур|влажност',focused_question,re.I) and re.search(r'работ|эксплуат',focused_question,re.I):
                    candidates=[p for p in candidates if re.search(r'раб\b|рабоч|эксплуатац',p.text,re.I)]
                # Вопрос «какое напряжение» не задаёт вход/выход, номинал или диапазон.
                # Уточнение лучше произвольного выбора одной из разных строк.
                qualifier=set(terms('номинальный выходной входной диапазон предел переменный постоянный подстройка'))
                if re.search(r'напряжени|\bток\b',focused_question,re.I) and not qualifier.intersection(current_words):
                    if len({p.heading for p in candidates})>1:
                        return {'status':'clarify','text':'Уточните, какой параметр нужен: входной или выходной, номинальное значение или диапазон.', 'links':[],'evidence':[]}
        technical_source=followup['source'] if followup else selected_source or technical_route
        technical_candidates=[item for item in candidates if item.source['id']==technical_source] if technical_source else []
        technical = search_technical(focused_question, technical_candidates, terms) if technical_source and (followup or not context_used) else None
        if technical is not None:
            if technical['status'] == 'answer':
                self._remember(sessions, scope, [technical_source], current_words)
            if followup:
                technical['context_used']=True
                technical['_resolved_question']=focused_question
            technical['timing_ms'] = round((time.perf_counter() - started) * 1000, 2)
            return technical
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
            if (item.structure or {}).get('local_issue'):
                # Геометрически отделённые страницы/таблицы оцениваем по
                # релевантности ниже. Они не становятся доказательством ответа.
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
                candidates = [item for item in candidates if re.search(r"\b(?:день|дня|дней|дни|час\w*|минут\w*|недел\w*|месяц\w*|суток|срок\w*|год\w*|лет)\b", normal(item.text))]
        # Обзор объясняет весь порядок вместе с условиями. Он не требует
        # выяснять частные обстоятельства пользователя, как конкретный случай.
        conditional = None if want_overview or scoped_rules else self._conditional_answer(question, candidates, current_words, named, facet, scope, sessions,
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
        minimum = (0.10 if properties else 0.30) if scoped_rules else (0.65 if len(words) <= 3 else 0.45)
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
        if any((row[2].structure or {}).get('issue') and row[0] >= ranked[0][0] * .98 for row in ranked):
            return {'status': 'unavailable', 'text': ISSUE_TEXT, 'links': [], 'evidence': []}
        ranked = [row for row in ranked if not (row[2].structure or {}).get('issue')]
        if not ranked:
            return {'status': 'unavailable', 'text': ISSUE_TEXT, 'links': [], 'evidence': []}
        # Сначала проверяем противоречия, включая менее авторитетный источник.
        # Официальная отметка не разрешает молча выбрать одну из разных норм.
        relevant_sentences = lambda text: [sentence for sentence in self._sentences(text) if supplies_facet(sentence, facet)]
        if conflicting_passages([row[2] for row in ranked], relevant_sentences, terms):
            return {"status": "conflict", "text": CONFLICT, "links": [], "evidence": []}
        highest_authority = max(authority(row[2].source) for row in ranked)
        ranked = [row for row in ranked if authority(row[2].source) == highest_authority]
        best_score, _, best = ranked[0]
        if scoped_rules and guarded(best.text) and not want_overview and re.search(r'\bесли\b|можно|разреш|действует ли|нужно ли|допуска',question,re.I):
            conditional=self._conditional_answer(question,[best],current_words,[],facet,scope,sessions,
                                                 session if context_used else None,True,started)
            if conditional is not None:return conditional
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
        if (best.structure or {}).get('rule_sections'):
            # Раздел уже выделен геометрией/нумерацией. Повторное сокращение
            # до предложения потеряло бы «запрещается», исключение или шаг 1.
            if len(best.text)>1800:
                return {'status':'clarify','text':'Уточните пункт: в этом разделе несколько связанных условий.', 'links':[],'evidence':[]}
            answer=best.text
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

## ai_core/site_search_followup.py

Полный путь: C:\Users\KROLL\Desktop\Ai\ai_core\site_search_followup.py.

SHA-256: `3976bf0f7ba2319fb1cbb4d66d86eb54bf6adf0020acd0c2d48b4135c9de0998`.

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
    available=[item for item in passages if item.source['id']==identifier and (item.structure or {}).get('local_issue')
               and not (item.structure or {}).get('rule_sections')]
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
    if not any(p.source['id']==evidence['id'] and p.heading==evidence['heading'] and (p.structure or {}).get('local_issue')
               and not (p.structure or {}).get('rule_sections') for p in passages):return result
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


def explicit_document(question, sources, passages, tokenize):
    """Находит явно названный документ по обозначению или имени из его заголовка.

    Работает только внутри уже разрешённого набора. Общие слова вопроса
    не превращаются в имя файла, а совпадение в двух документах неоднозначно.
    """
    eligible={p.source['id'] for p in passages if (p.structure or {}).get('rule_sections')}
    identifiers=re.findall(r'\b(?:[А-ЯA-Z]{2,}[А-ЯA-Zа-яa-z\d-]*|[A-Z][a-z]{2,})(?:[- ]\d+[A-ZА-Я\d-]*)?',question)
    candidates=[]
    for identifier,source in sources.items():
        if identifier not in eligible:continue
        text=normalized(source['title']+' '+' '.join(p.text for p in passages if p.source['id']==identifier))
        title=set(tokenize(source['title']))
        names=explicit_names(question)
        named_tokens=set(tokenize(' '.join(names)))
        exact=any(re.search(r'(?<!\w)'+re.escape(normalized(label))+r'(?!\w)',text) for label in identifiers if len(label)>2)
        if exact or (named_tokens and len(named_tokens)>=2 and named_tokens.issubset(title)):
            candidates.append(identifier)
    return candidates[0] if len(candidates)==1 else None


def document_continuation(question):
    """Признак продолжения: переносим предмет, но не все слова старого вопроса."""
    return bool(len(question)<=200 and (short_reference(question) or re.match(r'^(?:теперь|отложим)\b',question,re.I)))

````

## ai_core/site_search_language.py

Полный путь: C:\Users\KROLL\Desktop\Ai\ai_core\site_search_language.py.

SHA-256: `8e9b77f11cad650e5e61cff5c58f491739eb2bfe4111d5e744a2781937c7e39e`.

````python
"""Небольшой словарь русских поисковых понятий; фактов и готовых ответов здесь нет."""
import re
from .site_search_reasoning import NUMBERS

# Выражения нормализуются только для поиска. Исходный текст ответа сохраняется.
PHRASES = (
    (r"\bкак\s+долго\s+(?:можно\s+)?держать\b", "сколько хранится"),
    (r"\bухожу\s+после\s+смены\b|\bконец\s+рабочего\s+дня\b", "закрытие"),
    (r"\bне\s+(?:работает|включается)\b|\bперестал[аио]?\s+работать\b", "поломка"),
    (r"\b(?:надеть|одеть)\s+на\s+руки\b", "перчатки"),
    (r"\bнаведения?\s+порядка\b", "уборка"),
    (r"\bсовсем\s+(?:немного|мало)\b", "минимальный"),
    (r"\bпоздоровал\w*\s+за\s+руку\b", "рукопожатие"),
)

SYNONYMS = {
    "наличные": ["купюра", "купюры", "банкнота", "наличные"],
    "мыть": ["мыть", "вымыть", "помыть", "мытьё"],
    "улица": ["улица", "снаружи"],
    "помещение": ["помещение", "внутри"],
    "взвешивание": ["взвешивание", "взвесить", "взвешивать", "отвесить", "вразвес", "весовой"],
    "грамм": ["грамм", "граммы", "г"],
    "день": ["день", "дня", "дней", "дни"],
    "человек": ["человек", "родственник", "родственники"],
    "использовать": ["использовать", "использоваться", "пользоваться"],
    "один": ["один", "одним"],
    "ремонт": ["ремонт", "починить", "ремонтировать"],
    "самостоятельный": ["самостоятельный", "самостоятельно", "самому"],
    "поломка": ["поломка", "сломалась", "сломанный", "сломался"],
    "выключить": ["выключить", "выключаются", "обесточивать", "обесточить"],
    "обязательно": ["обязательно", "обязан", "обязаны"],
}


def search_language(text: str) -> str:
    value = text.casefold().replace('ё', 'е')
    for pattern, replacement in PHRASES:
        value = re.sub(pattern, replacement, value)
    # Утрата накопленных прав/баллов — отдельный смысл, не физическое горение.
    if re.search(r"скидк|бонус|балл|накоплен", value):
        value = re.sub(r"\b(?:пропад\w*|исчез\w*|сгора\w*)\b", "истечение", value)
    return value


def requested_facet(question: str) -> str | None:
    value = question.casefold()
    if re.search(r'в каких\s+(?:пакет|упаков)|какую\s+упаков|вариант\w*\s+(?:пакет|упаков)', value):
        return 'packaging'
    if re.search(r"сколько\s+(?:(?:сейчас|сегодня|примерно|будет)\s+)*(?:стоит|стоить)|стоимость|цена|почём|за какую сумму", value):
        return 'cost'
    if re.search(r"(?:с какого|в каком) год|год (?:начала|основания)", value):
        return 'year'
    if re.search(r"\bкому\b|кто (?:(?:его|её|ее|их) )?(?:выда|принима|обраб|отвечает|исправ|оформ)", value):
        return 'recipient'
    if re.search(r"как долго|какой срок|сколько.*(?:ждать|дн|хран|длит)|подожд[её]т.*(?:день|дня|недел)", value):
        return 'duration'
    if re.search(r"как (?:отмен|оформ|направ|отправ|перед|получ)", value):
        return 'procedure'
    return None


def supplies_facet(text: str, facet: str | None) -> bool:
    value = text.casefold()
    if facet == 'packaging':
        return bool(re.search(r'пакет\w*|упаков\w*|банка|банку|коробк\w*', value))
    if facet == 'cost':
        return bool(re.search(r"\bруб\w*|₽|цен\w*|стоимост\w*|бесплатн\w*", value)) and not bool(re.search(r"не (?:указан|определ[её]н|извест|установлен)|нет (?:сведений|информации)", value))
    if facet == 'year':
        return bool(re.search(r"\b(?:19|20)\d{2}\b", text))
    if facet == 'recipient':
        return bool(re.search(r"координатор|диспетчер|старш\w* сотрудник|старш\w* смен|администратор|дежурн|менеджер|руководител|кладовщик|оператор|продавец|продавц|библиотекар", value))
    if facet == 'duration':
        return bool(re.search(r"\b(?:день|дня|дней|дни|суток|час\w*|минут\w*|недел\w*|месяц\w*|год\w*|лет)\b", value)) and bool(re.search(r"\d|\b(?:" + "|".join(NUMBERS) + r")\b", value.replace('ё', 'е')))
    if facet == 'procedure':
        return bool(re.search(r"отправ\w*|переда\w*|заполн\w*|запис\w*|показыв\w*|покаж\w*|сообщ\w*|подат\w*|звон\w*|обратит\w*", value))
    return True

````

## ai_core/site_search_runtime.py

Полный путь: C:\Users\KROLL\Desktop\Ai\ai_core\site_search_runtime.py.

SHA-256: `8c5d1b350dd8e7f601b04ad38f509bf8cd09f710cee9aff5e86c87c661f3e1dc`.

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
    digest.update(Path(__file__).with_name('site_pdf_sections.py').read_bytes())
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
        focus=context.get('document_focus')
        if isinstance(focus,dict) and focus.get('source') in identifiers:
            source=self.index.sources.get(focus['source'])
            if source and source['revision']==focus.get('revision'):
                saved['document_focus']={'source':source['id'],'revision':source['revision']}
                vocabulary=set().union(*(set(p.words) for p in self.index.passages if p.source['id']==source['id']))
                incoming=focus.get('topic_terms',[])
                if isinstance(incoming,list):
                    saved['document_focus']['topic_terms']=[word for word in incoming[:12] if isinstance(word,str) and word in vocabulary]
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

## ai_core/site_search_structure.py

Полный путь: C:\Users\KROLL\Desktop\Ai\ai_core\site_search_structure.py.

SHA-256: `1c99d27f28551f0f591bf1aed37b2dfc15349f6d330c59f0446c45fcd8c56640`.

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
    rule_sections = any('[PDF_RULE_SECTIONS]' in str(section.get('text', '')) for section in source.get('sections', []))
    for section_number, section in enumerate(source.get('sections', [])):
        raw = normalize_markup(str(section.get('text', '')))
        raw = raw.replace('[PDF_LOCAL_SECTIONS]', '').replace('[PDF_PARAMETER_MANUAL]', '').replace('[PDF_RULE_SECTIONS]', '')
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
                                         **({'rule_sections': True} if rule_sections else {}),
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

Полный путь: C:\Users\KROLL\Desktop\Ai\ai_core\site_search_technical.py.

SHA-256: `581a61f1c037f404a367a252551d01bfccae9bd044abe71a987587187594613d`.

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
    if any((p.structure or {}).get('rule_sections') for p in candidates):
        # Общие разделы содержат имя устройства в заголовке документа, а не
        # обязательно в каждой строке. Здесь нужен обычный поиск по разделам.
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

## tests/test_site_pdf_sections.py

Полный путь: C:\Users\KROLL\Desktop\Ai\tests\test_site_pdf_sections.py.

SHA-256: `28bb64e06bb5a91641c805db277b773fdb10e6bbf2279db98f87b7ad021019b9`.

````python
"""Общие свойства разбиения; вымышленные числа, не ответы экзамена."""
import tempfile
import unittest
from pathlib import Path
from ai_core.site_pdf_sections import section_blocks, reading_lines, MARKER
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch


def record(text, revision='r1'):
    return {'id':'document:regulation','revision':revision,'title':'Правила ЦБК',
            'kind':'document','url':'http://localhost/rules','access':{'scope':'restricted','users':['reader']},
            'sections':[{'heading':'','text':'[PDF_LOCAL_SECTIONS]\n'+MARKER+'\n'+text}]}


class SectionTests(unittest.TestCase):
    def test_parent_prohibition_reaches_each_child_and_ends_at_sibling(self):
        blocks=section_blocks([(1,['4.2. Читателям запрещается:', '4.2.1. Выносить журналы.',
                                  '4.2.2. Снимать на камеру.', '4.3. Посещение выставки разрешено.'])])
        self.assertIn('Читателям запрещается: Снимать на камеру.',[body for _,body in blocks])
        self.assertEqual(blocks[-1][1],'Посещение выставки разрешено.')

    def test_multisentence_point_keeps_exception_across_page(self):
        blocks=section_blocks([(1,['4.1.7. Разрешено фотографировать документы.',
                                  'Не допускается фотографирование:', '– изданных до 1902 года;']),
                               (2,['– повреждённых водой.', '4.1.8. Вход бесплатный.'])])
        first=blocks[0][1]
        self.assertIn('Разрешено',first);self.assertIn('Не допускается',first)
        self.assertIn('1902',first);self.assertIn('повреждённых',first)
        self.assertNotIn('Вход',first)

    def test_parent_condition_reaches_children_but_not_next_rule(self):
        blocks=section_blocks([(1,['4.2. Если дверь закрыта:',
                                  '4.2.1. Позвоните дежурному.']),
                               (2,['4.2.2. Ожидайте у стойки.',
                                   '4.3. Заявка оформляется бесплатно.'])])
        self.assertIn('Если дверь закрыта: Позвоните дежурному.',[b for _,b in blocks])
        self.assertIn('Если дверь закрыта: Ожидайте у стойки.',[b for _,b in blocks])
        self.assertEqual(blocks[-1][1],'Заявка оформляется бесплатно.')

    def test_procedure_keeps_prerequisite_and_steps(self):
        blocks=section_blocks([(1,['3.2. Очистка','1. Отключите устройство от сети.',
                                  '2. Добавьте раствор.', '3. Промойте.', '3.3. Хранение','Высушите устройство.'])])
        procedure=blocks[0][1]
        self.assertIn('Отключите',procedure);self.assertIn('Добавьте',procedure)
        self.assertNotIn('Высушите',procedure)

    def test_uppercase_prohibition_is_not_dropped_as_title(self):
        blocks=section_blocks([(1,['ЗАПРЕЩАЕТСЯ:', '– разбирать устройство;', '– включать без крышки.'])])
        self.assertIn('ЗАПРЕЩАЕТСЯ',blocks[0][1]);self.assertIn('без крышки',blocks[0][1])

    def test_number_without_space_keeps_parent_prohibition(self):
        blocks=section_blocks([(1,['4.2.Пользователям запрещается:', '4.2.1.Выносить журналы.',
                                  '4.2.2.Снимать на камеру.', '4.3.Доступ в зал свободный.'])])
        self.assertIn('Пользователям запрещается: Снимать на камеру.',[b for _,b in blocks])
        self.assertNotIn('запрещается',blocks[-1][1])

    def test_related_safety_rule_is_not_shortened_to_permission(self):
        blocks=section_blocks([(1,['4.2.Пользователям запрещается:', '4.2.1.Выносить журналы.'])])
        text='\n'.join('# '+heading+'\n'+body for heading,body in blocks)
        index=ClosedSiteIndex(['http://localhost']);index.publish([record(text)],index.begin('a'))
        result=index.search('Можно выносить журналы ЦБК?',Principal('reader','staff'),'q')
        self.assertEqual(result['status'],'answer');self.assertIn('запрещается',result['text'])

    def test_inline_uppercase_citation_does_not_end_prohibition_scope(self):
        blocks=section_blocks([(1,['4.2. Пользователям запрещается:',
                                  '4.2.1. Публиковать материалы (ст. 250','УК РФ).',
                                  '4.2.2. Выносить журналы.'])])
        self.assertIn('Пользователям запрещается: Выносить журналы.',[b for _,b in blocks])

    def test_safety_bullet_keeps_its_condition_and_second_sentence(self):
        blocks=section_blocks([(1,['1.1. Общая безопасность','• Не включайте прибор, если провод повреждён.',
                                  'Обратитесь в мастерскую.', '• Не используйте удлинитель.'])])
        damaged=next(body for _,body in blocks if 'повреждён' in body)
        self.assertIn('Обратитесь',damaged);self.assertNotIn('удлинитель',damaged)

    def test_one_conditional_point_does_not_absorb_entire_document(self):
        blocks=section_blocks([(1,['2.1.1. Если дверь закрыта, доступ запрещён.',
                                  '2.1.2. Температура хранения от 7 до 19 градусов.',
                                  '2.1.3. Выдача карты бесплатна.'])])
        body='\n'.join('# '+heading+'\n'+text for heading,text in blocks)
        index=ClosedSiteIndex(['http://localhost']);index.publish([record(body)],index.begin('a'))
        self.assertEqual(len(index.passages),3)
        self.assertFalse(any('доступ' in p.text and 'карты' in p.text for p in index.passages))

    def test_wrapped_safety_heading_keeps_each_bullet_and_its_continuation(self):
        blocks=section_blocks([(1,['1.1 Общая','безопасность',
                                  '• Не включайте прибор, если повреждён провод.',
                                  'Обратитесь в мастерскую.',
                                  '• Не используйте переходник.',
                                  '• Перед очисткой отключите питание.'])])
        damaged=next(body for _,body in blocks if 'повреждён' in body)
        self.assertIn('Обратитесь',damaged)
        self.assertNotIn('переходник',damaged)
        self.assertIn('Не используйте переходник.',[body for _,body in blocks])
        self.assertIn('Перед очисткой отключите питание.',[body for _,body in blocks])

    def test_failed_question_keeps_explicit_document_but_not_other_user(self):
        index=ClosedSiteIndex(['http://localhost']);index.publish([record('# Порядок\nВыдача карты бесплатна.')],index.begin('a'))
        index.search('Какая масса книги ЦБК?',Principal('reader','staff'),'a')
        context=index.sessions.get(('reader','staff',(),'a'),{})
        self.assertEqual(context.get('document_focus',{}).get('source'),'document:regulation')
        self.assertNotEqual(index.search('А стоимость карты?',Principal('outsider','staff'),'a')['status'],'answer')

    def test_explicit_topic_survives_restart_and_not_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'search.sqlite'
            engine=DurableSearch(['http://localhost'],db)
            engine.publish([record('# Выдача\nВыдача карты бесплатна.')],engine.begin('r1'))
            engine.query('Какая масса книги ЦБК?',Principal('reader','staff'),'a',None,{'document:regulation':'r1'})
            restored=DurableSearch(['http://localhost'],db)
            self.assertIn('document_focus',restored.index.sessions[('reader','staff',(),'a')])
            restored.publish([record('# Выдача\nВыдача карты бесплатна.','r2')],restored.begin('r2'))
            self.assertNotIn(('reader','staff',(),'a'),restored.index.sessions)

    def test_ambiguous_new_chat_does_not_guess_procedure(self):
        index=ClosedSiteIndex(['http://localhost']);index.publish([record('# Очистка\nДобавьте 8 ложек раствора.')],index.begin('a'))
        answer=index.search('Сколько добавить?',Principal('reader','staff'),'new')
        self.assertEqual(answer['status'],'clarify')


if __name__=='__main__':
    unittest.main()

````

## evaluation/closed_search/pdf_sections_v7_20261031/run.py

Полный путь: C:\Users\KROLL\Desktop\Ai\evaluation\closed_search\pdf_sections_v7_20261031\run.py.

SHA-256: `a5bbe64d42567d9c4f9449a9a18afe5d2b1549cd8b3e0e3d852d4fd6afe82ca4`.

````python
"""Повторная регрессия прежних 100 вопросов, отдельный журнал каждого запуска."""
import base64
import hashlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
EXAM=HERE.with_name('holdout_20261031')
sys.path.insert(0,str(ROOT))


def digest(file):return hashlib.sha256(file.read_bytes()).hexdigest()


def main():
    label=sys.argv[1]
    if not label.isalnum():raise ValueError('Invalid run label')
    out=HERE/'results'/label
    out.mkdir(exist_ok=False)
    frozen=json.loads((EXAM/'FREEZE.json').read_text(encoding='utf-8'))
    # Эталоны здесь не читаются: сравнение выполняется после записи всех ответов.
    for name in ['questions.json','sources.json']:
        if digest(EXAM/name)!=frozen['files'][name]:raise ValueError('Changed exam input')
    runtime={str(p.relative_to(ROOT)):digest(p) for p in (ROOT/'ai_core').glob('*.py')}
    (out/'START.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'runtime':runtime,
                                            'frozen_inputs':{name:frozen['files'][name] for name in ['questions.json','sources.json','gold.json']}},indent=2),encoding='utf-8')
    from ai_core import site_documents
    from ai_core.site_search import Principal
    from ai_core.site_search_runtime import DurableSearch
    site_documents.CACHE=HERE/'results'/'document-cache'
    sources=json.loads((EXAM/'sources.json').read_text(encoding='utf-8'))
    questions=json.loads((EXAM/'questions.json').read_text(encoding='utf-8'))
    records=[];extraction=[]
    for source in sources:
        file=EXAM/'sources'/source['file']
        if digest(file)!=source['sha256']:raise ValueError('Changed original')
        start=time.perf_counter()
        def progress(value):
            if value.get('pages_read',0)%20==0:print(source['id'],value,flush=True)
        try:
            text,complete=site_documents.extract_document('data:application/octet-stream;base64,'+base64.b64encode(file.read_bytes()).decode(),source['file'],progress=progress)
        except Exception:
            text,complete='',False
            (out/(source['id']+'-error.txt')).write_text(traceback.format_exc(),encoding='utf-8')
        extraction.append({'id':source['id'],'seconds':time.perf_counter()-start,'complete':complete,'chars':len(text)})
        (out/(source['id']+'-extracted.txt')).write_text(text,encoding='utf-8')
        records.append({'id':'document:'+source['id'],'revision':source['sha256'],'title':source['title'],
                        'kind':'document','authority':'official','url':'http://holdout.invalid/doc/'+source['id'],
                        'access':{'scope':'restricted','users':['exam-reader']},'complete':complete,
                        'sections':[{'heading':source['title'],'text':text}]})
        print('extracted',extraction[-1],flush=True)
    (out/'extraction.json').write_text(json.dumps(extraction,indent=2),encoding='utf-8')
    (out/'records.json').write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')
    engine=DurableSearch(['http://holdout.invalid'],out/'exam.sqlite',extraction_version=site_documents.PARSER_VERSION)
    start=time.perf_counter();engine.publish(records,engine.begin(label))
    (out/'index.json').write_text(json.dumps({'seconds':time.perf_counter()-start,'passages':len(engine.index.passages),
        'per_source':{r['id']:{'count':sum(p.source['id']==r['id'] for p in engine.index.passages),
        'largest':max((len(p.text) for p in engine.index.passages if p.source['id']==r['id']),default=0)} for r in records}},indent=2),encoding='utf-8')
    revisions={r['id']:r['revision'] for r in records}
    with (out/'answers.jsonl').open('x',encoding='utf-8') as output:
        for question in questions:
            start=time.perf_counter()
            try:answer=engine.query(question['question'],Principal('exam-reader','staff'),question['chat'],None,revisions)
            except Exception:answer={'status':'error','text':'','evidence':[],'links':[],'error':traceback.format_exc()}
            row={**question,'result':answer,'milliseconds':round((time.perf_counter()-start)*1000,3)}
            output.write(json.dumps(row,ensure_ascii=False)+'\n');output.flush();os.fsync(output.fileno())
            print(question['id'],answer['status'],flush=True)
    changed=[name for name,value in runtime.items() if digest(ROOT/name)!=value]
    (out/'FINISH.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'changed_during_run':changed,
                                             'answers_sha256':digest(out/'answers.jsonl')},indent=2),encoding='utf-8')
    if changed:raise RuntimeError('Runtime changed during run')


if __name__=='__main__':
    try:main()
    except Exception:traceback.print_exc();sys.exit(1)

````

## evaluation/closed_search/pdf_sections_v7_20261031/postcheck.py

Полный путь: C:\Users\KROLL\Desktop\Ai\evaluation\closed_search\pdf_sections_v7_20261031\postcheck.py.

SHA-256: `5d38d01b51f3a739612dbe06471d5e387bb38d1f8be10868dc309742813766da`.

````python
"""Отдельные проверки после экзамена; исходная база и 100 ответов не меняются."""
from __future__ import annotations
import copy
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'results' / 'verified'
EXAM = HERE.with_name('holdout_20261031')
sys.path.insert(0, str(HERE.parents[2]))
from ai_core.site_documents import PARSER_VERSION
from ai_core.site_search import Principal
from ai_core.site_search_runtime import DurableSearch


def main():
    if not (OUT / 'FINISH.json').is_file():
        raise RuntimeError('First finish all 100 questions')
    db = OUT / 'postcheck.sqlite'
    if db.exists():
        raise RuntimeError('Postcheck already started; preserve its database')
    original = json.loads((OUT / 'records.json').read_text(encoding='utf-8'))
    records = copy.deepcopy(original)
    # Только технический адрес просмотра. Текст и права исходных документов прежние.
    for record in records:
        record['fragment_url'] = 'http://holdout.invalid/closed-search-fragment'
    engine = DurableSearch(['http://holdout.invalid'], db, extraction_version=PARSER_VERSION)
    engine.publish(records, engine.begin('postcheck-initial'))
    revisions = {item['id']: item['revision'] for item in records}
    actor, outsider = Principal('exam-reader', 'staff'), Principal('exam-outsider', 'staff')
    checks = []

    def check(name, passed, **detail):
        checks.append({'name': name, 'passed': bool(passed), **detail})

    statistics = []
    for source in records:
        parts = [p for p in engine.index.passages if p.source['id'] == source['id']]
        statistics.append({'id': source['id'], 'passages': len(parts),
                           'largest_passage': max((len(p.text) for p in parts), default=0),
                           'structure_unavailable': engine.index.sources[source['id']].get('structure_unavailable'),
                           'longer_than_2000': sum(len(p.text)>2000 for p in parts)})
    questions = json.loads((EXAM / 'questions.json').read_text(encoding='utf-8'))
    for position in [0, 20, 40, 60, 80]:
        q = questions[position]
        answer = engine.query(q['question'], outsider, q['chat'], None, revisions)
        check('outsider-' + q['id'], answer['status'] != 'answer' and not answer.get('evidence') and not answer.get('links'), result=answer)

    # Создаём реальный успешный контекст доступного документа и проверяем scope.
    answer = engine.query(questions[80]['question'], actor, 'isolation-shared', None, revisions)
    owner_scope = (actor.user_id, actor.role, (), 'isolation-shared')
    other_scope = (outsider.user_id, outsider.role, (), 'isolation-shared')
    check('owner-context-created', owner_scope in engine.index.sessions, status=answer['status'])
    owner_context = copy.deepcopy(engine.index.sessions.get(owner_scope))
    answer = engine.query('А адрес можно пришить?', outsider, 'isolation-shared', None, revisions)
    check('same-chat-other-user', other_scope not in engine.index.sessions and engine.index.sessions.get(owner_scope)==owner_context
          and not answer.get('evidence') and not answer.get('links') and answer['status']!='answer', result=answer)
    fresh_scope = (actor.user_id, actor.role, (), 'isolation-fresh')
    check('fresh-chat-has-no-inherited-context', fresh_scope not in engine.index.sessions)
    # Новый объект восстанавливает SQLite без OCR; качество реплик не переоцениваем.
    restored = DurableSearch(['http://holdout.invalid'], db, extraction_version=PARSER_VERSION)
    check('restore-index', restored.restored and restored.index.state=='ready' and len(restored.index.passages)==len(engine.index.passages),
          restored=restored.restored, passages=len(restored.index.passages), milliseconds=restored.restore_ms)
    restored_context = restored.index.sessions.get(owner_scope)
    check('restore-owner-context-only', restored_context is not None and other_scope not in restored.index.sessions
          and fresh_scope not in restored.index.sessions
          and restored_context.get('ids')==owner_context.get('ids'),
          restored_ids=restored_context.get('ids') if restored_context else [])
    target = next(item for item in restored.index.fragment_targets.values() if item['id']=='document:post')
    args = (target['id'], target['revision'], target['fragment'])
    check('owner-fragment-opens', restored.fragment(*args, actor, revisions)['status']=='ready')
    hidden = restored.fragment(*args, outsider, revisions)
    check('outsider-fragment-blocked', hidden['status']=='not_found' and 'text' not in hidden, result=hidden)
    new_revisions = {**revisions, 'document:post':'postcheck-revision-2'}
    changed = restored.fragment(*args, actor, new_revisions)
    check('live-revision-blocks-old-link', changed['status']=='changed' and 'text' not in changed, result=changed)
    changed_records = copy.deepcopy(records)
    changed_records[-1]['revision'] = new_revisions['document:post']
    # Смена только метки редакции проверяет закрытие старого адреса, не перенос разделов.
    restored.publish(changed_records, restored.begin('postcheck-revision-2'))
    changed = restored.fragment(*args, actor, new_revisions)
    check('published-revision-blocks-old-link', changed['status']=='changed' and 'text' not in changed, result=changed)
    remaining = [item for item in changed_records if item['id']!='document:post']
    remaining_revisions = {item['id']:item['revision'] for item in remaining}
    restored.publish(remaining, restored.begin('postcheck-deleted'))
    deleted = restored.fragment(*args, actor, remaining_revisions)
    check('deleted-source-blocks-old-link', deleted['status']=='not_found' and 'text' not in deleted, result=deleted)
    check('deleted-source-prunes-context', all('document:post' not in c.get('ids',[]) for c in restored.index.sessions.values()))
    report = {'at':datetime.now(timezone.utc).isoformat(), 'passed':sum(c['passed'] for c in checks),
              'total':len(checks), 'checks':checks, 'passage_statistics':statistics}
    with (OUT / 'postcheck.json').open('x',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)

````

## evaluation/closed_search/pdf_sections_v7_20261031/grade.cjs

Полный путь: C:\Users\KROLL\Desktop\Ai\evaluation\closed_search\pdf_sections_v7_20261031\grade.cjs.

SHA-256: `909406c908e4eeecffa8a287e82d1c37d08036d5e2e8a9e23f4d387046b1b26c`.

````javascript
/* Ручные оценки после выполнения движка. Этот файл не импортируется поиском.
 * Эталон, вопрос и первый ответ остаются в прежнем каталоге без изменений.
 * Скрипт проверяет целостность, подсчитывает решения и пишет полный журнал.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const base = __dirname;
const root = path.resolve(base, '../../..');
const exam = path.join(base, '../holdout_20261031');
const out = path.join(base, 'results/verified');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const digest = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const lines = file => fs.readFileSync(file, 'utf8').trim().split(/\r?\n/).map(JSON.parse);
const decisions = new Map();
function mark(numbers, grade, reason, issue = null, presentation = []) {
  for (const number of numbers) {
    const id = 'q' + String(number).padStart(3, '0');
    if (decisions.has(id)) throw new Error('Duplicate judgment ' + id);
    decisions.set(id, { id, grade, reason, issue, presentation });
  }
}

mark([1], 'correct', 'Для модификации 12 В выбран её номинальный ток 2,50 А.');
mark([2], 'correct', 'Для модификации 24 В выбран её номинальный ток 1,25 А.');
mark([3], 'correct', 'Общая ячейка правильно отнесена к обеим модификациям: 30 Вт.');
mark([4], 'abstained', 'Подстройка ±8% есть в оригинале, но ответ не найден.', 'retrieval_miss');
mark([5,6,7,8,9,10], 'correct', 'Искомая характеристика, числовая граница, единицы и режим соответствуют эталону.');
mark([11], 'correct', 'Сохранены условие параллельного подключения и одинаковые длина и сечение проводов.');
mark([12], 'correct', 'В тексте присутствует полный ответ: керамический конденсатор параллельно нагрузке, не менее 0,1 мкФ и ≥1,5 Uвых. Статус partial сам по себе не определяет смысловую оценку; лишнее уточнение и подписи отмечены отдельно.', null, ['diagram_noise','unnecessary_clarification']);
mark([13,14,15], 'correct', 'Масса, срок эксплуатации и гарантия найдены для того же блока; короткие уточнения сохраняют предмет.');
mark([16], 'correct', 'Смена характеристики на монтаж выполнена; питание должно быть отключено у блока и всех подключённых устройств.', null, ['service_heading']);
mark([17,18], 'correct', 'Не выдуманы отсутствующие складские остатки или личный серийный номер.');
mark([19,20], 'correct', 'Запрошен недостающий вид характеристики без случайного выбора значения. Модификацию может потребоваться уточнить следующим шагом.');

mark([21,22,24,25], 'abstained', 'Ответ есть в инструкции, но смысл индикатора либо порядок проверки соединения не найден.', 'retrieval_miss');
mark([23], 'wrong', 'На вопрос о мигающем индикаторе выдан порядок проверки кабеля; передача/приём данных не объяснены.', 'retrieval_precision');
mark([26], 'correct', 'Максимум 100 метров указан явно, но вместе с лишними действиями проверки.', null, ['excess']);
mark([27,28,29,30], 'correct', 'Рабочий режим и хранение различены; температуры и влажность с отсутствием конденсата верны.');
mark([31], 'correct', 'Назван правильный метод Store-and-Forward.');
mark([32], 'correct', 'Для Gigabit Ethernet явно указаны 2000 Мбит/с в полнодуплексном режиме.', null, ['excess']);
mark([33], 'correct', 'Названы все три автоматически согласуемые скорости RJ45.');
mark([34,35], 'correct', 'Сохранены коммутатор и характеристика кадров; нужные 1 488 000 и 148 800 явно привязаны к соответствующим портам.', null, ['excess']);
mark([36], 'partial', 'Запрет сырого подвала узнаваем, но строки условия перемешаны с авторскими правами соседней колонки. Такой повреждённый текст не засчитывается полным ответом.', 'column_order', ['mixed_columns']);
mark([37,38], 'correct', 'Цена магазина и MAC-адрес экземпляра не выдуманы; отсутствие данных сообщено.');
mark([39,40], 'correct', 'В новом чате запрошен неизвестный предмет вопроса.');

mark([41], 'wrong', 'Вместо регистрации с 14 лет выданы условия посещения несовершеннолетними мероприятий с взрослым.', 'retrieval_precision');
mark([42], 'wrong', 'Описана очная запись; возможность дистанционной записи не названа.', 'retrieval_precision');
mark([43], 'correct', 'Очная запись прямо названа бесплатной.', null, ['excess']);
mark([44], 'correct', 'Пластиковый билет с фотографией указан явно.', null, ['excess']);
mark([45], 'correct', 'Стоимость изготовления дубликата компенсирует пользователь.', null, ['service_heading']);
mark([46], 'correct', 'Основание коллективного пользования — договор.');
mark([47], 'correct', 'Сохранены отсутствие документа у ребёнка и сопровождение лицом от 18 лет.', null, ['excess']);
mark([48], 'correct', 'Сохранено требование выключенных звуковых сигналов.');
mark([49], 'correct', 'Свободные розетки в читательских помещениях указаны.');
mark([50], 'correct', 'Сохранены музыкальный отдел, чехол и специально отведённое место для игры.');
mark([51], 'wrong', 'Общий запрет теперь сохранён, но найден запрет копировать электронные ресурсы вместо правила о штампе на книге.', 'retrieval_precision');
mark([52], 'correct', 'Сохранён родительский запрет и граница до 1830 года, применимая к книге 1820 года.', null, ['excess']);
mark([53], 'correct', 'Билет выдаётся на 5 лет.');
mark([54], 'correct', 'Короткая реплика об утере относится к прежнему билету; компенсация дубликата указана.', null, ['service_heading']);
mark([55], 'correct', 'Назван допустимый способ дистанционного пользования — открытые ресурсы электронной библиотеки.');
mark([56], 'correct', 'Полный пункт сохраняет запрет съёмки при корешке более 4 см; он применим к 5 см.', null, ['excess','unrelated_footnote']);
mark([57], 'correct', 'Не выдуман номер личного читательского билета.');
mark([58], 'partial', 'Обязанность оплатить дубликат названа, но не сообщено, что конкретной суммы в документе нет.', 'wrong_action', ['service_heading']);
mark([59,60], 'correct', 'Запрошен недостающий предмет вопроса.');

mark([61], 'abstained', 'Напряжение и частота находятся в исходнике, но составной вопрос не получил ответа.', 'retrieval_miss');
mark([62], 'correct', 'Мощность 1850–2200 Вт явно указана; рядом остались лишние технические сведения.', null, ['excess']);
mark([63], 'correct', 'Сохранён прямой запрет использования на улице и в ванной.');
mark([64], 'correct', 'Прямо указаны бытовое назначение и запрет профессионального использования.', null, ['punctuation']);
mark([65], 'correct', 'Условие повреждения шнура, запрет использования и обращение в авторизованный сервис сохранены вместе.');
mark([66], 'wrong', 'Вместо запрета удлинителя выбрана общая вводная о безопасности. Это регрессия относительно первой оценки.', 'retrieval_precision');
mark([67], 'wrong', 'Запрет открывать крышку при кипении не отвечает на вопрос о заполнении на подставке. Это регрессия относительно первой оценки.', 'retrieval_precision');
mark([68], 'correct', 'Открытая крышка связана с отсутствием автоматического выключения.', null, ['excess','service_symbols']);
mark([69], 'correct', 'Для ручной остановки явно указано перевести выключатель вверх.', null, ['excess','service_symbols']);
mark([70], 'correct', 'Прямо указано дать чайнику остыть перед каждым нагревом.', null, ['excess','service_symbols']);
mark([71], 'correct', 'Смягчённая или фильтрованная вода рекомендована для предотвращения накипи.', null, ['excess','service_symbols']);
mark([72], 'correct', 'Сохранён порядок: вскипятить, отключить от сети, добавить 2 стакана уксуса либо 1 столовую ложку кислоты.', null, ['excess','service_symbols']);
mark([73], 'wrong', 'Вместо запрещённых чистящих средств выдано правило утилизации упаковки.', 'retrieval_precision');
mark([74], 'correct', 'Предмет предыдущего вопроса сохранён; запрет погружения прибора в воду назван.');
mark([75], 'wrong', 'Вместо условий хранения выдана процедура удаления накипи.', 'retrieval_precision', ['excess']);
mark([76], 'abstained', 'Перевозка относится к прежнему прибору, но смешанный длинный раздел вызывает уточнение вместо ответа.', 'section_boundary');
mark([77,78], 'correct', 'Серийный номер личного экземпляра и текущая цена не выдуманы.');
mark([79,80], 'correct', 'В новом чате без предмета использования/добавления запрошена вводная.');

mark([81], 'correct', 'Названы допустимые варианты упаковки бумажных материалов до 2 кг.');
mark([82], 'correct', 'Размер 51 × 28 × 20 см относится именно к гофрокоробке.', null, ['excess']);
mark([83], 'correct', 'Минимальное место для адресного ярлыка 10,5 × 14,8 см указано.', null, ['excess']);
mark([84], 'correct', 'Явно запрещено повторное использование коробки Почты России.', null, ['excess']);
mark([85], 'correct', 'Явно запрещено перекрывать штрихкод скотчем или плёнкой.', null, ['excess']);
mark([86], 'abstained', 'Правило заполнения пустот есть, но разговорная формулировка не распознана.', 'retrieval_miss');
mark([87], 'abstained', 'Вместо правил о жидкости запрошено постороннее условие о наклеивании бланка.', 'false_condition');
mark([88], 'wrong', 'Вместо герметичной ёмкости для порошка выдана упаковка пушнины.', 'retrieval_precision', ['excess']);
mark([89], 'wrong', 'Свалка правил для других вложений не содержит нужного ответа о саженцах и защите дна.', 'retrieval_precision', ['excess']);
mark([90], 'partial', 'Вентиляция указана только для твёрдых сортов; общий вопрос о фруктах покрыт не полностью, как и в первой оценке.', 'missing_condition', ['excess']);
mark([91], 'correct', 'Шины должны быть чистыми и в мешках.', null, ['excess']);
mark([92], 'correct', 'Исключение для цельного небьющегося предмета без острых выступов сохранено.', null, ['excess']);
mark([93], 'wrong', 'Требования к светлой однородной ткани и внутренним швам отсутствуют.', 'retrieval_precision');
mark([94], 'abstained', 'Короткое продолжение о тканевой упаковке потеряно.', 'followup');
mark([95], 'wrong', 'Вместо упаковки хрупкого вложения выбран класс электрической защиты блока питания из другого документа.', 'wrong_source');
mark([96], 'abstained', 'Порядок предъявления отправления с описью не найден в прежнем почтовом контексте.', 'followup');
mark([97], 'correct', 'Тариф отсутствует и не выдуман.');
mark([98], 'wrong', 'Вместо признания отсутствия сведений о личной посылке выдано меню сайта.', 'wrong_action', ['navigation']);
mark([99], 'correct', 'При неизвестном предмете выбора запрошена вводная.');
mark([100], 'partial', 'Зависимость от типа отправления названа, но тип и направление не запрошены, как и в первой оценке.', 'wrong_action');

function metric(rows) {
  const correct = rows.filter(r => r.grade === 'correct').length;
  return { correct, total: rows.length, percent: 100 * correct / rows.length };
}
function counts(rows, key) {
  const values = {};
  for (const row of rows) values[row[key] || 'none'] = (values[row[key] || 'none'] || 0) + 1;
  return values;
}
function main() {
  const freeze = read(path.join(exam, 'FREEZE.json'));
  const finish = read(path.join(out, 'FINISH.json'));
  const start = read(path.join(out, 'START.json'));
  const errors = [];
  for (const [name, hash] of Object.entries(freeze.files)) {
    if (digest(path.join(exam, name)) !== hash) errors.push(name);
  }
  for (const [name, hash] of Object.entries(start.runtime)) {
    if (digest(path.join(root, name)) !== hash) errors.push(name);
  }
  const firstFinish = read(path.join(exam, 'results/FINISH.json'));
  const firstStart = read(path.join(exam, 'results/START.json'));
  if (digest(path.join(exam, 'FREEZE.json')) !== firstStart.freeze_sha256) errors.push('first freeze');
  // Связываем ручные решения с тем неизменённым журналом, который прочитан.
  if (finish.answers_sha256 !== '033c4cedba11a484a8eb09399ede0d256599dd1cc8977052f9851c17f6f3e015') errors.push('unreviewed answers');
  if (digest(path.join(exam, 'results/baseline.jsonl')) !== firstFinish.baseline_sha256) errors.push('first baseline');
  if (digest(path.join(out, 'answers.jsonl')) !== finish.answers_sha256) errors.push('current answers');
  if (finish.changed_during_run.length) errors.push('runtime modified during evaluation');
  if (errors.length) throw new Error('Integrity failed: ' + errors.join(', '));
  const raw = lines(path.join(out, 'answers.jsonl'));
  const questions = read(path.join(exam, 'questions.json'));
  const gold = read(path.join(exam, 'gold.json'));
  const previous = read(path.join(exam, 'results/judgments.json'));
  if (raw.length !== 100 || decisions.size !== 100) throw new Error('Need 100 answers and judgments');
  for (let i = 0; i < 100; i++) {
    if (raw[i].id !== questions[i].id || raw[i].question !== questions[i].question || raw[i].chat !== questions[i].chat) {
      throw new Error('Changed question or order: ' + i);
    }
  }
  const rows = raw.map((r, i) => ({ ...r, ...gold[i], ...decisions.get(r.id), previous_grade: previous[i].grade }));
  const latency = raw.map(r => r.milliseconds).sort((a, b) => a - b);
  const sources = read(path.join(exam, 'sources.json'));
  const postcheck = read(path.join(out, 'postcheck.json'));
  const summary = {
    graded_at: new Date().toISOString(), type: 'known regression; not new holdout',
    integrity: 'frozen inputs, first baseline and final runtime hashes verified',
    answers_sha256: finish.answers_sha256, first_answers_sha256: firstFinish.baseline_sha256,
    overall: metric(rows), answerable: metric(rows.filter(r => r.action === 'answer')),
    insufficient: metric(rows.filter(r => r.action === 'insufficient')),
    clarify: metric(rows.filter(r => r.action === 'clarify')),
    dialogue_turns: metric(rows.filter(r => r.dialogue)),
    followup_only: metric(rows.filter(r => r.dialogue && ((Number(r.id.slice(1)) - 1) % 20) > 12)),
    by_source: Object.fromEntries(sources.map(s => [s.id, metric(rows.filter(r => r.source === s.id))])),
    grades: counts(rows, 'grade'), issues: counts(rows.filter(r => r.grade !== 'correct'), 'issue'),
    statuses: counts(raw.map(r => r.result), 'status'),
    newly_correct: rows.filter(r => r.grade === 'correct' && r.previous_grade !== 'correct').map(r => r.id),
    lost_correct: rows.filter(r => r.grade !== 'correct' && r.previous_grade === 'correct').map(r => r.id),
    correct_with_presentation_issues: rows.filter(r => r.grade === 'correct' && r.presentation.length).length,
    latency_ms: { median: (latency[49] + latency[50]) / 2, p95: latency[94], max: latency[99], total: latency.reduce((a, b) => a + b, 0) },
    index: read(path.join(out, 'index.json')), extraction: read(path.join(out, 'extraction.json')),
    postcheck: { passed: postcheck.passed, total: postcheck.total }
  };
  const md = ['# Повторная проверка PDF и уточнений: 100 прежних вопросов', '',
    'Это известный регрессионный набор. Вопросы, эталоны, оригиналы и первый результат не изменялись. Движок получил только прежние вопросы и чаты; правильный файл или страницы извне не выбирались. Оценка ручная по прежнему METHOD.md, оценщик — агент, не внешняя комиссия. Статус ответа не подменяет смысловую оценку.', '',
    'Известные вопросы использовались при отладке; диагностические прогоны сохранены отдельно. Итоговый журнал verified записан одним проходом без изменений ядра. Доля правильных здесь не является оценкой качества на новых документах.', '',
    'Итог: ' + summary.overall.correct + '/100; раньше 30/100. SHA-256: `' + finish.answers_sha256 + '`.', ''];
  for (const row of rows) {
    md.push('## ' + row.id, '', 'Вопрос: ' + row.question, '', 'Прежний эталон: ' + row.answer, '',
      'Проверка по оригиналу: ' + row.reference, '', 'Ожидаемое действие: ' + row.action + '. Статус движка: ' + row.result.status + '.', '',
      'Полный ответ Ватэс:', '', '```text', row.result.text, '```', '',
      'Оценка: ' + row.grade + ' (первый прогон: ' + row.previous_grade + '). ' + row.reason, '',
      'Подача: ' + (row.presentation.join(', ') || 'существенных замечаний нет') + '.', '',
      'Доказательства и ссылки:', '', '```json', JSON.stringify({ evidence: row.result.evidence, links: row.result.links }, null, 2), '```', '');
  }
  fs.writeFileSync(path.join(out, 'judgments.json'), JSON.stringify([...decisions.values()].sort((a, b) => a.id.localeCompare(b.id)), null, 2) + '\n');
  fs.writeFileSync(path.join(out, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
  fs.writeFileSync(path.join(base, 'ALL_100_RESULTS.md'), md.join('\n'));
  console.log(JSON.stringify(summary, null, 2));
}
try { main(); } catch (error) { console.error(error.stack); process.exitCode = 1; }

````

## evaluation/closed_search/pdf_sections_v7_20261031/package_report.cjs

Полный путь: C:\Users\KROLL\Desktop\Ai\evaluation\closed_search\pdf_sections_v7_20261031\package_report.cjs.

SHA-256: `971f357dbffa8dc4c9a7162955bb53366246c60c2d8207f2c1b9a2be6d79158d`.

````javascript
/* Собирает полный исходный код и перечень хешей для локальной проверки.
 * Не изменяет ядро, первый экзамен, журналы ответов или рабочую LMS.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

function main() {
  const here = __dirname;
  const root = path.resolve(here, '../../..');
  const before = path.join(here, 'before/ai_core');
  const relative = file => path.relative(root, file).replaceAll('\\', '/');
  const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  const names = fs.readdirSync(path.join(root, 'ai_core')).filter(name => name.endsWith('.py')).sort();
  const changed = names.filter(name => !fs.existsSync(path.join(before, name)) || hash(path.join(before, name)) !== hash(path.join(root, 'ai_core', name)));
  const files = changed.map(name => path.join(root, 'ai_core', name));
  files.push(path.join(root, 'tests/test_site_pdf_sections.py'));
  for (const name of ['run.py', 'postcheck.py', 'grade.cjs', 'package_report.cjs']) files.push(path.join(here, name));
  const start = JSON.parse(fs.readFileSync(path.join(here, 'results/verified/START.json'), 'utf8'));
  for (const [name, expected] of Object.entries(start.runtime)) {
    if (hash(path.join(root, name)) !== expected) throw new Error('Core changed after run: ' + name);
  }
  const manifest = {
    at: new Date().toISOString(), root, final_run: 'results/verified',
    runtime_count: Object.keys(start.runtime).length,
    changed_core: changed.map(name => ({
      path: 'ai_core/' + name,
      before: fs.existsSync(path.join(before, name)) ? hash(path.join(before, name)) : null,
      after: hash(path.join(root, 'ai_core', name))
    })),
    full_code_files: files.map(file => ({ path: relative(file), sha256: hash(file) })),
    checks: ['tests-verified.txt', 'verified/answers.jsonl', 'verified/judgments.json', 'verified/postcheck.json', 'verified/summary.json']
      .map(name => ({ path: 'results/' + name, sha256: hash(path.join(here, 'results', name)) }))
  };
  const md = ['# Разделы PDF и уточнения: полный код', '',
    'Проект: C:/Users/KROLL/Desktop/Ai. Полные текущие исходники изменённых файлов, нового теста и проверочных скриптов. Содержимое не сокращено. Для ядра проверено совпадение с хешами итогового прогона verified. Снимок прежнего ядра сохранён в evaluation/closed_search/pdf_sections_v7_20261031/before/ai_core.', '',
    'Отчёт и ограничения: PDF_SECTIONS_V7_REPORT_2026_10_31.md. Текущий результат 72/100 не является приёмкой универсального качества; рабочая LMS автоматически не обновлялась.', ''];
  for (const file of files) {
    const body = fs.readFileSync(file, 'utf8');
    md.push('## ' + relative(file), '', 'Полный путь: ' + file + '.', '', 'SHA-256: `' + hash(file) + '`.', '',
      '````' + (file.endsWith('.py') ? 'python' : 'javascript'), body, '````', '');
  }
  fs.writeFileSync(path.join(root, 'PDF_SECTIONS_V7_FULL_CODE_2026_10_31.md'), md.join('\n'));
  fs.writeFileSync(path.join(here, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
  console.log(JSON.stringify({ changed_core: changed.length, full_files: files.length, runtime_count: manifest.runtime_count }));
}
try { main(); } catch (error) { console.error(error.stack); process.exitCode = 1; }

````
