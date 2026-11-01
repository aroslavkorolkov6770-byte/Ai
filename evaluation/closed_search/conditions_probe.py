"""Фиксированные сценарии условий: результаты до/после сохраняются раздельно."""
import argparse
import json
import time
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal

ROOT = Path(__file__).resolve().parents[2]
SPEC = Path(__file__).with_name('conditions_20261029.json')


def sources(spec):
    return [{'id': item['id'], 'revision': 'v1', 'kind': 'document', 'title': item['name'],
             'url': 'http://127.0.0.1:3040/materials/' + item['id'], 'authority': 'official',
             'access': {'scope': 'workspace'}, 'sections': [{'heading': item['name'], 'text': item['text']}]}
            for item in spec['documents']]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='conditions-baseline')
    parser.add_argument('--holdout', action='store_true')
    args = parser.parse_args()
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    index = ClosedSiteIndex(['http://127.0.0.1:3040'])
    index.publish(sources(spec), index.begin('conditions'))
    rows = []
    for case in spec['holdout' if args.holdout else 'cases']:
        result = index.search(case['q'], Principal(case.get('user', 'a'), 'staff'), case.get('chat', case['id']))
        errors = []
        if result['status'] != case.get('status', 'answer'):
            errors.append('status:' + result['status'])
        for text in case.get('has', []):
            if text.casefold() not in result['text'].casefold():
                errors.append('missing:' + text)
        for text in case.get('absent', []):
            if text.casefold() in result['text'].casefold():
                errors.append('unwanted:' + text)
        if case.get('source') and not any(item['id'] == case['source'] for item in result['evidence']):
            errors.append('source')
        if result['status'] in {'clarify', 'conflict', 'not_found'} and result['links']:
            errors.append('unnecessary-links')
        rows.append({'id': case['id'], 'question': case['q'], **result, 'pass': not errors, 'errors': errors})
    output = ROOT / '.runtime/closed-search-lab-20261028' / (args.label + '-' + str(time.time_ns()) + '.json')
    report = {'passed': sum(item['pass'] for item in rows), 'total': len(rows), 'cases': rows}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(output), 'passed': report['passed'], 'total': len(rows), 'failures': [row for row in rows if not row['pass']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
