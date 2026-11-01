"""Небольшой словарь русских поисковых понятий; фактов и готовых ответов здесь нет."""
import re
from .site_search_reasoning import NUMBERS

# Выражения нормализуются только для поиска. Исходный текст ответа сохраняется.
PHRASES = (
    (r"\bкак\s+долго\s+(?:можно\s+)?держать\b", "сколько хранится"),
    (r"\bухожу\s+после\s+смены\b|\bконец\s+рабочего\s+дня\b", "закрытие"),
    (r"\bне\s+(?:работает|включается)\b|\bперестал[аио]?\s+работать\b", "поломка"),
    (r"\b(?:надеть|одеть)\s+на\s+руки\b", "перчатки"),
    (r"\bнаведения?\s+порядка\b", "уборка"),
    (r"\bсовсем\s+(?:немного|мало)\b", "минимальный"),
    (r"\bпоздоровал\w*\s+за\s+руку\b", "рукопожатие"),
)

SYNONYMS = {
    "наличные": ["купюра", "купюры", "банкнота", "наличные"],
    "мыть": ["мыть", "вымыть", "помыть", "мытьё"],
    "улица": ["улица", "снаружи"],
    "помещение": ["помещение", "внутри"],
    "взвешивание": ["взвешивание", "взвесить", "взвешивать", "отвесить", "вразвес", "весовой"],
    "грамм": ["грамм", "граммы", "г"],
    "день": ["день", "дня", "дней", "дни"],
    "человек": ["человек", "родственник", "родственники"],
    "использовать": ["использовать", "использоваться", "пользоваться"],
    "один": ["один", "одним"],
    "ремонт": ["ремонт", "починить", "ремонтировать"],
    "самостоятельный": ["самостоятельный", "самостоятельно", "самому"],
    "поломка": ["поломка", "сломалась", "сломанный", "сломался"],
    "выключить": ["выключить", "выключаются", "обесточивать", "обесточить"],
    "обязательно": ["обязательно", "обязан", "обязаны"],
}


def search_language(text: str) -> str:
    value = text.casefold().replace('ё', 'е')
    for pattern, replacement in PHRASES:
        value = re.sub(pattern, replacement, value)
    # Утрата накопленных прав/баллов — отдельный смысл, не физическое горение.
    if re.search(r"скидк|бонус|балл|накоплен", value):
        value = re.sub(r"\b(?:пропад\w*|исчез\w*|сгора\w*)\b", "истечение", value)
    return value


def cleaning_agent_statement(text: str, forbidden: bool) -> bool:
    """Средство связано с глаголом применения, а не с «до/после очистки».

    Ищем структуру фразы без перечня конкретных веществ из документов.
    Запрет и отсутствие запрета различаются; исходная цитата не меняется.
    """
    clean=r'(?:чистк\w*|очистк\w*|очищ\w*)'
    predicate=r'(?:использ(?:у|ова)\w*|примен\w*)'
    for original in re.split(r'(?<=[.!?])\s+(?=[А-ЯA-Z])',text):
        sentence=original.casefold().replace('ё','е')
        means=(re.search(predicate+r'[^.!?]{1,400}(?:для|при)\s+'+clean,sentence)
               or re.search(r'(?:для|при)\s+'+clean+r'[^.!?]{0,90}'+predicate+r'\s+\S+',sentence)
               or re.search(r'(?:чистите|очищайте|чистить|очищать)\s+[^.!?]{1,160}(?:средств\w*|\w+(?:кой|ками|ткой|ством|ствами))\b',sentence))
        if not means:continue
        not_banned=bool(re.search(r'не\s+(?:запрещ\w*|возбраня\w*)',sentence))
        banned=not not_banned and bool(re.search(
            r'не\s+(?:использ(?:у|ова)\w*|примен\w*|чист\w*|очища\w*)|'
            r'(?:нельзя|запрещ\w*)[^.!?]{0,100}(?:использ\w*|примен\w*|чист\w*|очища\w*)',sentence))
        if banned==forbidden:return True
    return False


def requested_facet(question: str) -> str | None:
    value = question.casefold()
    if re.search(r'\bхран\w*',value) and re.search(r'в каком состоянии|как подготов\w*',value):
        # Запрос состояния отличается от срока годности или разрешения
        # хранить до определённой даты. Эти ограничения не ослабляются.
        if not re.search(r'можно|допустим|разреш|сколько|как долго|срок',value):
            return 'storage_conditions' if re.search(r'\bгде\b|в каком месте',value) else 'storage_state'
    if re.search(r'чист\w*|очищ\w*|очист\w*',value) and re.search(r'\bчем\b|каки\w*\s+(?:чистящ\w*\s+)?средств\w*',value):
        forbidden=not re.search(r'не\s+запрещ\w*',value) and re.search(r'запрещ\w*|нельзя|не\s+(?:разреш\w*|допуска\w*)',value)
        return 'forbidden_cleaning_agents' if forbidden else 'cleaning_agents'
    if re.search(r'в каких\s+(?:пакет|упаков)|какую\s+упаков|вариант\w*\s+(?:пакет|упаков)', value):
        return 'packaging'
    if re.search(r"сколько\s+(?:(?:сейчас|сегодня|примерно|будет)\s+)*(?:стоит|стоить)|стоимость|цена|почём|за какую сумму", value):
        return 'cost'
    if re.search(r"(?:с какого|в каком) год|год (?:начала|основания)", value):
        return 'year'
    if re.search(r"\bкому\b|кто (?:(?:его|её|ее|их) )?(?:выда|принима|обраб|отвечает|исправ|оформ)", value):
        return 'recipient'
    if re.search(r"как долго|какой срок|сколько.*(?:ждать|дн|хран|длит)|подожд[её]т.*(?:день|дня|недел)", value):
        return 'duration'
    if re.search(r"как (?:отмен|оформ|направ|отправ|перед|получ)", value):
        return 'procedure'
    return None


def supplies_facet(text: str, facet: str | None) -> bool:
    value = text.casefold()
    if facet in {'storage_state','storage_conditions'}:
        # Ищем указание подготовить/проверить состояние, а не единственное
        # слово «хранить». Конкретное состояние всегда берётся из источника.
        state=bool(re.search(r'(?:перед|для)\s+хранени\w*\s+[^.!?]{1,180}|'
                             r'(?:убедитесь|проверьте),?\s+что\b|'
                             r'\bв\s+[^.!?]{1,70}\s+(?:состоянии|виде)\b',value))
        place=bool(re.search(r'\bхран\w*\s+[^.!?]{0,100}\b(?:в|на|под)\s+\S+',value))
        return state and (facet!='storage_conditions' or place)
    if facet in {'cleaning_agents','forbidden_cleaning_agents'}:
        return cleaning_agent_statement(text,facet=='forbidden_cleaning_agents')
    if facet == 'packaging':
        return bool(re.search(r'пакет\w*|упаков\w*|банка|банку|коробк\w*', value))
    if facet == 'cost':
        return bool(re.search(r"\bруб\w*|₽|цен\w*|стоимост\w*|бесплатн\w*", value)) and not bool(re.search(r"не (?:указан|определ[её]н|извест|установлен)|нет (?:сведений|информации)", value))
    if facet == 'year':
        return bool(re.search(r"\b(?:19|20)\d{2}\b", text))
    if facet == 'recipient':
        return bool(re.search(r"координатор|диспетчер|старш\w* сотрудник|старш\w* смен|администратор|дежурн|менеджер|руководител|кладовщик|оператор|продавец|продавц|библиотекар", value))
    if facet == 'duration':
        return bool(re.search(r"\b(?:день|дня|дней|дни|суток|час\w*|минут\w*|недел\w*|месяц\w*|год\w*|лет)\b", value)) and bool(re.search(r"\d|\b(?:" + "|".join(NUMBERS) + r")\b", value.replace('ё', 'е')))
    if facet == 'procedure':
        return bool(re.search(r"отправ\w*|переда\w*|заполн\w*|запис\w*|показыв\w*|покаж\w*|сообщ\w*|подат\w*|звон\w*|обратит\w*", value))
    return True
