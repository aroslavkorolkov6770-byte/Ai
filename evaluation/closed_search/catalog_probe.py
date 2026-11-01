"""Проверяет товарные диалоги на копиях пользовательских выгрузок."""
import argparse
import hashlib
import json
import time
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal
from evaluation.closed_search.real_materials_probe import records, LAB, ORIGIN

SPEC = Path(__file__).with_name('catalog_cases_20261030.json')


def catalog_sources():
    sources, manifest = records()
    documents = {doc['id']: doc for doc in manifest['documents']}
    for source in sources:
        doc = documents[source['id'].removeprefix('document:')]
        if doc['kind'] == 'catalog-copy':
            # Только проверенная подготовка задаёт тип содержимого и ключ товара.
            # Текст файла не может назначить себе права или официальность.
            source['catalog_items'] = [{'key': 'tea-export:' + row['code'], 'heading': row['title']}
                                       for row in doc['rows']]
    return sources


def check(case, result):
    errors = []
    if result['status'] != case['status']:
        errors.append('status:' + result['status'])
    for phrase in case.get('has', []):
        if phrase.casefold() not in result['text'].casefold():
            errors.append('missing:' + phrase)
    for phrase in case.get('absent', []):
        if phrase.casefold() in result['text'].casefold():
            errors.append('unexpected:' + phrase)
    if result['status'] == 'answer' and (not result['links'] or not result['evidence']):
        errors.append('missing-evidence')
    if result['status'] != 'answer' and (result['links'] or result['evidence']):
        errors.append('unexpected-evidence')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='catalog')
    args = parser.parse_args()
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    index = ClosedSiteIndex([ORIGIN])
    index.publish(catalog_sources(), index.begin('catalog'))
    rows = []
    for case in spec['cases']:
        started = time.perf_counter()
        result = index.search(case['q'], Principal('closed_lab_b', 'staff'), case['chat'])
        errors = check(case, result)
        rows.append({**case, 'result': result, 'errors': errors, 'pass': not errors,
                     'ms': round((time.perf_counter() - started) * 1000, 2)})
    report = {'spec_sha256': hashlib.sha256(SPEC.read_bytes()).hexdigest(),
              'total': len(rows), 'passed': sum(row['pass'] for row in rows), 'cases': rows}
    output = LAB / (args.label + '-' + str(time.time_ns()) + '.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(output), 'total': len(rows), 'passed': report['passed'],
                      'failures': [row for row in rows if not row['pass']]}, ensure_ascii=False))
    return 0 if report['total'] == report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        raise SystemExit('Catalog probe failed: ' + type(error).__name__) from error
