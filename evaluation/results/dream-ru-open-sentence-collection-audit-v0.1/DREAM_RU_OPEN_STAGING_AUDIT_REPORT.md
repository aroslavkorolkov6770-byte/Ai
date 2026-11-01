# Независимый audit staging-кандидата DReaM-ru-open

Статус: candidate-only; active manifest и обучение не изменялись.
Источник: `dream-ru-open-cc-by-v1`; лицензия: `CC-BY-4.0`.
SHA-256 candidate JSONL: `2be39dee5fa108d5375253edaf7a6fb3ec3ddf06188bc6377b279f2f76cb7ca1`.
Принято: 54987; отклонено: 752; всего: 55739.
Split: {'test': 2738, 'train': 49522, 'validation': 2727}.
Типы: {'language_document': 54987}; языковых символов: 3958246; диалоговых ходов: 0.
Причины отклонения: {'near_duplicate_candidate': 752}.
Межsplit точных дублей: 0; близких кандидатов: 169.
Training allowed: `False`; причины блокировки: ['insufficient_unique_dialogue_turns', 'insufficient_language_characters', 'rejected_record_ratio_exceeded'].

Аудит использовал временный hard link и не копировал тексты в отчёт. До отдельного решения candidate нельзя переносить в raw/ или добавлять в active manifest.
