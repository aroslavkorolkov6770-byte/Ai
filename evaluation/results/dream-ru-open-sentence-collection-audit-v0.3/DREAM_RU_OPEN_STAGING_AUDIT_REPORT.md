# Независимый audit staging-кандидата DReaM-ru-open

Статус: candidate-only; active manifest и обучение не изменялись.
Источник: `dream-ru-open-cc-by-v1`; лицензия: `CC-BY-4.0`.
SHA-256 candidate JSONL: `bec0786af854e0ae5bf564261ffea1a1fb79abdf3fed455cf9ebf7270b65f0e6`.
Принято: 54984; отклонено: 0; всего: 54984.
Split: {'test': 2738, 'train': 49519, 'validation': 2727}.
Типы: {'language_document': 54984}; языковых символов: 3958224; диалоговых ходов: 0.
Причины отклонения: {}.
Межsplit точных дублей: 0; близких кандидатов: 0.
Training allowed: `False`; причины блокировки: ['insufficient_unique_dialogue_turns', 'insufficient_language_characters'].

Аудит использовал временный hard link и не копировал тексты в отчёт. До отдельного решения candidate нельзя переносить в raw/ или добавлять в active manifest.
