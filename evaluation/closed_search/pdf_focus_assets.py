"""Учебные PDF для проверки подсветки и переноса раздела, без данных компании."""
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'evaluation/closed_search/fixtures/pdf-focus'
RULE = 'Поставку Лиственит принимает координатор.'
SCAN_RULE = 'Поставку Бериллит принимает диспетчер.'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'spec.json').exists():
        raise ValueError('Frozen PDF focus corpus already exists')
    pdfmetrics.registerFont(TTFont('FocusArial', 'C:/Windows/Fonts/arial.ttf'))
    docs = []
    for key, pages in [
        ('base', ['Общие сведения.', RULE, 'Архивные заметки.']),
        ('moved', ['Общие сведения.', 'Архивные заметки.', RULE]),
        ('removed', ['Общие сведения.', 'Архивные заметки.', 'Приёмка описана в другом документе.']),
        ('duplicate', ['Общие сведения.', RULE, RULE]),
        ('scan', ['Общие сведения.', SCAN_RULE]),
    ]:
        target = OUT / (key + '.pdf')
        pdf = canvas.Canvas(str(target), pagesize=(595, 842), invariant=1)
        for number, text in enumerate(pages, 1):
            if key == 'scan':
                image = Image.new('RGB', (1785, 2526), 'white')
                draw = ImageDraw.Draw(image)
                font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 42)
                draw.text((150, 130), 'Учебный скан ВатэсAi', font=font, fill='black')
                draw.text((150, 230), 'Все правила вымышлены.', font=font, fill='black')
                draw.text((150, 520), text, font=font, fill='black')
                draw.text((150, 2350), 'Страница ' + str(number), font=font, fill='black')
                pdf.drawImage(ImageReader(image), 0, 0, 595, 842)
            else:
                pdf.setFont('FocusArial', 14)
                pdf.drawString(50, 790, 'Учебная проверка PDF ВатэсAi.')
                pdf.drawString(50, 760, 'Все правила вымышлены.')
                pdf.drawString(50, 660, text)
                pdf.drawString(50, 45, 'Страница ' + str(number))
            pdf.showPage()
        pdf.save()
        docs.append({'key': key, 'file': target.name, 'pages': len(pages), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    spec = {'documents': docs, 'question': 'Кто принимает поставку Лиственит?', 'rule': RULE,
            'scan_question': 'Кто принимает поставку Бериллит?', 'scan_rule': SCAN_RULE,
            'expected': {'base_page': 2, 'moved_page': 3, 'scan_page': 2, 'removed': 'not_found',
                         'old_revision': 'changed', 'duplicate_location': 'ambiguous'}}
    (OUT / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'files': len(docs), 'directory': str(OUT)}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('Fixture creation failed: ' + str(error)) from error
