"""Явные ссылки между зарегистрированными документами, без сетевых запросов.

Граф собирается при индексации. Доступ к каждому его узлу повторно проверяет
поиск для конкретного пользователя. Загруженный текст остаётся данными.
"""
from __future__ import annotations

import copy
import re
import uuid
from collections import defaultdict
from urllib.parse import urljoin,urlsplit,parse_qsl,urlencode,unquote
from .site_search_structure import compile_sections, normalize_markup

EDITION = re.compile(r'(?im)^\s*Редакция:\s*([\w-]+(?:\.[\w-]+)*)\s*\.?\s*$')
LINK = re.compile(r'\b(?:см\.?|смотри(?:те)?|согласно)\s+документ(?:у)?\s+(?:«([^»\n]{1,200})»|"([^"\n]{1,200})")\s*,?\s*раздел(?:у)?\s+(\d+(?:\.\d+)*)\s*(?:,\s*редакция\s+([\w-]+(?:\.[\w-]+)*))?\.?',re.I)
LINK_HINT = re.compile(r'\b(?:см\.?|смотри(?:те)?|согласно)\s+документ(?:у)?\b',re.I)
MARKDOWN_LINK = re.compile(r'\b(?:см\.?|смотри(?:те)?|согласно)\s+\[[^\]\n]{1,200}\]\(([^\s)]+)\)\s*(?:,\s*редакция\s+([\w-]+(?:\.[\w-]+)*))?\.?',re.I)


def url_key(value):
    parsed=urlsplit(value)
    return (parsed.scheme.casefold(),parsed.netloc.casefold(),parsed.path,urlencode(sorted(parse_qsl(parsed.query))))


def title_key(value):
    return ' '.join(value.casefold().replace('ё','е').split())


def compile_documents(sources,plain):
    """Разрешаем только точное название и единственный раздел нужной редакции."""
    titles=defaultdict(list)
    editions={}
    compiled={}
    references={}
    urls=defaultdict(list)
    for identifier,source in sources.items():
        if source.get('kind')=='document' and source.get('status','published')=='published':
            urls[url_key(source['url'])].append(identifier)
    # Входной текст не может выдать себя за внутренний указатель графа.
    marker_prefix='VATESDOCREF'+uuid.uuid4().hex+'Z'
    marker_pattern=re.compile(re.escape(marker_prefix)+r'(\d+)X')
    for identifier,source in sources.items():
        if source.get('kind')=='document' and source.get('status','published')=='published':
            titles[title_key(source['title'])].append(identifier)
        prepared=copy.deepcopy(source)
        labels=set()
        refs=[]
        for section in prepared.get('sections',[]):
            raw=normalize_markup(str(section.get('text','')))
            labels.update(match[1] for match in EDITION.finditer(raw))
            raw=EDITION.sub('',raw)
            def replace(match):
                # Непохожий на сноску маркер переживает локальный разбор раздела.
                marker=marker_prefix+str(len(refs))+'X'
                refs.append((match[1] or match[2],match[3],match[4]))
                return marker
            raw=LINK.sub(replace,raw)
            def replace_url(match):
                # URL — указатель на уже зарегистрированный источник, не
                # команда скачать сайт. Подпись ссылки не задаёт права доступа.
                try:
                    url=urljoin(source['url'],match[1])
                    target=urls.get(url_key(url),[])
                    section=re.fullmatch(r'(?:section-|раздел-)?(\d+(?:\.\d+)*)',unquote(urlsplit(url).fragment),re.I)
                except ValueError:
                    return '[STRUCTURE_UNCERTAIN]'
                if len(target)!=1 or not section:
                    return '[STRUCTURE_UNCERTAIN]'
                marker=marker_prefix+str(len(refs))+'X'
                refs.append((sources[target[0]]['title'],section[1],match[2]))
                return marker
            raw=MARKDOWN_LINK.sub(replace_url,raw)
            if LINK_HINT.search(raw):
                raw+='\n[STRUCTURE_UNCERTAIN]'
            section['text']=raw
        editions[identifier]=next(iter(labels)) if len(labels)==1 else None
        compiled[identifier]=compile_sections(prepared,plain)
        references[identifier]=refs

    def expand(identifier,position,trail,budget):
        key=(identifier,position)
        item=compiled[identifier][position]
        structure=copy.deepcopy(item.get('structure') or {})
        markers=list(marker_pattern.finditer(item['text']))
        own=marker_pattern.sub('',item['text']).strip(' .')
        own=(own+'.') if own else ''
        origin={'id':identifier,'revision':sources[identifier]['revision'],'heading':item['heading'],'text':own}
        budget[0]+=1
        if key in trail or len(trail)>=8 or budget[0]>64 or len(markers)>16:
            return own,structure,[],[origin],True
        texts=[own]
        dependencies=[]
        origins=[origin] if own else []
        bad=bool(structure.get('issue'))
        for marker in markers:
            title,section,edition=references[identifier][int(marker[1])]
            targets=titles.get(title_key(title),[])
            if len(targets)!=1:
                bad=True
                continue
            target=targets[0]
            candidates=[i for i,part in enumerate(compiled[target])
                        if re.match(r'^(?:Раздел\s+)?'+re.escape(section)+r'(?:[.\s]|$)',part['heading'],re.I)
                        and not re.match(r'^(?:Раздел\s+)?'+re.escape(section)+r'\.\d',part['heading'],re.I)]
            if len(candidates)!=1 or edition and editions[target]!=edition:
                bad=True
                continue
            text,extra,children,quotes,failed=expand(target,candidates[0],trail+[key],budget)
            dependencies.append({'id':target,'revision':sources[target]['revision'],'periods':extra.get('periods',[])})
            dependencies.extend(children)
            texts.append(text)
            origins.extend(quotes)
            bad|=failed
            if budget[0]>64 or sum(map(len,texts))>32000:
                bad=True
                break
        return ' '.join(text for text in texts if text),structure,dependencies,origins,bad

    result={}
    for identifier,items in compiled.items():
        rows=[]
        for position,item in enumerate(items):
            if not marker_pattern.search(item['text']):
                rows.append(item)
                continue
            text,structure,dependencies,origins,bad=expand(identifier,position,[],[0])
            public=marker_pattern.sub('',item['text']).strip(' .')
            # Ошибочная цепочка не публикует даже частично извлечённые детали.
            if bad:
                rows.append({**item,'text':public or item['heading'],'structure':{**structure,'issue':True,'linked':True}})
                continue
            unique={}
            for value in dependencies:
                if value['id'] not in unique:
                    unique[value['id']]=copy.deepcopy(value)
                else:
                    unique[value['id']]['periods'].extend(value['periods'])
            shared={**structure,'linked':True,'document_dependencies':list(unique.values()),'document_origins':origins}
            rows.append({**item,'text':text,'structure':shared})
            # Запасной фрагмент содержит только общедоступный текст корневого
            # файла. Скрытые слова не участвуют даже в ранжировании запроса.
            rows.append({**item,'text':public or item['heading'],'structure':{**structure,'issue':True,'linked':True,
                         'document_guard':True,'document_dependencies':list(unique.values())}})
        result[identifier]=rows
    return result
