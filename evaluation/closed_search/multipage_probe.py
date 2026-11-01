"""Независимый набор ожиданий для многостраничных PDF."""
import argparse
import base64
import hashlib
import json
import time
from ai_core.site_documents import extract_document, PARSER_VERSION
from ai_core.site_search import ClosedSiteIndex, Principal
from .quality_probe import ROOT, LAB, ORIGIN, errors_for

ASSETS = ROOT/'evaluation/closed_search/fixtures/multipage'


def load_sources():
    spec = json.loads((ASSETS/'spec.json').read_text(encoding='utf-8'))
    sources, extraction = [], []
    for item in spec['documents']:
        data = (ASSETS/item['name']).read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise AssertionError('Frozen fixture changed: '+item['id'])
        start = time.perf_counter()
        text, complete = extract_document('data:application/pdf;base64,'+base64.b64encode(data).decode(), item['name'])
        extraction.append({'id':item['id'],'text':text,'complete':complete,'seconds':round(time.perf_counter()-start,3)})
        sources.append({'id':item['id'],'revision':'multipage-v1','title':item['name'],'kind':'document',
                        'url':ORIGIN+'/materials/'+item['id'],'access':{'scope':'workspace'},'complete':complete,
                        'sections':[{'heading':item['name'],'text':text}]})
    return spec, sources, extraction


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label',default='multipage-probe')
    parser.add_argument('--holdout',action='store_true')
    args = parser.parse_args()
    spec, sources, extraction = load_sources()
    index = ClosedSiteIndex([ORIGIN]); index.publish(sources,index.begin('multipage'))
    rows = []
    for case in spec['holdout' if args.holdout else 'cases']:
        answer = index.search(case['q'],Principal('a','staff'),case['id'],case['source'])
        errors = errors_for(case,answer)
        rows.append({'id':case['id'],'pass':not errors,'errors':errors,'answer':answer})
    report = {'parser':PARSER_VERSION,'passed':sum(r['pass'] for r in rows),'total':len(rows),'cases':rows,'extraction':extraction}
    file = LAB/(args.label+'-'+str(time.time_ns())+'.json')
    file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'file':str(file),'passed':report['passed'],'total':report['total'],'failures':[r for r in rows if not r['pass']]},ensure_ascii=False))
    if report['passed'] != report['total']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
