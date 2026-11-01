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
