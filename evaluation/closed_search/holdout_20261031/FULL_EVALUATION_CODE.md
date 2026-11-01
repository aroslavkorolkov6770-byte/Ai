# Полные листинги экзамена 5 документов / 100 вопросов

Код ядра не менялся. Ниже каждый добавленный скрипт приведён полностью, без пропусков. Скрипты подготовки и первого прогона защищены от перезаписи артефактов. Повторять их поверх этого набора нельзя; для нового экзамена нужен отдельный каталог.

## prepare.cjs

```javascript
/* Подготовка новых источников. Не вызывает ИИ, поисковый индекс или обучение. */
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const sources = [
 {id:'bp30',title:'БП30Б-Д3-х — краткое руководство',type:'technical_datasheet',file:'bp30.pdf',url:'https://owen.ru/uploads/25/kr_bp30b-d3-x_m01__033_a4.pdf'},
 {id:'tplink',title:'TL-SG1005D/TL-SG1008D — руководство по установке',type:'network_installation',file:'tplink.pdf',url:'https://static.tp-link.com/resources/document/TL_SG1005D_V6_QIG_RU_7106505336_1.pdf'},
 {id:'rsl',title:'Правила пользования Российской государственной библиотекой — редакция 10.04.2023',type:'institution_rules',file:'rsl.pdf',url:'https://www.rsl.ru/uploads/29cc879c-e3ad-46f6-999a-6b06a33061e2.pdf'},
 {id:'beko',title:'Beko WKM 4215 W — руководство пользователя',type:'appliance_manual',file:'beko.pdf',url:'https://www.beko.com/content/dam/beko-global-multichannel-mc-aem/beko-global-multichannel-mc-aemProductCatalog/product-documents/8912843200-WKM-4215-W/ro-RO-8912843200-MDM2-USER-MANUAL-FILE-ro-RO.pdf'},
 {id:'post',title:'Почта России — требования к содержимому и упаковке отправлений',type:'service_rules_html',file:'post.html',url:'https://info.pochta.ru/support/post-rules/content-package-rules'}
];
const hash = data => crypto.createHash('sha256').update(data).digest('hex');
async function main(){
 if(fs.existsSync(path.join(__dirname,'FREEZE.json')))throw new Error('Already frozen');
 const results = await Promise.allSettled(sources.map(async source => {
  const file = path.join(__dirname,'sources',source.file);
  if(fs.existsSync(file))throw new Error('Source already exists: '+source.id);
  const response = await fetch(source.url,{signal:AbortSignal.timeout(60000)});
  if(!response.ok)throw new Error(source.id+' HTTP '+response.status);
  const bytes=Buffer.from(await response.arrayBuffer());
  if(source.file.endsWith('.pdf')&&!bytes.subarray(0,5).equals(Buffer.from('%PDF-')))throw new Error('Not PDF: '+source.id);
  fs.writeFileSync(file,bytes,{flag:'wx'});
  return {...source,downloaded_at:new Date().toISOString(),final_url:response.url,bytes:bytes.length,sha256:hash(bytes),content_type:response.headers.get('content-type')};
 }));
 const complete=[];
 for(let i=0;i<results.length;i++){const result=results[i];if(result.status==='fulfilled'){complete.push(result.value);console.log(JSON.stringify(result.value));}else console.error(sources[i].id,result.reason.message);}
 fs.writeFileSync(path.join(__dirname,'sources.json'),JSON.stringify(complete,null,2));
 if(complete.length!==5)process.exitCode=1;
}
main().catch(error=>{console.error(error.stack);process.exitCode=1;});
```

## read_reference.py

```python
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
```

## author.cjs

```javascript
/* Независимые эталоны по оригиналам. Скрипт не импортирует поисковый движок. */
const fs = require('node:fs');
const path = require('node:path');
const groups = {
 bp30: [
  ['Какой номинальный выходной ток у БП30Б-Д3-12?', '2,50 А.', '1, таблица технических характеристик'],
  ['Адаптер БП30Б-Д3-24: какой у него номинальный ток на выходе?', '1,25 А.', '1, таблица'],
  ['Одинакова ли номинальная выходная мощность вариантов БП30Б-Д3-12 и БП30Б-Д3-24? Назови её.', 'Да, у обоих 30 Вт.', '1, общая ячейка таблицы'],
  ['На сколько процентов можно подстроить выходное напряжение БП30Б-Д3-х?', 'В пределах ±8%.', '1, таблица'],
  ['В каких пределах переменного напряжения на входе работает БП30Б-Д3-х?', 'От 85 до 264 В переменного тока.', '1, диапазон, а не номинал 110/230'],
  ['Какая частота питающей сети подходит БП30Б-Д3-х?', '47–63 Гц.', '1, таблица'],
  ['Какой диапазон постоянного напряжения допускается на входе БП30Б-Д3-х?', '110–370 В постоянного тока.', '1, таблица'],
  ['Ниже какого значения не должен быть КПД БП30Б-Д3-х?', 'КПД не менее 85%.', '1, таблица'],
  ['При какой температуре воздуха можно эксплуатировать БП30Б-Д3-х?', 'От −20 до +50 °C.', '1, условия эксплуатации'],
  ['При какой температуре можно хранить и перевозить БП30Б-Д3-х?', 'От −40 до +50 °C.', '1, условия хранения и транспортирования'],
  ['Соединяю два БП30Б-Д3-х параллельно. Какими должны быть провода от выходов до точки соединения?', 'Одинаковой длины и одинакового сечения.', '1, примечание о параллельном включении'],
  ['У БП30Б-Д3-х провод до нагрузки длиной два метра, входных конденсаторов у нагрузки нет. Какой конденсатор рекомендуют добавить и как выбрать его напряжение?', 'Параллельно нагрузке рекомендуют керамический конденсатор не менее 0,1 мкФ; его напряжение не менее 1,5 выходного напряжения блока.', '2, сноска под схемой'],
  ['Сколько максимально весит БП30Б-Д3-х?', 'Не более 0,25 кг.', '1, таблица'],
  ['А срок службы?', '10 лет.', '1, таблица; продолжение диалога о БП30Б-Д3-х'],
  ['А гарантия?', '2 года.', '1, таблица; продолжение того же диалога'],
  ['Теперь про монтаж: можно оставить подключённые устройства под питанием?', 'Нет. При монтаже питание отключают у блока и у всех подключённых к нему устройств.', '1, монтаж и подключение'],
  ['Сколько БП30Б-Д3-24 прямо сейчас осталось на складе нашего магазина?', 'В документах нет текущих складских остатков.', 'Полный документ: индивидуальных складских данных нет'],
  ['Назови серийный номер моего экземпляра БП30Б-Д3-12.', 'В документе нет серийного номера экземпляра пользователя.', 'Полный документ'],
  ['Для БП30Б-Д3-х какое напряжение?', 'Уточнить: входное или выходное напряжение и какая модификация блока интересует.', 'Недостаёт вида напряжения и модификации'],
  ['Для БП30Б-Д3-х какой ток?', 'Уточнить, какой ток и какая модификация интересуют: выходной, потребления или пусковой.', 'Несколько разных токов в таблице']
 ],
 tplink: [
  ['На TL-SG1005D горит индикатор питания. Что это означает?', 'Питание включено.', '1, состояние индикатора питания'],
  ['На TL-SG1008D Link/Activity светится постоянно и не мигает. Что это значит?', 'Соединение установлено, но активность отсутствует.', '1, индикатор Link/Activity'],
  ['На TL-SG1005D моргает Link/Activity. Это какая работа порта?', 'Идёт передача или приём данных.', '1, индикатор Link/Activity'],
  ['На TL-SG1008D индикатор Link/Activity погас. Как это расшифровать?', 'Соединение отсутствует.', '1, индикатор Link/Activity'],
  ['Подключил компьютер к TL-SG1005D, но Link/Activity не загорелся. Что проверить по инструкции?', 'Кабель надёжно подключён с обоих концов; подключённое устройство включено и работает нормально; кабель не длиннее 100 м.', '2, вопрос 2 в FAQ'],
  ['Какова максимальная длина сетевого кабеля к TL-SG1008D?', '100 м.', '2, FAQ и характеристики кабеля'],
  ['В каком диапазоне температур может работать TL-SG1005D?', 'От 0 до 40 °C.', '2, условия окружающей среды'],
  ['Какая температура допускается при хранении TL-SG1008D?', 'От −40 до 70 °C.', '2, условия окружающей среды'],
  ['При какой влажности можно эксплуатировать TL-SG1005D?', '10–90%, без образования конденсата.', '2, рабочая влажность'],
  ['Какая влажность допустима при хранении TL-SG1008D?', '5–90%, без образования конденсата.', '2, влажность хранения'],
  ['Какой метод передачи кадров использует TL-SG1005D?', 'Store-and-Forward, хранение и передача.', '2, основные характеристики'],
  ['Какую скорость передачи для Gigabit Ethernet в полнодуплексном режиме приводит руководство TL-SG1008D?', '2000 Мбит/с.', '2, скорость передачи данных; не скорость отдельного направления'],
  ['Какие скорости согласуют RJ45-порты TL-SG1005D автоматически?', '10, 100 и 1000 Мбит/с.', '2, интерфейсы'],
  ['А сколько кадров в секунду фильтруется и передаётся на гигабитном порту?', '1 488 000 пакетов/кадров в секунду на порт.', '2, фильтрация/передача пакетов'],
  ['А для 100 мегабит?', '148 800 пакетов/кадров в секунду на порт.', '2, та же строка для 100 Мбит/с'],
  ['Отложим скорость: можно поставить его в сыром подвале?', 'Нет, инструкция запрещает использование устройства во влажной среде, в частности в сыром подвале.', '2, меры предосторожности'],
  ['Сколько рублей стоит новый TL-SG1005D сегодня в нашем магазине?', 'В руководстве нет сегодняшней цены магазина.', 'Полный документ'],
  ['Какой MAC-адрес у моего экземпляра TL-SG1008D?', 'В руководстве нет MAC-адреса экземпляра пользователя.', 'Полный документ'],
  ['Какой режим выбрать?', 'Уточнить, у какого устройства и для какой задачи выбирается режим.', 'Новый чат, устройство и задача неизвестны'],
  ['Сколько там?', 'Уточнить, что именно нужно посчитать и о каком объекте речь.', 'Новый чат, нет объекта']
 ],
 rsl: [
  ['С какого возраста можно стать пользователем РГБ по этим правилам и какой документ нужен?', 'С 14 лет, при предъявлении документа, удостоверяющего личность; это относится к гражданам РФ и иностранным гражданам.', '3, пункт 2.1'],
  ['Обязательно ли лично идти в РГБ для записи, или можно записаться через интернет?', 'Можно записаться дистанционно через официальный сайт РГБ.', '4, пункт 2.2.2'],
  ['Нужно ли платить за оформление читательского билета при очной записи в РГБ?', 'Нет, билет при очной записи оформляется бесплатно.', '4, пункт 2.2.1'],
  ['Как выглядит билет, который оформляют при очной записи в РГБ?', 'Пластиковая карта с фотографией читателя.', '4, пункт 2.2.1'],
  ['Кто оплачивает дубликат потерянного читательского билета РГБ?', 'Читатель возмещает стоимость изготовления дубликата.', '4, пункт 2.6'],
  ['На каком основании организация становится коллективным пользователем РГБ?', 'На основании соответствующего договора.', '4, пункт 2.5'],
  ['Можно ли ребёнку без удостоверения личности попасть на культурное мероприятие РГБ одному?', 'Нет; несовершеннолетнему без документа нужно сопровождение лица не моложе 18 лет.', '4, пункт 3.1.3'],
  ['Разрешены ли звуковые сигналы у мобильных устройств в читальных залах РГБ?', 'Нет, мобильные устройства используют без звуковых сигналов.', '6, пункт 3.1.16'],
  ['Где посетитель РГБ может зарядить телефон?', 'В свободных электрических розетках в читательской зоне библиотеки.', '6, пункт 3.1.17'],
  ['Можно ли принести музыкальный инструмент в РГБ и играть на нём где угодно?', 'Инструмент в футляре/чехле разрешён в отделе нотных изданий и звукозаписей; играть можно только в специально отведённых местах.', '6, пункт 3.1.18'],
  ['В книге РГБ стоит штамп «Не подлежит копированию». Можно её сфотографировать?', 'Нет, фотографирование таких документов не допускается.', '6, пункт 3.1.20'],
  ['Можно сфотографировать в РГБ книгу, изданную в 1820 году?', 'Нет, фотографирование документов, изданных до 1830 года, не допускается.', '6, пункт 3.1.20'],
  ['На какой срок выдаётся читательский билет РГБ?', 'На 5 лет.', '4, пункт 2.3'],
  ['А если я его потеряю?', 'Можно получить дубликат с возмещением стоимости его изготовления.', '4, пункт 2.6'],
  ['А пользоваться библиотекой удалённо можно?', 'Да, допускается дистанционное пользование библиотекой.', '4, пункт 3.1.1'],
  ['Теперь о съёмке: можно сфотографировать книгу с корешком высотой 5 см?', 'Нет: фотографирование документов с корешком высотой более 4 см не допускается.', '7, продолжение пункта 3.1.20'],
  ['Какой номер у моего читательского билета РГБ?', 'В правилах нет номера личного билета пользователя.', 'Полный документ'],
  ['Сколько именно рублей стоит изготовление дубликата билета РГБ?', 'В данном документе нет точной стоимости изготовления дубликата.', '4, пункт 2.6: указана обязанность возместить, не сумма'],
  ['Мне это разрешено?', 'Уточнить, какое действие имеется в виду и в какой ситуации.', 'Новый чат, нет действия'],
  ['Сколько это стоит?', 'Уточнить, какая услуга или товар интересует.', 'Новый чат, нет предмета']
 ],
 beko: [
  ['Какое напряжение и частота сети нужны чайнику Beko WKM 4215 W?', '220–240 В переменного тока, 50–60 Гц.', '63, технические данные'],
  ['Какая потребляемая мощность у Beko WKM 4215 W?', '1850–2200 Вт.', '63, технические данные'],
  ['Можно пользоваться чайником Beko WKM 4215 W на улице или в ванной?', 'Нет, ни на открытом воздухе, ни в ванной использовать нельзя.', '64, безопасность'],
  ['Подходит ли Beko WKM 4215 W для профессионального использования в общепите?', 'Нет, руководство предусматривает бытовое, а не профессиональное использование.', '64, назначение'],
  ['У Beko WKM 4215 W повреждён шнур питания. Можно продолжать пользоваться и куда обращаться?', 'Не пользоваться; обратиться в авторизованный сервисный центр.', '64, безопасность'],
  ['Разрешает ли инструкция включать Beko WKM 4215 W через удлинитель?', 'Нет.', '64, безопасность'],
  ['Можно наливать воду в Beko WKM 4215 W, пока он стоит на подставке?', 'Нет, при наполнении чайник должен быть снят с подставки.', '65 и 67'],
  ['У Beko WKM 4215 W во время кипячения открыта крышка. Будет ли он автоматически выключаться?', 'По инструкции при открытой крышке автоматического выключения не будет; во время кипячения крышка должна быть закрыта.', '67, раздел 2.1'],
  ['Как вручную остановить нагрев Beko WKM 4215 W?', 'Перевести выключатель вверх.', '67, раздел 2.1'],
  ['Нужно ли охлаждать Beko WKM 4215 W перед повторным нагревом?', 'Да, дать чайнику остыть перед каждым нагревом.', '67, раздел 2.1'],
  ['Какую воду рекомендуют для Beko WKM 4215 W, чтобы меньше образовывалась накипь?', 'Смягчённую или фильтрованную.', '67, полезные советы'],
  ['Когда и сколько уксуса или лимонной кислоты добавлять при удалении накипи в Beko WKM 4215 W?', '2 стакана белого уксуса или 1 столовую ложку лимонной кислоты; добавляют после кипячения воды и отключения чайника от сети.', '68, раздел 3.2, шаги 1–3'],
  ['Чем запрещено чистить Beko WKM 4215 W?', 'Бензином, растворителями, абразивными чистящими средствами, металлическими предметами и жёсткими щётками.', '68, раздел 3.1'],
  ['А погружать его в воду можно?', 'Нет; прибор, подставку и шнур не погружают в воду или другие жидкости.', '65 и 68, безопасность и очистка'],
  ['А где и в каком состоянии хранить до следующего сезона?', 'Отключённым от сети, остывшим и полностью сухим, в сухом прохладном месте, недоступном детям.', '68, раздел 3.3'],
  ['Теперь о перевозке: как его упаковать?', 'Перевозить в оригинальной упаковке; не класть тяжёлые грузы на прибор или упаковку.', '68, раздел 3.4'],
  ['Назови серийный номер моего Beko WKM 4215 W.', 'В руководстве нет серийного номера экземпляра пользователя.', 'Полный документ'],
  ['Какая сегодня цена Beko WKM 4215 W в нашем магазине?', 'В руководстве нет актуальной цены магазина.', 'Полный документ'],
  ['Сколько добавить?', 'Уточнить, какое вещество, куда и для какой цели нужно добавить.', 'Новый чат, нет вещества и процедуры'],
  ['Можно это использовать?', 'Уточнить, что именно и для какой цели пользователь хочет использовать.', 'Новый чат, нет объекта и цели']
 ],
 post: [
  ['Как по правилам Почты России упаковать бумажные материалы весом полтора килограмма?', 'Допустимы конверт, конверт с воздушной прослойкой, пластиковый пакет или плотная бумага с перевязкой/заклеиванием; категория — бумажная продукция и мелкие вещи до 2 кг.', 'Статья, общие требования, первый абзац'],
  ['Какие максимальные размеры гофрокартонной коробки указаны в требованиях Почты России к упаковке?', '51 × 28 × 20 см. Это размеры коробки, а не самого вложения.', 'Статья, общие требования'],
  ['Какое место под адресный ярлык надо оставить на упаковке Почты России?', 'Не менее 10,5 × 14,8 см.', 'Статья, общие требования'],
  ['Можно второй раз отправить посылку в старой коробке Почты России?', 'Нет, вторично использовать коробки Почты России нельзя.', 'Статья, общие требования'],
  ['Можно защитить штрихкод отправления Почты России, заклеив его прозрачным скотчем?', 'Нет, штрихкод нельзя закрывать скотчем, плёнкой или иным материалом.', 'Статья, общие требования'],
  ['В коробке Почты России вещи болтаются. Что сделать с пустотами?', 'Заполнить пустоты, чтобы содержимое не перемещалось; например, пенопластом, опилками, стружкой или ватой.', 'Статья, общие требования'],
  ['Хочу отправить Почтой России флаконы с жидкостью в пластиковом пакете. Так можно?', 'Нет. Для способных протекать жидкостей нужна твёрдая упаковка с защитным/амортизирующим материалом; пластиковый пакет недопустим.', 'Статья, разные виды вложений: жидкости'],
  ['Как упаковать красящий порошок для пересылки Почтой России?', 'Обязательно в герметичную ёмкость и затем в твёрдую упаковку.', 'Статья, порошки'],
  ['Какую упаковку Почта России требует для саженцев?', 'Корзину, обшитую тканью, либо ящик с вентиляционными отверстиями; дно закрыть полиэтиленовой плёнкой или клеёнкой.', 'Статья, саженцы'],
  ['Нужно ли делать вентиляцию в коробке с фруктами для Почты России?', 'Да, нужны вентиляционные отверстия; для твёрдых свежих фруктов допускаются также вертикальные просветы. Прокладка разрешена, но здесь не названа обязательной.', 'Статья, свежие фрукты и овощи'],
  ['Как подготовить автомобильные шины к пересылке Почтой России?', 'Шины должны быть чистыми и упакованными в мешки.', 'Статья, автопокрышки'],
  ['Предмет цельный, небьющийся, без острых выступов. Требует ли Почта России дополнительную упаковку?', 'Для такого предмета допускается отправка без дополнительной упаковки.', 'Статья, исключение об отправке без дополнительной упаковки'],
  ['Какая ткань и какие швы нужны для тканевой упаковки отправления Почтой России?', 'Прочная светлая однотонная ткань: цельный кусок либо однородные куски, сшитые внутренним швом.', 'Статья, тканевая упаковка'],
  ['А адрес можно пришить?', 'Нет, адрес надо написать на ткани; пришивать адрес запрещено.', 'Статья, тканевая упаковка'],
  ['Теперь отправляю хрупкий измерительный прибор. Как его защитить?', 'Твёрдая упаковка и защитный/амортизирующий материал, исключающий перемещение и повреждение.', 'Статья, хрупкие предметы и точные приборы'],
  ['А если с описью, можно заклеить ещё дома?', 'Нет: сначала предъявить содержимое сотруднику для сверки с описью, затем упаковать.', 'Статья, общие требования, опись вложения'],
  ['Сколько рублей сегодня стоит отправить Почтой России посылку 3 кг из Москвы в Казань?', 'В этой статье нет тарифа для такого отправления.', 'Полная статья о содержимом и упаковке'],
  ['Где сейчас моя последняя посылка Почты России?', 'В материалах нет данных отслеживания личной посылки; нужен трек-номер и соответствующий сервис, положение не выдумывать.', 'Полная статья'],
  ['Что выбрать?', 'Уточнить, между какими вариантами и для какой задачи выбирает пользователь.', 'Новый чат, нет предмета выбора'],
  ['Назови допустимый вес для моего отправления Почтой России.', 'Уточнить вид отправления и направление; единого предела для всех отправлений в статье нет.', 'В статье различаются виды отправлений, параметры пользователя отсутствуют']
 ]
};

function main() {
 if (fs.existsSync(path.join(__dirname,'FREEZE.json'))) throw new Error('Экзамен уже зафиксирован');
 const questions = [], gold = [];
 for (const [source, rows] of Object.entries(groups)) {
  if (rows.length !== 20) throw new Error('Нужно 20 вопросов: '+source);
  rows.forEach(([question, answer, reference], position) => {
   const id = 'q'+String(questions.length+1).padStart(3,'0');
   const dialogue = position >= 12 && position < 16;
   const chat = dialogue ? 'dialogue-'+source : 'single-'+id;
   questions.push({id, chat, question});
   gold.push({id, source, action: position < 16 ? 'answer' : position < 18 ? 'insufficient' : 'clarify', dialogue, answer, reference});
  });
 }
 if (questions.length !== 100 || new Set(questions.map(q=>q.id)).size !== 100) throw new Error('Некорректное число вопросов');
 for (const [name, value] of [['questions.json',questions],['gold.json',gold]]) fs.writeFileSync(path.join(__dirname,name),JSON.stringify(value,null,2)+'\n',{flag:'wx'});
 const sources=JSON.parse(fs.readFileSync(path.join(__dirname,'sources.json'),'utf8'));
 const md=['# Независимый экзамен: 100 вопросов и эталоны','',
  'Эталоны составлены до первого вызова Ватэс по этим документам. Номера страниц — физические страницы PDF. Проверяются именно скачанные редакции. В каждом блоке вопросы 13–16 идут одним диалогом; все остальные — в отдельных новых чатах.',
  '', 'Полностью правильный ответ должен передавать все существенные факты и ограничения эталона, не добавлять противоречащих или неподтверждённых утверждений. Точная формулировка не требуется. При отсутствии данных нельзя выдумывать число или личный реквизит. В неоднозначном вопросе нужно запросить недостающую вводную.', ''];
 for (const source of sources) {
  md.push('## '+source.title,'','Источник: ['+source.title+']('+source.url+')','', 'SHA-256: `'+source.sha256+'`','');
  for (const item of gold.filter(item=>item.source===source.id)) {
   const q=questions.find(q=>q.id===item.id);
   md.push('### '+q.id+' — '+item.action+(item.dialogue?' — последовательный диалог':''),'','Вопрос: '+q.question,'','Эталон: '+item.answer,'','Основание: '+item.reference+'.','');
  }
 }
 fs.writeFileSync(path.join(__dirname,'QUESTIONS_AND_GOLD.md'),md.join('\n'),{flag:'wx'});
 console.log(JSON.stringify({questions:questions.length,answer:80,insufficient:10,clarify:10,dialogue_turns:20}));
}
try { main(); } catch (error) { console.error(error.stack); process.exitCode=1; }
```

## freeze.cjs

```javascript
/* Фиксируем входы до обращения к ИИ. Контрольные суммы проверяем повторно в конце. */
const fs=require('node:fs'), path=require('node:path'), crypto=require('node:crypto');
const root=path.resolve(__dirname,'../../..');
const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function walk(directory){return fs.readdirSync(directory,{withFileTypes:true}).flatMap(entry=>entry.isDirectory()?walk(path.join(directory,entry.name)):[path.join(directory,entry.name)]);}
function main(){
 const questions=JSON.parse(fs.readFileSync(path.join(__dirname,'questions.json'),'utf8'));
 const gold=JSON.parse(fs.readFileSync(path.join(__dirname,'gold.json'),'utf8'));
 const sources=JSON.parse(fs.readFileSync(path.join(__dirname,'sources.json'),'utf8'));
 if(questions.length!==100||gold.length!==100||sources.length!==5)throw new Error('Wrong count');
 for(const q of questions){if(Object.keys(q).sort().join(',')!=='chat,id,question'||!gold.some(g=>g.id===q.id))throw new Error('Invalid question');}
 for(const source of sources){if(hash(path.join(__dirname,'sources',source.file))!==source.sha256)throw new Error('Source changed');}
 const inputs=['sources.json','questions.json','gold.json','QUESTIONS_AND_GOLD.md','METHOD.md','author.cjs','prepare.cjs','read_reference.py','freeze.cjs','run.py',...sources.map(s=>'sources/'+s.file)];
 const files=Object.fromEntries(inputs.map(name=>[name,hash(path.join(__dirname,name))]));
 const runtime=Object.fromEntries(walk(path.join(root,'ai_core')).filter(file=>file.endsWith('.py')).sort().map(file=>[path.relative(root,file).replaceAll('\\','/'),hash(file)]));
 const freeze={frozen_at:new Date().toISOString(),questions:100,documents:5,files,runtime};
 fs.writeFileSync(path.join(__dirname,'FREEZE.json'),JSON.stringify(freeze,null,2)+'\n',{flag:'wx'});
 console.log(JSON.stringify({frozen_at:freeze.frozen_at,freeze_sha256:hash(path.join(__dirname,'FREEZE.json')),runtime_files:Object.keys(runtime).length}));
}
try{main();}catch(error){console.error(error.stack);process.exitCode=1;}
```

## run.py

```python
"""Первый прогон. Этот процесс не открывает эталоны или ручное извлечение."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / 'results'


def now():
    return datetime.now(timezone.utc).isoformat()


def write_new(name, value):
    with (OUT / name).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def main():
    freeze = json.loads((HERE / 'FREEZE.json').read_text(encoding='utf-8'))
    # Проверяем движок и разрешённые входы. Gold даже для хеширования здесь не читаем.
    for name, expected in freeze['runtime'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('runtime_changed:' + name)
    for name in ['questions.json', 'sources.json', 'run.py']:
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != freeze['files'][name]:
            raise RuntimeError('input_changed:' + name)
    OUT.mkdir(exist_ok=True)
    write_new('START.json', {'started_at': now(), 'frozen_at': freeze['frozen_at'],
                            'freeze_sha256': hashlib.sha256((HERE / 'FREEZE.json').read_bytes()).hexdigest(),
                            'python': sys.version, 'pid': os.getpid()})
    sys.path.insert(0, str(ROOT))
    from ai_core import site_documents
    from ai_core.site_search import Principal
    from ai_core.site_search_runtime import DurableSearch, engine_version

    # Меняем только место производных данных, не алгоритмы и не рабочий кэш.
    site_documents.CACHE = OUT / 'document-cache'
    sources = json.loads((HERE / 'sources.json').read_text(encoding='utf-8'))
    questions = json.loads((HERE / 'questions.json').read_text(encoding='utf-8'))
    records, extraction = [], []
    for source in sources:
        data = (HERE / 'sources' / source['file']).read_bytes()
        if hashlib.sha256(data).hexdigest() != source['sha256']:
            raise RuntimeError('source_changed:' + source['id'])
        started = time.perf_counter()
        details = {}

        def progress(value):
            details.update(value)
            print(json.dumps({'extracting': source['id'], 'progress': value}, ensure_ascii=False), flush=True)

        error = None
        try:
            text, complete = site_documents.extract_document(
                'data:' + source['content_type'].split(';')[0] + ';base64,' + base64.b64encode(data).decode('ascii'),
                source['file'], progress=progress)
        except Exception:
            # Не теряем неудобный документ и его вопросы из знаменателя.
            error = traceback.format_exc()
            text, complete = '', False
        (OUT / (source['id'] + '-extracted.txt')).write_text(text, encoding='utf-8')
        extraction.append({'id': source['id'], 'seconds': time.perf_counter()-started,
                           'characters': len(text), 'complete': complete, 'progress': details, 'error': error})
        print(json.dumps({'extracted': extraction[-1]}, ensure_ascii=False), flush=True)
        records.append({'id': 'document:' + source['id'], 'revision': source['sha256'],
                        'title': source['title'], 'kind': 'document', 'authority': 'official',
                        'url': 'http://holdout.invalid/doc/' + source['id'],
                        'access': {'scope': 'restricted', 'users': ['exam-reader']},
                        'complete': complete, 'sections': [{'heading': source['title'], 'text': text}]})
    write_new('extraction.json', extraction)
    write_new('records.json', records)
    engine = DurableSearch(['http://holdout.invalid'], OUT / 'exam.sqlite', extraction_version=site_documents.PARSER_VERSION)
    started = time.perf_counter()
    if not engine.publish(records, engine.begin('holdout-20261031-first')):
        raise RuntimeError('publish_failed')
    write_new('index.json', {'seconds': time.perf_counter()-started, 'state': engine.index.state,
                            'passages': len(engine.index.passages), 'engine_version': engine_version(),
                            'parser_version': site_documents.PARSER_VERSION})
    revisions = {source['id']: source['revision'] for source in records}
    with (OUT / 'baseline.jsonl').open('x', encoding='utf-8') as handle:
        for question in questions:
            started = time.perf_counter()
            try:
                result = engine.query(question['question'], Principal('exam-reader', 'staff'), question['chat'], None, revisions)
            except Exception:
                result = {'status': 'error', 'text': '', 'evidence': [], 'links': [], 'error': traceback.format_exc()}
            row = {**question, 'at': now(), 'milliseconds': round((time.perf_counter()-started)*1000, 3), 'result': result}
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
            print(json.dumps({'id': question['id'], 'status': result.get('status'), 'ms': row['milliseconds']}), flush=True)
    write_new('FINISH.json', {'finished_at': now(), 'answers': len(questions),
                             'baseline_sha256': hashlib.sha256((OUT / 'baseline.jsonl').read_bytes()).hexdigest()})


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
```

## postcheck.py

```python
"""Отдельные проверки после экзамена; исходная база и 100 ответов не меняются."""
from __future__ import annotations
import copy
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'results'
sys.path.insert(0, str(HERE.parents[2]))
from ai_core.site_documents import PARSER_VERSION
from ai_core.site_search import Principal
from ai_core.site_search_runtime import DurableSearch


def main():
    if not (OUT / 'FINISH.json').is_file():
        raise RuntimeError('First finish all 100 questions')
    db = OUT / 'postcheck.sqlite'
    if db.exists():
        raise RuntimeError('Postcheck already started; preserve its database')
    original = json.loads((OUT / 'records.json').read_text(encoding='utf-8'))
    records = copy.deepcopy(original)
    # Только технический адрес просмотра. Текст и права исходных документов прежние.
    for record in records:
        record['fragment_url'] = 'http://holdout.invalid/closed-search-fragment'
    engine = DurableSearch(['http://holdout.invalid'], db, extraction_version=PARSER_VERSION)
    engine.publish(records, engine.begin('postcheck-initial'))
    revisions = {item['id']: item['revision'] for item in records}
    actor, outsider = Principal('exam-reader', 'staff'), Principal('exam-outsider', 'staff')
    checks = []

    def check(name, passed, **detail):
        checks.append({'name': name, 'passed': bool(passed), **detail})

    statistics = []
    for source in records:
        parts = [p for p in engine.index.passages if p.source['id'] == source['id']]
        statistics.append({'id': source['id'], 'passages': len(parts),
                           'largest_passage': max((len(p.text) for p in parts), default=0),
                           'structure_unavailable': engine.index.sources[source['id']].get('structure_unavailable'),
                           'longer_than_2000': sum(len(p.text)>2000 for p in parts)})
    questions = json.loads((HERE / 'questions.json').read_text(encoding='utf-8'))
    for position in [0, 20, 40, 60, 80]:
        q = questions[position]
        answer = engine.query(q['question'], outsider, q['chat'], None, revisions)
        check('outsider-' + q['id'], answer['status'] != 'answer' and not answer.get('evidence') and not answer.get('links'), result=answer)

    # Создаём реальный успешный контекст доступного документа и проверяем scope.
    answer = engine.query(questions[80]['question'], actor, 'isolation-shared', None, revisions)
    owner_scope = (actor.user_id, actor.role, (), 'isolation-shared')
    other_scope = (outsider.user_id, outsider.role, (), 'isolation-shared')
    check('owner-context-created', owner_scope in engine.index.sessions, status=answer['status'])
    owner_context = copy.deepcopy(engine.index.sessions.get(owner_scope))
    answer = engine.query('А адрес можно пришить?', outsider, 'isolation-shared', None, revisions)
    check('same-chat-other-user', other_scope not in engine.index.sessions and engine.index.sessions.get(owner_scope)==owner_context
          and not answer.get('evidence') and not answer.get('links') and answer['status']!='answer', result=answer)
    fresh_scope = (actor.user_id, actor.role, (), 'isolation-fresh')
    check('fresh-chat-has-no-inherited-context', fresh_scope not in engine.index.sessions)
    # Новый объект восстанавливает SQLite без OCR; качество реплик не переоцениваем.
    restored = DurableSearch(['http://holdout.invalid'], db, extraction_version=PARSER_VERSION)
    check('restore-index', restored.restored and restored.index.state=='ready' and len(restored.index.passages)==len(engine.index.passages),
          restored=restored.restored, passages=len(restored.index.passages), milliseconds=restored.restore_ms)
    restored_context = restored.index.sessions.get(owner_scope)
    check('restore-owner-context-only', restored_context is not None and other_scope not in restored.index.sessions
          and fresh_scope not in restored.index.sessions
          and restored_context.get('ids')==owner_context.get('ids'),
          restored_ids=restored_context.get('ids') if restored_context else [])
    target = next(item for item in restored.index.fragment_targets.values() if item['id']=='document:post')
    args = (target['id'], target['revision'], target['fragment'])
    check('owner-fragment-opens', restored.fragment(*args, actor, revisions)['status']=='ready')
    hidden = restored.fragment(*args, outsider, revisions)
    check('outsider-fragment-blocked', hidden['status']=='not_found' and 'text' not in hidden, result=hidden)
    new_revisions = {**revisions, 'document:post':'postcheck-revision-2'}
    changed = restored.fragment(*args, actor, new_revisions)
    check('live-revision-blocks-old-link', changed['status']=='changed' and 'text' not in changed, result=changed)
    changed_records = copy.deepcopy(records)
    changed_records[-1]['revision'] = new_revisions['document:post']
    # Смена только метки редакции проверяет закрытие старого адреса, не перенос разделов.
    restored.publish(changed_records, restored.begin('postcheck-revision-2'))
    changed = restored.fragment(*args, actor, new_revisions)
    check('published-revision-blocks-old-link', changed['status']=='changed' and 'text' not in changed, result=changed)
    remaining = [item for item in changed_records if item['id']!='document:post']
    remaining_revisions = {item['id']:item['revision'] for item in remaining}
    restored.publish(remaining, restored.begin('postcheck-deleted'))
    deleted = restored.fragment(*args, actor, remaining_revisions)
    check('deleted-source-blocks-old-link', deleted['status']=='not_found' and 'text' not in deleted, result=deleted)
    check('deleted-source-prunes-context', all('document:post' not in c.get('ids',[]) for c in restored.index.sessions.values()))
    report = {'at':datetime.now(timezone.utc).isoformat(), 'passed':sum(c['passed'] for c in checks),
              'total':len(checks), 'checks':checks, 'passage_statistics':statistics}
    with (OUT / 'postcheck.json').open('x',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
```

## grade.cjs

```javascript
/* Ручные смысловые решения после чтения всех ответов. Не обучает и не вызывает ИИ. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const base=__dirname, out=path.join(base,'results');
const read=name=>JSON.parse(fs.readFileSync(path.join(base,name),'utf8'));
const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const decisions=new Map();
function mark(numbers,grade,reason,issue=null,presentation=[]){
 for(const number of numbers){const id='q'+String(number).padStart(3,'0');if(decisions.has(id))throw new Error('Duplicate '+id);decisions.set(id,{id,grade,reason,issue,presentation});}
}
const range=(first,last)=>Array.from({length:last-first+1},(_,i)=>first+i);
mark(range(1,13),'abstained','Нужные сведения есть в оригинале, но вместо ответа — сообщение о непроверенных условиях.','conditional_guard');
mark([14,15],'abstained','Предмет назван в первой реплике этой цепочки; запрошено повторное указание материала.','followup');
mark([16],'wrong','На вопрос о монтаже блока питания выдана инструкция по чайнику.','wrong_source',['excess']);
mark([17,18],'wrong','Не сообщено об отсутствии личного/складского факта: выдана техническая неопределённость условий.','wrong_action');
mark([19,20],'wrong','Не запрошен вид параметра или модификация; вместо уточнения — непроверенные условия.','wrong_action');
mark([21],'abstained','Уточнение об эксплуатации дома не нужно для расшифровки горящего индикатора.','false_condition');
mark([22,24],'abstained','Модель и индикатор уже названы, но движок просит указать материал.','unnecessary_clarify');
mark([23],'abstained','Значение мигающего индикатора есть на первой странице, но не найдено.','retrieval_miss');
mark([25],'abstained','Три действия из FAQ не выданы; сообщение о непроверенных условиях.','conditional_guard');
mark([...range(26,34),36],'abstained','Параметр или правило однозначны в оригинале; движок объявил ложное противоречие.','false_conflict');
mark([35],'abstained','Скорость 100 Мбит/с — продолжение вопроса о фильтрации кадров, но контекст потерян.','followup');
mark([37],'correct','Правильно сообщено об отсутствии цены в доступных материалах.');
mark([38],'wrong','Личного MAC-адреса нет, но вместо отсутствия данных объявлено противоречие.','false_conflict');
mark([39],'wrong','Нет вводных для выбора режима; нужен вопрос пользователю, а не просто отсутствие данных.','wrong_action');
mark([40],'correct','Запрошен недостающий предмет вопроса.');
mark([41],'correct','Сохранены возраст 14 лет, удостоверение личности и круг пользователей.',null,['excess','service_numbering']);
mark([42],'wrong','Описана очная запись, дистанционная запись из п.2.2.2 не упомянута.','retrieval_precision',['excess']);
mark([43],'wrong','Общие платные/бесплатные услуги не отвечают, бесплатно ли именно оформление билета.','retrieval_precision',['excess']);
mark([44],'wrong','Форма билета названа верно, но добавлено «Нарушать целостность документа…» без управляющего «Пользователям запрещается» из п.3.3. Это потеря смысла запрета, а не только многословие.','lost_prohibition',['excess','service_numbering']);
mark([45],'correct','Указано возмещение стоимости дубликата пользователем.',null,['excess','service_numbering']);
mark([46],'correct','Договор указан как основание регистрации коллективного пользователя.',null,['excess','service_numbering']);
mark([47],'wrong','Общее право посещать мероприятия выдано без обязательного сопровождения ребёнка взрослым.','missing_condition',['excess']);
mark([48],'correct','Сохранено требование выключенных звуковых сигналов.',null,['excess','service_numbering']);
mark([49],'correct','Названы свободные розетки в читательских помещениях.',null,['excess','service_numbering']);
mark([50],'correct','Сохранены музыкальный отдел, чехол и специально отведённое место для игры.',null,['excess','service_numbering']);
mark([51],'wrong','Запрет фотографирования отмеченной книги есть, но добавлено «Осуществлять несанкционированное копирование…» без общего запрета п.3.3. Ответ в целом нельзя считать корректным.','lost_prohibition',['excess','service_numbering']);
mark([52],'abstained','Не применено сравнение 1820 < 1830, хотя правило есть в документе.','comparison');
mark([53],'wrong','Вместо срока 5 лет выдано утверждение о рукописном фонде.','retrieval_precision',['service_numbering']);
mark([54,55],'abstained','Библиотека и билет установлены предыдущими репликами, но контекст не использован.','followup');
mark([56],'correct','Приведён запрет съёмки при корешке более 4 см, применимый к 5 см.',null,['excess','service_numbering']);
mark([57],'wrong','Не признано отсутствие личного номера билета; вместо этого — оформление билета и розетки.','wrong_action',['excess']);
mark([58],'partial','Сумма не выдумана и обязанность возмещения указана, но не сказано, что точной суммы в документе нет.','wrong_action',['excess','service_numbering']);
mark([59,60],'correct','Запрошена недостающая вводная для вопроса.');
mark([61],'wrong','Напряжение и частота не выданы; вместо них обложка, схемы, безопасность и утилизация.','retrieval_precision',['excess','other_language','diagram_noise','unrequested_url']);
mark([62],'correct','Правильная мощность 1850–2200 Вт явно присутствует. Подача засорена, но сам ответ на параметр однозначен.',null,['excess','other_language','diagram_noise','unrequested_url']);
mark([63,64,66,67],'correct','Нужный однозначный запрет прямо присутствует в ответе и подтверждён оригиналом; отмечены серьёзные дефекты подачи.',null,['excess','other_language','diagram_noise','unrequested_url']);
mark([65],'wrong','Нет ни запрета пользоваться при повреждённом шнуре, ни направления в авторизованный сервис.','retrieval_precision',['excess','other_language','diagram_noise','unrequested_url']);
mark([68],'correct','Названы закрытая крышка и отсутствие автоотключения при открытой.',null,['excess','service_numbering']);
mark([69,70,71],'correct','Искомое действие или рекомендация явно присутствуют и совпадают с источником.',null,['excess','other_language','diagram_noise','unrequested_url']);
mark([72],'partial','Количество и кипячёная вода указаны, но пропущено предварительное отключение от сети — существенная часть вопроса «когда».','missing_condition',['excess','other_language','diagram_noise','unrequested_url']);
mark([73],'wrong','Список запрещённых средств отсутствует; вместо него обложка и многоязычные подписи.','retrieval_precision',['excess','other_language','diagram_noise','unrequested_url']);
mark([74],'correct','Сохранён контекст чайника и явный запрет погружения прибора в воду.',null,['excess','service_numbering']);
mark([75],'abstained','Условия хранения есть в п.3.3, но объявлено противоречие.','false_conflict');
mark([76],'wrong','Вместо упаковки чайника выдано меню сайта Почты России.','wrong_source',['navigation']);
mark([77],'partial','Есть полезное указание, где искать номер на изделии, но отсутствует признание, что сам личный номер неизвестен.','wrong_action',['excess','other_language','diagram_noise','unrequested_url']);
mark([78],'wrong','Вместо отсутствия текущей цены — адрес, телефон и другой язык.','wrong_action',['other_language','unrequested_url']);
mark([79],'wrong','В новом чате без предмета и цели самостоятельно выбран рецепт удаления накипи.','wrong_action',['service_numbering']);
mark([80],'correct','Запрошен предмет использования.');
mark([81],'correct','Названы допустимые варианты упаковки для бумажных материалов до 2 кг.');
mark([82],'correct','Размер 51 × 28 × 20 см явно относится к гофрокоробке.',null,['excess']);
mark([83],'correct','Правильно указан минимальный размер места для ярлыка.',null,['excess']);
mark([84],'correct','Прямо указан запрет повторного использования коробки Почты России.',null,['excess']);
mark([85],'correct','Прямо запрещено перекрывать штрихкод плёнкой или скотчем.',null,['excess']);
mark([86],'abstained','Инструкция заполнить свободное пространство есть, но разговорная формулировка не распознана.','retrieval_miss');
mark([87],'abstained','Вместо правил для жидкости задан посторонний вопрос о наклеивании бланка.','false_condition');
mark([88],'wrong','Правило для пушнины подменило правило для красящего порошка; герметичность не названа.','retrieval_precision',['excess']);
mark([89],'wrong','Выдан огромный набор правил для разных вложений, но правило для саженцев и подкладка дна отсутствуют.','retrieval_precision',['excess','service_numbering']);
mark([90],'partial','Вентиляция названа только для твёрдых сортов, общий вопрос о фруктах покрыт не полностью.','missing_condition',['excess']);
mark([91],'correct','Сохранены чистота шин и мешки.',null,['excess']);
mark([92],'correct','Сохранено исключение: цельный небьющийся предмет без острых выступов допускается без дополнительной упаковки.',null,['excess']);
mark([93],'wrong','Текстильный товар перепутан с тканевой упаковкой; требования к цвету, однородности и шву не выданы.','retrieval_precision',['excess']);
mark([94],'abstained','Продолжение о тканевой упаковке не распознано, хотя прямой запрет пришивать адрес есть.','followup');
mark([95],'abstained','Правило о твёрдой упаковке и прокладках есть; вместо него — непроверенные условия.','conditional_guard');
mark([96],'abstained','Опись и упаковка относятся к установленной теме; вместо порядка действий — уточнение материала.','followup');
mark([97],'correct','Тариф не выдуман, сообщено об отсутствии информации.');
mark([98],'wrong','Местоположение личной посылки неизвестно, но выданы правила коробки и штрихкода.','wrong_action',['excess']);
mark([99],'wrong','В новом чате предмет выбора неизвестен, но самостоятельно выбрана тема почтовых отправлений.','wrong_action');
mark([100],'partial','Сообщена зависимость от типа отправления, но не запрошены конкретный тип и направление.','wrong_action');

function wilson(k,n){
 const z=1.959963984540054,p=k/n,d=1+z*z/n;
 const middle=(p+z*z/(2*n))/d,half=z*Math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d;
 return [middle-half,middle+half].map(v=>Number((100*v).toFixed(2)));
}
function metric(rows){const correct=rows.filter(r=>r.grade==='correct').length;return {correct,total:rows.length,percent:100*correct/rows.length,wilson95: wilson(correct,rows.length)};}
function counts(rows,field){const result={};for(const r of rows){const v=r[field]||'none';result[v]=(result[v]||0)+1;}return result;}
function main(){
 const freeze=read('FREEZE.json'),finish=read('results/FINISH.json'),start=read('results/START.json');
 const root=path.resolve(base,'../../..');
 const errors=[];
 for(const [name,expected] of Object.entries(freeze.files))if(hash(path.join(base,name))!==expected)errors.push(name);
 for(const [name,expected] of Object.entries(freeze.runtime))if(hash(path.join(root,name))!==expected)errors.push(name);
 if(hash(path.join(base,'FREEZE.json'))!==start.freeze_sha256)errors.push('FREEZE.json');
 if(hash(path.join(out,'baseline.jsonl'))!==finish.baseline_sha256)errors.push('baseline.jsonl');
 if(errors.length)throw new Error('Changed after freeze: '+errors.join(','));
 const questions=read('questions.json'),gold=read('gold.json');
 const raw=fs.readFileSync(path.join(out,'baseline.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
 if(raw.length!==100||decisions.size!==100||new Set(raw.map(r=>r.id)).size!==100)throw new Error('Must grade 100 distinct answers');
 for(let i=0;i<100;i++)if(raw[i].id!==questions[i].id||raw[i].question!==questions[i].question||raw[i].chat!==questions[i].chat)throw new Error('Question sequence changed');
 if(Date.parse(start.started_at)<=Date.parse(freeze.frozen_at)||raw.some(r=>Date.parse(r.at)<Date.parse(start.started_at)))throw new Error('Wrong timing');
 const rows=raw.map(r=>({...r,...gold.find(g=>g.id===r.id),...decisions.get(r.id)}));
 const latency=raw.map(r=>r.milliseconds).sort((a,b)=>a-b);
 const sources=read('sources.json');
 const summary={graded_at:new Date().toISOString(),integrity:'all frozen files and 77 runtime files unchanged',
  first_run_sha256:finish.baseline_sha256,overall:metric(rows),answerable:metric(rows.filter(r=>r.action==='answer')),
  insufficient:metric(rows.filter(r=>r.action==='insufficient')),clarify:metric(rows.filter(r=>r.action==='clarify')),
  unknown_or_clarify:metric(rows.filter(r=>r.action!=='answer')),dialogue_turns:metric(rows.filter(r=>r.dialogue)),
  followup_only:metric(rows.filter(r=>r.dialogue&&((Number(r.id.slice(1))-1)%20)>12)),
  by_source:Object.fromEntries(sources.map(s=>[s.id,metric(rows.filter(r=>r.source===s.id))])),
  grades:counts(rows,'grade'),issues:counts(rows.filter(r=>r.grade!=='correct'),'issue'),
  statuses:counts(raw.map(r=>r.result),'status'),
  presentation_flagged:rows.filter(r=>r.presentation.length).length,
  correct_with_presentation_issues:rows.filter(r=>r.grade==='correct'&&r.presentation.length).length,
  latency_ms:{min:latency[0],median:(latency[49]+latency[50])/2,p95:latency[94],max:latency[99],total:latency.reduce((a,b)=>a+b,0)},
  answer_latency_ms:raw.filter(r=>r.result.status==='answer').map(r=>r.milliseconds),
  extraction:read('results/extraction.json'),index:read('results/index.json'),postcheck:read('results/postcheck.json')};
 const md=['# Результаты первого экзамена: все 100 решений','',
  'Все вопросы и эталоны зафиксированы до первого вызова. Ниже — неизменённые ответы Ватэс и ручная смысловая оценка. correct оценивает фактическое содержание, а не удобство подачи: дефекты длины, языка и служебного текста отмечены отдельно. Оценщик — агент, не независимая внешняя комиссия.',
  '', 'Фиксация: '+freeze.frozen_at+'. Начало: '+start.started_at+'. Конец: '+finish.finished_at+'.',
  '', 'SHA-256 журнала первого прогона: `'+finish.baseline_sha256+'`.', '',
  'Итого: '+summary.overall.correct+'/100. Отвечаемые: '+summary.answerable.correct+'/80. Отсутствие данных: '+summary.insufficient.correct+'/10. Уточнения: '+summary.clarify.correct+'/10.',''];
 for(const r of rows){
  md.push('## '+r.id+' — '+r.grade,'','Вопрос: '+r.question,'','Эталон: '+r.answer,'','Основание: '+r.reference+'.',
   '', 'Ответ Ватэс (полностью):','',...r.result.text.split('\n').map(line=>'> '+line),'',
   'Оценка: '+r.reason,'','Замечания к подаче: '+(r.presentation.join(', ')||'нет')+'.',
   '', 'Статус движка: '+r.result.status+'. Время: '+r.milliseconds+' мс. Источники: '+(r.result.evidence.map(e=>e.id).join(', ')||'нет')+'.','');
 }
 for(const [name,value] of [['judgments.json',rows.map(r=>({id:r.id,grade:r.grade,reason:r.reason,issue:r.issue,presentation:r.presentation}))],['summary.json',summary]])fs.writeFileSync(path.join(out,name),JSON.stringify(value,null,2)+'\n',{flag:'wx'});
 fs.writeFileSync(path.join(base,'ALL_100_RESULTS.md'),md.join('\n'),{flag:'wx'});
 console.log(JSON.stringify({...summary,extraction:undefined,postcheck:{passed:summary.postcheck.passed,total:summary.postcheck.total},answer_latency_ms:undefined},null,2));
}
try{main();}catch(error){console.error(error.stack);process.exitCode=1;}
```

## report.cjs

```javascript
/* Собирает отчёт из сохранённых измерений, не запускает повторный экзамен. */
const fs=require('node:fs'),path=require('node:path');
const base=__dirname,root=path.resolve(base,'../../..');
const read=name=>JSON.parse(fs.readFileSync(path.join(base,name),'utf8'));
function main(){
 const s=read('results/summary.json'),sources=read('sources.json'),freeze=read('FREEZE.json');
 const m=value=>`${value.correct}/${value.total} (${value.percent.toFixed(1)}%; описательный интервал 95% ${value.wilson95.join('–')}%)`;
 const answered=s.answer_latency_ms.slice().sort((a,b)=>a-b);
 const extractionSeconds=s.extraction.reduce((n,r)=>n+r.seconds,0);
 const md=[
 '# ВатэсAi — первый экзамен на пяти новых документах, 31.10.2026','',
 '**Итог: 30/100 корректных по содержанию решений. Критерии качества основного плана не достигнуты.** Это завершённый независимый первый прогон, а не результат исправлений на заранее известных вопросах. Даже среди 30 зачтённых ответов у 23 есть лишний текст, другой язык или служебные обозначения. Фактический зачёт не означает, что эти ответы уже пригодны для удобного чата.','',
 '80 вопросов имели ответ в оригиналах: зачтены 24. В 10 случаях данных не было: корректно обработаны 2. В 10 случаях требовалась вводная: корректно обработаны 4. Остальные оценки: 40 отказов на отвечаемые вопросы, 25 неверных ответов/действий, 5 частичных. У движка было 48 статусов answer, но это не 48 правильных ответов. Исключений выполнения и пустых ответов не было.','',
 '## Что проверялось','',
 'Использован существующий закрытый поиск из C:/Users/KROLL/Desktop/Ai: штатное извлечение документов, DurableSearch и изолированная SQLite. Это ядро, которое обслуживает сайт. В данном прогоне не проверялись браузер, HTTP-авторизация, голос, внешняя сеть и обучение весов. Настройки Alice и действующая LMS не изменялись.','',
 'Все пять оригиналов индексировались одновременно. Движок сам выбирал источник: selected=None во всех 100 вызовах. Ему не передавались эталоны, номера страниц, подготовленные выдержки или подсказки после ошибки. В каждой последовательной цепочке сохранялся только его собственный контекст.','',
 'Четыре PDF содержат суммарно 100 физических страниц. Beko передан целиком: все 81 страница с другими языками, обложкой и схемами. Пятый источник — HTML официальной статьи.','',
 '| Новый источник | Тип | Результат из 20 |','|---|---|---|',
 ...sources.map(source=>`| [${source.title}](${source.url}) | ${source.file.endsWith('.pdf')?'PDF':'HTML'} | ${s.by_source[source.id].correct}/20 |`),'',
 'Редакция РГБ датирована 10.04.2023, инструкция TP-Link — REV3.0.2 с указанием 2015 года. Они проверяются как предоставленные редакции; текущая юридическая или техническая актуальность не утверждается. Адреса и контрольные суммы оригиналов: [sources.json](evaluation/closed_search/holdout_20261031/sources.json).','',
 '## Фиксация до начала','',
 `FREEZE.json создан ${freeze.frozen_at}, до импорта поискового ядра и первого запроса. Начало первого прогона: ${read('results/START.json').started_at}; завершение: ${read('results/FINISH.json').finished_at}.`,
 '', 'Хеш фиксации: `'+read('results/START.json').freeze_sha256+'`.',
 '', 'Хеш журнала первых ответов: `'+s.first_run_sha256+'`.',
 '', 'После оценки повторно проверены оригиналы, вопросы, эталоны, методика, исходные скрипты и 77 файлов ядра: изменений нет. gold.json ни разу не открывается процессом run.py. Автоматическая проверка подтвердила 100 уникальных вопросов в исходном порядке, без замены текста или чатов. Даты и хеши доказывают порядок локальных операций, но не являются внешним нотариальным свидетельством.','',
 '## Метрики','',
 '| Срез | Корректно |','|---|---|',
 `| Все вопросы | ${m(s.overall)} |`,
 `| Ответ есть в документе | ${m(s.answerable)} |`,
 `| Данных нет | ${m(s.insufficient)} |`,
 `| Нужно уточнение | ${m(s.clarify)} |`,
 `| Все реплики пяти диалогов | ${m(s.dialogue_turns)} |`,
 `| Только продолжения после первой реплики | ${m(s.followup_only)} |`,'',
 'Это 5 документов и связанные реплики, а не 100 независимых случайных примеров. Интервалы Уилсона приведены как описательный ориентир. Такой набор не доказывает результат на всём возможном документообороте. Все эталоны и оценки составил агент; независимой человеческой комиссии здесь не было. Пользователь может проверить любое решение по полному журналу.','',
 'Подача отмечена как проблемная в 45 ответах. Из 24 содержательно правильных ответов на отвечаемые вопросы только один не получил замечания к подаче; остальные 23 содержат лишний текст, другие языки, подписи схем или номера пунктов. Это отдельная редакторская оценка, а не заранее заданный автоматический показатель длины.','',
 '## Что конкретно сломано','',
 '1. **Границы фрагментов.** После штатного извлечения каждый PDF стал одним поисковым фрагментом. В Beko он содержит 138 955 символов, в РГБ — 28 673. Это подтверждено диагностикой индекса. В site_search_conditions.py функция paragraphs объединяет весь вход при guarded(text), а ветка формирования фрагментов в site_search.py не делит такой блок по обычному лимиту. При отсутствии распознанных заголовков одно условие влияет на большой участок документа. Это механизм, который необходимо исправлять и отдельно проверять, а не компенсировать новыми заученными вопросами.','',
 '| Источник | Фрагментов | Максимальная длина | Неуверенная структура |','|---|---:|---:|---|',
 ...s.postcheck.passage_statistics.map(p=>`| ${p.id} | ${p.passages} | ${p.largest_passage} | ${p.structure_unavailable?'да':'нет'} |`),'',
 '2. **Таблицы и ложные конфликты.** БП30 помечен как структурно неуверенный, и все его 16 отвечаемых вопросов провалены. У TP-Link тоже 0/16 по отвечаемым: значения таблицы смешаны с названиями строк и другими разделами. Всего в экзамене 12 статусов conflict. Наличие complete=true у всех пяти документов означает завершение чтения, а не правильное восстановление таблиц.','',
 '3. **Потеря отрицания и обязательных условий.** В q044 и q051 движок присоединил пункты из раздела «Пользователям запрещается», опустив этот заголовок. В q047 потерял требование сопровождения несовершеннолетнего. В q072 сохранил количество средства от накипи, но пропустил отключение от сети. Эти ответы не зачтены полностью, даже когда часть нужных слов присутствует.','',
 '4. **Короткие продолжения.** Всего 2/15 правильных ответов на продолжения. В q016 после блока питания ответ пришёл из инструкции чайника, в q076 вместо перевозки чайника — меню почтового сайта. Неудачный поиск не должен уничтожать установленный пользователем предмет разговора.','',
 '5. **Недостаточность вопроса и выбор действия.** В новом чате q079 «Сколько добавить?» привёл к рецепту удаления накипи, хотя предмет не назван. Это не доказательство утечки памяти: во время первого прогона чаты были разными, а отдельная проверка изоляции прошла. Нужен надёжный выбор между ответом, уточнением и сообщением об отсутствии данных.','',
 '6. **Точность выбора и очистка ответа.** Вместо серийного номера или цены выдаются соседние инструкции, адреса, подписи схем и другой язык. На вопросы про саженцы и тканевую упаковку возвращаются правила для других предметов. Лексического совпадения недостаточно для признания фрагмента доказательством.','',
 '## Время и устойчивость','',
 `Извлечение пяти документов: ${extractionSeconds.toFixed(2)} с суммарно; сборка индекса: ${s.index.seconds.toFixed(2)} с. Последовательные запросы: медиана ${s.latency_ms.median.toFixed(2)} мс, p95 ${s.latency_ms.p95.toFixed(2)} мс, максимум ${s.latency_ms.max.toFixed(2)} мс. Сумма времени 100 запросов: ${(s.latency_ms.total/1000).toFixed(2)} с.`,
 '', `Среди 48 статусов answer медиана ${(answered[23]/2+answered[24]/2).toFixed(2)} мс, p95 ${answered[Math.ceil(answered.length*.95)-1].toFixed(2)} мс. Быстрые отказы не выдаются за быстрый полезный ответ. Это последовательные вызовы подготовленного локального ядра, не нагрузка и не задержка пользовательского интерфейса.`,
 '', 'Извлечение выдало предупреждения pypdf об отсутствии fontTools для части CFF-шрифтов. В процессе экзамена зависимости не устанавливались и не менялись; это отдельный пункт диагностики, не доказанная единственная причина ошибок.',
 '', '**Дополнительные функциональные проверки: 16/16.** Пять запросов от профиля без доступа не раскрыли источники; одинаковый chat_id другого сотрудника не получил чужой контекст; новый чат не наследовал контекст; новый объект восстановил SQLite и только допустимую память. Ссылка открывается владельцу и закрыта постороннему; старая ссылка закрыта после смены редакции и удаления документа; удалённый источник убран из памяти.',
 '', 'Редакционная проверка меняла номер редакции и удаляла источник в отдельном тестовом индексе. Перенос содержимого разделов, PDF-подсветка и браузерное открытие здесь не проверялись. 16/16 не является пентестом или доказательством полной безопасности системы.',
 '', '## Решение и следующий шаг','',
 'Экзамен проведён полностью; порог этапа 2 (90% на отвечаемые и 90% на отсутствие данных/уточнения) не достигнут. Обучение весов и замена действующей модели этим результатом не разрешаются автоматически. Теперь этот набор известен разработчику и используется только как регрессия.',
 '', 'Следующая работа в рамках этапа 3: сначала восстановить границы разделов и таблиц, сохранив связь с управляющим запретом, условием и сноской. Разделять языки и отделять навигацию/подписи схем от доказательств. Затем исправить сохранение предмета диалога и выбор ответ/уточнение/нет данных. Для сравнений вариантов использовать отдельные новые документы, не эти 100 вопросов. Проверить старые ТРМ1 и UEP на регрессии. После улучшений провести новый заранее зафиксированный экзамен; его результат хранить отдельно от этого первого прогона.',
 '', '## Проверяемые артефакты','',
 '- [Вопросы и эталоны, зафиксированные до запуска](evaluation/closed_search/holdout_20261031/QUESTIONS_AND_GOLD.md).',
 '- [Все 100 ответов и объяснение каждой оценки](evaluation/closed_search/holdout_20261031/ALL_100_RESULTS.md).',
 '- [Методика](evaluation/closed_search/holdout_20261031/METHOD.md) и [фиксация SHA-256](evaluation/closed_search/holdout_20261031/FREEZE.json).',
 '- [Неизменённый журнал первого прогона](evaluation/closed_search/holdout_20261031/results/baseline.jsonl), [измерения](evaluation/closed_search/holdout_20261031/results/summary.json), [доступ и редакции](evaluation/closed_search/holdout_20261031/results/postcheck.json).',
 '- [Полные листинги всех добавленных скриптов](evaluation/closed_search/holdout_20261031/FULL_EVALUATION_CODE.md).',
 '', 'Все файлы находятся локально в C:/Users/KROLL/Desktop/Ai. GitHub, удалённые серверы и рабочие данные не изменялись.'
 ];
 fs.writeFileSync(path.join(root,'DOCUMENT_HOLDOUT_100_REPORT_2026_10_31.md'),md.join('\n')+'\n',{flag:'wx'});
 const names=['prepare.cjs','read_reference.py','author.cjs','freeze.cjs','run.py','postcheck.py','grade.cjs','report.cjs'];
 const full=['# Полные листинги экзамена 5 документов / 100 вопросов','','Код ядра не менялся. Ниже каждый добавленный скрипт приведён полностью, без пропусков. Скрипты подготовки и первого прогона защищены от перезаписи артефактов. Повторять их поверх этого набора нельзя; для нового экзамена нужен отдельный каталог.',''];
 for(const name of names)full.push('## '+name,'','```'+(name.endsWith('.py')?'python':'javascript'),fs.readFileSync(path.join(base,name),'utf8').trimEnd(),'```','');
 fs.writeFileSync(path.join(base,'FULL_EVALUATION_CODE.md'),full.join('\n'),{flag:'wx'});
 console.log(JSON.stringify({report:'DOCUMENT_HOLDOUT_100_REPORT_2026_10_31.md',full_scripts:names.length}));
}
try{main();}catch(error){console.error(error.stack);process.exitCode=1;}
```
