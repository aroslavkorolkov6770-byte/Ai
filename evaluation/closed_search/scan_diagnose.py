"""Локальная диагностика только собственных вымышленных растровых файлов."""
import logging
import tempfile
from pathlib import Path
from ai_core import site_scan_tables as scans
from ai_core.site_documents import _command, _run, _ocr, _join_visual_lines
from .scan_probe import ASSETS


def main():
    logging.basicConfig(level=logging.INFO)
    original = scans.reliable
    def inspect(words):
        result = original(words)
        if not result:
            print('UNRELIABLE', [(word['text'], word['confidence']) for word in words])
        return result
    scans.reliable = inspect
    with tempfile.TemporaryDirectory() as directory:
        for name in ['Сложная_таблица_Аметрин.pdf', 'Объединённые_строки_Хризолит.pdf', 'Редакция_Родонит_v2.pdf']:
            prefix = Path(directory) / 'page'
            _run([_command('pdftoppm'), '-f', '1', '-l', '1', '-r', '220', '-png', '-singlefile', str(ASSETS / name), str(prefix)])
            result = scans.extract_scan(prefix.with_suffix('.png'), lambda path: _ocr(path, tsv=True), _join_visual_lines)
            print(name, result)


if __name__ == '__main__':
    main()
