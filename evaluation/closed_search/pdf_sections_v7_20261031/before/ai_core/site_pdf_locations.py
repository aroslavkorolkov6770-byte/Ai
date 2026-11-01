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
