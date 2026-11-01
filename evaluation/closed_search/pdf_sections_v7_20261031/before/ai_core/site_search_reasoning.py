"""Проверяемые операции над вопросами и фактами без генерации новых правил.

Этот слой не заменяет понимание языка: он обрабатывает явные части вопроса,
известные виды утверждений и доверенные метаданные источника.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


QUESTION_START = r"(?:кому|кто|сколько|как|где|когда|что|зачем|почему|можно ли|до какого времени|в какой срок|при какой температуре)"
QUESTION_WORDS = set("кому кто сколько как где когда что зачем почему можно до в при какой какая какие какое а и если для это он она его ее её".split())


def explicit_names(text: str) -> list[str]:
    """Заглавное слово в начале предложения само по себе не является именем."""
    names = []
    for match in re.finditer(r'\b[А-ЯЁA-Z][а-яёa-zА-ЯЁA-Z0-9-]+',text):
        prefix = text[:match.start()].rstrip()
        if not prefix or prefix[-1:] in '.!?':
            continue
        if match.group().casefold() not in QUESTION_WORDS:
            names.append(match.group())
    return list(dict.fromkeys(names))


def question_context(text: str) -> tuple[str,str]:
    """Отделяем факты перед вопросом; их смысл остаётся доступен проверке условий."""
    match = re.search(rf'(?:^|[.!?]\s+)({QUESTION_START})\b',text,re.I)
    if match and match.start(1)>0:
        return text[:match.start(1)].strip(),text[match.start(1):].strip()
    return '',text


def lookup_question(text: str) -> str:
    facts, focus = question_context(text)
    names = explicit_names(facts)
    # «Заказ Аврора оплачен. Сколько дней он хранится?» всё ещё про Аврору.
    if len(names)==1 and not explicit_names(focus):
        focus = focus.rstrip(' ?. ')+' '+names[0]+'?'
    # «Кофемашина сломалась. Кому сообщить?» не содержит предмета во
    # второй фразе: описание случая по-прежнему нужно для поиска.
    focus_tokens = set(re.findall(r'\w+',focus.casefold()))
    generic_actions = set('сообщить сказать обратиться делать сделать долго дней день ждать'.split())
    if facts and not (focus_tokens - QUESTION_WORDS - generic_actions):
        return text
    # Вводная описывает предмет вопроса: «Ошибка в чеке. Кто её исправляет?».
    # Общая просьба о действии или ответственном не становится новой темой.
    # Явное название другого предмета по-прежнему имеет приоритет.
    if facts and not explicit_names(focus) and re.fullmatch(
            r'(?:кто\s+(?:(?:е[её]|его|их|это)\s+)?(?:исправ\w*|оформ\w*|делает|отвечает)(?:\s+(?:исправление|это))?'
            r'|что\s+(?:делать|сделать)(?:\s+(?:сотруднику|продавцу|мне|нам))?)\s*[?.!]*', focus, re.I):
        return text
    return focus


def compound_parts(question: str) -> list[str]:
    """Разделяем только явные вопросы, а не обычное перечисление объектов."""
    parts = re.split(rf"(?:\?\s+|[;,]\s*|\s+(?:и|а также)\s+)(?={QUESTION_START}\b)", question.strip(), flags=re.I)
    if len(parts) == 1:
        return parts
    facts, parts[0] = question_context(parts[0])
    # «Кому и в какой срок направить заявку?» имеет общий хвост предложения.
    if re.fullmatch(QUESTION_START, parts[0], re.I):
        tail = re.sub(rf"^{QUESTION_START}\s+", "", parts[1], count=1, flags=re.I)
        parts[0] += " " + tail
    names = explicit_names(question)
    # Единственный явно названный объект относится и к соседней части вопроса.
    # При нескольких объектах ничего не угадываем и не склеиваем их правила.
    if len(names) == 1:
        name = names[0]
        parts = [part if name.casefold() in part.casefold() else part.rstrip(" ?.") + " " + name for part in parts]
    result = []
    fact_names = set(explicit_names(facts))
    for part in parts:
        # Общий факт переносится вместе с вопросом. Факт об одном явно
        # названном товаре не становится фактом о другом товаре.
        shared = facts if not fact_names or fact_names.issubset(set(explicit_names(part))) else ''
        result.append((shared+' '+part.strip(' ,;?.')+'?').strip())
    return result


def authority(source: dict) -> int:
    """Официальность задаёт сервер сайта, а не название или текст файла."""
    return 2 if source.get("authority") == "official" else 1


def validate_authority(source: dict) -> None:
    value = source.get("authority", "reference")
    if value not in {"official", "reference"} or (value == "official" and source.get("kind") != "document"):
        raise ValueError("source_authority_invalid")


NUMBERS = {
    "один": "1", "одна": "1", "одного": "1", "одной": "1",
    "два": "2", "две": "2", "двух": "2", "три": "3", "трех": "3",
    "четыре": "4", "четырех": "4", "пять": "5", "пяти": "5",
    "шесть": "6", "шести": "6", "семь": "7", "семи": "7",
    "восемь": "8", "восьми": "8", "девять": "9", "девяти": "9",
    "десять": "10", "десяти": "10", "одиннадцать": "11", "одиннадцати": "11",
    "двенадцать": "12", "двенадцати": "12",
}
RELATIONS = (
    ("storage", r"хран\w*"), ("processing", r"обработ\w*"),
    ("cancellation", r"отмен\w*|подтвержд\w*"), ("delivery", r"достав\w*"),
    ("repair", r"ремонт\w*|чинить|почин\w*"), ("return", r"возврат\w*|вернуть|возвращ\w*"),
)
NOISE = r"\b(?:срок\w*|длительност\w*|составля\w*|течени\w*|длит\w*|занима\w*|равен|равно|можно|нельзя|разреш\w*|запрещ\w*|допуска\w*|хран\w*|обработ\w*|отмен\w*|подтвержд\w*|достав\w*|ремонт\w*|чинить|почин\w*|возврат\w*|вернуть|возвращ\w*|производ\w*|выполня\w*|осуществля\w*|календарн\w*|рабоч\w*|день|дня|дней|дни|час\w*|минут\w*|суток|сутки)\b"


@dataclass(frozen=True)
class Claim:
    relation: str
    subject: frozenset[str]
    condition: str
    value: tuple


def claims(sentence: str, tokenize) -> list[Claim]:
    """Выделяем срок и разрешение/запрет; условия сохраняем отдельно.

    Разные условия не объявляем противоречием. Сложные исключения и неясные
    числовые диапазоны не сводим к одному числу.
    """
    value = sentence.casefold().replace("ё", "е")
    value = re.sub(r"\b(" + "|".join(NUMBERS) + r")\b", lambda m: NUMBERS[m.group()], value)
    condition = ""
    conditional = re.match(r"(если\b[^,]+|при\b[^,]+),\s*(.+)", value)
    if conditional:
        condition, value = conditional.groups()
    # Уточнения «после уведомления», «в холодильнике» и т. п. тоже существенны.
    qualifier = re.search(r"\b(?:после|до|при|если|в холодильнике|в помещении|на улице)\b.*", value)
    if qualifier:
        condition += " " + qualifier.group()
        value = value[:qualifier.start()]
    # Сохраняем связки и отрицание: поиск отбрасывает их как стоп-слова,
    # но для сравнения условий «оплачен» и «не оплачен» принципиально различны.
    condition = " ".join(condition.split())
    relation = next((name for name, pattern in RELATIONS if re.search(pattern, value)), None)
    if relation is None:
        return []
    quantities = re.findall(r"\b(\d+)\s*(рабоч\w*|календарн\w*)?\s*(день|дня|дней|дни|суток|сутки|час\w*|минут\w*)\b", value)
    # Не упрощаем диапазоны, сроки «до» и составные числительные.
    uncertain_number = bool(re.search(r"\d\s*[-–—]\s*\d|\bне (?:менее|более)|\bот\s+\d|\d\s+\d", value))
    subject_text = re.sub(NOISE, " ", value)
    subject_text = re.sub(r"\d+(?:[.,]\d+)?", " ", subject_text)
    subject = frozenset(tokenize(subject_text))
    if not subject:
        return []
    result = []
    if len(quantities) == 1 and not uncertain_number:
        number, calendar, unit = quantities[0]
        unit = "day" if unit.startswith(("д", "сут")) else "hour" if unit.startswith("час") else "minute"
        calendar = "working" if calendar.startswith("рабоч") else "calendar" if calendar else "unspecified"
        result.append(Claim("duration:" + relation, subject, condition, (int(number), unit, calendar)))
    not_forbidden = bool(re.search(r"\bне запрещ", value))
    forbidden = bool(re.search(r"\bнельзя|\bне (?:разреш|допуска)", value)) or (bool(re.search(r"\bзапрещ", value)) and not not_forbidden)
    permitted = (not_forbidden or bool(re.search(r"\bможно|\bразреш|\bдопуска", value))) and not forbidden
    if re.search(r"\bможно не\b", value):
        permitted = False  # Право не выполнять действие не является разрешением выполнить его.
    if forbidden or permitted:
        result.append(Claim("permission:" + relation, subject, condition, (not forbidden,)))
    return result


def contradictory(left: Claim, right: Claim) -> bool:
    if left.relation != right.relation or left.condition != right.condition:
        return False
    # Строго совпадающий предмет уменьшает риск смешать сходные товары/операции.
    if left.subject != right.subject:
        return False
    return left.value != right.value


def conflicting_passages(passages, sentences, tokenize, inherit_heading=False) -> bool:
    """Сравниваем только уже доступные и подходящие к вопросу фрагменты."""
    found = {}
    templates = {}
    for passage in passages:
        for sentence in sentences(passage.text):
            # Сохраняем прежнюю проверку дословного правила с разными числами.
            normalized = re.sub(r"[^\w\s.,]", " ", sentence.casefold().replace("ё", "е"))
            normalized = " ".join(normalized.split())
            numbers = tuple(re.findall(r"\b\d+(?:[.,]\d+)?\b", normalized))
            # Номера страниц описывают расположение, а не разные значения правила.
            pagination = bool(re.match(r'^(?:страница|стр\.|лист)\s+\d+\b', sentence, re.I))
            if numbers and not pagination and not re.search(r'\bесли\b|\bпри\b|исключени|только если', sentence, re.I):
                template = re.sub(r"\b\d+(?:[.,]\d+)?\b", "#", normalized)
                if template in templates and templates[template] != numbers:
                    return True
                templates[template] = numbers
            extracted = claims(sentence, tokenize)
            # Короткая строка «срок хранения — 2 дня» может называть предмет
            # только в заголовке раздела. Заголовок нужен для сравнения,
            # исходная цитата в ответе остаётся неизменной.
            if not extracted and inherit_heading and not re.search(r'\.(?:md|txt|pdf|docx?)$',passage.heading,re.I):
                relation = next((pattern for _,pattern in RELATIONS if re.search(pattern,sentence,re.I)),None)
                if relation and re.search(relation,passage.heading,re.I):
                    extracted = claims(passage.heading+' '+sentence,tokenize)
            for claim in extracted:
                key = (claim.relation, claim.subject, claim.condition)
                if key in found and contradictory(claim, found[key]):
                    return True
                found[key] = claim
    return False
