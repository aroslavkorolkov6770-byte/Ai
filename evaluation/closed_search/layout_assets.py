"""Новые растровые fixtures: без рамок, перспектива, явное продолжение и сноски."""
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader
from .scan_assets import page

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'evaluation/closed_search/fixtures/layout'
PREVIEWS = ROOT / '.runtime/closed-search-lab-20261028/layout-previews'
FONT = 'C:/Windows/Fonts/arial.ttf'


def plain(title, lines):
    image = Image.new('RGB', (1800, 2100), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT, 34)
    draw.text((90, 70), title, font=font, fill='black')
    for number, text in enumerate(lines):
        draw.text((90, 260 + number * 60), text, font=font, fill='black')
    return image


def borderless(missing=False):
    image = plain('Учебная таблица сроков. Все правила вымышлены.', [])
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT, 34)
    rows = [('Товар', 'Срок хранения', 'Ответственный'),
            ('Амазонит', '' if missing else '6 календарных дней', 'Координатор'),
            ('Кианит', '14 календарных дней', 'Старший сотрудник')]
    for row, values in enumerate(rows):
        for col, text in enumerate(values):
            draw.text(([90, 620, 1220][col], 260 + row * 150), text, font=font, fill='black')
    return image


def photograph(image):
    # Контрольная фотография создаётся проективным преобразованием всего листа.
    corners = [(220, 180), (1780, 320), (1910, 2120), (90, 2260)]
    original = [(0, 0), (1800, 0), (1800, 2100), (0, 2100)]
    matrix, target = [], []
    for (x, y), (u, v) in zip(corners, original):
        matrix += [[x, y, 1, 0, 0, 0, -u*x, -u*y], [0, 0, 0, x, y, 1, -v*x, -v*y]]
        target += [u, v]
    coefficients = np.linalg.solve(np.array(matrix), np.array(target))
    return image.transform((2100, 2400), Image.Transform.PERSPECTIVE, coefficients,
                           resample=Image.Resampling.BICUBIC, fillcolor=(75, 75, 75))


def write(name, images):
    target = OUT / name
    if target.exists():
        raise ValueError('Frozen fixture exists: ' + name)
    pdf = canvas.Canvas(str(target), pagesize=(600, 700), invariant=1)
    for number, image in enumerate(images, 1):
        image.save(PREVIEWS / (target.stem + f'-{number}.png'))
        pdf.drawImage(ImageReader(image), 0, 0, 600, 700)
        pdf.showPage()
    pdf.save()
    assert not any((p.extract_text() or '').strip() for p in PdfReader(target).pages)


def continuation(title, subject, text, note=None):
    return page(title, [600, 1020], [120, 250], [(0,0,1,1,'Поставка'), (0,1,1,1,'Действия'),
               (1,0,1,1,subject), (1,1,1,1,text)], note=note)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    write('Таблица_без_рамок.pdf', [borderless()])
    write('Пустая_ячейка_без_рамок.pdf', [borderless(True)])
    photo = page('Таблица 3. Учебные сроки', [650,970], [120,150,150],
                 [(0,0,1,1,'Товар'),(0,1,1,1,'Срок хранения'),(1,0,1,1,'Альбит'),(1,1,1,1,'9 календарных дней'),
                  (2,0,1,1,'Гелиодор'),(2,1,1,1,'4 календарных дня')])
    write('Фото_с_перспективой.pdf', [photograph(photo)])
    start = continuation('Таблица 7. Приёмка поставок', 'Флюорит',
                         'При повреждении упаковки сделайте фото и уведомите',
                         'Продолжение строки на следующей странице.')
    write('Перенос_строки_Флюорит.pdf', [start, continuation('Продолжение таблицы 7', 'Флюорит (продолжение)', 'старшего смены.')])
    write('Неверный_перенос_Флюорит.pdf', [start, continuation('Продолжение таблицы 9', 'Флюорит (продолжение)', 'старшего смены.')])
    first = page('Таблица 8. Учебное хранение Данбурита', [650,970], [120,150],
                 [(0,0,1,1,'Товар'),(0,1,1,1,'Срок хранения'),(1,0,1,1,'Данбурит'),(1,1,1,1,'8 календарных дней [1]')])
    footnote = ['[1]: Исключение: если упаковка вскрыта, товар Данбурит', 'хранится 2 календарных дня.']
    write('Сноска_на_следующей_странице.pdf', [first, plain('Сноски к таблице 8', footnote)])
    write('Чужая_сноска_на_следующей_странице.pdf', [first, plain('Сноски к таблице 9', footnote)])
    docs = [{'id': 'lab_layout_' + identifier, 'name': name} for identifier, name in [
        ('borderless','Таблица_без_рамок.pdf'),('missing','Пустая_ячейка_без_рамок.pdf'),('photo','Фото_с_перспективой.pdf'),
        ('continued','Перенос_строки_Флюорит.pdf'),('wrong_continued','Неверный_перенос_Флюорит.pdf'),
        ('notes','Сноска_на_следующей_странице.pdf'),('wrong_notes','Чужая_сноска_на_следующей_странице.pdf')]]
    cases = [
        {'id':'borderless-first','source':docs[0]['id'],'q':'Сколько хранится товар Амазонит?','has':['6 календарных'],'absent':['14 календарных','Кианит']},
        {'id':'borderless-next','source':docs[0]['id'],'q':'Сколько хранится товар Кианит?','has':['14 календарных'],'absent':['6 календарных','Амазонит']},
        {'id':'borderless-recipient','source':docs[0]['id'],'q':'Кто отвечает за товар Амазонит?','has':['Координатор'],'absent':['Старший сотрудник']},
        {'id':'photo-first','source':docs[2]['id'],'q':'Сколько хранится товар Альбит?','has':['9 календарных'],'absent':['4 календарных','Гелиодор']},
        {'id':'photo-next','source':docs[2]['id'],'q':'Сколько хранится товар Гелиодор?','has':['4 календарных'],'absent':['9 календарных']},
        {'id':'continued','source':docs[3]['id'],'q':'Что сделать при повреждении упаковки поставки Флюорит?','has':['сделайте фото','уведомите старшего смены'],'absent':['уведомите.']},
        {'id':'note','source':docs[5]['id'],'q':'Упаковка вскрыта. Сколько хранится товар Данбурит?','has':['2 календарных'],'absent':['8 календарных']},
        {'id':'note-base','source':docs[5]['id'],'q':'Упаковка не вскрыта. Сколько хранится товар Данбурит?','has':['8 календарных'],'absent':['2 календарных']},
        {'id':'missing','source':docs[1]['id'],'q':'Сколько хранится товар Амазонит?','status':'unavailable','absent':['14 календарных','6 календарных']},
        {'id':'wrong-continuation','source':docs[4]['id'],'q':'Что сделать при повреждении упаковки поставки Флюорит?','status':'unavailable','absent':['уведомите старшего смены']},
        {'id':'wrong-note','source':docs[6]['id'],'q':'Упаковка вскрыта. Сколько хранится товар Данбурит?','status':'unavailable','absent':['8 календарных','2 календарных']}
    ]
    holdout = [
        {'id':'borderless-paraphrase','source':docs[0]['id'],'q':'Как долго можно держать товар Кианит?','has':['14 календарных'],'absent':['6 календарных']},
        {'id':'photo-paraphrase','source':docs[2]['id'],'q':'Какой срок хранения у Альбита?','has':['9 календарных'],'absent':['4 календарных']},
        {'id':'continued-paraphrase','source':docs[3]['id'],'q':'У поставки Флюорит повреждена упаковка. Что делать?','has':['сделайте фото','уведомите старшего смены']}
    ]
    (OUT/'spec.json').write_text(json.dumps({'documents':docs,'cases':cases,'holdout':holdout},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pdfs':7,'pages':11,'cases':11,'holdout':3,'directory':str(OUT)}))


if __name__ == '__main__':
    main()
