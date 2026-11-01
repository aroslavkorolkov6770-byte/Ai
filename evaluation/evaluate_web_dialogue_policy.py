"""Независимо проверяет установленный web-dialogue-policy на test split.

Скрипт читает только пользовательские реплики и метки из уже проверенного ZIP.
Тексты диалогов, готовые ответы и URL в отчёт не попадают.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_core.web_dialogue_dataset import (
    BROWSE_LABELS,
    WebDialogueDatasetError,
    audit_web_dialogue_dataset,
    file_sha256,
    iter_web_dialogue_policy_examples,
)
from ai_core.web_dialogue_policy import SEARCH_LABELS, WebDialoguePolicy


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE = Path(
    r"C:\Users\KROLL\Downloads\Telegram Desktop\dialogue_web_training_seed_v1.zip"
)
DEFAULT_CHECKPOINT = ROOT / "model_artifacts" / "web_dialogue_policy" / "policy.pt"
DEFAULT_OUTPUT = ROOT / "evaluation" / "results" / "web-dialogue-policy-v1-acceptance"


def _safe_divide(numerator: int, denominator: int) -> float:
    """Считает отношение без аварии на маленьком проверочном наборе."""
    return numerator / denominator if denominator else 0.0


def _metrics(rows: list[Any], policy: WebDialoguePolicy) -> dict[str, Any]:
    """Измеряет production-решение policy, а не внутренние logits тренера."""
    predicted_labels: list[str] = []
    accepted_search: list[bool] = []
    expected_labels = [row.browse_label for row in rows]
    for row in rows:
        prediction = policy.predict(row.prompt)
        predicted_labels.append(prediction.browse_label)
        accepted_search.append(prediction.should_search)

    per_label: dict[str, dict[str, float | int]] = {}
    for label in BROWSE_LABELS:
        expected = [value == label for value in expected_labels]
        predicted = [value == label for value in predicted_labels]
        true_positive = sum(left and right for left, right in zip(expected, predicted))
        false_positive = sum(not left and right for left, right in zip(expected, predicted))
        false_negative = sum(left and not right for left, right in zip(expected, predicted))
        precision = _safe_divide(true_positive, true_positive + false_positive)
        recall = _safe_divide(true_positive, true_positive + false_negative)
        per_label[label] = {
            "support": sum(expected),
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(_safe_divide(2 * precision * recall, precision + recall), 6),
        }

    expected_search = [label in SEARCH_LABELS for label in expected_labels]
    true_positive = sum(left and right for left, right in zip(accepted_search, expected_search))
    false_positive = sum(left and not right for left, right in zip(accepted_search, expected_search))
    false_negative = sum(not left and right for left, right in zip(accepted_search, expected_search))
    precision = _safe_divide(true_positive, true_positive + false_positive)
    recall = _safe_divide(true_positive, true_positive + false_negative)
    must_expected = [label == "must_browse" for label in expected_labels]
    should_expected = [label == "should_browse" for label in expected_labels]
    return {
        "records": len(rows),
        "classification_accuracy": round(
            _safe_divide(
                sum(left == right for left, right in zip(expected_labels, predicted_labels)),
                len(rows),
            ),
            6,
        ),
        "per_label": per_label,
        "search_gate": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "true_negative": sum(
                not left and not right for left, right in zip(accepted_search, expected_search)
            ),
            "predicted_search": sum(accepted_search),
            "expected_search": sum(expected_search),
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(_safe_divide(2 * precision * recall, precision + recall), 6),
            "must_browse_recall": round(
                _safe_divide(
                    sum(left and right for left, right in zip(accepted_search, must_expected)),
                    sum(must_expected),
                ),
                6,
            ),
            "should_browse_recall": round(
                _safe_divide(
                    sum(left and right for left, right in zip(accepted_search, should_expected)),
                    sum(should_expected),
                ),
                6,
            ),
        },
    }


def _accepted(metrics: dict[str, Any]) -> bool:
    """Повторяет release-gate в понятной форме, не подгоняя его под результат."""
    search = metrics["search_gate"]
    return (
        float(metrics["classification_accuracy"]) >= 0.55
        and float(search["precision"]) >= 0.75
        and float(search["recall"]) >= 0.50
        and float(search["must_browse_recall"]) >= 0.50
    )


def _markdown(report: dict[str, Any]) -> str:
    """Готовит компактный отчёт без фрагментов исходных диалогов."""
    search = report["metrics"]["search_gate"]
    status = "пройдена" if report["accepted"] else "не пройдена"
    return "\n".join(
        [
            "# Приёмка web-dialogue-policy v1.0",
            "",
            f"Проверка {status} на закрытом `test` split.",
            "",
            "## Результат",
            "",
            f"- Записей test: {report['metrics']['records']}.",
            f"- Accuracy пяти меток: {report['metrics']['classification_accuracy']:.2%}.",
            f"- Точность автоматического поиска: {search['precision']:.2%}.",
            f"- Полнота автоматического поиска: {search['recall']:.2%}.",
            f"- Полнота `must_browse`: {search['must_browse_recall']:.2%}.",
            f"- Ложных автоматических поисков: {search['false_positive']}.",
            "",
            "## Граница применения",
            "",
            "Это не универсальный генератор и не источник фактов. Policy только "
            "добавляет осторожный сигнал к существующим правилам веб-маршрута. "
            "Погода, время, явная просьба о поиске и уже работающий internet-router "
            "сохраняют приоритет. Готовые ответы, system, tool, evidence и ссылки "
            "из ZIP не используются для обучения или ответа.",
            "",
        ]
    )


def evaluate(archive_path: Path, checkpoint_path: Path) -> dict[str, Any]:
    """Проверяет связь checkpoint с архивом и оценивает закрытый test split."""
    audit = audit_web_dialogue_dataset(archive_path)
    if not audit["training_decision"]["web_policy_classifier_eligible"]:
        raise ValueError("Аудит не разрешил использовать этот набор для policy.")
    policy = WebDialoguePolicy.load(checkpoint_path)
    # Метаданные checkpoint проверяем с weights_only=False не нужно: policy уже
    # проверил безопасно веса, а SHA входного архива записана в checkpoint.
    import torch

    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    if checkpoint.get("archive_sha256") != file_sha256(archive_path):
        raise ValueError("Checkpoint обучен на другом байтовом архиве.")
    rows = list(iter_web_dialogue_policy_examples(archive_path, "test"))
    if {row.browse_label for row in rows} != set(BROWSE_LABELS):
        raise ValueError("Test split потерял обязательную policy-метку.")
    metrics = _metrics(rows, policy)
    return {
        "format_version": 1,
        "model_version": policy.version,
        "archive_sha256": audit["container"]["archive_sha256"],
        "checkpoint_sha256": file_sha256(checkpoint_path),
        "thresholds": policy.thresholds,
        "metrics": metrics,
        "accepted": _accepted(metrics),
        "release_gate": {
            "minimum_classification_accuracy": 0.55,
            "minimum_search_precision": 0.75,
            "minimum_search_recall": 0.50,
            "minimum_must_browse_recall": 0.50,
        },
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
    }


def main() -> None:
    """Запускает проверку и сохраняет JSON/Markdown, не перезаписывая отчёт."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError(f"Каталог отчёта уже существует: {args.output}")
        report = evaluate(args.archive, args.checkpoint)
        args.output.mkdir(parents=True)
        (args.output / "web_dialogue_policy_acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (args.output / "WEB_DIALOGUE_POLICY_ACCEPTANCE_REPORT.md").write_text(
            _markdown(report),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["accepted"]:
            raise SystemExit("Web-dialogue-policy не прошёл release-gate.")
    except (
        FileNotFoundError,
        OSError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        WebDialogueDatasetError,
    ) as error:
        raise SystemExit(f"Приёмка web-dialogue-policy остановлена: {error}") from error


if __name__ == "__main__":
    main()
