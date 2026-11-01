"""Приёмка адресных ссылок: конкретный фрагмент, права, редакция и рестарт."""
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ai_core.site_search import ClosedSiteIndex, Principal, readable
from ai_core.site_search_fragments import evidence_links, resolve_fragment
from ai_core.site_search_runtime import DurableSearch

ORIGIN = 'http://127.0.0.1:3040'
B = Principal('b', 'staff')
A = Principal('a', 'staff')


def document(**extra):
    return {'id': 'document:fixture', 'revision': 'v1', 'title': 'Учебный каталог', 'kind': 'document',
            'url': ORIGIN + '/tasks?tab=docs&documentId=fixture', 'fragment_url': ORIGIN + '/closed-search-fragment',
            'access': {'scope': 'restricted', 'users': ['b']},
            'catalog_items': [{'key': 'one', 'heading': 'Термос Север'}, {'key': 'two', 'heading': 'Термос Юг'}],
            'sections': [{'heading': 'Термос Север', 'text': 'Объём: 500 мл. Материал: сталь.'},
                         {'heading': 'Термос Юг', 'text': 'Объём: 700 мл. Материал: стекло.'}], **extra}


class FragmentTests(unittest.TestCase):
    def setUp(self):
        self.index = ClosedSiteIndex([ORIGIN])
        self.publish(document())

    def publish(self, source):
        self.index.publish([source], self.index.begin('snapshot'))

    def link(self, heading='Термос Север', quote='Объём: 500 мл.'):
        links = evidence_links(self.index, {'id': 'document:fixture', 'heading': heading, 'quote': quote})
        self.assertTrue(links)
        return {key: value[0] for key, value in parse_qs(urlsplit(links[0]['url']).query).items()}

    def open(self, link, actor=B, revisions=None):
        return resolve_fragment(self.index, link['source'], link['revision'], link['fragment'], actor,
                                revisions if revisions is not None else {'document:fixture': 'v1'}, readable)

    def test_comparison_links_open_different_products_in_one_file(self):
        answer = self.index.search('Сравни объем термоса Север и термоса Юг', B)
        self.assertEqual(answer['status'], 'answer')
        self.assertEqual(len(answer['links']), 2)
        self.assertNotEqual(answer['links'][0]['url'], answer['links'][1]['url'])
        first = self.open(self.link())
        self.assertEqual(first['status'], 'ready')
        self.assertIn('500 мл', first['text'])
        self.assertNotIn('700 мл', first['text'])

    def test_private_link_is_not_a_permission(self):
        self.assertEqual(self.open(self.link(), A), {'status': 'not_found', 'message': 'Материал недоступен.'})

    def test_fact_in_product_heading_opens_that_card(self):
        self.publish(document(catalog_items=[{'key': 'one', 'heading': 'Термос Север 500 мл'}],
            sections=[{'heading': 'Термос Север 500 мл', 'text': 'Описание отсутствует.'}]))
        result = self.open(self.link('Термос Север 500 мл', 'Термос Север 500 мл'))
        self.assertEqual(result['heading'], 'Термос Север 500 мл')
        self.assertEqual(result['text'], 'Описание отсутствует.')

    def test_live_revocation_precedes_exporter(self):
        self.assertEqual(self.open(self.link(), revisions={})['status'], 'not_found')

    def test_live_revision_precedes_exporter(self):
        result = self.open(self.link(), revisions={'document:fixture': 'v2'})
        self.assertEqual(result['status'], 'changed')
        self.assertNotIn('text', result)

    def test_new_revision_changes_fragment_address(self):
        old = self.link()
        updated = document(revision='v2')
        updated['sections'][0]['text'] = 'Объём: 600 мл. Материал: сталь.'
        self.publish(updated)
        new = self.link(quote='Объём: 600 мл.')
        self.assertNotEqual(old['fragment'], new['fragment'])
        self.assertEqual(self.open(old, revisions={'document:fixture': 'v2'})['status'], 'changed')
        self.assertIn('600 мл', self.open(new, revisions={'document:fixture': 'v2'})['text'])

    def test_removed_document_returns_no_data(self):
        link = self.link()
        self.index.publish([], self.index.begin('removed'))
        self.assertEqual(self.open(link)['status'], 'not_found')

    def test_archived_document_cannot_be_opened(self):
        link = self.link()
        self.publish(document(status='archived'))
        self.assertEqual(self.open(link)['status'], 'not_found')

    def test_unknown_fragment_is_not_replaced_with_first_section(self):
        link = {**self.link(), 'fragment': '0' * 64}
        self.assertEqual(self.open(link)['status'], 'not_found')

    def test_processing_does_not_serve_old_text(self):
        link = self.link()
        self.index.begin('processing')
        self.assertEqual(self.open(link)['status'], 'processing')

    def test_same_heading_points_to_matching_paragraph(self):
        self.publish(document(catalog_items=[], sections=[{'heading': 'Правила', 'text': 'Приёмка выполняется утром.\n\nВозврат выполняется вечером.'}]))
        value = self.open(self.link('Правила', 'Возврат выполняется вечером.'))
        self.assertIn('вечером', value['text'])
        self.assertNotIn('утром', value['text'])

    def test_no_exact_quote_has_no_fake_fragment(self):
        self.assertEqual(evidence_links(self.index, {'id': 'document:fixture', 'heading': 'Термос Север', 'quote': 'Неподтверждённый факт.'}), [])

    def test_expired_source_is_not_readable(self):
        link = self.link()
        self.publish(document(expires_at=1))
        self.assertEqual(self.open(link)['status'], 'not_found')

    def test_wrong_fragment_origin_rejected(self):
        with self.assertRaises(ValueError):
            self.publish(document(fragment_url='https://unrelated.example/closed-search-fragment'))

    def test_incomplete_source_not_returned_as_exact_text(self):
        link = self.link()
        self.publish(document(complete=False))
        self.assertEqual(self.open(link)['status'], 'not_found')

    def test_same_link_survives_sqlite_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'index.sqlite3'
            runtime = DurableSearch([ORIGIN], database)
            runtime.publish([document()], runtime.begin('first'))
            answer = runtime.query('Какой объем у термоса Север?', B, 'chat', None, {'document:fixture': 'v1'})
            target = {key: value[0] for key, value in parse_qs(urlsplit(answer['links'][0]['url']).query).items()}
            restored = DurableSearch([ORIGIN], database)
            self.assertTrue(restored.restored)
            result = restored.fragment(target['source'], target['revision'], target['fragment'], B, {'document:fixture': 'v1'})
            self.assertEqual(result['status'], 'ready')
            self.assertIn('500 мл', result['text'])


if __name__ == '__main__':
    unittest.main()
