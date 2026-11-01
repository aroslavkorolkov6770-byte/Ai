"""Обучает отдельный собственный классификатор автоматического интернет-маршрута."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch import nn

from .chat_dataset import (
    ChatDatasetAuditError,
    file_sha256,
    iter_internet_router_examples,
)
from .internet_router import (
    DEFAULT_ROUTER_ARTIFACT,
    LABELS,
    InternetRouter,
    RouterFeatureEncoder,
    RouterNetwork,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_PATH = (
    ROOT / "evaluation" / "results" / "chat-ai-500k-audit" / "chat_ai_500k_audit.json"
)


def _load_verified_audit(audit_path: Path, archive_path: Path) -> dict:
    """Связывает обучение с уже проверенным точным байтовым архивом."""
    if not audit_path.is_file():
        raise FileNotFoundError(f"Не найден отчёт аудита: {audit_path}")
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    if not payload.get("training_decision", {}).get("internet_router_eligible"):
        raise ValueError("Аудит не разрешил обучать internet-router.")
    recorded_hash = payload.get("container", {}).get("archive_sha256")
    current_hash = file_sha256(archive_path)
    if recorded_hash != current_hash:
        raise ValueError(
            "SHA-256 архива изменился после аудита. Сначала запустите аудит повторно."
        )
    return payload


def _load_split(archive_path: Path, split: str) -> tuple[list[str], torch.Tensor]:
    """Загружает только текст пользователя и независимую метку split."""
    rows = list(iter_internet_router_examples(archive_path, split))
    if not rows:
        raise ValueError(f"Internet-router split {split!r} пуст.")
    labels = {row.label for row in rows}
    if labels - set(LABELS):
        raise ValueError(f"Неизвестные метки internet-router: {sorted(labels - set(LABELS))}")
    if set(LABELS) - labels:
        raise ValueError(f"Split {split!r} потерял одну из обязательных меток.")
    texts = [row.user for row in rows]
    target = torch.tensor([LABELS.index(row.label) for row in rows], dtype=torch.long)
    return texts, target


def _batch_inputs(
    encoder: RouterFeatureEncoder,
    texts: list[str],
    indices: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, list[float]]:
    """Создаёт один разреженный batch из выбранных индексов."""
    batch_texts = [texts[index] for index in indices.tolist()]
    return encoder.encode_batch(batch_texts)


def _inference(
    model: RouterNetwork,
    encoder: RouterFeatureEncoder,
    texts: list[str],
    targets: torch.Tensor,
    batch_size: int,
) -> tuple[torch.Tensor, list[float], float, float]:
    """Считает вероятности и метрики без создания плотной матрицы всего split."""
    probabilities: list[torch.Tensor] = []
    coverage: list[float] = []
    loss_total = 0.0
    correct = 0
    model.eval()
    with torch.inference_mode():
        for start in range(0, len(texts), batch_size):
            stop = min(start + batch_size, len(texts))
            batch_indices = torch.arange(start, stop)
            features, offsets, weights, batch_coverage = _batch_inputs(
                encoder, texts, batch_indices
            )
            logits = model(features, offsets, weights)
            batch_targets = targets[start:stop]
            loss_total += float(nn.functional.cross_entropy(logits, batch_targets, reduction="sum"))
            correct += int((logits.argmax(dim=1) == batch_targets).sum())
            probabilities.append(logits.softmax(dim=-1))
            coverage.extend(batch_coverage)
    return (
        torch.cat(probabilities),
        coverage,
        loss_total / len(texts),
        correct / len(texts),
    )


def _web_metrics(
    probabilities: torch.Tensor,
    coverage: list[float],
    targets: torch.Tensor,
    confidence_threshold: float,
    coverage_threshold: float,
) -> dict[str, float | int]:
    """Оценивает именно риск лишнего поиска, а не только общую accuracy."""
    use_web_index = LABELS.index("USE_WEB")
    predicted = (
        (probabilities[:, use_web_index] >= confidence_threshold)
        & (torch.tensor(coverage) >= coverage_threshold)
    )
    expected = targets == use_web_index
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
        "predicted_use_web": int(predicted.sum()),
    }


def _calibrate(
    probabilities: torch.Tensor,
    coverage: list[float],
    targets: torch.Tensor,
) -> tuple[dict[str, float], dict[str, float | int]]:
    """Выбирает осторожный порог только по validation, не открывая test."""
    candidates: list[tuple[tuple[float, ...], dict[str, float], dict[str, float | int]]] = []
    for confidence in (0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 0.975, 0.99):
        for minimum_coverage in (0.0, 0.05, 0.10, 0.20):
            metrics = _web_metrics(
                probabilities,
                coverage,
                targets,
                confidence,
                minimum_coverage,
            )
            precision = float(metrics["precision"])
            recall = float(metrics["recall"])
            # Неверный поиск вреднее пропущенного: он задерживает ответ и
            # приводит к нерелевантной странице. Сначала требуем точность.
            accepted_precision = 1.0 if precision >= 0.99 else 0.0
            accepted_recall = 1.0 if recall >= 0.50 else 0.0
            rank = (
                accepted_precision,
                accepted_recall,
                recall if accepted_precision else float(metrics["f1"]),
                precision,
                # При одинаковом качестве на validation выбираем не самый
                # низкий порог, а рабочую осторожную точку. Это защищает
                # неизвестные формулировки, которых не было в синтетическом
                # наборе, от лишнего медленного интернет-поиска.
                -abs(confidence - 0.90),
                -abs(minimum_coverage - 0.05),
            )
            candidates.append(
                (
                    rank,
                    {
                        "use_web_confidence": confidence,
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
    epochs: int = 24,
    batch_size: int = 512,
) -> dict:
    """Создаёт новый кандидат; активный чат не перезаписывается до проверки."""
    if output.exists():
        raise ValueError(f"Каталог кандидата уже существует: {output}")
    if epochs < 1 or batch_size < 32:
        raise ValueError("epochs должен быть положительным, batch_size — не меньше 32.")
    archive_path = archive_path.expanduser().resolve()
    audit_path = audit_path.expanduser().resolve()
    verified_audit = _load_verified_audit(audit_path, archive_path)

    torch.set_num_threads(2)
    torch.manual_seed(42)
    train_texts, train_targets = _load_split(archive_path, "train")
    valid_texts, valid_targets = _load_split(archive_path, "validation")
    test_texts, test_targets = _load_split(archive_path, "test")
    encoder = RouterFeatureEncoder.fit(train_texts)
    config = {
        "vocabulary_size": len(encoder.vocabulary),
        "embedding_dim": 48,
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
            batch_targets = train_targets[selected]
            optimizer.zero_grad(set_to_none=True)
            logits = model(features, offsets, weights)
            loss = nn.functional.cross_entropy(logits, batch_targets, weight=class_weights)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += float(loss.detach()) * len(selected)

        _, _, validation_loss, validation_accuracy = _inference(
            model, encoder, valid_texts, valid_targets, batch_size
        )
        history.append(
            {
                "epoch": epoch,
                "train_loss": round(total_loss / len(train_texts), 6),
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
        if stale_epochs >= 5:
            break

    if best_state is None:
        raise RuntimeError("Internet-router не сохранил ни одного состояния модели.")
    model.load_state_dict(best_state)
    valid_probabilities, valid_coverage, valid_loss, valid_accuracy = _inference(
        model, encoder, valid_texts, valid_targets, batch_size
    )
    thresholds, validation_web_metrics = _calibrate(
        valid_probabilities,
        valid_coverage,
        valid_targets,
    )
    test_probabilities, test_coverage, test_loss, test_accuracy = _inference(
        model, encoder, test_texts, test_targets, batch_size
    )
    test_web_metrics = _web_metrics(
        test_probabilities,
        test_coverage,
        test_targets,
        thresholds["use_web_confidence"],
        thresholds["minimum_coverage"],
    )
    if float(test_web_metrics["precision"]) < 0.98 or float(test_web_metrics["recall"]) < 0.50:
        raise RuntimeError(
            "Кандидат internet-router не прошёл качество на закрытом test split."
        )

    metadata = {
        "format_version": 1,
        "model_version": "internet-router-v1.0",
        "architecture": "own-sparse-features-embeddingbag-mlp",
        "labels": list(LABELS),
        "model_config": config,
        "vocabulary": encoder.vocabulary,
        "idf": encoder.idf,
        "feature_stopwords": sorted(encoder.stopwords),
        "thresholds": thresholds,
        "seed": 42,
        "archive_sha256": verified_audit["container"]["archive_sha256"],
        "audit_sha256": file_sha256(audit_path),
        "selected_epoch": best_epoch,
        "training_records": len(train_texts),
        "validation_records": len(valid_texts),
        "test_records": len(test_texts),
        "validation_loss": round(valid_loss, 6),
        "validation_accuracy": round(valid_accuracy, 6),
        "validation_web_metrics": validation_web_metrics,
        "test_loss": round(test_loss, 6),
        "test_accuracy": round(test_accuracy, 6),
        "test_web_metrics": test_web_metrics,
        "history": history,
    }
    output.mkdir(parents=True)
    torch.save({**metadata, "model_state": best_state}, output / "router.pt")
    metrics = {key: value for key, value in metadata.items() if key not in {"vocabulary", "idf"}}
    (output / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Проверяем checkpoint тем же классом, которым будет пользоваться API.
    InternetRouter.load(output / "router.pt")
    return metrics


def main() -> None:
    """Обрабатывает параметры командной строки и печатает метрики кандидата."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_ROUTER_ARTIFACT.parent)
    parser.add_argument("--epochs", type=int, default=24)
    parser.add_argument("--batch-size", type=int, default=512)
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
        raise SystemExit(f"Обучение internet-router остановлено: {error}") from error


if __name__ == "__main__":
    main()
