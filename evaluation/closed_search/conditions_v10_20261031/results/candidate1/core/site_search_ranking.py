"""Веса поискового вопроса: отделяем предмет от служебной формулировки.

Здесь нет названий документов, фактов или ответов. Слова исходного правила
не изменяются: уменьшение веса применяется только при выборе доказательства.
"""
import re


LABEL = re.compile(r'(?:штамп\w*|пометк\w*|отметк\w*|надпис\w*|маркировк\w*|ярлык\w*)\s*[:—-]?\s*[«"]([^»"\n]{1,160})[»"]', re.I)


def matching_label_condition(question, text, tokenize):
    """Отметка в вопросе должна соответствовать отметке из правила.

    «Не подлежит копированию» и «не копировать» — одна операция с отрицанием.
    Противоположная или неизвестная надпись не позволяет применить этот запрет.
    Сравнение используется только при отборе; цитаты остаются дословными.
    """
    requested=list(LABEL.finditer(question))
    if not requested:return True
    stated=list(LABEL.finditer(text))
    if not stated:return False

    def signature(label):
        value=label.casefold().replace('ё','е')
        value=re.sub(r'\bне\s+подлеж\w*\s+','не ',value)
        value=re.sub(r'\bне\s+(?:запрещено|возбраняется)\b','разрешено',value)
        value=re.sub(r'\b(?:нельзя|запрещено)\b','не',value)
        negative=bool(re.search(r'\bне\b',value))
        value=re.sub(r'\b(?:разрешено|допускается|можно)\b','',value)
        words=set(tokenize(value))
        # Ограниченная нормализация названия действия, без удаления других
        # свойств отметки («частично», «обложку» и т. п. остаются значимыми).
        for variants in ('копировать копирование', 'фотографировать фотографирование фотосъемка'):
            group=set(tokenize(variants))
            if words & group:
                words=(words-group)|{sorted(group)[0]}
        return negative,frozenset(words)

    available={signature(match[1]) for match in stated}
    for match in requested:
        prefix=question[max(0,match.start()-35):match.start()]
        if re.search(r'\b(?:без|нет|отсутствует)\s*$',prefix,re.I):
            return False
        if signature(match[1]) not in available:return False
    return True


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

def question_weights(words, tokenize, question=''):
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
    # «Где и в каком состоянии хранить до следующего использования?»
    # спрашивает условия, а не точный срок. Уменьшаем вес только формы
    # такого запроса; числа, температурные пределы и названия не исчезают.
    # Для «можно ли хранить до ...» этот переход неприменим: срок существенен.
    storage_conditions=bool(re.search(r'\bхран\w*',question,re.I)
                            and re.search(r'\bгде\b|в каком состоянии|каки\w* услови',question,re.I)
                            and not re.search(r'можно|допустим|разреш|сколько|как долго|срок',question,re.I))
    if storage_conditions:
        framing_phrases=re.findall(r'в каком состоянии|каки\w* услови\w*|'
            r'до\s+следующ\w*\s+(?:сезон\w*|использовани\w*|применени\w*|включени\w*)',question,re.I)
        for word in tokenize(' '.join(framing_phrases)):
            if word in weights:weights[word]=0.15
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
        'фотографировать сфотографировать фотографирование фотосъемка',
        'копировать копирование',
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
