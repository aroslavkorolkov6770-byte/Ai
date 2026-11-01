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
