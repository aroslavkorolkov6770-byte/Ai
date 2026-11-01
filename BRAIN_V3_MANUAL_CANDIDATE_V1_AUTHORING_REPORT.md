# Brain v3 — отчёт о первой review-only партии ручного candidate

Статус: **required review coverage complete; v1 не является training dataset**  
Дата: 2026-09-29  
Основной план: `BRAIN_V3_QUALITY_MODEL_ROADMAP.md`, этап B

## Что создано

- `data/brain_v3/reviews/brain-v3-owned-manual-candidate-v1.review.jsonl` —
  24 оригинальные project-agent draft-сцены по четыре хода, всего 96 ходов.
- Темы распределены равномерно: по 4 сцены в `casual_dialogue`,
  `emotional_support`, `correction`, `uncertainty_and_clarification`,
  `privacy_and_memory` и `safety_boundaries`.
- Источник обозначен как `owned` / `OWNED_BY_PROJECT`; запрещены внешний текст,
  личные переписки и template generator. Каждая сцена имеет отдельный family
  ID и остаётся в логическом split `review_only`.

## Техническая проверка

Проверка выполнена локальным модулем
`evaluation/brain_v3_manual_candidate_review.py`.

- Формат строго отделён от schema training corpus: отсутствуют train,
  validation и test split, а также поля, которыми запись могла бы выдать себя
  за активный corpus record.
- Проверены ownership flags, идентификаторы, фиксированное чередование
  `user`/`assistant`, четыре непустых хода, labels, coverage scope,
  exact/near duplicates и рискованные маркеры URL, контактов, секретов и
  instruction-like текста.
- SHA-256 review-only файла:
  `07be0fef71c2e97263adba8261129f236d370e5e7f8fd38951c61bc8888f8731`.
- Создана immutable metadata-only очередь:
  `evaluation/results/brain-v3-owned-manual-candidate-v1-review-queue-v0.2/`.
  В ней нет реплик: только IDs, per-record SHA-256, labels и обязательность
  проверки.

## Human review

- Все 12 сцен из `emotional_support`, `privacy_and_memory` и
  `safety_boundaries` требуют полной проверки.
- Остальные 12 образуют пул для стратифицированной проверки; минимум 2 должны
  быть просмотрены до следующего решения.
- Для каждой выбранной сцены reviewer оценивает происхождение и отсутствие
  личных данных, связность всех четырёх ходов, естественность без шаблонного
  ритма и совпадение labels с реальным смыслом.
- Локальный просмотр одной сцены доступен только по явной команде
  `python -m evaluation.brain_v3_manual_candidate_review --show-id <ID>`.
  Эта команда ничего не записывает и не меняет статус.
- Для записи настоящего verdict создан отдельный metadata-only workflow
  `evaluation/brain_v3_manual_candidate_human_review.py`. Он принимает только
  фиксированные reason codes, не умеет самостоятельно одобрять запись и
  хранит лишь ID, SHA-256, verdict, роль reviewer и время решения.
- Начальный summary находится в
  `evaluation/results/brain-v3-owned-manual-candidate-v1-human-review-v0.1/`:
  в начале human review ещё не был начат; актуальный summary v0.15 фиксирует
  одобрений `12` и отклонений `2`, training/promotion по-прежнему запрещены.
- Отклонённая privacy-сцена `018` не переписывается в immutable v1 файле.
  Для её замены создан отдельный review-only candidate v1.1: явная просьба
  «запомни» должна работать как память текущего локального чата, а не
  автоматически становиться долговременным хранением.
- Отклонённая privacy-сцена `019` также остаётся в v1 для честного audit
  history. В v1.1 вынесена отдельная проверка того, что подтверждение уже
  явно данной просьбы остаётся коротким, естественным и вариативным.

## Проверка кода

Выполнен набор из 20 тестов: manual-review pipeline, human-review ledger,
expanded acceptance
contract, identity и приоритет identity route. Результат: `OK`.

## Что намеренно не сделано

После отдельного решения владельца создан только новый неактивный staging/audit
snapshot из одобренных сцен: подробности в
`BRAIN_V3_REVIEWED_STAGING_V0_1_REPORT.md`. Не обновлены corpus manifest и API,
не запускались promotion/training, не создавались веса и не выполнялись
GitHub-операции.

## Следующий шаг

Подготовить scope нового review-only tranche для незакрытых semantic ячеек.
Он не должен смешиваться с этим v1 или с staging snapshot; обучение собственных
весов всё ещё не разрешено.
