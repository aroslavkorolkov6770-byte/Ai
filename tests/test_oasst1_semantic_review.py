"""Проверяет metadata-only semantic review queue для локального OASST1 source."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluation.oasst1_semantic_review import (
    OASST1SemanticReviewError,
    SOURCE_ID,
    create_semantic_review_queue,
    select_semantic_review_queue,
)


def _record(
    identifier: str,
    user_text: str,
    assistant_text: str,
    *,
    split: str,
) -> dict[str, object]:
    """Создаёт минимальный OASST1-like fixture без внешнего source content."""
    return {
        "id": identifier,
        "record_type": "dialogue",
        "language": "ru",
        "layer": "dialogue",
        "split": split,
        "content_sha256": (identifier.encode("utf-8").hex() + "0" * 64)[:64],
        "provenance": {"source_id": SOURCE_ID},
        "turns": [
            {"role": "user", "text": user_text},
            {"role": "assistant", "text": assistant_text},
            {"role": "user", "text": "Можешь продолжить без лишних деталей?"},
            {"role": "assistant", "text": "Да, продолжу аккуратно и по существу."},
        ],
    }


class OASST1SemanticReviewTests(unittest.TestCase):
    """Проверяет, что эвристика остаётся review-only и не раскрывает реплики."""

    def setUp(self) -> None:
        self.records = [
            _record(
                "oasst.fixture.001",
                "Мне тревожно, нужна спокойная поддержка.",
                "Сначала давай поймём, что сейчас труднее всего.",
                split="train",
            ),
            _record(
                "oasst.fixture.002",
                "Как объяснить это простыми словами и коротко?",
                "Начнём с краткого определения и одного примера.",
                split="validation",
            ),
            _record(
                "oasst.fixture.003",
                "Какая сегодня погода и нужен ли официальный источник?",
                "Сначала уточню город и проверю актуальные данные.",
                split="test",
            ),
            _record(
                "oasst.fixture.004",
                "Я хочу исправить ошибку в предыдущем вопросе.",
                "Спасибо за уточнение, буду опираться на новую формулировку.",
                split="train",
            ),
        ]

    def test_queue_contains_only_metadata_and_unverified_candidate_labels(self) -> None:
        report = select_semantic_review_queue(self.records, items_per_topic=2)
        rendered = json.dumps(report, ensure_ascii=False)
        self.assertEqual(report["source_id"], SOURCE_ID)
        self.assertEqual(report["review_status"], "unreviewed")
        self.assertTrue(report["candidate_labels_are_unverified"])
        self.assertFalse(report["active_manifest_updated"])
        self.assertFalse(report["training_started"])
        self.assertNotIn(self.records[0]["turns"][0]["text"], rendered)
        self.assertNotIn(self.records[0]["turns"][1]["text"], rendered)
        item = next(item for item in report["items"] if item["id"] == "oasst.fixture.001")
        self.assertIn("emotional_support", item["candidate_topic_labels"])
        self.assertIn("emotion_state", item["candidate_reason_codes"]["emotional_support"])

    def test_queue_selects_available_splits_for_each_candidate_topic(self) -> None:
        report = select_semantic_review_queue(self.records, items_per_topic=2)
        self.assertGreaterEqual(report["selected_records"], 4)
        self.assertIn("web_grounding", report["eligible_records_by_candidate_topic"])
        self.assertIn("correction", report["selected_by_candidate_topic"])
        self.assertEqual(report["coverage"]["splits"], {"test": 1, "train": 2, "validation": 1})

    def test_quality_filter_excludes_legacy_identity_without_exposing_text(self) -> None:
        legacy_user = "Привет, расскажи о своих возможностях."
        legacy_assistant = "Я OpenAssistant, тестовый помощник."
        records = [
            *self.records,
            _record(
                "oasst.fixture.legacy",
                legacy_user,
                legacy_assistant,
                split="train",
            ),
        ]
        report = select_semantic_review_queue(records, items_per_topic=2)
        rendered = json.dumps(report, ensure_ascii=False)
        selected_ids = {item["id"] for item in report["items"]}
        self.assertNotIn("oasst.fixture.legacy", selected_ids)
        self.assertEqual(report["excluded_by_quality_reason"], {"assistant_model_identity": 1})
        self.assertNotIn(legacy_user, rendered)
        self.assertNotIn(legacy_assistant, rendered)

    def test_quality_filter_excludes_explicit_rejection_of_previous_answer(self) -> None:
        rejected_record = {
            **self.records[0],
            "id": "oasst.fixture.user-rejects-answer",
            "content_sha256": "f" * 64,
            "turns": [
                {"role": "user", "text": "Помоги придумать понятный пример."},
                {"role": "assistant", "text": "Напишите мне чертежи туалетов."},
                {
                    "role": "user",
                    "text": "Это не является ответом на мой запрос. Попробуй ещё раз.",
                },
                {"role": "assistant", "text": "Давай подберём спокойный пример по шагам."},
            ],
        }
        report = select_semantic_review_queue(
            [*self.records, rejected_record],
            items_per_topic=2,
        )
        selected_ids = {item["id"] for item in report["items"]}
        self.assertNotIn("oasst.fixture.user-rejects-answer", selected_ids)
        self.assertEqual(
            report["excluded_by_quality_reason"].get(
                "user_rejects_previous_assistant_answer"
            ),
            1,
        )

    def test_quality_filter_excludes_user_restatement_of_unmet_request(self) -> None:
        rejected_record = {
            **self.records[0],
            "id": "oasst.fixture.user-restates-request",
            "content_sha256": "e" * 64,
            "turns": [
                {"role": "user", "text": "Приведи пример из реальной жизни."},
                {"role": "assistant", "text": "Это пригодится на экзамене."},
                {
                    "role": "user",
                    "text": "Я же попросил привести пример из реальной жизни.",
                },
                {"role": "assistant", "text": "Уточните, о какой ситуации идёт речь."},
            ],
        }
        report = select_semantic_review_queue(
            [*self.records, rejected_record],
            items_per_topic=2,
        )
        selected_ids = {item["id"] for item in report["items"]}
        self.assertNotIn("oasst.fixture.user-restates-request", selected_ids)
        self.assertEqual(
            report["excluded_by_quality_reason"].get(
                "user_restates_unmet_request"
            ),
            1,
        )

    def test_creation_only_writes_new_report_inside_results_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_root = root / "raw"
            raw_root.mkdir()
            source_path = raw_root / "openassistant-oasst1-apache-v1.jsonl"
            source_path.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in self.records),
                encoding="utf-8",
            )
            results_root = root / "results"
            report_directory = results_root / "review-queue"

            report = create_semantic_review_queue(
                source_path,
                report_directory,
                items_per_topic=2,
                raw_root=raw_root,
                results_root=results_root,
            )

            self.assertTrue((report_directory / "oasst1_semantic_review_queue.json").is_file())
            self.assertTrue((report_directory / "OASST1_SEMANTIC_REVIEW_QUEUE.md").is_file())
            self.assertEqual(report["review_status"], "unreviewed")
            with self.assertRaises(OASST1SemanticReviewError):
                create_semantic_review_queue(
                    source_path,
                    report_directory,
                    items_per_topic=2,
                    raw_root=raw_root,
                    results_root=results_root,
                )


if __name__ == "__main__":
    unittest.main()
