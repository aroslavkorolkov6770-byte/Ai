"""Отдельный холодный кэш и повторные локальные запросы без OCR на пути ответа."""
import hashlib
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_documents import PARSER_VERSION
from .layout_probe import ROOT, LAB, ORIGIN, load_sources


def main():
    with tempfile.TemporaryDirectory(prefix='vates-layout-cache-check-') as directory:
        with patch('ai_core.site_documents.CACHE',Path(directory)):
            started=time.perf_counter()
            spec,sources,extraction=load_sources()
            cold=time.perf_counter()-started
            with patch('ai_core.site_documents._scan_text',side_effect=AssertionError('OCR repeated')):
                started=time.perf_counter()
                cached=load_sources()[1]
                warm=time.perf_counter()-started
            if sources!=cached: raise AssertionError('Cache content differs')
    index=ClosedSiteIndex([ORIGIN]);index.publish(sources,index.begin('layout-performance'))
    timings,failures=[],[]
    with patch('ai_core.site_documents._scan_text',side_effect=AssertionError('OCR on query')):
        for cycle in range(20):
            for case in spec['cases']+spec['holdout']:
                started=time.perf_counter()
                answer=index.search(case['q'],Principal('a','staff'),str(cycle)+case['id'],case['source'])
                timings.append((time.perf_counter()-started)*1000)
                valid=answer['status']==case.get('status','answer')
                valid &= all(text.casefold() in answer['text'].casefold() for text in case.get('has',[]))
                valid &= all(text.casefold() not in answer['text'].casefold() for text in case.get('absent',[]))
                if not valid:failures.append({'case':case['id'],'cycle':cycle})
    timings.sort()
    report={'mode':'seven-synthetic-pdfs-eleven-raster-pages-local-query-no-http','parser':PARSER_VERSION,
            'coldSeconds':round(cold,3),'warmSeconds':round(warm,4),
            'files':[{key:value for key,value in item.items() if key!='text'} for item in extraction],
            'requests':len(timings),'failures':failures,'queryP50Ms':round(timings[len(timings)//2],2),
            'queryP95Ms':round(timings[int(len(timings)*.95)-1],2),
            'engineHashes':{name:hashlib.sha256((ROOT/'ai_core'/name).read_bytes()).hexdigest() for name in
                            ['site_documents.py','site_scan_tables.py','site_scan_layout.py','site_search_structure.py',
                             'site_search_reasoning.py','site_search_runtime.py','site_search_store.py','site_search_api.py']}}
    output=LAB/('layout-performance-'+str(time.time_ns())+'.json')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(output),**report},ensure_ascii=False))
    if failures:raise SystemExit(1)


if __name__=='__main__':main()
