"""Создаёт небольшие настоящие файлы для сквозной проверки структуры."""
import base64
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    factory = runpy.run_path(str(ROOT / 'tests/test_site_search_structure.py'))['word_document']
    html = '<h1>Сроки</h1><table><tr><th>Товар</th><th>Срок хранения</th></tr><tr><td>Селенит</td><td>6 календарных дней<sup>1</sup></td></tr></table><p>[1]: Исключение: если упаковка вскрыта, товар Селенит хранится 2 календарных дня.</p>'
    documents = [
        ('lab_struct_docx', 'Учебная_таблица_Берилл.docx', factory()),
        ('lab_struct_html', 'Учебная_таблица_Селенит.html', html.encode()),
    ]
    directory = ROOT / '.runtime/closed-search-lab-20261028/structure-assets'
    directory.mkdir(exist_ok=True)
    manifest = []
    for identifier, name, data in documents:
        path = directory / name
        if path.exists() and path.read_bytes() != data:
            # ZIP содержит время упаковки; сохраняем уже созданный контрольный файл.
            data = path.read_bytes()
        else:
            path.write_bytes(data)
        manifest.append({'id': identifier, 'name': name, 'searchAuthority': 'official',
                         'data': 'data:application/octet-stream;base64,' + base64.b64encode(data).decode()})
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'fixtures': len(manifest), 'directory': str(directory)}))


if __name__ == '__main__':
    main()
