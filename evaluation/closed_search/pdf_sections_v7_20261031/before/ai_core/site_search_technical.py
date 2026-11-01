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
