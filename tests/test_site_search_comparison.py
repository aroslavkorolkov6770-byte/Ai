"""Смысловые проверки сравнения: единицы, принадлежность чисел, память и доступ."""
import tempfile
import unittest
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch

ORIGIN = 'http://127.0.0.1:3040'
USER = Principal('tester', 'staff')


def source(identifier, name, text, key=None, **extra):
    return {'id': identifier, 'revision': 'v1', 'title': 'Учебная карточка ' + name, 'kind': 'document',
            'url': ORIGIN + '/materials/' + identifier, 'access': {'scope': 'workspace'},
            'catalog_items': [{'key': key or identifier, 'heading': name}],
            'sections': [{'heading': name, 'text': text}], **extra}


def pair():
    return [source('alpha', 'Прибор Альфа', 'Размеры изделия: 20×10×4 см. Размеры упаковки: 25×15×8 см. '
                   'Масса нетто: 500 г. Масса брутто: 900 г. Объём: 500 мл. Диаметр корпуса: 5 см.'),
            source('beta', 'Прибор Бета', 'Размеры изделия: 220×110×50 мм. Размеры упаковки: 30×20×10 см. '
                   'Масса без упаковки: 0,8 кг. Масса с упаковкой: 1,1 кг. Объём: 0,7 л. Диаметр корпуса: 50 мм.')]


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.index = ClosedSiteIndex([ORIGIN])
        self.publish(pair())

    def publish(self, sources):
        self.index.publish(sources, self.index.begin('new-snapshot'))

    def ask(self, question, actor=USER, chat='chat', **extra):
        return self.index.search(question, actor, chat, **extra)

    def test_compares_two_objects_instead_of_conflicting_them(self):
        answer = self.ask('Сравни размеры прибора Альфа и прибора Бета')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('20×10×4 см', answer['text'])
        self.assertIn('220×110×50 мм', answer['text'])
        self.assertNotIn('25×15×8', answer['text'])
        self.assertEqual({item['id'] for item in answer['evidence']}, {'alpha', 'beta'})

    def test_scope_followup_preserves_both_products(self):
        self.ask('Сравни размеры прибора Альфа и прибора Бета')
        answer = self.ask('А коробок?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('25×15×8 см', answer['text'])
        self.assertIn('30×20×10 см', answer['text'])

    def test_unit_conversion_for_heavier_without_packaging(self):
        answer = self.ask('Какой тяжелее без упаковки: прибор Альфа или прибор Бета?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('300 г', answer['text'])
        self.assertIn('Большее значение у «Прибор Бета»', answer['text'])
        self.assertNotIn('1,1 кг', answer['text'])

    def test_lightest_winner_is_reversed(self):
        answer = self.ask('Какой легче без упаковки: прибор Альфа или прибор Бета?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('Меньшее значение у «Прибор Альфа»', answer['text'])

    def test_packaging_mass_is_not_gross_mass(self):
        self.assertEqual(self.ask('Какая масса упаковки прибора Альфа?')['status'], 'not_found')
        answer = self.ask('Какова масса прибора Альфа с упаковкой?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('900 г', answer['text'])

    def test_equivalent_diameters_compare_as_equal(self):
        answer = self.ask('Сравни диаметры прибора Альфа и прибора Бета')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('одинаковые', answer['text'])

    def test_partial_answer_has_evidence_only_for_known_fact(self):
        self.publish([pair()[0], source('empty', 'Прибор Гамма', 'Материал: стекло.')])
        answer = self.ask('Сравни объем прибора Альфа и прибора Гамма')
        self.assertEqual(answer['status'], 'partial')
        self.assertIn('не указан', answer['text'])
        self.assertEqual({item['id'] for item in answer['evidence']}, {'alpha'})
        self.assertEqual(len(answer['links']), 1)

    def test_unknown_product_is_not_silently_omitted(self):
        answer = self.ask('Сравни объем прибора Альфа и прибора Ненайденный')
        self.assertEqual(answer['status'], 'not_found')
        self.assertEqual(answer['evidence'], [])

    def test_missing_property_is_clarified_then_remembered(self):
        self.assertEqual(self.ask('Сравни прибор Альфа и прибор Бета')['status'], 'clarify')
        answer = self.ask('По объему')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('500 мл', answer['text'])
        self.assertIn('0,7 л', answer['text'])

    def test_same_product_twice_does_not_create_fake_comparison(self):
        self.assertEqual(self.ask('Сравни объем прибора Альфа и прибора Альфа')['status'], 'clarify')

    def test_hidden_product_cannot_supply_comparison(self):
        rows = pair()
        rows[1]['access'] = {'scope': 'admin'}
        self.publish(rows)
        answer = self.ask('Сравни объем прибора Альфа и прибора Бета')
        self.assertEqual(answer['status'], 'not_found')
        self.assertNotIn('0,7', str(answer))

    def test_new_topic_clears_comparison(self):
        self.ask('Сравни объем прибора Альфа и прибора Бета')
        self.ask('Какие размеры прибора Альфа?')
        answer = self.ask('А упаковки?')
        self.assertEqual(answer['status'], 'answer')
        self.assertNotIn('Бета', answer['text'])
        self.assertIn('25×15×8', answer['text'])

    def test_item_size_does_not_use_only_available_package_size(self):
        self.publish([source('empty', 'Прибор Гамма', 'Размеры упаковки: 30×20×10 см.')])
        self.assertEqual(self.ask('Какие размеры прибора Гамма?')['status'], 'not_found')

    def test_explicit_box_of_item_is_still_box(self):
        self.publish([source('one', 'Прибор Альфа', 'Размеры коробки товара: 30×20×10 см.')])
        self.assertEqual(self.ask('Какие размеры прибора Альфа?')['status'], 'not_found')
        self.assertIn('30×20×10', self.ask('Какие размеры коробки прибора Альфа?')['text'])

    def test_package_difference_does_not_conflict_with_object(self):
        self.publish([pair()[0], source('copy', 'Прибор Альфа', 'Размеры изделия: 200×100×40 мм. Размеры коробки: 30×20×10 см.', key='alpha')])
        self.assertEqual(self.ask('Какие размеры прибора Альфа?')['status'], 'answer')
        self.assertEqual(self.ask('Какие размеры коробки прибора Альфа?')['status'], 'conflict')

    def test_component_length_does_not_become_item_length(self):
        self.publish([source('one', 'Прибор Альфа', 'Длина ручки: 12 см. Длина изделия: 30 см.')])
        answer = self.ask('Какая длина прибора Альфа?')
        self.assertIn('30 см', answer['text'])
        self.assertNotIn('12 см', answer['text'])

    def test_food_nutrition_is_not_weight(self):
        self.publish([source('food', 'Чай Роса', 'Масса нетто: 200 г. Пищевая ценность на 100 г: белки 5 г, жиры 3 г.')])
        self.assertEqual(self.ask('Сколько весит чай Роса?')['text'], '200 г.')

    def test_storage_capacity_is_not_container_mass(self):
        self.publish([source('can', 'Банка Роса 100 г', 'Банка для хранения чая.')])
        self.assertEqual(self.ask('Сколько весит банка Роса?')['status'], 'not_found')

    def test_numeric_conflict_inside_one_product(self):
        self.publish([source('one', 'Прибор Альфа 500 мл', 'Объём: 750 мл.')])
        self.assertEqual(self.ask('Какой объем прибора Альфа?')['status'], 'conflict')

    def test_equivalent_volume_inside_one_product(self):
        self.publish([source('one', 'Прибор Альфа 500 мл', 'Объём: 0,5 л.')])
        self.assertEqual(self.ask('Какой объем прибора Альфа?')['status'], 'answer')

    def test_range_is_not_replaced_with_endpoint(self):
        self.publish([source('one', 'Прибор Альфа', 'Длина изделия: 10–12 см.')])
        self.assertEqual(self.ask('Какая длина прибора Альфа?')['status'], 'not_found')

    def test_unrelated_already_word_does_not_activate_comparison(self):
        rule = source('rule', 'Оплаченный заказ', 'Если заказ оплачен, обратитесь к оператору.')
        del rule['catalog_items']
        self.publish(pair() + [rule])
        answer = self.ask('Что делать, если заказ уже оплачен?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('оператору', answer['text'])

    def test_restart_keeps_pair_and_pending_property_but_not_other_user(self):
        with tempfile.TemporaryDirectory(prefix='vates-comparison-') as folder:
            path = Path(folder) / 'search.sqlite'
            runtime = DurableSearch([ORIGIN], path)
            runtime.publish(pair(), runtime.begin('start'))
            ask = lambda question, chat='chat', actor=USER, revisions=None: runtime.query(question, actor, chat, None, revisions if revisions is not None else {'alpha': 'v1', 'beta': 'v1'})
            ask('Сравни размеры прибора Альфа и прибора Бета')
            ask('Сравни прибор Альфа и прибор Бета', chat='pending')
            runtime = DurableSearch([ORIGIN], path)
            self.assertTrue(runtime.restored)
            self.assertIn('25×15×8', ask('А коробок?')['text'])
            self.assertIn('0,7 л', ask('По объему', chat='pending')['text'])
            self.assertEqual(ask('А коробок?', actor=Principal('other', 'staff'))['status'], 'clarify')
            self.assertNotEqual(ask('А коробок?', revisions={'alpha': 'v1'})['status'], 'answer')


if __name__ == '__main__':
    unittest.main()
