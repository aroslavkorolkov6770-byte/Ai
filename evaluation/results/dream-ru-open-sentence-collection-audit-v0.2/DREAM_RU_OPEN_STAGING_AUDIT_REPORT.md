# Независимый audit staging-кандидата DReaM-ru-open

Статус: candidate-only; active manifest и обучение не изменялись.
Источник: `dream-ru-open-cc-by-v1`; лицензия: `CC-BY-4.0`.
SHA-256 candidate JSONL: `d2f65956c064ec6c5672b83ae4ecfa1a87bbdce4f69beff59610557b68ba1d78`.
Принято: 54987; отклонено: 0; всего: 54987.
Split: {'test': 2738, 'train': 49522, 'validation': 2727}.
Типы: {'language_document': 54987}; языковых символов: 3958246; диалоговых ходов: 0.
Причины отклонения: {}.
Межsplit точных дублей: 0; близких кандидатов: 0.
Training allowed: `False`; причины блокировки: ['insufficient_unique_dialogue_turns', 'insufficient_language_characters'].

Аудит использовал временный hard link и не копировал тексты в отчёт. До отдельного решения candidate нельзя переносить в raw/ или добавлять в active manifest.
