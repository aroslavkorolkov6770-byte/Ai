"""Второй этап разрешён только после успешной приёмки одиночных ответов."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import torch
from torch import nn

from .reliable_qa import ROOT, ReliableQA, FeatureEncoder, fingerprint, normalize
from .reliable_dialogue import (
    ContextNetwork,
    mask_semantic_markers,
    mask_transitions,
)
from .train_reliable_qa import calibrate


def calibrate_states(probabilities: torch.Tensor, coverage: list[float], labels: torch.Tensor,
                     state_ids: torch.Tensor, unknown: int) -> tuple[dict[str, dict], dict]:
    """Подбирает порог отдельно для каждого подтверждённого состояния.

    У разных объектов разное число допустимых продолжений. Единый порог
    заставлял простые переходы наследовать осторожность самой неоднозначной
    кнопки, поэтому корректные ответы отклонялись без пользы для точности.
    """
    thresholds: dict[str, dict] = {}
    accepted_correct = accepted_wrong = supported = rejected_context = 0
    coverage_tensor = torch.tensor(coverage)
    for state in sorted(set(state_ids.tolist())):
        selected = state_ids == state
        state_thresholds, metrics = calibrate(
            probabilities[selected], coverage_tensor[selected].tolist(), labels[selected], unknown,
        )
        thresholds[str(state)] = state_thresholds
        accepted_correct += metrics["accepted_correct"]
        accepted_wrong += metrics["accepted_wrong"]
        supported += metrics["supported"]
        rejected_context += metrics["unknown"]
    return thresholds, {
        "accepted_correct": accepted_correct,
        "accepted_wrong": accepted_wrong,
        "supported": supported,
        "unknown": rejected_context,
    }


def make_examples(data: dict, intents: list[str], split: str) -> list[tuple[str, int, int]]:
    """Одинаковый текст в другом состоянии — отдельный отрицательный пример."""
    matches: dict[tuple[str, int], int] = {}
    prompts: dict[str, str] = {}
    states_by_intent: dict[str, set[str]] = {}
    for group in data["groups"]:
        states_by_intent.setdefault(group["intent"], set()).update(group["previous"])
    for group in data["groups"]:
        for prompt in group[split]:
            key = normalize(prompt)
            prompts[key] = prompt
            # Вариант фразы наследует все подходящие состояния своего смысла.
            # Иначе «её фон» после кнопки и «его фон» после блока становились
            # взаимными отрицательными примерами, хотя задача одна и та же.
            for previous in sorted(states_by_intent[group["intent"]]):
                state = intents.index(previous)
                target = intents.index(group["intent"])
                if (key, state) in matches and matches[key, state] != target:
                    raise ValueError("Конфликт целей диалогового curriculum.")
                matches[key, state] = target
    # Последний индекс — отсутствие подходящего контекста/неизвестный ответ.
    return [(prompt, state, matches.get((key, state), len(intents)))
            for key, prompt in prompts.items() for state in range(len(intents) + 1)]


def make_composed_examples(data: dict, intents: list[str]) -> list[tuple[str, int, int]]:
    """Строит новые сочетания из смысловых частей, а не копий диалогов."""
    composition = data.get("semantic_composition")
    if not composition:
        return []
    states_by_intent: dict[str, set[str]] = {}
    for group in data["groups"]:
        states_by_intent.setdefault(group["intent"], set()).update(group["previous"])
    active_states = sorted({state for states in states_by_intent.values() for state in states})
    rows: list[tuple[str, int, int]] = []
    for intent_offset, (intent, actions) in enumerate(composition["intents"].items()):
        if intent not in states_by_intent or intent not in intents:
            raise ValueError(f"Semantic intent не связан с переходами: {intent}")
        valid_states = states_by_intent[intent]
        target = intents.index(intent)
        invalid_states = [state for state in active_states if state not in valid_states]
        for template_offset, template in enumerate(composition["templates"]):
            for action_offset, action in enumerate(actions):
                for reference_offset, reference in enumerate(composition["references"]):
                    prompt = template.format(action=action, reference=reference)
                    for state in sorted(valid_states):
                        rows.append((prompt, intents.index(state), target))
                    # Четыре меняющихся чужих состояния на формулировку дают
                    # разнообразные негативы без квадратичного раздувания данных.
                    if invalid_states:
                        offset = (intent_offset + template_offset + action_offset
                                  + reference_offset) % len(invalid_states)
                        selected = [invalid_states[(offset + index) % len(invalid_states)]
                                    for index in range(min(4, len(invalid_states)))]
                        rows.extend((prompt, intents.index(state), len(intents))
                                    for state in selected)
    # Одинаковая фраза о цвете означает фон после вопроса о блоке, но цвет
    # текста после вопроса о шрифте. Такие пары специально имеют общий текст:
    # правильную цель обязано определять состояние, а не ключевое слово.
    for dependent in composition.get("state_dependent", []):
        target_by_state: dict[str, int] = {}
        for intent in dependent["intents"]:
            if intent not in states_by_intent or intent not in intents:
                raise ValueError(f"State-dependent intent не связан с переходами: {intent}")
            for state in states_by_intent[intent]:
                previous_target = target_by_state.get(state)
                target = intents.index(intent)
                if previous_target is not None and previous_target != target:
                    raise ValueError(f"Состояние неоднозначно в state-dependent наборе: {state}")
                target_by_state[state] = target
        for template in dependent["templates"]:
            for action in dependent["actions"]:
                prompt = template.format(action=action)
                rows.extend((prompt, intents.index(state), target_by_state.get(state, len(intents)))
                            for state in active_states)
    # Контрастные группы используют одинаковые объекты с разными действиями.
    # Так слово «ключ» не сможет само по себе означать удаление: сеть должна
    # различить «вернуть ключ» и «стереть ключ» по глаголу задачи.
    for contrast_offset, contrast in enumerate(composition.get("contrast_composition", [])):
        valid_states = set(contrast["previous"])
        invalid_states = [state for state in active_states if state not in valid_states]
        for intent_offset, (intent, actions) in enumerate(contrast["actions"].items()):
            if intent not in intents:
                raise ValueError(f"Contrast intent отсутствует в QA: {intent}")
            target = intents.index(intent)
            for template_offset, template in enumerate(contrast["templates"]):
                for action_offset, action in enumerate(actions):
                    for object_offset, object_name in enumerate(contrast["objects"]):
                        prompt = template.format(action=action, object=object_name)
                        rows.extend((prompt, intents.index(state), target)
                                    for state in sorted(valid_states))
                        if invalid_states:
                            offset = (contrast_offset + intent_offset + template_offset
                                      + action_offset + object_offset) % len(invalid_states)
                            selected = [invalid_states[(offset + index) % len(invalid_states)]
                                        for index in range(min(4, len(invalid_states)))]
                            rows.extend((prompt, intents.index(state), len(intents))
                                        for state in selected)
    # Общие глаголы намеренно скрещиваются с разными техническими объектами.
    # Сеть не должна считать «установить» синонимом только одной операции.
    marker_composition = composition.get("marker_composition", {})
    for intent_offset, (intent, marker_phrases) in enumerate(
        marker_composition.get("intents", {}).items()
    ):
        if intent not in states_by_intent or intent not in intents:
            raise ValueError(f"Marker intent не связан с переходами: {intent}")
        valid_states = states_by_intent[intent]
        target = intents.index(intent)
        invalid_states = [state for state in active_states if state not in valid_states]
        for verb_offset, verb in enumerate(marker_composition["verbs"]):
            for marker_offset, marker in enumerate(marker_phrases):
                for reference_offset, reference in enumerate(marker_composition["references"]):
                    prompt = f"Как {verb} {marker} {reference}?"
                    rows.extend((prompt, intents.index(state), target)
                                for state in sorted(valid_states))
                    if invalid_states:
                        offset = (intent_offset + verb_offset + marker_offset
                                  + reference_offset) % len(invalid_states)
                        selected = [invalid_states[(offset + index) % len(invalid_states)]
                                    for index in range(min(4, len(invalid_states)))]
                        rows.extend((prompt, intents.index(state), len(intents))
                                    for state in selected)
    return rows


def merge_examples(*collections: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Удаляет повторы и запрещает разные цели для одной пары текст/состояние."""
    merged: dict[tuple[str, int], tuple[str, int, int]] = {}
    for rows in collections:
        for prompt, state, target in rows:
            key = (normalize(prompt), state)
            previous = merged.get(key)
            if previous is not None and previous[2] != target:
                raise ValueError(f"Конфликт составного curriculum: {prompt}")
            merged[key] = (prompt, state, target)
    return list(merged.values())


def train(qa_path: Path, gate_path: Path, output: Path, epochs: int = 220) -> None:
    if output.exists():
        raise ValueError("Выберите новую папку кандидата.")
    gate = json.loads(gate_path.read_text(encoding="utf-8"))["summary"]
    before = fingerprint(qa_path)
    if not gate.get("qa_gate_passed") or gate.get("checkpoint_sha256") != before:
        raise ValueError("Сначала нужна успешная QA-приёмка именно этих весов.")
    torch.set_num_threads(2)
    torch.manual_seed(42)
    qa = ReliableQA.load(qa_path)
    qa.network.requires_grad_(False)
    path = ROOT / "training_data/dialogue_curriculum.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    manual_train_rows = make_examples(data, qa.intents, "train")
    composed_train_rows = make_composed_examples(data, qa.intents)
    train_rows = merge_examples(manual_train_rows, composed_train_rows)
    valid_rows = make_examples(data, qa.intents, "validation")
    train_keys = {(normalize(prompt), state) for prompt, state, _ in train_rows}
    if train_keys & {(normalize(prompt), state) for prompt, state, _ in valid_rows}:
        raise ValueError("Train и validation диалогов пересекаются.")
    encoder = FeatureEncoder.fit(sorted({row[0] for row in train_rows}))
    train_prompts = [row[0] for row in train_rows]
    valid_prompts = [row[0] for row in valid_rows]
    train_x, _ = encoder.transform(train_prompts)
    valid_x, coverage = encoder.transform(valid_prompts)
    # Замороженная одиночная QA-сеть уже научилась распознавать действие в
    # полностью сформулированных вопросах. Её вероятности добавляются как
    # смысловые признаки, а состояние диалога разрешает неоднозначные местоимения.
    # Градиенты в QA не идут: первый этап остаётся неизменным и проверяемым по hash.
    with torch.inference_mode():
        train_qa_probabilities, _ = qa.probabilities(train_prompts)
        valid_qa_probabilities, _ = qa.probabilities(valid_prompts)
    train_x = torch.cat((train_x, train_qa_probabilities), dim=1)
    valid_x = torch.cat((valid_x, valid_qa_probabilities), dim=1)
    train_state = torch.tensor([row[1] for row in train_rows])
    valid_state = torch.tensor([row[1] for row in valid_rows])
    train_y = torch.tensor([row[2] for row in train_rows])
    valid_y = torch.tensor([row[2] for row in valid_rows])
    allowed = [sorted({target for _, state, target in train_rows
                       if state == index and target < len(qa.intents)})
               for index in range(len(qa.intents) + 1)]
    semantic_markers = data["semantic_composition"].get("semantic_markers", {})
    hard_semantic_markers = data["semantic_composition"].get(
        "hard_semantic_markers", {},
    )
    config = {"input_dim": train_x.shape[1], "states": len(qa.intents) + 1, "hidden_dim": 96}
    model = ContextNetwork(**config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.005, weight_decay=0.01)
    counts = train_y.bincount(minlength=len(qa.intents) + 1).clamp(min=1).float()
    # Суммарный вес ошибочного контекста равен суммарному весу поддерживаемых
    # переходов. Это консервативная настройка: лучше попросить уточнение, чем
    # уверенно применить правильное действие к неверному объекту из истории.
    weights = counts.reciprocal()
    active_intents = int((counts[:-1] > 1).sum())
    negative_weight_multiplier = active_intents
    weights[-1] *= negative_weight_multiplier
    weights /= weights.mean()
    best_loss, best_state, best_epoch = float("inf"), None, 0
    best_rank = None
    for epoch in range(1, epochs + 1):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        logits = model(nn.functional.dropout(train_x, p=0.1, training=True), train_state)
        loss = nn.functional.cross_entropy(logits, train_y, weight=weights)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        model.eval()
        with torch.inference_mode():
            logits = mask_transitions(model(valid_x, valid_state), valid_state, allowed)
            logits, _ = mask_semantic_markers(
                logits, valid_prompts, valid_state, qa.intents, semantic_markers, allowed,
            )
            valid_loss = float(nn.functional.cross_entropy(logits, valid_y, weight=weights))
        # Выбираем по фактически принятым validation-ответам. Низкая loss
        # на многочисленных ошибочных контекстах сама по себе не доказывает
        # способность отвечать на подходящие уточнения.
        if epoch % 10 == 0:
            _, current = calibrate_states(
                logits.softmax(-1), coverage, valid_y, valid_state, len(qa.intents),
            )
            rank = (-current["accepted_wrong"], current["accepted_correct"], -valid_loss)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_loss, best_epoch = valid_loss, epoch
                best_state = {key: value.detach().clone() for key, value in model.state_dict().items()}
        if epoch % 40 == 0:
            print(f"dialogue_epoch={epoch}, validation_loss={valid_loss:.4f}", flush=True)
    if best_state is None:
        raise RuntimeError("Нет обученного состояния диалога.")
    model.load_state_dict(best_state)
    with torch.inference_mode():
        logits = mask_transitions(model(valid_x, valid_state), valid_state, allowed)
        logits, _ = mask_semantic_markers(
            logits, valid_prompts, valid_state, qa.intents, semantic_markers, allowed,
        )
        probabilities = logits.softmax(-1)
    thresholds, global_calibration = calibrate(
        probabilities, coverage, valid_y, len(qa.intents),
    )
    state_thresholds, calibration = calibrate_states(
        probabilities, coverage, valid_y, valid_state, len(qa.intents),
    )
    training_prompts = sorted({normalize(prompt) for prompt, _, _ in train_rows})
    training_prompts_sha256 = hashlib.sha256(
        "\n".join(training_prompts).encode("utf-8")
    ).hexdigest()
    checkpoint = {"format_version": 1, "model_version": "web-dialogue-intent-v2.10",
                  "base_qa_sha256": before, "intents": qa.intents,
                  "model_config": config, "model_state": best_state,
                  "vocabulary": encoder.vocabulary, "idf": encoder.idf.tolist(),
                  "feature_stopwords": sorted(encoder.stopwords), "thresholds": thresholds,
                  "state_thresholds": state_thresholds,
                  "curriculum_sha256": fingerprint(path), "selected_epoch": best_epoch,
                  "validation_loss": best_loss, "calibration": calibration,
                  "global_calibration": global_calibration,
                  "train_examples": len(train_rows), "validation_examples": len(valid_rows),
                  "manual_train_examples": len(manual_train_rows),
                  "composed_train_examples": len(composed_train_rows),
                  "negative_weight_multiplier": negative_weight_multiplier,
                  "include_qa_probabilities": True,
                  "curriculum_schema_version": data["schema_version"],
                  "training_prompts": training_prompts,
                  "training_prompts_sha256": training_prompts_sha256,
                  "semantic_markers": semantic_markers,
                  "hard_semantic_markers": hard_semantic_markers}
    checkpoint["selection"] = "validation accepted answers after rejection calibration"
    checkpoint["allowed_transitions"] = allowed
    if before != fingerprint(qa_path):
        raise RuntimeError("Базовые веса QA изменились.")
    output.mkdir(parents=True)
    torch.save(checkpoint, output / "dialogue.pt")
    metrics = {key: value for key, value in checkpoint.items()
               if key not in {"model_state", "vocabulary", "idf", "feature_stopwords",
                              "training_prompts"}}
    (output / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-checkpoint", type=Path, required=True)
    parser.add_argument("--qa-gate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        train(args.qa_checkpoint, args.qa_gate, args.output)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        raise SystemExit(f"Диалоговое обучение остановлено: {error}") from error
