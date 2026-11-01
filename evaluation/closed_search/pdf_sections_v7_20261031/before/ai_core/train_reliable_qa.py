"""Первый этап curriculum: одиночные вопросы, отказы и контроль смысла."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch import nn

from .reliable_qa import (
    CURRICULUM_PATH, DEFAULT_ARTIFACT, FeatureEncoder, IntentNetwork,
    fingerprint, load_curriculum,
)


def calibrate(probabilities: torch.Tensor, coverage: list[float], labels: torch.Tensor,
              unknown: int) -> tuple[dict, dict]:
    """Выбирает порог по validation, не открывая приёмочный test-набор."""
    top, indices = probabilities.topk(2, dim=1)
    best = None
    for confidence in (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.92, 0.97):
        for margin in (0.05, 0.15, 0.25, 0.4):
            for minimum_coverage in (0.25, 0.45, 0.6, 0.75):
                accepted = ((indices[:, 0] != unknown) & (top[:, 0] >= confidence)
                            & ((top[:, 0] - top[:, 1]) >= margin)
                            & (torch.tensor(coverage) >= minimum_coverage))
                wrong = int((accepted & (indices[:, 0] != labels)).sum())
                correct = int((accepted & (indices[:, 0] == labels)).sum())
                # В приоритете точность принятых ответов, затем их покрытие.
                rank = (-wrong, correct, -confidence, -margin, -minimum_coverage)
                if best is None or rank > best[0]:
                    best = (rank, {"confidence": confidence, "margin": margin,
                                   "coverage": minimum_coverage},
                            {"accepted_correct": correct, "accepted_wrong": wrong,
                             "supported": int((labels != unknown).sum()),
                             "unknown": int((labels == unknown).sum())})
    if best is None:
        raise RuntimeError("Не удалось подобрать порог.")
    return best[1], best[2]


def train(output: Path, epochs: int = 240) -> None:
    if output.exists():
        raise ValueError(f"Каталог эксперимента уже существует: {output}")
    if epochs < 1:
        raise ValueError("epochs должен быть положительным.")
    torch.set_num_threads(2)
    torch.manual_seed(42)
    data = load_curriculum()
    intents = [row["id"] for row in data["intents"]]
    rows = [*data["intents"], data["unknown"]]
    train_text = [text for row in rows for text in row["train"]]
    valid_text = [text for row in rows for text in row["validation"]]
    train_y = torch.tensor([i for i, row in enumerate(rows) for _ in row["train"]])
    # Короткие смысловые опоры отделяют действие от общего контекста.
    # Ответы с несколькими действиями не используем как вопросы: они давали
    # ложные связи между соседними задачами (например рамка и цвет фона).
    for index, row in enumerate(data["intents"]):
        anchors = row.get("anchors", [])
        train_text.extend(anchors)
        train_y = torch.cat((train_y, torch.full((len(anchors),), index, dtype=torch.long)))
    for group in data.get("contrast_groups", []):
        for intent, actions in group["actions"].items():
            texts = [action + " " + obj for action in actions for obj in group["objects"]]
            train_text.extend(texts)
            train_y = torch.cat((train_y, torch.full((len(texts),), intents.index(intent))))
    # Дополнительная факторизация: одно действие применяется к разным
    # объектам. Нельзя угадывать тип ответа только по слову «разметка».
    for group in data.get("object_groups", []):
        for intent, objects in group["intents"].items():
            texts = [(group["action"] + " " + obj).strip() for obj in objects]
            train_text.extend(texts)
            train_y = torch.cat((train_y, torch.full((len(texts),), intents.index(intent))))
    # Негативы учат не игнорировать отрицание и незнакомый контекст языка.
    # Это отдельная цель, а не запись отказов в качестве правильных ответов.
    negatives = ["не надо " + text for row in data["intents"][2:] for text in row["train"]]
    negatives += [row["train"][0] + " " + scope for row in data["intents"][2:]
                  for scope in ("python", "java", "rust", "tkinter", "swift")]
    train_text.extend(negatives)
    train_y = torch.cat((train_y, torch.full((len(negatives),), len(intents))))
    valid_y = torch.tensor([i for i, row in enumerate(rows) for _ in row["validation"]])
    encoder = FeatureEncoder.fit(train_text)
    train_x, _ = encoder.transform(train_text)
    valid_x, valid_coverage = encoder.transform(valid_text)
    config = {"input_dim": train_x.shape[1], "classes": len(rows), "hidden_dim": 96}
    model = IntentNetwork(**config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.003, weight_decay=0.02)
    # Частый класс «неизвестно» не должен вытеснять остальные задачи.
    weights = train_y.bincount().float().reciprocal()
    weights /= weights.mean()
    best_loss, state, best_epoch = float("inf"), None, 0
    history = []
    for epoch in range(1, epochs + 1):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        # Пропуски отдельных признаков мешают запомнить одну случайную часть
        # фразы и заставляют различать задачу по нескольким словам.
        noisy_x = nn.functional.dropout(train_x, p=0.25, training=True)
        loss = nn.functional.cross_entropy(model(noisy_x), train_y, weight=weights)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        model.eval()
        with torch.inference_mode():
            valid_logits = model(valid_x)
            valid_loss = float(nn.functional.cross_entropy(valid_logits, valid_y))
            accuracy = float((valid_logits.argmax(1) == valid_y).float().mean())
        if valid_loss < best_loss:
            best_loss, best_epoch = valid_loss, epoch
            state = {key: value.detach().clone() for key, value in model.state_dict().items()}
        history.append({"epoch": epoch, "train_loss": float(loss.detach()),
                        "validation_loss": valid_loss, "validation_accuracy": accuracy})
        if epoch % 40 == 0:
            print(f"epoch={epoch}, validation_accuracy={accuracy:.3f}", flush=True)
    if state is None:
        raise RuntimeError("Обучение не создало состояния.")
    model.load_state_dict(state)
    with torch.inference_mode():
        probabilities = model(valid_x).softmax(-1)
    thresholds, calibration = calibrate(probabilities, valid_coverage, valid_y, len(intents))
    metadata = {
        "format_version": 1, "model_version": "web-qa-intent-v2.0",
        "architecture": "character-features-mlp-complete-answer",
        "model_config": config, "intents": intents,
        "answers": {row["id"]: row["answer"] for row in data["intents"]},
        "vocabulary": encoder.vocabulary, "idf": encoder.idf.tolist(),
        "feature_stopwords": sorted(encoder.stopwords),
        "thresholds": thresholds, "seed": 42,
        "curriculum_sha256": fingerprint(CURRICULUM_PATH),
        "selected_epoch": best_epoch, "validation_loss": best_loss,
        "calibration": calibration,
        "training_examples": len(train_text), "validation_examples": len(valid_text),
        "dialogue_examples": 0,
        "training_samples": [{"prompt": text, "label": int(label)}
                             for text, label in zip(train_text, train_y.tolist())],
    }
    output.mkdir(parents=True)
    torch.save({**metadata, "model_state": state}, output / "qa.pt")
    metrics = {key: value for key, value in metadata.items()
               if key not in {"vocabulary", "idf", "answers", "feature_stopwords", "training_samples"}}
    (output / "metrics.json").write_text(json.dumps({**metrics, "history": history},
                                                  ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_ARTIFACT.parent)
    parser.add_argument("--epochs", type=int, default=240)
    args = parser.parse_args()
    try:
        train(args.output, args.epochs)
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"QA-обучение остановлено: {error}") from error
