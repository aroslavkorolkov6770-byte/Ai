"""Диагностика известных отказов; эталоны не передаются поисковому ядру."""
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from ai_core.site_search import ClosedSiteIndex, Principal


def main():
    prior = HERE.with_name('information_v9_20261031') / 'results/candidate1'
    records = json.loads((prior / 'records.json').read_text(encoding='utf-8'))
    questions = json.loads((HERE.with_name('holdout_20261031') / 'questions.json').read_text(encoding='utf-8'))
    wanted = {int(number) for number in sys.argv[1:]}
    index = ClosedSiteIndex(['http://holdout.invalid'])
    index.publish(records, index.begin('diagnostic'))
    lines = (ROOT / 'ai_core/site_search.py').read_text(encoding='utf-8').splitlines()
    trace = []

    def observe(frame, event, value):
        if frame.f_code.co_name != '_search_impl':
            return None
        statement = lines[frame.f_lineno - 1].strip()
        if event == 'line' and (statement == 'ranked = []' or statement == 'if not ranked:'):
            local = frame.f_locals
            trace.append({
                'at': statement, 'focused': local.get('focused_question'), 'words': local.get('words'),
                'facet': local.get('facet'), 'selected_source': local.get('selected_source'),
                'minimum': local.get('minimum'), 'weights': local.get('weights'),
                'candidates': [{'heading': item.heading, 'text': item.text,
                    'coverage': local.get('weighted_coverage', {}).get(index.passage_positions[id(item)], 0)}
                    for item in local.get('candidates', [])][:20],
                'ranked': [{'score': score, 'coverage': coverage, 'heading': item.heading, 'text': item.text}
                    for score, coverage, item in local.get('ranked', [])[:5]],
            })
        return observe

    chats = {item['chat'] for number, item in enumerate(questions, 1) if number in wanted}
    for number, question in enumerate(questions, 1):
        if question['chat'] not in chats:
            continue
        if number in wanted:
            sys.settrace(observe)
        try:
            answer = index.search(question['question'], Principal('exam-reader', 'staff'), question['chat'])
        finally:
            sys.settrace(None)
        if number in wanted:
            print(json.dumps({'id': question['id'], 'question': question['question'], 'answer': answer, 'trace': trace}, ensure_ascii=False))
        trace.clear()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
