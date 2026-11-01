# Brain v3 — project-owned dialogue candidate v0.1

## Что создано

- Staging candidate: `C:\Users\KROLL\Desktop\Ai\data\brain_v3\staging\brain-v3-owned-dialogues-v0.1.jsonl`.
- SHA-256: `ce173fa25e5c0da1934a7ccfbc2642f2e76136c9319e9a7b802b28fd18285b54`; bytes: 8818244.
- Dialogue-записей и ходов: 3475 и 13900.
- Структура 2/4/6 ходов: {'2': 434, '4': 2607, '6': 434}.
- Уникальных первых user-реплик: 3475.
- Уникальных assistant-реплик: 6906 из 6950 (0.994).

## Граница качества

- Это original scenario-authored local candidate, а не набор личных переписок и не готовые веса модели.
- Проверены структура, semantic quotas, hashes, безопасные текстовые ограничения и повторяемость стартовых/assistant-реплик.
- Candidate ещё не является human-reviewed: нужна отдельная стратифицированная ручная выборка, independent staging audit и active preflight.
- Active corpus, API, модельные веса, обучение и GitHub этим действием не меняются.
