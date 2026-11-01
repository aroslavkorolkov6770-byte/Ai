"""Подготовка проверяемых локальных копий двух CSV, без изменения оригиналов.

Каждая строка остаётся отдельным разделом. Часть содержит не более 50 товаров
и 70 000 символов, поэтому большой CSV не обрезается общим лимитом документа.
Порядок строк и их исходные значения сохраняются в manifest.json.
"""
import csv
import hashlib
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / '.runtime/closed-search-lab-20261028/real-materials'
DOWNLOADS = Path('C:/Users/KROLL/Downloads')
FILES = ['products_export_18-06-2026.csv', 'products_export_18-06-2026 (1).csv']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    DEST.mkdir(exist_ok=True)
    manifest = {'snapshot_date': '2026-06-18', 'documents': [], 'inputs': [], 'rows': 0,
                'note': 'Локальная проверка архивного снимка; актуальность правил, цен и остатков не подтверждена.'}
    for file_index, name in enumerate(FILES, 1):
        raw = (DOWNLOADS / name).read_bytes()
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), delimiter=';')
        required = {'Код', 'Наименование', 'Описание', 'Скрыт'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Unexpected product columns')
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError('CSV row width mismatch')
        manifest['inputs'].append({'name': name, 'sha256': digest(raw), 'rows': len(rows), 'columns': reader.fieldnames})
        batch, origins, part = [], [], 0

        def save():
            nonlocal batch, origins, part
            if not batch:
                return
            part += 1
            identifier = f'real_products_{file_index}_{part:03d}'
            filename = f'Товары_18-06-2026_выгрузка_{file_index}_часть_{part:03d}.md'
            content = '\n\n'.join(batch).encode('utf-8')
            (DEST / filename).write_bytes(content)
            manifest['documents'].append({'id': identifier, 'name': filename, 'sha256': digest(content),
                                          'input': name, 'rows': origins, 'kind': 'catalog-copy'})
            batch, origins = [], []

        for number, row in enumerate(rows, 2):
            if row['Скрыт'] not in {'Да', 'Нет'}:
                raise ValueError('Unknown hidden-product flag')
            if row['Скрыт'] == 'Да':
                continue
            title = row['Наименование'].strip()
            if not title or '\n' in title or '\r' in title:
                raise ValueError('Invalid product title')
            # Административные поля не становятся ответом покупателю. Полное
            # происхождение и код хранятся в закрытом журнале подготовки.
            description = row['Описание'].strip()
            block = '# ' + title + '\n' + (description or 'Описание товара в выгрузке отсутствует.')
            if len(block) > 70000:
                raise ValueError('One product exceeds the document budget')
            if batch and (len(batch) >= 50 or sum(map(len, batch)) + len(block) + 2 * len(batch) > 70000):
                save()
            batch.append(block)
            origins.append({'row': number, 'code': row['Код'], 'title': title,
                            'description_sha256': digest(description.encode('utf-8'))})
            manifest['rows'] += 1
        save()
    original = DOWNLOADS / 'ЧаВо покупателей.doc'
    converted = DEST.parent / 'ЧаВо покупателей.docx'
    if not converted.exists():
        raise ValueError('Convert the original Word document locally, read-only, before preparing')
    faq = converted.read_bytes()
    (DEST / converted.name).write_bytes(faq)
    manifest['inputs'].append({'name': original.name, 'sha256': digest(original.read_bytes()), 'conversion': 'Word read-only to DOCX; macros disabled'})
    manifest['documents'].append({'id': 'real_faq', 'name': converted.name, 'sha256': digest(faq),
                                  'input': original.name, 'kind': 'word-conversion'})
    (DEST / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'documents': len(manifest['documents']), 'rows': manifest['rows'], 'path': str(DEST)}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, csv.Error) as error:
        raise SystemExit('Real material preparation failed: ' + type(error).__name__) from error
