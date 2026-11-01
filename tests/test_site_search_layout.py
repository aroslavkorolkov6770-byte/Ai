"""Реальные растры и отрицательные проверки связей страниц, строк и сносок."""
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
from ai_core.site_scan_layout import compose_pages, rectify_page, borderless, UNCERTAIN
from ai_core.site_scan_tables import Cell, describe_table, reliable, words_text
from ai_core.site_documents import _join_visual_lines
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.layout_probe import load_sources, ORIGIN


def continued_table(page, subject, action):
    cells = [Cell(0, 0, 1, 1, 'Поставка'), Cell(0, 1, 1, 2, 'Действия'),
             Cell(1, 0, 2, 1, subject), Cell(1, 1, 2, 2, action)]
    text = describe_table(cells, 2, 2, page, 1)
    return {'page': page, 'cells': cells, 'rows': 2, 'cols': 2, 'ordinal': 1, 'text': text}


def continuation():
    first = continued_table(1, 'Тестовая', 'При повреждении уведомите')
    second = continued_table(2, 'Тестовая (продолжение)', 'координатора.')
    return [first, second], ['Таблица 23. Приёмка\n' + first['text'] + '\nПродолжение строки на следующей странице.',
                             'Продолжение таблицы 23\n' + second['text']]


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec, cls.sources, cls.extraction = load_sources()

    def test_real_rasters_frozen_answers_and_warm_cache(self):
        index = ClosedSiteIndex([ORIGIN])
        index.publish(self.sources, index.begin('layout'))
        for case in self.spec['cases']:
            with self.subTest(case=case['id']):
                answer = index.search(case['q'], Principal('a', 'staff'), case['id'], case['source'])
                self.assertEqual(answer['status'], case.get('status', 'answer'))
                for value in case.get('has', []):
                    self.assertIn(value.casefold(), answer['text'].casefold())
                for value in case.get('absent', []):
                    self.assertNotIn(value.casefold(), answer['text'].casefold())
                if answer['status'] == 'answer':
                    self.assertEqual({p['id'] for p in answer['evidence']}, {case['source']})
        with patch('ai_core.site_documents._scan_text', side_effect=AssertionError('OCR on cached request')):
            self.assertEqual(load_sources()[1], self.sources)

    def test_explicit_continuation_keeps_complete_sentence_and_provenance(self):
        tables, pages = continuation()
        result = compose_pages(pages, tables, describe_table)
        self.assertNotIn(UNCERTAIN, result)
        self.assertIn('Страницы 1 и 2', result)
        self.assertIn('При повреждении уведомите координатора.', result)
        self.assertNotIn('уведомите.', result)
        self.assertEqual(tables[0]['cells'][-1].text, 'При повреждении уведомите')

    def test_mismatch_or_absent_page_cannot_produce_partial_answer(self):
        for variant in ['caption', 'header', 'subject', 'complete', 'no-marker', 'last-page', 'text-mismatch', 'chain']:
            with self.subTest(variant=variant):
                tables, pages = continuation()
                if variant == 'caption': pages[1] = pages[1].replace('таблицы 23', 'таблицы 24')
                if variant == 'header': tables[1]['cells'][1].text = 'Срок'
                if variant == 'subject': tables[1]['cells'][2].text = 'Чужая (продолжение)'
                if variant == 'complete': tables[0]['cells'][3].text += '.'
                if variant == 'no-marker': pages[0] = pages[0].split('\nПродолжение строки')[0]
                if variant == 'last-page': pages = pages[:1]
                if variant == 'text-mismatch': tables[1]['text'] += ' отсутствующий текст'
                if variant == 'chain': pages[1] += '\nПродолжение строки на следующей странице.'
                self.assertIn(UNCERTAIN, compose_pages(pages, tables, describe_table))

    def test_caption_for_footnote_is_printed_not_generated(self):
        text = compose_pages(['# Страница 1, таблица 8: Товар\nСрок: 8 дней [1].',
                              'Сноски к таблице 8\n[1]: Исключение: срок 2 дня.'], [], describe_table)
        self.assertIn('[p1-1]', text)
        self.assertIn('[t8-1]:', text)
        # Одинаковые номера без печатной области остаются разными сносками.
        local = compose_pages(['Срок [1].\n[1]: 4 дня.', 'Срок [1].\n[1]: 9 дней.'], [], describe_table)
        self.assertIn('[p1-1]: 4 дня.', local)
        self.assertIn('[p2-1]: 9 дней.', local)

    def test_footnote_after_ocr_blank_line_remains_one_condition(self):
        text = _join_visual_lines('Сноски к таблице 8\n[1]: Исключение: если упаковка вскрыта, товар\n\nхранится 2 дня.')
        self.assertIn('\n[1]: Исключение: если упаковка вскрыта, товар хранится 2 дня.', text)

    def test_standalone_footnote_does_not_bypass_critical_number_confidence(self):
        words=[{'text':text,'confidence':confidence,'left':20+index*100,'top':30,'width':80,'height':20,
                'block_num':1,'par_num':1,'line_num':1}
               for index,(text,confidence) in enumerate([('[1]:',96),('Срок',96),('2',79),('дня.',96)])]
        text,handled=borderless(words,1000,Cell,describe_table,reliable,_join_visual_lines,words_text,1,[])
        self.assertTrue(handled)
        self.assertIn(UNCERTAIN,text)
        words[2]['confidence']=98
        text,handled=borderless(words,1000,Cell,describe_table,reliable,_join_visual_lines,words_text,1,[])
        self.assertNotIn(UNCERTAIN,text)

    def test_one_caption_for_multiple_tables_with_notes_is_ambiguous(self):
        first = continued_table(1, 'Первый', '4 дня [1]')
        second = continued_table(1, 'Второй', '8 дней [1]')
        text = compose_pages(['Таблица 8\n' + first['text'] + '\n' + second['text'] + '\n[1]: 2 дня.'],
                             [first, second], describe_table)
        self.assertIn(UNCERTAIN, text)

    def test_rectification_changes_only_a_visible_sheet(self):
        ordinary = np.full((700, 600), 255, np.uint8)
        cv2.rectangle(ordinary, (40, 150), (540, 500), 0, 3)
        self.assertTrue(np.array_equal(rectify_page(ordinary), ordinary))
        photo = np.full((900, 800), 70, np.uint8)
        cv2.fillConvexPoly(photo, np.array([[140,80],[680,150],[730,770],[70,830]]), 255)
        result = rectify_page(photo)
        self.assertLess(result.size, photo.size)
        self.assertGreater(float(result.mean()), 250)

    def test_changed_bad_revision_invalidates_old_answer_and_context(self):
        good = deepcopy(next(s for s in self.sources if s['id'] == 'lab_layout_continued'))
        bad = deepcopy(next(s for s in self.sources if s['id'] == 'lab_layout_wrong_continued'))
        good.update(id='changing', revision='1')
        bad.update(id='changing', revision='2')
        actor = Principal('a', 'staff')
        question = 'Что сделать при повреждении упаковки поставки Флюорит?'
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory) / 'index.sqlite3'
            runtime = DurableSearch([ORIGIN], location)
            runtime.publish([good], runtime.begin('first'))
            self.assertIn('уведомите старшего смены', runtime.query(question, actor, 'chat', 'changing', {'changing':'1'})['text'])
            runtime = DurableSearch([ORIGIN], location)
            self.assertEqual(runtime.query(question, actor, 'chat', 'changing', {'changing':'2'})['status'], 'processing')
            runtime.publish([bad], runtime.begin('second'))
            runtime = DurableSearch([ORIGIN], location)
            answer = runtime.query(question, actor, 'chat', 'changing', {'changing':'2'})
            self.assertEqual(answer['status'], 'unavailable')
            self.assertNotIn('уведомите старшего смены', answer['text'])


if __name__ == '__main__':
    unittest.main()
