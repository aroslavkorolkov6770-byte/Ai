"""Товарная идентичность, неоднозначность, происхождение и сохранение контекста."""
import copy
import tempfile
import unittest
from pathlib import Path

from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch

ORIGIN = 'http://127.0.0.1:3040'
ACTOR = Principal('buyer', 'staff')


def catalog(identifier='catalog', revision='v1', rows=None, access=None):
    rows = rows or [
        ('stand-a', 'Подставка Север', 'Металлическая подставка. Свечки в комплект не входят.'),
        ('stand-b', 'Подставка Юг', 'Стеклянная подставка. Свечки входят в комплект.'),
    ]
    return {'id': identifier, 'revision': revision, 'title': 'Архив товаров', 'kind': 'document',
            'url': ORIGIN + '/materials/' + identifier, 'authority': 'reference',
            'access': access or {'scope': 'workspace'},
            'catalog_items': [{'key': key, 'heading': title} for key, title, text in rows],
            'sections': [{'heading': title, 'text': text} for key, title, text in rows]}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.index = ClosedSiteIndex([ORIGIN])
        self.publish([catalog()])

    def publish(self, sources):
        self.index.publish(sources, self.index.begin('snapshot'))

    def ask(self, question, chat='chat', actor=ACTOR, **extra):
        return self.index.search(question, actor, chat, **extra)

    def test_same_file_ambiguity_then_selection_keeps_requested_property(self):
        question = self.ask('Из чего сделана подставка?')
        self.assertEqual(question['status'], 'clarify')
        answer = self.ask('Север')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('Металлическая', answer['text'])
        self.assertNotIn('Свечки', answer['text'])
        self.assertEqual(answer['links'][0]['title'], 'Подставка Север')

    def test_same_file_followup_is_not_neighbor_product(self):
        self.ask('Из чего сделана подставка Север?')
        answer = self.ask('А свечки в комплекте?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('не входят', answer['text'])
        self.assertEqual(answer['evidence'][0]['heading'], 'Подставка Север')

    def test_short_explicit_product_change_reuses_property_not_old_product(self):
        self.ask('Из чего сделана подставка Север?')
        answer = self.ask('А Юг?')
        self.assertEqual(answer['status'], 'answer')
        self.assertIn('Стеклянная', answer['text'])
        self.assertNotIn('Металлическая', answer['text'])

    def test_duplicates_do_not_vote_or_create_two_options(self):
        rows = [('stand-a', 'Подставка Север', 'Металлическая подставка.')]
        self.publish([catalog(rows=rows), catalog(identifier='copy', rows=rows)])
        answer = self.ask('Из чего подставка Север?')
        self.assertEqual(answer['status'], 'answer')
        self.assertEqual(len(answer['links']), 1)
        self.assertEqual(len(answer['evidence']), 1)

    def test_conflicting_duplicate_materials_do_not_choose_majority(self):
        first = catalog(rows=[('a', 'Подставка Север', 'Металлическая подставка.')])
        second = catalog(identifier='other', rows=[('a', 'Подставка Север', 'Стеклянная подставка.')])
        third = copy.deepcopy(first)
        third['id'] = 'third'
        self.publish([first, second, third])
        self.assertEqual(self.ask('Из чего подставка Север?')['status'], 'conflict')

    def test_official_document_conflict_is_not_hidden_by_catalog(self):
        official = catalog(identifier='spec', rows=[('a', 'Подставка Север', 'Керамическая подставка.')])
        del official['catalog_items']
        official['authority'] = 'official'
        self.publish([catalog(), official])
        self.assertEqual(self.ask('Из чего подставка Север?')['status'], 'conflict')

    def test_matching_official_document_is_preferred_as_evidence(self):
        official = catalog(identifier='spec', rows=[('a', 'Подставка Север', 'Металлическая подставка.')])
        del official['catalog_items']
        official['authority'] = 'official'
        self.publish([catalog(), official])
        answer = self.ask('Из чего подставка Север?')
        self.assertEqual(answer['status'], 'answer')
        self.assertEqual(answer['evidence'][0]['id'], 'spec')

    def test_dimension_units_conflict_within_one_product(self):
        self.publish([catalog(rows=[('a', 'Доска Север 540х340 мм', 'Доска из дерева размером 540х340х5 см.')])])
        answer = self.ask('Какие размеры у доски Север?')
        self.assertEqual(answer['status'], 'conflict')
        self.assertEqual(answer['links'], [])

    def test_equivalent_dimensions_are_not_conflict(self):
        self.publish([catalog(rows=[('a', 'Доска Север 540х340 мм', 'Доска размером 54х34х0,5 см.')])])
        self.assertEqual(self.ask('Какие размеры у доски Север?')['status'], 'answer')

    def test_missing_material_does_not_borrow_neighbor(self):
        self.publish([catalog(rows=[('a', 'Подставка Север', 'Описание товара в выгрузке отсутствует.'),
                                    ('b', 'Подставка Юг', 'Стеклянная подставка.')])])
        self.assertEqual(self.ask('Из чего подставка Север?')['status'], 'not_found')

    def test_unimplemented_property_does_not_borrow_neighbor_on_followup(self):
        self.publish([catalog(rows=[('a', 'Подставка Север', 'Металлическая подставка.'),
                                    ('b', 'Подставка Юг', 'Срок гарантии на подставку Юг — 5 лет.')])])
        self.ask('Из чего подставка Север?')
        answer = self.ask('Какой у нее срок гарантии?')
        self.assertNotEqual(answer['status'], 'answer')
        self.assertNotIn('5 лет', answer['text'])

    def test_equivalent_dimensions_across_copies(self):
        self.publish([catalog(rows=[('a', 'Доска Север', 'Размер 540х340 мм.')]),
                      catalog(identifier='copy', rows=[('a', 'Доска Север', 'Размер 54х34 см.')])])
        self.assertEqual(self.ask('Какие размеры у доски Север?')['status'], 'answer')

    def test_unknown_price_does_not_use_dimensions_as_price(self):
        self.publish([catalog(rows=[('a', 'Доска Север 540х340 мм', 'Деревянная доска.')])])
        self.assertEqual(self.ask('Сколько стоит доска Север?')['status'], 'not_found')

    def test_hidden_product_does_not_appear_in_clarification(self):
        public = catalog(rows=[('a', 'Подставка Север', 'Металлическая подставка.')])
        private = catalog(identifier='hidden', rows=[('b', 'Подставка Секрет', 'Стеклянная подставка.')],
                          access={'scope': 'admin'})
        self.publish([public, private])
        answer = self.ask('Из чего сделана подставка?')
        self.assertEqual(answer['status'], 'answer')
        self.assertNotIn('Секрет', str(answer))

    def test_hidden_conflict_does_not_influence_public_answer(self):
        public = catalog(rows=[('a', 'Подставка Север', 'Металлическая подставка.')])
        private = catalog(identifier='hidden', rows=[('a', 'Подставка Север', 'Стеклянная подставка.')],
                          access={'scope': 'admin'})
        self.publish([public, private])
        self.assertEqual(self.ask('Из чего подставка Север?')['status'], 'answer')

    def test_other_user_and_chat_do_not_inherit_product(self):
        self.ask('Из чего подставка Север?')
        for actor, chat in [(Principal('other', 'staff'), 'chat'), (ACTOR, 'other')]:
            answer = self.ask('А свечки в комплекте?', actor=actor, chat=chat)
            self.assertEqual(answer['status'], 'clarify')

    def test_current_manifest_revokes_product_focus(self):
        self.ask('Из чего подставка Север?')
        answer = self.ask('А свечки в комплекте?', current_revisions={})
        self.assertNotEqual(answer['status'], 'answer')
        self.assertEqual(answer['evidence'], [])

    def test_new_revision_does_not_return_old_material(self):
        self.ask('Из чего подставка Север?')
        answer = self.ask('А свечки в комплекте?', current_revisions={'catalog': 'v2'})
        self.assertEqual(answer['status'], 'processing')
        self.publish([catalog(revision='v2', rows=[('a', 'Подставка Север', 'Керамическая подставка.')])])
        self.assertIn('Керамическая', self.ask('Из чего подставка Север?')['text'])

    def test_malformed_catalog_metadata_rejected(self):
        source = catalog()
        source['catalog_items'][0]['key'] = []
        with self.assertRaises(ValueError):
            self.publish([source])

    def test_plain_document_does_not_enable_product_mode(self):
        source = catalog()
        del source['catalog_items']
        self.publish([source])
        self.assertEqual(self.index.catalog_products, [])

    def test_persisted_product_focus_and_pending_choice(self):
        with tempfile.TemporaryDirectory(prefix='vates-catalog-') as directory:
            path = Path(directory) / 'search.sqlite'
            runtime = DurableSearch([ORIGIN], path)
            runtime.publish([catalog()], runtime.begin('initial'))
            ask = lambda text, chat='chat': runtime.query(text, ACTOR, chat, None, {'catalog': 'v1'})
            self.assertEqual(ask('Из чего подставка Север?')['status'], 'answer')
            self.assertEqual(ask('Из чего подставка?', 'pending')['status'], 'clarify')
            runtime = DurableSearch([ORIGIN], path)
            self.assertTrue(runtime.restored)
            self.assertIn('не входят', ask('А свечки в комплекте?')['text'])
            self.assertIn('Стеклянная', ask('Юг', 'pending')['text'])
            other = runtime.query('А свечки в комплекте?', Principal('other', 'staff'), 'chat', None, {'catalog': 'v1'})
            self.assertEqual(other['status'], 'clarify')


if __name__ == '__main__':
    unittest.main()
