"""Обучает локальный классификатор обычного диалога без генерации из архива.

Этот скрипт намеренно не учит сеть повторять поле ``assistant``. В архиве
готовые ответы для одинаковых диалоговых условий могут противоречить друг
другу, а свободное декодирование из такого массива вернуло бы неустойчивый
текст. Вместо этого сеть обучается только на пользовательской реплике и
стабильной категории поведения: ``DIALOGUE`` или ``TASK``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import torch
from torch import nn

from .chat_dataset import (
    ChatDatasetAuditError,
    ConversationPolicyExample,
    file_sha256,
    iter_conversation_policy_examples,
)
from .conversation_brain import (
    DEFAULT_CONVERSATION_BRAIN_ARTIFACT,
    FORMAT_VERSION,
    LABELS,
    ConversationBrain,
)
from .internet_router import RouterFeatureEncoder, RouterNetwork, normalize_route_text


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_PATH = (
    ROOT / "evaluation" / "results" / "chat-ai-500k-audit" / "chat_ai_500k_audit.json"
)
SEED = 42


@dataclass(frozen=True)
class TrainingRow:
    """Одна уникальная реплика для train/validation/test без текста ответа."""

    user: str
    label: str


def _stable_hash(*parts: str) -> str:
    """Строит детерминированный ключ, не раскрывая текст в отчёте."""
    payload = "\u0000".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_verified_audit(audit_path: Path, archive_path: Path) -> dict:
    """Связывает обучение с тем же байтово проверенным архивом."""
    if not audit_path.is_file():
        raise FileNotFoundError(f"Не найден отчёт аудита: {audit_path}")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    recorded_hash = audit.get("container", {}).get("archive_sha256")
    current_hash = file_sha256(archive_path)
    if not isinstance(recorded_hash, str) or recorded_hash != current_hash:
        raise ValueError(
            "SHA-256 архива не совпадает с аудитом. Сначала повторите безопасный аудит."
        )
    if not audit.get("chat_splits"):
        raise ValueError("Отчёт аудита не содержит проверенных chat split.")
    return audit


def _unique_policy_rows(archive_path: Path) -> tuple[list[TrainingRow], dict[str, int]]:
    """Дедуплицирует реплики и проверяет, что их политика не меняется.

    Исходные split архива не используются: один и тот же пользовательский
    текст встречается в разных частях набора. Поэтому сначала образуется
    единственная запись на нормализованную реплику, после чего будет выполнено
    собственное независимое разбиение.
    """
    unique: dict[str, ConversationPolicyExample] = {}
    conflicting_policy_rows = 0
    for row in iter_conversation_policy_examples(archive_path):
        key = normalize_route_text(row.user)
        if not key:
            raise ChatDatasetAuditError("В chat policy найден пустой user после нормализации.")
        earlier = unique.get(key)
        policy = (row.category, row.subtype, row.should_use_web)
        if earlier is not None:
            earlier_policy = (earlier.category, earlier.subtype, earlier.should_use_web)
            if earlier_policy != policy:
                conflicting_policy_rows += 1
                continue
        else:
            unique[key] = row
    if conflicting_policy_rows:
        raise ChatDatasetAuditError(
            "Одинаковые пользовательские реплики имеют конфликтующую policy-разметку."
        )

    dialogue = [row for row in unique.values() if row.category == "communication"]
    tasks = [row for row in unique.values() if row.category != "communication"]
    if len(dialogue) < 50 or len(tasks) < len(dialogue):
        raise ValueError("В архиве недостаточно уникальных примеров для conversation-brain.")

    # Классы делаются равными, чтобы модель не научилась всегда отвечать TASK
    # из-за преобладания internet-routing сообщений в исходном архиве.
    selected_tasks = sorted(
        tasks,
        key=lambda row: _stable_hash("conversation-brain-v1-task", normalize_route_text(row.user)),
    )[:len(dialogue)]
    rows = [
        *(TrainingRow(row.user, "DIALOGUE") for row in dialogue),
        *(TrainingRow(row.user, "TASK") for row in selected_tasks),
    ]
    if len({normalize_route_text(row.user) for row in rows}) != len(rows):
        raise ValueError("Conversation-brain получил дубль пользовательской реплики.")
    return rows, {
        "unique_user_messages": len(unique),
        "unique_dialogue_messages": len(dialogue),
        "balanced_task_messages": len(selected_tasks),
        "source_conflicting_policy_rows": conflicting_policy_rows,
    }


def _split_rows(rows: list[TrainingRow]) -> dict[str, list[TrainingRow]]:
    """Создаёт стратифицированное групповое разбиение по уникальной реплике."""
    grouped = {label: [] for label in LABELS}
    for row in rows:
        if row.label not in grouped:
            raise ValueError(f"Неизвестная метка conversation-brain: {row.label}")
        grouped[row.label].append(row)

    splits = {"train": [], "validation": [], "test": []}
    for label, label_rows in grouped.items():
        if len(label_rows) < 30:
            raise ValueError(f"Для метки {label} недостаточно уникальных примеров.")
        ordered = sorted(
            label_rows,
            key=lambda row: _stable_hash("conversation-brain-v1-split", label, normalize_route_text(row.user)),
        )
        validation_count = max(1, round(len(ordered) * 0.10))
        test_count = max(1, round(len(ordered) * 0.10))
        train_count = len(ordered) - validation_count - test_count
        if train_count < 1:
            raise ValueError(f"Невозможно разрезать метку {label} на три независимые части.")
        splits["train"].extend(ordered[:train_count])
        splits["validation"].extend(ordered[train_count:train_count + validation_count])
        splits["test"].extend(ordered[train_count + validation_count:])

    seen: set[str] = set()
    for split, split_rows in splits.items():
        if not split_rows:
            raise ValueError(f"Split conversation-brain {split} пуст.")
        keys = {normalize_route_text(row.user) for row in split_rows}
        if len(keys) != len(split_rows) or seen & keys:
            raise ValueError("Найдена утечка пользовательской реплики между split.")
        seen.update(keys)
    return splits


def _texts_and_targets(rows: list[TrainingRow]) -> tuple[list[str], torch.Tensor]:
    """Переводит безопасные записи в текст и индексы классов."""
    labels = {row.label for row in rows}
    if labels != set(LABELS):
        raise ValueError("Каждый split должен содержать TASK и DIALOGUE.")
    return (
        [row.user for row in rows],
        torch.tensor([LABELS.index(row.label) for row in rows], dtype=torch.long),
    )


def _batch_inputs(
    encoder: RouterFeatureEncoder,
    texts: list[str],
    indices: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, list[float]]:
    """Собирает один компактный EmbeddingBag-batch."""
    return encoder.encode_batch([texts[index] for index in indices.tolist()])


def _inference(
    model: RouterNetwork,
    encoder: RouterFeatureEncoder,
    texts: list[str],
    targets: torch.Tensor,
    batch_size: int,
) -> tuple[torch.Tensor, list[float], float, float]:
    """Считает вероятность, покрытие, loss и accuracy без утечки train."""
    probabilities: list[torch.Tensor] = []
    coverage: list[float] = []
    loss_total = 0.0
    correct = 0
    model.eval()
    with torch.inference_mode():
        for start in range(0, len(texts), batch_size):
            stop = min(start + batch_size, len(texts))
            selected = torch.arange(start, stop)
            features, offsets, weights, current_coverage = _batch_inputs(
                encoder,
                texts,
                selected,
            )
            logits = model(features, offsets, weights)
            current_targets = targets[start:stop]
            loss_total += float(
                nn.functional.cross_entropy(logits, current_targets, reduction="sum")
            )
            correct += int((logits.argmax(dim=1) == current_targets).sum())
            probabilities.append(logits.softmax(dim=-1))
            coverage.extend(current_coverage)
    return (
        torch.cat(probabilities),
        coverage,
        loss_total / len(texts),
        correct / len(texts),
    )


def _dialogue_metrics(
    probabilities: torch.Tensor,
    coverage: list[float],
    targets: torch.Tensor,
    confidence_threshold: float,
    coverage_threshold: float,
) -> dict[str, float | int]:
    """Измеряет риск выдать разговорный ответ вместо прикладного маршрута."""
    dialogue_index = LABELS.index("DIALOGUE")
    predicted = (
        (probabilities[:, dialogue_index] >= confidence_threshold)
        & (torch.tensor(coverage) >= coverage_threshold)
    )
    expected = targets == dialogue_index
    true_positive = int((predicted & expected).sum())
    false_positive = int((predicted & ~expected).sum())
    false_negative = int((~predicted & expected).sum())
    true_negative = int((~predicted & ~expected).sum())
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    f1 = 2 * precision * recall / max(0.000001, precision + recall)
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "predicted_dialogue": int(predicted.sum()),
    }


def _calibrate(
    probabilities: torch.Tensor,
    coverage: list[float],
    targets: torch.Tensor,
) -> tuple[dict[str, float], dict[str, float | int]]:
    """Подбирает консервативный порог только на validation split."""
    candidates: list[tuple[tuple[float, ...], dict[str, float], dict[str, float | int]]] = []
    for confidence in (0.70, 0.80, 0.85, 0.90, 0.95, 0.975, 0.99):
        for minimum_coverage in (0.0, 0.05, 0.10, 0.20):
            metrics = _dialogue_metrics(
                probabilities,
                coverage,
                targets,
                confidence,
                minimum_coverage,
            )
            precision = float(metrics["precision"])
            recall = float(metrics["recall"])
            # Неверный разговорный ответ на вопрос о факте вреднее безопасного
            # перехода к следующему маршруту, поэтому точность имеет приоритет.
            rank = (
                1.0 if precision >= 0.95 else 0.0,
                1.0 if recall >= 0.50 else 0.0,
                recall if precision >= 0.95 else float(metrics["f1"]),
                precision,
                -abs(confidence - 0.90),
                -abs(minimum_coverage - 0.05),
            )
            candidates.append(
                (
                    rank,
                    {
                        "dialogue_confidence": confidence,
                        "minimum_coverage": minimum_coverage,
                    },
                    metrics,
                )
            )
    _, thresholds, metrics = max(candidates, key=lambda item: item[0])
    return thresholds, metrics


def train(
    archive_path: Path,
    audit_path: Path,
    output: Path,
    epochs: int = 32,
    batch_size: int = 64,
) -> dict:
    """Обучает и принимает checkpoint только после независимого test split."""
    if output.exists():
        raise ValueError(f"Каталог результата уже существует: {output}")
    if epochs < 1 or batch_size < 16:
        raise ValueError("epochs должен быть положительным, batch_size — не меньше 16.")
    archive_path = archive_path.expanduser().resolve()
    audit_path = audit_path.expanduser().resolve()
    audit = _load_verified_audit(audit_path, archive_path)
    rows, data_summary = _unique_policy_rows(archive_path)
    splits = _split_rows(rows)
    train_texts, train_targets = _texts_and_targets(splits["train"])
    validation_texts, validation_targets = _texts_and_targets(splits["validation"])
    test_texts, test_targets = _texts_and_targets(splits["test"])

    torch.set_num_threads(2)
    torch.manual_seed(SEED)
    encoder = RouterFeatureEncoder.fit(train_texts, max_features=5_000)
    config = {
        "vocabulary_size": len(encoder.vocabulary),
        "embedding_dim": 32,
        "hidden_dim": 24,
        "classes": len(LABELS),
    }
    model = RouterNetwork(**config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.004, weight_decay=0.01)
    class_weights = train_targets.bincount(minlength=len(LABELS)).float().reciprocal()
    class_weights /= class_weights.mean()
    best_loss = float("inf")
    best_state: dict[str, torch.Tensor] | None = None
    best_epoch = 0
    stale_epochs = 0
    history: list[dict[str, float | int]] = []

    for epoch in range(1, epochs + 1):
        model.train()
        order = torch.randperm(len(train_texts))
        total_loss = 0.0
        for start in range(0, len(train_texts), batch_size):
            selected = order[start:start + batch_size]
            features, offsets, weights, _ = _batch_inputs(encoder, train_texts, selected)
            targets = train_targets[selected]
            optimizer.zero_grad(set_to_none=True)
            logits = model(features, offsets, weights)
            loss = nn.functional.cross_entropy(logits, targets, weight=class_weights)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += float(loss.detach()) * len(selected)

        _, _, validation_loss, validation_accuracy = _inference(
            model,
            encoder,
            validation_texts,
            validation_targets,
            batch_size,
        )
        history.append(
            {
                "epoch": epoch,
                "train_loss": round(total_loss / len(train_texts), 6),
                "validation_loss": round(validation_loss, 6),
                "validation_accuracy": round(validation_accuracy, 6),
            }
        )
        print(
            f"epoch={epoch}, validation_accuracy={validation_accuracy:.4f}, "
            f"validation_loss={validation_loss:.4f}",
            flush=True,
        )
        if validation_loss < best_loss - 0.00001:
            best_loss = validation_loss
            best_epoch = epoch
            stale_epochs = 0
            best_state = {
                name: value.detach().clone() for name, value in model.state_dict().items()
            }
        else:
            stale_epochs += 1
        if stale_epochs >= 6:
            break

    if best_state is None:
        raise RuntimeError("Conversation-brain не сохранил ни одного состояния модели.")
    model.load_state_dict(best_state)
    validation_probabilities, validation_coverage, validation_loss, validation_accuracy = _inference(
        model,
        encoder,
        validation_texts,
        validation_targets,
        batch_size,
    )
    thresholds, validation_dialogue_metrics = _calibrate(
        validation_probabilities,
        validation_coverage,
        validation_targets,
    )
    test_probabilities, test_coverage, test_loss, test_accuracy = _inference(
        model,
        encoder,
        test_texts,
        test_targets,
        batch_size,
    )
    test_dialogue_metrics = _dialogue_metrics(
        test_probabilities,
        test_coverage,
        test_targets,
        thresholds["dialogue_confidence"],
        thresholds["minimum_coverage"],
    )
    if (
        test_accuracy < 0.75
        or float(test_dialogue_metrics["precision"]) < 0.80
        or float(test_dialogue_metrics["recall"]) < 0.50
    ):
        raise RuntimeError(
            "Кандидат conversation-brain не прошёл независимый test split."
        )

    metadata = {
        "format_version": FORMAT_VERSION,
        "model_version": "conversation-brain-v1.0",
        "architecture": "own-sparse-features-embeddingbag-mlp",
        "labels": list(LABELS),
        "model_config": config,
        "vocabulary": encoder.vocabulary,
        "idf": encoder.idf,
        "feature_stopwords": sorted(encoder.stopwords),
        "thresholds": thresholds,
        "seed": SEED,
        "archive_sha256": audit["container"]["archive_sha256"],
        "audit_sha256": file_sha256(audit_path),
        "data_policy": {
            "assistant_text_used": False,
            "system_text_used": False,
            "context_text_used": False,
            "web_context_used": False,
            "source_policy_label": "category=communication",
            "task_negative_sample": "deterministic_balanced_non_communication",
        },
        "data_summary": data_summary,
        "selected_epoch": best_epoch,
        "training_records": len(train_texts),
        "validation_records": len(validation_texts),
        "test_records": len(test_texts),
        "validation_loss": round(validation_loss, 6),
        "validation_accuracy": round(validation_accuracy, 6),
        "validation_dialogue_metrics": validation_dialogue_metrics,
        "test_loss": round(test_loss, 6),
        "test_accuracy": round(test_accuracy, 6),
        "test_dialogue_metrics": test_dialogue_metrics,
        "history": history,
    }
    output.mkdir(parents=True)
    torch.save({**metadata, "model_state": best_state}, output / "brain.pt")
    metrics = {key: value for key, value in metadata.items() if key not in {"vocabulary", "idf"}}
    (output / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ConversationBrain.load(output / "brain.pt")
    return metrics


def main() -> None:
    """Обрабатывает CLI-параметры и печатает метрики без текстов датасета."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_CONVERSATION_BRAIN_ARTIFACT.parent)
    parser.add_argument("--epochs", type=int, default=32)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    try:
        metrics = train(args.archive, args.audit, args.output, args.epochs, args.batch_size)
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
    except (
        FileNotFoundError,
        OSError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        ChatDatasetAuditError,
    ) as error:
        raise SystemExit(f"Обучение conversation-brain остановлено: {error}") from error


if __name__ == "__main__":
    main()
