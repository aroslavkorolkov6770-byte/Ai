"""Первый прогон. Этот процесс не открывает эталоны или ручное извлечение."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / 'results'


def now():
    return datetime.now(timezone.utc).isoformat()


def write_new(name, value):
    with (OUT / name).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def main():
    freeze = json.loads((HERE / 'FREEZE.json').read_text(encoding='utf-8'))
    # Проверяем движок и разрешённые входы. Gold даже для хеширования здесь не читаем.
    for name, expected in freeze['runtime'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('runtime_changed:' + name)
    for name in ['questions.json', 'sources.json', 'run.py']:
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != freeze['files'][name]:
            raise RuntimeError('input_changed:' + name)
    OUT.mkdir(exist_ok=True)
    write_new('START.json', {'started_at': now(), 'frozen_at': freeze['frozen_at'],
                            'freeze_sha256': hashlib.sha256((HERE / 'FREEZE.json').read_bytes()).hexdigest(),
                            'python': sys.version, 'pid': os.getpid()})
    sys.path.insert(0, str(ROOT))
    from ai_core import site_documents
    from ai_core.site_search import Principal
    from ai_core.site_search_runtime import DurableSearch, engine_version

    # Меняем только место производных данных, не алгоритмы и не рабочий кэш.
    site_documents.CACHE = OUT / 'document-cache'
    sources = json.loads((HERE / 'sources.json').read_text(encoding='utf-8'))
    questions = json.loads((HERE / 'questions.json').read_text(encoding='utf-8'))
    records, extraction = [], []
    for source in sources:
        data = (HERE / 'sources' / source['file']).read_bytes()
        if hashlib.sha256(data).hexdigest() != source['sha256']:
            raise RuntimeError('source_changed:' + source['id'])
        started = time.perf_counter()
        details = {}

        def progress(value):
            details.update(value)
            print(json.dumps({'extracting': source['id'], 'progress': value}, ensure_ascii=False), flush=True)

        error = None
        try:
            text, complete = site_documents.extract_document(
                'data:' + source['content_type'].split(';')[0] + ';base64,' + base64.b64encode(data).decode('ascii'),
                source['file'], progress=progress)
        except Exception:
            # Не теряем неудобный документ и его вопросы из знаменателя.
            error = traceback.format_exc()
            text, complete = '', False
        (OUT / (source['id'] + '-extracted.txt')).write_text(text, encoding='utf-8')
        extraction.append({'id': source['id'], 'seconds': time.perf_counter()-started,
                           'characters': len(text), 'complete': complete, 'progress': details, 'error': error})
        print(json.dumps({'extracted': extraction[-1]}, ensure_ascii=False), flush=True)
        records.append({'id': 'document:' + source['id'], 'revision': source['sha256'],
                        'title': source['title'], 'kind': 'document', 'authority': 'official',
                        'url': 'http://holdout.invalid/doc/' + source['id'],
                        'access': {'scope': 'restricted', 'users': ['exam-reader']},
                        'complete': complete, 'sections': [{'heading': source['title'], 'text': text}]})
    write_new('extraction.json', extraction)
    write_new('records.json', records)
    engine = DurableSearch(['http://holdout.invalid'], OUT / 'exam.sqlite', extraction_version=site_documents.PARSER_VERSION)
    started = time.perf_counter()
    if not engine.publish(records, engine.begin('holdout-20261031-first')):
        raise RuntimeError('publish_failed')
    write_new('index.json', {'seconds': time.perf_counter()-started, 'state': engine.index.state,
                            'passages': len(engine.index.passages), 'engine_version': engine_version(),
                            'parser_version': site_documents.PARSER_VERSION})
    revisions = {source['id']: source['revision'] for source in records}
    with (OUT / 'baseline.jsonl').open('x', encoding='utf-8') as handle:
        for question in questions:
            started = time.perf_counter()
            try:
                result = engine.query(question['question'], Principal('exam-reader', 'staff'), question['chat'], None, revisions)
            except Exception:
                result = {'status': 'error', 'text': '', 'evidence': [], 'links': [], 'error': traceback.format_exc()}
            row = {**question, 'at': now(), 'milliseconds': round((time.perf_counter()-started)*1000, 3), 'result': result}
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
            print(json.dumps({'id': question['id'], 'status': result.get('status'), 'ms': row['milliseconds']}), flush=True)
    write_new('FINISH.json', {'finished_at': now(), 'answers': len(questions),
                             'baseline_sha256': hashlib.sha256((OUT / 'baseline.jsonl').read_bytes()).hexdigest()})


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
