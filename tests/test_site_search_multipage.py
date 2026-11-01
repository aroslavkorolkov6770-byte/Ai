"""Проверки целой цепочки, отказов, сносок и изоляции редакций."""
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ai_core.site_scan_layout import compose_pages, UNCERTAIN
from ai_core.site_scan_tables import describe_table, Cell
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.multipage_probe import load_sources, ORIGIN, errors_for


def chain(values, row_marker=True):
    tables, pages = [], []
    for number, value in enumerate(values,1):
        subject = 'Поставка' if number == 1 or not row_marker else 'Поставка (продолжение)'
        if not row_marker:
            subject += str(number)
        cells = [Cell(0,0,1,1,'Товар'),Cell(0,1,1,2,'Действия'),Cell(1,0,2,1,subject),Cell(1,1,2,2,value)]
        text = describe_table(cells,2,2,number,1)
        tables.append({'page':number,'cells':cells,'rows':2,'cols':2,'ordinal':1,'text':text})
        caption = 'Таблица 9' if number == 1 else 'Продолжение таблицы 9'
        marker = ('\nПродолжение '+('строки' if row_marker else 'таблицы')+' на следующей странице.') if number < len(values) else ''
        pages.append(caption+'\n'+text+marker)
    return tables, pages


class MultipageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec, cls.sources, cls.extraction = load_sources()

    def test_frozen_main_and_holdout(self):
        index = ClosedSiteIndex([ORIGIN]); index.publish(self.sources,index.begin('multipage'))
        for case in self.spec['cases'] + self.spec['holdout']:
            with self.subTest(case=case['id']):
                answer = index.search(case['q'],Principal('a','staff'),case['id'],case['source'])
                self.assertEqual(errors_for(case,answer),[])

    def test_four_page_row_keeps_all_fragments_and_original_cells(self):
        tables, pages = chain(['Сделайте фото и','сообщите координатору, затем','сохраните номер и','заполните журнал.'])
        before = deepcopy(tables)
        text = compose_pages(pages,tables,describe_table)
        self.assertNotIn(UNCERTAIN,text)
        self.assertIn('Сделайте фото и сообщите координатору, затем сохраните номер и заполните журнал.',text)
        self.assertIn('Страницы 1-4',text)
        self.assertEqual(tables,before)

    def test_repeated_headers_do_not_become_body_rows(self):
        tables, pages = chain(['Сделайте фото.','Заполните журнал.','Позвоните координатору.'],False)
        text = compose_pages(pages,tables,describe_table)
        self.assertNotIn(UNCERTAIN,text)
        for subject in ['Поставка1','Поставка2','Поставка3']:
            self.assertIn(subject,text)
        self.assertEqual(text.count('Действия:'),3)

    def test_chain_failures_are_atomic(self):
        for fault in ['header','missing','shape','truncated','subject','two-markers','duplicate-caption','orphan']:
            with self.subTest(fault=fault):
                tables,pages = chain(['Сделайте фото и','сообщите координатору, затем','заполните журнал.'])
                if fault == 'header': tables[2]['cells'][1].text='Другая колонка'
                if fault == 'missing': pages=pages[:2]
                if fault == 'shape': tables[2]['cells'].pop()
                if fault == 'truncated': tables[2]['cells'][-1].text='заполните'
                if fault == 'subject': tables[2]['cells'][2].text='Чужой (продолжение)'
                if fault == 'two-markers': pages[1]+='\nПродолжение таблицы на следующей странице.'
                if fault == 'duplicate-caption': pages[2]+='\nТаблица 9'
                if fault == 'orphan': pages[0]=pages[0].replace('Продолжение строки на следующей странице.','')
                result=compose_pages(pages,tables,describe_table)
                self.assertIn(UNCERTAIN,result)
                self.assertNotIn('Сделайте фото и сообщите координатору, затем заполните журнал.',result)

    def test_duplicate_table_numbers_cannot_share_footnotes(self):
        text = compose_pages(['Таблица 1\nПравило [1].','Таблица 1\n[1]: Другое правило.'],[],describe_table)
        self.assertIn(UNCERTAIN,text)

    def test_footnote_scope_cycles_and_duplicate_definitions(self):
        for suffix in ['[2]: Исключение: если упаковка вскрыта, товар хранится 2 дня.\n[2]: Товар хранится 9 дней.',
                       '[2]: См. [1].', '[3]: Исключение: товар хранится 2 дня.']:
            source={'id':'notes','revision':'1','kind':'document','title':'Сроки Образца','url':ORIGIN+'/materials/notes',
                    'access':{'scope':'workspace'},'sections':[{'heading':'Образец','text':'Товар Образец хранится 8 дней [1].\n[1]: См. [2].\n'+suffix}]}
            index=ClosedSiteIndex([ORIGIN]);index.publish([source],index.begin('notes'))
            self.assertEqual(index.search('Сколько хранится товар Образец?',Principal('a','staff'),selected_source='notes')['status'],'unavailable')

    def test_warm_cache_and_query_never_repeat_ocr(self):
        with patch('ai_core.site_documents._scan_text',side_effect=AssertionError('OCR repeated')):
            self.assertEqual(load_sources()[1],self.sources)
            index=ClosedSiteIndex([ORIGIN]);index.publish(self.sources,index.begin('warm'))
            case=self.spec['cases'][0]
            self.assertEqual(errors_for(case,index.search(case['q'],Principal('a','staff'),selected_source=case['source'])),[])

    def test_unselected_question_is_not_intercepted_by_other_rule(self):
        original = deepcopy(self.sources[0])
        unrelated = {'id':'storage','revision':'1','kind':'document','title':'Родонит. Хранение',
                     'url':ORIGIN+'/materials/storage','access':{'scope':'workspace'},
                     'sections':[{'heading':'Поставка Родонит','text':'Поставка Родонит хранится 11 дней. Исключение: если упаковка вскрыта, товар хранится 3 дня.'}]}
        index=ClosedSiteIndex([ORIGIN]);index.publish([original,unrelated],index.begin('mixed'))
        answer=index.search(self.spec['cases'][0]['q'],Principal('a','staff'))
        self.assertEqual(errors_for(self.spec['cases'][0],answer),[])
        self.assertEqual(index.search('Сколько хранится поставка Родонит?',Principal('a','staff'),'storage')['status'],'clarify')

    def test_revision_restart_and_rights_do_not_restore_old_tail(self):
        good=deepcopy(self.sources[0]);new=deepcopy(self.sources[1]);bad=deepcopy(self.sources[3])
        for source, revision in [(good,'1'),(new,'2'),(bad,'3')]:
            source.update(id='changing',revision=revision,access={'scope':'restricted','users':['a']})
        q=self.spec['cases'][0]['q'];actor=Principal('a','staff')
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'index.sqlite3'
            runtime=DurableSearch([ORIGIN],file);runtime.publish([good],runtime.begin('first'))
            self.assertIn('бумажный журнал',runtime.query(q,actor,'chat','changing',{'changing':'1'})['text'])
            runtime=DurableSearch([ORIGIN],file)
            self.assertEqual(runtime.query(q,actor,'chat','changing',{'changing':'2'})['status'],'processing')
            runtime.publish([new],runtime.begin('second'));runtime=DurableSearch([ORIGIN],file)
            answer=runtime.query(q,actor,'chat','changing',{'changing':'2'})
            self.assertIn('электронный журнал',answer['text']);self.assertNotIn('бумажный журнал',answer['text'])
            self.assertEqual(runtime.query(q,Principal('b','staff'),'chat','changing',{'changing':'2'})['status'],'not_found')
            runtime.publish([bad],runtime.begin('third'));runtime=DurableSearch([ORIGIN],file)
            self.assertEqual(runtime.query(q,actor,'chat','changing',{'changing':'3'})['status'],'unavailable')


if __name__ == '__main__':
    unittest.main()
