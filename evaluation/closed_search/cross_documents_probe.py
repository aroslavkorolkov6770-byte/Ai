"""Ожидания междокументного ответа фиксируются до изменения поиска."""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from ai_core.site_search import ClosedSiteIndex, Principal

ROOT=Path(__file__).resolve().parents[2]
SPEC=Path(__file__).with_name('cross_documents_20261029.json')
LAB=ROOT/'.runtime/closed-search-lab-20261028'
ORIGIN='http://127.0.0.1:3040'


def sources(spec):
    return [{'id':item['id'],'revision':'cross-v1','kind':'document','authority':'official','title':item['name'],
             'url':ORIGIN+'/materials/'+item['id'],'status':item.get('status','published'),
             'access':{'scope':'restricted','users':item['allowedUserIds']} if item.get('allowedUserIds') else {'scope':'workspace'},
             'sections':[{'heading':item['name'],'text':item['text']}]} for item in spec['documents']]


def errors_for(case,result):
    errors=[]
    if result['status'] not in case.get('allowed_statuses',[case.get('status','answer')]): errors.append('status:'+result['status'])
    def contains(text):
        return bool(re.search((r'(?<!\d)' if text[:1].isdigit() else '')+re.escape(text),result['text'],re.I))
    for text in case.get('has',[]):
        if not contains(text):errors.append('missing:'+text)
    for text in case.get('absent',[]):
        if contains(text):errors.append('unexpected:'+text)
    if 'sources' in case and {e['id'] for e in result['evidence']} != set(case['sources']):errors.append('sources')
    if result['status'] not in {'answer','partial'} and (result['evidence'] or result['links']):errors.append('evidence-on-refusal')
    if result['status']=='answer' and len(case.get('sources',[]))>1 and not all(e.get('question') for e in result['evidence']):errors.append('unattributed-part')
    return errors


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--label',default='cross-documents');parser.add_argument('--holdout',action='store_true');args=parser.parse_args()
    spec=json.loads(SPEC.read_text(encoding='utf-8'))
    index=ClosedSiteIndex([ORIGIN]);index.publish(sources(spec),index.begin('cross'))
    rows=[]
    for case in spec['holdout' if args.holdout else 'cases']:
        result=index.search(case['q'],Principal('closed_lab_'+case.get('user','a'),'staff'),case.get('chat',case['id']))
        errors=errors_for(case,result)
        rows.append({'id':case['id'],'question':case['q'],'result':result,'pass':not errors,'errors':errors})
    file=LAB/(args.label+'-'+str(time.time_ns())+'.json')
    report={'specSHA256':hashlib.sha256(SPEC.read_bytes()).hexdigest(),'passed':sum(r['pass'] for r in rows),'total':len(rows),'cases':rows}
    file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'file':str(file),'passed':report['passed'],'total':report['total'],'failures':[{'id':r['id'],'errors':r['errors'],'text':r['result']['text']} for r in rows if not r['pass']]},ensure_ascii=False))
    if report['passed']!=report['total']:raise SystemExit(1)


if __name__=='__main__':main()
