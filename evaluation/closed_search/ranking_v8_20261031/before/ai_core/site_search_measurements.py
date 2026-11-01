"""Измерения с предметом: изделие, коробка и товар с упаковкой не смешиваются."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

NUMBER = r'\d+(?:[.,]\d+)?'
LENGTH_UNIT = r'(?:мм|см|м)\b'
VECTOR = re.compile(rf'(?<![\d.,]){NUMBER}\s*(?:{LENGTH_UNIT})?(?:\s*[xх×]\s*{NUMBER}\s*(?:{LENGTH_UNIT})?){{1,2}}', re.I)
SCALAR = re.compile(rf'(?<![\d.,])({NUMBER})\s*(мл|литр\w*|л|кг|грамм\w*|гр\.?|г|мм|см|м)\b', re.I)
DIMENSION_LABEL = re.compile(r'диаметр\w*|высот\w*|длин\w*|ширин\w*|глубин\w*', re.I)
ROLE = re.compile(r'без\s+упаков\w*|с\s+упаков\w*|нетто|брутто|(?:упаков\w*|короб\w*)(?:\s+(?:товар\w*|издел\w*))?|издел\w*|корпус\w*|товар\w*|крышк\w*|ручк\w*|носик\w*', re.I)
NUMERIC_FACETS = {'dimensions', 'length', 'width', 'height', 'depth', 'diameter', 'mass', 'volume'}
SCOPES = {'product', 'package', 'packed'}
LABELS = {'dimensions': 'Размеры', 'length': 'Длина', 'width': 'Ширина', 'height': 'Высота',
          'depth': 'Глубина', 'diameter': 'Диаметр', 'mass': 'Масса', 'volume': 'Объём'}


@dataclass(frozen=True)
class Measurement:
    facet: str
    scope: str
    values: tuple[Decimal, ...]
    unit: str
    display: str
    quote: str


def requested_scope(question, facet=None):
    text = question.casefold().replace('ё', 'е')
    if re.search(r'без\s+упаков|нетто|самого|самой|сам[оа]\s+издел', text):
        return 'product'
    if re.search(r'с\s+упаков|брутто', text):
        return 'packed' if facet == 'mass' else 'package'
    if re.search(r'упаков|короб|транспортн\w*\s+тар', text):
        return 'package'
    return None


def dimension_facet(text):
    text = text.casefold().replace('ё', 'е')
    for prefix, facet in [('диаметр', 'diameter'), ('высот', 'height'), ('длин', 'length'),
                          ('ширин', 'width'), ('глубин', 'depth')]:
        if prefix in text:
            return facet
    return None


def role_for(text, position, facet, inherited='product'):
    roles = list(ROLE.finditer(text[:position]))
    if not roles:
        return inherited
    role = roles[-1].group().casefold()
    if role.startswith(('без ', 'нетто', 'издел', 'корпус', 'товар')):
        return 'product'
    if role.startswith(('с ', 'брутто')):
        return 'packed' if facet == 'mass' else 'package'
    if role.startswith(('крыш', 'руч', 'носик')):
        return 'component'
    return 'package'


def decimal(text):
    return Decimal(text.replace(',', '.'))


def measurements(product):
    """Читаем подпись рядом с числом; величины из пищевой ценности исключаем."""
    result = []
    texts = [(product.heading, True)] + [(p.text, False) for p in product.passages]
    for text, is_heading in texts:
        inherited = 'product'
        for clause in re.split(r'(?<=[.!?])\s+(?=[А-ЯA-Z])|[;\n]+', text):
            if not clause.strip():
                continue
            if not re.search(r'\d', clause) and re.fullmatch(r'\s*(?:упаковка|коробка|изделие|товар)\s*:\s*', clause, re.I):
                inherited = 'package' if re.search(r'упаковка|коробка', clause, re.I) else 'product'
                continue
            if re.search(r'\b(?:не|около|примерно|до|от)\s+(?:\w+\s+)?\d', clause, re.I):
                # Приблизительное, отрицательное или предельное число не
                # подменяет точный размер для арифметического сравнения.
                continue
            occupied = []
            for match in VECTOR.finditer(clause):
                units = re.findall(LENGTH_UNIT, match.group(), re.I)
                if not units:
                    continue
                pieces = re.split(r'[xх×]', match.group(), flags=re.I)
                values = []
                for piece in pieces:
                    number = re.search(NUMBER, piece).group()
                    own_units = re.findall(LENGTH_UNIT, piece, re.I)
                    unit = (own_units or units[-1:])[0].lower()
                    values.append(decimal(number) * {'мм': 1, 'см': 10, 'м': 1000}[unit])
                scope = role_for(clause, match.start(), 'dimensions', inherited)
                result.append(Measurement('dimensions', scope, tuple(values), 'мм', match.group().strip(), clause.strip()))
                occupied.append(match.span())
            for match in SCALAR.finditer(clause):
                if any(start <= match.start() < end for start, end in occupied):
                    continue
                # Не превращаем диапазон 10–12 см в точное значение 12 см.
                if re.search(r'\d\s*[-–—]\s*$', clause[:match.start()]):
                    continue
                unit = match[2].lower().rstrip('.')
                number = decimal(match[1])
                if unit in {'мм', 'см', 'м'}:
                    labels = list(DIMENSION_LABEL.finditer(clause[:match.start()]))
                    if not labels:
                        continue
                    facet = dimension_facet(labels[-1].group())
                    number *= {'мм': 1, 'см': 10, 'м': 1000}[unit]
                    base_unit = 'мм'
                elif unit == 'мл' or unit == 'л' or unit.startswith('литр'):
                    facet, base_unit = 'volume', 'мл'
                    number *= 1 if unit == 'мл' else 1000
                else:
                    facet, base_unit = 'mass', 'г'
                    number *= 1000 if unit == 'кг' else 1
                    if re.search(r'пищев\w*\s+ценност|на\s+100\s*г|белк|жир[ыа]|углевод|засыпк', clause, re.I):
                        continue
                    if is_heading and re.search(r'банка|баночк|калабас|для хранения', clause, re.I):
                        continue
                    if re.search(r'шт\.?\s*[xх×]', clause, re.I):
                        continue
                scope = role_for(clause, match.start(), facet, inherited)
                result.append(Measurement(facet, scope, (number,), base_unit, match.group().strip(), clause.strip()))
    return list(dict.fromkeys(result))


def conflicts(values):
    """Сравниваем только одинаковую характеристику одного предмета."""
    for left in values:
        for right in values:
            if (left.facet, left.scope, left.unit) != (right.facet, right.scope, right.unit):
                continue
            count = min(len(left.values), len(right.values))
            if left.values[:count] != right.values[:count]:
                return True
    return False


def extract_measurements(product, facet, scope):
    values = [value for value in measurements(product) if value.scope == scope
              and (value.facet == facet or (facet == 'dimensions' and value.facet in {'length', 'width', 'height', 'depth', 'diameter'}))]
    if conflicts(values):
        return [], True, values
    # Дубли в названии и описании не повторяем; выбираем полную запись размеров.
    selected = {}
    for value in values:
        previous = selected.get(value.facet)
        if previous is None or len(value.values) > len(previous.values):
            selected[value.facet] = value
    quotes = [(value.display if value.facet == facet else LABELS[value.facet] + ': ' + value.display, value.quote)
              for value in selected.values()]
    return quotes, False, list(selected.values())


def scalar_value(values, facet):
    values = [value for value in values if value.facet == facet]
    if not values or conflicts(values) or any(len(value.values) != 1 for value in values):
        return None
    return {'value': str(values[0].values[0]), 'unit': values[0].unit, 'facet': facet, 'scope': values[0].scope}
