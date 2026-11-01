"""Наблюдение за ранжированием, без изменения ядра и контрольных вопросов."""
import json
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[2]))
from ai_core.site_search import ClosedSiteIndex, Principal, terms

def main():
    records=json.loads((HERE.with_name('pdf_sections_v7_20261031')/'results/verified/records.json').read_text(encoding='utf-8'))
    questions=json.loads((HERE.with_name('holdout_20261031')/'questions.json').read_text(encoding='utf-8'))
    index=ClosedSiteIndex(['http://holdout.invalid'])
    index.publish(records,index.begin('diagnostic'))
    wanted={int(n) for n in sys.argv[1:]}
    probes=[]
    def observe(frame,event,arg):
        if frame.f_code.co_name!='_search_impl':return None
        if event=='line' and 'ranked' in frame.f_locals:
            loc=frame.f_locals
            # Снимок непосредственно перед фильтром покрытия — по строке кода,
            # не по неизменному номеру, который смещается после редактирования.
            code=source_lines[frame.f_lineno-1].strip()
            if code=='best_coverage = max(row[1] for row in ranked)':
                probes.append({'question':loc['question'],'words':loc['words'],
                    'ranked':[{'score':s,'coverage':c,'heading':p.heading,'text':p.text}
                              for s,c,p in loc['ranked'][:6]],
                    'candidates':[{'text':p.text,'tokens':list(p.words)} for p in loc['candidates']
                                  if any(w in p.text.casefold() for w in ['удлинител','подставке','14-лет','14 лет'])]})
        return observe
    source_lines=(HERE.parents[2]/'ai_core/site_search.py').read_text(encoding='utf-8').splitlines()
    for n in sorted(wanted):
        q=questions[n-1]
        sys.settrace(observe)
        try:answer=index.search(q['question'],Principal('exam-reader','staff'),q['chat'])
        finally:sys.settrace(None)
        print(json.dumps({'id':q['id'],'answer':answer,'trace':probes},ensure_ascii=False))
        probes.clear()

if __name__=='__main__':
    try:main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
