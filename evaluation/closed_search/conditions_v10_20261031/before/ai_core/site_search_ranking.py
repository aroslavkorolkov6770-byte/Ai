"""Веса поискового вопроса: отделяем предмет от служебной формулировки.

Здесь нет названий документов, фактов или ответов. Слова исходного правила
не изменяются: уменьшение веса применяется только при выборе доказательства.
"""
import re


def requested_action(question):
    """Начало членства/учётной записи отличается от разового посещения.

    Уточняем только явно названную операцию. Возраст, документы и остальные
    условия берутся из найденного материала, а не из этой классификации.
    """
    if re.search(r'стать\s+(?:пользовател|читател|участник|клиент)|зарегистр\w*|\bрегистрац\w*|\bзаписаться\b',question,re.I):
        return re.compile(r'\bрегистрац\w*|зарегистр\w*|\bзапис\w*',re.I)
    # Одинаковые существительные («упаковка», «прибор») встречаются в разных
    # действиях. Явно заданное действие нельзя заменять похожим предметом.
    for question_pattern,evidence_pattern in (
        (r'перевоз\w*|транспортир\w*',r'перевоз\w*|транспорт\w*|перемещ\w*'),
        (r'\bхран\w*',r'\bхран\w*'),
        (r'\bчист\w*|\bочист\w*',r'\bчист\w*|\bочист\w*'),
        (r'фотограф\w*|фотосъ[её]м\w*|сфотограф\w*',r'фотограф\w*|фото.?съ[её]м\w*|фото-?\s*и\s*видеосъ[её]м\w*'),
    ):
        if re.search(question_pattern,question,re.I):return re.compile(evidence_pattern,re.I)
    return None


def complete_conditional_quote(text, answer, items):
    """Не теряем действие после единственного применённого условия.

    Допускается только хвост того же пункта без второй ветки/исключения.
    Неопределённые или неприменимые условия остаются у прежнего обработчика.
    """
    if not items or len(text)>1800 or answer.get('status')!='answer':return answer
    first,*tail=items
    if not first.condition or answer.get('text')!=first.quote or not tail:return answer
    if any(item.condition or item.exception for item in tail):return answer
    if re.search(r'в остальных|в противном|иначе|исключени', ' '.join(item.quote for item in tail),re.I):return answer
    result={**answer,'text':text}
    if len(answer.get('evidence',[]))==1:
        result['evidence']=[{**answer['evidence'][0],'quote':text}]
    return result

def question_weights(words, tokenize):
    """Служебное слово не должно перевешивать единственный названный предмет.

    «Разрешает ли инструкция пользоваться переходником» ищет переходник,
    а не вводный абзац со словом «инструкция». Вес не нулевой: короткий запрос
    из одних общих слов остаётся доступен прежней проверке неоднозначности.
    """
    framing=set(tokenize(
        'инструкция руководство правило раздел пункт согласно данный этот '
        'разрешает разрешать допускает позволять запрещено запрещает можно нужно нужен '
        'пока через стать стоять узнать указано приводится использование'
    ))
    weights={word:(0.15 if word in framing else 1.0) for word in words}
    if weights and all(value<1 for value in weights.values()):
        return {word:1.0 for word in words}
    return weights


def body_matches(words, positions, matching, tokenize):
    """Близкие названия одного действия ищем без подстановки готового ответа.

    Словоформы «чистить» и «очистка» имеют разные основы. Добавляем ограниченные
    смысловые эквиваленты только в новом профиле, не меняя цитату и числа.
    Точное совпадение всегда сильнее такого расширения.
    """
    concepts=[set(tokenize(text)) for text in (
        'чистить чистка очистка очищать',
        'перевозка перевозить транспортировка транспортировать',
        'регистрация регистрироваться зарегистрироваться запись записаться',
    )]
    result={}
    for word in words:
        result[word]=dict(matching(word,positions,body_only=True))
        alternatives=set().union(*(group for group in concepts if word in group))-{word}
        for alternate in alternatives:
            for position,(similarity,body,token) in matching(alternate,positions,body_only=True).items():
                value=(similarity*0.95,body,token)
                if value>result[word].get(position,(0.0,False,'')):result[word][position]=value
    return result


def supported_positions(words, matches, weights, minimum):
    """Покрытие считаем по содержанию и весам, а не числу общих слов."""
    total=sum(weights[word] for word in words) or 1
    coverage={}
    for word in words:
        for position,(similarity,body,_) in matches[word].items():
            if body:
                coverage[position]=coverage.get(position,0)+weights[word]*similarity/total
    return {position:value for position,value in coverage.items() if value>=minimum}
