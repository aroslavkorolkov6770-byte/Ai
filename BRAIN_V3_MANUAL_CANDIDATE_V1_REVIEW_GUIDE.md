# Brain v3 — как провести human review первого manual candidate

Статус: инструкция для review-only партии; обучение не разрешает  
Дата: 2026-09-29

## Что проверяется

Нужно оценить сцены как человек, а не просто убедиться, что они открываются.
Для каждой просмотренной сцены проверь четыре вещи:

1. Это project-owned черновик без личных данных и внешнего текста.
2. Все четыре реплики связаны между собой, а второй ответ учитывает первую
   часть разговора.
3. Русский текст звучит естественно и не похож на повторяющийся шаблон.
4. Topic, tone и ожидаемые свойства совпадают со смыслом сцены.

## Обязательная выборка

Нужно проверить все high-risk ID:

- `brain-v3-owned-manual-v1-005`–`008` — emotional support;
- `brain-v3-owned-manual-v1-017`–`020` — privacy and memory;
- `brain-v3-owned-manual-v1-021`–`024` — safety boundaries.

Для общей стратифицированной выборки минимум достаточно двух записей. Лучше
просмотреть три разные темы: `001` (casual dialogue), `009` (correction) и
`013` (uncertainty/clarification).

## Как открыть одну сцену

В локальном терминале из папки проекта:

```powershell
.\.venv\Scripts\python.exe -m evaluation.brain_v3_manual_candidate_human_review --show-id brain-v3-owned-manual-v1-005
```

Команда только показывает текст. Она не меняет corpus, API, status или
обучение.

## Как фиксируется verdict

Одобрение допустимо только когда пройдены все четыре проверки. Если пользователь
попросит записать одобрение, используется локальная команда с четырьмя
reason code: `provenance_checked`, `coherent_follow_up`,
`natural_non_template_tone`, `metadata_route_match`.

Отклонение фиксируется одним или несколькими короткими кодами, например
`template_like`, `incoherent_follow_up`, `unfounded_memory`,
`privacy_boundary_failure` или `unsafe_or_unhelpful_advice`. Свободный текст
сцены в ledger не сохраняется.

Вердикт можно также сообщить прямо здесь в чате в форме:

```text
одобряю brain-v3-owned-manual-v1-005
```

или:

```text
отклоняю brain-v3-owned-manual-v1-005: template_like
```

Тогда запись будет добавлена в локальный metadata-only ledger после явного
подтверждения review, без изменения модели.

## После review

Скрипт создаёт новый immutable summary с количеством verdict. Даже если
обязательная выборка пройдена положительно, это не запускает обучение:
следующим шагом остаётся отдельное решение о создании staging/audit candidate.
