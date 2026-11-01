"""Зафиксированный набор структуры документов, без изменения ожиданий после запуска."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from ai_core.site_search import ClosedSiteIndex, Principal

ROOT = Path(__file__).resolve().parents[2]
SPEC = Path(__file__).with_name('structure_20261029.json')


def sources(spec):
    return [{'id': item['id'], 'revision': 'v1', 'kind': 'document', 'title': item['name'],
             'url': 'http://127.0.0.1:3040/materials/' + item['id'], 'authority': 'official',
             'access': {'scope': 'workspace'}, 'sections': [{'heading': item['name'], 'text': item['text']}]}
            for item in spec['documents']]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='structure-baseline')
    parser.add_argument('--holdout', action='store_true')
    args = parser.parse_args()
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    index = ClosedSiteIndex(['http://127.0.0.1:3040'], clock=lambda: datetime(2026, 10, 29, 12, tzinfo=timezone.utc).timestamp())
    index.publish(sources(spec), index.begin('structure'))
    rows = []
    for case in spec['holdout' if args.holdout else 'cases']:
        result = index.search(case['q'], Principal('reader', 'staff'), case['id'])
        errors = []
        if result['status'] != case.get('status', 'answer'):
            errors.append('status:' + result['status'])
        errors.extend('missing:' + value for value in case.get('has', []) if value.casefold() not in result['text'].casefold())
        errors.extend('unwanted:' + value for value in case.get('absent', []) if value.casefold() in result['text'].casefold())
        if case.get('source') and not any(item['id'] == case['source'] for item in result['evidence']):
            errors.append('source')
        rows.append({'id': case['id'], 'question': case['q'], **result, 'pass': not errors, 'errors': errors})
    report = {'passed': sum(row['pass'] for row in rows), 'total': len(rows), 'cases': rows}
    output = ROOT / '.runtime/closed-search-lab-20261028' / (args.label + '-' + str(time.time_ns()) + '.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(output), **{key: report[key] for key in ('passed', 'total')}, 'failures': [row for row in rows if not row['pass']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
