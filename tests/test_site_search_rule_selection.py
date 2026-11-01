"""Поиск условий и запрещающих отметок на иных вымышленных материалах."""
import unittest
from ai_core.site_search import ClosedSiteIndex, Principal, terms
from ai_core.site_search_ranking import matching_label_condition, question_weights

READER = Principal('reader', 'staff')


def document(blocks, identifier='document:rules', users=None, revision='r1'):
    return {'id': identifier, 'revision': revision, 'title': 'Правила центра АРХ',
            'kind': 'document', 'authority': 'official', 'url': 'http://localhost/' + identifier.split(':')[-1],
            'access': {'scope': 'restricted', 'users': users or ['reader']},
            'sections': [{'heading': '', 'text': '[PDF_LOCAL_SECTIONS]\n[PDF_RULE_SECTIONS]\n' +
                         '\n\n'.join('# ' + heading + '\n' + body for heading, body in blocks)}]}


def engine(*records):
    index = ClosedSiteIndex(['http://localhost'])
    index.publish(list(records), index.begin('rules'))
    return index


def storage_document():
    record = document([
        ('Очистка', 'Не применяйте порошок для чистки устройства.'),
        ('Упаковка', 'Храните упаковочные материалы в отдельном шкафу.'),
        ('Хранение', 'Если устройство не используется длительное время, подготовьте его к хранению. '
         'Перед хранением отсоедините аккумулятор и установите защитную крышку. '
         'Храните устройство на верхней полке в запираемом помещении. '
         'Исключение: повреждённый аккумулятор храните отдельно в специальном контейнере.')])
    record['title'] = 'Руководство МЛ-4'
    return record


class RuleSelectionTests(unittest.TestCase):
    def test_book_photography_does_not_select_visitor_registration(self):
        index = engine(document([
            ('Запись', 'Запись в АРХ бесплатна. Фотосъемка посетителя в АРХ производится при выдаче пропуска.'),
            ('Издания', 'Запрещается фотосъемка изданий, опубликованных до 1940 года.')]))
        answer = index.search('Можно сфотографировать в АРХ книгу, изданную в 1930 году?', READER)
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('до 1940 года', answer['text'])
        self.assertNotIn('выдаче пропуска', answer['text'])

    def test_visitor_photography_does_not_select_book_prohibition(self):
        index = engine(document([
            ('Запись', 'Фотосъемка посетителя проводится без головного убора.'),
            ('Издания', 'Фотосъемка книг запрещена.')]))
        answer = index.search('Как фотографируют посетителя АРХ?', READER)
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('без головного убора', answer['text'])
        self.assertNotIn('книг', answer['text'])

    def test_missing_book_rule_does_not_borrow_visitor_photography(self):
        index = engine(document([('Запись', 'Фотосъемка посетителя в АРХ производится при выдаче пропуска.')]))
        answer = index.search('Можно сфотографировать в АРХ книгу?', READER)
        self.assertNotEqual(answer['status'], 'answer')

    def test_photo_verb_finds_same_operation_and_preserves_ban(self):
        raw = 'Фотосъемка запрещена для предметов со штампом «не копировать». Исключение: письменное разрешение владельца.'
        index = engine(document([('Вход', 'Фотографирование посетителя производится на входе.'), ('Съёмка', raw)]))
        answer = index.search('У экспоната АРХ штамп «Не подлежит копированию». Можно его сфотографировать?', READER)
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('Фотосъемка запрещена', answer['text'])
        self.assertIn('письменное разрешение владельца', answer['text'])
        self.assertEqual(answer['evidence'][0]['quote'], raw)

    def test_permission_label_does_not_inherit_opposite_ban(self):
        index = engine(document([
            ('Запрет', 'Фотографирование предметов со штампом «не копировать» запрещено.'),
            ('Разрешение', 'Фотографирование предметов со штампом «копировать разрешено» допускается без вспышки.')]))
        answer = index.search('У экспоната АРХ штамп «Копировать разрешено». Можно его сфотографировать?', READER)
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('допускается без вспышки', answer['text'])
        self.assertNotIn('запрещено', answer['text'])

    def test_unknown_label_does_not_borrow_known_ban(self):
        index = engine(document([('Съёмка', 'Фотографирование предметов со штампом «не копировать» запрещено.')]))
        answer = index.search('У экспоната АРХ штамп «На проверке». Можно его сфотографировать?', READER)
        self.assertNotEqual(answer['status'], 'answer')
        self.assertFalse(answer['evidence'])

    def test_absent_label_does_not_trigger_its_rule(self):
        index = engine(document([('Съёмка', 'Фотографирование предметов со штампом «не копировать» запрещено.')]))
        answer = index.search('У экспоната АРХ нет штампа «Не копировать». Можно его сфотографировать?', READER)
        self.assertNotEqual(answer['status'], 'answer')

    def test_label_details_are_not_dropped(self):
        self.assertFalse(matching_label_condition('Есть штамп «Не копировать обложку»',
                         'Со штампом «не копировать» запрещено фотографировать.', terms))
        self.assertTrue(matching_label_condition('Есть штамп «Не подлежит копированию»',
                        'Со штампом «не копировать» запрещено фотографировать.', terms))

    def test_hidden_label_cannot_supply_evidence(self):
        visible = document([('Вход', 'Для посещения центра предъявите пропуск.')])
        hidden = document([('Съёмка', 'Фотографирование предметов со штампом «не копировать» запрещено.')],
                          'document:hidden', ['owner'])
        answer = engine(visible, hidden).search('У экспоната АРХ штамп «Не копировать». Можно его сфотографировать?', READER)
        self.assertNotEqual(answer['status'], 'answer')
        self.assertFalse(answer['evidence'])

    def test_changed_label_rule_uses_new_revision(self):
        record = document([('Съёмка', 'Фотографирование предметов со штампом «осмотрен» запрещено.')])
        index = engine(record)
        question = 'У экспоната АРХ штамп «Осмотрен». Можно его сфотографировать?'
        first = index.search(question, READER, 'same')
        self.assertIn('запрещено', first['text'])
        updated = document([('Съёмка', 'Фотографирование предметов со штампом «осмотрен» разрешено без вспышки.')], revision='r2')
        index.publish([updated], index.begin('revision'))
        second = index.search(question, READER, 'same')
        self.assertIn('разрешено без вспышки', second['text'])
        self.assertEqual(second['evidence'][0]['revision'], 'r2')

    def test_storage_followup_keeps_preparation_place_and_exception(self):
        index = engine(storage_document())
        first = index.search('Чем запрещено чистить МЛ-4?', READER, 'one')
        self.assertEqual(first['status'], 'answer')
        answer = index.search('А где и в каком состоянии хранить до следующего использования?', READER, 'one')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('отсоедините аккумулятор', answer['text'])
        self.assertIn('верхней полке', answer['text'])
        self.assertIn('повреждённый аккумулятор', answer['text'])
        self.assertNotIn('упаковочные материалы', answer['text'])

    def test_storage_followup_in_new_chat_does_not_borrow_device(self):
        index = engine(storage_document())
        index.search('Чем запрещено чистить МЛ-4?', READER, 'one')
        answer = index.search('А где и в каком состоянии хранить до следующего использования?', READER, 'two')
        self.assertNotEqual(answer['status'], 'answer')
        self.assertFalse(answer['evidence'])

    def test_location_alone_does_not_answer_requested_state(self):
        record = storage_document()
        record['sections'][0]['text'] = '[PDF_LOCAL_SECTIONS]\n[PDF_RULE_SECTIONS]\n# Хранение\nХраните устройство в кладовой.'
        answer = engine(record).search('Где и в каком состоянии хранить МЛ-4 до следующего использования?', READER)
        self.assertNotEqual(answer['status'], 'answer')

    def test_storage_request_type_does_not_filter_next_topic(self):
        index = engine(storage_document())
        first = index.search('Где и в каком состоянии хранить МЛ-4 до следующего использования?', READER, 'one')
        self.assertEqual(first['status'], 'answer')
        answer = index.search('А чем нельзя чистить?', READER, 'one')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('порошок', answer['text'])
        self.assertNotIn('аккумулятор', answer['text'])

    def test_storage_permission_does_not_relax_duration(self):
        for question in ['Можно хранить до следующего сезона?', 'Сколько месяцев хранить МЛ-4?']:
            with self.subTest(question=question):
                words = terms(question)
                self.assertEqual(question_weights(words, terms, question), question_weights(words, terms))

    def test_storage_limits_and_properties_keep_weight(self):
        question = 'Где хранить МЛ-4 при влажности 90 процентов до следующего сезона?'
        weights = question_weights(terms(question), terms, question)
        for word in terms('влажности 90 процентов'):
            self.assertEqual(weights[word], 1.0)

    def test_revoked_storage_document_is_not_inherited(self):
        record = storage_document()
        index = engine(record)
        index.search('Чем запрещено чистить МЛ-4?', READER, 'one')
        record['access'] = {'scope': 'restricted', 'users': ['owner']}
        index.publish([record], index.begin('revoked'))
        answer = index.search('А где и в каком состоянии хранить до следующего использования?', READER, 'one')
        self.assertNotEqual(answer['status'], 'answer')
        self.assertFalse(answer['evidence'])


if __name__ == '__main__':
    unittest.main()
