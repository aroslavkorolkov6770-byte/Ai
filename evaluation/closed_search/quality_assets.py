"""Независимые ухудшенные растры. Эталон задаётся по исходному правилу, не OCR."""
import io
import json
import hashlib
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader
from .scan_assets import page

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'evaluation/closed_search/fixtures/quality'
PREVIEWS = ROOT/'.runtime/closed-search-lab-20261028/quality-previews'


def shadow(image):
    values = np.asarray(image).astype(np.float32)
    gradient = np.linspace(.28, 1, image.width).reshape(1, -1, 1)
    return Image.fromarray(np.uint8(np.clip(values*gradient, 0, 255)))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    if any(OUT.glob('*.pdf')) or (OUT/'spec.json').exists():
        raise RuntimeError('Frozen quality fixtures already exist')
    table = page('Учебные сроки хранения', [650, 970], [120, 150, 150],
                 [(0,0,1,1,'Товар'), (0,1,1,1,'Срок хранения'),
                  (1,0,1,1,'Селенит'), (1,1,1,1,'13 календарных дней'),
                  (2,0,1,1,'Лабрадорит'), (2,1,1,1,'18 календарных дней')])
    plain = Image.new('RGB', (1800, 2100), 'white')
    draw = ImageDraw.Draw(plain)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 36)
    draw.text((90,70), 'Учебный документ. Все правила вымышлены.', font=font, fill='black')
    draw.text((90,300), 'Не храните товар Иолит рядом с водой.', font=font, fill='black')
    compressed = io.BytesIO()
    table.resize((900,1050),Image.Resampling.LANCZOS).save(compressed,format='JPEG',quality=35)
    compressed = Image.open(io.BytesIO(compressed.getvalue())).convert('RGB').resize(table.size)
    local_blur = table.copy()
    # Ухудшена именно область двух цифр, остальные буквы остаются чёткими.
    box = (760, 400, 825, 458)
    local_blur.paste(table.crop(box).filter(ImageFilter.GaussianBlur(4.5)),box)
    partial_negation = plain.copy()
    draw = ImageDraw.Draw(partial_negation)
    draw.rectangle((90,315,138,330),fill=(170,170,170))
    cut = 90 + round(ImageDraw.Draw(plain).textlength('Не ',font=font))
    variants = [
        ('clean','Чёткие_сроки.pdf',table,'answer','Сколько хранится товар Селенит?',['13 календарных'],['18 календарных']),
        ('soft','Лёгкое_размытие.pdf',table.filter(ImageFilter.GaussianBlur(1.1)),'answer','Сколько хранится товар Селенит?',['13 календарных'],['18 календарных']),
        ('shadow','Тень_на_таблице.pdf',shadow(table),'answer','Сколько хранится товар Лабрадорит?',['18 календарных'],['13 календарных']),
        ('jpeg','Сжатая_таблица.pdf',compressed,'answer','Сколько хранится товар Селенит?',['13 календарных'],['18 календарных']),
        ('blur','Сильное_размытие.pdf',table.filter(ImageFilter.GaussianBlur(7)),'unavailable','Сколько хранится товар Селенит?',[],['13 календарных','18 календарных']),
        ('crop','Обрезанный_столбец.pdf',table.crop((0,0,1030,2100)),'unavailable','Сколько хранится товар Селенит?',[],['13 календарных','18 календарных']),
        ('number','Нечёткие_цифры.pdf',local_blur,'exact_or_refuse','Сколько хранится товар Селенит?',['13 календарных'],['3 календарных','18 календарных']),
        ('negation','Чёткий_запрет.pdf',plain,'answer','Можно ли хранить товар Иолит рядом с водой?',['Не храните'],[]),
        ('negation_shadow','Тень_на_запрете.pdf',shadow(plain),'answer','Можно ли хранить товар Иолит рядом с водой?',['Не храните'],[]),
        ('negation_crop','Обрезанное_отрицание.pdf',plain.crop((cut,0,1800,2100)),'unavailable','Можно ли хранить товар Иолит рядом с водой?',[],[]),
        ('negation_spot','Повреждённое_отрицание.pdf',partial_negation,'exact_or_refuse','Можно ли хранить товар Иолит рядом с водой?',['Не храните'],[]),
    ]
    documents,cases=[],[]
    for key,name,image,status,question,has,absent in variants:
        image.save(PREVIEWS/(key+'.png'))
        target=OUT/name
        pdf=canvas.Canvas(str(target),pagesize=(image.width/3,image.height/3),invariant=1)
        pdf.drawImage(ImageReader(image),0,0,image.width/3,image.height/3)
        pdf.showPage();pdf.save()
        if any((p.extract_text() or '').strip() for p in PdfReader(target).pages):
            raise AssertionError('Fixture is not raster-only')
        identifier='lab_quality_'+key
        documents.append({'id':identifier,'name':name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                          'degradation':key,'negative':status!='answer'})
        cases.append({'id':key,'source':identifier,'q':question,'status':status,'has':has,'absent':absent})
    # Запрещённая подстрока "3 календарных" входит в корректное "13";
    # числовые подмены проверяются отдельным набором разрешённых чисел.
    for case in cases:
        if case['id']=='number':
            case['absent']=['18 календарных']
            case['numbers']=['13']
    spec={'documents':documents,'cases':cases,'holdout':[
        {'id':'shadow-paraphrase','source':'lab_quality_shadow','q':'Как долго хранить Лабрадорит?','status':'answer','has':['18 календарных'],'absent':['13 календарных']},
        {'id':'crop-paraphrase','source':'lab_quality_crop','q':'Какой срок хранения у Селенита?','status':'unavailable'},
        {'id':'negation-paraphrase','source':'lab_quality_negation_shadow','q':'Товар Иолит можно держать около воды?','status':'answer','has':['Не храните']} ]}
    (OUT/'spec.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pdfs':len(documents),'cases':len(cases),'holdout':3,'directory':str(OUT)},ensure_ascii=False))


if __name__=='__main__':main()
