"""Проверка учебных выборок до создания модели и изменения весов.

Проверка техническая: она не обещает хорошего языка или достоверных ответов.
В отчёте сохраняются количества и отпечатки, а не тексты учебных сообщений.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
from itertools import combinations
from pathlib import Path
from typing import Any
from uuid import uuid4

from .dataset_tools import dataset_fingerprint, normalize_prompt
from .conversation_context import build_context
from .dialogue_data import DialogueExample, format_dialogue_prompt
from .training_data import TrainingPair


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def make_run_directory(requested: str | Path | None = None) -> Path:
    """Каждый опыт получает новую папку; действующие веса не перезаписываются."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = Path(requested) if requested else (
        PROJECT_ROOT / "model_candidates" / f"run-{stamp}-{uuid4().hex[:8]}"
    )
    destination = destination.resolve()
    active = (PROJECT_ROOT / "model_artifacts").resolve()
    if destination == active or active in destination.parents:
        raise ValueError("Каталог действующей модели нельзя использовать для эксперимента.")
    # exist_ok=False защищает и предыдущий опыт от случайной перезаписи.
    destination.mkdir(parents=True, exist_ok=False)
    return destination


def _fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_prompt(text).encode("utf-8")).hexdigest()


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def analyze_training_preflight(
    splits: dict[str, list[TrainingPair]],
    tokenizer: Any,
    max_len: int,
    dialogues: list[DialogueExample] | None = None,
) -> dict[str, Any]:
    """Проверяет фактический train после добавления диалогов, без обрезания.

    Здесь измеряется encoder-decoder формат старого train.py: вопрос + BOS/SEP
    и ответ + BOS/EOS имеют отдельный лимит. Для decoder-only нужен другой
    бюджет общей последовательности — этот отчёт не следует применять к нему.
    """
    if max_len < 5:
        raise ValueError("max_len должен быть не меньше 5.")
    if set(splits) != {"train", "validation", "test"}:
        raise ValueError("Нужны ровно train, validation и test.")
    summaries: dict[str, Any] = {}
    prompt_sets: dict[str, set[str]] = {}
    reasons: list[str] = []
    for name, pairs in splits.items():
        prompt_lengths: list[int] = []
        target_lengths: list[int] = []
        examples_over_limit = 0
        unknown_prompt = unknown_target = 0
        prompt_sets[name] = {_fingerprint(pair.prompt) for pair in pairs}
        for pair in pairs:
            # max_length специально не передаётся: сначала видим весь пример.
            prompt_ids = tokenizer.encode(pair.prompt)
            target_ids = tokenizer.encode(pair.completion)
            prompt_length, target_length = len(prompt_ids) + 2, len(target_ids) + 2
            prompt_lengths.append(prompt_length)
            target_lengths.append(target_length)
            examples_over_limit += int(max(prompt_length, target_length) > max_len)
            unknown_prompt += prompt_ids.count(tokenizer.unk_id)
            unknown_target += target_ids.count(tokenizer.unk_id)
        source_overflow = sum(length > max_len for length in prompt_lengths)
        target_overflow = sum(length > max_len for length in target_lengths)
        summaries[name] = {
            "examples": len(pairs), "fingerprint": dataset_fingerprint(pairs),
            "prompt_tokens": sum(prompt_lengths), "target_tokens": sum(target_lengths),
            "prompt_max": max(prompt_lengths, default=0),
            "target_max": max(target_lengths, default=0),
            "prompt_p95": _percentile(prompt_lengths, .95),
            "target_p95": _percentile(target_lengths, .95),
            "prompt_over_limit": source_overflow, "target_over_limit": target_overflow,
            "examples_over_limit": examples_over_limit,
            "unknown_prompt_tokens": unknown_prompt, "unknown_target_tokens": unknown_target,
        }
        if not pairs:
            reasons.append(f"empty_{name}")
        if examples_over_limit:
            reasons.append(f"truncation_{name}")
        if name == "train" and (unknown_prompt or unknown_target):
            reasons.append("unknown_tokens_in_train")

    # Проверяем именно итоговые части, а не базовый split до добавления диалогов.
    cross_split = []
    for left, right in combinations(splits, 2):
        overlap = prompt_sets[left] & prompt_sets[right]
        if overlap:
            cross_split.append({"left": left, "right": right, "count": len(overlap),
                                "sample_hashes": sorted(overlap)[:10]})
    if cross_split:
        reasons.append("cross_split_prompt_overlap")

    # Проверочный вопрос может находиться внутри истории учебного диалога,
    # хотя целый многоходовый prompt не совпадает с ним строка в строку.
    # Считаем это сигналом загрязнения оценки, даже если ответ в истории иной.
    dialogue_overlap: Counter[str] = Counter()
    overlap_examples = []
    for index, example in enumerate(dialogues or []):
        if _fingerprint(format_dialogue_prompt(example)) not in prompt_sets["train"]:
            continue
        # Форматтер оставляет только недавнюю историю. Отброшенная старая
        # реплика не считается утечкой через вход текущего учебного примера.
        visible_lines = set(build_context(example.messages).splitlines())
        prompts = {example.prompt}
        for message in example.messages:
            if message["role"] != "user":
                continue
            full_line = build_context([message], max_characters=max(1600, len(message["content"]) + 64))
            if full_line in visible_lines:
                prompts.add(message["content"])
        fingerprints = {_fingerprint(prompt) for prompt in prompts}
        for heldout in ("validation", "test"):
            found = fingerprints & prompt_sets[heldout]
            dialogue_overlap[heldout] += len(found)
            if found and len(overlap_examples) < 20:
                overlap_examples.append({"dialogue_index": index, "heldout": heldout,
                                         "prompt_hashes": sorted(found)[:10]})
    if any(dialogue_overlap.values()):
        reasons.append("dialogue_history_overlaps_holdout")

    return {
        "format_version": 1, "encoding": "encoder-decoder",
        "max_len": max_len, "splits": summaries,
        "cross_split_prompt_overlap": cross_split,
        "dialogue_history_overlap": dict(dialogue_overlap),
        "dialogue_history_overlap_examples": overlap_examples,
        "checks_passed": not reasons, "blocking_reasons": reasons,
        "quality_approved": False,
        "limitations": [
            "Техническая проверка не подтверждает качество языка, фактов или лицензии.",
            "Совпадение вопросов не заменяет проверку близких дубликатов и семейств источников.",
            "Лимит измерен в токенах данного токенизатора, включая служебные токены.",
        ],
    }


def require_valid_preflight(report: dict[str, Any]) -> None:
    """Не запускает оптимизатор на молча повреждённых или смешанных данных."""
    if not report["checks_passed"]:
        raise ValueError("Обучение остановлено до изменения весов: " +
                         ", ".join(report["blocking_reasons"]))
