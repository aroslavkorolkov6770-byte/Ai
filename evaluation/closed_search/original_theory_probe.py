"""Независимые вопросы к неизменённой теории из пользовательского ZIP.

Из архива читаются только два известных файла учебных тем. Профили,
результаты тестов, ответы на тесты и переписка в поисковый набор не попадают.
"""
import argparse
import hashlib
import json
import time
import zipfile
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / '.runtime/closed-search-lab-20261028'
SPEC = Path(__file__).with_name('original_theory_20261029.json')
ORIGIN = 'http://127.0.0.1:3040'


def load_sources(archive):
    """Сохраняем исходный текст и его происхождение, без синтетических добавок."""
    records, hashes = [], {}
    with zipfile.ZipFile(archive) as bundle:
        for name, kind in [('tea_hub_dynamic_route_v2', 'route'),
                           ('tea_hub_dynamic_basics_v2', 'basic')]:
            entry = 'tea-app-main/data/' + name + '.json'
            raw = bundle.read(entry)
            hashes[entry] = hashlib.sha256(raw).hexdigest()
            content = json.loads(raw)
            modules = content if kind == 'route' else [module for group in content for module in group.get('modules', [])]
            for item in modules:
                sections = [{'heading': item.get('h' + str(n)) or item['title'],
                             'text': item['t' + str(n)]} for n in (1, 2, 3) if item.get('t' + str(n))]
                public = {'id': kind + ':' + item['id'], 'title': item['title'], 'sections': sections}
                route = '/tasks?tab=edu&routeId=' if kind == 'route' else '/closed-search-material?basicId='
                records.append({**public, 'revision': hashlib.sha256(json.dumps(public, ensure_ascii=False).encode()).hexdigest(),
                                'url': ORIGIN + route + item['id'], 'kind': 'lesson', 'authority': 'reference',
                                'status': 'published', 'access': {'scope': 'workspace'}})
    return records, hashes


def check(case, result):
    errors = []
    if result['status'] != case.get('status', 'answer'):
        errors.append('status:' + result['status'])
    for phrase in case.get('has', []):
        if phrase.casefold() not in result['text'].casefold():
            errors.append('missing:' + phrase)
    for phrase in case.get('absent', []):
        if phrase.casefold() in result['text'].casefold():
            errors.append('unwanted:' + phrase)
    if case.get('sources') and not any(row['id'] in case['sources'] for row in result['evidence']):
        errors.append('source')
    if result['status'] not in {'answer', 'partial'} and (result['links'] or result['evidence']):
        errors.append('unexpected-evidence')
    if len(result['text']) > 900:
        errors.append('answer-too-long')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='original-theory')
    parser.add_argument('--holdout', action='store_true')
    args = parser.parse_args()
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    records, hashes = load_sources(spec['archive'])
    index = ClosedSiteIndex([ORIGIN])
    index.publish(records, index.begin('original-theory'))
    rows = []
    for case in spec['holdout' if args.holdout else 'cases']:
        result = index.search(case['q'], Principal('closed_lab_a', 'staff'), case['id'])
        errors = check(case, result)
        rows.append({'id': case['id'], 'question': case['q'], 'result': result, 'errors': errors, 'pass': not errors})
    report = {'source': 'unchanged-user-archive-theory', 'source_count': len(records), 'entry_sha256': hashes,
              'spec_sha256': hashlib.sha256(SPEC.read_bytes()).hexdigest(), 'total': len(rows),
              'passed': sum(row['pass'] for row in rows), 'cases': rows}
    destination = LAB / (args.label + '-' + str(time.time_ns()) + '.json')
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(destination), 'total': report['total'], 'passed': report['passed'],
                      'failures': [{'id': row['id'], 'errors': row['errors'], 'text': row['result']['text']}
                                   for row in rows if not row['pass']]}, ensure_ascii=False))
    return 0 if report['total'] == report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise SystemExit('Original theory probe failed: ' + type(error).__name__) from error
