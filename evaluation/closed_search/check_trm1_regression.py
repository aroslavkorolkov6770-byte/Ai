"""Повторная проверка уже известного PDF; это не независимый экзамен.

Рубрика используется только после получения всех ответов. В поисковый API
передаются вопрос, субъект доступа, чат и выбранный файл — без эталона/страницы.
"""
import base64
import hashlib
import json
import re
import time
from pathlib import Path
from ai_core.site_documents import extract_document
from ai_core.site_pdf_locations import extract_pdf_layout, locate_fragment, prepare_pdf_lookup, prepare_structured_lookup
from ai_core.site_search import ClosedSiteIndex, Principal

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / '.runtime/pdf-manual-20261031'


def run():
    specification = ROOT / 'evaluation/closed_search/independent_trm1_20261030.json'
    assert hashlib.sha256(specification.read_bytes()).hexdigest() == '353dc7a3350f5ac771887737725ce7a2f392cbed56a236c44dd4613039443446'
    spec = json.loads(specification.read_text(encoding='utf-8'))
    pdf = ROOT / spec['source']
    data = pdf.read_bytes()
    assert hashlib.sha256(data).hexdigest() == spec['source_sha256']
    data_url = 'data:application/pdf;base64,' + base64.b64encode(data).decode()
    started = time.perf_counter()
    text, complete = extract_document(data_url, pdf.name)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT/'extracted.txt').write_text(text, encoding='utf-8')
    extraction_seconds = time.perf_counter() - started
    layout = extract_pdf_layout(data_url)
    (OUTPUT/'layout.json').write_text(json.dumps(layout, ensure_ascii=False), encoding='utf-8')
    record = {'id': 'document:trm1', 'revision': spec['source_sha256'], 'kind': 'document',
              'title': 'ТРМ1 — руководство 1.39', 'url': 'http://localhost/document/trm1',
              'access': {'scope': 'restricted', 'users': ['reader']}, 'complete': complete,
              'sections': [{'heading': '', 'text': text}]}
    index = ClosedSiteIndex(['http://localhost'])
    index.publish([record], index.begin('regression'))
    results = []
    for case in spec['cases'] + spec['dialogue']:
        answer = index.search(case['question'], Principal('reader', 'staff'), case.get('chat', case['id']), record['id'])
        results.append({'id': case['id'], 'question': case['question'], 'answer': answer})
    (OUTPUT/'answers.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    # Эта рубрика намеренно лежит вне runtime. Проверяем числа и ограничения,
    # а не только статус «answer». Вопросы/ожидания в исходном JSON не меняются.
    expected = {
        'trm-01': ['измерени', 'регулирован', 'температур'],
        'trm-02': [r'21\.\.\.120', 'постоянное'],
        'trm-03': ['10 ВА', 'не более'], 'trm-04': ['8 Вт', 'не более'],
        'trm-05': ['1 с', 'не более'], 'trm-06': ['10 мин', 'не более'],
        'trm-07': ['0,25 кг', 'без упаковки'], 'trm-08': ['0,4 кг', 'с упаковкой'],
        'trm-09': ['90 × 88 × 59', '± 1 мм'], 'trm-10': ['96 × 48 × 100', '± 1 мм'],
        'trm-11': ['IP20', 'для корпуса Д', 'лицевой'],
        'trm-12': ['Modbus RTU', 'Modbus ASCII', 'Только для модификации', 'RS-485'],
        'trm-13': ['7', 'Только для Modbus ASCII', '8'],
        'trm-14': ['минус 40', r'\+55'], 'trm-15': ['80%', r'\+35', 'без конденсации'],
        'trm-16': ['12 лет'], 'trm-17': ['24 месяца', 'со дня продажи'],
        'trm-18': ['бесплатный ремонт или замену', 'гарантийного срока', 'эксплуатации, транспортирования, хранения и монтажа'],
        'trm-19': ['на стеллажах'], 'trm-20': ['двум', 'каналов: 1', 'противоречие'],
    }
    for target, origin in zip(range(1, 9), (7, 8, 9, 10, 17, 17, 14, 15)):
        expected[f'follow-{target:02}'] = expected[f'trm-{origin:02}']
    lookup, structured = prepare_pdf_lookup(layout), prepare_structured_lookup(layout)
    grades = []
    for row in results:
        key, answer = row['id'], row['answer']
        status = 'not_found' if key in {'trm-21', 'trm-22', 'trm-23', 'trm-24'} else 'conflict' if key == 'trm-20' else 'answer'
        missing = [pattern for pattern in expected.get(key, []) if not re.search(pattern, answer['text'], re.I)]
        locations = [locate_fragment(layout, item['quote'], heading=item['heading'], lookup=lookup, structured_lookup=structured)
                     for item in answer.get('evidence', [])]
        passed = answer['status'] == status and not missing
        grades.append({'id': key, 'passed': passed, 'expected_status': status, 'actual_status': answer['status'], 'missing': missing,
                       'locations': [{'status': item['status'], 'page': item.get('initial_page')} for item in locations]})
    summary = {'method': 'known-document regression; no oracle passed to search', 'passed': sum(row['passed'] for row in grades),
               'total': len(grades), 'complete': complete, 'layout_complete': layout['complete'],
               'extraction_seconds': round(extraction_seconds, 3), 'grades': grades,
               'pdf_sha256': spec['source_sha256'], 'spec_sha256': hashlib.sha256(specification.read_bytes()).hexdigest()}
    (OUTPUT/'regression.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in summary.items() if key != 'grades'}, ensure_ascii=False))
    for grade in grades:
        if not grade['passed'] or any(item['status'] != 'located' for item in grade['locations']):
            print(json.dumps(grade, ensure_ascii=False))
    assert summary['passed'] == summary['total'], 'See regression.json for failed answers'


if __name__ == '__main__':
    run()
