"""Цена контроля качества в фоне; на пути 280 запросов OCR запрещён проверкой."""
import hashlib
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_documents import PARSER_VERSION
from .quality_probe import ROOT, LAB, ORIGIN, load_sources, errors_for


def main():
    with tempfile.TemporaryDirectory(prefix='vates-quality-cache-') as directory:
        with patch('ai_core.site_documents.CACHE',Path(directory)):
            started=time.perf_counter();spec,sources,extraction=load_sources();cold=time.perf_counter()-started
            with patch('ai_core.site_documents._scan_text',side_effect=AssertionError('Repeated OCR')):
                started=time.perf_counter();cached=load_sources()[1];warm=time.perf_counter()-started
            if sources!=cached:raise AssertionError('Cache differs')
    index=ClosedSiteIndex([ORIGIN]);index.publish(sources,index.begin('quality-performance'))
    timings,failures=[],[]
    with patch('ai_core.site_documents._scan_text',side_effect=AssertionError('OCR on query')):
        for cycle in range(20):
            for case in spec['cases']+spec['holdout']:
                started=time.perf_counter()
                answer=index.search(case['q'],Principal('a','staff'),str(cycle)+case['id'],case['source'])
                timings.append((time.perf_counter()-started)*1000)
                errors=errors_for(case,answer)
                if errors:failures.append({'case':case['id'],'cycle':cycle,'errors':errors})
    timings.sort()
    names=['site_documents.py','site_scan_tables.py','site_scan_layout.py','site_scan_quality.py','site_search.py',
           'site_search_structure.py','site_search_reasoning.py','site_search_runtime.py','site_search_store.py','site_search_api.py']
    report={'mode':'eleven-synthetic-pdfs-eleven-pages-local-query-no-http','parser':PARSER_VERSION,
            'coldSeconds':round(cold,3),'warmSeconds':round(warm,4),'requests':len(timings),'failures':failures,
            'queryP50Ms':round(timings[len(timings)//2],2),'queryP95Ms':round(timings[int(len(timings)*.95)-1],2),
            'extraction':[{k:v for k,v in item.items() if k!='text'} for item in extraction],
            'engineHashes':{name:hashlib.sha256((ROOT/'ai_core'/name).read_bytes()).hexdigest() for name in names}}
    output=LAB/('quality-performance-'+str(time.time_ns())+'.json')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'file':str(output),**report},ensure_ascii=False))
    if failures:raise SystemExit(1)


if __name__=='__main__':main()
