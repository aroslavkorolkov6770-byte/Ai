"""Замороженные междокументные связи и диалог из двадцати сообщений."""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from ai_core.site_search import ClosedSiteIndex, Principal
from evaluation.closed_search.cross_documents_probe import sources, ORIGIN, LAB, SPEC as CROSS_SPEC

SPEC=Path(__file__).with_name('document_links_20261029.json')


def records(spec):
    return sources(spec)+sources(json.loads(CROSS_SPEC.read_text(encoding='utf-8')))


def checks(case,result):
    errors=[]
    if result['status']!=case.get('status','answer'):errors.append('status:'+result['status'])
    for key,expected in [('has',True),('absent',False)]:
        for text in case.get(key,[]):
            present=bool(re.search((r'(?<!\d)' if text[:1].isdigit() else '')+re.escape(text),result['text'],re.I))
            if present!=expected:errors.append(key+':'+text)
    if 'evidence_ids' in case and {item['id'] for item in result['evidence']}!=set(case['evidence_ids']):errors.append('evidence-origin')
    if result['status'] not in {'answer','partial'} and (result['links'] or result['evidence']):errors.append('leaked-evidence')
    return errors


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--label',default='links');parser.add_argument('--holdout',action='store_true');args=parser.parse_args()
    spec=json.loads(SPEC.read_text(encoding='utf-8'))
    index=ClosedSiteIndex([ORIGIN]);index.publish(records(spec),index.begin('links'))
    rows=[]
    groups=['holdout'] if args.holdout else ['cases','dialogue']
    for group in groups:
        for case in spec[group]:
            result=index.search(case['q'],Principal('closed_lab_'+case.get('user','a'),'staff'),
                                'long' if group=='dialogue' else case.get('chat',case['id']),case.get('source'))
            errors=checks(case,result)
            rows.append({'id':case['id'],'question':case['q'],'result':result,'pass':not errors,'errors':errors})
    report={'specSHA256':hashlib.sha256(SPEC.read_bytes()).hexdigest(),'total':len(rows),'passed':sum(r['pass'] for r in rows),'cases':rows}
    file=LAB/(args.label+'-'+str(time.time_ns())+'.json');file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'file':str(file),'total':report['total'],'passed':report['passed'],'failures':[{'id':r['id'],'errors':r['errors'],'text':r['result']['text']} for r in rows if not r['pass']]},ensure_ascii=False))
    if report['passed']!=report['total']:raise SystemExit(1)


if __name__=='__main__':main()
