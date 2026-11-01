# Полные листинги этапа сравнения товаров — 30.10.2026


## ai_core/site_search_measurements.py

SHA-256: 0c95b8f63e2b956f625bd6910a9c8c676b6c6288a4c97b147cb1f64b85a3b061

````python
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

````

## ai_core/site_search_comparison.py

SHA-256: 51e38c07ff3425f7902d3e61778902c3c0beec29cbde52062c96920793e74284

````python
"""Сравнение двух найденных товаров: отдельное доказательство для каждой стороны."""
from __future__ import annotations

import re
from decimal import Decimal

from .site_search_measurements import requested_scope, LABELS

COMPARE = re.compile(r'\b(?:сравн\w*|сопостав\w*|отлича\w*|разниц\w*)\b', re.I)
ORDINAL = re.compile(r'\b(?:вместительнее|тяжелее|легче|длиннее|короче|шире|уже|выше|ниже|больше|меньше)\b', re.I)
EXTRA_NOISE = 'сравни сравнить сравнение сопоставь сопоставить отличаются отличие разница между обоих оба обе двух два по чем какой какая что больше меньше вместительнее тяжелее легче длиннее короче шире уже выше ниже'


def unique_keys(products):
    return list(dict.fromkeys(product.key for product in products))


def number_text(value):
    return format(value.normalize(), 'f').replace('.', ',')


def compare_catalog(index, question, products, session, sessions, scope, facet, tokenise, ignored, solve):
    from .site_search import terms, affinity, NO_DATA

    noise = ignored | set(terms(EXTRA_NOISE))
    subject_words = lambda text: set(tokenise(text)) - noise

    def match(text):
        words = subject_words(text)
        exact = [product for product in products if words and words.issubset(product.words)]
        if exact:
            return unique_keys(exact)
        return unique_keys(product for product in products if words and
                           all(any(affinity(word, token) for token in product.words) for word in words))

    explicit_request = bool(COMPARE.search(question))
    requested = explicit_request or bool(ORDINAL.search(question) and re.search(r'\b(?:какой|какая|какое|кто|что|котор\w*)\b', question, re.I))
    previous = (session or {}).get('catalog_targets', []) if (session or {}).get('catalog_compare') else []
    words = subject_words(question)
    context_used = False
    targets = []
    if requested:
        # Перебираем разделители, а не угадываем вторую карточку по общим словам.
        # «с миндалём» внутри названия не мешает «сравни X с Y».
        pairs = []
        separators = list(re.finditer(r'\s+(?:и|или|против|vs\.?)\s+', question, re.I))
        if not separators:
            separators = list(re.finditer(r'\s+с\s+', question, re.I))
        for separator in separators:
            left_text, right_text = question[:separator.start()], question[separator.end():]
            left, right = match(left_text), match(right_text)
            if len(left) == 1 and len(right) > 1:
                product = next(product for product in products if product.key == left[0])
                category = tokenise(product.heading)[:1]
                # Общий предмет слева относится и к перечисленному названию справа:
                # «объём чахаев Дао и Феникс». Явное другое название не подменяем.
                if category and category[0] in subject_words(left_text):
                    narrower = unique_keys(product for product in products if product.key in right and category[0] in product.words)
                    if narrower:
                        right = narrower
            if left and right:
                pair = (tuple(left), tuple(right))
                if pair not in pairs:
                    pairs.append(pair)
        if len(pairs) == 1:
            targets = [list(part) for part in pairs[0]]
        elif not pairs and previous and not words:
            targets = previous
            context_used = True
        elif not pairs and not explicit_request:
            # «Что делать, если уже оплатил?» не является сравнением ширины.
            return None
        else:
            return {'status': 'not_found' if not pairs else 'clarify',
                    'text': NO_DATA if not pairs else 'Уточните названия двух товаров для сравнения.', 'links': [], 'evidence': []}
    elif previous and (not words or facet is None):
        targets = [list(keys) for keys in previous]
        context_used = True
        if words:
            choices = set(match(question))
            narrowed = [(position, [key for key in keys if key in choices]) for position, keys in enumerate(targets)]
            narrowed = [(position, keys) for position, keys in narrowed if keys and len(targets[position]) > 1]
            if len(narrowed) != 1:
                return None
            targets[narrowed[0][0]] = narrowed[0][1]
    else:
        return None
    available = set(unique_keys(products))
    if len(targets) != 2 or any(not keys or not set(keys).issubset(available) for keys in targets):
        return {'status': 'not_found', 'text': NO_DATA, 'links': [], 'evidence': []}
    facet = facet or ((session or {}).get('catalog_facet') if context_used else None)
    measure_scope = requested_scope(question, facet) or ((session or {}).get('catalog_measure_scope', 'product') if context_used else 'product')
    all_keys = list(dict.fromkeys(key for keys in targets for key in keys))
    identifiers = list(dict.fromkeys(product.source['id'] for product in products if product.key in all_keys))

    def remember(extra_ids=None):
        index._remember(sessions, scope, list(dict.fromkeys(identifiers + (extra_ids or []))), [])
        sessions[scope].update(catalog_keys=all_keys, catalog_targets=targets, catalog_compare=True,
                               catalog_facet=facet, catalog_measure_scope=measure_scope)

    def result(status, text, evidence=None):
        return {'status': status, 'text': text, 'links': [], 'evidence': evidence or [], 'context_used': context_used}

    if any(len(keys) != 1 for keys in targets):
        remember()
        position = next(pos for pos, keys in enumerate(targets) if len(keys) > 1)
        names = list(dict.fromkeys(product.heading for product in products if product.key in targets[position]))
        return result('clarify', 'Уточните ' + ('первый' if position == 0 else 'второй') + ' товар: '
                      + '; '.join('«' + name + '»' for name in names[:3]) + '?')
    if targets[0] == targets[1]:
        return result('clarify', 'Указан один и тот же товар. Назовите второй товар для сравнения.')
    if facet is None:
        remember()
        return result('clarify', 'Какую характеристику сравнить: материал, состав, объём, массу или размеры?')
    ordinal = ORDINAL.search(question)
    if ordinal and facet == 'dimensions':
        remember()
        return result('clarify', 'Какой размер сравнить: длину, ширину, высоту или диаметр?')
    answers = [solve(keys[0], facet, measure_scope) for keys in targets]
    for answer, context in answers:
        if answer['status'] in {'conflict', 'unavailable', 'processing'}:
            return answer
    names = [next(product.heading for product in products if product.key == keys[0]) for keys in targets]
    texts, evidence, references = [], [], []
    answered = 0
    for name, (answer, context) in zip(names, answers):
        references.extend((context or {}).get('ids', []))
        if answer['status'] == 'answer':
            answered += 1
            texts.append(name + ' — ' + answer['text'])
            evidence.extend(answer['evidence'])
        else:
            label = LABELS.get(facet, 'Характеристика')
            texts.append(name + ' — ' + label.lower() + ' не указан' + ('ы' if facet == 'dimensions' else 'а' if facet in {'mass', 'height', 'length', 'width', 'depth'} else '') + ' в доступных материалах.')
    if not answered:
        return result('not_found', NO_DATA)
    left, right = [answer.get('_numeric') for answer, context in answers]
    if answered == 2 and left and right and (left['facet'], left['unit'], left['scope']) == (right['facet'], right['unit'], right['scope']):
        a, b = Decimal(left['value']), Decimal(right['value'])
        if a == b:
            texts.append('Значения одинаковые.')
        else:
            lower = ordinal and ordinal.group().casefold() in {'легче', 'короче', 'уже', 'ниже', 'меньше'}
            winner = (0 if a < b else 1) if lower else (0 if a > b else 1)
            label = 'Меньшее значение' if lower else 'Большее значение'
            texts.append(label + ' у «' + names[winner] + '»; разница — ' + number_text(abs(a - b)) + ' ' + left['unit'] + '.')
    remember(references)
    return result('answer' if answered == 2 else 'partial', '\n'.join(texts), evidence)

````

## ai_core/site_search_catalog.py

SHA-256: 692a61e4a61a16f1056af882412706e1e7f9937cf900f02a00d540a51dc79996

````python
"""Поиск характеристик конкретного товара в доверенно размеченном каталоге.

Это извлечение опубликованных фактов, а не генерация новых характеристик.
Разметку назначает серверный импортёр; обычный текст не включает этот режим.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from .site_search_measurements import NUMERIC_FACETS, SCOPES, requested_scope, dimension_facet, extract_measurements, conflicts, scalar_value
from .site_search_comparison import compare_catalog

FACETS = {'material', 'composition', 'dimensions', 'volume', 'mass', 'capacity', 'kit', 'price', 'stock'} | NUMERIC_FACETS
MATERIAL = r'металл\w*|стекл\w*|фарфор\w*|керами\w*|глин\w*|бамбук\w*|грушев\w*|дерев\w*|пластик\w*|силикон\w*|сталь\w*|сталь|стал[ьи]'
NUMBER = r'\d+(?:[.,]\d+)?'
DIMENSIONS = re.compile(rf'({NUMBER}(?:\s*[xх×]\s*{NUMBER}){{1,2}})\s*(мм|см|м)\b', re.I)
SKIP = ('из чего сделан сделана изготовлен изготовлена состоит состав материал дерево размер размеры '
        'габариты объем вместимость масса вес рассчитан рассчитана человек комплект комплектация входят '
        'входит свеча свечи свечка свечки продается продаются сколько стоит стоимость цена сейчас сегодня '
        'наличие магазин этот эта это он она оно него нее ней там тогда тот другой вариант товар продукция '
        'пожалуйста уточнить уточняю имеется вместе включен включены есть ли').split()
SKIP += ('сам саму самого самой без изделие корпус упаковка упаковок коробка коробок транспортная тара нетто брутто '
         'длина ширина высота глубина диаметр какова каковы назови весит').split()


@dataclass
class Product:
    key: str
    heading: str
    source: dict
    passages: list
    words: frozenset


def validate_catalog(source):
    """Ограничиваем доверенную разметку: ключ и точный заголовок раздела."""
    items = source.get('catalog_items', [])
    if not isinstance(items, list) or len(items) > 500:
        raise ValueError('catalog_metadata_invalid')
    headings = set()
    for item in items:
        if (not isinstance(item, dict) or set(item) != {'key', 'heading'}
                or not isinstance(item['key'], str) or not 1 <= len(item['key']) <= 160
                or not isinstance(item['heading'], str) or not 1 <= len(item['heading']) <= 400
                or item['heading'] in headings):
            raise ValueError('catalog_item_invalid')
        headings.add(item['heading'])


def build_catalog(passages, terms):
    """Группируем все абзацы одной позиции один раз при публикации/рестарте."""
    grouped = {}
    metadata = {}
    for passage in passages:
        source = passage.source
        if source['id'] not in metadata:
            metadata[source['id']] = {item['heading']: item['key'] for item in source.get('catalog_items', [])}
        key = metadata[source['id']].get(passage.heading)
        if key:
            identity = (source['id'], key, passage.heading)
            if identity not in grouped:
                grouped[identity] = Product(key, passage.heading, source, [], frozenset(identity_terms(passage.heading, terms)))
            grouped[identity].passages.append(passage)
    return list(grouped.values())


def identity_terms(text, terms):
    # Название чайной доски не равно сокращению «чай в банке».
    # Термины оборудования нормализуются до склонения, до общего стеммера.
    text = re.sub(r'\bча\s+бан[ьи]\b', 'чабань', text, flags=re.I)
    text = re.sub(r'\bчах(?:ай|ая|аю|аем|ае|аи|аев)\b', 'чахай', text, flags=re.I)
    return terms(text)


def facet_of(question):
    text = question.casefold().replace('ё', 'е')
    numeric = dimension_facet(text)
    if numeric:
        return numeric
    for pattern, facet in [('тяжелее|легче', 'mass'), ('вместительнее', 'volume'), ('длиннее|короче', 'length'),
                           ('шире', 'width'), ('выше|ниже', 'height')]:
        if re.search(r'\b(?:' + pattern + r')\b', text):
            return facet
    if re.search(r'состав|состоит|ингредиент', text):
        return 'composition'
    if re.search(r'материал|из чего|из како\w* дерева', text):
        return 'material'
    if re.search(r'размер|габарит', text):
        return 'dimensions'
    if re.search(r'объем|вместимост', text):
        return 'volume'
    if re.search(r'сколько.*весит|\bвес\b|масс\w*', text):
        return 'mass'
    if re.search(r'сколько.*человек|сколько.*персон', text):
        return 'capacity'
    if re.search(r'комплект|свеч', text):
        return 'kit'
    if re.search(r'цен[ауы]|стоимост|стоит|стоить', text):
        return 'price'
    if re.search(r'наличи|остат[ок]', text):
        return 'stock'
    return None


def dimensions(text):
    # Сравниваем значения одной характеристики в миллиметрах.
    # Порядок осей сохраняется: длину нельзя молча поменять местами с шириной.
    scales = {'мм': Decimal(1), 'см': Decimal(10), 'м': Decimal(1000)}
    return [tuple(Decimal(number.replace(',', '.')) * scales[match[1].lower()]
                  for number in re.findall(NUMBER, match[0])) for match in DIMENSIONS.findall(text)]


def extract(product, facet, sentences):
    """Возвращает короткие дословные фрагменты и обнаруженный конфликт."""
    heading = product.heading
    bodies = list(dict.fromkeys(p.text for p in product.passages))
    texts = [heading, *bodies]
    if facet == 'dimensions':
        values = [value for text in texts for value in dimensions(text)]
        if any(a[:min(len(a), len(b))] != b[:min(len(a), len(b))] for a in values for b in values):
            return [], True
        quotes = [(match.group(), text) for text in texts for match in DIMENSIONS.finditer(text)]
        if quotes:
            # Если размеры согласованы, достаточно самой полной записи.
            quotes = [max(quotes, key=lambda pair: len(re.findall(NUMBER, pair[0])))]
    elif facet in {'volume', 'mass'}:
        units = r'мл|л|литр\w*' if facet == 'volume' else r'кг|г|гр|грамм\w*'
        quotes = [(match.group(), text) for text in texts
                  for match in re.finditer(rf'\b{NUMBER}\s*(?:{units})\b', text, re.I)]
    elif facet == 'capacity':
        quotes = [(match.group(), text) for text in bodies
                  for match in re.finditer(r'на\s+\d+(?:\s*[-–]\s*\d+)?\s+(?:человек|персон\w*)', text, re.I)]
    elif facet == 'material':
        quotes = []
        for text in bodies or [heading]:
            for sentence in sentences(text):
                # Сохраняем целый смысловой оборот, а спорные размеры не добавляем
                # к независимому вопросу о дереве или металле.
                clause = sentence.split(',')[0]
                if re.search(MATERIAL, clause, re.I):
                    quotes.append((clause.rstrip('.'), sentence))
        if not quotes and re.search(MATERIAL, heading, re.I):
            quotes = [(match.group(), heading) for match in re.finditer(MATERIAL, heading, re.I)]
    elif facet == 'composition':
        # В каталоге состав бывает записан без метки «Состав:».
        quotes = [(sentence, sentence) for text in bodies for sentence in sentences(text)
                  if re.search(r'состав|ингредиент|м[её]д|чай|кофе|сахар|молок|орех|фрукт|ягод', sentence, re.I)
                  and 'отсутствует' not in sentence.casefold()]
    elif facet == 'kit':
        quotes = [(sentence, sentence) for text in bodies for sentence in sentences(text)
                  if re.search(r'комплект|свеч', sentence, re.I)]
    else:
        # Архивное описание не доказывает текущую цену и доступность товара.
        return [], False
    return list(dict.fromkeys(quotes)), False


def saved_context(context, products, identifiers):
    """Память содержит ключи доступных позиций, без копии ответа и переписки."""
    keys = context.get('catalog_keys')
    facet = context.get('catalog_facet')
    allowed = {product.key for product in products if product.source['id'] in identifiers}
    comparing = context.get('catalog_compare') is True
    if (not isinstance(keys, list) or not 1 <= len(keys) <= 64 or (facet not in FACETS and not (comparing and facet is None))
            or any(not isinstance(key, str) or key not in allowed for key in keys)):
        return {}
    saved = {'catalog_keys': list(dict.fromkeys(keys)), 'catalog_facet': facet}
    measure_scope = context.get('catalog_measure_scope', 'product')
    if measure_scope in SCOPES:
        saved['catalog_measure_scope'] = measure_scope
    targets = context.get('catalog_targets')
    if (comparing and isinstance(targets, list) and len(targets) == 2
            and all(isinstance(part, list) and part and all(isinstance(key, str) and key in keys for key in part) for part in targets)):
        saved.update(catalog_compare=True, catalog_targets=targets)
    return saved


def search_catalog(index, question, allowed, selected, day, session, sessions, scope, *, selection=None, facet_override=None, scope_override=None):
    from .site_search import terms, affinity, NO_DATA
    from .site_search_structure import active, ISSUE_TEXT
    from .site_search_reasoning import authority

    products = [product for product in index.catalog_products if product.source['id'] in allowed
                and (not selected or product.source['id'] == selected)
                and all(active(p.structure or {}, day) and index._usable_passage(p, allowed, day) for p in product.passages)]
    if not products:
        return None
    facet = facet_of(question)
    if selection is None:
        def solve(key, property_name, object_scope):
            local = {}
            answer = search_catalog(index, '', allowed, selected, day, None, local, scope,
                                    selection=key, facet_override=property_name, scope_override=object_scope)
            return answer, local.get(scope)
        compared = compare_catalog(index, question, products, session, sessions, scope, facet,
                                   lambda text: identity_terms(text, terms), set(terms(' '.join(SKIP))), solve)
        if compared is not None:
            return compared
    previous = (session or {}).get('catalog_keys', [])
    words = set(identity_terms(question, terms)) - set(terms(' '.join(SKIP)))
    object_scope = scope_override or requested_scope(question, facet)
    if facet is None and previous and object_scope:
        facet = session.get('catalog_facet')
        object_scope = scope_override or requested_scope(question, facet)
    context_used = False
    candidates = [product for product in products if words and
                  all(any(affinity(word, token) for token in product.words) for word in words)]
    if selection is not None:
        candidates = [product for product in products if product.key == selection]
        facet = facet_override
    if previous and not words and facet:
        candidates = [product for product in products if product.key in previous]
        context_used = True
    elif previous and candidates and facet is None:
        # Короткий ответ на уточнение: «с миндалём». Другой явно названный
        # товар ищется заново и не наследует незаданный вопрос о составе.
        narrowed = [product for product in candidates if product.key in previous]
        if narrowed:
            candidates = narrowed
            facet = session.get('catalog_facet')
            context_used = True
        elif re.match(r'^\s*а\s+', question, re.I):
            facet = session.get('catalog_facet')
            context_used = True
    if not facet or not candidates:
        return None
    object_scope = object_scope or ((session or {}).get('catalog_measure_scope', 'product') if context_used else 'product')
    keys = list(dict.fromkeys(product.key for product in candidates))
    # Размеченный каталог не отменяет более авторитетный документ о том же
    # товаре. Сравниваем опубликованные характеристики, не выбираем удобную
    # версию. Обычные правила/уроки без совпадения предмета сюда не попадают.
    if len(keys) == 1:
        base = candidates[0]
        identity = words if len(words) >= 2 else set(base.words)
        identity -= set(terms(' '.join(SKIP) + ' г гр кг мл л мм см'))
        identity = {word for word in identity if not re.fullmatch(r'[\d.,]+', word)}
        external = {}
        if identity:
            for passage in index.passages:
                source = passage.source
                if (source['id'] not in allowed or source.get('catalog_items')
                        or (selected and source['id'] != selected)
                        or authority(source) <= max(authority(product.source) for product in candidates)
                        or not active(passage.structure or {}, day) or not index._usable_passage(passage, allowed, day)):
                    continue
                vocabulary = set(passage.words) | set(passage.title_words)
                if all(any(affinity(word, token) for token in vocabulary) for word in identity):
                    pair = (source['id'], passage.heading)
                    external.setdefault(pair, Product(base.key, passage.heading, source, [], frozenset())).passages.append(passage)
        candidates.extend(external.values())
    identifiers = list(dict.fromkeys(product.source['id'] for product in candidates))

    def result(status, text, evidence=None):
        return {'status': status, 'text': text, 'evidence': evidence or [], 'links': [], 'context_used': context_used}

    def remember():
        index._remember(sessions, scope, identifiers, words or (session or {}).get('terms', []))
        if len(keys) <= 64:
            sessions[scope].update(catalog_keys=keys, catalog_facet=facet, catalog_measure_scope=object_scope)

    if len(keys) > 1:
        remember()
        names = list(dict.fromkeys(product.heading for product in candidates))
        text = 'Уточните товар: ' + '; '.join('«' + name + '»' for name in names[:4])
        text += '? ' if len(names) <= 4 else '. Есть и другие варианты — напишите полное название.'
        return result('clarify', text.strip())
    if any(not product.source.get('complete', True) or any((p.structure or {}).get('issue') for p in product.passages)
           for product in candidates):
        return result('unavailable', ISSUE_TEXT)
    numeric_values = []
    if facet in NUMERIC_FACETS:
        extracted = []
        for product in candidates:
            quotes, conflict, values = extract_measurements(product, facet, object_scope)
            extracted.append((product, quotes, conflict))
            numeric_values.extend(values)
    else:
        extracted = [(product, *extract(product, facet, index._sentences)) for product in candidates]
    # Повтор одной строки не создаёт неоднозначности. Различающиеся факты
    # одного ключа не выбираем голосованием или по порядку файлов.
    facts = {tuple(sorted(text.casefold().rstrip('. ') for text, quote in quotes))
             for product, quotes, conflict in extracted if quotes}
    if facet in NUMERIC_FACETS:
        different = conflicts(numeric_values)
    elif facet == 'dimensions':
        values = [value for product, quotes, conflict in extracted for text, quote in quotes for value in dimensions(text)]
        different = any(a[:min(len(a), len(b))] != b[:min(len(a), len(b))] for a in values for b in values)
    else:
        different = len(facts) > 1
    if any(conflict for product, quotes, conflict in extracted) or different:
        return result('conflict', 'В доступных материалах есть противоречие в характеристиках товара. Уточните данные у администратора.')
    usable = [(product, quotes) for product, quotes, conflict in extracted if quotes]
    if not usable:
        return result('not_found', NO_DATA)
    product, quotes = max(usable, key=lambda pair: authority(pair[0].source))
    text = '; '.join(dict.fromkeys(text.rstrip('. ') for text, quote in quotes)) + '.'
    if len(text) > 900:
        return result('clarify', 'Уточните, какая именно характеристика товара нужна.')
    remember()
    evidence = [{'id': product.source['id'], 'revision': product.source['revision'], 'heading': product.heading, 'quote': quote}
                for quote in dict.fromkeys(quote for text, quote in quotes)]
    answer = result('answer', text, evidence)
    if selection is not None:
        # Внутреннее числовое значение нужно только сравнению. В обычный
        # ответ API и пользовательский текст оно отдельным полем не попадает.
        answer['_numeric'] = scalar_value(numeric_values, facet)
    return answer

````

## ai_core/site_search.py

SHA-256: c72cb9b8c9243dd236bb547fca6fd6f323735802cf91e0e9320ed9fd3164181a

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

    def rebuild_lookup(self):
        """Обратный указатель: слово → номера фрагментов; без изменения score."""
        self.postings = {}
        self.passage_positions = {}
        self.condition_positions = set()
        self.passage_operations = {}
        self.token_matches = {}
        self.catalog_products = build_catalog(self.passages, terms)
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
        if result['status'] in {'not_found','unavailable','conflict','processing'} or (result['status']=='clarify' and sessions.get(scope) is previous and not result.get('context_used')):
            sessions.pop(scope,None)
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
        return {**result,'evidence':evidence,'links':links}

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
        catalog = search_catalog(self, question, allowed, selected_source, day, session, sessions, scope)
        if catalog is not None:
            return catalog
        focused_question = lookup_question(question)
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
        generic = not raw_words or all(word in FOLLOWUP for word in raw_words)
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
        words = list(dict.fromkeys(current_words + (session["terms"] if context_used and not topic_followup else [])))
        named = [term for name in explicit_names(focused_question) if name.casefold() not in STOP for term in terms(name)][:16]
        if re.match(r'^а\s+(?:если|при)\b',normal(question)) and not named and not context_used and not selected_source:
            return {'status':'clarify','text':'Уточните, к какому материалу или правилу относится это условие.','links':[],'evidence':[]}
        numbers = re.findall(r"\d+(?:[.,]\d+)*", focused_question)[:16]
        candidates = [item for item in self.passages if item.source["id"] in allowed and (not selected_source or item.source["id"] == selected_source)
                      and active(item.structure or {}, day) and self._usable_passage(item,allowed,day)]
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
                candidates = [item for item in candidates if re.search(r"\b(?:день|дня|дней|дни|час\w*|минут\w*|недел\w*|месяц\w*|суток|срок\w*)\b", normal(item.text))]
        # Обзор объясняет весь порядок вместе с условиями. Он не требует
        # выяснять частные обстоятельства пользователя, как конкретный случай.
        conditional = None if want_overview else self._conditional_answer(question, candidates, current_words, named, facet, scope, sessions,
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

## ai_core/site_search_runtime.py

SHA-256: 33f5645f8a8cccd4b739dacf8ba2463c0b465fcc7b6af8d935e9fe1415e3f153

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

CONTEXT_TTL = 30 * 24 * 60 * 60
LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def engine_version() -> str:
    digest = hashlib.sha256()
    for name in ("site_search.py", "site_search_language.py", "site_search_reasoning.py", "site_search_conditions.py", "site_search_structure.py", "site_search_links.py", "site_search_catalog.py", "site_search_measurements.py", "site_search_comparison.py"):
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


class DurableSearch:
    def __init__(self, origins: list[str], path: Path, clock=time.time, extraction_version: str | None = None):
        self.lock = threading.RLock()
        self.index = ClosedSiteIndex(origins, clock=clock, session_ttl=CONTEXT_TTL)
        self.store = SearchStore(path)
        self.restored = False
        self.restore_ms = 0.0
        self.rebuilt = False
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
        return saved

    def begin(self, snapshot: str):
        with self.lock:
            if self.index.snapshot == snapshot and self.index.state in {"ready", "processing"}:
                return None
            generation = self.store.begin(snapshot)
            self.index.begin(snapshot)
            self.index.generation = generation
            self.restored = False
            return generation

    def publish(self, sources: list[dict], generation: int, aliases: dict | None = None):
        with self.lock:
            if generation != self.index.generation:
                return False
            try:
                self.index.publish(sources, generation, aliases)
                if not self.store.save_index(self.index, engine_version(), self.extraction_version):
                    raise ValueError("index_save_superseded")
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

    def status(self):
        with self.lock:
            return {"state": self.index.state, "sources": len(self.index.sources), "passages": len(self.index.passages),
                    "snapshot": self.index.snapshot, "restored_from_disk": self.restored,
                    "restore_ms": self.restore_ms, "rebuilt_from_text": self.rebuilt, "context_count": len(self.index.sessions)}

````

## tests/test_site_search_comparison.py

SHA-256: f31d1ad10ae58458e49e8ad8fce767651c99b345eda30e6239f82eecaac11c47

````python
"""Смысловые проверки сравнения: единицы, принадлежность чисел, память и доступ."""
import tempfile
import unittest
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch

ORIGIN = 'http://127.0.0.1:3040'
USER = Principal('tester', 'staff')


def source(identifier, name, text, key=None, **extra):
    return {'id': identifier, 'revision': 'v1', 'title': 'Учебная карточка ' + name, 'kind': 'document',
            'url': ORIGIN + '/materials/' + identifier, 'access': {'scope': 'workspace'},
            'catalog_items': [{'key': key or identifier, 'heading': name}],
            'sections': [{'heading': name, 'text': text}], **extra}


def pair():
    return [source('alpha', 'Прибор Альфа', 'Размеры изделия: 20×10×4 см. Размеры упаковки: 25×15×8 см. '
                   'Масса нетто: 500 г. Масса брутто: 900 г. Объём: 500 мл. Диаметр корпуса: 5 см.'),
            source('beta', 'Прибор Бета', 'Размеры изделия: 220×110×50 мм. Размеры упаковки: 30×20×10 см. '
                   'Масса без упаковки: 0,8 кг. Масса с упаковкой: 1,1 кг. Объём: 0,7 л. Диаметр корпуса: 50 мм.')]


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.index = ClosedSiteIndex([ORIGIN])
        self.publish(pair())

    def publish(self, sources):
        self.index.publish(sources, self.index.begin('new-snapshot'))

    def ask(self, question, actor=USER, chat='chat', **extra):
        return self.index.search(question, actor, chat, **extra)

    def test_compares_two_objects_instead_of_conflicting_them(self):
        answer = self.ask('Сравни размеры прибора Альфа и прибора Бета')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('20×10×4 см', answer['text'])
        self.assertIn('220×110×50 мм', answer['text'])
        self.assertNotIn('25×15×8', answer['text'])
        self.assertEqual({item['id'] for item in answer['evidence']}, {'alpha', 'beta'})

    def test_scope_followup_preserves_both_products(self):
        self.ask('Сравни размеры прибора Альфа и прибора Бета')
        answer = self.ask('А коробок?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('25×15×8 см', answer['text'])
        self.assertIn('30×20×10 см', answer['text'])

    def test_unit_conversion_for_heavier_without_packaging(self):
        answer = self.ask('Какой тяжелее без упаковки: прибор Альфа или прибор Бета?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('300 г', answer['text'])
        self.assertIn('Большее значение у «Прибор Бета»', answer['text'])
        self.assertNotIn('1,1 кг', answer['text'])

    def test_lightest_winner_is_reversed(self):
        answer = self.ask('Какой легче без упаковки: прибор Альфа или прибор Бета?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('Меньшее значение у «Прибор Альфа»', answer['text'])

    def test_packaging_mass_is_not_gross_mass(self):
        self.assertEqual(self.ask('Какая масса упаковки прибора Альфа?')['status'], 'not_found')
        answer = self.ask('Какова масса прибора Альфа с упаковкой?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('900 г', answer['text'])

    def test_equivalent_diameters_compare_as_equal(self):
        answer = self.ask('Сравни диаметры прибора Альфа и прибора Бета')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('одинаковые', answer['text'])

    def test_partial_answer_has_evidence_only_for_known_fact(self):
        self.publish([pair()[0], source('empty', 'Прибор Гамма', 'Материал: стекло.')])
        answer = self.ask('Сравни объем прибора Альфа и прибора Гамма')
        self.assertEqual(answer['status'], 'partial')
        self.assertIn('не указан', answer['text'])
        self.assertEqual({item['id'] for item in answer['evidence']}, {'alpha'})
        self.assertEqual(len(answer['links']), 1)

    def test_unknown_product_is_not_silently_omitted(self):
        answer = self.ask('Сравни объем прибора Альфа и прибора Ненайденный')
        self.assertEqual(answer['status'], 'not_found')
        self.assertEqual(answer['evidence'], [])

    def test_missing_property_is_clarified_then_remembered(self):
        self.assertEqual(self.ask('Сравни прибор Альфа и прибор Бета')['status'], 'clarify')
        answer = self.ask('По объему')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('500 мл', answer['text'])
        self.assertIn('0,7 л', answer['text'])

    def test_same_product_twice_does_not_create_fake_comparison(self):
        self.assertEqual(self.ask('Сравни объем прибора Альфа и прибора Альфа')['status'], 'clarify')

    def test_hidden_product_cannot_supply_comparison(self):
        rows = pair()
        rows[1]['access'] = {'scope': 'admin'}
        self.publish(rows)
        answer = self.ask('Сравни объем прибора Альфа и прибора Бета')
        self.assertEqual(answer['status'], 'not_found')
        self.assertNotIn('0,7', str(answer))

    def test_new_topic_clears_comparison(self):
        self.ask('Сравни объем прибора Альфа и прибора Бета')
        self.ask('Какие размеры прибора Альфа?')
        answer = self.ask('А упаковки?')
        self.assertEqual(answer['status'], 'answer')
        self.assertNotIn('Бета', answer['text'])
        self.assertIn('25×15×8', answer['text'])

    def test_item_size_does_not_use_only_available_package_size(self):
        self.publish([source('empty', 'Прибор Гамма', 'Размеры упаковки: 30×20×10 см.')])
        self.assertEqual(self.ask('Какие размеры прибора Гамма?')['status'], 'not_found')

    def test_explicit_box_of_item_is_still_box(self):
        self.publish([source('one', 'Прибор Альфа', 'Размеры коробки товара: 30×20×10 см.')])
        self.assertEqual(self.ask('Какие размеры прибора Альфа?')['status'], 'not_found')
        self.assertIn('30×20×10', self.ask('Какие размеры коробки прибора Альфа?')['text'])

    def test_package_difference_does_not_conflict_with_object(self):
        self.publish([pair()[0], source('copy', 'Прибор Альфа', 'Размеры изделия: 200×100×40 мм. Размеры коробки: 30×20×10 см.', key='alpha')])
        self.assertEqual(self.ask('Какие размеры прибора Альфа?')['status'], 'answer')
        self.assertEqual(self.ask('Какие размеры коробки прибора Альфа?')['status'], 'conflict')

    def test_component_length_does_not_become_item_length(self):
        self.publish([source('one', 'Прибор Альфа', 'Длина ручки: 12 см. Длина изделия: 30 см.')])
        answer = self.ask('Какая длина прибора Альфа?')
        self.assertIn('30 см', answer['text'])
        self.assertNotIn('12 см', answer['text'])

    def test_food_nutrition_is_not_weight(self):
        self.publish([source('food', 'Чай Роса', 'Масса нетто: 200 г. Пищевая ценность на 100 г: белки 5 г, жиры 3 г.')])
        self.assertEqual(self.ask('Сколько весит чай Роса?')['text'], '200 г.')

    def test_storage_capacity_is_not_container_mass(self):
        self.publish([source('can', 'Банка Роса 100 г', 'Банка для хранения чая.')])
        self.assertEqual(self.ask('Сколько весит банка Роса?')['status'], 'not_found')

    def test_numeric_conflict_inside_one_product(self):
        self.publish([source('one', 'Прибор Альфа 500 мл', 'Объём: 750 мл.')])
        self.assertEqual(self.ask('Какой объем прибора Альфа?')['status'], 'conflict')

    def test_equivalent_volume_inside_one_product(self):
        self.publish([source('one', 'Прибор Альфа 500 мл', 'Объём: 0,5 л.')])
        self.assertEqual(self.ask('Какой объем прибора Альфа?')['status'], 'answer')

    def test_range_is_not_replaced_with_endpoint(self):
        self.publish([source('one', 'Прибор Альфа', 'Длина изделия: 10–12 см.')])
        self.assertEqual(self.ask('Какая длина прибора Альфа?')['status'], 'not_found')

    def test_unrelated_already_word_does_not_activate_comparison(self):
        rule = source('rule', 'Оплаченный заказ', 'Если заказ оплачен, обратитесь к оператору.')
        del rule['catalog_items']
        self.publish(pair() + [rule])
        answer = self.ask('Что делать, если заказ уже оплачен?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('оператору', answer['text'])

    def test_restart_keeps_pair_and_pending_property_but_not_other_user(self):
        with tempfile.TemporaryDirectory(prefix='vates-comparison-') as folder:
            path = Path(folder) / 'search.sqlite'
            runtime = DurableSearch([ORIGIN], path)
            runtime.publish(pair(), runtime.begin('start'))
            ask = lambda question, chat='chat', actor=USER, revisions=None: runtime.query(question, actor, chat, None, revisions if revisions is not None else {'alpha': 'v1', 'beta': 'v1'})
            ask('Сравни размеры прибора Альфа и прибора Бета')
            ask('Сравни прибор Альфа и прибор Бета', chat='pending')
            runtime = DurableSearch([ORIGIN], path)
            self.assertTrue(runtime.restored)
            self.assertIn('25×15×8', ask('А коробок?')['text'])
            self.assertIn('0,7 л', ask('По объему', chat='pending')['text'])
            self.assertEqual(ask('А коробок?', actor=Principal('other', 'staff'))['status'], 'clarify')
            self.assertNotEqual(ask('А коробок?', revisions={'alpha': 'v1'})['status'], 'answer')


if __name__ == '__main__':
    unittest.main()

````

## evaluation/closed_search/comparison_20261030.json

SHA-256: 3278eaa3ff3a1a4ffe4c72356573f8272af7d5cfb2b0bae53582d9135c76e035

````json
{
  "note": "Новые вопросы зафиксированы до изменения алгоритма. Реальные позиции взяты из архивных CSV. Источник cmp_measurements — отдельные вымышленные карточки для проверки размеров, не сведения компании. Набор holdout запускается после реализации и не используется в первом прогоне.",
  "documents": [
    {"id":"cmp_measurements","name":"Учебные_товары_и_упаковки.md","rows":[
      {"key":"fixture:thermos-north","heading":"Термос Север","text":"Материал: нержавеющая сталь. Объём термоса: 500 мл. Размеры товара: 20×8×8 см. Размеры упаковки: 24×10×10 см. Масса без упаковки: 300 г. Масса с упаковкой: 420 г. Диаметр корпуса: 8 см."},
      {"key":"fixture:thermos-south","heading":"Термос Юг","text":"Материал: нержавеющая сталь. Объём термоса: 0,75 л. Размеры товара: 220×80×80 мм. Размеры упаковки: 26×11×11 см. Масса нетто: 0,35 кг. Масса брутто: 0,47 кг. Диаметр корпуса: 80 мм."},
      {"key":"fixture:glass-meadow","heading":"Стакан Луг","text":"Размеры упаковки: 9×9×14 см. Размеры самого стакана не указаны. Материал: стекло."},
      {"key":"fixture:cup-peak","heading":"Кружка Пик","text":"Высота кружки: 9 см. Высота упаковки: 13 см. Диаметр кружки: 7 см. Диаметр упаковки: 11 см. Объём кружки: 300 мл."},
      {"key":"fixture:board-forest","heading":"Доска Лес","text":"Размеры изделия: 540×340 мм. Размеры коробки: 600×400×80 мм. Материал: бамбук."},
      {"key":"fixture:scale-stone","heading":"Чай Камень","text":"Масса нетто: 200 г. Пищевая ценность на 100 г: белки 3 г, жиры 4 г, углеводы 20 г. Размеры упаковки: 10×8×4 см."}
    ]}
  ],
  "cases": [
    {"id":"compare-01","chat":"real-material","q":"Сравни материал подставок Хайвэй и Звездочка","status":"answer","has":["Хайвэй","Металлическая","Звездочка","Стеклянная"]},
    {"id":"compare-02","chat":"real-volume","q":"Сравни объем чахаев Дао и Феникс","status":"answer","has":["Дао","190 мл","Феникс","140 мл"]},
    {"id":"compare-03","chat":"real-composition","q":"Сравни состав медового мусса Dolche Vita с миндалем и медового мусса Dolche Vita с клюквой","status":"answer","has":["миндаля","клюква"]},
    {"id":"compare-04","chat":"real-conflict","q":"Сравни размеры Ча Бань Груша и Ча Бань Точка","status":"conflict","has":["противореч"],"absent":["540"]},
    {"id":"compare-05","chat":"scope","q":"Какие размеры самого термоса Север?","status":"answer","has":["20×8×8 см"],"absent":["24×10×10"]},
    {"id":"compare-06","chat":"scope","q":"А упаковки?","status":"answer","has":["24×10×10 см"],"absent":["20×8×8"]},
    {"id":"compare-07","chat":"both","q":"Сравни размеры термоса Север и термоса Юг","status":"answer","has":["20×8×8 см","220×80×80 мм"],"absent":["24×10×10","26×11×11"]},
    {"id":"compare-08","chat":"mass","q":"Какой тяжелее без упаковки: термос Север или термос Юг?","status":"answer","has":["300 г","0,35 кг","Юг","50 г"],"absent":["420","0,47"]},
    {"id":"compare-09","chat":"mass","q":"А с упаковкой?","status":"answer","has":["420 г","0,47 кг"],"absent":["300 г","0,35 кг"]},
    {"id":"compare-10","chat":"missing","q":"Какие размеры стакана Луг?","status":"not_found","absent":["9×9×14"]},
    {"id":"compare-11","chat":"partial","q":"Сравни объем термоса Север и стакана Луг","status":"partial","has":["500 мл","Луг","не указан"]},
    {"id":"compare-12","chat":"aspect","q":"Сравни термос Север и термос Юг","status":"clarify","has":["характеристик"]},
    {"id":"compare-13","chat":"aspect","q":"По объему","status":"answer","has":["500 мл","0,75 л"]}
  ],
  "holdout": [
    {"id":"new-01","chat":"order","q":"Сопоставь по объему чахай Феникс и чахай Дао","status":"answer","has":["140 мл","190 мл"]},
    {"id":"new-02","chat":"larger","q":"Что вместительнее: чахай Феникс или чахай Дао?","status":"answer","has":["Дао","50 мл"]},
    {"id":"new-03","chat":"package-pair","q":"Сравни габариты коробок термоса Юг и термоса Север","status":"answer","has":["26×11×11 см","24×10×10 см"],"absent":["220×80×80","20×8×8"]},
    {"id":"new-04","chat":"height","q":"Какова высота кружки Пик?","status":"answer","has":["9 см"],"absent":["13 см"]},
    {"id":"new-05","chat":"height","q":"А у коробки?","status":"answer","has":["13 см"],"absent":["9 см"]},
    {"id":"new-06","chat":"equal","q":"Сравни диаметры термосов Север и Юг","status":"answer","has":["8 см","80 мм","одинаков"]},
    {"id":"new-07","chat":"unknown-package","q":"Каковы габариты упаковки подставки Хайвэй?","status":"not_found"},
    {"id":"new-08","chat":"missing-item","q":"Сравни объем термоса Север и термоса Ненайденный","status":"not_found"},
    {"id":"new-09","chat":"food","q":"Сколько весит чай Камень без упаковки?","status":"answer","has":["200 г"],"absent":["100 г","3 г","4 г","20 г"]},
    {"id":"new-10","chat":"plain-board","q":"Назови габариты доски Лес","status":"answer","has":["540×340 мм"],"absent":["600×400×80"]},
    {"id":"new-11","chat":"plain-board","q":"А транспортной коробки?","status":"answer","has":["600×400×80 мм"],"absent":["540×340"]},
    {"id":"new-12","chat":"incomplete-pair","q":"Сравни состав медового мусса Dolche Vita и медового мусса Dolche Vita с клюквой","status":"clarify"}
  ]
}

````

## evaluation/closed_search/comparison_probe.py

SHA-256: bcf0448e8874803f4d67897f512226be3ce7212c00536be271bbeb8c5a338085

````python
"""Новые вопросы о сравнении и упаковке; реальные данные и явно учебные карточки."""
import argparse
import hashlib
import json
import time
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal
from evaluation.closed_search.catalog_probe import catalog_sources, LAB, ORIGIN

SPEC = Path(__file__).with_name('comparison_20261030.json')


def fixture_sources(spec):
    return [{'id': 'document:' + doc['id'], 'revision': hashlib.sha256(json.dumps(doc, ensure_ascii=False).encode()).hexdigest(),
             'kind': 'document', 'title': doc['name'], 'authority': 'reference',
             'url': ORIGIN + '/tasks?tab=docs&documentId=' + doc['id'],
             'access': {'scope': 'restricted', 'users': ['closed_lab_b']},
             'catalog_items': [{'key': row['key'], 'heading': row['heading']} for row in doc['rows']],
             'sections': [{'heading': row['heading'], 'text': row['text']} for row in doc['rows']]}
            for doc in spec['documents']]


def check(case, result):
    errors = []
    if result['status'] != case['status']:
        errors.append('status:' + result['status'])
    for phrase in case.get('has', []):
        if phrase.casefold() not in result['text'].casefold():
            errors.append('missing:' + phrase)
    for phrase in case.get('absent', []):
        if phrase.casefold() in result['text'].casefold():
            errors.append('unexpected:' + phrase)
    answered = result['status'] in {'answer', 'partial'}
    if answered and (not result['links'] or not result['evidence']):
        errors.append('missing-evidence')
    if not answered and (result['links'] or result['evidence']):
        errors.append('unexpected-evidence')
    if len(result['text']) > 1200 or '**' in result['text']:
        errors.append('readability')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--holdout', action='store_true')
    parser.add_argument('--label', default='comparison')
    args = parser.parse_args()
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    index = ClosedSiteIndex([ORIGIN])
    index.publish(catalog_sources() + fixture_sources(spec), index.begin('comparison'))
    rows = []
    for case in spec['holdout' if args.holdout else 'cases']:
        start = time.perf_counter()
        result = index.search(case['q'], Principal('closed_lab_b', 'staff'), case['chat'])
        errors = check(case, result)
        rows.append({**case, 'result': result, 'errors': errors, 'pass': not errors,
                     'ms': round((time.perf_counter() - start) * 1000, 2)})
    report = {'spec_sha256': hashlib.sha256(SPEC.read_bytes()).hexdigest(), 'holdout': args.holdout,
              'total': len(rows), 'passed': sum(row['pass'] for row in rows), 'cases': rows}
    output = LAB / (args.label + '-' + str(time.time_ns()) + '.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(output), 'total': len(rows), 'passed': report['passed'],
                      'failures': [{'id': row['id'], 'errors': row['errors'], 'text': row['result']['text']}
                                   for row in rows if not row['pass']]}, ensure_ascii=False))
    return 0 if report['total'] == report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        raise SystemExit('Comparison probe failed: ' + type(error).__name__) from error

````

## evaluation/closed_search/comparison_http.cjs

SHA-256: 13ef3b737a98ed1a082e497846c8869ade3382aa4aa38259fce48500b4373423

````javascript
/* Сквозная проверка сравнений только в изолированной копии сайта на 3040. */
const { fs, path, lab, login, storage, call } = require('./lab_client.cjs');
const { waitSnapshot } = require('./scan_http.cjs');
const { createHash } = require('node:crypto');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

async function prepare(spec, cookie) {
    const directory = path.join(lab, 'comparison-assets');
    await fs.mkdir(directory, { recursive: true });
    const catalog = await storage('tea_hub_urgent_files_v1', cookie);
    const manifest = [];
    for (const doc of spec.documents) {
        const text = 'Учебные данные для проверки ВатэсAi. Не являются характеристиками товаров компании.\n\n'
            + doc.rows.map(row => '# ' + row.heading + '\n' + row.text).join('\n\n');
        const bytes = Buffer.from(text, 'utf8');
        const data = 'data:text/markdown;base64,' + bytes.toString('base64');
        const record = { id: doc.id, name: doc.name, hasSeparateData: true,
            section: 'Учебная проверка сравнения товаров', size: bytes.length + ' B', date: 'Учебные данные 30.10.2026',
            searchAuthority: 'reference', accessPolicy: { version: 1, visibility: 'restricted', userIds: ['closed_lab_b'] },
            searchCatalog: doc.rows.map(row => ({ key: row.key, heading: row.heading })) };
        const existing = catalog.find(item => item.id === doc.id);
        if (existing) {
            if (JSON.stringify(existing) !== JSON.stringify(record) || await storage('file_data_' + doc.id, cookie) !== data) {
                throw new Error('Existing comparison fixture differs');
            }
        } else {
            await storage('file_data_' + doc.id, cookie, data);
            catalog.push(record);
        }
        await fs.writeFile(path.join(directory, doc.name), bytes);
        manifest.push({ id: doc.id, name: doc.name, sha256: sha(bytes), rows: doc.rows.length });
    }
    await storage('tea_hub_urgent_files_v1', cookie, catalog);
    await fs.writeFile(path.join(directory, 'manifest.json'), JSON.stringify(manifest, null, 2));
}

async function main() {
    const bytes = await fs.readFile(path.join(__dirname, 'comparison_20261030.json'));
    const spec = JSON.parse(bytes);
    const cookies = { admin: await login('closed-admin'), a: await login('closed-a'), b: await login('closed-b') };
    await prepare(spec, cookies.admin);
    const ready = await waitSnapshot();
    const prefix = 'comparison-http-' + Date.now();
    const rows = [], waits = [];
    async function ask(item, actor = 'b') {
        const body = { chatId: prefix + '-' + item.chat, messages: [{ role: 'user', content: item.q }] };
        let start = performance.now();
        let result = await call('/api/ai-chat', body, cookies[actor]);
        if (result.response.status === 429) {
            const seconds = Number(result.response.headers.get('retry-after')) || 300;
            if (seconds < 0 || seconds > 360) throw new Error('Unexpected rate limit interval');
            waits.push({ id: item.id, actor, seconds });
            console.log(JSON.stringify({ event: 'respecting-rate-limit', seconds }));
            for (let remaining = seconds * 1000 + 100; remaining > 0; remaining -= Math.min(remaining, 30000)) {
                await new Promise(resolve => setTimeout(resolve, Math.min(remaining, 30000)));
            }
            start = performance.now();
            result = await call('/api/ai-chat', body, cookies[actor]);
        }
        const { response, value } = result;
        const answer = value.output_text || '', links = value.sourceLinks || [], errors = [];
        if (response.status !== 200) errors.push('http:' + response.status);
        if (value.search_status !== item.status) errors.push('status:' + value.search_status);
        for (const phrase of item.has || []) if (!answer.toLowerCase().includes(phrase.toLowerCase())) errors.push('missing:' + phrase);
        for (const phrase of item.absent || []) if (answer.toLowerCase().includes(phrase.toLowerCase())) errors.push('unexpected:' + phrase);
        const answered = ['answer', 'partial'].includes(value.search_status);
        if (answered && !links.length) errors.push('no-source-links');
        if (!answered && links.length) errors.push('unexpected-links');
        if (answer.length > 1200 || answer.includes('**')) errors.push('readability');
        rows.push({ ...item, actor, answer, links, status: value.search_status, errors,
                    pass: !errors.length, ms: Math.round(performance.now() - start) });
    }
    for (const item of [...spec.cases, ...spec.holdout]) await ask(item);
    await ask({ id: 'acl-fixture', chat: 'denied', q: 'Сравни объем термоса Север и термоса Юг', status: 'not_found' }, 'a');
    await ask({ id: 'acl-context', chat: 'both', q: 'А упаковки?', status: 'clarify' }, 'a');
    await ask({ id: 'separate-chat', chat: 'fresh', q: 'А упаковки?', status: 'clarify' });
    const report = { ready, specSHA256: sha(bytes), chatPrefix: prefix, waits,
                     total: rows.length, passed: rows.filter(row => row.pass).length, cases: rows };
    const output = path.join(lab, 'comparison-http-' + Date.now() + '.json');
    await fs.writeFile(output, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ file: output, total: report.total, passed: report.passed, failures: rows.filter(row => !row.pass) }));
    if (report.total !== report.passed) process.exitCode = 1;
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## evaluation/closed_search/verify_lab.cjs

SHA-256: 0c9fd6f94fa4757371a9868addf8ddab1c7a2fb8a69ed1e2ba1dcfc5e2996b0a

````javascript
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
    const closedOnly = process.argv.includes('--closed-only');
    // Общий чат на 8000 не участвует в отдельном закрытом поиске на 8001.
    // По умолчанию по-прежнему требуем оба сервиса; отдельный режим явный.
    let generalStatus = 0;
    try {
        const general = await fetch('http://127.0.0.1:8000/health', { signal: AbortSignal.timeout(10000) });
        generalStatus = general.status;
    } catch (error) {
        if (!closedOnly) throw error;
    }
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
    const crossFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'cross_documents_20261029.json'), 'utf8'));
    const linkFixtures = JSON.parse(await fs.readFile(path.join(__dirname, 'document_links_20261029.json'), 'utf8'));
    const realManifest = JSON.parse(await fs.readFile(path.join(lab, 'real-materials/manifest.json'), 'utf8'));
    let comparisonCount = 0;
    try {
        comparisonCount = (JSON.parse(await fs.readFile(path.join(lab, 'comparison-assets/manifest.json'), 'utf8'))).length;
    } catch (error) {
        if (error.code !== 'ENOENT') throw error;
    }
    const expectedDocuments = 9 + reasoningFixtures.documents.length + conditionFixtures.documents.length + structureFixtures.documents.length + structureAssets.length + scanFixtures.documents.length + layoutFixtures.documents.length + qualityFixtures.documents.length + multipageFixtures.documents.length + crossFixtures.documents.length + linkFixtures.documents.length + realManifest.documents.length + comparisonCount;
    const result = { state: status.state, indexedSources: status.sources, indexedPassages: status.passages,
        restoredFromDisk: status.restored_from_disk, restoreMs: status.restore_ms, persistedContextCount: status.context_count,
        catalogDocuments: catalog.length, coreWithoutKey: unauthenticatedCore.status,
        chatWithoutSession: unauthenticatedSite.response.status, generalApiHealth: generalStatus, generalApiRequired: !closedOnly,
        expectedSources, expectedDocuments, pass: status.state === 'ready' && status.sources === expectedSources && catalog.length === expectedDocuments
            && unauthenticatedCore.status === 403 && unauthenticatedSite.response.status === 401 && (closedOnly || generalStatus === 200) };
    await fs.writeFile(path.join(lab, 'final-service-status.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
    if (!result.pass) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## evaluation/closed_search/finalize_comparison.cjs

SHA-256: 8e24091381371163a80c474299a746409e3beea0f3956284a489acdc707f4b14

````javascript
/* Проверяет доказательства этапа и сохраняет отчёт вместе с полным кодом. */
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const site = path.join(lab, 'tea-app-main');
const reportName = 'CLOSED_SEARCH_COMPARISON_REPORT_2026_10_30.md';
const snapshotName = 'CLOSED_SEARCH_COMPARISON_CODE_SNAPSHOT_2026_10_30.md';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const read = async name => JSON.parse((await fs.readFile(path.join(lab, name), 'utf8')).replace(/^\uFEFF/, ''));
const percentile = (rows, fraction) => [...rows].sort((a, b) => a - b)[Math.ceil(rows.length * fraction) - 1];

// История остаётся на месте: новая запись добавляется сразу после заголовка.
async function prepareEntry(file, entry) {
    const text = await fs.readFile(file, 'utf8');
    if (text.includes(entry.split('\n')[0])) throw new Error('Stage already recorded: ' + file);
    const split = text.indexOf('\n');
    if (split < 0) throw new Error('Missing Markdown heading: ' + file);
    return { file, text: text.slice(0, split + 1) + '\n' + entry + '\n\n' + text.slice(split + 1) };
}

async function main() {
    const baseline = await read('comparison-baseline-1793339859056060400.json');
    const local = await read('comparison-final-1793340741145921800.json');
    const holdout = await read('comparison-new-first-1793340539394228100.json');
    const http = await read('comparison-http-1793340715425.json');
    const services = await read('comparison-services.json');
    const browser = await read('comparison-browser-verification.json');
    const manifest = await read('real-materials/manifest.json');
    const specHash = sha(await fs.readFile(path.join(__dirname, 'comparison_20261030.json')));
    const integrity = { originals: [], copies: [], code: [], specUnchanged: false };
    for (const input of manifest.inputs) {
        const actual = sha(await fs.readFile(path.join('C:/Users/KROLL/Downloads', input.name)));
        integrity.originals.push({ name: input.name, expected: input.sha256, actual, pass: actual === input.sha256 });
    }
    for (const doc of manifest.documents) {
        const actual = sha(await fs.readFile(path.join(lab, 'real-materials', doc.name)));
        integrity.copies.push({ name: doc.name, expected: doc.sha256, actual, pass: actual === doc.sha256 });
    }
    integrity.specUnchanged = [baseline.spec_sha256, local.spec_sha256, holdout.spec_sha256, http.specSHA256]
        .every(value => value === specHash);
    const unitLog = await fs.readFile(path.join(lab, 'comparison-unittest.log'), 'utf8');
    if (!integrity.specUnchanged || !integrity.originals.every(row => row.pass) || !integrity.copies.every(row => row.pass)
        || local.passed !== local.total || holdout.passed !== holdout.total || http.passed !== http.total
        || !services.pass || !services.restoredFromDisk || !browser.pass
        || !/Ran 216 tests/.test(unitLog) || !/\bOK\s*$/.test(unitLog)) {
        throw new Error('Stage evidence is incomplete');
    }
    const latency = http.cases.map(row => row.ms);
    const report = `# Сравнение двух товаров и размеры упаковки — 30.10.2026

Текущий этап основного плана ВатэсAi: закрытый поиск по материалам сайта.
Код: C:/Users/KROLL/Desktop/Ai. Изменён собственный алгоритм поиска и извлечения;
обучение весов нейросети не выполнялось. Рабочая LMS и публикация не затронуты.

## Что изменилось

Поиск выбирает два отдельных товара, извлекает одну запрошенную характеристику
для каждого и формирует короткий ответ. Совпавшие коды в разных копиях выгрузки
объединяются. Категория из вопроса помогает отличить чахай «Феникс» от кофе
с похожим именем. Названия ингредиентов с предлогом «с» не разрывают название
товара при сравнении составов.

Размеры изделия, размеры коробки, масса нетто, масса с упаковкой и масса самой
упаковки разбираются отдельно. Размер коробки не заполняет отсутствующий размер
изделия. Пищевая ценность на 100 г, масса белков/жиров и допустимая нагрузка
не становятся массой товара. Длина носика или крышки не выдаётся за размер
всего изделия. Нормализация миллиметров/сантиметров, граммов/килограммов,
миллилитров/литров позволяет сравнить значения и вычислить разницу.

Пара товаров и характеристика сохраняются в памяти конкретного чата.
Продолжения «А упаковки?» и «А с упаковкой?» используют соответствующие поля.
Если характеристика не названа, система уточняет её. Если название неоднозначно,
нужно выбрать вариант. Если доступен факт только об одном товаре, ответ сообщает
известное и явно указывает недостающие данные. Скрытый товар не подменяется
доступным. Противоречия внутри описания и с официальными документами не скрываются.

| Вопрос или продолжение | Проверенный результат |
|---|---|
| Сравни объём чахаев Дао и Феникс | 190 мл и 140 мл; разница 50 мл |
| Сравни материал подставок Хайвэй и Звёздочка | Металл и стекло |
| Сравни состав двух вариантов медового мусса | Отдельный состав каждого выбранного варианта |
| Сравни размеры термосов Север и Юг | 20×8×8 см и 220×80×80 мм — размеры изделий |
| А упаковки? | 24×10×10 см и 26×11×11 см — размеры коробок |
| Что легче, Север или Юг? | 300 г и 0,35 кг; нормализация единиц, разница 50 г |
| Какой размер стакана Луг? | Размер изделия неизвестен; данные коробки не подставляются |
| Сравни размеры Ча Бань Груша и Ча Бань Точка | Сообщение о противоречии в исходных данных |

Север, Юг, Луг, Пик, Лес и Камень — шесть явно учебных позиций, созданных только
для проверки границ. В настоящих выгрузках не нашлось достаточных пар полей
«размер изделия / размер упаковки». Учебный файл помечен как тестовый,
не является регламентом компании и доступен только профилю Б/администратору.
Настоящие сравнения проверены отдельно на предоставленных товарных выгрузках.

## Результаты проверки

- Основной фиксированный набор: ${baseline.passed}/${baseline.total} до изменения → ${local.passed}/${local.total} после.
- Дополнительные новые формулировки: ${holdout.passed}/${holdout.total} с первого запуска после разработки.
  Эти 12 вопросов не использовались для исправлений после получения результата.
- Весь локальный набор: 216/216 тестов за 6,149 с, включая 23 новых сценария.
- Предыдущие товарные вопросы: 14/14; исходная теория: 16/16 и 4/4.
- Через настоящий HTTP-маршрут сайта: ${http.passed}/${http.total}. Это 25 вопросов,
  две проверки отсутствия доступа у профиля А и один вопрос в новом чате Б.
- В HTTP-прогоне медиана ${percentile(latency, .5)} мс, p95 ${percentile(latency, .95)} мс,
  максимум ${Math.max(...latency)} мс. Это короткий замер, не длительная нагрузка.
- В браузере получены три ответа: объёмы чахаев, размеры изделий, размеры коробок.
  Перед третьим вопросом действительно перезапущен API 8001; пара и предмет
  уточнения сохранились. Повторное открытие страницы сохранило весь диалог.
- Обнаружено и исправлено совпадение React-ключей у ссылок на два товара из одного
  файла. После перезагрузки ошибок браузера нет. Остаётся предупреждение Next.js
  о приоритете загрузки логотипа, не связанное с поиском. Проверка синтаксиса TSX
  прошла; полная проверка типов всего стороннего стенда здесь не заявляется.
- Все ${integrity.originals.length} оригинала в Downloads и ${integrity.copies.length} подготовленные копии сохранили SHA-256.
- SHA-256 вопросов и ожиданий неизменен: ${specHash}.

В журнале unittest сообщения restore_failed и context_save_failed принадлежат
тестам искусственного сбоя. Итоговый результат набора — OK.

Это ограниченная приёмка новых сценариев, а не доказательство понимания всех
1 827 товаров и любых формулировок. Предыдущая нагрузка 430/430 относится к
прошлой версии и не считается нагрузочной проверкой этого изменения.

## Состояние стенда

Сайт: http://127.0.0.1:3040/tasks?tab=standards. Закрытый API: 8001.
${services.catalogDocuments} документов, ${services.indexedSources} источников, ${services.indexedPassages} фрагментов.
Индекс восстановлен с диска за ${services.restoreMs} мс; повторный OCR не выполнялся.
Общий чат 8000 не участвует в этой проверке. Команда проверки явно использовала
--closed-only; проверка без этого флага по-прежнему требует общий API.
Порт 3000 и рабочий проект LMS не изменялись. На GitHub ничего не отправлено.

## Ограничения и следующий шаг

Сравнение поддерживает две позиции и одну названную характеристику за вопрос.
Произвольные рекомендации «что лучше» без критерия требуют уточнения.
Из вектора без подписи осей нельзя надёжно вывести длину или высоту;
приблизительные значения и диапазоны не превращаются в точные числа.
Распознавание свободного текста ограничено поддержанными конструкциями.
Новые реальные карточки с другим оформлением требуют отдельной приёмки.

Архив от 18.06.2026 не подтверждает сегодняшние цены, остатки и официальность
рекламных утверждений. Ссылки имеют названия товаров, но пока открывают целую
часть выгрузки. Следующий шаг по плану — открывать нужную позицию или фрагмент
по ссылке из ответа и проверять это с правами доступа и сменой редакции.
Затем — приёмка на актуальных настоящих документах и сканах, когда они будут доступны.

## Воспроизводимость

Из C:/Users/KROLL/Desktop/Ai:

~~~powershell
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
.\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tests -p 'test_site_search*.py'
.\\.venv\\Scripts\\python.exe -X utf8 -m evaluation.closed_search.comparison_probe --label comparison-recheck
.\\.venv\\Scripts\\python.exe -X utf8 -m evaluation.closed_search.comparison_probe --holdout --label comparison-new-recheck
node evaluation/closed_search/comparison_http.cjs
node evaluation/closed_search/verify_lab.cjs --closed-only
~~~

Свидетельства находятся в .runtime/closed-search-lab-20261028:
comparison-baseline-1793339859056060400.json;
comparison-final-1793340741145921800.json;
comparison-new-first-1793340539394228100.json;
comparison-http-1793340715425.json;
comparison-catalog-regression-1793340738515432800.json;
comparison-theory-1793340742082207100.json;
comparison-theory-alternate-1793340742813196000.json;
comparison-unittest.log; comparison-services.json;
comparison-browser-verification.json; comparison-proof.png; comparison-integrity.json.

Полные листинги всех новых и изменённых файлов кода: ${snapshotName}.
`;
    const entry = `## 2026-10-30 — сравнение товаров и разделение размеров изделия/упаковки

Завершён следующий этап закрытого поиска: сравнение двух товаров по выбранной
характеристике; размеры изделия и коробки; масса нетто, с упаковкой и самой
упаковки; нормализация единиц. Пара товаров сохраняется после рестарта.
Недостающие значения не выдумываются, конфликт остаётся конфликтом, права
проверяются до выбора товара. Шесть карточек с упаковками явно помечены учебными.

Проверено: 216/216 локальных тестов; основной набор 2/13 → 13/13;
12/12 новых формулировок с первого прогона; HTTP 28/28 с двумя профилями.
В браузере проверено сравнение и продолжение после реального рестарта API.
Исправлен повтор ключей двух ссылок на один документ. Оригиналы сохранены.
Стенд 3040 / API 8001: 125 документов, 208 источников, 2 849 фрагментов.
Это улучшение алгоритма поиска, не обучение весов и не рабочая интеграция LMS.

Отчёт: ${reportName}. Полный код: ${snapshotName}.
Далее по плану: переход из ответа прямо к нужному товару/фрагменту; проверка
доступа и смены редакции. Затем — приёмка на актуальных документах и сканах.
Ниже сохранена история предыдущих этапов.`;
    const siteEntry = `## 2026-10-30 — приёмка сравнения двух товаров

Изолированный стенд ВатэсAi 3040 использует закрытый поиск 8001. Добавлен один
явно учебный файл с шестью карточками и различными размерами изделия/коробки,
доступный Б/администратору. Число документов — 125, источников — 208.
Через сайт прошли 28/28 проверок сравнения, памяти и доступа; в браузере
проверено продолжение «А упаковки?» после перезапуска API. В AIAssistant.tsx
исправлены ключи ссылок: два товара в одном файле больше не создают ошибку React.
Настоящие исходные файлы не изменены. Рабочая LMS и публикация не затронуты;
процедура запуска/развёртывания не менялась.

Полный отчёт: C:/Users/KROLL/Desktop/Ai/${reportName}.
Далее — переход к нужной позиции по ссылке с проверкой доступа и редакции.`;
    const writes = [{ file: path.join(root, reportName), text: report }];
    for (const name of ['CLOSED_SEARCH_PLAN_2026_10_28.md', 'README.md', 'PROGRESS_LOG.md', 'DECISIONS.md']) {
        writes.push(await prepareEntry(path.join(root, name), entry));
    }
    for (const name of ['PROJECT_CONTEXT.md', 'TASKS.md', 'HANDOFF.md', 'DEPLOYMENT.md']) {
        writes.push(await prepareEntry(path.join(site, name), siteEntry));
    }
    const files = ['ai_core/site_search_measurements.py', 'ai_core/site_search_comparison.py',
        'ai_core/site_search_catalog.py', 'ai_core/site_search.py', 'ai_core/site_search_runtime.py',
        'tests/test_site_search_comparison.py', 'evaluation/closed_search/comparison_20261030.json',
        'evaluation/closed_search/comparison_probe.py', 'evaluation/closed_search/comparison_http.cjs',
        'evaluation/closed_search/verify_lab.cjs', 'evaluation/closed_search/finalize_comparison.cjs',
        '.runtime/closed-search-lab-20261028/tea-app-main/app/tasks/components/AIAssistant.tsx'];
    let snapshot = '# Полные листинги этапа сравнения товаров — 30.10.2026\n\n';
    for (const file of files) {
        const bytes = await fs.readFile(path.join(root, file));
        const hash = sha(bytes);
        integrity.code.push({ file, sha256: hash, bytes: bytes.length });
        const language = file.endsWith('.py') ? 'python' : file.endsWith('.json') ? 'json'
            : file.endsWith('.tsx') ? 'tsx' : 'javascript';
        snapshot += '\n## ' + file + '\n\nSHA-256: ' + hash + '\n\n````' + language + '\n' + bytes.toString('utf8') + '\n````\n';
    }
    writes.push({ file: path.join(root, snapshotName), text: snapshot });
    writes.push({ file: path.join(lab, 'comparison-integrity.json'), text: JSON.stringify(integrity, null, 2) });
    // Запись начинается лишь после проверки всех входов и подготовки всех текстов.
    for (const item of writes) await fs.writeFile(item.file, item.text, 'utf8');
    console.log(JSON.stringify({ report: reportName, snapshot: snapshotName, sourceFiles: files.length,
        originalsUnchanged: true, copiesUnchanged: true, specUnchanged: true,
        httpMedianMs: percentile(latency, .5), httpP95Ms: percentile(latency, .95), documentsUpdated: writes.length }));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## .runtime/closed-search-lab-20261028/tea-app-main/app/tasks/components/AIAssistant.tsx

SHA-256: 9a798c313dc3c3d498b29a372edb92712d381c69f010267be88054ae1d2f51fa

````tsx
"use client";
import React, { useState, useEffect, useEffectEvent, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import CustomIcon from '@/app/components/CustomIcon';
import { isClientAdminView } from '@/app/lib/authClient';

interface Message {
    id: string;
    role: 'user' | 'ai';
    content: string;
    timestamp: string;
    sourceLinks?: Array<{ title: string; url: string }>;
}

interface ChatSession {
    id: string;
    title: string;
    messages: Message[];
    updatedAt: number;
    isPinned?: boolean; 
    documentId?: string;
}

const MAX_AI_HISTORY_MESSAGES = 10;
const MAX_AI_HISTORY_MESSAGE_CHARACTERS = 3_500;
const MAX_AI_CURRENT_MESSAGE_CHARACTERS = 12_000;
const MARKDOWN_STAR_CHARACTERS = /[*＊﹡∗⁎⁕✱✲✳]/gu;
const INTERNAL_LMS_LINK = /(?:https?:\/\/(?:www\.)?tea-hub\.ru)?\/tasks\?[^\s<>"'`]+/gu;

const limitAiText = (value: string, maxCharacters: number): string => {
    if (value.length <= maxCharacters) {
        return value;
    }

    // Оставляем конец текста, потому что там находится текущий вопрос
    // пользователя и самые свежие найденные совпадения.
    const headLength = Math.floor(maxCharacters * 0.65);
    const tailLength = maxCharacters - headLength;

    return `${value.slice(0, headLength)}\n\n[Контекст сокращен]\n\n${value.slice(-tailLength)}`;
};

const normalizeSearchValue = (value: unknown) => String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();

const normalizeAiHref = (value: string): string | null => {
    const trimmedValue = value.trim().replace(/[.,!?;:)\]]+$/u, '');

    try {
        const parsedUrl = new URL(trimmedValue, 'https://tea-hub.ru');
        const isInternalHost = parsedUrl.hostname === 'tea-hub.ru' || parsedUrl.hostname === 'www.tea-hub.ru';
        if (!isInternalHost || !['/tasks', '/closed-search-material'].includes(parsedUrl.pathname)) {
            return null;
        }

        return `${parsedUrl.pathname}${parsedUrl.search}${parsedUrl.hash}`;
    } catch {
        return null;
    }
};

const hideInternalIdentifiers = (value: string): string => {
    const links: string[] = [];
    const textWithMaskedLinks = value.replace(INTERNAL_LMS_LINK, (link) => {
        const placeholder = `__LMS_LINK_${links.length}__`;
        links.push(link);
        return placeholder;
    });
    const cleanedText = textWithMaskedLinks
        .replace(/\b(?:id|ид|идентификатор)(?:\s+(?:документа|темы|теста|товара|источника|файла))?\s*[:№#-]?\s*[a-z0-9][a-z0-9_-]{2,}/giu, '')
        .replace(/\b(?:file|document|topic|test|product|route|lms|chat|msg)_[a-z0-9_-]{3,}\b/giu, '')
        .replace(/[ \t]{2,}/gu, ' ')
        .replace(/\n[ \t]+/gu, '\n')
        .replace(/\n{3,}/gu, '\n\n');

    return links.reduce((result, link, index) => {
        return result.replace(`__LMS_LINK_${index}__`, link);
    }, cleanedText).trim();
};

const cleanAiMessageText = (value: string): string => hideInternalIdentifiers(
    value
        .replace(/\r\n?/gu, '\n')
        .replace(/\\([*_`~#>])/gu, '$1')
        .replace(/^\s{0,3}#{1,6}\s*/gmu, '')
        .replace(/^\s{0,3}>\s?/gmu, '')
        .replace(MARKDOWN_STAR_CHARACTERS, '')
        .replace(/[`~]/gu, '')
        .replace(/(^|\n)\s*[-+]\s+/gmu, '$1')
        .replace(/\n{3,}/gu, '\n\n')
        .trim(),
);

const prepareAiMessageText = (value: string): string => cleanAiMessageText(
    value.replace(/\[([^\]]+)\]\(([^)\s]+)\)/gu, (...matches: string[]) => {
        const label = matches[1] || '';
        const href = matches[2] || '';
        const safeHref = normalizeAiHref(href);
        return safeHref ? `${label} ${safeHref}` : label;
    }),
);

const getAiLinkLabel = (href: string): string => {
    try {
        const parsedUrl = new URL(href, 'https://tea-hub.ru');
        if (parsedUrl.searchParams.has('routeId')) {
            return 'Открыть урок';
        }

        if (parsedUrl.searchParams.has('testId')) {
            return 'Открыть тест';
        }

        if (parsedUrl.searchParams.has('documentId')) {
            return 'Открыть документ';
        }

        if (parsedUrl.searchParams.has('productId')) {
            return 'Открыть карточку товара';
        }
    } catch {
        return 'Открыть';
    }

    return 'Открыть раздел';
};

const renderAiMessage = (value: string): React.ReactNode => {
    const messageText = prepareAiMessageText(value);
    const linkPattern = /(?:https?:\/\/[^\s<>"'`]+|\/tasks\?[^\s<>"'`]+)/gu;
    const parts: React.ReactNode[] = [];
    let cursor = 0;
    let match: RegExpExecArray | null;
    let linkIndex = 0;

    while ((match = linkPattern.exec(messageText)) !== null) {
        const rawLink = match[0];
        const safeHref = normalizeAiHref(rawLink);
        if (!safeHref) {
            continue;
        }

        if (match.index > cursor) {
            parts.push(messageText.slice(cursor, match.index));
        }

        parts.push(
            <a
                key={`ai-link-${linkIndex}`}
                href={safeHref}
                className="ai-message-link"
            >
                {getAiLinkLabel(safeHref)}
            </a>,
        );
        linkIndex += 1;
        cursor = match.index + rawLink.length;
    }

    if (parts.length === 0) {
        return messageText;
    }

    if (cursor < messageText.length) {
        parts.push(messageText.slice(cursor));
    }

    return parts;
};

const extractAiResponseText = (data: Record<string, unknown>): string => {
    const output = Array.isArray(data.output) ? data.output : [];
    const outputParts = output.flatMap((item) => {
        if (!item || typeof item !== 'object') {
            return [];
        }

        const content = (item as { content?: unknown }).content;
        if (!Array.isArray(content)) {
            return [];
        }

        return content.flatMap((contentItem) => {
            if (!contentItem || typeof contentItem !== 'object') {
                return [];
            }

            const text = (contentItem as { text?: unknown }).text;
            return typeof text === 'string' && text.trim() ? [text.trim()] : [];
        });
    });

    if (outputParts.length > 0) {
        return outputParts.join('\n\n');
    }

    const message = data.message;
    const messageText = message && typeof message === 'object'
        ? (message as { text?: unknown; content?: unknown })
        : {};
    const fallbackValues: unknown[] = [
        data.output_text,
        data.text,
        messageText.text,
        typeof messageText.content === 'string' ? messageText.content : '',
    ];
    const fallbackText = fallbackValues.find((item) => typeof item === 'string' && item.trim());
    return typeof fallbackText === 'string' ? fallbackText.trim() : '';
};

export default function AIAssistant({ userId, isAdmin }: { userId?: string, isAdmin?: boolean }) {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [sessions, setSessions] = useState<ChatSession[]>([]);
    const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
    const [inputValue, setInputValue] = useState("");
    const [isTyping, setIsTyping] = useState(false);
    const [showClearConfirm, setShowClearConfirm] = useState(false);
    const [isMobileHistoryOpen, setIsMobileHistoryOpen] = useState(false);
    const [historyQuery, setHistoryQuery] = useState("");
    const [isHistoryLoaded, setIsHistoryLoaded] = useState(false);
    
    const [currentUserId, setCurrentUserId] = useState<string | null>(null);
    
    const chatContainerRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const handledDocumentRequestRef = useRef('');

    // =========================================================================
    // ОПРЕДЕЛЕНИЕ ПОЛЬЗОВАТЕЛЯ (АДМИН - 100% ПРИОРИТЕТ)
    // =========================================================================
    useEffect(() => {
        const determineUser = () => {
            const storedUserId = localStorage.getItem('current_user_id') || localStorage.getItem('login') || localStorage.getItem('userId');
            const isSystemAccount = localStorage.getItem('is_system_account') === 'true';
            const isAdminView = isAdmin === true || isClientAdminView();

            if (isSystemAccount && isAdminView) {
                const normalizedSystemId = String(userId || storedUserId || 'system').replace(/[^a-zA-Z0-9_-]/g, '_');
                return `system_admin_${normalizedSystemId}`;
            }

            if (isAdminView || userId === 'admin') {
                return 'admin_master'; 
            }

            if (userId && userId !== 'guest' && userId.trim() !== '') {
                return 'emp_' + String(userId).replace(/[^a-zA-Z0-9_-]/g, '_');
            }

            const foundId = storedUserId;
            if (foundId && foundId !== 'guest' && foundId !== 'null') {
                return 'emp_' + String(foundId).replace(/[^a-zA-Z0-9_-]/g, '_');
            }

            let guestId = localStorage.getItem('th_stable_guest_id');
            if (!guestId) {
                guestId = 'guest_' + Math.random().toString(36).substr(2, 9);
                localStorage.setItem('th_stable_guest_id', guestId);
            }
            return guestId;
        };

        const activeUser = determineUser();
        const userIdTimer = window.setTimeout(() => setCurrentUserId(activeUser), 0);

        const loadHistory = async () => {
            let serverDataFound = false;
            try {
                const res = await fetch(`/api/storage?key=th_ai_history_${activeUser}&t=${Date.now()}`);
                if (res.ok) {
                    const data = await res.json();
                    if (Array.isArray(data) && data.length > 0) {
                        setSessions(data);
                        setActiveSessionId(data[0].id);
                        serverDataFound = true;
                    }
                }
            } catch {
                console.warn("Сервер недоступен, читаем из памяти");
            }

            if (!serverDataFound) {
                const savedSessions = localStorage.getItem(`th_ai_history_${activeUser}`);
                if (savedSessions) {
                    try {
                        const parsed = JSON.parse(savedSessions);
                        if (Array.isArray(parsed) && parsed.length > 0) {
                            setSessions(parsed);
                            setActiveSessionId(parsed[0].id);
                        }
                    } catch {}
                }
            }

            setIsHistoryLoaded(true);
        };

        loadHistory();
        return () => window.clearTimeout(userIdTimer);
    }, [userId, isAdmin]); 

    useEffect(() => {
        if (chatContainerRef.current) {
            chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
        }
    }, [sessions, activeSessionId, isTyping]);

    useEffect(() => {
        if (!showClearConfirm) {
            return;
        }

        const previousOverflow = document.body.style.overflow;
        const closeOnEscape = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                setShowClearConfirm(false);
            }
        };

        document.body.style.overflow = 'hidden';
        window.addEventListener('keydown', closeOnEscape);

        return () => {
            document.body.style.overflow = previousOverflow;
            window.removeEventListener('keydown', closeOnEscape);
        };
    }, [showClearConfirm]);

    const saveSessions = (newSessions: ChatSession[]) => {
        setSessions(newSessions);
        if (!currentUserId) return;

        localStorage.setItem(`th_ai_history_${currentUserId}`, JSON.stringify(newSessions));
        
        fetch('/api/storage', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                key: `th_ai_history_${currentUserId}`,
                data: newSessions 
            })
        }).catch(err => console.error("Ошибка сохранения на сервер", err));
    };

    const focusComposer = () => {
        window.setTimeout(() => inputRef.current?.focus(), 0);
    };

    const createNewSession = () => {
        const existingEmptySession = sessions.find((session) => session.messages.length === 0);

        if (existingEmptySession) {
            setActiveSessionId(existingEmptySession.id);
            setHistoryQuery("");
            setIsMobileHistoryOpen(false);
            focusComposer();
            return;
        }

        const newSession: ChatSession = {
            id: `chat_${Date.now()}`,
            title: "Новый диалог",
            messages: [],
            updatedAt: Date.now()
        };
        saveSessions([newSession, ...sessions]);
        setActiveSessionId(newSession.id);
        setHistoryQuery("");
        setIsMobileHistoryOpen(false);
        focusComposer();
    };

    const clearHistory = () => {
        saveSessions([]);
        setActiveSessionId(null);
        setShowClearConfirm(false);
        setIsMobileHistoryOpen(false);
    };

    const deleteSession = (sessionId: string, e: React.MouseEvent) => {
        e.stopPropagation(); 
        const filtered = sessions.filter((s: ChatSession) => s.id !== sessionId);
        saveSessions(filtered);
        
        if (activeSessionId === sessionId) {
            setActiveSessionId(filtered.length > 0 ? sortedSessions.filter(s => s.id !== sessionId)[0]?.id || null : null);
        }
    };

    const togglePin = (sessionId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        const updated = sessions.map(s => s.id === sessionId ? { ...s, isPinned: !s.isPinned } : s);
        saveSessions(updated);
    };

    const sortedSessions = [...sessions].sort((a, b) => {
        if (a.isPinned && !b.isPinned) return -1;
        if (!a.isPinned && b.isPinned) return 1;
        return b.updatedAt - a.updatedAt;
    });

    const normalizedHistoryQuery = normalizeSearchValue(historyQuery);
    const visibleSessions = sortedSessions.filter((session) => {
        if (!normalizedHistoryQuery) {
            return true;
        }

        return normalizeSearchValue(`${session.title} ${session.messages.map((message) => message.content).join(' ')}`).includes(normalizedHistoryQuery);
    });

    const formatMessageCount = (count: number) => {
        const lastTwoDigits = count % 100;
        const lastDigit = count % 10;

        if (lastTwoDigits >= 11 && lastTwoDigits <= 14) {
            return `${count} сообщений`;
        }

        if (lastDigit === 1) {
            return `${count} сообщение`;
        }

        if (lastDigit >= 2 && lastDigit <= 4) {
            return `${count} сообщения`;
        }

        return `${count} сообщений`;
    };

    const handleSendMessage = async (
        text: string,
        options: { documentId?: string; newSession?: boolean } = {},
    ) => {
        if (!text.trim() || !currentUserId || isTyping) return;

        let currentActiveId = options.newSession ? null : activeSessionId;
        let currentSessions = [...sessions];

        if (!currentActiveId || currentSessions.length === 0) {
            const newSession: ChatSession = {
                id: `chat_${Date.now()}`,
                title: text.slice(0, 25) + "...",
                messages: [],
                updatedAt: Date.now(),
                ...(options.documentId ? { documentId: options.documentId } : {}),
            };
            currentSessions = [newSession, ...currentSessions];
            currentActiveId = newSession.id;
            setActiveSessionId(currentActiveId);
        }

        const userMsg: Message = {
            id: `msg_${Date.now()}`,
            role: 'user',
            content: text,
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        };

        const updatedSessions = currentSessions.map((s: ChatSession) => {
            if (s.id === currentActiveId) {
                const newTitle = s.messages.length === 0 ? text.slice(0, 25) + "..." : s.title;
                return {
                    ...s,
                    title: newTitle,
                    messages: [...s.messages, userMsg],
                    updatedAt: Date.now(),
                    ...(options.documentId ? { documentId: options.documentId } : {}),
                };
            }
            return s;
        }); 

        saveSessions(updatedSessions);
        setInputValue("");
        if (inputRef.current) {
            inputRef.current.style.height = 'auto';
        }
        setIsTyping(true);

        try {
            const currentSession = updatedSessions.find((s: ChatSession) => s.id === currentActiveId);
            const activeDocumentId = options.documentId || currentSession?.documentId;

            const recentMessages = currentSession?.messages.slice(-MAX_AI_HISTORY_MESSAGES) || [];
            const apiMessages = recentMessages.map((message, index) => {
                const maxCharacters = index === recentMessages.length - 1
                    ? MAX_AI_CURRENT_MESSAGE_CHARACTERS
                    : MAX_AI_HISTORY_MESSAGE_CHARACTERS;

                return {
                    role: message.role === 'ai' ? 'assistant' : 'user',
                    content: limitAiText(message.content, maxCharacters),
                };
            });

            const response = await fetch('/api/ai-chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    messages: apiMessages,
                    chatId: currentActiveId,
                    ...(activeDocumentId ? { documentId: activeDocumentId } : {}),
                })
            });

            const data = await response.json() as Record<string, unknown>;

            if (data.error) {
                const errStr = JSON.stringify(data.error).toLowerCase();
                if (errStr.includes('quota') || errStr.includes('token') || errStr.includes('токен') || errStr.includes('limit') || errStr.includes('баланс') || errStr.includes('429')) {
                    throw new Error("TOKEN_LIMIT_EXCEEDED");
                }
                throw new Error(JSON.stringify(data.error));
            }

            if (!response.ok) throw new Error(`HTTP ${response.status}: ${JSON.stringify(data)}`);

            let aiText = extractAiResponseText(data);

            if (!aiText) {
                aiText = `СЫРОЙ ОТВЕТ ЯНДЕКСА:\n${JSON.stringify(data, null, 2)}`;
            }

            aiText = prepareAiMessageText(aiText);

            const aiMsg: Message = {
                id: `msg_${Date.now() + 1}`,
                role: 'ai',
                content: aiText,
                sourceLinks: Array.isArray(data.sourceLinks) ? data.sourceLinks : [],
                timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            };

            const finalSessions = updatedSessions.map((s: ChatSession) => 
                s.id === currentActiveId ? { ...s, messages: [...s.messages, aiMsg] } : s
            );
            saveSessions(finalSessions);

        } catch (error: unknown) {
            console.error("ОШИБКА:", error);

            const errorText = error instanceof Error ? error.message : String(error || 'Неизвестная ошибка');
            let displayError = `СИСТЕМНАЯ ОШИБКА:\n\n${errorText}`;
            const errStr = errorText.toLowerCase();
            
            if (errStr.includes('token') || errStr.includes('токен') || errStr.includes('quota') || errStr.includes('429') || errStr.includes('402') || errStr.includes('limit') || errStr.includes('баланс') || errStr.includes('too many requests')) {
                displayError = "Закончились токены, просьба обратиться к администратору.";
            }

            const errorMsg: Message = {
                id: `msg_${Date.now() + 1}`, role: 'ai',
                content: displayError,
                timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            };
            const finalSessions = updatedSessions.map((s: ChatSession) => 
                s.id === currentActiveId ? { ...s, messages: [...s.messages, errorMsg] } : s
            );
            saveSessions(finalSessions);
        } finally {
            setIsTyping(false); 
        }
    };

    const sendDocumentQuestion = useEffectEvent((question: string, documentId: string) => {
        void handleSendMessage(question, { documentId, newSession: true });
    });

    useEffect(() => {
        const documentId = searchParams.get('askDocumentId')?.trim() || '';
        if (!documentId || !currentUserId || !isHistoryLoaded || isTyping) {
            return;
        }

        const documentTitle = searchParams.get('askDocumentTitle')?.trim() || 'выбранный документ';
        const documentSection = searchParams.get('askDocumentSection')?.trim() || 'Основной раздел';
        const documentRequest = searchParams.get('askDocumentRequest')?.trim() || documentId;
        const requestKey = `${currentUserId}:${documentId}:${documentRequest}`;
        if (handledDocumentRequestRef.current === requestKey) {
            return;
        }

        handledDocumentRequestRef.current = requestKey;
        router.replace('/tasks?tab=standards', { scroll: false });
        sendDocumentQuestion(
            `Кратко расскажи, о чем документ «${documentTitle}», и выдели главное. Используй только этот документ. Раздел: «${documentSection}».`,
            documentId,
        );
    }, [currentUserId, isHistoryLoaded, isTyping, router, searchParams]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSendMessage(inputValue);
        }
    };

    const activeSession = sessions.find((s: ChatSession) => s.id === activeSessionId);

    if (!currentUserId) {
        return null;
    }

    return (
        <section className="ai-monolithic-section">
            <header className="vates-page-heading vates-ai-heading">
                <div>
                    <span className="vates-eyebrow">Ватэс AI</span>
                    <h1>AI-ассистент</h1>
                    <p>Быстрые ответы по регламентам, обучению, документам и товарной базе компании.</p>
                </div>
            </header>

            <div className="ai-monolithic-container">
                {isMobileHistoryOpen && (
                    <button
                        type="button"
                        className="ai-mobile-overlay"
                        aria-label="Закрыть историю диалогов"
                        onClick={() => setIsMobileHistoryOpen(false)}
                    />
                )}

                <aside className={`ai-sidebar custom-scroll ${isMobileHistoryOpen ? 'open' : ''}`}>
                    <div className="ai-sidebar-create">
                        <div className="ai-sidebar-heading">
                            <strong>История</strong>
                        </div>

                        <button
                            type="button"
                            className="hover-unified-app ai-new-session-button"
                            onClick={createNewSession}
                        >
                            <CustomIcon name="chat" size={17} color="currentColor" accent="none" />
                            Новый чат
                        </button>

                        {sessions.length > 0 && (
                            <label className="ai-history-search">
                                <span className="ai-history-search-icon" aria-hidden="true" />
                                <input
                                    type="search"
                                    value={historyQuery}
                                    onChange={(event) => setHistoryQuery(event.target.value)}
                                    placeholder="Поиск"
                                    aria-label="Поиск по истории диалогов"
                                />
                            </label>
                        )}
                    </div>

                    <div className="ai-sidebar-history custom-scroll">
                        {visibleSessions.length === 0 ? (
                            <div className="ai-history-empty">
                                <strong>{sessions.length === 0 ? 'Диалогов пока нет' : 'Ничего не найдено'}</strong>
                                <p>{sessions.length === 0 ? 'Задайте первый вопрос — чат появится здесь.' : 'Измените поисковый запрос.'}</p>
                            </div>
                        ) : (
                            visibleSessions.map((session: ChatSession) => (
                                <div key={session.id} className={`ai-session-item ${activeSessionId === session.id ? 'active' : ''}`}>
                                    <button
                                        type="button"
                                        className="ai-session-select"
                                        onClick={() => {
                                            setActiveSessionId(session.id);
                                            setIsMobileHistoryOpen(false);
                                            focusComposer();
                                        }}
                                    >
                                        <span className="ai-session-icon">
                                            <CustomIcon name={session.isPinned ? 'star' : 'chat'} size={16} color="currentColor" accent="none" />
                                        </span>
                                        <span className="ai-session-copy">
                                            <strong className="ai-session-title">{hideInternalIdentifiers(session.title)}</strong>
                                            <small>{formatMessageCount(session.messages.length)} · {new Date(session.updatedAt).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}</small>
                                        </span>
                                    </button>

                                    <div className={`ai-session-actions ${session.isPinned ? 'pinned' : ''}`}>
                                        <button
                                            type="button"
                                            className={`ai-pin-btn ${session.isPinned ? 'active' : ''}`}
                                            onClick={(event) => togglePin(session.id, event)}
                                            title={session.isPinned ? 'Открепить' : 'Закрепить'}
                                            aria-label={session.isPinned ? 'Открепить диалог' : 'Закрепить диалог'}
                                        >
                                            <CustomIcon name="star" size={15} color="currentColor" accent="none" />
                                        </button>
                                        <button
                                            type="button"
                                            className="ai-session-del-btn"
                                            onClick={(event) => deleteSession(session.id, event)}
                                            title="Удалить диалог"
                                            aria-label="Удалить диалог"
                                        >
                                            <CustomIcon name="close" size={15} color="currentColor" accent="none" />
                                        </button>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>

                    {sessions.length > 0 && (
                        <div className="ai-sidebar-clear">
                            <button
                                type="button"
                                className="hover-unified-app ai-clear-history-button"
                                onClick={() => setShowClearConfirm(true)}
                            >
                                Очистить историю
                            </button>
                        </div>
                    )}
                </aside>

                <div className="ai-chat-area">
                    <header className="ai-chat-toolbar">
                        <button
                            type="button"
                            className="ai-mobile-back-button"
                            onClick={() => router.push('/tasks?tab=welcome')}
                            aria-label="Вернуться к разделам"
                        >
                            <CustomIcon name="arrow-left" size={18} color="currentColor" accent="none" />
                        </button>

                        <button type="button" onClick={() => setIsMobileHistoryOpen(true)} className="ai-history-btn">
                            <CustomIcon name="chat" size={16} color="currentColor" accent="none" />
                            История
                        </button>

                        <div className="ai-active-dialog">
                            <strong>{activeSession?.title || 'Новый чат'}</strong>
                        </div>
                    </header>

                    <div className="ai-messages custom-scroll" ref={chatContainerRef}>
                        {activeSession && activeSession.messages.length > 0 && (
                            activeSession.messages.map((message: Message) => (
                                <div key={message.id} className={`ai-message-row ${message.role}`}>
                                    <div className={`ai-avatar ${message.role}`}>
                                        <CustomIcon
                                            name={message.role === 'user' ? 'user' : 'brain'}
                                            size={18}
                                            color="currentColor"
                                            accent="none"
                                        />
                                    </div>
                                    <div className="ai-message-body">
                                        <div className={`ai-bubble ${message.role} ${message.content.includes('СИСТЕМНАЯ ОШИБКА') || message.content.includes('СЫРОЙ ОТВЕТ') ? 'is-error' : ''}`}>
                                            {message.role === 'ai' ? renderAiMessage(message.content) : hideInternalIdentifiers(message.content)}
                                            {/* Два товара могут ссылаться на один файл. Ключ различает обе ссылки внутри сообщения. */}
                                            {message.sourceLinks?.map((link, linkIndex) => (
                                                <p key={`${link.url}:${link.title}:${linkIndex}`}>
                                                    <a href={normalizeAiHref(link.url) || '#'}>{link.title}</a>
                                                </p>
                                            ))}
                                        </div>
                                        <time>{message.timestamp}</time>
                                    </div>
                                </div>
                            ))
                        )}

                        {isTyping && (
                            <div className="ai-message-row ai">
                                <div className="ai-avatar ai">
                                    <CustomIcon name="brain" size={18} color="currentColor" accent="none" />
                                </div>
                                <div className="ai-message-body">
                                    <div className="ai-bubble ai ai-typing-bubble">
                                        <div className="typing-indicator"><span /><span /><span /></div>
                                    </div>
                                    <time>Формирует ответ</time>
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="ai-input-wrapper">
                        <div className="ai-input-box">
                            <textarea
                                ref={inputRef}
                                value={inputValue}
                                onChange={(event) => {
                                    setInputValue(event.target.value);
                                    event.currentTarget.style.height = 'auto';
                                    event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 144)}px`;
                                }}
                                onKeyDown={handleKeyDown}
                                placeholder="Напишите сообщение..."
                                aria-label="Вопрос для AI-ассистента"
                                className="custom-scroll ai-textarea"
                                rows={1}
                            />
                            <button
                                type="button"
                                onClick={() => handleSendMessage(inputValue)}
                                disabled={!inputValue.trim() || isTyping}
                                className="ai-send-btn"
                                aria-label="Отправить вопрос"
                            >
                                <CustomIcon name="send" size={19} color="currentColor" accent="none" />
                                <span>Отправить</span>
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            {showClearConfirm && (
                <div className="ai-confirm-overlay" role="presentation" onClick={() => setShowClearConfirm(false)}>
                    <section className="ai-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="ai-clear-history-title" onClick={(event) => event.stopPropagation()}>
                        <span className="ai-confirm-icon"><CustomIcon name="alert" size={30} color="currentColor" /></span>
                        <span className="vates-eyebrow">Необратимое действие</span>
                        <h2 id="ai-clear-history-title">Удалить всю историю?</h2>
                        <p>Все диалоги AI-ассистента будут удалены. Восстановить их после подтверждения не получится.</p>
                        <div className="ai-confirm-actions">
                            <button type="button" className="vates-button secondary" onClick={() => setShowClearConfirm(false)}>Отмена</button>
                            <button type="button" className="vates-button danger" onClick={clearHistory}>Удалить историю</button>
                        </div>
                    </section>
                </div>
            )}

            <style jsx global>{`
                .ai-monolithic-section { margin: -20px -60px -60px -60px; height: calc(100vh - 100px); display: flex; flex-direction: column; }
                .ai-monolithic-container { display: flex; flex: 1; background: transparent; height: 100%; }
                .ai-sidebar { width: 300px; border-right: 1px solid #1a1a1a; display: flex; flex-direction: column; background: transparent; transition: 0.3s ease; }
                .ai-session-item { position: relative; padding: 14px 15px; margin-bottom: 8px; cursor: pointer; border: 1px solid transparent; transition: all 0.2s ease; display: flex; justify-content: space-between; align-items: center; border-radius: 12px; }
                .ai-session-item.active { background: #1a1a1a; border-color: #333; }
                .ai-session-item:hover { border-color: #ff4d4d !important; box-shadow: 0 0 10px rgba(255, 77, 77, 0.15); background: rgba(255, 77, 77, 0.02); }
                .ai-session-title { color: #aaa; font-size: 14px; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; padding-right: 10px; }
                .ai-session-item.active .ai-session-title { color: #0abab5; }
                
                .ai-session-actions { display: flex; align-items: center; gap: 8px; opacity: 0; transition: opacity 0.2s ease; }
                .ai-session-item:hover .ai-session-actions { opacity: 1; }
                .ai-session-actions.pinned { opacity: 1; }

                .ai-pin-btn { background: transparent; border: none; cursor: pointer; color: #555; transition: all 0.2s ease; padding: 2px; display: flex; align-items: center; justify-content: center; }
                .ai-pin-btn:hover { color: #ffd700; transform: scale(1.15); }
                .ai-pin-btn.active { color: #ffd700; }

                .ai-session-del-btn { background: transparent; border: none; color: #ff4d4d; cursor: pointer; transition: transform 0.1s ease, color 0.2s ease; padding: 2px; display: flex; align-items: center; justify-content: center; }
                .ai-session-del-btn:hover { color: #ff1a1a; }
                .ai-session-del-btn:active { transform: scale(0.85); } 

                .ai-chat-area { flex: 1; display: flex; flex-direction: column; background: transparent; position: relative; }
                .ai-mobile-header { display: none; }
                .ai-messages { flex: 1; overflow-y: auto; padding: 40px; display: flex; flex-direction: column; }
                .ai-empty-state { margin: auto; text-align: center; max-width: 550px; }
                .quick-prompts-container { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; }
                .quick-prompt { background: #111; border: 1px solid #222; padding: 12px 18px; border-radius: 20px; color: #0abab5; font-size: 14px; font-weight: 800; cursor: pointer; transition: 0.2s; }
                .quick-prompt:hover { background: rgba(10,186,181,0.05); border-color: #0abab5; transform: translateY(-2px); }
                .ai-avatar { width: 40px; height: 40px; border-radius: 12px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; font-size: 20px; }
                .ai-avatar.user { background: #0abab5; color: #000; }
                .ai-avatar.ai { background: #161816; border: 1px solid #333; }
                 .ai-bubble { padding: 16px 22px; border-radius: 20px; font-size: 15px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
                 .ai-message-link { color: #45c7c2; font-weight: 800; text-decoration: underline; text-underline-offset: 3px; white-space: nowrap; }
                 .ai-message-link:hover { color: #8ce5e0; }
                 .ai-bubble.user { background: rgba(10,186,181,0.1); border: 1px solid rgba(10,186,181,0.3); color: #fff; border-bottom-right-radius: 6px; }
                .ai-bubble.ai { background: #111; border: 1px solid #222; color: #ddd; border-bottom-left-radius: 6px; }
                .ai-input-wrapper { padding: 20px 40px 30px 40px; background: transparent; }
                .ai-input-box { position: relative; display: flex; align-items: flex-end; background: #111; border: 1px solid #222; border-radius: 24px; padding: 6px; transition: 0.2s; }
                .ai-input-box:focus-within { border-color: #0abab5; box-shadow: 0 0 15px rgba(10,186,181,0.1); }
                .ai-textarea { flex: 1; background: transparent; border: none; color: #fff; padding: 16px 20px; font-size: 16px; outline: none; resize: none; max-height: 150px; min-height: 56px; font-family: inherit; }
                .ai-send-btn { margin: 8px; width: 44px; height: 44px; border-radius: 16px; border: none; display: flex; align-items: center; justify-content: center; transition: 0.2s; }
                .ai-send-btn:not(:disabled) { background: #0abab5; color: #000; cursor: pointer; }
                .ai-send-btn:not(:disabled):hover { transform: scale(1.05); }
                .ai-send-btn:disabled { background: #1a1a1a; color: #555; cursor: not-allowed; }
                .ai-footer-text { text-align: center; font-size: 12px; color: #555; margin-top: 12px; }
                .typing-indicator { display: flex; gap: 4px; padding: 2px 0; }
                .typing-indicator span { width: 8px; height: 8px; background: #0abab5; border-radius: 50%; animation: bounce 1.4s infinite ease-in-out both; }
                .typing-indicator span:nth-child(1) { animation-delay: -0.32s; }
                .typing-indicator span:nth-child(2) { animation-delay: -0.16s; }
                .ai-mobile-overlay { display: none; }
                @media (max-width: 768px) {
                    .ai-monolithic-section { margin: -10px -15px -50px -15px; height: calc(100vh - 70px); }
                    .ai-mobile-header { display: flex; justify-content: space-between; align-items: center; padding: 15px 20px; border-bottom: 1px solid #1a1a1a; background: #0a0a0a; }
                    .ai-history-btn { display: inline-flex; align-items: center; gap: 8px; background: rgba(10,186,181,0.1); color: #0abab5; border: 1px solid rgba(10,186,181,0.3); padding: 8px 16px; border-radius: 10px; font-weight: 800; font-size: 13px; transition: transform 0.16s ease, box-shadow 0.16s ease, background 0.16s ease, border-color 0.16s ease, color 0.16s ease; }
                    .ai-history-btn:hover { transform: translateY(1px) scale(0.985); box-shadow: inset 0 2px 6px rgba(0,0,0,0.18), 0 0 0 1px rgba(10, 186, 181, 0.24); background: rgba(10,186,181,0.14); border-color: rgba(10,186,181,0.45); color: #fff; }
                    .ai-history-btn:active { transform: translateY(2px) scale(0.97); box-shadow: inset 0 3px 8px rgba(0,0,0,0.24); }
                    .ai-sidebar { position: fixed; top: 0; left: -300px; width: 280px !important; height: 100vh; background: #000; z-index: 10006; box-shadow: 10px 0 30px rgba(0,0,0,0.8); }
                    .ai-sidebar.open { left: 0; }
                    .ai-mobile-overlay { display: block; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.7); z-index: 10005; backdrop-filter: blur(4px); }
                    .ai-messages { padding: 20px 15px; }
                    .ai-input-wrapper { padding: 15px; }
                    .quick-prompt { font-size: 13px; padding: 10px 14px; }
                    .ai-bubble { font-size: 14px; padding: 12px 16px; }
                }
            `}</style>
        </section>
    );
}

````
