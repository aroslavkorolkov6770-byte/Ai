"""Рендер всех новых PDF через Poppler и контактные листы для ручной проверки."""
import json
import argparse
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[2]
ASSETS=ROOT/'evaluation/closed_search/fixtures/layout'
OUT=ROOT/'.runtime/closed-search-lab-20261028/layout-rendered'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--fixture',choices=['layout','quality','multipage'],default='layout')
    args=parser.parse_args()
    assets=ROOT/'evaluation/closed_search/fixtures'/args.fixture
    output=ROOT/'.runtime/closed-search-lab-20261028'/(args.fixture+'-rendered')
    output.mkdir(parents=True,exist_ok=True)
    renderer=shutil.which('pdftoppm')
    if not renderer: raise RuntimeError('Poppler is required')
    spec=json.loads((assets/'spec.json').read_text(encoding='utf-8'))
    rendered=[]
    for document in spec['documents']:
        target=output/document['id']
        subprocess.run([renderer,'-r','100','-png',str(assets/document['name']),str(target)],
                       check=True,capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
        for path in sorted(output.glob(document['id']+'-*.png')):
            rendered.append((document['name'],path))
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',16)
    sheets=[]
    for start in range(0,len(rendered),4):
        sheet=Image.new('RGB',(1240,1580),'#dddddd')
        draw=ImageDraw.Draw(sheet)
        for offset,(name,path) in enumerate(rendered[start:start+4]):
            image=Image.open(path).convert('RGB')
            image.thumbnail((600,740))
            x,y=(offset%2)*620,(offset//2)*790
            sheet.paste(image,(x+10,y+40))
            draw.text((x+10,y+8),name,font=font,fill='black')
        filename=output/f'contact-{start//4+1}.png'
        sheet.save(filename);sheets.append(str(filename))
    print(json.dumps({'pages':len(rendered),'sheets':sheets},ensure_ascii=False))


if __name__=='__main__':main()
