"""Быстрый повтор зафиксированных вопросов на том же ядре, без HTTP-лимита."""
import json
import sys
import time
from pathlib import Path
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_documents import extract_document


def main():
    root = Path(__file__).resolve().parents[2]
    lab = root / '.runtime/closed-search-lab-20261028'
    snapshot = json.loads((lab / 'probe-snapshot.json').read_text(encoding='utf-8'))
    for item in snapshot['sources']:
        if 'file_data' in item:
            text, complete = extract_document(item.pop('file_data'), item['file_name'])
            item['sections'] = [{'heading': item['title'], 'text': text}]
            item['complete'] = complete
    index = ClosedSiteIndex(['http://127.0.0.1:3040'])
    index.publish(snapshot['sources'], index.begin('probe'), snapshot.get('aliases'))
    cases = json.loads((lab / 'probe-questions.json').read_text(encoding='utf-8'))
    if '--reasoning' in sys.argv:
        cases = json.loads((root / 'evaluation/closed_search/reasoning_20261029.json').read_text(encoding='utf-8'))['cases']
    rows = []
    for item in cases:
        result = index.search(item['q'], Principal('closed_lab_a', 'staff'), item['id'])
        answer = result['text']
        errors = []
        if result['status'] != item.get('status', 'answer'):
            errors.append('status:' + result['status'])
        if item.get('source') and not any(item['source'] in link['url'] for link in result['links']):
            errors.append('source')
        errors.extend('missing:' + term for term in item.get('has', []) if term.casefold() not in answer.casefold())
        errors.extend('unwanted:' + term for term in item.get('absent', []) if term.casefold() in answer.casefold())
        rows.append({'id': item['id'], 'question': item['q'], 'answer': answer, 'status': result['status'], 'pass': not errors, 'errors': errors})
    report = {'mode': 'offline-same-core', 'passed': sum(row['pass'] for row in rows), 'total': len(rows), 'cases': rows}
    prefix = 'reasoning-offline-' if '--reasoning' in sys.argv else 'paraphrase-offline-'
    (lab / (prefix + str(time.time_ns()) + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({**report, 'cases': [row for row in rows if not row['pass']]}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(type(error).__name__, file=sys.stderr)
        raise
