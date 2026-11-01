"""Искажение не должно превращать запрет в разрешение или терять число срока."""
import tempfile
import io
import base64
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
from ai_core.site_scan_quality import normalize_lighting, clipped_content, unrecognized_ink, critical_consistent
from ai_core.site_scan_tables import Cell, describe_table
from ai_core.site_documents import extract_document
from pypdf import PdfReader, PdfWriter
from ai_core.site_search import ClosedSiteIndex, Principal
from ai_core.site_search_runtime import DurableSearch
from evaluation.closed_search.quality_probe import load_sources, errors_for, ORIGIN, ASSETS


def word(text, left=20, top=20, width=30, height=20, confidence=98):
    return dict(text=text,left=left,top=top,width=width,height=height,confidence=confidence)


class QualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec,cls.sources,cls.extraction=load_sources()

    def test_real_degradations_keep_facts_or_refuse(self):
        index=ClosedSiteIndex([ORIGIN]);index.publish(self.sources,index.begin('quality'))
        for case in self.spec['cases']:
            with self.subTest(case=case['id']):
                answer=index.search(case['q'],Principal('a','staff'),case['id'],case['source'])
                self.assertEqual(errors_for(case,answer),[])

    def test_light_normalization_preserves_white_page_and_recovers_shadow(self):
        clean=np.full((500,600),255,np.uint8)
        cv2.putText(clean,'13', (180,250),cv2.FONT_HERSHEY_SIMPLEX,2,0,3)
        self.assertTrue(np.array_equal(normalize_lighting(clean),clean))
        shaded=np.uint8(clean.astype(float)*np.linspace(.28,1,600))
        corrected=normalize_lighting(shaded)
        self.assertGreater(float(corrected[:100].mean()),250)
        self.assertLess(float(corrected[clean==0].mean()),5)

    def test_ink_at_page_edge_is_not_accepted_as_complete_line(self):
        image=np.full((400,600),255,np.uint8)
        cv2.putText(image,'Never store', (30,90),cv2.FONT_HERSHEY_SIMPLEX,1,0,2)
        self.assertFalse(clipped_content(image))
        cv2.rectangle(image,(0,180),(9,200),0,-1)
        self.assertTrue(clipped_content(image))

    def test_missing_recognition_of_visible_ink_is_not_high_confidence(self):
        image=np.full((200,300),255,np.uint8)
        cv2.rectangle(image,(20,20),(28,38),0,-1)
        self.assertTrue(unrecognized_ink(image,[]))
        self.assertFalse(unrecognized_ink(image,[word('1',20,20,10,20)]))

    def test_disagreement_missing_negation_and_low_number_confidence_are_refused(self):
        for left,right in [([word('13')],[word('18')]),([word('Не')],[]),
                           ([word('13')],[word('13',confidence=79)]),([word('не')],[word('ни')])]:
            with self.subTest(left=left,right=right):
                self.assertFalse(critical_consistent(left,right))
        self.assertTrue(critical_consistent([word('13,')],[word('13')]))
        # Совпадение цифры из другой строки не подтверждает исходную ячейку.
        self.assertFalse(critical_consistent([word('13')],[word('13',top=100)]))

    def test_numeric_column_cannot_keep_unit_after_quantity_disappears(self):
        def cells(value):
            return [Cell(0,0,1,1,'Товар'),Cell(0,1,1,2,'Срок хранения'),
                    Cell(1,0,2,1,'Пример'),Cell(1,1,2,2,value)]
        for value in ['календарных дней','‚алендарных дней','рабочих часов']:
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'quantity_missing'):
                describe_table(cells(value),2,2,1,1)
        for value in ['13 календарных дней','три рабочих дня','бессрочно']:
            self.assertIn(value,describe_table(cells(value),2,2,1,1))

    def test_empty_unreadable_scan_retains_status_after_restart_without_acl_leak(self):
        source={'id':'unreadable','revision':'1','title':'Закрытый скан','kind':'document',
                'url':ORIGIN+'/materials/unreadable','access':{'scope':'restricted','users':['a']},
                'complete':True,'sections':[{'heading':'Скан','text':'[STRUCTURE_UNCERTAIN]'}]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'index.sqlite3'
            runtime=DurableSearch([ORIGIN],path)
            runtime.publish([source],runtime.begin('unreadable'))
            reopened=DurableSearch([ORIGIN],path)
            self.assertTrue(reopened.restored)
            for question in ['Сколько дней?','Кратко перескажи выбранный файл']:
                self.assertEqual(reopened.query(question,Principal('a','staff'),'chat','unreadable',{'unreadable':'1'})['status'],'unavailable')
                denied=reopened.query(question,Principal('b','staff'),'chat','unreadable',{'unreadable':'1'})
                self.assertEqual(denied['status'],'not_found')
                self.assertEqual(denied['evidence'],[])
            self.assertEqual(reopened.query('Как доставляется заказ Вега?',Principal('a','staff'),'other',None,{'unreadable':'1'})['status'],'not_found')

    def test_quality_checks_do_not_run_ocr_again_on_cached_reads_or_questions(self):
        index=ClosedSiteIndex([ORIGIN]);index.publish(self.sources,index.begin('quality-cache'))
        with patch('ai_core.site_documents._scan_text',side_effect=AssertionError('Repeated OCR')):
            self.assertEqual(load_sources()[1],self.sources)
            for case in self.spec['cases']:
                answer=index.search(case['q'],Principal('a','staff'),'cached-'+case['id'],case['source'])
                self.assertEqual(errors_for(case,answer),[])

    def test_blank_reverse_side_does_not_close_readable_front_page(self):
        writer=PdfWriter(clone_from=PdfReader(ASSETS/'Чёткие_сроки.pdf'))
        writer.add_blank_page(width=600,height=700)
        buffer=io.BytesIO();writer.write(buffer)
        text,complete=extract_document('data:application/pdf;base64,'+base64.b64encode(buffer.getvalue()).decode(),'blank-back.pdf')
        self.assertTrue(complete)
        self.assertIn('13 календарных дней',text)
        self.assertNotIn('[STRUCTURE_UNCERTAIN]',text)


if __name__=='__main__':unittest.main()
