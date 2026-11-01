"""Предмет короткого уточнения берётся из доказанного ответа, а не угадывается.

В памяти остаются только обозначения из документа и известный вид вопроса.
Текст личных сообщений не сохраняется. Проверки прав и редакции выполняет индекс.
"""
from __future__ import annotations

import re
from .site_search_technical import anchors, normalized, render_quote, facets
from .site_search_reasoning import explicit_names

PROPERTIES = (
    ('масса', r'масс|вес'),
    ('габариты', r'габарит|размер|вхшхг'),
    ('сечение', r'сечени'),
    ('тепловыделение', r'тепловыдел|выдел.*тепл'),
    ('гарантия', r'гаранти'),
    ('срок службы', r'срок.*служ|долговеч'),
    ('температура эксплуатации', r'температур.*(?:работ|эксплуатац|окружающ)|(?:работ|эксплуатац).*температур'),
    ('влажность', r'влажност'),
    ('мощность', r'мощност|потребля'),
    ('напряжение', r'напряжени'),
    ('ток', r'\bток\b|по току'),
)


def property_name(question):
    found=[name for name,pattern in PROPERTIES if re.search(pattern,question,re.I)]
    return found[0] if len(found)==1 else None


def short_reference(question):
    text=question.strip()
    return len(text)<=120 and bool(re.match(r'^(?:а|и)\s|^(?:с какого момента|с упаковкой|без упаковки|с кожухом)\b',text,re.I)
                                  or re.search(r'\b(?:его|её|нее|него|этого|этой)\b',text,re.I))


def document_entities(quote):
    # В таблице имя строки — точное обозначение изделия. «Полюс питания»
    # обозначает цепь, поэтому его не превращаем в название всего устройства.
    match=re.search(r'Наименование(?! полюса)[^:;]*:\s*([^;\n]+)',quote,re.I)
    return [match[1].strip().rstrip('.')] if match else []


def remember_focus(question,result,passages):
    if result.get('status')!='answer' or not result.get('evidence'):
        return None
    evidence=result['evidence'];ids={item['id'] for item in evidence}
    if len(ids)!=1:
        return None
    identifier=next(iter(ids))
    available=[item for item in passages if item.source['id']==identifier and (item.structure or {}).get('local_issue')]
    if not available:
        return None
    manual = all((item.structure or {}).get('parameter_manual') for item in available)
    subjects=[]
    for item in evidence:
        for label in document_entities(item['quote']):
            if label not in subjects:subjects.append(label)
    if not subjects:
        # Не сохраняем произвольное имя из сообщения: обозначение должно
        # присутствовать в процитированном тексте или его заголовке.
        proven=normalized(' '.join(item['quote']+' '+item['heading'] for item in evidence))
        labels=anchors(question) or explicit_names(question)
        subjects=[value for value in labels if normalized(value) in proven]
    if not subjects and manual:
        # В однопредметном руководстве название на обложке/в заголовке
        # источника задаёт прибор. Оно не обязано повторяться в каждой строке.
        title = normalized(available[0].source['title'])
        subjects = [match[0] for match in re.finditer(r'\b[А-ЯA-Z]{2,}\d+(?:[-.]\d+)*\b', question)
                    if normalized(match[0]) in title]
    if not subjects or len(subjects)>4 or any(len(value)>140 for value in subjects):
        return None
    return {'source':identifier,'revision':evidence[0]['revision'],'subjects':subjects,'property':property_name(question),
            **({'manual': True} if manual else {})}


def validated_focus(focus,sources,passages):
    """Повторная проверка производной памяти, в том числе после SQLite restore."""
    if not isinstance(focus,dict):return None
    source=sources.get(focus.get('source'))
    if source is None or source['revision']!=focus.get('revision'):return None
    subjects=focus.get('subjects')
    if not isinstance(subjects,list) or not 1<=len(subjects)<=4 or any(not isinstance(s,str) or not 0<len(s)<=140 for s in subjects):return None
    property=focus.get('property')
    if property not in {None,*[name for name,_ in PROPERTIES]}:return None
    text=normalized(source['title']+' '+' '.join(p.heading+' '+p.text for p in passages if p.source['id']==focus['source']))
    if not all(normalized(value) in text for value in subjects):return None
    manual = all((p.structure or {}).get('parameter_manual') for p in passages if p.source['id']==focus['source'])
    return {'source':focus['source'],'revision':source['revision'],'subjects':list(subjects),'property':property,
            **({'manual': True} if manual else {})}


def resolve_followup(question,focus):
    """Возвращает новый поисковый запрос или необходимость короткого уточнения."""
    if not short_reference(question):return None
    current=anchors(question) or explicit_names(question)
    # Явно названное полное изделие начинает новый запрос. Голое «18 кВА»
    # меняет исполнение прежнего изделия и обрабатывается ниже.
    prop=property_name(question)
    if focus and focus.get('manual'):
        from .site_search_manual import variant
        casing = variant(question)
        if casing and len(focus['subjects']) == 1 and (prop or focus['property']):
            return {'question': (prop or focus['property']) + ' ' + focus['subjects'][0] + ' в корпусе ' + casing,
                    'source': focus['source']}
    if current:
        # «А масса ДВГ?» — новый явно названный предмет, без примеси старого.
        # «А РК 18 кВА?» переносит только вид характеристики, не прежнее изделие.
        if not prop and focus and focus['property'] and len(current)==1:
            return {'question':focus['property']+' '+question,'source':focus['source']}
        return None
    variant=re.search(r'(?<!\w)\d+(?:[.,]\d+)?\s*кВА\b',question,re.I)
    relevant=bool(prop or variant or re.search(r'упаков|кожух|с какого момента',question,re.I))
    if not relevant:return None
    if not focus or len(focus['subjects'])!=1:
        return {'clarify':'Уточните, о каком устройстве идёт речь.'}
    subject=focus['subjects'][0]
    if variant:
        changed,count=re.subn(r'\d+(?:[.,]\d+)?\s*кВА\b',variant[0],subject,flags=re.I)
        if count!=1:return {'clarify':'Уточните полное обозначение устройства.'}
        subject=changed
    prop=prop or focus['property']
    if not prop:return {'clarify':'Уточните, какую характеристику нужно сравнить.'}
    extra=re.sub(r'^(?:а|и)\s+','',question.strip(' ?.'),flags=re.I)
    return {'question':prop+' '+subject+' '+extra,'source':focus['source']}


def concise_result(question,result,passages):
    """Меняем только показ; исходная цитата и ссылка остаются полными."""
    if result.get('status')!='answer' or len(result.get('evidence',[]))!=1:return result
    evidence=result['evidence'][0]
    if not any(p.source['id']==evidence['id'] and p.heading==evidence['heading'] and (p.structure or {}).get('local_issue') for p in passages):return result
    # Уже составленный ответ с родительским условием повторно не сокращаем.
    raw=evidence['quote'].strip()
    if result['text'].strip()!=raw:
        source=next(p.source for p in passages if p.source['id']==evidence['id'])
        if result['text'].strip()!=source['title']+'. '+raw:return result
    return {**result,'text':render_quote(raw,question,facets(question))}


def unique_technical_source(question,candidates):
    """Обычный чат тоже находит технический файл, если обозначение однозначно.

    Проверяем все доступные источники, включая обычные документы. Если одно
    обозначение встречается в нескольких файлах, сохраняем обычный поиск с
    приоритетом документов и проверкой противоречий.
    """
    identifiers=anchors(question)
    manual_identifiers = [normalized(match[0]) for match in re.finditer(r'\b[А-ЯA-Z]{2,}\d+(?:[-.]\d+)*\b', question)]
    from .site_search_manual import relation
    manual_query = bool(manual_identifiers and relation(question))
    if not identifiers and manual_query:
        identifiers = manual_identifiers
    if not identifiers or not (facets(question) or manual_query):return None
    documents={}
    for item in candidates:documents.setdefault(item.source['id'],[]).append(item)
    matched=[]
    for identifier,items in documents.items():
        text=normalized(items[0].source['title']+' '+' '.join(item.heading+' '+item.text for item in items))
        if all(re.search(r'(?<!\w)'+re.escape(value)+r'(?!\d)',text) for value in identifiers):matched.append(identifier)
    if len(matched)==1 and all((item.structure or {}).get('local_issue') for item in documents[matched[0]]):return matched[0]
    return None
