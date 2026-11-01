"""Проверки структуры и поиска на собственных числах, вне экзамена ТРМ1."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from ai_core.site_pdf_manual import owner, align_values, bind_notes, read_notes, symbol_legend, value_boxes
from ai_core.site_pdf_locations import locate_fragment
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch


def word(text, y, x=10):
    return {'text': text, 'x0': x, 'x1': x + 20, 'top': y, 'bottom': y + 8}


BODY = '''[PDF_LOCAL_SECTIONS]
[PDF_PARAMETER_MANUAL]
# Страница 2 — Таблица 1.1
Общие сведения. Масса прибора — без упаковки: 1,7 кг.
# Страница 2 — Таблица 1.1
Общие сведения. Масса прибора — с упаковкой: 2,3 кг.
# Страница 2 — Таблица 1.1
Габаритные размеры прибора — DIN-реечный Д: (31 × 42 × 53) ± 2 мм.
# Страница 2 — Таблица 1.1
Габаритные размеры прибора — щитовой Щ2: (64 × 75 × 86) ± 2 мм.
# Страница 2 — Таблица 1.1
Питание. Диапазон напряжения питания — постоянное: 17...43 В.
# Страница 2 — Таблица 1.1
Питание. Номинальное напряжение питания — постоянное: 28 В.
# Страница 2 — Таблица 1.1
Параметры обмена (Только для модификации с портом связи). Количество бит данных: 7 (Только для ASCII), 8.
# Страница 3 — Монтаж
Рисунок 5. Габаритные размеры корпуса Д и отверстия в щите.
# Страница 3 — Внешнее оборудование
Внешний источник питания постоянного тока должен иметь напряжение в диапазоне от 12 до 16 В.
# Страница 5 — Гарантия
Гарантийный срок эксплуатации — 15 месяцев со дня продажи. При нарушении условий монтажа гарантия не действует.
'''


def source(body=BODY, revision='r1'):
    return {'id': 'document:manual', 'revision': revision, 'title': 'Руководство АБР9', 'kind': 'document',
            'url': 'http://localhost/manual', 'access': {'scope': 'restricted', 'users': ['reader']},
            'sections': [{'heading': '', 'text': body}]}


class ManualGeometryTests(unittest.TestCase):
    def test_only_isolated_native_symbol_caption_is_localized(self):
        page = SimpleNamespace(height=800, images=[{'bottom': 150}])
        body = [word('Соответствие символов индикатора буквам латинского алфавита', 60)]
        self.assertTrue(symbol_legend(page, body))
        self.assertFalse(symbol_legend(page, body + [word('Обязательное условие питания', 180)]))
        page.images[0]['bottom'] = 700
        self.assertFalse(symbol_legend(page, body))

    def test_highlight_excludes_adjacent_variant_value(self):
        keys = [word('Масса:', 0), word('без упаковки', 12), word('с упаковкой', 32)]
        values = [word('1,7', 12, 80), word('2,3', 32, 80)]
        boxes = value_boxes(keys, values, 1, 2)
        self.assertIn((80, 32, 100, 40), boxes)
        self.assertNotIn((80, 12, 100, 20), boxes)

    def test_rowspan_uses_owning_rectangle(self):
        cells = [(0, 0, 10, 40), (10, 0, 20, 20), (10, 20, 20, 40)]
        self.assertEqual(owner(cells, 5, 30), cells[0])
        self.assertEqual(owner(cells, 15, 30), cells[2])

    def test_overlapping_ownership_is_not_guessed(self):
        self.assertIsNone(owner([(0, 0, 20, 20), (5, 5, 15, 15)], 10, 10))
        self.assertIsNone(owner([(0, 0, 20, 20)], 25, 10))

    def test_multiple_values_keep_prefix_and_wrapped_labels(self):
        keys = [word('Защита корпуса:', 0), word('спереди', 12), word('(для корпуса А)', 22), word('сзади', 42), word('(для корпуса Б)', 52)]
        values = [word('IP31', 17), word('IP45', 47)]
        result = align_values(keys, values)
        self.assertEqual(result, [('Защита корпуса — спереди (для корпуса А)', 'IP31'), ('Защита корпуса — сзади (для корпуса Б)', 'IP45')])

    def test_missing_label_or_distant_value_is_uncertain(self):
        self.assertIsNone(align_values([word('Размеры:', 0), word('А', 12)], [word('18', 12), word('19', 32)]))
        self.assertIsNone(align_values([word('Размеры:', 0), word('А', 12), word('Б', 32)], [word('18', 112), word('19', 132)]))

    def test_multiline_single_value_is_not_split_without_colon(self):
        self.assertEqual(align_values([word('Скорости', 0)], [word('9600;', 0), word('19200', 12)]), [('Скорости', '9600; 19200')])

    def test_six_stars_resolve_exactly_without_using_one_star(self):
        definitions = {1: [{'text': 'Для варианта А.', 'page': 2, 'box': [0, 0, 20, 20]}],
                       6: [{'text': 'Только режим X.', 'page': 3, 'box': [0, 0, 20, 20]}]}
        text, missing, used = bind_notes('7******, 8', definitions)
        self.assertFalse(missing)
        self.assertEqual(text, '7 (Только режим X), 8')
        self.assertEqual(used[0]['page'], 3)
        self.assertNotIn('варианта А', text)

    def test_missing_and_duplicate_notes_block_confirmation(self):
        for notes in ({}, {2: [{'text': 'А'}, {'text': 'Б'}]}):
            self.assertTrue(bind_notes('28**', notes)[1])

    def test_note_continuation_stays_with_its_marker(self):
        notes = read_notes([word('ПРИМЕЧАНИЕ', 1), word('* Только режим А', 12), word('при условии Б.', 24), word('** Режим В.', 36)], (0, 0, 100, 50), 4)
        self.assertEqual(notes[1][0]['text'], 'Только режим А при условии Б.')
        self.assertEqual(notes[2][0]['text'], 'Режим В.')

    def test_highlight_can_include_note_from_next_page(self):
        region = {'kind': 'table', 'page': 1, 'heading': 'Страница 1 — Таблица 1.1', 'text': 'Ток: 4 (Только при X).',
                  'rects': [[.1, .2, .8, .3]], 'row_rects': [[.1, .2, .8, .3]],
                  'extra_rects': [{'page': 2, 'box': [.1, .5, .8, .6]}]}
        layout = {'sha256': 'a'*64, 'page_count': 2, 'pages': [{'page': 1, 'words': [], 'regions': [region]}]}
        result = locate_fragment(layout, region['text'], heading=region['heading'])
        self.assertEqual(result['status'], 'located')
        self.assertEqual([hit['page'] for hit in result['hits']], [1, 2])


class ManualSearchTests(unittest.TestCase):
    def setUp(self):
        self.index = ClosedSiteIndex(['http://localhost'])
        self.index.publish([source()], self.index.begin('manual'))

    def ask(self, text, chat='one', user='reader', selected='document:manual'):
        return self.index.search(text, Principal(user, 'staff'), chat, selected)

    def test_device_range_does_not_take_nominal_or_external_supply(self):
        result = self.ask('Какой диапазон постоянного напряжения питания у АБР9?')
        self.assertEqual(result['status'], 'answer')
        self.assertIn('17...43', result['text'])
        self.assertNotIn('28 В', result['text'])

    def test_switch_package_and_case_in_same_chat(self):
        self.assertIn('1,7', self.ask('Какая масса АБР9 без упаковки?')['text'])
        self.assertIn('2,3', self.ask('А с упаковкой?')['text'])
        self.assertIn('31 × 42 × 53', self.ask('А размеры в корпусе Д?')['text'])
        self.assertIn('64 × 75 × 86', self.ask('А в Щ2?')['text'])

    def test_unselected_document_is_found_by_own_name(self):
        self.assertIn('1,7', self.ask('Какая масса АБР9 без упаковки?', selected=None)['text'])

    def test_unknown_variant_does_not_pick_other_case(self):
        self.assertNotEqual(self.ask('Какие размеры АБР9 в корпусе Ж?')['status'], 'answer')

    def test_other_named_device_does_not_borrow_selected_manual(self):
        self.assertEqual(self.ask('Какая масса АБР10 без упаковки?')['status'], 'not_found')

    def test_no_case_requires_clarification(self):
        self.assertEqual(self.ask('Какие размеры АБР9?')['status'], 'clarify')

    def test_other_chat_and_user_do_not_inherit_subject(self):
        self.ask('Какая масса АБР9 без упаковки?')
        self.assertEqual(self.ask('А с упаковкой?', chat='other')['status'], 'clarify')
        self.assertNotEqual(self.ask('Какая масса АБР9 без упаковки?', user='outsider')['status'], 'answer')

    def test_only_own_footnote_is_kept_in_answer(self):
        result = self.ask('Можно ли использовать семь бит данных?')
        self.assertIn('Только для ASCII', result['text'])
        self.assertIn('для модификации с портом связи', result['text'])

    def test_unresolved_geometry_does_not_answer(self):
        self.index.publish([source(BODY.replace('Масса прибора', '[LOCAL_STRUCTURE_UNCERTAIN] Масса прибора'))], self.index.begin('bad'))
        self.assertEqual(self.ask('Какая масса АБР9 без упаковки?')['status'], 'unavailable')

    def test_restart_preserves_focus_and_revision_change_clears_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'search.sqlite3'
            runtime = DurableSearch(['http://localhost'], path)
            runtime.publish([source()], runtime.begin('v1'))
            actor = Principal('reader', 'staff')
            runtime.query('Какая масса АБР9 без упаковки?', actor, 'one', 'document:manual', {'document:manual': 'r1'})
            restored = DurableSearch(['http://localhost'], path)
            self.assertIn('2,3', restored.query('А с упаковкой?', actor, 'one', 'document:manual', {'document:manual': 'r1'})['text'])
            restored.publish([source(revision='r2')], restored.begin('v2'))
            self.assertEqual(restored.query('А с упаковкой?', actor, 'one', 'document:manual', {'document:manual': 'r2'})['status'], 'clarify')


if __name__ == '__main__':
    unittest.main()
