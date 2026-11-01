"""Проверки страницы и геометрии без догадок о похожих разделах."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from ai_core.site_documents import extract_document
from ai_core.site_pdf_locations import extract_pdf_layout, locate_fragment, valid_box
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from ai_core.site_search_fragments import build_fragments

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'evaluation/closed_search/fixtures/pdf-focus'
ORIGIN = 'http://127.0.0.1:3040'
B = Principal('b', 'staff')


class PdfFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads((ASSETS / 'spec.json').read_text(encoding='utf-8'))
        cls.layouts, cls.urls = {}, {}
        for key in ['base', 'moved', 'removed', 'duplicate', 'scan']:
            cls.urls[key] = 'data:application/pdf;base64,' + base64.b64encode((ASSETS / (key + '.pdf')).read_bytes()).decode()
            cls.layouts[key] = extract_pdf_layout(cls.urls[key])

    def source(self, key):
        text, complete = extract_document(self.urls[key], key + '.pdf')
        return {'id': 'document:pdf', 'revision': key, 'title': 'Учебный PDF', 'kind': 'document',
                'url': ORIGIN + '/tasks?tab=docs&documentId=pdf', 'fragment_url': ORIGIN + '/closed-search-fragment',
                'access': {'scope': 'restricted', 'users': ['b']}, 'complete': complete,
                'sections': [{'heading': 'Учебный PDF', 'text': text}], 'pdf_layout': self.layouts[key]}

    def locate(self, key, text=None):
        return locate_fragment(self.layouts[key], text or self.spec['rule'])

    def test_native_page_two(self):
        self.assertEqual(self.locate('base')['initial_page'], 2)

    def test_box_matches_actual_line_coordinates(self):
        box = self.locate('base')['hits'][0]['rects'][0]
        self.assertAlmostEqual(box[0], 50 / 595, places=3)
        self.assertTrue(.20 < box[1] < .21)
        self.assertTrue(.21 < box[3] < .23)
        self.assertTrue(.57 < box[2] < .60)

    def test_move_changes_page_to_three(self):
        self.assertEqual(self.locate('moved')['initial_page'], 3)
        self.assertNotEqual(self.layouts['base']['sha256'], self.layouts['moved']['sha256'])

    def test_removed_rule_has_no_location(self):
        self.assertEqual(self.locate('removed')['status'], 'unlocated')

    def test_duplicate_rule_not_guessed(self):
        result = self.locate('duplicate')
        self.assertEqual(result['status'], 'ambiguous')
        self.assertNotIn('initial_page', result)

    def test_scan_uses_visible_geometry(self):
        result = self.locate('scan', self.spec['scan_rule'])
        self.assertEqual(result['initial_page'], 2)
        self.assertTrue(all(valid_box(box) for box in result['hits'][0]['rects']))

    def test_unknown_words_not_matched(self):
        self.assertEqual(self.locate('base', 'Поставку Лиственит принимает директор.')['status'], 'unlocated')

    def test_numbers_must_match(self):
        layout = {'sha256': 'a' * 64, 'page_count': 1, 'pages': [{'page': 1, 'words': [
            {'text': value, 'confidence': 100, 'box': [.1, .1, .3, .2]} for value in ['Срок', 'хранения', '17', 'дней']]}]}
        self.assertEqual(locate_fragment(layout, 'Срок хранения 7 дней')['status'], 'unlocated')

    def test_low_confidence_digit_not_silently_accepted(self):
        layout = {'sha256': 'a' * 64, 'page_count': 1, 'pages': [{'page': 1, 'words': [
            {'text': value, 'confidence': confidence, 'box': [.1, .1, .3, .2]}
            for value, confidence in [('Срок', 100), ('хранения', 100), ('7', 65), ('дней', 100)]]}]}
        self.assertEqual(locate_fragment(layout, 'Срок хранения 7 дней')['status'], 'unlocated')

    def test_cache_does_not_render_or_ocr_again(self):
        with patch('ai_core.site_documents._run', side_effect=AssertionError('Unexpected OCR')):
            self.assertEqual(extract_pdf_layout(self.urls['scan']), self.layouts['scan'])

    def test_search_link_targets_sentence_without_header(self):
        index = ClosedSiteIndex([ORIGIN])
        index.publish([self.source('base')], index.begin('base'))
        answer = index.search(self.spec['question'], B)
        self.assertEqual(answer['status'], 'answer')
        token = parse_qs(urlsplit(answer['links'][0]['url']).query)['fragment'][0]
        target = index.fragment_targets[('document:pdf', token)]
        self.assertEqual(target['text'], self.spec['rule'])
        self.assertEqual(target['pdf_location']['initial_page'], 2)

    def test_restart_preserves_location(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'search.sqlite3'
            runtime = DurableSearch([ORIGIN], db)
            runtime.publish([self.source('base')], runtime.begin('base'))
            restored = DurableSearch([ORIGIN], db)
            self.assertTrue(restored.restored)
            answer = restored.query(self.spec['question'], B, 'c', None, {'document:pdf': 'base'})
            token = parse_qs(urlsplit(answer['links'][0]['url']).query)['fragment'][0]
            result = restored.fragment('document:pdf', 'base', token, B, {'document:pdf': 'base'})
            self.assertEqual(result['pdf_location']['initial_page'], 2)
            self.assertEqual(restored.fragment('document:pdf', 'base', token, Principal('a', 'staff'), {'document:pdf': 'base'})['status'], 'not_found')

    def test_old_paragraph_links_remain_available(self):
        index = ClosedSiteIndex([ORIGIN])
        index.publish([self.source('base')], index.begin('base'))
        legacy, _ = build_fragments(index.passages)
        self.assertTrue(legacy)
        for key, target in legacy.items():
            self.assertIn(key, index.fragment_targets)
            self.assertEqual(index.fragment_targets[key]['text'], target['text'])
        answer = index.search(self.spec['question'], B)
        self.assertEqual(len(answer['links']), 1)


if __name__ == '__main__':
    unittest.main()
