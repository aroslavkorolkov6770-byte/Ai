"""Независимые новые сканы; одинаковые названия в плохом/хорошем документе разделены выбором файла."""
import argparse
import base64
import json
import time
from pathlib import Path
from ai_core.site_documents import extract_document
from ai_core.site_search import ClosedSiteIndex, Principal

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'evaluation/closed_search/fixtures/layout'
LAB = ROOT / '.runtime/closed-search-lab-20261028'
ORIGIN = 'http://127.0.0.1:3040'


def load_sources():
    spec = json.loads((ASSETS/'spec.json').read_text(encoding='utf-8'))
    sources, extraction = [], []
    for document in spec['documents']:
        data = (ASSETS/document['name']).read_bytes()
        started = time.perf_counter()
        text, complete = extract_document('data:application/pdf;base64,'+base64.b64encode(data).decode(), document['name'])
        extraction.append({'id':document['id'],'seconds':round(time.perf_counter()-started,3),'complete':complete,'text':text})
        sources.append({'id':document['id'],'revision':'layout-v1','title':document['name'],'kind':'document',
                        'url':ORIGIN+'/materials/'+document['id'],'access':{'scope':'workspace'},'complete':complete,
                        'sections':[{'heading':document['name'],'text':text}]})
    return spec, sources, extraction


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label',default='layout-probe')
    parser.add_argument('--holdout',action='store_true')
    args = parser.parse_args()
    spec,sources,extraction=load_sources()
    index=ClosedSiteIndex([ORIGIN]);index.publish(sources,index.begin('layout'))
    rows=[]
    for case in spec['holdout' if args.holdout else 'cases']:
        answer=index.search(case['q'],Principal('a','staff'),case['id'],case['source'])
        errors=[]
        if answer['status']!=case.get('status','answer'):errors.append('status:'+answer['status'])
        for text in case.get('has',[]):
            if text.casefold() not in answer['text'].casefold():errors.append('missing:'+text)
        for text in case.get('absent',[]):
            if text.casefold() in answer['text'].casefold():errors.append('unexpected:'+text)
        if answer['status']=='answer' and {part['id'] for part in answer['evidence']}!={case['source']}:errors.append('source')
        rows.append({'id':case['id'],'question':case['q'],'pass':not errors,'errors':errors,'answer':answer})
    report={'passed':sum(row['pass'] for row in rows),'total':len(rows),'extraction':extraction,'cases':rows}
    output=LAB/(args.label+'-'+str(time.time_ns())+'.json')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(output),'passed':report['passed'],'total':report['total'],'failures':[row for row in rows if not row['pass']]},ensure_ascii=False))
    if report['passed']!=report['total']:raise SystemExit(1)


if __name__=='__main__':main()
