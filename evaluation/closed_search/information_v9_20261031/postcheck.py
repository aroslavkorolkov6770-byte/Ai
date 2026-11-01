"""Отдельные проверки после экзамена; исходная база и 100 ответов не меняются."""
from __future__ import annotations
import copy
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'results' / 'candidate1'
EXAM = HERE.with_name('holdout_20261031')
sys.path.insert(0, str(HERE.parents[2]))
from ai_core.site_documents import PARSER_VERSION
from ai_core.site_search import Principal
from ai_core.site_search_runtime import DurableSearch


def main():
    if not (OUT / 'FINISH.json').is_file():
        raise RuntimeError('First finish all 100 questions')
    db = OUT / 'postcheck.sqlite'
    if db.exists():
        raise RuntimeError('Postcheck already started; preserve its database')
    original = json.loads((OUT / 'records.json').read_text(encoding='utf-8'))
    records = copy.deepcopy(original)
    # Только технический адрес просмотра. Текст и права исходных документов прежние.
    for record in records:
        record['fragment_url'] = 'http://holdout.invalid/closed-search-fragment'
    engine = DurableSearch(['http://holdout.invalid'], db, extraction_version=PARSER_VERSION)
    engine.publish(records, engine.begin('postcheck-initial'))
    revisions = {item['id']: item['revision'] for item in records}
    actor, outsider = Principal('exam-reader', 'staff'), Principal('exam-outsider', 'staff')
    checks = []

    def check(name, passed, **detail):
        checks.append({'name': name, 'passed': bool(passed), **detail})

    statistics = []
    for source in records:
        parts = [p for p in engine.index.passages if p.source['id'] == source['id']]
        statistics.append({'id': source['id'], 'passages': len(parts),
                           'largest_passage': max((len(p.text) for p in parts), default=0),
                           'structure_unavailable': engine.index.sources[source['id']].get('structure_unavailable'),
                           'longer_than_2000': sum(len(p.text)>2000 for p in parts)})
    questions = json.loads((EXAM / 'questions.json').read_text(encoding='utf-8'))
    for position in [0, 20, 40, 60, 80]:
        q = questions[position]
        answer = engine.query(q['question'], outsider, q['chat'], None, revisions)
        check('outsider-' + q['id'], answer['status'] != 'answer' and not answer.get('evidence') and not answer.get('links'), result=answer)

    # Создаём реальный успешный контекст доступного документа и проверяем scope.
    answer = engine.query(questions[80]['question'], actor, 'isolation-shared', None, revisions)
    owner_scope = (actor.user_id, actor.role, (), 'isolation-shared')
    other_scope = (outsider.user_id, outsider.role, (), 'isolation-shared')
    check('owner-context-created', owner_scope in engine.index.sessions, status=answer['status'])
    owner_context = copy.deepcopy(engine.index.sessions.get(owner_scope))
    answer = engine.query('А адрес можно пришить?', outsider, 'isolation-shared', None, revisions)
    check('same-chat-other-user', other_scope not in engine.index.sessions and engine.index.sessions.get(owner_scope)==owner_context
          and not answer.get('evidence') and not answer.get('links') and answer['status']!='answer', result=answer)
    fresh_scope = (actor.user_id, actor.role, (), 'isolation-fresh')
    check('fresh-chat-has-no-inherited-context', fresh_scope not in engine.index.sessions)
    # Новый объект восстанавливает SQLite без OCR; качество реплик не переоцениваем.
    restored = DurableSearch(['http://holdout.invalid'], db, extraction_version=PARSER_VERSION)
    check('restore-index', restored.restored and restored.index.state=='ready' and len(restored.index.passages)==len(engine.index.passages),
          restored=restored.restored, passages=len(restored.index.passages), milliseconds=restored.restore_ms)
    restored_context = restored.index.sessions.get(owner_scope)
    check('restore-owner-context-only', restored_context is not None and other_scope not in restored.index.sessions
          and fresh_scope not in restored.index.sessions
          and restored_context.get('ids')==owner_context.get('ids'),
          restored_ids=restored_context.get('ids') if restored_context else [])
    target = next(item for item in restored.index.fragment_targets.values() if item['id']=='document:post')
    args = (target['id'], target['revision'], target['fragment'])
    check('owner-fragment-opens', restored.fragment(*args, actor, revisions)['status']=='ready')
    hidden = restored.fragment(*args, outsider, revisions)
    check('outsider-fragment-blocked', hidden['status']=='not_found' and 'text' not in hidden, result=hidden)
    new_revisions = {**revisions, 'document:post':'postcheck-revision-2'}
    changed = restored.fragment(*args, actor, new_revisions)
    check('live-revision-blocks-old-link', changed['status']=='changed' and 'text' not in changed, result=changed)
    changed_records = copy.deepcopy(records)
    changed_records[-1]['revision'] = new_revisions['document:post']
    # Смена только метки редакции проверяет закрытие старого адреса, не перенос разделов.
    restored.publish(changed_records, restored.begin('postcheck-revision-2'))
    changed = restored.fragment(*args, actor, new_revisions)
    check('published-revision-blocks-old-link', changed['status']=='changed' and 'text' not in changed, result=changed)
    remaining = [item for item in changed_records if item['id']!='document:post']
    remaining_revisions = {item['id']:item['revision'] for item in remaining}
    restored.publish(remaining, restored.begin('postcheck-deleted'))
    deleted = restored.fragment(*args, actor, remaining_revisions)
    check('deleted-source-blocks-old-link', deleted['status']=='not_found' and 'text' not in deleted, result=deleted)
    check('deleted-source-prunes-context', all('document:post' not in c.get('ids',[]) for c in restored.index.sessions.values()))
    report = {'at':datetime.now(timezone.utc).isoformat(), 'passed':sum(c['passed'] for c in checks),
              'total':len(checks), 'checks':checks, 'passage_statistics':statistics}
    with (OUT / 'postcheck.json').open('x',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
