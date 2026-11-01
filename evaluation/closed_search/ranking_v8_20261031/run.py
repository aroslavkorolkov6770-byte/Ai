"""Повторная регрессия прежних 100 вопросов, отдельный журнал каждого запуска."""
import base64
import hashlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
EXAM=HERE.with_name('holdout_20261031')
sys.path.insert(0,str(ROOT))


def digest(file):return hashlib.sha256(file.read_bytes()).hexdigest()


def main():
    label=sys.argv[1]
    if not label.isalnum():raise ValueError('Invalid run label')
    out=HERE/'results'/label
    out.mkdir(exist_ok=False)
    frozen=json.loads((EXAM/'FREEZE.json').read_text(encoding='utf-8'))
    # Эталоны здесь не читаются: сравнение выполняется после записи всех ответов.
    for name in ['questions.json','sources.json']:
        if digest(EXAM/name)!=frozen['files'][name]:raise ValueError('Changed exam input')
    runtime={str(p.relative_to(ROOT)):digest(p) for p in (ROOT/'ai_core').glob('*.py')}
    (out/'START.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'runtime':runtime,
                                            'frozen_inputs':{name:frozen['files'][name] for name in ['questions.json','sources.json','gold.json']}},indent=2),encoding='utf-8')
    from ai_core import site_documents
    from ai_core.site_search import Principal
    from ai_core.site_search_runtime import DurableSearch
    site_documents.CACHE=HERE/'results'/'document-cache'
    sources=json.loads((EXAM/'sources.json').read_text(encoding='utf-8'))
    questions=json.loads((EXAM/'questions.json').read_text(encoding='utf-8'))
    records=[];extraction=[]
    for source in sources:
        file=EXAM/'sources'/source['file']
        if digest(file)!=source['sha256']:raise ValueError('Changed original')
        start=time.perf_counter()
        def progress(value):
            if value.get('pages_read',0)%20==0:print(source['id'],value,flush=True)
        try:
            text,complete=site_documents.extract_document('data:application/octet-stream;base64,'+base64.b64encode(file.read_bytes()).decode(),source['file'],progress=progress)
        except Exception:
            text,complete='',False
            (out/(source['id']+'-error.txt')).write_text(traceback.format_exc(),encoding='utf-8')
        extraction.append({'id':source['id'],'seconds':time.perf_counter()-start,'complete':complete,'chars':len(text)})
        (out/(source['id']+'-extracted.txt')).write_text(text,encoding='utf-8')
        records.append({'id':'document:'+source['id'],'revision':source['sha256'],'title':source['title'],
                        'kind':'document','authority':'official','url':'http://holdout.invalid/doc/'+source['id'],
                        'access':{'scope':'restricted','users':['exam-reader']},'complete':complete,
                        'sections':[{'heading':source['title'],'text':text}]})
        print('extracted',extraction[-1],flush=True)
    (out/'extraction.json').write_text(json.dumps(extraction,indent=2),encoding='utf-8')
    (out/'records.json').write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')
    engine=DurableSearch(['http://holdout.invalid'],out/'exam.sqlite',extraction_version=site_documents.PARSER_VERSION)
    start=time.perf_counter();engine.publish(records,engine.begin(label))
    (out/'index.json').write_text(json.dumps({'seconds':time.perf_counter()-start,'passages':len(engine.index.passages),
        'per_source':{r['id']:{'count':sum(p.source['id']==r['id'] for p in engine.index.passages),
        'largest':max((len(p.text) for p in engine.index.passages if p.source['id']==r['id']),default=0)} for r in records}},indent=2),encoding='utf-8')
    revisions={r['id']:r['revision'] for r in records}
    with (out/'answers.jsonl').open('x',encoding='utf-8') as output:
        for question in questions:
            start=time.perf_counter()
            try:answer=engine.query(question['question'],Principal('exam-reader','staff'),question['chat'],None,revisions)
            except Exception:answer={'status':'error','text':'','evidence':[],'links':[],'error':traceback.format_exc()}
            row={**question,'result':answer,'milliseconds':round((time.perf_counter()-start)*1000,3)}
            output.write(json.dumps(row,ensure_ascii=False)+'\n');output.flush();os.fsync(output.fileno())
            print(question['id'],answer['status'],flush=True)
    changed=[name for name,value in runtime.items() if digest(ROOT/name)!=value]
    (out/'FINISH.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'changed_during_run':changed,
                                             'answers_sha256':digest(out/'answers.jsonl')},indent=2),encoding='utf-8')
    if changed:raise RuntimeError('Runtime changed during run')


if __name__=='__main__':
    try:main()
    except Exception:traceback.print_exc();sys.exit(1)
