"""Оригинальные растровые контрольные PDF, без текстового слоя и правил компании.

Запускать bundled Python: Pillow и reportlab нужны только для изготовления тестов.
Ожидания задаются здесь до изменения распознавания, а не выводятся из ответа OCR.
"""
import json
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'evaluation/closed_search/fixtures/scans'
PREVIEWS = ROOT / '.runtime/closed-search-lab-20261028/scan-assets'
FONT = 'C:/Windows/Fonts/arial.ttf'


def page(title, columns, heights, cells, *, angle=0, note=None, damaged=False):
    image = Image.new('RGB', (1800, 2100), 'white')
    draw = ImageDraw.Draw(image)
    font, small = ImageFont.truetype(FONT, 34), ImageFont.truetype(FONT, 30)
    draw.text((90, 70), title, font=font, fill='black')
    draw.text((90, 125), 'Учебный документ. Все правила вымышлены.', font=small, fill='black')
    xs, ys = [90], [260]
    for width in columns:
        xs.append(xs[-1] + width)
    for height in heights:
        ys.append(ys[-1] + height)
    for row, col, rowspan, colspan, text in cells:
        left, top, right, bottom = xs[col], ys[row], xs[col + colspan], ys[row + rowspan]
        draw.rectangle((left, top, right, bottom), outline='black', width=3)
        lines, current = [], ''
        for word in text.split():
            candidate = (current + ' ' + word).strip()
            if draw.textlength(candidate, font=font) > right - left - 40 and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
        for offset, line in enumerate(lines):
            draw.text((left + 20, top + 18 + offset * 43), line, font=font, fill='black')
    if note:
        for offset, line in enumerate(textwrap.wrap(note, 76)):
            draw.text((90, ys[-1] + 60 + offset * 44), line, font=small, fill='black')
    if damaged:
        # Физически пустая ячейка: нельзя достраивать число по соседней строке.
        draw.rectangle((xs[1] + 6, ys[2] + 6, xs[2] - 6, ys[3] - 6), fill='white')
    image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor='white')
    return image.filter(ImageFilter.GaussianBlur(0.25))


def headers():
    return [(0, 0, 2, 1, 'Товар'), (0, 1, 1, 2, 'Срок хранения'),
            (1, 1, 1, 1, 'Если упаковка не вскрыта'), (1, 2, 1, 1, 'Если упаковка вскрыта')]


def matrix(rows, title, angle=0, damaged=False):
    cells = headers()
    for row, values in enumerate(rows, 2):
        cells.extend((row, col, 1, 1, text) for col, text in enumerate(values))
    return page(title, [480, 570, 570], [110, 150] + [150] * len(rows), cells, angle=angle, damaged=damaged)


def write_pdf(name, pages):
    target = OUT / name
    if target.exists():
        raise ValueError('Do not overwrite frozen scan fixtures: ' + name)
    pdf = canvas.Canvas(str(target), pagesize=(600, 700), invariant=1)
    for number, image in enumerate(pages, 1):
        image.save(PREVIEWS / (target.stem + f'-page-{number}.png'))
        pdf.drawImage(ImageReader(image), 0, 0, 600, 700)
        pdf.showPage()
    pdf.save()
    if any((p.extract_text() or '').strip() for p in PdfReader(target).pages):
        raise ValueError('Fixture must be image-only')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    write_pdf('Сложная_таблица_Аметрин.pdf', [
        matrix([['Аметрин', '8 календарных дней', '2 календарных дня'], ['Турмалин', '12 календарных дней', '3 календарных дня']], 'Сроки хранения. Страница 1', angle=0.65),
        matrix([['Нефрит', '5 календарных дней', '1 календарный день']], 'Сроки хранения. Страница 2', angle=-0.45)])
    cells = [(0, 0, 1, 1, 'Товар'), (0, 1, 1, 1, 'Условие'), (0, 2, 1, 1, 'Срок хранения'),
             (1, 0, 2, 1, 'Хризолит'), (1, 1, 1, 1, 'Если упаковка не вскрыта'), (1, 2, 1, 1, '10 календарных дней'),
             (2, 1, 1, 1, 'Если упаковка вскрыта'), (2, 2, 1, 1, '4 календарных дня')]
    write_pdf('Объединённые_строки_Хризолит.pdf', [page('Хранение Хризолита', [450, 620, 550], [120, 180, 180], cells, angle=0.4)])
    cells = [(0, 0, 1, 1, 'Товар'), (0, 1, 1, 1, 'Срок хранения'),
             (1, 0, 1, 1, 'Родонит'), (1, 1, 1, 1, '7 календарных дней [1]')]
    write_pdf('Редакция_Родонит_v1.pdf', [page('Регламент Родонит. Редакция 1', [600, 1020], [120, 160], cells,
        note='[1]: Исключение: если упаковка вскрыта, товар Родонит хранится 1 календарный день.')])
    cells[-1] = (1, 1, 1, 1, '11 календарных дней [1]')
    write_pdf('Редакция_Родонит_v2.pdf', [page('Регламент Родонит. Редакция 2', [600, 1020], [120, 160], cells,
        note='[1]: Исключение: если упаковка вскрыта, товар Родонит хранится 3 календарных дня.')])
    write_pdf('Повреждённая_таблица_Коралл.pdf', [matrix([['Коралл', '9 календарных дней', '2 календарных дня']], 'Повреждённая таблица', damaged=True)])
    spec = {'documents': [
        {'id': 'lab_scan_matrix', 'name': 'Сложная_таблица_Аметрин.pdf'},
        {'id': 'lab_scan_rowspan', 'name': 'Объединённые_строки_Хризолит.pdf'},
        {'id': 'lab_scan_note', 'name': 'Редакция_Родонит_v2.pdf'},
        {'id': 'lab_scan_damaged', 'name': 'Повреждённая_таблица_Коралл.pdf'}], 'cases': [
        {'id': 'matrix-closed', 'source': 'lab_scan_matrix', 'q': 'Упаковка не вскрыта. Сколько хранится товар Аметрин?', 'has': ['8 календарных'], 'absent': ['2 календарных', '12 календарных']},
        {'id': 'matrix-open', 'source': 'lab_scan_matrix', 'q': 'Упаковка вскрыта. Сколько хранится товар Аметрин?', 'has': ['2 календарных'], 'absent': ['8 календарных']},
        {'id': 'matrix-neighbour', 'source': 'lab_scan_matrix', 'q': 'Упаковка вскрыта. Сколько хранится товар Турмалин?', 'has': ['3 календарных'], 'absent': ['2 календарных', '12 календарных']},
        {'id': 'matrix-page-two', 'source': 'lab_scan_matrix', 'q': 'Упаковка вскрыта. Сколько хранится товар Нефрит?', 'has': ['1 календарный'], 'absent': ['5 календарных']},
        {'id': 'matrix-clarify', 'source': 'lab_scan_matrix', 'q': 'Сколько хранится товар Аметрин?', 'status': 'clarify', 'has': ['упаковка'], 'absent': ['8 календарных', '2 календарных']},
        {'id': 'rowspan-closed', 'source': 'lab_scan_rowspan', 'q': 'Упаковка не вскрыта. Сколько хранится товар Хризолит?', 'has': ['10 календарных'], 'absent': ['4 календарных']},
        {'id': 'rowspan-open', 'source': 'lab_scan_rowspan', 'q': 'Упаковка вскрыта. Сколько хранится товар Хризолит?', 'has': ['4 календарных'], 'absent': ['10 календарных']},
        {'id': 'note-current', 'source': 'lab_scan_note', 'q': 'Упаковка вскрыта. Сколько хранится товар Родонит?', 'has': ['3 календарных'], 'absent': ['11 календарных', '1 календарный']},
        {'id': 'damaged', 'source': 'lab_scan_damaged', 'q': 'Упаковка не вскрыта. Сколько хранится товар Коралл?', 'status': 'unavailable', 'absent': ['9 календарных', '2 календарных']}
    ], 'holdout': [
        {'id': 'paraphrase', 'source': 'lab_scan_matrix', 'q': 'Товар Аметрин уже открыли. Упаковка вскрыта. Как долго его хранить?', 'has': ['2 календарных'], 'absent': ['8 календарных']},
        {'id': 'second-closed', 'source': 'lab_scan_matrix', 'q': 'Упаковка не вскрыта. Сколько дней можно хранить Нефрит?', 'has': ['5 календарных'], 'absent': ['1 календарный']}
    ]}
    (OUT / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'directory': str(OUT), 'pdfs': 5, 'pages': 6, 'cases': 9, 'holdout': 2}))


if __name__ == '__main__':
    main()
