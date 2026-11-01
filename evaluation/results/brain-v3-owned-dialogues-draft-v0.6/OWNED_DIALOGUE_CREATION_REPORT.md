# Brain v3 — project-owned dialogue candidate v0.6

## Что создано

- Staging candidate: `C:\Users\KROLL\Desktop\Ai\data\brain_v3\staging\brain-v3-owned-dialogues-v0.6.jsonl`.
- SHA-256: `bef3f60bbd8e481c33248c5c6a1e19d4f3bf96524e54692488bbf483f3519f6c`; bytes: 7052783.
- Dialogue-записей и ходов: 3475 и 13900.
- Структура 2/4/6 ходов: {'2': 434, '4': 2607, '6': 434}.
- Уникальных первых user-реплик: 3475.
- Уникальных assistant-реплик: 6950 из 6950 (1.000).
- Автоматических context-echo сигналов: 0 (0.000); допустимый максимум: 0.12.
- Чрезмерно длинных assistant-реплик: 9 (0.001); допустимый максимум: 0.08.

## Граница качества

- Это original scenario-authored local candidate, а не набор личных переписок и не готовые веса модели.
- Проверены структура, semantic quotas, hashes, безопасные текстовые ограничения, повторяемость стартовых/assistant-реплик и отсутствие обязательного context-echo паттерна.
- Candidate ещё не является human-reviewed: нужна отдельная стратифицированная ручная выборка, independent staging audit и active preflight.
- Active corpus, API, модельные веса, обучение и GitHub этим действием не меняются.
