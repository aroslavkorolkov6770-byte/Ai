"""Тип запрошенных сведений и свойства предмета; вымышленные материалы."""
import unittest
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_reasoning import lookup_question

READER=Principal('reader','staff')

def document(blocks,identifier='document:manual',profile=True,users=None):
    sections=[{'heading':heading,'text':body} for heading,body in blocks]
    if profile:
        sections=[{'heading':'','text':'[PDF_LOCAL_SECTIONS]\n[PDF_RULE_SECTIONS]\n'+
                   '\n\n'.join('# '+heading+'\n'+body for heading,body in blocks)}]
    return {'id':identifier,'revision':'r1','title':'Руководство МН-8','kind':'document',
            'url':'http://localhost/'+identifier.split(':')[-1],
            'access':{'scope':'restricted','users':users or ['reader']},'sections':sections}

def engine(*records):
    index=ClosedSiteIndex(['http://localhost']);index.publish(list(records),index.begin('new'))
    return index

class InformationTests(unittest.TestCase):
    def test_agents_are_not_preparation_steps(self):
        index=engine(document([
            ('Подготовка','Перед очисткой отключите аппарат. После очистки высушите корпус.'),
            ('Средства','Не применяйте раствор аммиака или порошок для чистки корпуса.')]))
        answer=index.search('Чем запрещено чистить МН-8?',READER)
        self.assertEqual(answer['status'],'answer')
        self.assertIn('аммиака',answer['text']);self.assertIn('порошок',answer['text'])
        self.assertNotIn('отключите',answer['text'])

    def test_allowed_and_prohibited_agents_are_distinct(self):
        index=engine(document([
            ('Средства','Для очистки используйте пену. Не используйте масло для чистки корпуса.')]))
        allowed=index.search('Чем можно чистить МН-8?',READER,'allowed')
        banned=index.search('Чем нельзя чистить МН-8?',READER,'banned')
        self.assertEqual(allowed['status'],'answer');self.assertIn('пену',allowed['text'])
        self.assertNotIn('масло',allowed['text']);self.assertIn('масло',banned['text'])
        self.assertNotIn('пену',banned['text'])

    def test_not_forbidden_is_not_a_ban(self):
        index=engine(document([
            ('Средства','Для очистки не запрещено использовать пену.'),
            ('Запрет','Для очистки запрещено использовать масло.')]))
        answer=index.search('Чем не запрещено чистить МН-8?',READER)
        self.assertEqual(answer['status'],'answer');self.assertIn('пену',answer['text'])
        self.assertNotIn('масло',answer['text'])

    def test_no_agent_fact_is_not_replaced_with_preparation(self):
        index=engine(document([('Подготовка','Перед очисткой отключите аппарат.')]))
        answer=index.search('Чем нельзя чистить МН-8?',READER)
        self.assertNotEqual(answer['status'],'answer')

    def test_exception_and_condition_are_preserved(self):
        for tail in ['Исключение: на резиновых деталях раствор не применяют.',
                     'Однако на резиновых деталях раствор не применяют.']:
            with self.subTest(tail=tail):
                index=engine(document([('Средства','Используйте раствор для очистки корпуса. '+tail)]))
                answer=index.search('Чем чистить МН-8?',READER)
                self.assertEqual(answer['status'],'answer');self.assertIn(tail,answer['text'])

    def test_requested_type_does_not_leak_to_next_question(self):
        index=engine(document([
            ('Средства','Не применяйте масло для очистки корпуса.'),
            ('Погружение','Не погружайте аппарат в воду.')]))
        index.search('Чем запрещено чистить МН-8?',READER,'one')
        answer=index.search('А погружать его в воду можно?',READER,'one')
        self.assertEqual(answer['status'],'answer');self.assertIn('Не погружайте',answer['text'])
        self.assertNotIn('масло',answer['text'])

    def test_local_pronoun_preserves_object_description(self):
        text='Везу стеклянный измерительный датчик. Как его защитить?'
        self.assertEqual(lookup_question(text),text)

    def test_explicit_new_object_does_not_inherit_description(self):
        self.assertNotIn('стеклянная',lookup_question('Колба Омега стеклянная. Как защитить Дельту?'))

    def test_packing_uses_object_features_in_same_message(self):
        index=engine(document([('Упаковка',
            'Стеклянные измерительные датчики помещают в жёсткий контейнер с мягкими вставками. '
            'Ткань защищают от влаги с помощью непромокаемого чехла.')],profile=False))
        answer=index.search('Везу стеклянный измерительный датчик. Как его защитить?',READER,'one','document:manual')
        self.assertEqual(answer['status'],'answer');self.assertIn('мягкими вставками',answer['text'])
        self.assertNotIn('Ткань защищают',answer['text'])

    def test_hidden_agents_are_not_retrieved(self):
        index=engine(document([('Подготовка','Перед очисткой отключите аппарат.')]),
                     document([('Средства','Не используйте масло для чистки корпуса.')],
                              'document:hidden',users=['owner']))
        answer=index.search('Чем нельзя чистить МН-8?',READER)
        self.assertNotEqual(answer['status'],'answer');self.assertFalse(answer['evidence'])

    def test_visible_text_cleaned_but_exact_evidence_retained(self):
        original=('Очистка ПРЕДУПРЕЖДЕНИЕ: Не используйте A масло для чистки корпуса. '
                  'A ПРЕДУПРЕЖДЕНИЕ: Не погружайте аппарат в воду.')
        index=engine(document([('Средства',original)]))
        answer=index.search('Чем запрещено чистить МН-8?',READER)
        self.assertEqual(answer['text'],'Не используйте масло для чистки корпуса.')
        self.assertIn('A масло',answer['evidence'][0]['quote'])

    def test_product_letter_without_warning_legend_is_preserved(self):
        index=engine(document([('Средства','Используйте A средство для чистки корпуса.')]))
        answer=index.search('Чем чистить МН-8?',READER)
        self.assertIn('A средство',answer['text'])

if __name__=='__main__':
    unittest.main()
