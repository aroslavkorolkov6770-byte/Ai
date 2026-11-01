"""Поведение условий, выбранного документа и памяти после уточнения."""
import json
import tempfile
import unittest
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal, terms, NO_DATA
from ai_core.site_search_conditions import atomic_observation
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.conditions_probe import sources, SPEC

ORIGIN = 'http://127.0.0.1:3040'
ACTOR = Principal('reader', 'staff')


def record(identifier, text, **extra):
    return {'id': identifier, 'revision': 'v1', 'title': 'Документ ' + identifier,
            'url': ORIGIN + '/materials/' + identifier, 'kind': 'document', 'access': {'scope': 'workspace'},
            'sections': [{'heading': 'Порядок', 'text': text}], **extra}


class ConditionsTests(unittest.TestCase):
    def make(self, *records):
        index = ClosedSiteIndex([ORIGIN])
        index.publish(list(records), index.begin('initial'))
        return index

    def test_frozen_twenty_scenarios(self):
        spec = json.loads(SPEC.read_text(encoding='utf-8'))
        index = self.make(*sources(spec))
        for case in spec['cases']:
            with self.subTest(case=case['id']):
                result = index.search(case['q'], Principal(case.get('user', 'a'), 'staff'), case.get('chat', case['id']))
                self.assertEqual(result['status'], case.get('status', 'answer'))
                for text in case.get('has', []):
                    self.assertIn(text.casefold(), result['text'].casefold())
                for text in case.get('absent', []):
                    self.assertNotIn(text.casefold(), result['text'].casefold())

    def test_unknown_is_not_false_but_false_short_circuits_and(self):
        doc = record('Топаз', 'Если заказ оплачен и есть паспорт, заказ Топаз хранится 8 дней. В остальных случаях заказ Топаз хранится 2 дня.')
        index = self.make(doc)
        self.assertEqual(index.search('Заказ Топаз оплачен. Сколько дней хранится?', ACTOR)['status'], 'clarify')
        result = index.search('Заказ Топаз не оплачен. Сколько дней хранится?', ACTOR)
        self.assertEqual(result['status'], 'answer')
        self.assertIn('2 дня', result['text'])

    def test_conditional_hypothesis_is_not_confused_with_question_about_a_fact(self):
        self.assertEqual(atomic_observation('заказ оплачен', 'Сколько хранится, если заказ оплачен?', terms), (True, True))
        self.assertEqual(atomic_observation('заказ оплачен', 'Оплачен ли заказ?', terms), (True, None))

    def test_mass_units_and_strict_boundaries(self):
        for question, expected in [('Вес посылки 79,9 г', False), ('Вес посылки 80 г', True),
                                   ('Вес посылки 80,1 г', True), ('Вес посылки 0,08 кг', True)]:
            with self.subTest(question=question):
                self.assertEqual(atomic_observation('вес посылки не менее 80 г', question, terms), (True, expected))
        self.assertEqual(atomic_observation('вес посылки больше 80 г', 'Вес посылки 80 г', terms), (True, False))

    def test_two_different_amounts_do_not_become_one_fact(self):
        self.assertEqual(atomic_observation('сумма заказа не менее 5000 рублей', 'Сумма заказа 3000 рублей или сумма заказа 7000 рублей', terms), (True, None))

    def test_correction_to_unknown_clears_previous_boolean(self):
        spec = json.loads(SPEC.read_text(encoding='utf-8'))
        index = self.make(*sources(spec))
        index.search('Заказ Аврора оплачен, упаковка не вскрыта. Сколько дней он хранится?', ACTOR, 'chat')
        result = index.search('А если не знаю, вскрыта ли упаковка?', ACTOR, 'chat')
        self.assertEqual(result['status'], 'clarify')
        self.assertNotIn('10 календарных', result['text'])

    def test_clarification_does_not_repeat_opposite_versions_of_one_fact(self):
        index = self.make(*sources(json.loads(SPEC.read_text(encoding='utf-8'))))
        result = index.search('Заказ Аврора оплачен. Сколько дней он хранится?', ACTOR)
        self.assertEqual(result['status'], 'clarify')
        self.assertEqual(result['text'], 'Уточните: упаковка вскрыта?')

    def test_adjacent_unparsed_exception_is_not_dropped(self):
        index = self.make(record('Спектр', 'Возврат товара Спектр разрешён.\nИсключение: вскрытые товары Спектр возврату не подлежат.'))
        result = index.search('Можно вернуть товар Спектр?', ACTOR)
        self.assertIn('не подлежат', result['text'])

    def test_short_exception_overrides_more_verbose_permission(self):
        index = self.make(record('Альфа', 'Если заказ оплачен, возврат товара Альфа разрешён. Исключение: если упаковка вскрыта, возврат запрещён.'))
        result = index.search('Заказ Альфа оплачен, упаковка вскрыта. Можно вернуть товар Альфа?', ACTOR, selected_source='Альфа')
        self.assertEqual(result['status'], 'answer')
        self.assertIn('запрещён', result['text'])
        self.assertNotIn('разрешён', result['text'])

    def test_exception_from_another_operation_does_not_replace_answer(self):
        index = self.make(record('Альфа', 'Если заказ оплачен, возврат товара Альфа разрешён. Если упаковка целая, хранение товара Альфа разрешено. Исключение: если упаковка вскрыта, хранение товара Альфа запрещено.'))
        result = index.search('Заказ Альфа оплачен, упаковка вскрыта. Можно оформить возврат товара Альфа?', ACTOR, selected_source='Альфа')
        self.assertEqual(result['status'], 'answer')
        self.assertIn('возврат товара Альфа разрешён', result['text'])
        self.assertNotIn('хранение', result['text'])

    def test_return_rule_does_not_answer_question_about_delivery(self):
        index = self.make(*sources(json.loads(SPEC.read_text(encoding='utf-8'))))
        result = index.search('Сколько часов длится доставка заказа Вега?', ACTOR)
        self.assertEqual(result['status'], 'not_found')
        self.assertEqual(result['text'], NO_DATA)

    def test_overview_explains_rules_without_requesting_personal_conditions(self):
        index = self.make(record('Лояльность', 'Скидка накопительная: с каждых 1000 рублей оплат к скидке прибавляется 1%, максимум — 12%. Скидка не сгорает. Один номер телефона могут использовать несколько человек — ограничений нет. Если номер не зарегистрирован — скидки нет, без вариантов.', title='Программа лояльности'))
        result = index.search('Как устроена программа лояльности?', ACTOR)
        self.assertEqual(result['status'], 'answer')
        for expected in ['1000', '12%', 'не зарегистрирован']:
            self.assertIn(expected, result['text'])

    def test_storage_location_keeps_negation_and_uncertainty(self):
        for question, expected in [('Товар не хранится в холодильнике.', False),
                                   ('Товар хранится в холодильнике.', True),
                                   ('Не знаю, хранится ли товар в холодильнике.', None)]:
            with self.subTest(question=question):
                self.assertEqual(atomic_observation('товар хранится в холодильнике', question, terms), (True, expected))
        self.assertEqual(atomic_observation('товар хранится в холодильнике', 'Я работаю в офисе.', terms), (False, None))
        self.assertEqual(atomic_observation('товар не хранится в холодильнике', 'Товар не хранится в холодильнике.', terms), (True, True))

    def test_decimal_correction_replaces_old_amount_in_memory(self):
        index = self.make(*sources(json.loads(SPEC.read_text(encoding='utf-8'))))
        selected = 'lab_cond_rubin'
        index.search('Сумма заказа Рубин 6000 рублей, заказ оплачен. Сколько стоит доставка?', ACTOR, 'chat', selected_source=selected)
        result = index.search('Уточнение: сумма заказа 4999.99 рублей, заказ оплачен. Сколько стоит доставка?', ACTOR, 'chat', selected_source=selected)
        self.assertIn('300 рублей', result['text'])
        self.assertIn('300 рублей', index.search('А сколько стоит доставка?', ACTOR, 'chat', selected_source=selected)['text'])

    def test_summary_named_file_overrides_previous_topic(self):
        index = self.make(record('Альфа', 'Заказ Альфа хранится 3 дня.'), record('Бета', 'Заказ Бета хранится 7 дней.'))
        index.search('Сколько хранится заказ Альфа?', ACTOR, 'chat')
        result = index.search('Перескажи файл Бета', ACTOR, 'chat')
        self.assertEqual(result['status'], 'answer')
        self.assertEqual({item['id'] for item in result['evidence']}, {'Бета'})
        self.assertIn('7 дней', result['text'])
        missing = index.search('Перескажи файл Неизвестный', ACTOR, 'chat')
        self.assertEqual(missing['status'], 'clarify')
        self.assertEqual(missing['evidence'], [])
        self.assertEqual(index.search('Перескажи этот файл', ACTOR, 'chat')['status'], 'clarify')

    def test_summary_is_only_selected_document_and_keeps_exception(self):
        index = self.make(record('Альфа', 'Заказ Альфа хранится 40 дней.'),
                          record('Бета', 'Документ описывает возврат Бета.\nВозврат Бета разрешён.\nИсключение: повреждённые товары возврату не подлежат.\nОбратитесь к координатору.'))
        index.search('Сколько хранится заказ Альфа?', ACTOR, 'chat')
        result = index.search('Кратко перескажи выбранный файл', ACTOR, 'chat', selected_source='Бета')
        self.assertEqual(result['status'], 'answer')
        self.assertIn('не подлежат', result['text'])
        self.assertNotIn('40 дней', result['text'])
        self.assertEqual({item['id'] for item in result['evidence']}, {'Бета'})
        self.assertEqual(len(result['links']), 1)

    def test_selected_file_overrides_old_followup(self):
        index = self.make(record('Альфа', 'Заказ Альфа хранится 3 дня.'), record('Бета', 'Заказ Бета хранится 7 дней.'))
        index.search('Сколько хранится заказ Альфа?', ACTOR, 'chat')
        result = index.search('А сколько дней?', ACTOR, 'chat', selected_source='Бета')
        self.assertIn('7 дней', result['text'])
        self.assertNotIn('3 дня', result['text'])

    def test_summary_requires_source_without_chat_context(self):
        index = self.make(record('Альфа', 'Заказ Альфа хранится 3 дня.'))
        self.assertEqual(index.search('Перескажи файл', ACTOR)['status'], 'clarify')

    def test_partial_ocr_is_not_presented_as_complete_summary(self):
        index = self.make(record('Альфа', 'Первые строки.', complete=False))
        self.assertEqual(index.search('Перескажи файл', ACTOR, selected_source='Альфа')['status'], 'unavailable')

    def test_selected_revoked_file_does_not_fall_back_or_leave_context(self):
        index = self.make(record('Альфа', 'Заказ Альфа хранится 3 дня.'))
        index.search('Сколько хранится заказ Альфа?', ACTOR, 'chat')
        result = index.search('Перескажи файл', ACTOR, 'chat', selected_source='Альфа', current_revisions={})
        self.assertEqual(result['text'], NO_DATA)
        self.assertEqual(result['links'], [])
        self.assertEqual(index.search('А сколько дней?', ACTOR, 'chat')['status'], 'clarify')

    def test_summary_hidden_expired_and_changed_files_fail_closed(self):
        index = self.make(record('Скрытый', 'Секрет 99101', access={'scope': 'admin'}), record('Альфа', 'Текст.'))
        hidden = index.search('Перескажи файл', ACTOR, selected_source='Скрытый')
        self.assertEqual(hidden['status'], 'not_found')
        self.assertNotIn('99101', json.dumps(hidden, ensure_ascii=False))
        changed = index.search('Перескажи файл', ACTOR, selected_source='Альфа', current_revisions={'Альфа': 'v2'})
        self.assertEqual(changed['status'], 'processing')

    def test_summary_preserves_sections_without_control_mark_noise(self):
        doc = record('Альфа', '')
        doc['sections'] = [{'heading': 'Назначение', 'text': 'Документ описывает приёмку.'},
                           {'heading': 'Действия', 'text': 'Сверьте накладную. Сосчитайте места.'},
                           {'heading': 'Ограничение', 'text': 'Повреждённую поставку нельзя размещать в зале.'},
                           {'heading': 'Метка', 'text': 'Контрольная отметка: 12345.'}]
        result = self.make(doc).search('Кратко расскажи, о чем документ, и выдели главное', ACTOR, selected_source='Альфа')
        self.assertEqual(result['status'], 'answer')
        for word in ['приёмку', 'накладную', 'нельзя']:
            self.assertIn(word, result['text'])
        self.assertNotIn('12345', result['text'])

    def test_conflicting_summary_is_not_merged_into_one_rule(self):
        index = self.make(record('Альфа', 'Заказ Альфа хранится 3 дня. Заказ Альфа хранится 7 дней.'))
        self.assertEqual(index.search('Краткое содержание файла', ACTOR, selected_source='Альфа')['status'], 'conflict')

    def test_summary_ocr_line_wrap_keeps_incident_action(self):
        scan = record('Кристалл', 'РЕГЛАМЕНТ ПРИЁМКИ\nПоставка проверяется после доставки.\nСверьте накладную и число мест.\nПри повреждении упаковки сделайте фото и\nуведомите старшего смены.\nКонтрольная отметка: 12345.')
        result = self.make(scan).search('Перескажи файл', ACTOR, selected_source='Кристалл')
        for term in ['накладную', 'число мест', 'фото и уведомите старшего']:
            self.assertIn(term, result['text'])
        self.assertNotIn('12345', result['text'])

    def test_boolean_fact_and_pending_question_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = json.loads(SPEC.read_text(encoding='utf-8'))
            records = sources(spec)
            revisions = {item['id']: item['revision'] for item in records}
            db = Path(directory) / 'search.sqlite3'
            runtime = DurableSearch([ORIGIN], db)
            runtime.publish(records, runtime.begin('test'))
            first = runtime.query('Заказ Аврора оплачен. Сколько дней он хранится?', ACTOR, 'chat', None, revisions)
            self.assertEqual(first['status'], 'clarify')
            restored = DurableSearch([ORIGIN], db)
            result = restored.query('Упаковка не вскрыта', ACTOR, 'chat', None, revisions)
            self.assertIn('10 календарных', result['text'])
            corrected = restored.query('А если упаковка вскрыта?', ACTOR, 'chat', None, revisions)
            self.assertIn('1 календарный', corrected['text'])
            again = DurableSearch([ORIGIN], db)
            self.assertIn('1 календарный', again.query('А сколько дней?', ACTOR, 'chat', None, revisions)['text'])
            self.assertEqual(again.query('А сколько дней?', Principal('another', 'staff'), 'chat', None, revisions)['status'], 'clarify')
            self.assertEqual(again.query('А сколько дней?', ACTOR, 'another', None, revisions)['status'], 'clarify')


if __name__ == '__main__':
    unittest.main()
