# Проверка надёжного режима через HTTP

Проверка выполнена против реально запущенного локального uvicorn, а не имитации транспорта.

| Показатель | Значение |
|---|---:|
| api | http://127.0.0.1:8000 |
| checks | 15 |
| checks_passed | 15 |
| memory_messages | 6 |
| memory_passed | True |
| qa_version | web-qa-intent-v2.0 |
| dialogue_version | web-dialogue-intent-v2.10 |
| supported_intents | 32 |
| live_api_gate_passed | True |

## Запросы

- Как сделать кнопку в HTML?
  Ожидалось: html_button; получено: html_button; контекст: False; проверка: True.
  Ответ: Кнопку создаёт тег button: <button type="button">Нажми</button>. Тип button предотвращает случайную отправку формы.

- Как добавить кнопку в HTML?
  Ожидалось: html_button; получено: html_button; контекст: False; проверка: True.
  Ответ: Кнопку создаёт тег button: <button type="button">Нажми</button>. Тип button предотвращает случайную отправку формы.

- А как сделать её синей?
  Ожидалось: css_background; получено: css_background; контекст: True; проверка: True.
  Ответ: Цвет фона задаётся свойством background-color: .card { background-color: blue; }. Селектор должен указывать на нужный элемент.

- Как изменить размер текста в CSS?
  Ожидалось: css_font_size; получено: css_font_size; контекст: False; проверка: True.
  Ответ: Размер текста задаётся свойством font-size: .text { font-size: 20px; }. Можно использовать px, rem и другие единицы CSS.

- Как сделать синим именно его?
  Ожидалось: css_text_color; получено: css_text_color; контекст: True; проверка: True.
  Ответ: Цвет текста задаётся свойством color: .text { color: blue; }. Вместо blue можно указать другой CSS-цвет.

- Как запустить таймер в JavaScript?
  Ожидалось: timer_start; получено: timer_start; контекст: False; проверка: True.
  Ответ: Таймер с однократным запуском: const timerId = setTimeout(() => { console.log('Готово'); }, 1000);. Задержка указывается в миллисекундах.

- Как теперь его остановить?
  Ожидалось: timer_cancel; получено: timer_cancel; контекст: True; проверка: True.
  Ответ: Для отмены таймера передай его сохранённый идентификатор в clearTimeout: clearTimeout(timerId);.

- Как сохранить данные в localStorage?
  Ожидалось: storage_save; получено: storage_save; контекст: False; проверка: True.
  Ответ: Строку можно сохранить так: localStorage.setItem('theme', 'dark');. Данные сохраняются для текущего origin и обычно остаются после закрытия браузера.

- Как теперь прочитать сохранённое значение?
  Ожидалось: storage_read; получено: storage_read; контекст: True; проверка: True.
  Ответ: Прочитай строку через localStorage.getItem('theme'). Если ключа нет, результат равен null; обработай этот случай.

- Как теперь удалить эту настройку?
  Ожидалось: storage_remove; получено: storage_remove; контекст: True; проверка: True.
  Ответ: Чтобы удалить один ключ, вызови localStorage.removeItem('theme'). Остальные сохранённые значения останутся на месте.

- Как создать кнопку в HTML?
  Ожидалось: html_button; получено: html_button; контекст: False; проверка: True.
  Ответ: Кнопку создаёт тег button: <button type="button">Нажми</button>. Тип button предотвращает случайную отправку формы.

- Как повесить обработчик на неё?
  Ожидалось: js_click; получено: js_click; контекст: True; проверка: True.
  Ответ: Добавь обработчик события: button.addEventListener('click', () => { console.log('Нажато'); });. Переменная button должна содержать найденную кнопку.

- Как вставить кнопку в HTML?
  Ожидалось: html_button; получено: html_button; контекст: False; проверка: True.
  Ответ: Кнопку создаёт тег button: <button type="button">Нажми</button>. Тип button предотвращает случайную отправку формы.

- Как добавить изображение в HTML?
  Ожидалось: html_image; получено: html_image; контекст: False; проверка: True.
  Ответ: Добавь изображение через img: <img src="photo.jpg" alt="Описание фотографии">. В src укажи путь к файлу, а в alt — его смысл.

- Какая сегодня погода на Марсе?
  Ожидалось: None; получено: None; контекст: False; проверка: True.
  Ответ: Пока не могу уверенно ответить. Уточни, что нужно сделать и с каким элементом. Сейчас моя основная область — HTML, CSS и JavaScript.
