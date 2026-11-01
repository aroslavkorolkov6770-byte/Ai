"""Замороженные растры: длинные переносы и сноски. Создаются до правок ядра."""
import hashlib
import json
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader
from .scan_assets import page
from .layout_assets import plain

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'evaluation/closed_search/fixtures/multipage'
ROW = 'Продолжение строки на следующей странице.'
TABLE = 'Продолжение таблицы на следующей странице.'


def table(title, rows, footer=None, header='Действия'):
    cells = [(0, 0, 1, 1, 'Поставка'), (0, 1, 1, 1, header)]
    cells.extend((r, c, 1, 1, text) for r, values in enumerate(rows, 1) for c, text in enumerate(values))
    return page(title, [600, 1020], [120] + [260] * len(rows), cells, note=footer)


def chain(wrong=False, missing=False, revised=False):
    pages = [table('Таблица 31. Приёмка', [('Родонит', 'При повреждении упаковки сделайте фото и')], ROW),
             table('Продолжение таблицы 31', [('Родонит (продолжение)', 'уведомите координатора, затем')], ROW),
             table('Продолжение таблицы 32' if wrong else 'Продолжение таблицы 31',
                   [('Родонит (продолжение)', 'запишите номер поставки в электронный журнал.' if revised else 'запишите номер поставки в бумажный журнал.')])]
    return pages[:2] if missing else pages


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'spec.json').exists():
        raise ValueError('Frozen corpus already exists')
    documents = []
    def write(key, name, pages, negative=False):
        target = OUT / name
        if target.exists():
            raise ValueError('Frozen PDF already exists')
        pdf = canvas.Canvas(str(target), pagesize=(600, 700), invariant=1)
        for image in pages:
            pdf.drawImage(ImageReader(image), 0, 0, 600, 700)
            pdf.showPage()
        pdf.save()
        assert not any(p.extract_text() for p in PdfReader(target).pages)
        documents.append({'id':'lab_multi_' + key, 'name':name, 'pages':len(pages),
                          'negative':negative, 'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    write('chain', 'Три_страницы_Родонит.pdf', chain())
    write('revision', 'Новая_редакция_Родонит.pdf', chain(revised=True), True)
    write('wrong', 'Чужое_продолжение.pdf', chain(wrong=True), True)
    write('missing', 'Без_последней_страницы.pdf', chain(missing=True), True)
    write('rows', 'Четыре_страницы_сроков.pdf', [
        table(('Таблица 41. Хранение' if i == 0 else 'Продолжение таблицы 41'),
              [(name, days + ' календарных дней')], TABLE if i < 3 else None, 'Срок хранения')
        for i, (name, days) in enumerate([('Гелиотроп', '11'), ('Пренит', '16'), ('Пироп', '21'), ('Рутил', '26')])])
    base = table('Таблица 51. Хранение', [('Авантюрин', '12 календарных дней [1] [2]')], header='Срок хранения')
    first = plain('Сноски к таблице 51', ['[1]: Исключение: если упаковка вскрыта,', 'товар хранится 3 календарных дня.'])
    second = plain('Сноски к таблице 51', ['[2]: Исключение: если этикетка повреждена,', 'товар хранится 2 календарных дня.'])
    write('notes', 'Две_сноски_на_трёх_страницах.pdf', [base, first, second])
    write('wrong_notes', 'Неверная_область_сноски.pdf', [base, first,
        plain('Сноски к таблице 52', ['[2]: Исключение: если этикетка повреждена,', 'товар хранится 2 календарных дня.'])], True)
    def case(key, source, q, has=(), absent=(), status='answer'):
        return {'id':key,'source':'lab_multi_'+source,'q':q,'has':list(has),'absent':list(absent),'status':status}
    question = 'Что сделать при повреждении упаковки поставки Родонит?'
    cases = [case('chain','chain',question,['сделайте фото','уведомите координатора','запишите номер поставки в бумажный журнал']),
             case('revision','revision',question,['электронный журнал'],['бумажный журнал']),
             case('wrong','wrong',question,absent=['сделайте фото'],status='unavailable'),
             case('missing','missing',question,absent=['уведомите координатора'],status='unavailable')]
    for key, name, days in [('first','Гелиотроп','11'),('second','Пренит','16'),('third','Пироп','21'),('fourth','Рутил','26')]:
        cases.append(case(key,'rows','Сколько хранится поставка '+name+'?',[days+' календарных']))
    cases += [case('note-base','notes','Упаковка не вскрыта, этикетка не повреждена. Сколько хранится поставка Авантюрин?',['12 календарных'],['3 календарных','2 календарных']),
              case('note-first','notes','Упаковка вскрыта, этикетка не повреждена. Сколько хранится поставка Авантюрин?',['3 календарных'],['12 календарных','2 календарных']),
              case('note-second','notes','Упаковка не вскрыта, этикетка повреждена. Сколько хранится поставка Авантюрин?',['2 календарных'],['12 календарных','3 календарных']),
              case('note-missing','wrong_notes','Упаковка не вскрыта, этикетка не повреждена. Сколько хранится поставка Авантюрин?',status='unavailable')]
    holdout = [case('chain-paraphrase','chain','У поставки Родонит повреждена упаковка. Что делать?',['уведомите координатора','бумажный журнал']),
               case('last-paraphrase','rows','Как долго можно держать поставку Рутил?',['26 календарных']),
               case('both-exceptions','notes','Упаковка вскрыта, этикетка повреждена. Сколько хранится поставка Авантюрин?',status='conflict')]
    (OUT/'spec.json').write_text(json.dumps({'documents':documents,'cases':cases,'holdout':holdout},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pdfs':len(documents),'pages':sum(d['pages'] for d in documents),'cases':len(cases),'holdout':len(holdout)}))


if __name__ == '__main__':
    main()
