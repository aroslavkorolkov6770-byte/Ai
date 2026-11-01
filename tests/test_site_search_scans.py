"""Геометрия, реальные растры и отсутствие старых правил при смене редакции."""
import json
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
from ai_core.site_scan_tables import Cell, grid_cells, describe_table, reliable, parse_words
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.scan_probe import ASSETS, extracted_sources, ORIGIN


class ScanTests(unittest.TestCase):
    def test_merged_headers_and_body_rows_use_visible_spans(self):
        cells = [Cell(0, 0, 2, 1, 'Товар'), Cell(0, 1, 1, 3, 'Срок хранения'),
                 Cell(1, 1, 2, 2, 'Если упаковка не вскрыта'), Cell(1, 2, 2, 3, 'Если упаковка вскрыта'),
                 Cell(2, 0, 3, 1, 'Пример'), Cell(2, 1, 3, 2, '8 дней'), Cell(2, 2, 3, 3, '2 дня')]
        text = describe_table(cells, 3, 3, 2, 1)
        self.assertIn('Если упаковка вскрыта, товар Пример; Срок хранения: 2 дня.', text)
        self.assertIn('Страница 2', text)
        self.assertEqual(text.count('8 дней'), 1)

    def test_broken_partial_border_does_not_turn_into_merge(self):
        horizontal, vertical = np.zeros((221, 221), np.uint8), np.zeros((221, 221), np.uint8)
        for y in (10, 110, 210):
            cv2.line(horizontal, (10, y), (210, y), 255, 2)
        for x in (10, 110, 210):
            cv2.line(vertical, (x, 10), (x, 210), 255, 2)
        horizontal[108:113, 50:90] = 0
        with self.assertRaisesRegex(ValueError, 'broken_border'):
            grid_cells(horizontal, vertical, [10, 110, 210], [10, 110, 210])

    def test_low_confidence_numbers_negation_and_empty_cell_are_refused(self):
        self.assertFalse(reliable([]))
        for text in ['13', 'не']:
            self.assertFalse(reliable([{'text': text, 'confidence': 79}, {'text': 'дней', 'confidence': 99}]))
        self.assertTrue(reliable([{'text': '13', 'confidence': 95}, {'text': 'дней', 'confidence': 99}]))

    def test_body_horizontal_merge_is_not_guessed(self):
        cells = [Cell(0, 0, 1, 1, 'Товар'), Cell(0, 1, 1, 2, 'Срок'), Cell(0, 2, 1, 3, 'Цена'),
                 Cell(1, 0, 2, 1, 'Пример'), Cell(1, 1, 2, 3, '5')]
        with self.assertRaisesRegex(ValueError, 'body_colspan'):
            describe_table(cells, 2, 3, 1, 1)

    def test_real_scans_frozen_expectations_and_cached_extraction(self):
        spec = json.loads((ASSETS / 'spec.json').read_text(encoding='utf-8'))
        sources, extraction = extracted_sources(spec)
        index = ClosedSiteIndex([ORIGIN])
        index.publish(sources, index.begin('scans'))
        for item in spec['cases']:
            with self.subTest(case=item['id']):
                answer = index.search(item['q'], Principal('a', 'staff'), item['id'])
                self.assertEqual(answer['status'], item.get('status', 'answer'))
                for value in item.get('has', []):
                    self.assertIn(value.casefold(), answer['text'].casefold())
                for value in item.get('absent', []):
                    self.assertNotIn(value.casefold(), answer['text'].casefold())
        self.assertTrue(next(item for item in extraction if item['id'] == 'lab_scan_damaged')['complete'])
        self.assertTrue(all(item.structure['issue'] for item in index.passages if item.source['id'] == 'lab_scan_damaged'))
        # Скан прочитан до конца, но таблица неразрешима. Это не меняет
        # отсутствие ответа по совершенно другой теме на общую ошибку OCR.
        self.assertEqual(index.search('Как доставляется заказ Вега?', Principal('a', 'staff'), 'unrelated')['status'], 'not_found')
        summary = index.search('Кратко перескажи выбранный файл', Principal('a', 'staff'), 'summary', 'lab_scan_matrix')
        self.assertEqual(summary['status'], 'answer')
        self.assertIn('8 календарных', summary['text'])
        self.assertIn('Если упаковка вскрыта', summary['text'])
        with patch('ai_core.site_documents._scan_text', side_effect=AssertionError('OCR on warm cache')):
            self.assertEqual(extracted_sources(spec)[0], sources)

    def test_table_calendar_columns_are_preserved_as_periods(self):
        cells = [Cell(0, n, 1, n + 1, value) for n, value in enumerate(['Товар', 'Срок хранения', 'Действует с', 'Действует по'])]
        cells += [Cell(1, n, 2, n + 1, value) for n, value in enumerate(['Пример', '6 дней', '01.11.2026', '30.11.2026'])]
        text = describe_table(cells, 2, 4, 1, 1)
        self.assertIn('Действует с 01.11.2026 по 30.11.2026 включительно.', text)

    def test_pagination_is_not_conflict_but_changed_rule_is(self):
        from ai_core.site_search_reasoning import conflicting_passages
        from ai_core.site_search import terms
        from types import SimpleNamespace
        pages = [SimpleNamespace(text='Страница 1. Товар Пример хранится 4 дня.'),
                 SimpleNamespace(text='Страница 2. Товар Пример хранится 4 дня.')]
        self.assertFalse(conflicting_passages(pages, ClosedSiteIndex._sentences, terms))
        pages[1].text = 'Страница 2. Товар Пример хранится 8 дней.'
        self.assertTrue(conflicting_passages(pages, ClosedSiteIndex._sentences, terms))

    def test_extraction_upgrade_never_restores_old_flattened_index(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'index.sqlite3'
            old = DurableSearch([ORIGIN], path, extraction_version='ocr-v1')
            old.publish([], old.begin('first'))
            changed = DurableSearch([ORIGIN], path, extraction_version='ocr-v2')
            self.assertEqual(changed.status()['state'], 'unavailable')
            self.assertFalse(changed.restored)
            changed.publish([], changed.begin('fresh'))
            reopened = DurableSearch([ORIGIN], path, extraction_version='ocr-v2')
            self.assertEqual(reopened.status()['state'], 'ready')
            self.assertTrue(reopened.restored)

    def test_real_scanned_revision_replaces_saved_context_after_restart(self):
        from ai_core.site_documents import extract_document
        actor = Principal('revision-a', 'staff')
        def source(version):
            data = (ASSETS / f'Редакция_Родонит_v{version}.pdf').read_bytes()
            text, complete = extract_document('data:application/pdf;base64,' + base64.b64encode(data).decode(), 'rule.pdf')
            return {'id': 'revision', 'revision': str(version), 'title': 'Регламент Родонит', 'kind': 'document',
                    'url': ORIGIN + '/materials/revision', 'access': {'scope': 'workspace'},
                    'complete': complete, 'sections': [{'heading': 'Правило', 'text': text}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'revision.sqlite3'
            runtime = DurableSearch([ORIGIN], path)
            runtime.publish([source(1)], runtime.begin('v1'))
            old = runtime.query('Упаковка вскрыта. Сколько хранится товар Родонит?', actor, 'chat', None, {'revision': '1'})
            self.assertIn('1 календарный', old['text'])
            runtime = DurableSearch([ORIGIN], path)
            pending = runtime.query('А сколько дней?', actor, 'chat', None, {'revision': '2'})
            self.assertEqual(pending['status'], 'processing')
            runtime.publish([source(2)], runtime.begin('v2'))
            runtime = DurableSearch([ORIGIN], path)
            self.assertEqual(runtime.query('А сколько дней?', actor, 'chat', None, {'revision': '2'})['status'], 'clarify')
            updated = runtime.query('Упаковка вскрыта. Сколько хранится товар Родонит?', actor, 'chat', None, {'revision': '2'})
            self.assertIn('3 календарных', updated['text'])
            self.assertNotIn('1 календарный', updated['text'])
            self.assertEqual({item['revision'] for item in updated['evidence']}, {'2'})

    def test_hidden_old_text_layer_does_not_replace_visible_scan(self):
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        from ai_core.site_documents import extract_document
        writer = PdfWriter(clone_from=PdfReader(ASSETS / 'Редакция_Родонит_v2.pdf'))
        page = writer.pages[0]
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        resources = page['/Resources']
        resources[NameObject('/Font')] = DictionaryObject({NameObject('/Stale'): writer._add_object(font)})
        stream = DecodedStreamObject()
        stream.set_data(page.get_contents().get_data() + b'\nBT /Stale 12 Tf 3 Tr 10 650 Td (OLD HIDDEN SCAN OCR WRONG DATA NUMBER 999 days should never be the source for an answer) Tj ET\n')
        page[NameObject('/Contents')] = writer._add_object(stream)
        buffer = io.BytesIO()
        writer.write(buffer)
        self.assertIn('999', PdfReader(io.BytesIO(buffer.getvalue())).pages[0].extract_text())
        text, complete = extract_document('data:application/pdf;base64,' + base64.b64encode(buffer.getvalue()).decode(), 'hidden.pdf')
        self.assertTrue(complete)
        self.assertIn('11 календарных', text)
        self.assertNotIn('999', text)


if __name__ == '__main__':
    unittest.main()
