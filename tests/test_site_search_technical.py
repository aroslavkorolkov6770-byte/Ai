"""Независимые технические примеры: без ответов из пользовательского PDF."""
import unittest
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_pdf_structure import numbered_paragraphs, ordered_columns


class TechnicalPdfTests(unittest.TestCase):
    def index(self, body, access=None):
        index = ClosedSiteIndex(['http://localhost'])
        source = {'id':'doc', 'revision':'r1', 'title':'Руководство', 'url':'http://localhost/doc',
                  'access':access or {'scope':'workspace'},
                  'sections':[{'text':'[PDF_LOCAL_SECTIONS]\n'+body}]}
        index.publish([source],index.begin('r1'))
        return index

    def ask(self, index, question, user='reader', revision='r1'):
        return index.search(question,Principal(user,'staff'),'isolated-'+question,
                            selected_source='doc',current_revisions={'doc':revision})

    def test_definition_and_placement_are_different_relations(self):
        index=self.index('# Раздел 1.1\nБлок РКС предназначен для управления насосами.\n'
                         '# Раздел 1.2\nБлок РКС предназначен для размещения в сухом шкафу.\n'
                         '# Состав\nРКС – совокупность контроллера и резервной батареи.')
        self.assertIn('насосами', self.ask(index,'Для каких устройств предназначен РКС?')['text'])
        self.assertIn('сухом шкафу', self.ask(index,'Где предполагается размещать РКС?')['text'])
        self.assertIn('контроллера', self.ask(index,'Из каких частей состоит РКС?')['text'])

    def test_table_rows_do_not_conflict_when_model_numbers_differ(self):
        index=self.index('# Таблица\nНаименование: МБ 8 кВА; Тепловыделение не более, Вт: 320.\n'
                         '# Таблица\nНаименование: МБ 16 кВА; Тепловыделение не более, Вт: 690.')
        answer=self.ask(index,'Сравни тепловыделение МБ 8 кВА и МБ 16 кВА.')
        self.assertEqual(answer['status'],'answer')
        self.assertIn('320',answer['text']);self.assertIn('690',answer['text'])

    def test_filtered_column_keeps_shared_nested_footnote(self):
        index=self.index('# Таблица\nНаименование: ТР 9 кВА; Размер, мм: 400х300х200 '
                         '(Размеры и масса указаны с кожухом (масса кожуха 11 кг)); Масса, кг: 74.')
        answer=self.ask(index,'Какая масса ТР 9 кВА и учтён ли кожух?')
        self.assertIn('74',answer['text']);self.assertIn('11 кг',answer['text'])
        self.assertIn('с кожухом',answer['text'])

    def test_missing_footnote_cannot_supply_answer(self):
        index=self.index('# Таблица\nНаименование: РК 9 кВА; Масса, кг: 46 [missing].')
        self.assertNotEqual(self.ask(index,'Какая масса РК 9 кВА?')['status'],'answer')

    def test_unreadable_diagram_does_not_hide_independent_paragraph(self):
        index=self.index('# Страница 1 — схема\n[LOCAL_STRUCTURE_UNCERTAIN]\nРК 9 кВА масса 99.\n'
                         '# Таблица\nНаименование: РК 9 кВА; Масса, кг: 46.')
        answer=self.ask(index,'Какая масса РК 9 кВА?')
        self.assertEqual(answer['status'],'answer');self.assertIn('46',answer['text'])

    def test_global_uncertain_page_still_blocks_document(self):
        index=self.index('[STRUCTURE_UNCERTAIN]\n# Таблица\nНаименование: РК 9 кВА; Масса, кг: 46.')
        self.assertEqual(self.ask(index,'Какая масса РК 9 кВА?')['status'],'unavailable')

    def test_different_dimensions_for_same_variant_report_conflict(self):
        index=self.index('# Раздел 2.1\nЩит РК тип 3 имеет габариты 900х400х300 мм.\n'
                         '# Таблица\nНаименование: Щит РК тип 3; Размер, ВхШхГ, мм: 900х300х400.')
        answer=self.ask(index,'Какие габариты у РК типа 3?')
        self.assertEqual(answer['status'],'conflict')
        self.assertIn('900х400х300',answer['text']);self.assertIn('900х300х400',answer['text'])

    def test_normative_value_is_not_live_measurement(self):
        index=self.index('# Правило\nНапряжение на входе РК должно быть 48 В.')
        self.assertEqual(self.ask(index,'Какое напряжение сейчас на входе РК?')['status'],'not_found')

    def test_acl_and_revision_are_checked_before_technical_router(self):
        index=self.index('# Таблица\nНаименование: РК 9 кВА; Масса, кг: 46.',
                         {'scope':'private','owner_id':'reader'})
        self.assertEqual(self.ask(index,'Какая масса РК 9 кВА?',user='outsider')['status'],'not_found')
        self.assertEqual(self.ask(index,'Какая масса РК 9 кВА?',revision='r2')['status'],'processing')

    def test_duplicate_section_numbers_keep_facts_but_not_ambiguous_references(self):
        index=self.index('# Раздел 2.1 — страница 4\nНаименование: РК 9 кВА; Масса, кг: 46.\n'
                         '# Раздел 2.1 — страница 30\nНаименование: РК 8 кВА; Масса, кг: 37.\n'
                         '# Раздел 5.2\nПорядок перевозки РК 9 кВА: см. раздел 2.1.')
        self.assertEqual(self.ask(index,'Какая масса РК 9 кВА?')['status'],'answer')
        self.assertNotEqual(self.ask(index,'Какой порядок перевозки РК 9 кВА?')['status'],'answer')

    def test_definition_continuation_and_dimension_line_keep_context(self):
        rows=[{'text':text} for text in ['1.4 Определения:', 'Система РК – совокупность устройств,',
                'соединённых одним кабелем.', '2.1 Щит РК имеет размеры.', 'Габаритные размеры – 400х300х200 мм.']]
        result='\n'.join(numbered_paragraphs(rows,2))
        self.assertIn('устройств, соединённых',result)
        self.assertEqual(result.count('Раздел 2.1'),1)
        self.assertNotIn('# Страница 2 — Габаритные',result)

    def test_columns_use_empty_gutter(self):
        words=[{'x0':10,'x1':40},{'x0':50,'x1':100},{'x0':160,'x1':220}]
        columns=ordered_columns(words)
        self.assertEqual([len(column) for column in columns],[2,1])

    def test_child_rule_keeps_parent_hardware_variant(self):
        index=self.index('# Раздел 4.2\nЩит РК черт.77500-900-00-03.\n'
                         '# Раздел 4.2.1\nПри пропадании двух фидеров отключается резервный ввод щита РК.')
        answer=self.ask(index,'Что произойдёт с РК, если исчезнут фидеры?')
        self.assertEqual(answer['status'],'answer')
        self.assertIn('77500-900-00-03',answer['text'])
        self.assertEqual(len(answer['evidence']),2)


if __name__=='__main__':
    unittest.main()
