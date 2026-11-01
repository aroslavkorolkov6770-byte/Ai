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
