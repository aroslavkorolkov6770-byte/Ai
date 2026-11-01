"""Проверки связи ячеек, сносок, разделов, дат и сохранённого индекса."""
import base64
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from ai_core.site_documents import extract_document
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.structure_probe import sources, SPEC

ORIGIN = 'http://127.0.0.1:3040'
READER = Principal('reader', 'staff')
NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc).timestamp()


def record(text, identifier='Сфера', **extra):
    return {'id': identifier, 'revision': 'v1', 'title': 'Документ ' + identifier,
            'kind': 'document', 'url': ORIGIN + '/materials/' + identifier,
            'access': {'scope': 'workspace'}, 'sections': [{'heading': 'Правило', 'text': text}], **extra}


def word_document():
    """Настоящая таблица Word и footnotes.xml, а не текстовая имитация таблицы."""
    namespace = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    rows = '<w:tr><w:tc><w:p><w:r><w:t>Товар</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Срок хранения</w:t></w:r></w:p></w:tc></w:tr>'
    rows += '<w:tr><w:tc><w:p><w:r><w:t>Берилл</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>6 календарных дней</w:t><w:footnoteReference w:id="1"/></w:r></w:p></w:tc></w:tr>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/_rels/document.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/></Relationships>')
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{namespace}"><w:body><w:tbl>{rows}</w:tbl></w:body></w:document>')
        archive.writestr('word/footnotes.xml', f'<w:footnotes xmlns:w="{namespace}"><w:footnote w:id="1"><w:p><w:r><w:t>Исключение: если упаковка вскрыта, товар Берилл хранится 2 календарных дня.</w:t></w:r></w:p></w:footnote></w:footnotes>')
    return buffer.getvalue()


class StructureTests(unittest.TestCase):
    def make(self, *records, now=NOW):
        index = ClosedSiteIndex([ORIGIN], clock=lambda: now)
        index.publish(list(records), index.begin('test'))
        return index

    def test_frozen_cases(self):
        spec = json.loads(SPEC.read_text(encoding='utf-8'))
        index = self.make(*sources(spec))
        for case in spec['cases']:
            with self.subTest(case=case['id']):
                result = index.search(case['q'], READER, case['id'])
                self.assertEqual(result['status'], case.get('status', 'answer'))
                for value in case.get('has', []):
                    self.assertIn(value.casefold(), result['text'].casefold())
                for value in case.get('absent', []):
                    self.assertNotIn(value.casefold(), result['text'].casefold())

    def test_word_table_and_real_footnote(self):
        text, complete = extract_document('data:application/octet-stream;base64,' + base64.b64encode(word_document()).decode(), 'table.docx')
        self.assertTrue(complete)
        index = self.make(record(text, 'Берилл'))
        result = index.search('Упаковка вскрыта. Сколько хранится товар Берилл?', READER)
        self.assertIn('2 календарных', result['text'])
        self.assertNotIn('6 календарных', result['text'])

    def test_html_table_keeps_headers_and_hides_hidden_cells(self):
        html = '<h1>Сроки</h1><table><tr><th>Товар</th><th>Срок хранения</th></tr><tr><td>Сфера</td><td>4 календарных дня<span hidden>991122</span></td></tr></table>'
        index = self.make(record(html))
        result = index.search('Сколько хранится товар Сфера?', READER)
        self.assertEqual(result['status'], 'answer')
        self.assertIn('4 календарных', result['text'])
        self.assertNotIn('991122', json.dumps(result, ensure_ascii=False))

    def test_merged_or_empty_cells_are_not_inferred(self):
        examples = ['<table><tr><th>Товар</th><th>Срок хранения</th></tr><tr><td rowspan="2">Сфера</td><td>4 дня</td></tr></table>',
                    '| Товар | Срок хранения |\n| --- | --- |\n| Сфера | |']
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(self.make(record(text)).search('Сколько хранится товар Сфера?', READER)['status'], 'unavailable')

    def test_table_dates_choose_current_row(self):
        text = '| Товар | Срок хранения | Действует с | Действует по |\n| --- | --- | --- | --- |\n| Сфера | 3 дня | 01.01.2026 | 28.10.2026 |\n| Сфера | 7 дней | 29.10.2026 | 31.12.2026 |'
        result = self.make(record(text)).search('Сколько хранится товар Сфера?', READER)
        self.assertEqual(result['status'], 'answer')
        self.assertIn('7 дней', result['text'])
        self.assertNotIn('3 дня', result['text'])

    def test_overlapping_dates_conflict(self):
        text = '# 1. Срок\nДействует с 01.01.2026 по 31.12.2026 включительно.\nЗаказ Сфера хранится 3 дня.\n# 2. Срок\nДействует с 01.10.2026 по 31.12.2026 включительно.\nЗаказ Сфера хранится 7 дней.'
        self.assertEqual(self.make(record(text)).search('Сколько хранится заказ Сфера?', READER)['status'], 'conflict')

    def test_invalid_document_or_question_date(self):
        index = self.make(record('Действует с 31.02.2026 по 31.12.2026 включительно.\nЗаказ Сфера хранится 3 дня.'))
        self.assertEqual(index.search('Сколько хранится заказ Сфера?', READER)['status'], 'unavailable')
        self.assertEqual(index.search('Сколько хранится заказ Сфера на 31.02.2026?', READER)['status'], 'clarify')

    def test_global_period_applies_to_all_sections_and_midnight(self):
        text = 'Действует с 29.10.2026 по 29.10.2026 включительно.\n# Срок\nЗаказ Сфера хранится 3 дня.'
        before = datetime(2026, 10, 29, 20, 59, tzinfo=timezone.utc).timestamp()
        after = datetime(2026, 10, 29, 21, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(self.make(record(text), now=before).search('Сколько хранится заказ Сфера?', READER)['status'], 'answer')
        self.assertEqual(self.make(record(text), now=after).search('Сколько хранится заказ Сфера?', READER)['status'], 'not_found')

    def test_reverse_exception_declaration(self):
        text = '# 1. Основное\nЗаказ Сфера хранится 4 дня.\n# 2. Исключения к разделу 1\nИсключение: если упаковка вскрыта, заказ Сфера хранится 1 день.'
        result = self.make(record(text)).search('Упаковка вскрыта. Сколько хранится заказ Сфера?', READER)
        self.assertEqual(result['status'], 'answer')
        self.assertIn('1 день', result['text'])
        self.assertNotIn('4 дня', result['text'])

    def test_transitive_reference_and_duplicate_target(self):
        text = '# 1. Основное\nЗаказ Сфера хранится 4 дня. См. раздел 2.\n# 2. Уточнение\nСм. раздел 3.\n# 3. Исключение\nИсключение: если упаковка вскрыта, заказ Сфера хранится 1 день.'
        result = self.make(record(text)).search('Упаковка вскрыта. Сколько хранится заказ Сфера?', READER)
        self.assertIn('1 день', result['text'])
        bad = self.make(record(text + '\n# 3. Дубликат\nЗаказ Сфера хранится 8 дней.'))
        self.assertEqual(bad.search('Сколько хранится заказ Сфера?', READER)['status'], 'unavailable')

    def test_cycle_does_not_disappear_when_another_section_exists(self):
        text = '# 1. Основное\nЗаказ Сфера хранится 4 дня. См. раздел 2.\n# 2. Уточнение\nСм. раздел 1.\n# 3. Независимый\nЗаказ Коралл выдаёт координатор.'
        self.assertEqual(self.make(record(text)).search('Сколько хранится заказ Сфера?', READER)['status'], 'unavailable')

    def test_reference_never_resolves_into_another_private_document(self):
        public = record('Заказ Сфера хранится 4 дня. См. раздел 2.')
        secret = record('# 2. Исключение\nИсключение: если упаковка вскрыта, заказ Сфера хранится 997 дней.', 'Секрет', access={'scope': 'admin'})
        result = self.make(public, secret).search('Упаковка вскрыта. Сколько хранится заказ Сфера?', READER)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('997', json.dumps(result))

    def test_unsupported_reference_or_inline_footnote_period_is_not_ignored(self):
        texts = ['Возврат товара Сфера разрешён, кроме условий раздела 9.',
                 'Возврат товара Сфера разрешён.[1]\n[1]: Действует с 01.01.2025 по 31.12.2025 включительно. Возврат товара Сфера запрещён.']
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(self.make(record(text)).search('Можно вернуть товар Сфера?', READER)['status'], 'unavailable')

    def test_summary_uses_only_current_period_and_keeps_footnote(self):
        index = self.make(*sources(json.loads(SPEC.read_text(encoding='utf-8'))))
        summary = index.search('Перескажи выбранный файл', READER, selected_source='lab_struct_dates')
        self.assertIn('7 календарных', summary['text'])
        self.assertNotIn('11 календарных', summary['text'])
        note = index.search('Перескажи выбранный файл', READER, selected_source='lab_struct_note')
        self.assertIn('5 календарных', note['text'])
        self.assertIn('1 календарный', note['text'])
        self.assertNotIn('[1]', note['text'])

    def test_historical_date_survives_restart_without_crossing_users(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'index.sqlite3'
            records = sources(json.loads(SPEC.read_text(encoding='utf-8')))
            revisions = {item['id']: item['revision'] for item in records}
            runtime = DurableSearch([ORIGIN], path, clock=lambda: NOW)
            runtime.publish(records, runtime.begin('test'))
            first = runtime.query('Сколько хранится заказ Полюс на 28.10.2026?', READER, 'chat', None, revisions)
            self.assertIn('3 календарных', first['text'])
            runtime = DurableSearch([ORIGIN], path, clock=lambda: NOW)
            self.assertTrue(runtime.restored)
            follow = runtime.query('А сколько дней?', READER, 'chat', None, revisions)
            self.assertIn('3 календарных', follow['text'])
            other = runtime.query('Сколько хранится заказ Полюс?', Principal('other', 'staff'), 'chat', None, revisions)
            self.assertIn('7 календарных', other['text'])


if __name__ == '__main__':
    unittest.main()
