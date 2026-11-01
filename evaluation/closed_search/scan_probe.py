"""Замороженные сканы → настоящее OCR → поиск, без подстановки готового текста."""
import argparse
import base64
import json
import time
from pathlib import Path
from ai_core.site_documents import extract_document
from ai_core.site_search import ClosedSiteIndex, Principal

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / '.runtime/closed-search-lab-20261028'
ASSETS = ROOT / 'evaluation/closed_search/fixtures/scans'
ORIGIN = 'http://127.0.0.1:3040'


def extracted_sources(spec):
    sources, extraction = [], []
    for item in spec['documents']:
        data = (ASSETS / item['name']).read_bytes()
        started = time.perf_counter()
        text, complete = extract_document('data:application/pdf;base64,' + base64.b64encode(data).decode(), item['name'])
        extraction.append({'id': item['id'], 'complete': complete, 'seconds': round(time.perf_counter() - started, 2), 'text': text})
        sources.append({'id': item['id'], 'revision': 'scan-v1', 'title': item['name'], 'kind': 'document',
                        'url': ORIGIN + '/materials/' + item['id'], 'access': {'scope': 'workspace'},
                        'complete': complete, 'sections': [{'heading': item['name'], 'text': text}]})
    return sources, extraction


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='scan-probe')
    parser.add_argument('--holdout', action='store_true')
    args = parser.parse_args()
    spec = json.loads((ASSETS / 'spec.json').read_text(encoding='utf-8'))
    sources, extraction = extracted_sources(spec)
    index = ClosedSiteIndex([ORIGIN])
    index.publish(sources, index.begin('scan-test'))
    rows = []
    for item in spec['holdout' if args.holdout else 'cases']:
        answer = index.search(item['q'], Principal('a', 'staff'), item['id'])
        errors = []
        if answer['status'] != item.get('status', 'answer'):
            errors.append('status:' + answer['status'])
        for text in item.get('has', []):
            if text.casefold() not in answer['text'].casefold():
                errors.append('missing:' + text)
        for text in item.get('absent', []):
            if text.casefold() in answer['text'].casefold():
                errors.append('unexpected:' + text)
        if answer['status'] == 'answer' and {part['id'] for part in answer['evidence']} != {item['source']}:
            errors.append('source')
        rows.append({'id': item['id'], 'question': item['q'], 'pass': not errors, 'errors': errors, 'answer': answer})
    report = {'passed': sum(row['pass'] for row in rows), 'total': len(rows), 'extraction': extraction, 'cases': rows}
    path = LAB / (args.label + '-' + str(time.time_ns()) + '.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(path), 'passed': report['passed'], 'total': report['total'], 'failures': [row for row in rows if not row['pass']]}, ensure_ascii=False))
    if report['passed'] != report['total']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
