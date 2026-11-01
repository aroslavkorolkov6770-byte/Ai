"""Обучает собственную нейросеть выбора безопасной LMS-политики."""

from __future__ import annotations

import argparse
import gc
import json
import os
from collections import Counter
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from .lms_dataset import SPLIT_FILES, TASK_TYPES, build_context_grouped_splits, file_fingerprint
from .lms_reliable import (
    FACT_SPLITTER,
    LmsFactHashEncoder,
    LmsFactNetwork,
    LmsHashEncoder,
    LmsIntentNetwork,
)
from .reliable_qa import normalize


MODEL_VERSION = "lms-context-policy-v2.0"


def _labels(rows: list, task_types: list[str]) -> torch.Tensor:
    """Переводит типы задач в индексы нейросети с явной проверкой словаря."""
    index = {task_type: position for position, task_type in enumerate(task_types)}
    try:
        return torch.tensor([index[row.task_type] for row in rows], dtype=torch.long)
    except KeyError as error:
        raise ValueError(f"Неизвестный LMS task_type в обучении: {error.args[0]}") from error


def _macro_recall(logits: torch.Tensor, labels: torch.Tensor, classes: int) -> float:
    """Считает среднюю полноту классов, чтобы редкие задачи не пропали за accuracy."""
    predicted = logits.argmax(dim=1)
    values = []
    for label in range(classes):
        mask = labels == label
        if int(mask.sum()) == 0:
            continue
        values.append(float((predicted[mask] == label).float().mean()))
    return sum(values) / max(1, len(values))


def calibrate_policy(probabilities: torch.Tensor, labels: torch.Tensor) -> tuple[dict, dict]:
    """Подбирает порог на validation: ошибочный принятый ответ хуже уточнения."""
    top, indices = probabilities.topk(2, dim=1)
    best: tuple | None = None
    for confidence in (0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.94, 0.97, 0.99):
        for margin in (0.01, 0.05, 0.10, 0.20, 0.30, 0.45):
            accepted = (top[:, 0] >= confidence) & ((top[:, 0] - top[:, 1]) >= margin)
            correct = int((accepted & (indices[:, 0] == labels)).sum())
            wrong = int((accepted & (indices[:, 0] != labels)).sum())
            accepted_count = correct + wrong
            precision = correct / accepted_count if accepted_count else 0.0
            # Сначала ищем конфигурацию без ошибок, затем максимальное покрытие.
            rank = (wrong == 0, precision, correct, -confidence, -margin)
            if best is None or rank > best[0]:
                best = (
                    rank,
                    {"confidence": confidence, "margin": margin},
                    {
                        "accepted_correct": correct,
                        "accepted_wrong": wrong,
                        "accepted_total": accepted_count,
                        "validation_records": len(labels),
                        "precision": precision,
                        "coverage": accepted_count / len(labels),
                    },
                )
    if best is None or best[2]["accepted_total"] == 0:
        raise RuntimeError("Не удалось подобрать непустой порог LMS-политики.")
    return best[1], best[2]


def _per_task_metrics(
    logits: torch.Tensor, labels: torch.Tensor, task_types: list[str]
) -> dict[str, dict]:
    """Готовит раздельные метрики, чтобы не скрывать редкие классы."""
    predicted = logits.argmax(dim=1)
    result = {}
    for index, task_type in enumerate(task_types):
        mask = labels == index
        support = int(mask.sum())
        correct = int((predicted[mask] == index).sum()) if support else 0
        result[task_type] = {
            "support": support,
            "correct": correct,
            "recall": correct / support if support else 0.0,
        }
    return result


def _answer_fact_score(answer: str, fact: str) -> float:
    """Оценивает, какой факт поддерживает эталонный ответ в обучающей строке."""
    answer_words = {word for word in normalize(answer).split() if len(word) > 1}
    fact_words = {word for word in normalize(fact).split() if len(word) > 1}
    if not answer_words or not fact_words:
        return 0.0
    overlap = answer_words & fact_words
    numeric_overlap = {word for word in overlap if any(char.isdigit() for char in word)}
    return len(overlap) / len(answer_words) + len(numeric_overlap) * 0.5


def _fact_pair_rows(rows: list) -> tuple[list[tuple[str, str, str]], torch.Tensor, list[tuple[int, int, set[int]]]]:
    """Строит supervision выбора факта из training-ответов, не используя test."""
    triples: list[tuple[str, str, str]] = []
    labels: list[float] = []
    groups: list[tuple[int, int, set[int]]] = []
    limits = {
        "context_summary": 3,
        "source_attribution": 2,
        "clarify_ambiguity": 2,
    }
    supported = {
        "clarify_ambiguity",
        "context_summary",
        "direct_context_answer",
        "prompt_injection_resistance",
        "site_navigation",
        "source_attribution",
    }
    for row in rows:
        if row.task_type not in supported:
            continue
        facts = [
            fact.strip()
            for fact in FACT_SPLITTER.split(" ".join(row.context.split()))
            if fact.strip()
        ]
        if not facts:
            continue
        scores = [_answer_fact_score(row.assistant, fact) for fact in facts]
        best = max(scores, default=0.0)
        if best <= 0.0:
            continue
        limit = limits.get(row.task_type, 1)
        # Несколько равноподдержанных фактов нужны только для сводки и
        # уточнения. Берём именно самые релевантные, а не первые в документе.
        selected = [
            index
            for index, score in sorted(
                enumerate(scores), key=lambda item: (-item[1], item[0])
            )
            if score >= best * 0.45 and score > 0.0
        ][:limit]
        if not selected:
            continue
        start = len(triples)
        triples.extend((row.user, fact, row.task_type) for fact in facts)
        labels.extend(float(index in selected) for index in range(len(facts)))
        groups.append((start, len(triples), {start + index for index in selected}))
    if not triples or not any(labels):
        raise RuntimeError("Не удалось получить обучающие пары выбора LMS-фактов.")
    return triples, torch.tensor(labels, dtype=torch.float32), groups


def _ranking_recall(scores: torch.Tensor, groups: list[tuple[int, int, set[int]]]) -> float:
    """Проверяет, попадает ли релевантный факт на первое место внутри контекста."""
    hits = 0
    for start, end, positive in groups:
        top = start + int(scores[start:end].argmax())
        hits += top in positive
    return hits / max(1, len(groups))


def _calibrate_fact_threshold(scores: torch.Tensor, labels: torch.Tensor) -> tuple[float, dict]:
    """Выбирает порог факта с приоритетом точности, а не принудительной выдачи."""
    best: tuple | None = None
    for threshold in (0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85):
        selected = scores >= threshold
        true_positive = int((selected & (labels == 1)).sum())
        false_positive = int((selected & (labels == 0)).sum())
        selected_total = true_positive + false_positive
        precision = true_positive / selected_total if selected_total else 0.0
        recall = true_positive / max(1, int((labels == 1).sum()))
        rank = (precision >= 0.98, precision, recall, -threshold)
        if best is None or rank > best[0]:
            best = (
                rank,
                threshold,
                {
                    "true_positive": true_positive,
                    "false_positive": false_positive,
                    "precision": precision,
                    "recall": recall,
                },
            )
    if best is None or best[2]["true_positive"] == 0:
        raise RuntimeError("Не удалось подобрать порог выбора LMS-факта.")
    return best[1], best[2]


def train_fact_selector(train_rows: list, validation_rows: list) -> tuple[dict, dict, float, dict, list[dict]]:
    """Обучает вторую собственную сеть выбирать факт внутри уже данного контекста."""
    train_pairs, train_labels, _ = _fact_pair_rows(train_rows)
    validation_pairs, validation_labels, validation_groups = _fact_pair_rows(validation_rows)
    encoder = LmsFactHashEncoder(dimension=512)
    train_x = encoder.transform(train_pairs)
    validation_x = encoder.transform(validation_pairs)
    positive = float((train_labels == 1).sum())
    negative = float((train_labels == 0).sum())
    if positive <= 0 or negative <= 0:
        raise RuntimeError("Для выбора LMS-фактов нужны положительные и отрицательные пары.")
    model = LmsFactNetwork(input_dim=encoder.dimension)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.004, weight_decay=0.01)
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(negative / positive))
    loader = DataLoader(
        TensorDataset(train_x, train_labels),
        batch_size=512,
        shuffle=True,
        generator=torch.Generator().manual_seed(84),
    )
    best_rank: tuple | None = None
    best_state: dict[str, torch.Tensor] | None = None
    history = []
    stale = 0
    for epoch in range(1, 41):
        model.train()
        train_loss_total = 0.0
        seen = 0
        for batch_x, batch_y in loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(batch_x)
            loss = criterion(logits, batch_y)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss_total += float(loss.detach()) * len(batch_y)
            seen += len(batch_y)
        model.eval()
        with torch.inference_mode():
            validation_logits = model(validation_x)
            validation_loss = float(criterion(validation_logits, validation_labels))
            validation_scores = validation_logits.sigmoid()
            ranking_recall = _ranking_recall(validation_scores, validation_groups)
        rank = (ranking_recall, -validation_loss)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_state = {key: value.detach().clone() for key, value in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
        history.append(
            {
                "epoch": epoch,
                "train_loss": train_loss_total / max(1, seen),
                "validation_loss": validation_loss,
                "validation_top_fact_recall": ranking_recall,
            }
        )
        if epoch % 10 == 0:
            print(
                f"lms_fact_epoch={epoch}, top_fact_recall={ranking_recall:.4f}",
                flush=True,
            )
        if stale >= 12:
            break
    if best_state is None:
        raise RuntimeError("Обучение выбора LMS-фактов не создало состояния.")
    model.load_state_dict(best_state)
    model.eval()
    with torch.inference_mode():
        validation_scores = model(validation_x).sigmoid()
    threshold, calibration = _calibrate_fact_threshold(validation_scores, validation_labels)
    metrics = {
        "training_pairs": len(train_pairs),
        "validation_pairs": len(validation_pairs),
        "training_positive_pairs": int(positive),
        "validation_positive_pairs": int((validation_labels == 1).sum()),
        "validation_top_fact_recall": _ranking_recall(validation_scores, validation_groups),
        "calibration": calibration,
    }
    return (
        {key: value.detach().clone() for key, value in model.state_dict().items()},
        {"input_dim": encoder.dimension},
        threshold,
        metrics,
        history,
    )


def train(
    output: Path,
    epochs: int = 60,
    feature_dimension: int = 1024,
    hidden_dimension: int = 96,
) -> dict:
    """Строит кандидата, не перезаписывая рабочие веб-веса."""
    if output.exists():
        raise ValueError(f"Каталог LMS-кандидата уже существует: {output}")
    if epochs < 1 or feature_dimension < 128 or hidden_dimension < 16:
        raise ValueError("Некорректные параметры LMS-обучения.")
    torch.set_num_threads(min(8, max(1, os.cpu_count() or 1)))
    torch.manual_seed(42)
    splits, partition = build_context_grouped_splits(seed=42)
    task_types = sorted(TASK_TYPES)
    train_rows = splits["train"]
    validation_rows = splits["validation"]
    # Вектор содержит и обычные текстовые признаки, и компактные признаки
    # структуры запроса/контекста. Это меняет процедуру обобщения, не добавляя
    # искусственные копии диалогов в исходный датасет.
    encoder = LmsHashEncoder(feature_dimension, include_structural=True)
    train_x = encoder.transform([(row.user, row.context) for row in train_rows])
    validation_x = encoder.transform([(row.user, row.context) for row in validation_rows])
    train_y = _labels(train_rows, task_types)
    validation_y = _labels(validation_rows, task_types)
    config = {
        "input_dim": feature_dimension,
        "classes": len(task_types),
        "hidden_dim": hidden_dimension,
    }
    model = LmsIntentNetwork(**config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.003, weight_decay=0.015)
    # В уникальном наборе некоторые классы редкие. Вес по обратному корню
    # поддерживает их, но не превращает несколько примеров в единственный фокус.
    counts = train_y.bincount(minlength=len(task_types)).float()
    class_weights = counts.sqrt().reciprocal()
    class_weights /= class_weights.mean()
    loader = DataLoader(
        TensorDataset(train_x, train_y),
        batch_size=256,
        shuffle=True,
        generator=torch.Generator().manual_seed(42),
    )
    best_rank: tuple | None = None
    best_state: dict[str, torch.Tensor] | None = None
    best_epoch = 0
    best_validation_loss = float("inf")
    history = []
    stale_epochs = 0
    for epoch in range(1, epochs + 1):
        model.train()
        train_loss_total = 0.0
        train_rows_seen = 0
        for batch_x, batch_y in loader:
            optimizer.zero_grad(set_to_none=True)
            # Лёгкий dropout признаков не даёт запомнить один шаблонный маркер.
            logits = model(nn.functional.dropout(batch_x, p=0.08, training=True))
            loss = nn.functional.cross_entropy(logits, batch_y, weight=class_weights)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss_total += float(loss.detach()) * len(batch_y)
            train_rows_seen += len(batch_y)
        model.eval()
        with torch.inference_mode():
            validation_logits = model(validation_x)
            validation_loss = float(nn.functional.cross_entropy(validation_logits, validation_y))
            validation_accuracy = float(
                (validation_logits.argmax(dim=1) == validation_y).float().mean()
            )
            validation_macro_recall = _macro_recall(
                validation_logits, validation_y, len(task_types)
            )
        rank = (validation_macro_recall, validation_accuracy, -validation_loss)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_state = {key: value.detach().clone() for key, value in model.state_dict().items()}
            best_epoch = epoch
            best_validation_loss = validation_loss
            stale_epochs = 0
        else:
            stale_epochs += 1
        history.append(
            {
                "epoch": epoch,
                "train_loss": train_loss_total / max(1, train_rows_seen),
                "validation_loss": validation_loss,
                "validation_accuracy": validation_accuracy,
                "validation_macro_recall": validation_macro_recall,
            }
        )
        if epoch % 10 == 0:
            print(
                f"lms_epoch={epoch}, validation_accuracy={validation_accuracy:.4f}, "
                f"macro_recall={validation_macro_recall:.4f}",
                flush=True,
            )
        if stale_epochs >= 18:
            break
    if best_state is None:
        raise RuntimeError("LMS-обучение не создало состояния модели.")
    model.load_state_dict(best_state)
    model.eval()
    with torch.inference_mode():
        validation_logits = model(validation_x)
        probabilities = validation_logits.softmax(dim=1)
    thresholds, calibration = calibrate_policy(probabilities, validation_y)
    validation_metrics = {
        "accuracy": float((validation_logits.argmax(dim=1) == validation_y).float().mean()),
        "macro_recall": _macro_recall(validation_logits, validation_y, len(task_types)),
        "per_task": _per_task_metrics(validation_logits, validation_y, task_types),
    }
    # Второй этап обучается только на train и validation. Test остаётся
    # нетронутым для финальной независимой проверки кандидата.
    del train_x, validation_x
    gc.collect()
    (
        fact_model_state,
        fact_model_config,
        fact_threshold,
        fact_validation_metrics,
        fact_history,
    ) = train_fact_selector(train_rows, validation_rows)
    metadata = {
        "format_version": 1,
        "model_version": MODEL_VERSION,
        "architecture": "hashed-context-question-mlp-confidence-fused-semantic-structural-guard-general-purpose-fact-extractive-policy-with-fact-ranker",
        "model_config": config,
        "feature_dimension": feature_dimension,
        "structural_features_version": 8,
        "structured_policy_version": 8,
        "decoder_version": 8,
        "runtime_source_sha256": file_fingerprint(Path(__file__).with_name("lms_reliable.py")),
        "fact_feature_dimension": fact_model_config["input_dim"],
        "fact_model_config": fact_model_config,
        "fact_threshold": fact_threshold,
        "task_types": task_types,
        "thresholds": thresholds,
        "seed": 42,
        "selected_epoch": best_epoch,
        "validation_loss": best_validation_loss,
        "calibration": calibration,
        "validation_metrics": validation_metrics,
        "fact_validation_metrics": fact_validation_metrics,
        "partition": partition,
        "source_file_sha256": {
            split: file_fingerprint(path) for split, path in SPLIT_FILES.items()
        },
        "training_records": len(train_rows),
        "validation_records": len(validation_rows),
        "class_weights": class_weights.tolist(),
    }
    output.mkdir(parents=True)
    torch.save(
        {
            **metadata,
            "model_state": best_state,
            "fact_model_state": fact_model_state,
        },
        output / "lms.pt",
    )
    (output / "metrics.json").write_text(
        json.dumps(
            {**metadata, "history": history, "fact_history": fact_history},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--feature-dimension", type=int, default=1024)
    parser.add_argument("--hidden-dimension", type=int, default=96)
    args = parser.parse_args()
    try:
        train(args.output, args.epochs, args.feature_dimension, args.hidden_dimension)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"LMS-обучение остановлено: {error}") from error


if __name__ == "__main__":
    main()
