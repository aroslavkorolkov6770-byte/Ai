"""Составные вопросы и независимые правила проверяются вместе с памятью."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.cross_documents_probe import SPEC, ORIGIN, sources, errors_for

A = Principal('closed_lab_a','staff')
B = Principal('closed_lab_b','staff')


def ask(runtime,text,actor,chat):
    revisions={key:item['revision'] for key,item in runtime.index.sources.items()}
    return runtime.query(text,actor,chat,None,revisions)


class CrossDocumentTests(unittest.TestCase):
    def setUp(self):
        self.spec=json.loads(SPEC.read_text(encoding='utf-8'))
        self.records=sources(self.spec)
        self.index=ClosedSiteIndex([ORIGIN])
        self.index.publish(self.records,self.index.begin('cross-tests'))

    def check_cases(self,key):
        for case in self.spec[key]:
            with self.subTest(case=case['id']):
                result=self.index.search(case['q'],B if case.get('user')=='b' else A,case.get('chat',case['id']))
                self.assertEqual(errors_for(case,result),[],result)

    def test_frozen_main_cases(self):
        self.check_cases('cases')

    def test_frozen_alternate_cases(self):
        self.check_cases('holdout')

    def open_runtime(self):
        folder=tempfile.TemporaryDirectory(prefix='vates-cross-test-')
        self.addCleanup(folder.cleanup)
        path=Path(folder.name)/'index.sqlite3'
        runtime=DurableSearch([ORIGIN],path)
        runtime.publish(self.records,runtime.begin('initial'))
        return runtime,path

    def test_pending_rule_dependencies_survive_restart_and_correction(self):
        runtime,path=self.open_runtime()
        self.assertEqual(ask(runtime,'Сколько хранится товар Халцедон?',A,'chat')['status'],'clarify')
        runtime=DurableSearch([ORIGIN],path)
        result=ask(runtime,'Упаковка не вскрыта.',A,'chat')
        self.assertIn('8 календарных',result['text'])
        runtime=DurableSearch([ORIGIN],path)
        self.assertEqual(ask(runtime,'А если упаковка вскрыта?',A,'chat')['status'],'conflict')
        self.assertEqual(ask(runtime,'А сколько дней?',A,'chat')['status'],'clarify')

    def test_changed_nonquoted_dependency_invalidates_context(self):
        runtime,path=self.open_runtime()
        ask(runtime,'Упаковка не вскрыта. Сколько хранится товар Халцедон?',A,'chat')
        updated=copy.deepcopy(self.records)
        next(item for item in updated if item['id']=='lab_cross_short')['revision']='cross-v2'
        runtime.publish(updated,runtime.begin('revision'))
        runtime=DurableSearch([ORIGIN],path)
        self.assertEqual(ask(runtime,'А если упаковка вскрыта?',A,'chat')['status'],'clarify')

    def test_changed_base_is_rechecked_after_restart(self):
        runtime,path=self.open_runtime()
        ask(runtime,'Сколько хранится товар Халцедон?',A,'chat')
        updated=copy.deepcopy(self.records)
        base=next(item for item in updated if item['id']=='lab_cross_base')
        base['revision']='cross-v2'
        base['sections'][0]['text']='Товар Халцедон хранится 12 календарных дней.'
        runtime.publish(updated,runtime.begin('revision'))
        runtime=DurableSearch([ORIGIN],path)
        result=ask(runtime,'Упаковка не вскрыта. Сколько хранится товар Халцедон?',A,'chat')
        self.assertIn('12 календарных',result['text'])
        self.assertNotIn('8 календарных',result['text'])

    def test_users_and_chats_stay_separate_after_restart(self):
        runtime,path=self.open_runtime()
        ask(runtime,'Сколько хранится товар Танзанит?',B,'same')
        runtime=DurableSearch([ORIGIN],path)
        self.assertEqual(ask(runtime,'А если упаковка вскрыта?',A,'same')['status'],'clarify')
        self.assertEqual(ask(runtime,'А если упаковка вскрыта?',B,'other')['status'],'clarify')
        self.assertEqual(ask(runtime,'А если упаковка вскрыта?',B,'same')['status'],'conflict')

    def test_revoked_dependency_does_not_leak_from_disk(self):
        runtime,path=self.open_runtime()
        ask(runtime,'Сколько хранится товар Танзанит?',B,'same')
        updated=copy.deepcopy(self.records)
        limited=next(item for item in updated if item['id']=='lab_cross_restricted')
        limited['revision']='cross-v2'
        limited['access']={'scope':'admin'}
        runtime.publish(updated,runtime.begin('revoked'))
        runtime=DurableSearch([ORIGIN],path)
        self.assertEqual(ask(runtime,'А если упаковка вскрыта?',B,'same')['status'],'clarify')
        result=ask(runtime,'Упаковка вскрыта. Сколько хранится товар Танзанит?',B,'new')
        self.assertIn('7 календарных',result['text'])
        self.assertEqual({item['id'] for item in result['evidence']},{'lab_cross_public'})

    def test_explicit_new_subject_overrides_old_condition_memory(self):
        self.index.search('Упаковка вскрыта. Сколько дней обрабатывается поставка Содалит?',A,'chat')
        result=self.index.search('Упаковка вскрыта. Сколько хранится товар Танзанит?',A,'chat')
        self.assertIn('7 календарных',result['text'])
        self.assertNotIn('Содалит',result['text'])

    def test_named_fact_does_not_apply_to_other_subject(self):
        result=self.index.search('Упаковка поставки Содалит вскрыта. Сколько дней обрабатывается поставка Содалит и сколько хранится товар Танзанит?',B)
        self.assertEqual(result['status'],'partial',result)
        self.assertIn('2 рабочих',result['text'])
        self.assertIn('Уточните',result['text'])
        self.assertNotIn('3 календарных',result['text'])

    def test_selected_document_keeps_explicit_scope(self):
        result=self.index.search('Упаковка вскрыта. Сколько хранится товар Халцедон?',A,selected_source='lab_cross_base')
        self.assertEqual(result['status'],'answer')
        self.assertIn('8 календарных',result['text'])

    def test_lower_authority_cannot_hide_applicable_conflict(self):
        records=copy.deepcopy(self.records)
        next(item for item in records if item['id']=='lab_cross_short')['authority']='reference'
        self.index.publish(records,self.index.begin('reference'))
        result=self.index.search('Упаковка вскрыта. Сколько хранится товар Халцедон?',A)
        self.assertEqual(result['status'],'conflict')

    def test_unrelated_damaged_table_does_not_capture_lowercase_subject(self):
        damaged=copy.deepcopy(self.records[0])
        damaged.update(id='damaged',title='Коралл',url=ORIGIN+'/materials/damaged')
        damaged['sections']=[{'heading':'Хранение товара Коралл','text':'| Товар | Срок хранения |\n| --- | --- |\n| Коралл | |'}]
        self.index.publish(self.records+[damaged],self.index.begin('damaged'))
        result=self.index.search('Упаковка вскрыта. Как долго можно держать товар халцедон?',A)
        self.assertEqual(result['status'],'conflict')
        self.assertEqual(self.index.search('Сколько хранится товар коралл?',A)['status'],'unavailable')


if __name__=='__main__':
    unittest.main()
