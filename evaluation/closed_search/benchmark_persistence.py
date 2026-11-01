"""Воспроизводимое измерение ядра с SQLite на расширенном синтетическом корпусе.

Стенд сайта не изменяется. Время включает проверку прав/версий и запись контекста,
но не HTTP, UI и OCR. Это явно отличает измерение ядра от нагрузки всего сайта.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import platform
import time
from pathlib import Path

from ai_core.site_documents import extract_document
from ai_core.site_search import Principal
from ai_core.site_search_runtime import DurableSearch

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / ".runtime/closed-search-lab-20261028"
ORIGIN = "http://127.0.0.1:3040"


def synthetic(number: int):
    code = "Модуль" + str(10000 + number)
    days = 2 + number % 11
    return {"id": "bench-" + str(number), "revision": "v1", "kind": "lesson", "title": "Учебный порядок " + code,
            "url": ORIGIN + "/materials/bench-" + str(number), "access": {"scope": "workspace"},
            "sections": [
                {"heading": "Хранение", "text": f"Контейнер {code} хранится {days} календарных дней. Контейнер ставят на выделенную полку и сохраняют заводскую этикетку."},
                {"heading": "Получение", "text": f"Контейнер {code} выдаёт координатор склада. Сотрудник записывает номер контейнера в журнал выдачи и проверяет упаковку."},
                {"heading": "Повреждение", "text": f"Если контейнер {code} повреждён, сотрудник прекращает использование и сообщает координатору склада. Самостоятельный ремонт контейнера запрещён."},
                {"heading": "Учёт", "text": f"Номер контейнера {code} указывают в карточке движения. Карточка хранится вместе с накладной в выделенном разделе архива."},
                {"heading": "Учебный характер", "text": f"Порядок {code} вымышлен и используется только для измерения поиска. Он не является регламентом компании."},
            ]}


def percentile(times, fraction):
    return round(sorted(times)[max(0, math.ceil(len(times) * fraction) - 1)], 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='baseline')
    parser.add_argument('--rounds', type=int, default=1)
    parser.add_argument('--concurrent', type=int, default=0)
    args = parser.parse_args()
    base = json.loads((LAB / 'probe-snapshot.json').read_text(encoding='utf-8'))
    for item in base['sources']:
        if 'file_data' in item:
            text, complete = extract_document(item.pop('file_data'), item['file_name'])
            item['sections'], item['complete'] = [{'heading': item['title'], 'text': text}], complete
    directory = LAB / ('benchmark-' + args.label + '-' + str(time.time_ns()))
    directory.mkdir()
    report = {'label': args.label, 'mode': 'local-core-sqlite-no-http-no-ocr', 'platform': platform.platform(),
              'python': platform.python_version(), 'logicalCpuCount': os.cpu_count(), 'sizes': []}
    for size in (100, 1000, 3000):
        extra = size - len(base['sources'])
        if extra <= 0:
            raise ValueError('Base fixture exceeded benchmark size')
        sources = base['sources'] + [synthetic(number) for number in range(extra)]
        db = directory / f'{size}.sqlite3'
        runtime = DurableSearch([ORIGIN], db)
        start = time.perf_counter()
        runtime.publish(sources, runtime.begin('benchmark-' + str(size)), base.get('aliases'))
        build_ms = (time.perf_counter() - start) * 1000
        references = {item['id']: item['revision'] for item in sources}
        code = 'Модуль' + str(10000 + extra - 1)
        days = 2 + (extra - 1) % 11
        scenarios = [
            (f'Сколько хранится контейнер {code}?', 'answer', str(days) + ' календарных'),
            (f'Кто выдаёт контейнер {code}?', 'answer', 'координатор склада'),
            (f'Кто выдаёт контейнер {code} и сколько дней он хранится?', 'answer', str(days) + ' календарных'),
            ('Кому направить заявку Север?', 'answer', 'старшему'),
            ('Какие перчатки нужны для мытья пола внутри помещения?', 'answer', 'только резиновые'),
            ('Сколько хранится заказ Кварц?', 'conflict', 'противоречие'),
            ('Как отменить заявку Комета и сколько ждать подтверждения?', 'answer', '2 рабочих'),
            ('Сколько хранится контейнер НеизвестныйАстероид?', 'not_found', 'не нашёл'),
        ]
        measurements = []
        def execute(task):
            position, scenario = task
            question, status, required = scenario
            started = time.perf_counter()
            result = runtime.query(question, Principal('bench-reader', 'staff'), 'case-' + str(position), None, references)
            return {'question': question, 'ms': round((time.perf_counter() - started) * 1000, 2),
                    'status': result['status'], 'pass': result['status'] == status and required in result['text']}
        for position, scenario in enumerate(scenarios * args.rounds):
            measurements.append(execute((position, scenario)))
        start = time.perf_counter()
        restored = DurableSearch([ORIGIN], db)
        restore_ms = (time.perf_counter() - start) * 1000
        concurrency = None
        if args.concurrent:
            start = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrent) as pool:
                parallel = list(pool.map(execute, enumerate(scenarios * 2)))
            elapsed = time.perf_counter() - start
            concurrency = {'clients': args.concurrent, 'requests': len(parallel), 'passed': sum(item['pass'] for item in parallel),
                           'p50Ms': percentile([item['ms'] for item in parallel], .5), 'p95Ms': percentile([item['ms'] for item in parallel], .95),
                           'requestsPerSecond': round(len(parallel) / elapsed, 2)}
        times = [item['ms'] for item in measurements]
        row = {'sources': size, 'passages': len(runtime.index.passages), 'buildMs': round(build_ms, 2),
               'restoreMs': round(restore_ms, 2), 'restoredFromDisk': restored.restored, 'dbBytes': db.stat().st_size,
               'requests': len(times), 'passed': sum(item['pass'] for item in measurements), 'p50Ms': percentile(times, .5),
               'p95Ms': percentile(times, .95), 'maxMs': max(times), 'concurrent': concurrency, 'measurements': measurements}
        report['sizes'].append(row)
        (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({key: value for key, value in row.items() if key != 'measurements'}), flush=True)
    print(json.dumps({'report': str(directory / 'report.json')}), flush=True)


if __name__ == '__main__':
    main()
