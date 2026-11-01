"""Независимое чтение для составления эталона; модулей Ватэс здесь нет."""
import json
from html.parser import HTMLParser
from pathlib import Path
import pdfplumber

root = Path(__file__).parent
assert not (root/'FREEZE.json').exists(), 'Reference already frozen'
for source in json.loads((root/'sources.json').read_text(encoding='utf-8')):
    if not source['file'].endswith('.pdf'):
        continue
    with pdfplumber.open(root/'sources'/source['file']) as document:
        pages = [{'page': number, 'text': page.extract_text(x_tolerance=1, y_tolerance=2) or '',
                  'width': page.width, 'height': page.height} for number, page in enumerate(document.pages, 1)]
    (root/'reference'/f"{source['id']}.json").write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding='utf-8')
    (root/'reference'/f"{source['id']}.txt").write_text('\n\n'.join(f"=== PAGE {row['page']} ===\n"+row['text'] for row in pages), encoding='utf-8')
    print(source['id'], 'pages', len(pages), 'chars', sum(len(row['text']) for row in pages))

class TextReader(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript', 'svg'}: self.hidden += 1
        if tag in {'p', 'li', 'h1', 'h2', 'h3', 'div', 'br'} and not self.hidden: self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'svg'}: self.hidden = max(0, self.hidden - 1)
        if tag in {'p', 'li', 'h1', 'h2', 'h3'} and not self.hidden: self.parts.append('\n')
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)

reader=TextReader(); reader.feed((root/'sources/post.html').read_text(encoding='utf-8'))
(root/'reference/post.txt').write_text(''.join(reader.parts), encoding='utf-8')
