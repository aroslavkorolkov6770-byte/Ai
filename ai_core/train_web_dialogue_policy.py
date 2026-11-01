"""Обучает локальный policy-классификатор по dialogue web training seed.

Обучение использует только ``conversation.role == user`` и ``browse_label``.
Оно не пытается предсказывать ``ideal_response``: 200 синтетических записей
для этого слишком мало, а готовые ответы, evidence, system и tool-поля не
должны становиться целью свободной генерации.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import torch
from torch import nn

from .internet_router import RouterFeatureEncoder, RouterNetwork
from .web_dialogue_dataset import (
    BROWSE_LABELS,
    WebDialogueDatasetError,
    file_sha256,
    iter_web_dialogue_policy_examples,
)
from .web_dialogue_policy import (
    DEFAULT_WEB_DIALOGUE_POLICY_ARTIFACT,
    FORMAT_VERSION,
    LABELS,
    SEARCH_LABELS,
    WebDialoguePolicy,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_PATH = (
    ROOT / "evaluation" / "results" / "dialogue-web-seed-audit" / "dialogue_web_seed_audit.json"
)
SEED = 42


def _load_verified_audit(audit_path: Path, archive_path: Path) -> dict[str, Any]:
    """Связывает обучение только с архивом, уже прошедшим полный аудит."""
    if not audit_path.is_file():
        raise FileNotFoundError(f"Не найден отчёт аудита: {audit_path}")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    decision = audit.get("training_decision", {})
    if not decision.get("web_policy_classifier_eligible"):
        raise ValueError("Аудит не разрешил обучать web-dialogue-policy.")
    recorded_hash = audit.get("container", {}).get("archive_sha256")
    if not isinstance(recorded_hash, str) or recorded_hash != file_sha256(archive_path):
        raise ValueError(
            "SHA-256 архива не совпадает с аудитом. Сначала повторите безопасный аудит."
        )
    return audit


def _load_split(archive_path: Path, split: str) -> tuple[list[str], torch.Tensor]:
    """Берёт только user-контекст и сохранённую метку решения о поиске."""
    rows = list(iter_web_dialogue_policy_examples(archive_path, split))
    if not rows:
        raise ValueError(f"Web-dialogue-policy split {split!r} пуст.")
    labels = {row.browse_label for row in rows}
    if labels != set(BROWSE_LABELS):
        raise ValueError(
            f"Split {split!r} не содержит полный набор browse_label: {sorted(labels)}."
        )
    texts = [row.prompt for row in rows]
    if any(not text.strip() for text in texts):
        raise ValueError(f"Split {split!r} содержит пустую user-реплику.")
    targets = torch.tensor(
        [LABELS.index(row.browse_label) for row in rows],
        dtype=torch.long,
    )
    return texts, targets


def _batch_inputs(
    encoder: RouterFeatureEncoder,
    texts: list[str],
    indices: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, list[float]]:
    """Кодирует выбранный batch в разреженные признаки EmbeddingBag."""
    return encoder.encode_batch([texts[index] for index in indices.tolist()])


def _inference(
    model: RouterNetwork,
    encoder: RouterFeatureEncoder,
    texts: list[str],
    targets: torch.Tensor,
    batch_size: int,
) -> tuple[torch.Tensor, list[float], float, float]:
    """Считает вероятности и loss на одном заранее выделенном split."""
    probabilities: list[torch.Tensor] = []
    coverage: list[float] = []
    loss_total = 0.0
    correct = 0
    model.eval()
    with torch.inference_mode():
        for start in range(0, len(texts), batch_size):
            stop = min(start + batch_size, len(texts))
            indices = torch.arange(start, stop)
            features, offsets, weights, current_coverage = _batch_inputs(
                encoder, texts, indices
            )
            logits = model(features, offsets, weights)
            batch_targets = targets[start:stop]
            loss_total += float(
                nn.functional.cross_entropy(logits, batch_targets, reduction="sum")
            )
            correct += int((logits.argmax(dim=1) == batch_targets).sum())
            probabilities.append(logits.softmax(dim=-1))
            coverage.extend(current_coverage)
    return (
        torch.cat(probabilities),
        coverage,
        loss_total / len(texts),
        correct / len(texts),
    )


def _safe_divide(numerator: int, denominator: int) -> float:
    """Не допускает деления на ноль в маленьком validation split."""
    return numerator / denominator if denominator else 0.0


def _class_metrics(probabilities: torch.Tensor, targets: torch.Tensor) -> dict[str, Any]:
    """Возвращает accuracy и метрики каждой policy-метки."""
    predicted = probabilities.argmax(dim=1)
    per_label: dict[str, dict[str, float | int]] = {}
    for index, label in enumerate(LABELS):
        expected_label = targets == index
        predicted_label = predicted == index
        true_positive = int((expected_label & predicted_label).sum())
        false_positive = int((~expected_label & predicted_label).sum())
        false_negative = int((expected_label & ~predicted_label).sum())
        precision = _safe_divide(true_positive, true_positive + false_positive)
        recall = _safe_divide(true_positive, true_positive + false_negative)
        per_label[label] = {
            "support": int(expected_label.sum()),
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(_safe_divide(2 * precision * recall, precision + recall), 6),
        }
    return {
        "accuracy": round(float((predicted == targets).float().mean()), 6),
        "per_label": per_label,
    }


def _search_metrics(
    probabilities: torch.Tensor,
    coverage: list[float],
    targets: torch.Tensor,
    thresholds: dict[str, float],
) -> dict[str, float | int]:
    """Оценивает конкретно риск автоматического выхода в интернет."""
    top_values, top_indices = torch.topk(probabilities, k=2, dim=1)
    predicted_labels = [LABELS[int(index)] for index in top_indices[:, 0]]
    accepted = torch.tensor(
        [
            label in SEARCH_LABELS
            and float(confidence) >= thresholds["search_confidence"]
            and float(confidence - second) >= thresholds["minimum_margin"]
            and current_coverage >= thresholds["minimum_coverage"]
            for label, confidence, second, current_coverage in zip(
                predicted_labels,
                top_values[:, 0],
                top_values[:, 1],
                coverage,
            )
        ],
        dtype=torch.bool,
    )
    expected_search = torch.tensor(
        [LABELS[int(index)] in SEARCH_LABELS for index in targets],
        dtype=torch.bool,
    )
    must_index = LABELS.index("must_browse")
    should_index = LABELS.index("should_browse")
    true_positive = int((accepted & expected_search).sum())
    false_positive = int((accepted & ~expected_search).sum())
    false_negative = int((~accepted & expected_search).sum())
    precision = _safe_divide(true_positive, true_positive + false_positive)
    recall = _safe_divide(true_positive, true_positive + false_negative)
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": int((~accepted & ~expected_search).sum()),
        "predicted_search": int(accepted.sum()),
        "expected_search": int(expected_search.sum()),
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(_safe_divide(2 * precision * recall, precision + recall), 6),
        "must_browse_recall": round(
            _safe_divide(int((accepted & (targets == must_index)).sum()), int((targets == must_index).sum())),
            6,
        ),
        "should_browse_recall": round(
            _safe_divide(int((accepted & (targets == should_index)).sum()), int((targets == should_index).sum())),
            6,
        ),
    }


def _calibrate(
    probabilities: torch.Tensor,
    coverage: list[float],
    targets: torch.Tensor,
) -> tuple[dict[str, float], dict[str, float | int]]:
    """Выбирает пороги только по validation и в приоритете избегает ложного поиска."""
    candidates: list[
        tuple[tuple[float, ...], dict[str, float], dict[str, float | int]]
    ] = []
    for confidence in (0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.90):
        for margin in (0.0, 0.05, 0.10, 0.20, 0.30):
            for minimum_coverage in (0.0, 0.05, 0.10, 0.20):
                thresholds = {
                    "search_confidence": confidence,
                    "minimum_margin": margin,
                    "minimum_coverage": minimum_coverage,
                }
                metrics = _search_metrics(probabilities, coverage, targets, thresholds)
                precision = float(metrics["precision"])
                recall = float(metrics["recall"])
                must_recall = float(metrics["must_browse_recall"])
                accepted = precision >= 0.80 and recall >= 0.50 and must_recall >= 0.50
                # Среди безопасных конфигураций сначала сохраняем точность,
                # затем покрытие обязательного поиска. Если пороги равны по
                # качеству, выбираем более осторожную конфигурацию.
                rank = (
                    1.0 if accepted else 0.0,
                    precision if accepted else float(metrics["f1"]),
                    must_recall,
                    recall,
                    float(metrics["f1"]),
                    confidence,
                    margin,
                    minimum_coverage,
                )
                candidates.append((rank, thresholds, metrics))
    _, thresholds, metrics = max(candidates, key=lambda item: item[0])
    if (
        float(metrics["precision"]) < 0.80
        or float(metrics["recall"]) < 0.50
        or float(metrics["must_browse_recall"]) < 0.50
    ):
        raise RuntimeError(
            "Кандидат web-dialogue-policy не прошёл validation-gate для безопасного поиска."
        )
    return thresholds, metrics


def _test_gate(
    classification: dict[str, Any],
    search: dict[str, float | int],
) -> None:
    """Не позволяет активировать слабый модельный маршрут на API."""
    if float(classification["accuracy"]) < 0.55:
        raise RuntimeError("Кандидат не прошёл test-gate общей классификации policy.")
    if (
        float(search["precision"]) < 0.75
        or float(search["recall"]) < 0.50
        or float(search["must_browse_recall"]) < 0.50
    ):
        raise RuntimeError("Кандидат не прошёл test-gate автоматического поиска.")


def train(
    archive_path: Path,
    audit_path: Path,
    output: Path,
    epochs: int = 80,
    batch_size: int = 64,
) -> dict[str, Any]:
    """Обучает и сохраняет checkpoint только после закрытого test-gate."""
    if output.exists():
        raise ValueError(f"Каталог кандидата уже существует: {output}")
    if epochs < 1 or batch_size < 4:
        raise ValueError("epochs должен быть положительным, batch_size — не меньше 4.")
    archive_path = archive_path.expanduser().resolve()
    audit_path = audit_path.expanduser().resolve()
    verified_audit = _load_verified_audit(audit_path, archive_path)

    torch.set_num_threads(2)
    torch.manual_seed(SEED)
    train_texts, train_targets = _load_split(archive_path, "train")
    validation_texts, validation_targets = _load_split(archive_path, "validation")
    test_texts, test_targets = _load_split(archive_path, "test")
    encoder = RouterFeatureEncoder.fit(train_texts, max_features=4_000)
    model_config = {
        "vocabulary_size": len(encoder.vocabulary),
        "embedding_dim": 32,
        "hidden_dim": 32,
        "classes": len(LABELS),
    }
    model = RouterNetwork(**model_config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.006, weight_decay=0.02)
    class_weights = train_targets.bincount(minlength=len(LABELS)).float().reciprocal()
    class_weights /= class_weights.mean()
    best_loss = float("inf")
    best_epoch = 0
    best_state: dict[str, torch.Tensor] | None = None
    stale_epochs = 0
    history: list[dict[str, float | int]] = []

    for epoch in range(1, epochs + 1):
        model.train()
        order = torch.randperm(len(train_texts))
        train_loss = 0.0
        for start in range(0, len(train_texts), batch_size):
            selected = order[start:start + batch_size]
            features, offsets, weights, _ = _batch_inputs(encoder, train_texts, selected)
            optimizer.zero_grad(set_to_none=True)
            logits = model(features, offsets, weights)
            loss = nn.functional.cross_entropy(logits, train_targets[selected], weight=class_weights)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss += float(loss.detach()) * len(selected)
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
                "train_loss": round(train_loss / len(train_texts), 6),
                "validation_loss": round(validation_loss, 6),
                "validation_accuracy": round(validation_accuracy, 6),
            }
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
        print(
            f"epoch={epoch}, validation_accuracy={validation_accuracy:.4f}, "
            f"validation_loss={validation_loss:.4f}",
            flush=True,
        )
        if stale_epochs >= 12:
            break

    if best_state is None:
        raise RuntimeError("Web-dialogue-policy не сохранил ни одного состояния модели.")
    model.load_state_dict(best_state)
    validation_probabilities, validation_coverage, validation_loss, validation_accuracy = _inference(
        model,
        encoder,
        validation_texts,
        validation_targets,
        batch_size,
    )
    thresholds, validation_search = _calibrate(
        validation_probabilities,
        validation_coverage,
        validation_targets,
    )
    validation_classification = _class_metrics(validation_probabilities, validation_targets)
    test_probabilities, test_coverage, test_loss, test_accuracy = _inference(
        model,
        encoder,
        test_texts,
        test_targets,
        batch_size,
    )
    test_classification = _class_metrics(test_probabilities, test_targets)
    test_search = _search_metrics(test_probabilities, test_coverage, test_targets, thresholds)
    _test_gate(test_classification, test_search)

    metadata: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "model_version": "web-dialogue-policy-v1.0",
        "architecture": "own-sparse-features-embeddingbag-mlp",
        "labels": list(LABELS),
        "search_labels": sorted(SEARCH_LABELS),
        "model_config": model_config,
        "vocabulary": encoder.vocabulary,
        "idf": encoder.idf,
        "feature_stopwords": sorted(encoder.stopwords),
        "thresholds": thresholds,
        "seed": SEED,
        "archive_sha256": verified_audit["container"]["archive_sha256"],
        "audit_sha256": file_sha256(audit_path),
        "safe_input_fields": ["conversation.role=user", "browse_label"],
        "excluded_archive_fields": [
            "assistant",
            "system",
            "tool",
            "ideal_response",
            "evidence",
            "search_queries",
            "decision_rationale",
        ],
        "selected_epoch": best_epoch,
        "training_records": len(train_texts),
        "validation_records": len(validation_texts),
        "test_records": len(test_texts),
        "validation_loss": round(validation_loss, 6),
        "validation_accuracy": round(validation_accuracy, 6),
        "validation_classification": validation_classification,
        "validation_search_metrics": validation_search,
        "test_loss": round(test_loss, 6),
        "test_accuracy": round(test_accuracy, 6),
        "test_classification": test_classification,
        "test_search_metrics": test_search,
        "history": history,
    }
    output.mkdir(parents=True)
    torch.save({**metadata, "model_state": best_state}, output / "policy.pt")
    metrics = {
        key: value
        for key, value in metadata.items()
        if key not in {"vocabulary", "idf", "history"}
    }
    (output / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Проверка загружается тем же классом, который использует работающий API.
    WebDialoguePolicy.load(output / "policy.pt")
    return metrics


def main() -> None:
    """Запускает обучение из командной строки и печатает безопасные метрики."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_WEB_DIALOGUE_POLICY_ARTIFACT.parent)
    parser.add_argument("--epochs", type=int, default=80)
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
        WebDialogueDatasetError,
    ) as error:
        raise SystemExit(f"Обучение web-dialogue-policy остановлено: {error}") from error


if __name__ == "__main__":
    main()
