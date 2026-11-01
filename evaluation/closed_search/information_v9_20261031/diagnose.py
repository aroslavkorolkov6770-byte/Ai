"""Трасса выбора внутри документа; вопросы и история остаются исходными."""
import json
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
from ai_core.site_search import ClosedSiteIndex, Principal

def main():
    records=json.loads((HERE.with_name('ranking_v8_20261031')/'results/candidate2/records.json').read_text(encoding='utf-8'))
    questions=json.loads((HERE.with_name('holdout_20261031')/'questions.json').read_text(encoding='utf-8'))
    wanted={int(n) for n in sys.argv[1:]}
    index=ClosedSiteIndex(['http://holdout.invalid']);index.publish(records,index.begin('diagnostic'))
    source=(ROOT/'ai_core/site_search.py').read_text(encoding='utf-8').splitlines()
    probes=[]
    def observe(frame,event,arg):
        if frame.f_code.co_name!='_search_impl':return None
        if event=='line' and source[frame.f_lineno-1].strip()=='best_coverage = max(row[1] for row in ranked)':
            loc=frame.f_locals
            probes.append({'focused':loc['focused_question'],'words':loc['words'],'facet':loc['facet'],
                'selected_source':loc['selected_source'],'ranked':[
                    {'score':s,'coverage':c,'heading':p.heading,'text':p.text} for s,c,p in loc['ranked'][:5]]})
        return observe
    needed={q['chat'] for i,q in enumerate(questions,1) if i in wanted}
    for number,q in enumerate(questions,1):
        if q['chat'] not in needed:continue
        if number in wanted:sys.settrace(observe)
        try:answer=index.search(q['question'],Principal('exam-reader','staff'),q['chat'])
        finally:sys.settrace(None)
        if number in wanted:print(json.dumps({'id':q['id'],'question':q['question'],'answer':answer,'trace':probes},ensure_ascii=False))
        probes.clear()

if __name__=='__main__':
    try:main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
