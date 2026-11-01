"""Общие свойства разбиения; вымышленные числа, не ответы экзамена."""
import tempfile
import unittest
from pathlib import Path
from ai_core.site_pdf_sections import section_blocks, reading_lines, MARKER
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch


def record(text, revision='r1'):
    return {'id':'document:regulation','revision':revision,'title':'Правила ЦБК',
            'kind':'document','url':'http://localhost/rules','access':{'scope':'restricted','users':['reader']},
            'sections':[{'heading':'','text':'[PDF_LOCAL_SECTIONS]\n'+MARKER+'\n'+text}]}


class SectionTests(unittest.TestCase):
    def test_parent_prohibition_reaches_each_child_and_ends_at_sibling(self):
        blocks=section_blocks([(1,['4.2. Читателям запрещается:', '4.2.1. Выносить журналы.',
                                  '4.2.2. Снимать на камеру.', '4.3. Посещение выставки разрешено.'])])
        self.assertIn('Читателям запрещается: Снимать на камеру.',[body for _,body in blocks])
        self.assertEqual(blocks[-1][1],'Посещение выставки разрешено.')

    def test_multisentence_point_keeps_exception_across_page(self):
        blocks=section_blocks([(1,['4.1.7. Разрешено фотографировать документы.',
                                  'Не допускается фотографирование:', '– изданных до 1902 года;']),
                               (2,['– повреждённых водой.', '4.1.8. Вход бесплатный.'])])
        first=blocks[0][1]
        self.assertIn('Разрешено',first);self.assertIn('Не допускается',first)
        self.assertIn('1902',first);self.assertIn('повреждённых',first)
        self.assertNotIn('Вход',first)

    def test_parent_condition_reaches_children_but_not_next_rule(self):
        blocks=section_blocks([(1,['4.2. Если дверь закрыта:',
                                  '4.2.1. Позвоните дежурному.']),
                               (2,['4.2.2. Ожидайте у стойки.',
                                   '4.3. Заявка оформляется бесплатно.'])])
        self.assertIn('Если дверь закрыта: Позвоните дежурному.',[b for _,b in blocks])
        self.assertIn('Если дверь закрыта: Ожидайте у стойки.',[b for _,b in blocks])
        self.assertEqual(blocks[-1][1],'Заявка оформляется бесплатно.')

    def test_procedure_keeps_prerequisite_and_steps(self):
        blocks=section_blocks([(1,['3.2. Очистка','1. Отключите устройство от сети.',
                                  '2. Добавьте раствор.', '3. Промойте.', '3.3. Хранение','Высушите устройство.'])])
        procedure=blocks[0][1]
        self.assertIn('Отключите',procedure);self.assertIn('Добавьте',procedure)
        self.assertNotIn('Высушите',procedure)

    def test_uppercase_prohibition_is_not_dropped_as_title(self):
        blocks=section_blocks([(1,['ЗАПРЕЩАЕТСЯ:', '– разбирать устройство;', '– включать без крышки.'])])
        self.assertIn('ЗАПРЕЩАЕТСЯ',blocks[0][1]);self.assertIn('без крышки',blocks[0][1])

    def test_number_without_space_keeps_parent_prohibition(self):
        blocks=section_blocks([(1,['4.2.Пользователям запрещается:', '4.2.1.Выносить журналы.',
                                  '4.2.2.Снимать на камеру.', '4.3.Доступ в зал свободный.'])])
        self.assertIn('Пользователям запрещается: Снимать на камеру.',[b for _,b in blocks])
        self.assertNotIn('запрещается',blocks[-1][1])

    def test_related_safety_rule_is_not_shortened_to_permission(self):
        blocks=section_blocks([(1,['4.2.Пользователям запрещается:', '4.2.1.Выносить журналы.'])])
        text='\n'.join('# '+heading+'\n'+body for heading,body in blocks)
        index=ClosedSiteIndex(['http://localhost']);index.publish([record(text)],index.begin('a'))
        result=index.search('Можно выносить журналы ЦБК?',Principal('reader','staff'),'q')
        self.assertEqual(result['status'],'answer');self.assertIn('запрещается',result['text'])

    def test_inline_uppercase_citation_does_not_end_prohibition_scope(self):
        blocks=section_blocks([(1,['4.2. Пользователям запрещается:',
                                  '4.2.1. Публиковать материалы (ст. 250','УК РФ).',
                                  '4.2.2. Выносить журналы.'])])
        self.assertIn('Пользователям запрещается: Выносить журналы.',[b for _,b in blocks])

    def test_safety_bullet_keeps_its_condition_and_second_sentence(self):
        blocks=section_blocks([(1,['1.1. Общая безопасность','• Не включайте прибор, если провод повреждён.',
                                  'Обратитесь в мастерскую.', '• Не используйте удлинитель.'])])
        damaged=next(body for _,body in blocks if 'повреждён' in body)
        self.assertIn('Обратитесь',damaged);self.assertNotIn('удлинитель',damaged)

    def test_one_conditional_point_does_not_absorb_entire_document(self):
        blocks=section_blocks([(1,['2.1.1. Если дверь закрыта, доступ запрещён.',
                                  '2.1.2. Температура хранения от 7 до 19 градусов.',
                                  '2.1.3. Выдача карты бесплатна.'])])
        body='\n'.join('# '+heading+'\n'+text for heading,text in blocks)
        index=ClosedSiteIndex(['http://localhost']);index.publish([record(body)],index.begin('a'))
        self.assertEqual(len(index.passages),3)
        self.assertFalse(any('доступ' in p.text and 'карты' in p.text for p in index.passages))

    def test_wrapped_safety_heading_keeps_each_bullet_and_its_continuation(self):
        blocks=section_blocks([(1,['1.1 Общая','безопасность',
                                  '• Не включайте прибор, если повреждён провод.',
                                  'Обратитесь в мастерскую.',
                                  '• Не используйте переходник.',
                                  '• Перед очисткой отключите питание.'])])
        damaged=next(body for _,body in blocks if 'повреждён' in body)
        self.assertIn('Обратитесь',damaged)
        self.assertNotIn('переходник',damaged)
        self.assertIn('Не используйте переходник.',[body for _,body in blocks])
        self.assertIn('Перед очисткой отключите питание.',[body for _,body in blocks])

    def test_failed_question_keeps_explicit_document_but_not_other_user(self):
        index=ClosedSiteIndex(['http://localhost']);index.publish([record('# Порядок\nВыдача карты бесплатна.')],index.begin('a'))
        index.search('Какая масса книги ЦБК?',Principal('reader','staff'),'a')
        context=index.sessions.get(('reader','staff',(),'a'),{})
        self.assertEqual(context.get('document_focus',{}).get('source'),'document:regulation')
        self.assertNotEqual(index.search('А стоимость карты?',Principal('outsider','staff'),'a')['status'],'answer')

    def test_explicit_topic_survives_restart_and_not_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'search.sqlite'
            engine=DurableSearch(['http://localhost'],db)
            engine.publish([record('# Выдача\nВыдача карты бесплатна.')],engine.begin('r1'))
            engine.query('Какая масса книги ЦБК?',Principal('reader','staff'),'a',None,{'document:regulation':'r1'})
            restored=DurableSearch(['http://localhost'],db)
            self.assertIn('document_focus',restored.index.sessions[('reader','staff',(),'a')])
            restored.publish([record('# Выдача\nВыдача карты бесплатна.','r2')],restored.begin('r2'))
            self.assertNotIn(('reader','staff',(),'a'),restored.index.sessions)

    def test_ambiguous_new_chat_does_not_guess_procedure(self):
        index=ClosedSiteIndex(['http://localhost']);index.publish([record('# Очистка\nДобавьте 8 ложек раствора.')],index.begin('a'))
        answer=index.search('Сколько добавить?',Principal('reader','staff'),'new')
        self.assertEqual(answer['status'],'clarify')


if __name__=='__main__':
    unittest.main()
