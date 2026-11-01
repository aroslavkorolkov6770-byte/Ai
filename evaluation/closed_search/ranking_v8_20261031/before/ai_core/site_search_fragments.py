"""Адреса проверенных фрагментов: редакция и доступ проверяются при открытии."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlencode, urlsplit
from .site_pdf_locations import locate_fragment, prepare_pdf_lookup, prepare_structured_lookup


def validate_fragment_url(source, origins):
    value = source.get('fragment_url')
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError('fragment_url_invalid')
    url = urlsplit(value)
    if (f'{url.scheme}://{url.netloc}' not in origins or url.scheme not in {'http', 'https'}
            or url.username or url.password or url.query or url.fragment
            or url.path != '/closed-search-fragment'):
        raise ValueError('fragment_url_invalid')


def compact(text):
    return re.sub(r'\s+', ' ', text).strip()


def build_fragments(passages, sentences=None):
    """Готовим указатели один раз вместе с индексом, не во время OCR/вопроса."""
    grouped = {}
    for part in passages:
        if not part.source.get('fragment_url'):
            continue
        # Синтетическое правило из нескольких файлов не выдаём за цитату одного.
        if (part.structure or {}).get('document_origins') or (part.structure or {}).get('document_guard'):
            continue
        product = any(row['heading'] == part.heading for row in part.source.get('catalog_items', []))
        # В PDF выделяем предложение с фактом, а не соседние колонтитулы.
        # Сохраняем также старый указатель на абзац: добавление подсветки
        # не должно ломать ранее выданную ссылку на неизменённый документ.
        pieces = [part.text]
        if part.source.get('pdf_layout') and not product and sentences:
            pieces.extend(sentences(part.text))
        for piece in pieces:
            group = (part.source['id'], part.heading, '' if product else piece)
            if group not in grouped:
                grouped[group] = {'source': part.source, 'heading': part.heading, 'parts': [], 'kind': 'product' if product else 'fragment'}
            if piece not in grouped[group]['parts']:
                grouped[group]['parts'].append(piece)
    targets, by_heading, pdf_lookups, structured_lookups = {}, {}, {}, {}
    for item in grouped.values():
        source = item['source']
        text = '\n\n'.join(item['parts'])
        token = hashlib.sha256(json.dumps([source['id'], source['revision'], item['heading'], text], ensure_ascii=False).encode()).hexdigest()
        target = {'id': source['id'], 'revision': source['revision'], 'fragment': token,
                  'title': source['title'], 'heading': item['heading'], 'text': text,
                  'kind': item['kind'], 'source_url': source['url']}
        layout = source.get('pdf_layout')
        if isinstance(layout, dict) and source['id'] not in pdf_lookups:
            pdf_lookups[source['id']] = prepare_pdf_lookup(layout)
            structured_lookups[source['id']] = prepare_structured_lookup(layout)
        location = locate_fragment(layout, text, lookup=pdf_lookups.get(source['id']),
                                   structured_lookup=structured_lookups.get(source['id']), heading=item['heading'])
        if location is not None:
            target['pdf_location'] = location
        targets[(source['id'], token)] = target
        by_heading.setdefault((source['id'], item['heading']), []).append(target)
    return targets, by_heading


def evidence_links(index, evidence):
    """Точный переход только при буквальном совпадении цитаты с фрагментом."""
    source = index.sources[evidence['id']]
    quote = compact(evidence.get('quote', ''))
    if not quote or not source.get('fragment_url'):
        return []
    targets = index.fragment_headings.get((source['id'], evidence['heading']), [])
    selected = [item for item in targets if quote in compact(item['text'])
                or (item['kind'] == 'product' and quote in compact(item['heading']))]
    if selected and source.get('pdf_layout'):
        # Новый ответ ведёт к самой короткой точной цитате. Более широкий
        # старый абзац остаётся доступным по прежнему адресу.
        shortest = min(len(compact(item['text'])) for item in selected)
        selected = [item for item in selected if len(compact(item['text'])) == shortest]
    if not selected:
        # Составной ответ может цитировать несколько абзацев одного раздела.
        sentences = [compact(value) for value in index._sentences(quote)]
        selected = [item for item in targets if any(value in compact(item['text']) for value in sentences)]
        if not sentences or not all(any(value in compact(item['text']) for item in selected) for value in sentences):
            return []
    links = []
    for item in selected:
        title = item['heading'] or source['title']
        link = {'title': title, 'url': source['fragment_url'] + '?' + urlencode({
            'source': source['id'], 'revision': source['revision'], 'fragment': item['fragment']})}
        if link not in links:
            links.append(link)
    return links


def resolve_fragment(index, source_id, revision, fragment, principal, current_revisions, readable):
    """Ссылка не даёт прав: проверяем живой список сайта и индекс независимо."""
    missing = {'status': 'not_found', 'message': 'Материал недоступен.'}
    source = index.sources.get(source_id)
    if not source or source_id not in current_revisions or not readable(source, principal, index.clock()):
        return missing
    if current_revisions[source_id] != revision:
        return {'status': 'changed', 'message': 'Материал изменился. Повторите вопрос, чтобы получить ссылку на актуальную редакцию.'}
    if index.state != 'ready' or source['revision'] != revision:
        return {'status': 'processing', 'message': 'Материал обновляется. Попробуйте открыть ссылку позже.'}
    target = index.fragment_targets.get((source_id, fragment))
    if not target or source.get('complete') is False:
        return missing
    return {'status': 'ready', **target}
