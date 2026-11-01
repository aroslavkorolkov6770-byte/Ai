"""Предметное доказательство важнее вводной и случайного совпадения слов.

Данные вымышлены и отличаются от документов известного экзамена.
"""
import unittest
from ai_core.site_search import ClosedSiteIndex, Principal

def source(blocks, identifier='document:pump', users=None):
    return {'id':identifier,'revision':'r1','title':'Насос НР-7', 'kind':'document',
            'url':'http://localhost/manual','authority':'official',
            'access':{'scope':'restricted','users':users or ['reader']},
            'sections':[{'heading':'','text':'[PDF_LOCAL_SECTIONS]\n[PDF_RULE_SECTIONS]\n'+
                         '\n\n'.join('# '+heading+'\n'+text for heading,text in blocks)}]}

def search(question, blocks):
    index=ClosedSiteIndex(['http://localhost'])
    index.publish([source(blocks)],index.begin('test'))
    return index.search(question,Principal('reader','staff'),'new')

class RankingTests(unittest.TestCase):
    def test_specific_object_beats_framing_and_heading(self):
        answer=search('Разрешает ли инструкция включать НР-7 через переходник?',[
            ('Первое включение','Инструкция содержит общие сведения о безопасности.'),
            ('Подключение','Не используйте насос с переходником.')])
        self.assertEqual(answer['status'],'answer')
        self.assertIn('Не используйте насос с переходником',answer['text'])

    def test_conjunction_does_not_outweigh_named_support(self):
        answer=search('Можно наливать жидкость в НР-7, пока он стоит на стойке?',[
            ('Безопасность','Не открывайте крышку, пока жидкость кипит.'),
            ('Безопасность','Не заливайте в насос жидкость при нахождении на стойке.')])
        self.assertEqual(answer['status'],'answer')
        self.assertIn('нахождении на стойке',answer['text'])

    def test_unmatched_subject_does_not_turn_intro_into_answer(self):
        answer=search('Разрешает ли инструкция включать НР-7 через стабилизатор?',[
            ('Первое включение','Инструкция содержит общие сведения о безопасности.'),
            ('Подключение','Не используйте насос с переходником.')])
        self.assertNotEqual(answer['status'],'answer')

    def test_registration_is_not_replaced_by_higher_unweighted_coverage(self):
        record=source([
            ('Регистрация','Право записи в Центр предоставляется с 21-летнего возраста по предъявлении удостоверения личности.'),
            ('Посещение','Пользователи имеют право посещать Центр по этим правилам. Для посещения выставки дети без документа допускаются с гражданином 25-летнего возраста.')])
        record['title']='Правила АРХ'
        record['sections'][0]['text']+='\n\n# Название\nАРХ — архивный центр.'
        index=ClosedSiteIndex(['http://localhost']);index.publish([record],index.begin('age'))
        result=index.search('С какого возраста можно стать пользователем АРХ по этим правилам и какой документ нужен?',Principal('reader','staff'),'age')
        self.assertEqual(result['status'],'answer')
        self.assertIn('21',result['text']);self.assertNotIn('25-летнего',result['text'])

    def test_close_conflicting_rules_are_not_resolved_by_score(self):
        answer=search('Какой срок обслуживания НР-7?',[
            ('Пункт 1','Срок обслуживания насоса составляет 8 дней.'),
            ('Пункт 2','Срок обслуживания насоса составляет 12 дней.')])
        self.assertEqual(answer['status'],'conflict')

    def test_missing_registration_rule_does_not_use_visiting_age(self):
        answer=search('Как зарегистрироваться для обслуживания НР-7?',[
            ('Посещение','В сервисный центр допускаются посетители с 20 лет.')])
        self.assertNotEqual(answer['status'],'answer')

    def test_conditional_tail_does_not_add_opposite_branch(self):
        answer=search('Что делать, если у НР-7 повреждён провод?',[
            ('Безопасность','Если провод повреждён, не включайте насос. В остальных случаях включайте насос.')])
        self.assertEqual(answer['status'],'answer')
        self.assertNotIn('В остальных случаях',answer['text'])

    def test_own_rule_keeps_related_condition(self):
        answer=search('Что делать, если у НР-7 повреждён провод?',[
            ('Безопасность','Если провод повреждён, не включайте насос. Обратитесь в мастерскую.'),
            ('Общие сведения','Инструкция описывает насос и его подключение.')])
        self.assertEqual(answer['status'],'answer')
        self.assertIn('не включайте',answer['text']);self.assertIn('мастерскую',answer['text'])

    def test_hidden_exact_match_cannot_affect_visible_answer(self):
        index=ClosedSiteIndex(['http://localhost'])
        index.publish([source([('Общее','Инструкция описывает насос.')]),
                       source([('Подключение','Не используйте насос со стабилизатором.')],
                              'document:hidden',['owner'])],index.begin('acl'))
        result=index.search('Можно включать НР-7 через стабилизатор?',Principal('reader','staff'),'acl')
        self.assertNotEqual(result['status'],'answer')
        self.assertFalse(result['evidence']);self.assertFalse(result['links'])

    def test_transport_request_does_not_choose_packaging_disposal(self):
        answer=search('Как упаковать НР-7 для перевозки?',[
            ('Утилизация','Упаковка должна быть передана в пункт переработки.'),
            ('Перевозка','Для транспортировки упакуйте насос в деревянный ящик.')])
        self.assertEqual(answer['status'],'answer')
        self.assertIn('деревянный',answer['text']);self.assertNotIn('переработки',answer['text'])

    def test_cleaning_request_does_not_choose_disposal_ban(self):
        answer=search('Чем запрещено чистить НР-7?',[
            ('Утилизация','Запрещено выбрасывать насос вместе с бытовым мусором.'),
            ('Очистка','Не используйте спирт для очистки корпуса.')])
        self.assertEqual(answer['status'],'answer');self.assertIn('спирт',answer['text'])

    def test_failed_followup_keeps_html_document_without_borrowing_other_file(self):
        own=source([('Упаковка','Стеклянные изделия упаковывают в жёсткую тару.')])
        own['title']='Порядок отправки Лира'
        own['sections']=[{'heading':'Упаковка','text':'Стеклянные изделия упаковывают в жёсткую тару.'}]
        other=source([('Защита','Класс электрической защиты прибора — второй.')],'document:other')
        index=ClosedSiteIndex(['http://localhost']);index.publish([own,other],index.begin('both'))
        first=index.search('Как упаковывают стеклянные изделия?',Principal('reader','staff'),'thread',own['id'])
        self.assertEqual(first['status'],'answer')
        second=index.search('А гарантия?',Principal('reader','staff'),'thread')
        self.assertNotEqual(second['status'],'answer')
        scope=('reader','staff',(),'thread')
        self.assertEqual(index.sessions[scope]['document_focus']['source'],own['id'])
        third=index.search('Теперь про электрическую защиту: какой класс?',Principal('reader','staff'),'thread')
        self.assertNotEqual(third['status'],'answer');self.assertFalse(third['evidence'])

    def test_failed_followup_cannot_restore_revoked_context(self):
        own=source([('Масса','Масса насоса составляет 17 кг.')])
        index=ClosedSiteIndex(['http://localhost']);index.publish([own],index.begin('one'))
        actor=Principal('reader','staff')
        index.search('Какая масса НР-7?',actor,'thread')
        own['access']={'scope':'restricted','users':['owner']}
        index.publish([own],index.begin('revoked'))
        answer=index.search('А гарантия?',actor,'thread')
        self.assertNotEqual(answer['status'],'answer')
        self.assertNotIn(('reader','staff',(),'thread'),index.sessions)

if __name__=='__main__':
    unittest.main()
