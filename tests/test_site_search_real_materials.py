"""Регрессии, найденные в настоящих ЧаВо, каталоге и инструкциях.

Малые независимые примеры не зависят от наличия личной папки Downloads.
"""
import io
import unittest
import zipfile
from xml.sax.saxutils import escape

from ai_core.site_documents import _word_text
from ai_core.site_search import ClosedSiteIndex, Principal, terms
from ai_core.site_search_conditions import atomic_observation, rules
from ai_core.site_search_language import supplies_facet
from ai_core.site_search_reasoning import lookup_question

ORIGIN = 'http://127.0.0.1:3040'
READER = Principal('reader', 'staff')


def document(text, title='Инструкция', identifier='rules'):
    return {'id': identifier, 'revision': 'v1', 'title': title, 'kind': 'document',
            'url': ORIGIN + '/files/' + identifier, 'access': {'scope': 'workspace'},
            'sections': [{'heading': title, 'text': text}]}


def index_for(*records):
    index = ClosedSiteIndex([ORIGIN])
    index.publish(list(records), index.begin('regression'))
    return index


def word_text(paragraphs):
    buffer = io.BytesIO()
    namespace = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    body = ''.join('<w:p><w:r><w:t>' + escape(text) + '</w:t></w:r></w:p>' for text in paragraphs)
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{namespace}"><w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(buffer) as archive:
        return _word_text(archive)


class RealMaterialRegressionTests(unittest.TestCase):
    def test_question_answer_pairs_keep_only_the_relevant_answer(self):
        text = word_text(['Частые вопросы', '- Как упаковывают книги?', '- Книги кладут в бумажные пакеты.',
                          '- Что делать при повреждении книги?', '- Если книга повреждена, обратитесь к библиотекарю.'])
        result = index_for(document(text)).search('Как упаковывают книги?', READER)
        self.assertEqual(result['status'], 'answer')
        self.assertIn('бумажные пакеты', result['text'])
        self.assertNotIn('библиотекарю', result['text'])
        self.assertNotIn('Частые вопросы', result['text'])

    def test_question_without_answer_does_not_consume_the_next_question(self):
        text = word_text(['- Где хранить книгу?', '- Когда выдавать книгу?', '- По расписанию.'])
        self.assertNotIn('# Где хранить книгу?', text)
        self.assertIn('- Где хранить книгу?', text)

    def test_dash_rule_does_not_cut_off_actions_at_a_later_comma(self):
        text = 'Если ошибка выявлена до закрытия смены — продавец проверяет журнал, затем исправляет запись.'
        parsed = rules(text, ClosedSiteIndex._sentences, None, supplies_facet)
        self.assertEqual(parsed[0].condition, 'ошибка выявлена до закрытия смены')
        self.assertIn('затем исправляет запись', parsed[0].body)

    def test_temporal_opposites_and_uncertainty(self):
        atom = 'ошибка выявлена до закрытия смены'
        examples = [('Ошибка выявлена до закрытия смены.', (True, True)),
                    ('Ошибка выявлена после закрытия смены.', (True, False)),
                    ('Не знаю, ошибка выявлена до закрытия смены или после.', (True, None)),
                    ('Ошибка выявлена не после закрытия смены.', (True, None))]
        for text, expected in examples:
            with self.subTest(text=text):
                self.assertEqual(atomic_observation(atom, text, terms), expected)

    def test_temporal_event_cannot_be_taken_from_an_unrelated_operation(self):
        self.assertEqual(atomic_observation('ошибка выявлена до закрытия смены',
                                           'Ошибка выявлена после проверки склада.', terms), (False, None))

    def test_local_temporal_reference_is_self_contained(self):
        question = 'Смена уже закрыта, после этого нашли ошибку. Кто оформляет исправление?'
        self.assertEqual(lookup_question(question), question)
        self.assertEqual(atomic_observation('ошибка выявлена после закрытия смены', question, terms), (True, True))

    def test_new_named_subject_keeps_priority_over_introductory_fact(self):
        self.assertEqual(lookup_question('Ошибка в чеке. Кто принимает заказ Альтаир?'), 'Кто принимает заказ Альтаир?')

    def test_before_after_returns_the_matching_role_only(self):
        text = '# Действия при ошибке\nЕсли ошибка выявлена до закрытия смены — продавец проверяет журнал и исправляет запись. '
        text += 'Если ошибка выявлена после закрытия смены — администратор оформляет акт.'
        index = index_for(document(text, 'Исправление ошибки'))
        for when, expected, unwanted in [('до', 'продавец', 'администратор'), ('после', 'администратор', 'продавец')]:
            result = index.search(f'Ошибка выявлена {when} закрытия смены. Кто её исправляет?', READER, when)
            self.assertEqual(result['status'], 'answer', result)
            self.assertIn(expected, result['text'])
            self.assertNotIn(unwanted, result['text'])

    def test_price_question_does_not_return_material(self):
        index = index_for(document('Металлический держатель для книг.', 'Держатель Сапсан'))
        for qualifier in ('сейчас', 'сегодня', 'примерно'):
            self.assertEqual(index.search(f'Сколько {qualifier} стоит держатель Сапсан?', READER, qualifier)['status'], 'not_found')

    def test_packaging_enumeration_keeps_all_options(self):
        index = index_for(document('# Варианты упаковки книг\nКниги кладут в бумажные пакеты. Также доступны картонные коробки.'))
        result = index.search('В каких пакетах можно получить книги?', READER)
        self.assertEqual(result['status'], 'answer')
        self.assertIn('бумажные пакеты', result['text'])
        self.assertIn('картонные коробки', result['text'])

    def test_unrelated_product_condition_does_not_override_exact_faq(self):
        faq = document('# Минимальное взвешивание чая\nМинимальное взвешивание чая — 70 г.', 'Частые вопросы')
        item = document('# Весовой чай\nЕсли напиток остыл, его подогревают.', 'Каталог', 'catalog')
        result = index_for(faq, item).search('Сколько минимально можно купить чая вразвес?', READER)
        self.assertEqual(result['status'], 'answer', result)
        self.assertIn('70 г', result['text'])


if __name__ == '__main__':
    unittest.main()
