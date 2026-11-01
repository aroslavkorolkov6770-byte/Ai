"""Поиск характеристик в геометрически подтверждённых руководствах.

Словарь связывает способы задать вопрос с названием характеристики.
Значений, названий изделий и ответов проверочного набора в нём нет.
Результат всегда содержит исходную цитату с условиями и редакцией.
"""
from __future__ import annotations
import re

NO_DATA = 'В доступных материалах я не нашёл информации, которая однозначно отвечает на этот вопрос.'

RELATIONS = (
    ('purpose', r'для чего|зачем|назначени', r'предназначен\w* для (?!ознакомления)'),
    ('power', r'потребля|потреблен', r'потребляемая мощность'),
    ('poll', r'опраш|опрос', r'время опроса'),
    ('warmup', r'установлен.*режим|прогрев', r'время установления рабочего режима'),
    ('mass', r'масс|вес', r'масса'),
    ('dimensions', r'габарит|размер', r'габаритные размеры'),
    ('protection', r'степен.*защит|защит.*корпус', r'степень защиты корпуса'),
    ('data_bits', r'бит.*данн', r'количество бит данных'),
    ('protocol', r'протокол', r'протокол обмена'),
    ('temperature', r'температур.*(?:работ|эксплуатац|воздух)|(?:работ|эксплуатац).*температур', r'температура окружающего воздуха'),
    ('humidity', r'влажност', r'(?:относительной )?влажности воздуха'),
    ('lifespan', r'срок.*служ|долговеч', r'срок службы'),
    ('repair', r'ремонт|бесплатн.*гарант', r'бесплатный ремонт|ремонт.*бесплатн'),
    ('warranty', r'гарант', r'гарантийный срок'),
    ('storage', r'хран|склад', r'хранить'),
    ('channels', r'(?:сколько|количеств).*канал', r'количество.*канал|по \w+ независимым канал'),
    ('voltage', r'напряжени', r'напряжени'),
)


def relation(question):
    q = question.casefold().replace('ё', 'е')
    return next(((name, evidence) for name, query, evidence in RELATIONS if re.search(query, q)), None)


def evidence(row):
    passage, quote = row
    return {'id': passage.source['id'], 'revision': passage.source['revision'], 'heading': passage.heading, 'quote': quote}


def variant(question):
    match = re.search(r'\bкорпус\w*\s+[«"]?([А-ЯA-Z]{1,3}\d*)\b|\bв\s+(Щ\d+)\b', question)
    return next((part for part in match.groups() if part), None) if match else None


def accepts_variant(quote, wanted):
    # «кроме корпуса Д» не является характеристикой корпуса Д.
    escaped = re.escape(wanted)
    if re.search(r'кроме корпуса\s+' + escaped + r'\b', quote, re.I):
        return False
    return bool(re.search(r'(?<!\w)' + escaped + r'(?!\w)', quote, re.I))


def manual_quotes(passage):
    if re.search(r'таблиц', passage.heading, re.I):
        return [passage.text]
    # Не разрываем условие «при соблюдении ...» и действие из одного
    # предложения. Явные пункты списка сохраняют своё полное ограничение.
    parts = re.split(r'\s*[•]\s*|(?<=[.!?])\s+(?=[А-ЯЁ])', passage.text)
    return [part.strip() for part in parts if len(part.strip()) > 15]


def search_manual(question, candidates):
    empty = {'status': 'not_found', 'text': NO_DATA, 'links': [], 'evidence': []}
    named = re.findall(r'\b[А-ЯA-Z]{2,}\d+(?:[-.]\d+)*\b', question)
    available_text = ' '.join(p.source['title'] + ' ' + p.heading + ' ' + p.text for p in candidates)
    if any(not re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', available_text, re.I) for name in named):
        # Выбранный файл не даёт права отвечать за другое явно названное изделие.
        return empty
    if re.search(r'сейчас|сегодня|серийн|нашей компании|кто из сотрудников', question, re.I):
        return empty
    selected_relation = relation(question)
    if not selected_relation:
        return empty
    kind, pattern = selected_relation
    wanted = variant(question)
    rows, blocked = [], False
    for passage in candidates:
        for quote in manual_quotes(passage):
            if not re.search(pattern, quote, re.I):
                continue
            if wanted and kind in {'dimensions', 'protection'} and not accepts_variant(quote, wanted):
                continue
            if kind == 'mass':
                if re.search(r'без упаков', question, re.I) and not re.search(r'без упаков', quote, re.I):
                    continue
                if re.search(r'\bс упаков', question, re.I) and not re.search(r'\bс упаков', quote, re.I):
                    continue
            if kind == 'voltage':
                if re.search(r'внешн\w* источник', quote, re.I) and not re.search(r'внешн', question, re.I):
                    # Питание внешнего оборудования не подменяет питание прибора.
                    continue
                if re.search(r'питани', question, re.I) and not re.search(r'питани', quote, re.I):
                    continue
                if re.search(r'диапазон', question, re.I) and not re.search(r'диапазон', quote, re.I):
                    continue
                if re.search(r'номинальн', question, re.I) and not re.search(r'номинальн', quote, re.I):
                    continue
            if kind in {'voltage', 'power'}:
                if re.search(r'постоянн', question, re.I) and not re.search(r'постоянн', quote, re.I):
                    continue
                if re.search(r'переменн', question, re.I) and not re.search(r'переменн', quote, re.I):
                    continue
            if kind == 'protection':
                if re.search(r'спереди|лицев', question, re.I) and not re.search(r'лицев', quote, re.I):
                    continue
                if re.search(r'сзади|задн', question, re.I) and not re.search(r'задн', quote, re.I):
                    continue
            if kind == 'temperature' and not re.search(r'транспорт|хран', question, re.I) and re.search(r'транспорт|хран', passage.heading, re.I):
                continue
            if kind == 'temperature' and re.search(r'хран', question, re.I) and not re.search(r'хран', quote, re.I):
                continue
            if kind == 'temperature' and re.search(r'транспорт|перевоз', question, re.I) and not re.search(r'транспорт|перевоз', quote, re.I):
                continue
            if kind == 'purpose' and re.search(r'руководство|кнопк|разъем|интерфейс|датчик предназначен|предназначен\w* для эксплуатации', quote, re.I):
                continue
            if kind == 'dimensions' and not re.search(r'\d+\s*[×хx]\s*\d+\s*[×хx]\s*\d+', quote):
                # Номер рисунка в подписи не является размером устройства.
                continue
            if kind in {'dimensions', 'mass', 'power', 'poll', 'warmup', 'temperature', 'humidity', 'lifespan', 'warranty', 'voltage', 'protection', 'data_bits'} and not re.search(r'\d', quote):
                continue
            if (passage.structure or {}).get('issue'):
                blocked = True
                continue
            rows.append((passage, quote))
    # Одинаковый факт может повторяться; это не конфликт редакции.
    rows = list({quote: (passage, quote) for passage, quote in rows}.values())
    if not rows:
        return ({'status': 'unavailable', 'text': 'Не удалось проверить все условия этого правила. Обратитесь к администратору для уточнения документа.', 'links': [], 'evidence': []}
                if blocked else empty)
    if kind == 'channels':
        numbers = {}
        words = {'одному': '1', 'двум': '2', 'трем': '3', 'четырем': '4'}
        for row in rows:
            match = re.search(r'каналов\s*:\s*(\d+)|по (\w+) независимым канал', row[1], re.I)
            if match:
                value = match[1] or words.get(match[2].casefold().replace('ё', 'е'))
                if value:
                    numbers[value] = row
        if len(numbers) > 1:
            selected = list(numbers.values())
            return {'status': 'conflict', 'text': 'В руководстве есть противоречие: ' + ' '.join(row[1] for row in selected) + ' Уточните верное значение у администратора.',
                    'links': [], 'evidence': [evidence(row) for row in selected]}
    if len(rows) > 1 and kind in {'mass', 'dimensions', 'protection', 'voltage'}:
        return {'status': 'clarify', 'text': 'Уточните исполнение устройства и нужный вариант характеристики.', 'links': [], 'evidence': []}
    # Таблица параметров сильнее случайного упоминания в тексте настройки.
    rows.sort(key=lambda row: (0 if re.search(r'таблиц', row[0].heading, re.I) else 1, len(row[1])))
    passage, quote = rows[0]
    return {'status': 'answer', 'text': quote, 'evidence': [evidence(rows[0])],
            'links': [{'title': passage.source['title'], 'url': passage.source['url']}], 'context_used': False}
