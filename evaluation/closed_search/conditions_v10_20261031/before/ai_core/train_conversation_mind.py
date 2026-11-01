"""Обучает локальный conversation-mind на уникальных планах общения.

Модуль намеренно не обучает посимвольный или словарный генератор на поле
``assistant`` из пользовательского архива. В нём слишком мало уникальных
готовых ответов, поэтому такое обучение только закрепило бы повторяющиеся
заученные фразы. Вместо этого небольшая собственная сеть определяет план
разговора, а ``conversation_mind.py`` собирает краткий ответ из проверенных
смысловых частей с учётом истории текущего диалога.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch import nn

from .chat_dataset import ChatDatasetAuditError, file_sha256
from .conversation_mind import (
    DEFAULT_CONVERSATION_MIND_ARTIFACT,
    FORMAT_VERSION,
    LABELS,
    ConversationMind,
)
from .conversation_mind_dataset import (
    CONVERSATION_PLANS,
    SUBTYPE_TO_PLAN,
    ConversationMindDatasetError,
    build_conversation_mind_examples,
)
from .internet_router import RouterFeatureEncoder, RouterNetwork, normalize_route_text


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_PATH = (
    ROOT
    / "evaluation"
    / "results"
    / "conversation-mind-v2-data-audit-final"
    / "conversation_mind_data_audit.json"
)
SEED = 42


@dataclass(frozen=True)
class TrainingRow:
    """Одна дедуплицированная реплика без чужого готового ответа."""

    user: str
    plan: str


def _stable_hash(*parts: str) -> str:
    """Создаёт воспроизводимый ключ для split без публикации текста."""
    return hashlib.sha256("\u0000".join(parts).encode("utf-8")).hexdigest()


def _load_verified_audit(audit_path: Path, archive_path: Path) -> dict[str, Any]:
    """Проверяет, что обучение связано с тем же проверенным ZIP-архивом."""
    if not audit_path.is_file():
        raise FileNotFoundError(f"Не найден отчёт аудита: {audit_path}")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if not isinstance(audit, dict):
        raise ValueError("Аудит conversation-mind должен быть JSON-объектом.")
    recorded_hash = audit.get("archive_sha256")
    current_hash = file_sha256(archive_path)
    if not isinstance(recorded_hash, str) or recorded_hash != current_hash:
        raise ValueError(
            "SHA-256 архива не совпадает с аудитом. Сначала выполните безопасный аудит заново."
        )
    decision = audit.get("training_decision")
    if not isinstance(decision, dict) or not decision.get("conversation_policy_eligible"):
        raise ValueError("Аудит не разрешает обучать conversation-mind policy.")
    if decision.get("free_response_generator_eligible") is not False:
        raise ValueError(
            "Аудит должен явно запрещать свободный генератор для этого архива."
        )
    return audit


def _unique_rows(archive_path: Path) -> tuple[list[TrainingRow], dict[str, Any]]:
    """Строит обучающие строки только из проверенных уникальных user-реплик."""
    examples, audit = build_conversation_mind_examples(archive_path)
    rows = [TrainingRow(example.user, example.plan) for example in examples]
    fingerprints = [normalize_route_text(row.user) for row in rows]
    if len(fingerprints) != len(set(fingerprints)):
        raise ConversationMindDatasetError(
            "Conversation-mind получил повтор пользовательской реплики после дедупликации."
        )
    plan_counts = {label: sum(row.plan == label for row in rows) for label in LABELS}
    if set(plan_counts) != set(CONVERSATION_PLANS):
        raise ConversationMindDatasetError("В conversation-mind отсутствует один из планов.")
    if min(plan_counts.values()) < 20:
        raise ConversationMindDatasetError(
            "Для устойчивого плана ответа недостаточно уникальных реплик."
        )
    return rows, {
        "safe_unique_examples": len(rows),
        "plan_counts": plan_counts,
        "dataset_audit": audit,
    }


def _split_rows(rows: list[TrainingRow]) -> dict[str, list[TrainingRow]]:
    """Делает независимые train/validation/test split без утечки текста."""
    grouped = {label: [] for label in LABELS}
    for row in rows:
        if row.plan not in grouped:
            raise ValueError(f"Неизвестный план conversation-mind: {row.plan}")
        grouped[row.plan].append(row)

    splits: dict[str, list[TrainingRow]] = {"train": [], "validation": [], "test": []}
    for label, label_rows in grouped.items():
        if len(label_rows) < 20:
            raise ValueError(f"Для плана {label} недостаточно уникальных реплик.")
        ordered = sorted(
            label_rows,
            key=lambda row: _stable_hash(
                "conversation-mind-v2-split", label, normalize_route_text(row.user)
            ),
        )
        validation_count = max(3, round(len(ordered) * 0.15))
        test_count = max(3, round(len(ordered) * 0.15))
        train_count = len(ordered) - validation_count - test_count
        if train_count < 12:
            raise ValueError(f"После split для плана {label} осталось слишком мало train-строк.")
        splits["train"].extend(ordered[:train_count])
        splits["validation"].extend(
            ordered[train_count:train_count + validation_count]
        )
        splits["test"].extend(ordered[train_count + validation_count:])

    seen: set[str] = set()
    for split_name, split_rows in splits.items():
        if not split_rows:
            raise ValueError(f"Split conversation-mind {split_name} пуст.")
        texts = {normalize_route_text(row.user) for row in split_rows}
        if len(texts) != len(split_rows) or seen & texts:
            raise ValueError("Обнаружена утечка пользовательской реплики между split.")
        if {row.plan for row in split_rows} != set(LABELS):
            raise ValueError(f"Split conversation-mind {split_name} не содержит все планы.")
        seen.update(texts)
    return splits


def _texts_and_targets(rows: list[TrainingRow]) -> tuple[list[str], torch.Tensor]:
    """Переводит планы в индексы нейросети с фиксированным порядком меток."""
    return (
        [row.user for row in rows],
        torch.tensor([LABELS.index(row.plan) for row in rows], dtype=torch.long),
    )


def _batch_inputs(
    encoder: RouterFeatureEncoder,
    texts: list[str],
    selected: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, list[float]]:
    """Кодирует компактный batch, не создавая плотную матрицу признаков."""
    return encoder.encode_batch([texts[index] for index in selected.tolist()])


def _inference(
    model: RouterNetwork,
    encoder: RouterFeatureEncoder,
    texts: list[str],
    targets: torch.Tensor,
    batch_size: int,
) -> tuple[torch.Tensor, list[float], float, float]:
    """Считает вероятность, покрытие, loss и обычную точность на одном split."""
    probabilities: list[torch.Tensor] = []
    coverage: list[float] = []
    loss_total = 0.0
    correct = 0
    model.eval()
    with torch.inference_mode():
        for start in range(0, len(texts), batch_size):
            stop = min(start + batch_size, len(texts))
            selected = torch.arange(start, stop)
            features, offsets, weights, batch_coverage = _batch_inputs(
                encoder, texts, selected
            )
            logits = model(features, offsets, weights)
            batch_targets = targets[start:stop]
            loss_total += float(
                nn.functional.cross_entropy(logits, batch_targets, reduction="sum")
            )
            correct += int((logits.argmax(dim=1) == batch_targets).sum())
            probabilities.append(logits.softmax(dim=-1))
            coverage.extend(batch_coverage)
    return (
        torch.cat(probabilities),
        coverage,
        loss_total / len(texts),
        correct / len(texts),
    )


def _classification_metrics(
    probabilities: torch.Tensor,
    targets: torch.Tensor,
) -> dict[str, Any]:
    """Считает macro-F1 и показатели каждого плана без внешней библиотеки."""
    predicted = probabilities.argmax(dim=1)
    per_plan: dict[str, dict[str, float | int]] = {}
    f1_values: list[float] = []
    for index, label in enumerate(LABELS):
        expected_label = targets == index
        predicted_label = predicted == index
        true_positive = int((expected_label & predicted_label).sum())
        false_positive = int((~expected_label & predicted_label).sum())
        false_negative = int((expected_label & ~predicted_label).sum())
        precision = true_positive / max(1, true_positive + false_positive)
        recall = true_positive / max(1, true_positive + false_negative)
        f1 = 2 * precision * recall / max(0.000001, precision + recall)
        f1_values.append(f1)
        per_plan[label] = {
            "support": int(expected_label.sum()),
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        }
    return {
        "accuracy": round(float((predicted == targets).float().mean()), 6),
        "macro_f1": round(sum(f1_values) / len(f1_values), 6),
        "per_plan": per_plan,
    }


def _accepted_metrics(
    probabilities: torch.Tensor,
    coverage: list[float],
    targets: torch.Tensor,
    confidence_threshold: float,
    margin_threshold: float,
    coverage_threshold: float,
) -> dict[str, float | int]:
    """Измеряет качество только тех решений, которые дойдут до ответа в чате."""
    values, indices = torch.topk(probabilities, k=2, dim=1)
    accepted = (
        (values[:, 0] >= confidence_threshold)
        & ((values[:, 0] - values[:, 1]) >= margin_threshold)
        & (torch.tensor(coverage) >= coverage_threshold)
    )
    correct = indices[:, 0] == targets
    accepted_count = int(accepted.sum())
    true_positive = int((accepted & correct).sum())
    precision = true_positive / max(1, accepted_count)
    selected_accuracy = precision
    route_coverage = accepted_count / len(targets)
    return {
        "accepted": accepted_count,
        "correct_accepted": true_positive,
        "precision": round(precision, 6),
        "selected_accuracy": round(selected_accuracy, 6),
        "coverage": round(route_coverage, 6),
    }


def _calibrate(
    probabilities: torch.Tensor,
    coverage: list[float],
    targets: torch.Tensor,
) -> tuple[dict[str, float], dict[str, float | int]]:
    """Подбирает пороги только на validation, сохраняя достаточное покрытие."""
    candidates: list[
        tuple[tuple[float, ...], dict[str, float], dict[str, float | int]]
    ] = []
    for confidence in (0.40, 0.50, 0.60, 0.70, 0.80, 0.90):
        for margin in (0.0, 0.05, 0.10, 0.20, 0.30):
            for minimum_coverage in (0.0, 0.05, 0.10, 0.20):
                metrics = _accepted_metrics(
                    probabilities,
                    coverage,
                    targets,
                    confidence,
                    margin,
                    minimum_coverage,
                )
                precision = float(metrics["precision"])
                accepted_coverage = float(metrics["coverage"])
                # Для непроверенного текста лучше не выдать лишний шаблон,
                # чем ошибочно представить его как понимание собеседника.
                rank = (
                    1.0 if precision >= 0.85 else 0.0,
                    1.0 if accepted_coverage >= 0.50 else 0.0,
                    precision,
                    accepted_coverage,
                    -confidence,
                    -margin,
                    -minimum_coverage,
                )
                candidates.append(
                    (
                        rank,
                        {
                            "intent_confidence": confidence,
                            "minimum_margin": margin,
                            "minimum_coverage": minimum_coverage,
                        },
                        metrics,
                    )
                )
    _, thresholds, metrics = max(candidates, key=lambda item: item[0])
    return thresholds, metrics


def train(
    archive_path: Path,
    audit_path: Path = DEFAULT_AUDIT_PATH,
    output: Path = DEFAULT_CONVERSATION_MIND_ARTIFACT.parent,
    epochs: int = 48,
    batch_size: int = 48,
) -> dict[str, Any]:
    """Обучает candidate и сохраняет его только после независимой проверки."""
    if output.exists():
        raise ValueError(f"Каталог результата уже существует: {output}")
    if epochs < 8 or batch_size < 8:
        raise ValueError("epochs должен быть не меньше 8, batch_size — не меньше 8.")
    archive_path = archive_path.expanduser().resolve()
    audit_path = audit_path.expanduser().resolve()
    verified_audit = _load_verified_audit(audit_path, archive_path)
    rows, data_summary = _unique_rows(archive_path)
    if data_summary["dataset_audit"]["archive_sha256"] != verified_audit["archive_sha256"]:
        raise ValueError("Повторный разбор архива не совпал с аудиторским SHA-256.")
    splits = _split_rows(rows)
    train_texts, train_targets = _texts_and_targets(splits["train"])
    validation_texts, validation_targets = _texts_and_targets(splits["validation"])
    test_texts, test_targets = _texts_and_targets(splits["test"])

    torch.set_num_threads(2)
    torch.manual_seed(SEED)
    encoder = RouterFeatureEncoder.fit(train_texts, max_features=5_000)
    config = {
        "vocabulary_size": len(encoder.vocabulary),
        "embedding_dim": 40,
        "hidden_dim": 32,
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
        if stale_epochs >= 8:
            break

    if best_state is None:
        raise RuntimeError("Conversation-mind не сохранил ни одного состояния модели.")
    model.load_state_dict(best_state)
    validation_probabilities, validation_coverage, validation_loss, validation_accuracy = _inference(
        model,
        encoder,
        validation_texts,
        validation_targets,
        batch_size,
    )
    thresholds, validation_accepted_metrics = _calibrate(
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
    test_classification_metrics = _classification_metrics(test_probabilities, test_targets)
    test_accepted_metrics = _accepted_metrics(
        test_probabilities,
        test_coverage,
        test_targets,
        thresholds["intent_confidence"],
        thresholds["minimum_margin"],
        thresholds["minimum_coverage"],
    )
    if (
        test_accuracy < 0.65
        or float(test_classification_metrics["macro_f1"]) < 0.60
        or float(test_accepted_metrics["precision"]) < 0.75
        or float(test_accepted_metrics["coverage"]) < 0.40
    ):
        raise RuntimeError(
            "Кандидат conversation-mind не прошёл независимый test split. "
            "Активный чат не изменён."
        )

    metadata: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "model_version": "conversation-mind-v2.0",
        "architecture": "own-sparse-features-embeddingbag-mlp-plus-controlled-composer",
        "labels": list(LABELS),
        "model_config": config,
        "vocabulary": encoder.vocabulary,
        "idf": encoder.idf,
        "feature_stopwords": sorted(encoder.stopwords),
        "thresholds": thresholds,
        "seed": SEED,
        "archive_sha256": verified_audit["archive_sha256"],
        "audit_sha256": file_sha256(audit_path),
        "data_policy": {
            "assistant_text_used": False,
            "system_text_used": False,
            "context_text_used": False,
            "web_context_used": False,
            "source_policy_target": "communication_subtype_to_stable_plan",
            "subtype_to_plan": SUBTYPE_TO_PLAN,
            "response_text_source": "controlled_local_composer",
        },
        "data_summary": {
            "safe_unique_examples": data_summary["safe_unique_examples"],
            "plan_counts": data_summary["plan_counts"],
            "source_communication_records": data_summary["dataset_audit"][
                "source_communication_records"
            ],
            "duplicate_rows_removed": data_summary["dataset_audit"]["duplicate_rows_removed"],
        },
        "selected_epoch": best_epoch,
        "training_records": len(train_texts),
        "validation_records": len(validation_texts),
        "test_records": len(test_texts),
        "validation_loss": round(validation_loss, 6),
        "validation_accuracy": round(validation_accuracy, 6),
        "validation_accepted_metrics": validation_accepted_metrics,
        "test_loss": round(test_loss, 6),
        "test_accuracy": round(test_accuracy, 6),
        "test_classification_metrics": test_classification_metrics,
        "test_accepted_metrics": test_accepted_metrics,
        "history": history,
    }
    output.mkdir(parents=True)
    torch.save({**metadata, "model_state": best_state}, output / "mind.pt")
    metrics = {
        key: value
        for key, value in metadata.items()
        if key not in {"vocabulary", "idf"}
    }
    (output / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ConversationMind.load(output / "mind.pt")
    return metrics


def main() -> None:
    """Запускает обучение и печатает метрики без текстов датасета."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT_PATH)
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_CONVERSATION_MIND_ARTIFACT.parent
    )
    parser.add_argument("--epochs", type=int, default=48)
    parser.add_argument("--batch-size", type=int, default=48)
    args = parser.parse_args()
    try:
        metrics = train(
            args.archive,
            args.audit,
            args.output,
            args.epochs,
            args.batch_size,
        )
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
    except (
        FileNotFoundError,
        OSError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        ChatDatasetAuditError,
        ConversationMindDatasetError,
    ) as error:
        raise SystemExit(f"Обучение conversation-mind остановлено: {error}") from error


if __name__ == "__main__":
    main()
