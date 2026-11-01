"""Проверяет настоящие документы тем же извлечением и поиском, что использует API."""
import argparse
import base64
import hashlib
import json
import time
from pathlib import Path

from ai_core.site_documents import extract_document
from ai_core.site_search import ClosedSiteIndex, Principal
from evaluation.closed_search.original_theory_probe import check, LAB, ORIGIN

SPEC = Path(__file__).with_name('real_materials_20261029.json')
DIRECTORY = LAB / 'real-materials'


def records():
    manifest = json.loads((DIRECTORY / 'manifest.json').read_text(encoding='utf-8'))
    sources = []
    for doc in manifest['documents']:
        raw = (DIRECTORY / doc['name']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != doc['sha256']:
            raise ValueError('Prepared source changed')
        text, complete = extract_document('data:application/octet-stream;base64,' + base64.b64encode(raw).decode(), doc['name'])
        if not complete:
            raise ValueError('Real material is truncated')
        sources.append({'id': 'document:' + doc['id'], 'title': doc['name'], 'revision': doc['sha256'],
                        'url': ORIGIN + '/tasks?tab=docs&documentId=' + doc['id'],
                        'kind': 'document', 'authority': 'reference', 'status': 'published',
                        'access': {'scope': 'workspace'}, 'sections': [{'heading': doc['name'], 'text': text}]})
    return sources, manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--holdout', action='store_true')
    parser.add_argument('--label', default='real-materials')
    args = parser.parse_args()
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    sources, manifest = records()
    start = time.perf_counter()
    index = ClosedSiteIndex([ORIGIN])
    index.publish(sources, index.begin('real-materials'))
    publish_ms = round((time.perf_counter() - start) * 1000, 2)
    rows = []
    for case in spec['holdout' if args.holdout else 'cases']:
        started = time.perf_counter()
        result = index.search(case['q'], Principal('closed_lab_b', 'staff'), case['id'])
        errors = check(case, result)
        if case.get('source_prefix') and not any(item['id'].startswith('document:' + case['source_prefix']) for item in result['evidence']):
            errors.append('source')
        rows.append({'id': case['id'], 'question': case['q'], 'result': result, 'errors': errors,
                     'ms': round((time.perf_counter() - started) * 1000, 2), 'pass': not errors})
    report = {'inputs': manifest['inputs'], 'spec_sha256': hashlib.sha256(SPEC.read_bytes()).hexdigest(),
              'source_count': len(sources), 'product_rows': manifest['rows'], 'passages': len(index.passages),
              'publish_ms': publish_ms, 'total': len(rows), 'passed': sum(row['pass'] for row in rows), 'cases': rows}
    destination = LAB / (args.label + '-' + str(time.time_ns()) + '.json')
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(destination), 'total': report['total'], 'passed': report['passed'],
                      'failures': [{'id': row['id'], 'errors': row['errors'], 'text': row['result']['text']}
                                   for row in rows if not row['pass']]}, ensure_ascii=False))
    return 0 if report['total'] == report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        raise SystemExit('Real material probe failed: ' + type(error).__name__) from error
