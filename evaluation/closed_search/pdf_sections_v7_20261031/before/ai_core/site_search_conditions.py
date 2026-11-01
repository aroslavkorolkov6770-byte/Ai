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
    # Одинаковое событие может быть названо существительным или глаголом.
    # Эти формы нужны только проверке условий, исходную цитату не меняем.
    value = re.sub(r'\b(?:выявлен\w*|обнаружен\w*|нашли|найден\w*)\b', 'выявлена', value)
    value = re.sub(r'\bзакрыт\w*\b', 'закрытие', value)
    value = re.sub(r'\bуже\b', '', value)
    # Явная локальная связь двух частей предложения: событие, после этого факт.
    # Не переносим «этого» между предложениями или из чужого контекста.
    value = re.sub(r'([^.!?,;]+),\s*(до|после) этого\s+([^.!?;]+)',
                   lambda match: match[3] + ' ' + match[2] + ' ' + match[1], value)
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
    temporal = re.search(r'\b(до|после)\s+(.+)', expected)
    for part in fragments:
        tokens = set(tokenize(part))
        if temporal:
            # Поисковые стоп-слова не должны уравнивать «до» и «после».
            # Сравниваем только явно указанный момент того же события.
            event = set(tokenize(temporal[2]))
            predicate = set(tokenize(expected[:temporal.start()]))
            moments = [(match[1], set(tokenize(match[2]))) for match in
                       re.finditer(r'\b(до|после)\s+([^,;.!?]+)', part)]
            relevant = [moment for moment, observed in moments if event and event.issubset(observed)]
            if relevant and predicate.issubset(tokens):
                if UNKNOWN.search(part) or re.search(r'\bли\b', part) or len(set(relevant)) != 1:
                    observations.append(None)
                elif re.search(r'\bне\s+(?:до|после)\b', part):
                    # «Не после» не доказывает «до»: возможно, одновременно.
                    observations.append(None)
                else:
                    observations.append(relevant[0] == temporal[1])
                continue
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
        # В реальных инструкциях результат часто отделён тире, а не запятой.
        # Берём первое тире только после явного «если», сохраняя весь результат.
        dash_prefix = re.match(r'^если\s+([^—;:]+?)\s+(?:—|–|-)\s+(.+)', clause, re.I)
        if dash_prefix and (prefix is None or dash_prefix.start(2) < prefix.start(2)):
            prefix = dash_prefix
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
        def temporal_key(item):
            condition = normalized(item.condition or '')
            if not re.search(r'\b(?:до|после)\b', condition):
                return None
            return frozenset(tokenize(re.sub(r'\b(?:до|после)\b', '', condition)))
        temporal_keys = {temporal_key(item) for item in items if score(item) >= conditional_score} - {None}
        # Короткое исключение часто не повторяет название товара. Его нельзя
        # исключать по числу совпавших слов. Но запрет хранения не должен
        # подменять правило возврата из соседней части того же раздела.
        items = [item for item in items if score(item) >= conditional_score or
                 (same_operation(item) and (item.exception or temporal_key(item) in temporal_keys
                                           or re.match(r'^В остальных случаях\b', item.quote, re.I)))]
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
