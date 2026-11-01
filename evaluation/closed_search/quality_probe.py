"""Раздельно считаем правильный ответ, безопасный отказ и опасную подмену факта."""
import argparse
import base64
import hashlib
import json
import re
import time
from pathlib import Path
from ai_core.site_documents import extract_document, PARSER_VERSION
from ai_core.site_search import ClosedSiteIndex, Principal

ROOT=Path(__file__).resolve().parents[2]
ASSETS=ROOT/'evaluation/closed_search/fixtures/quality'
LAB=ROOT/'.runtime/closed-search-lab-20261028'
ORIGIN='http://127.0.0.1:3040'


def load_sources():
    spec=json.loads((ASSETS/'spec.json').read_text(encoding='utf-8'))
    sources,extraction=[],[]
    for item in spec['documents']:
        data=(ASSETS/item['name']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=item['sha256']:
            raise AssertionError('Frozen fixture changed: '+item['id'])
        started=time.perf_counter()
        text,complete=extract_document('data:application/pdf;base64,'+base64.b64encode(data).decode(),item['name'])
        extraction.append({'id':item['id'],'text':text,'complete':complete,'seconds':round(time.perf_counter()-started,3)})
        sources.append({'id':item['id'],'revision':'quality-v1','title':item['name'],'kind':'document',
                        'url':ORIGIN+'/materials/'+item['id'],'access':{'scope':'workspace'},'complete':complete,
                        'sections':[{'heading':item['name'],'text':text}]})
    return spec,sources,extraction


def errors_for(case,answer):
    status=answer['status'];expected=case.get('status','answer')
    allowed=['answer','unavailable'] if expected=='exact_or_refuse' else [expected]
    errors=[]
    if status not in allowed:errors.append('status:'+status)
    if status=='answer':
        for text in case.get('has',[]):
            if text.casefold() not in answer['text'].casefold():errors.append('missing:'+text)
        if case.get('numbers') and set(re.findall(r'\b\d+\b',answer['text']))!=set(case['numbers']):errors.append('wrong-number')
        if {e['id'] for e in answer['evidence']}!={case['source']}:errors.append('source')
    for text in case.get('absent',[]):
        # Запрещённые «2 дня» не должны совпадать с правильными «12 днями».
        pattern=(r'(?<!\d)' if text[:1].isdigit() else '')+re.escape(text.casefold())
        if re.search(pattern,answer['text'].casefold()):errors.append('unexpected:'+text)
    if status!='answer' and (answer.get('links') or answer.get('evidence')):errors.append('evidence-on-refusal')
    return errors


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--label',default='quality-probe');parser.add_argument('--holdout',action='store_true')
    args=parser.parse_args()
    spec,sources,extraction=load_sources()
    index=ClosedSiteIndex([ORIGIN]);index.publish(sources,index.begin('quality'))
    rows=[]
    for case in spec['holdout' if args.holdout else 'cases']:
        answer=index.search(case['q'],Principal('a','staff'),case['id'],case['source'])
        errors=errors_for(case,answer)
        rows.append({'id':case['id'],'question':case['q'],'pass':not errors,'errors':errors,'answer':answer})
    report={'parser':PARSER_VERSION,'passed':sum(r['pass'] for r in rows),'total':len(rows),'cases':rows,'extraction':extraction}
    file=LAB/(args.label+'-'+str(time.time_ns())+'.json')
    file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'file':str(file),'passed':report['passed'],'total':report['total'],'failures':[r for r in rows if not r['pass']]},ensure_ascii=False))
    if report['passed']!=report['total']:raise SystemExit(1)


if __name__=='__main__':main()
