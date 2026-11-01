# Brain v3 — v1.4 runtime-contract staging/audit snapshot v0.1

Статус: **проверенный metadata-only staging/audit snapshot; не training dataset**  
Дата: 2026-09-30  
Основной план: `BRAIN_V3_QUALITY_MODEL_ROADMAP.md`, этап B

## Что было создано

После отдельного решения владельца создан новый неизменяемый snapshot:

`data/brain_v3/staging/brain-v3-pickup-context-repair-v1.4-runtime-contract-v0.1.json`.

- Основание — две v1.4-сцены с реальными approved verdict и полным human review
  `2/2`.
- Snapshot содержит только provenance, SHA-256, свойства route-контракта и
  статус запретов.
- В него не вошли реплики, URL, прогнозы, web-факты или закрытые runtime
  markers.
- Он имеет JSON-формат metadata snapshot, а не corpus JSONL; active manifest
  не изменялся.

## Результат audit

- Проверено contract-источников: `2`.
- Все origin verdict: `approved`.
- Dynamic runtime content исключён: `true`.
- В snapshot нет текста диалога и runtime marker: `true`.
- SHA-256 active manifest до и после создания совпал:
  `7fb0e16c5851e44b56c4b8f1430670ea5de6e86837b6f9ef433d9977750721b3`.

Полные metadata-only отчёты находятся в
`evaluation/results/brain-v3-pickup-context-repair-v1.4-runtime-staging-v0.1/`
и
`evaluation/results/brain-v3-pickup-context-repair-v1.4-runtime-staging-audit-v0.1/`.

## Почему обучение не началось

Этот snapshot проверяет корректную границу runtime route: свежий результат
должен появляться только при живом запросе, а не быть выдан за постоянную
истину модели. Поэтому `training_eligible=false`, `training_started=false` и
`promotion_allowed=false`. Для обучения всё ещё нужен широкий статичный
human-reviewed corpus с достаточным числом диалоговых ходов и покрытием тем.

## Следующий шаг

Продолжить этап B: по coverage matrix подготовить следующий небольшой
original review-only tranche для статичных разговорных способностей, не
перенося runtime web-contracts в train данные.
