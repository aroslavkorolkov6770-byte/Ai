"""Границы больших документов, возобновление и прогресс редакций без генерации PDF."""
import base64
import json
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from ai_core.site_documents import extract_document
from ai_core.site_document_cache import ExtractionCancelled
from ai_core.site_search_runtime import DurableSearch
from ai_core.site_scan_layout import compose_pages
from ai_core.site_scan_tables import describe_table
from ai_core.site_search import ClosedSiteIndex
from ai_core.site_pdf_locations import locate_fragment, prepare_pdf_lookup

URL = 'data:application/pdf;base64,' + base64.b64encode(b'%PDF-test-double').decode()


class Page(dict):
    calls = []

    def __init__(self, number, text):
        super().__init__()
        self.number, self.text = number, text

    def extract_text(self):
        self.calls.append(self.number)
        return self.text


class LargePdfTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name) / 'cache'
        self.patch = patch('ai_core.site_documents.CACHE', self.cache)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        # Эти тесты моделируют PdfReader, а не реальные байты PDF. Новый
        # геометрический профиль проверяется отдельно на настоящем документе.
        technical = patch('ai_core.site_documents.extract_technical_pdf', return_value=None)
        technical.start()
        self.addCleanup(technical.stop)
        manual = patch('ai_core.site_documents.extract_manual_pdf', return_value=None)
        manual.start()
        self.addCleanup(manual.stop)
        Page.calls = []

    def pages(self, count=92):
        reader = type('Reader', (), {})()
        reader.pages = [Page(n, f'Страница {n}. Здесь находится самостоятельный раздел большого учебного документа для проверки чтения.\n'
                            + ('Содержимое страницы сохраняется полностью без сокращения текста.\n' * 45)) for n in range(1, count+1)]
        return patch('ai_core.site_documents.PdfReader', return_value=reader)

    def test_all_92_pages_and_more_than_200000_characters(self):
        events = []
        with self.pages():
            text, complete = extract_document(URL, 'large.pdf', progress=lambda value: events.append(value.copy()))
        self.assertTrue(complete)
        self.assertGreater(len(text), 200000)
        self.assertIn('Страница 92.', text)
        self.assertEqual(events[-1], {'pages_read': 92, 'pages_total': 92})
        self.assertEqual(Page.calls, list(range(1, 93)))
        with patch('ai_core.site_documents.PdfReader', side_effect=AssertionError('Cache must avoid PDF work')):
            self.assertEqual(extract_document(URL, 'large.pdf'), (text, complete))

    def test_interruption_resumes_and_preserves_identical_text(self):
        def cancel(value):
            if value.get('pages_read') == 41:
                raise ExtractionCancelled()
        with self.pages():
            with self.assertRaises(ExtractionCancelled):
                extract_document(URL, 'large.pdf', progress=cancel)
        self.assertEqual(Page.calls, list(range(1, 42)))
        Page.calls = []
        with self.pages():
            resumed = extract_document(URL, 'large.pdf')
        self.assertEqual(Page.calls, list(range(42, 93)))
        with tempfile.TemporaryDirectory() as other, patch('ai_core.site_documents.CACHE', Path(other)), self.pages():
            self.assertEqual(resumed, extract_document(URL, 'large.pdf'))

    def test_corrupted_page_is_re_read(self):
        def cancel(value):
            if value.get('pages_read') == 2:
                raise ExtractionCancelled()
        with self.pages(3), self.assertRaises(ExtractionCancelled):
            extract_document(URL, 'large.pdf', progress=cancel)
        checkpoint = next(self.cache.glob('*-pages/1.json'))
        saved = json.loads(checkpoint.read_text(encoding='utf-8'))
        saved['value']['text'] = 'Изменённое значение без совпадающего хеша.'
        checkpoint.write_text(json.dumps(saved), encoding='utf-8')
        Page.calls = []
        with self.pages(3):
            result = extract_document(URL, 'large.pdf')
        self.assertEqual(Page.calls, [1, 3])
        self.assertNotIn('Изменённое значение', result[0])

    def test_resource_limit_never_claims_complete(self):
        with self.pages(93), patch('ai_core.site_documents.MAX_PDF_PAGES', 92):
            text, complete = extract_document(URL, 'large.pdf')
        self.assertFalse(complete)
        self.assertTrue(text.startswith('[STRUCTURE_UNCERTAIN]'))
        self.assertNotIn('Страница 93.', text)

    def test_decimal_table_numbers_keep_separate_footnotes(self):
        text = compose_pages(['Таблица 3.1\nСрок [1].\n[1]: 4 дня.',
                              'Таблица 3.2\nСрок [1].\n[1]: 9 дней.'], [], describe_table)
        self.assertNotIn('[STRUCTURE_UNCERTAIN]', text)
        self.assertIn('[t3-1-1]: 4 дня.', text)
        self.assertIn('[t3-2-1]: 9 дней.', text)
        duplicate = compose_pages(['Таблица 3.1\nСрок [1].', 'Таблица 3.1\n[1]: 9 дней.'], [], describe_table)
        self.assertIn('[STRUCTURE_UNCERTAIN]', duplicate)

    def test_old_revision_cannot_update_progress_and_ready_status_survives_restart(self):
        path = Path(self.directory.name) / 'index.sqlite3'
        runtime = DurableSearch(['http://localhost'], path)
        old = runtime.begin('old')
        current = runtime.begin('current')
        self.assertFalse(runtime.update_document_progress('document:a', 'old', old, {'state': 'ready'}))
        self.assertTrue(runtime.update_document_progress('document:a', 'current', current, {'state': 'reading', 'pages_read': 41}))
        self.assertEqual(runtime.status()['documents'][0]['pages_read'], 41)
        source = {'id': 'document:a', 'revision': 'current', 'title': 'Документ', 'url': 'http://localhost/document',
                  'kind': 'document', 'access': {'scope': 'workspace'}, 'sections': [], 'complete': True,
                  'processing': {'state': 'review', 'pages_read': 92, 'pages_total': 92}}
        runtime.publish([source], current)
        self.assertEqual(DurableSearch(['http://localhost'], path).status()['documents'], runtime.status()['documents'])

    def test_status_and_revision_change_do_not_wait_for_heavy_index_build(self):
        runtime = DurableSearch(['http://localhost'], Path(self.directory.name) / 'slow.sqlite3')
        generation = runtime.begin('old')
        entered, release = threading.Event(), threading.Event()
        original = ClosedSiteIndex.publish
        def slow(index, sources, generation, aliases=None):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
            return original(index, sources, generation, aliases)
        with ThreadPoolExecutor(max_workers=2) as executor, patch.object(ClosedSiteIndex, 'publish', slow):
            build = executor.submit(runtime.publish, [], generation)
            try:
                self.assertTrue(entered.wait(1))
                status = executor.submit(runtime.status).result(timeout=1)
                self.assertEqual(status['state'], 'processing')
                current = executor.submit(runtime.begin, 'new').result(timeout=1)
                self.assertGreater(current, generation)
            finally:
                release.set()
            self.assertFalse(build.result(timeout=1))
        self.assertEqual(runtime.index.snapshot, 'new')

    def test_reused_coordinate_index_keeps_duplicate_and_uncertain_behavior(self):
        words = [{'text': word, 'confidence': confidence, 'box': [.1, .1, .3, .2]}
                 for word, confidence in [('Срок', 100), ('хранения', 100), ('7', 100), ('дней', 100),
                                          ('Срок', 100), ('хранения', 100), ('7', 60), ('дней', 100)]]
        layout = {'sha256': 'a'*64, 'page_count': 92, 'pages': [{'page': 92, 'words': words}]}
        lookup = prepare_pdf_lookup(layout)
        for question in ['Срок хранения 7 дней', 'Срок хранения 8 дней', 'Неизвестный фрагмент документа']:
            self.assertEqual(locate_fragment(layout, question), locate_fragment(layout, question, lookup=lookup))
        self.assertEqual(locate_fragment(layout, 'Срок хранения 7 дней', lookup=lookup)['initial_page'], 92)


if __name__ == '__main__':
    unittest.main()
