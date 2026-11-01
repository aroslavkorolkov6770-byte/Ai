"""Новые вопросы о сравнении и упаковке; реальные данные и явно учебные карточки."""
import argparse
import hashlib
import json
import time
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal
from evaluation.closed_search.catalog_probe import catalog_sources, LAB, ORIGIN

SPEC = Path(__file__).with_name('comparison_20261030.json')


def fixture_sources(spec):
    return [{'id': 'document:' + doc['id'], 'revision': hashlib.sha256(json.dumps(doc, ensure_ascii=False).encode()).hexdigest(),
             'kind': 'document', 'title': doc['name'], 'authority': 'reference',
             'url': ORIGIN + '/tasks?tab=docs&documentId=' + doc['id'],
             'access': {'scope': 'restricted', 'users': ['closed_lab_b']},
             'catalog_items': [{'key': row['key'], 'heading': row['heading']} for row in doc['rows']],
             'sections': [{'heading': row['heading'], 'text': row['text']} for row in doc['rows']]}
            for doc in spec['documents']]


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
    answered = result['status'] in {'answer', 'partial'}
    if answered and (not result['links'] or not result['evidence']):
        errors.append('missing-evidence')
    if not answered and (result['links'] or result['evidence']):
        errors.append('unexpected-evidence')
    if len(result['text']) > 1200 or '**' in result['text']:
        errors.append('readability')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--holdout', action='store_true')
    parser.add_argument('--label', default='comparison')
    args = parser.parse_args()
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    index = ClosedSiteIndex([ORIGIN])
    index.publish(catalog_sources() + fixture_sources(spec), index.begin('comparison'))
    rows = []
    for case in spec['holdout' if args.holdout else 'cases']:
        start = time.perf_counter()
        result = index.search(case['q'], Principal('closed_lab_b', 'staff'), case['chat'])
        errors = check(case, result)
        rows.append({**case, 'result': result, 'errors': errors, 'pass': not errors,
                     'ms': round((time.perf_counter() - start) * 1000, 2)})
    report = {'spec_sha256': hashlib.sha256(SPEC.read_bytes()).hexdigest(), 'holdout': args.holdout,
              'total': len(rows), 'passed': sum(row['pass'] for row in rows), 'cases': rows}
    output = LAB / (args.label + '-' + str(time.time_ns()) + '.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(output), 'total': len(rows), 'passed': report['passed'],
                      'failures': [{'id': row['id'], 'errors': row['errors'], 'text': row['result']['text']}
                                   for row in rows if not row['pass']]}, ensure_ascii=False))
    return 0 if report['total'] == report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        raise SystemExit('Comparison probe failed: ' + type(error).__name__) from error
