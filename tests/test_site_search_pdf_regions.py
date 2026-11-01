"""Привязка областей PDF: независимые числа, колонки, сноски и редакции."""
import tempfile
import unittest
from pathlib import Path
from ai_core.site_pdf_locations import locate_fragment, prepare_structured_lookup
from ai_core.site_search import Principal
from ai_core.site_search_runtime import DurableSearch


def word(text,x,y):
    return {'text':text,'confidence':100,'box':[x,y,x+.045,y+.02]}


def layout(regions):
    pages={}
    for region in regions:
        pages.setdefault(region['page'],{'page':region['page'],'words':[],'regions':[]})['regions'].append(region)
    return {'sha256':'a'*64,'page_count':4,'complete':True,'pages':list(pages.values())}


def table(page=2,text='Наименование: Блок РС 5; Масса, кг: 81 (с кожухом 9 кг).'):
    return {'kind':'table','page':page,'heading':f'Страница {page} — Таблица 8', 'text':text,
            'rects':[[.1,.3,.4,.35],[.1,.1,.4,.15],[.5,.7,.9,.74]],
            'row_rects':[[.1,.3,.4,.35]],'note_rects':[[.5,.7,.9,.74]]}


class PdfRegionsTests(unittest.TestCase):
    def test_column_reading_order_and_exact_excerpt(self):
        region={'kind':'paragraph','heading':'Раздел 1.2 — страница 2','page':2,
                'text':'Срок проверки блока семь дней. Затем включите питание.',
                'words':[word(text,.1+i*.05,.2) for i,text in enumerate('Срок проверки блока семь дней'.split())]
                        +[word(text,.6+i*.05,.1) for i,text in enumerate('Затем включите питание'.split())]}
        pdf=layout([region])
        pdf['pages'][0]['words']=sorted(region['words'],key=lambda value:(value['box'][1],value['box'][0]))
        answer=locate_fragment(pdf,region['text'],heading=region['heading'])
        self.assertEqual(answer['status'],'located');self.assertEqual(answer['initial_page'],2)
        excerpt=locate_fragment(pdf,'Затем включите питание.',heading=region['heading'])
        self.assertEqual(excerpt['status'],'located')
        self.assertTrue(all(box[0]>=.6 for box in excerpt['hits'][0]['rects']))

    def test_table_matches_row_and_its_note_without_neighbor(self):
        region=table();pdf=layout([region])
        result=locate_fragment(pdf,region['text'],heading=region['heading'])
        self.assertEqual(result['status'],'located')
        self.assertIn([.5,.7,.9,.74],result['hits'][0]['rects'])
        self.assertNotIn([.1,.35,.4,.4],result['hits'][0]['rects'])

    def test_different_number_and_missing_negation_are_not_matches(self):
        region=table(text='Наименование: Блок РС 5; Масса не более, кг: 81.')
        for altered in [region['text'].replace('81','8'),region['text'].replace('не более','более')]:
            self.assertEqual(locate_fragment(layout([region]),altered)['status'],'unlocated')

    def test_duplicate_rows_are_ambiguous_without_verified_heading(self):
        left,right=table(2),table(3)
        pdf=layout([left,right])
        self.assertEqual(locate_fragment(pdf,left['text'])['status'],'ambiguous')
        self.assertEqual(locate_fragment(pdf,left['text'],heading=right['heading'])['initial_page'],3)

    def test_wrong_page_cannot_choose_other_region(self):
        region=table(2)
        self.assertEqual(locate_fragment(layout([region]),region['text'],heading='Страница 4 — Таблица 8')['status'],'unlocated')

    def test_invalid_geometry_is_not_returned(self):
        region=table();region['rects'][0]=[-.1,.3,.4,.35]
        self.assertEqual(locate_fragment(layout([region]),region['text'])['status'],'unlocated')

    def test_unreliable_digit_in_paragraph_stays_unlocated(self):
        region={'kind':'paragraph','heading':'Правило','page':2,'text':'Срок ремонта 8 дней.',
                'words':[word(text,.1+i*.05,.2) for i,text in enumerate('Срок ремонта 8 дней'.split())]}
        region['words'][2]['confidence']=50
        self.assertEqual(locate_fragment(layout([region]),region['text'])['status'],'unlocated')

    def test_precomputed_lookup_has_identical_result(self):
        region=table();pdf=layout([region]);prepared=prepare_structured_lookup(pdf)
        self.assertEqual(locate_fragment(pdf,region['text']),locate_fragment(pdf,region['text'],structured_lookup=prepared))

    def test_move_delete_restart_and_acl(self):
        origin='http://localhost'
        def source(page,revision):
            region=table(page)
            return {'id':'document:a','title':'Руководство','kind':'document','revision':revision,
                    'url':origin+'/tasks?documentId=a','fragment_url':origin+'/closed-search-fragment',
                    'access':{'scope':'private','owner_id':'reader'},'pdf_layout':layout([region]),
                    'sections':[{'heading':region['heading'],'text':region['text']}]}
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'index.sqlite3'
            runtime=DurableSearch([origin],db)
            runtime.publish([source(2,'v1')],runtime.begin('v1'))
            target=next(item for item in runtime.index.fragment_targets.values() if item['pdf_location']['status']=='located')
            reader=Principal('reader','staff');outsider=Principal('other','staff')
            self.assertEqual(runtime.fragment('document:a','v1',target['fragment'],outsider,{'document:a':'v1'})['status'],'not_found')
            runtime.publish([source(3,'v2')],runtime.begin('v2'))
            self.assertEqual(runtime.fragment('document:a','v1',target['fragment'],reader,{'document:a':'v2'})['status'],'changed')
            restored=DurableSearch([origin],db)
            moved=next(iter(restored.index.fragment_targets.values()))
            self.assertEqual(moved['pdf_location']['initial_page'],3)
            restored.publish([],restored.begin('removed'))
            self.assertEqual(restored.fragment('document:a','v2',moved['fragment'],reader,{})['status'],'not_found')


if __name__=='__main__':
    unittest.main()
