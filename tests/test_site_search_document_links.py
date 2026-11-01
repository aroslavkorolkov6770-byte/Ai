"""Граф ссылок, редакции, ACL и сто последовательных вопросов после рестартов."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex,Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.document_links_probe import SPEC,ORIGIN,records,checks

A=Principal('closed_lab_a','staff')
B=Principal('closed_lab_b','staff')


class DocumentLinkTests(unittest.TestCase):
    def setUp(self):
        self.spec=json.loads(SPEC.read_text(encoding='utf-8'))
        self.records=records(self.spec)
        self.index=self.make(self.records)

    def make(self,items):
        index=ClosedSiteIndex([ORIGIN])
        index.publish(items,index.begin('links'))
        return index

    def ask(self,q,actor=A,chat='main',selected=None):
        return self.index.search(q,actor,chat,selected)

    def test_frozen_cases_and_alternates(self):
        for case in self.spec['cases']+self.spec['holdout']:
            with self.subTest(case=case['id']):
                result=self.ask(case['q'],B if case.get('user')=='b' else A,case.get('chat',case['id']),case.get('source'))
                self.assertEqual(checks(case,result),[],result)

    def test_one_hundred_turns_with_restarts_and_other_users(self):
        with tempfile.TemporaryDirectory(prefix='vates-links-') as folder:
            db=Path(folder)/'index.sqlite3'
            runtime=DurableSearch([ORIGIN],db)
            runtime.publish(self.records,runtime.begin('long'))
            manifest={item['id']:item['revision'] for item in self.records}
            for cycle in range(5):
                for position,case in enumerate(self.spec['dialogue']):
                    with self.subTest(cycle=cycle,id=case['id']):
                        if position in {1,9,11,17}:
                            runtime=DurableSearch([ORIGIN],db)
                        result=runtime.query(case['q'],A,'long',None,manifest)
                        self.assertEqual(checks(case,result),[],result)
                        if position==16:
                            for actor,chat in [(B,'long'),(A,'other')]:
                                result=runtime.query('А если упаковка вскрыта?',actor,chat,None,manifest)
                                self.assertEqual(result['status'],'clarify')

    def test_revision_and_acl_changes_invalidate_linked_memory(self):
        with tempfile.TemporaryDirectory(prefix='vates-links-revision-') as folder:
            db=Path(folder)/'index.sqlite3'
            runtime=DurableSearch([ORIGIN],db)
            runtime.publish(self.records,runtime.begin('v1'))
            manifest={item['id']:item['revision'] for item in self.records}
            runtime.query('Сколько хранится товар Нефрит?',A,'same',None,manifest)
            changed=copy.deepcopy(self.records)
            target=next(item for item in changed if item['id']=='link_exception')
            target['revision']='new-bytes'
            target['sections'][0]['text']=target['sections'][0]['text'].replace('2 календарных','5 календарных')
            manifest[target['id']]=target['revision']
            self.assertEqual(runtime.query('Упаковка вскрыта.',A,'same',None,manifest)['status'],'processing')
            runtime.publish(changed,runtime.begin('v2'))
            runtime=DurableSearch([ORIGIN],db)
            self.assertEqual(runtime.query('А если упаковка вскрыта?',A,'same',None,manifest)['status'],'clarify')
            result=runtime.query('Упаковка вскрыта. Сколько хранится товар Нефрит?',A,'same',None,manifest)
            self.assertIn('5 календарных',result['text'])
            target['access']={'scope':'admin'}
            target['revision']='revoked'
            manifest[target['id']]='revoked'
            runtime.publish(changed,runtime.begin('revoked'))
            runtime=DurableSearch([ORIGIN],db)
            result=runtime.query('Упаковка вскрыта. Сколько хранится товар Нефрит?',A,'same',None,manifest)
            self.assertEqual(result['status'],'unavailable')
            self.assertEqual(result['evidence'],[])

    def test_missing_from_current_manifest_cannot_use_dependency(self):
        manifest={item['id']:item['revision'] for item in self.records if item['id']!='link_exception'}
        result=self.index.search('Упаковка вскрыта. Сколько хранится товар Нефрит?',A,current_revisions=manifest)
        self.assertEqual(result['status'],'unavailable')

    def test_ambiguous_title_and_missing_section_fail_closed(self):
        for mode in ['duplicate','section','deleted','archived']:
            with self.subTest(mode=mode):
                data=copy.deepcopy(self.records)
                target=next(item for item in data if item['id']=='link_exception')
                if mode=='duplicate':data.append({**target,'id':'duplicate','url':ORIGIN+'/materials/duplicate'})
                if mode=='section':target['sections'][0]['text']=target['sections'][0]['text'].replace('# 2.','# 5.')
                if mode=='deleted':data.remove(target)
                if mode=='archived':target['status']='archived'
                result=self.make(data).search('Упаковка вскрыта. Сколько хранится товар Нефрит?',A)
                self.assertEqual(result['status'],'unavailable')

    def test_future_dependency_is_not_used_before_effective_date(self):
        data=copy.deepcopy(self.records)
        target=next(item for item in data if item['id']=='link_exception')
        target['sections'][0]['text']=target['sections'][0]['text'].replace('# 2. Хранение товара Нефрит','# 2. Хранение товара Нефрит\nДействует с 01.01.2099')
        index=self.make(data)
        result=index.search('Упаковка вскрыта. Сколько хранится товар Нефрит на 29.10.2026?',A)
        self.assertEqual(result['status'],'unavailable')
        result=index.search('Упаковка вскрыта. Сколько хранится товар Нефрит на 02.01.2099?',A)
        self.assertEqual(result['status'],'answer')
        self.assertIn('2 календарных',result['text'])

    def test_two_sections_of_same_dependency_keep_both_periods(self):
        data=copy.deepcopy(self.records)
        root=next(item for item in data if item['id']=='link_base')
        root['sections'][0]['text']+=' См. документ «Исключения_Нефрит.md», раздел 3, редакция 3.'
        target=next(item for item in data if item['id']=='link_exception')
        target['sections'][0]['text']+='\n# 3. Дополнение\nДействует с 01.01.2099\nТовар Нефрит держат на полке.'
        index=self.make(data)
        result=index.search('Упаковка вскрыта. Сколько хранится товар Нефрит на 29.10.2026?',A)
        self.assertEqual(result['status'],'unavailable')

    def test_transitive_private_dependency_does_not_leak_summary(self):
        data=copy.deepcopy(self.records)
        next(item for item in data if item['id']=='link_chain_end')['access']={'scope':'admin'}
        result=self.make(data).search('Кратко перескажи выбранный файл',A,selected_source='link_chain_base')
        self.assertEqual(result['status'],'unavailable')
        self.assertNotIn('бумажный',str(result))

    def test_unversioned_link_uses_only_unique_registered_document(self):
        data=copy.deepcopy(self.records)
        root=next(item for item in data if item['id']=='link_base')
        root['sections'][0]['text']=root['sections'][0]['text'].replace(', редакция 3','')
        result=self.make(data).search('Упаковка вскрыта. Сколько хранится товар Нефрит?',A)
        self.assertEqual(result['status'],'answer')
        self.assertIn('2 календарных',result['text'])

    def test_malformed_reference_and_literal_internal_word_are_data(self):
        data=copy.deepcopy(self.records)
        root=next(item for item in data if item['id']=='link_base')
        root['sections'][0]['text']='Товар Нефрит хранится 8 дней. См. документ неизвестный. VATESDOCREF99X'
        result=self.make(data).search('Сколько хранится товар Нефрит?',A)
        self.assertEqual(result['status'],'unavailable')

    def test_independent_same_subject_is_conflict_including_lowercase(self):
        data=copy.deepcopy(self.records)
        other={**data[0],'id':'other-rule','title':'Режим товара','url':ORIGIN+'/materials/other-rule',
               'sections':[{'heading':'Нефрит','text':'Если упаковка не вскрыта, товар Нефрит хранится 5 календарных дней. Если упаковка вскрыта, товар Нефрит хранится 1 календарный день.'}]}
        index=self.make(data+[other])
        for subject in ['Нефрит','нефрит']:
            result=index.search('Упаковка не вскрыта. Сколько хранится товар '+subject+'?',A)
            self.assertEqual(result['status'],'conflict')
        result=index.search('Упаковка вскрыта. Сколько хранится товар Нефрит?',A,selected_source='link_base')
        self.assertEqual(result['status'],'answer')
        self.assertIn('2 календарных',result['text'])

    def test_markdown_url_is_resolved_only_inside_registered_sources(self):
        for address,expected in [(ORIGIN+'/materials/link_exception#section-2','answer'),
                                 ('/materials/link_exception#2','answer'),
                                 ('https://external.invalid/materials/link_exception#section-2','unavailable'),
                                 ('http://[invalid#section-2','unavailable')]:
            with self.subTest(address=address):
                data=copy.deepcopy(self.records)
                root=next(item for item in data if item['id']=='link_base')
                root['sections'][0]['text']='Товар Нефрит хранится 8 календарных дней. См. [Уточнение]('+address+'), редакция 3.'
                result=self.make(data).search('Упаковка вскрыта. Сколько хранится товар Нефрит?',A,selected_source='link_base')
                self.assertEqual(result['status'],expected)
                if expected=='answer':self.assertIn('2 календарных',result['text'])

    def test_graph_is_not_recompiled_for_each_question(self):
        with patch('ai_core.site_search.compile_documents',side_effect=AssertionError('graph rebuilt during query')):
            result=self.ask('Упаковка вскрыта. Сколько хранится товар Нефрит?',selected='link_base')
            self.assertEqual(result['status'],'answer')

    def test_excessive_outgoing_links_fail_only_their_document(self):
        data=copy.deepcopy(self.records)
        root=next(item for item in data if item['id']=='link_base')
        root['sections'][0]['text']+=' См. документ «Исключения_Нефрит.md», раздел 2, редакция 3.'*17
        index=self.make(data)
        self.assertEqual(index.search('Сколько хранится товар Нефрит?',A)['status'],'unavailable')
        self.assertIn('диспетчер',index.search('Кто принимает товар Танзанит?',A)['text'])

    def test_broken_storage_reference_does_not_block_delivery(self):
        delivery={**self.records[0],'id':'delivery-rubin','title':'Доставка Рубин','url':ORIGIN+'/materials/delivery-rubin',
                  'sections':[{'heading':'Доставка','text':'Доставка заказа Рубин стоит 300 рублей.'}]}
        index=self.make(self.records+[delivery])
        self.assertIn('300 рублей',index.search('Сколько стоит доставка заказа Рубин?',A)['text'])
        self.assertEqual(index.search('Сколько хранится товар Рубин?',A)['status'],'unavailable')


if __name__=='__main__':unittest.main()
