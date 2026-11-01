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
CAPTION = re.compile(r'^(?:Продолжение\s+)?таблиц[аы]\s+(\d+(?:\.\d+)*)\b', re.I | re.M)
NOTES = re.compile(r'\bсноски\s+к\s+таблице\s+(\d+(?:\.\d+)*)\b', re.I)
PENDING = re.compile(r'Продолжение строки на следующей странице[.]?', re.I)
TABLE_PENDING = re.compile(r'Продолжение таблицы на следующей странице[.]?', re.I)
CONTINUATION = re.compile(r'^Продолжение\s+таблицы\s+\d+(?:\.\d+)*\b', re.I | re.M)
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
        # 3.1 и 3.2 — разные таблицы. Точка заменяется только в служебном ключе сноски.
        prefix='t'+next(iter(ids)).replace('.', '-') if len(ids)==1 else 'p'+str(index+1)
        output[index]=re.sub(r'\[(?:p\d+-)?(\d+)\]',lambda match:'['+prefix+'-'+match[1]+']',text)
    return (UNCERTAIN+'\n' if issue else '')+'\n\n'.join(output)
