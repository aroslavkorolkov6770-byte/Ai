"""Короткие технические уточнения на собственных числах, не из контрольных PDF."""
import tempfile
import unittest
from pathlib import Path
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch

ORIGIN='http://localhost'
READER=Principal('reader','staff')
BODY=('''[PDF_LOCAL_SECTIONS]
# Таблица 1
Наименование: РК 9 кВА; Размер, мм: 410х310х210 (Размеры и масса даны с кожухом (масса кожуха 11 кг)); Масса, кг: 74; Мощность, Вт: 80.
# Таблица 1
Наименование: РК 18 кВА; Размер, мм: 510х410х310; Масса, кг: 86; Мощность, Вт: 110.
# Гарантия
Гарантийный срок для блока РК составляет 18 месяцев со дня продажи. При нарушении условий хранения гарантия не действует.
# Таблица 2
Наименование: ДВГ; Масса, кг: 52; Размер, мм: 300х200х100.
''')


def source(body=BODY,revision='r1'):
    return {'id':'document:device','title':'Руководство РК','kind':'document','revision':revision,
            'url':ORIGIN+'/device','access':{'scope':'restricted','users':['reader']},
            'sections':[{'heading':'','text':body}]}


class ShortFollowupTests(unittest.TestCase):
    def setUp(self):
        self.index=ClosedSiteIndex([ORIGIN]);self.index.publish([source()],self.index.begin('r1'))

    def ask(self,question,chat='one',user=READER,selected='document:device'):
        return self.index.search(question,user,chat,selected,{'document:device':'r1'})

    def test_new_property_retains_single_device(self):
        self.ask('Какая масса РК 9 кВА?')
        answer=self.ask('А размеры?')
        self.assertEqual(answer['status'],'answer');self.assertIn('410х310х210',answer['text'])
        self.assertNotIn('510х410х310',answer['text']);self.assertTrue(answer['context_used'])

    def test_numeric_variant_replaces_previous_variant(self):
        self.ask('Какая масса РК 9 кВА?')
        answer=self.ask('А 18 кВА?')
        self.assertEqual(answer['status'],'answer');self.assertIn('86',answer['text']);self.assertNotIn('74',answer['text'])

    def test_followup_without_selected_file_uses_validated_context(self):
        self.ask('Какая масса РК 9 кВА?')
        answer=self.ask('А размеры?',selected=None)
        self.assertEqual(answer['status'],'answer');self.assertIn('410х310х210',answer['text'])

    def test_unknown_variant_does_not_reuse_previous_answer(self):
        self.ask('Какая масса РК 9 кВА?')
        self.assertNotEqual(self.ask('А 70 кВА?')['status'],'answer')

    def test_explicit_new_device_has_priority(self):
        self.ask('Какая масса РК 9 кВА?')
        answer=self.ask('Какие размеры РК 18 кВА?')
        self.assertIn('510х410х310',answer['text']);self.assertNotIn('410х310х210',answer['text'])

    def test_named_acronym_never_inherits_previous_device(self):
        self.ask('Какая масса РК 9 кВА?')
        answer=self.ask('А масса ДВГ?')
        self.assertEqual(answer['status'],'answer');self.assertIn('52',answer['text']);self.assertNotIn('74',answer['text'])

    def test_full_new_variant_inherits_only_property(self):
        self.ask('Какая масса РК 9 кВА?')
        answer=self.ask('А РК 18 кВА?')
        self.assertEqual(answer['status'],'answer');self.assertIn('86',answer['text']);self.assertNotIn('74',answer['text'])

    def test_unselected_document_answer_hides_extra_columns(self):
        answer=self.ask('Какая масса РК 9 кВА?',selected=None)
        self.assertEqual(answer['status'],'answer');self.assertNotIn('410х310х210',answer['text'])
        self.assertIn('410х310х210',answer['evidence'][0]['quote'])

    def test_unrelated_catalog_does_not_intercept_document_comparison(self):
        product={'id':'product:tea','revision':'r1','title':'Чай жасминовый','kind':'product',
                 'url':ORIGIN+'/tea','access':{'scope':'workspace'},
                 'sections':[{'heading':'Чай жасминовый','text':'Масса: 100 г. Состав: зелёный чай.'}]}
        self.index.publish([source(),product],self.index.begin('both'))
        answer=self.index.search('Сравни массу РК 9 кВА и РК 18 кВА.',READER,'comparison')
        self.assertEqual(answer['status'],'answer');self.assertIn('74',answer['text']);self.assertIn('86',answer['text'])
        self.assertEqual(self.index.search('А его размеры?',READER,'comparison')['status'],'clarify')

    def test_other_chat_does_not_inherit_device(self):
        self.ask('Какая масса РК 9 кВА?')
        self.assertEqual(self.ask('А размеры?',chat='other')['status'],'clarify')

    def test_two_devices_need_clarification(self):
        self.ask('Сравни массу РК 9 кВА и РК 18 кВА.')
        self.assertEqual(self.ask('А его размеры?')['status'],'clarify')

    def test_new_unrelated_topic_does_not_inherit_mass(self):
        self.ask('Какая масса РК 9 кВА?')
        self.assertNotEqual(self.ask('Как оформить отпуск сотруднику?')['status'],'answer')

    def test_shared_note_retained_without_unasked_columns(self):
        answer=self.ask('Какая масса РК 9 кВА?')
        self.assertIn('74',answer['text']);self.assertIn('11 кг',answer['text']);self.assertIn('с кожухом',answer['text'])
        self.assertNotIn('410х310х210',answer['text']);self.assertNotIn('Мощность',answer['text'])
        self.assertIn('410х310х210',answer['evidence'][0]['quote'])

    def test_conditional_guarantee_not_unconditionally_shortened(self):
        answer=self.ask('При нарушении условий хранения действует гарантия блока РК?')
        if answer['status']=='answer':
            self.assertIn('не действует',answer['text'])

    def test_acl_and_edition_invalidate_context(self):
        self.ask('Какая масса РК 9 кВА?')
        self.assertNotEqual(self.ask('А размеры?',user=Principal('outsider','staff'))['status'],'answer')
        self.index.publish([source(revision='r2')],self.index.begin('r2'))
        self.assertEqual(self.ask('А размеры?')['status'],'processing')

    def test_restart_preserves_bounded_document_context(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'index.db';runtime=DurableSearch([ORIGIN],file)
            runtime.publish([source()],runtime.begin('r1'))
            runtime.query('Какая масса РК 9 кВА?',READER,'chat','document:device',{'document:device':'r1'})
            restored=DurableSearch([ORIGIN],file)
            answer=restored.query('А размеры?',READER,'chat',None,{'document:device':'r1'})
            self.assertEqual(answer['status'],'answer');self.assertIn('410х310х210',answer['text'])


if __name__=='__main__':
    unittest.main()
