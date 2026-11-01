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
