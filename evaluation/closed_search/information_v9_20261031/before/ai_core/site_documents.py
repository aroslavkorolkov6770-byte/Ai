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
