"""Проверяет local web helper без запуска внешнего HTTP-сервера."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.oasst1_human_review import (
    APPROVAL_REASON_CODES,
    OASST1HumanReviewError,
    QUEUE_MODE,
)
from evaluation.oasst1_review_web import (
    ReviewDecisionRequest,
    ReviewPaths,
    build_review_order,
    build_review_status,
    create_review_app,
    is_loopback_host,
    load_next_candidate,
    record_human_decision,
    review_attention_codes,
)
from evaluation.oasst1_semantic_review import SOURCE_ID


def _sha256(value: str) -> str:
    """Создаёт fixture-хэш без обращения к реальным source-данным."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _queue_item(identifier: str, split: str) -> dict[str, object]:
    """Создаёт небольшой metadata-only candidate item для изолированной проверки."""

    return {
        "id": identifier,
        "content_sha256": _sha256(identifier),
        "split": split,
        "turn_count": 4,
        "primary_candidate_topic": "casual_dialogue",
        "candidate_topic_labels": ["casual_dialogue"],
        "candidate_reason_codes": {"casual_dialogue": ["casual_talk"]},
        "review_status": "unreviewed",
    }


def _source_record(item: dict[str, object]) -> dict[str, object]:
    """Создаёт source fixture, который не должен попасть в ledger/status."""

    return {
        "id": item["id"],
        "record_type": "dialogue",
        "language": "ru",
        "split": item["split"],
        "content_sha256": item["content_sha256"],
        "turns": [
            {"role": "user", "text": "Привет, можешь помочь спокойно разобрать тему?"},
            {"role": "assistant", "text": "Да, начнём с небольшого понятного шага."},
            {"role": "user", "text": "А если я запутаюсь, можно уточнить?"},
            {"role": "assistant", "text": "Конечно, уточни место, и мы разберём его вместе."},
        ],
    }


class OASST1ReviewWebTests(unittest.TestCase):
    """Проверяет browser helper, не подменяя его реальным human verdict."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.raw_root = self.root / "raw"
        self.results_root = self.root / "results"
        self.reviews_root = self.root / "reviews"
        self.raw_root.mkdir()
        self.results_root.mkdir()
        self.items = [
            _queue_item("oasst1.fixture.001", "train"),
            _queue_item("oasst1.fixture.002", "validation"),
        ]
        self.queue_path = self.results_root / "queue.json"
        self.queue_path.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "mode": QUEUE_MODE,
                    "source_id": SOURCE_ID,
                    "review_status": "unreviewed",
                    "selected_records": len(self.items),
                    "items": self.items,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.source_path = self.raw_root / "openassistant-oasst1-apache-v1.jsonl"
        self.source_path.write_text(
            "\n".join(
                json.dumps(_source_record(item), ensure_ascii=False) for item in self.items
            ),
            encoding="utf-8",
        )
        self.paths = ReviewPaths(
            queue_path=self.queue_path,
            source_path=self.source_path,
            decisions_path=self.reviews_root / "decisions.jsonl",
            results_root=self.results_root,
            raw_root=self.raw_root,
            reviews_root=self.reviews_root,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_status_stays_metadata_only_and_next_candidate_is_local_memory(self) -> None:
        snapshot = build_review_status(self.paths)
        serialized_snapshot = json.dumps(snapshot, ensure_ascii=False)
        self.assertEqual(snapshot["reviewed_records"], 0)
        self.assertEqual(snapshot["unreviewed_records"], 2)
        self.assertNotIn("Привет, можешь помочь спокойно разобрать тему?", serialized_snapshot)
        candidate = load_next_candidate(self.paths)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["id"], self.items[0]["id"])
        self.assertEqual(candidate["turns"][0]["role"], "user")
        self.assertEqual(candidate["review_attention_codes"], [])
        self.assertFalse(self.reviews_root.exists())

    def test_explicit_fixture_decision_writes_metadata_only_and_moves_forward(self) -> None:
        result = record_human_decision(
            ReviewDecisionRequest(
                candidate_id=self.items[0]["id"],
                decision="approved",
                reason_codes=sorted(APPROVAL_REASON_CODES),
                reviewer_role="local_human_reviewer",
                approval_confirmed=True,
            ),
            self.paths,
        )
        self.assertEqual(result["decision"], "approved")
        self.assertEqual(result["status"]["reviewed_records"], 1)
        ledger_text = self.paths.decisions_path.read_text(encoding="utf-8")
        self.assertNotIn("Привет, можешь помочь спокойно разобрать тему?", ledger_text)
        next_candidate = load_next_candidate(
            self.paths,
            after_candidate_id=self.items[0]["id"],
        )
        self.assertIsNotNone(next_candidate)
        self.assertEqual(next_candidate["id"], self.items[1]["id"])

    def test_approval_requires_explicit_local_confirmation(self) -> None:
        with self.assertRaisesRegex(
            OASST1HumanReviewError,
            "oasst1_review_web_approval_not_confirmed",
        ):
            record_human_decision(
                ReviewDecisionRequest(
                    candidate_id=self.items[0]["id"],
                    decision="approved",
                    reason_codes=sorted(APPROVAL_REASON_CODES),
                    reviewer_role="local_human_reviewer",
                ),
                self.paths,
            )

    def test_loopback_boundary_and_app_routes_are_explicit(self) -> None:
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("::1"))
        self.assertFalse(is_loopback_host("0.0.0.0"))
        self.assertFalse(is_loopback_host("192.168.1.10"))
        paths = {route.path for route in create_review_app(self.paths).routes}
        self.assertTrue({"/", "/health", "/api/status", "/api/next", "/api/decisions"}.issubset(paths))

    def test_review_order_interleaves_topics_before_repeating_one(self) -> None:
        items = [
            {
                **_queue_item("oasst1.fixture.alpha.001", "train"),
                "primary_candidate_topic": "alpha",
            },
            {
                **_queue_item("oasst1.fixture.alpha.002", "validation"),
                "primary_candidate_topic": "alpha",
            },
            {
                **_queue_item("oasst1.fixture.beta.001", "train"),
                "primary_candidate_topic": "beta",
            },
            {
                **_queue_item("oasst1.fixture.beta.002", "validation"),
                "primary_candidate_topic": "beta",
            },
            {
                **_queue_item("oasst1.fixture.gamma.001", "test"),
                "primary_candidate_topic": "gamma",
            },
        ]
        order = build_review_order(items)
        self.assertEqual(
            [item["primary_candidate_topic"] for item in order[:3]],
            ["alpha", "beta", "gamma"],
        )
        self.assertEqual([item["split"] for item in order[:3]], ["train", "validation", "test"])
        self.assertEqual(
            [item["primary_candidate_topic"] for item in order[3:]],
            ["alpha", "beta"],
        )
        self.assertEqual([item["split"] for item in order[3:]], ["validation", "train"])

    def test_attention_codes_prioritize_reading_but_do_not_decide_quality(self) -> None:
        record = _source_record(self.items[0])
        record["turns"][1]["text"] = "д" * 701
        record["turns"][2]["text"] = "Что это значит?"
        codes = review_attention_codes(record)
        self.assertEqual(
            codes,
            ("assistant_over_700_chars", "short_user_clarification_after_answer"),
        )
        low_attention = {
            **_queue_item("oasst1.fixture.alpha.low", "train"),
            "primary_candidate_topic": "alpha",
        }
        high_attention = {
            **_queue_item("oasst1.fixture.alpha.high", "train"),
            "primary_candidate_topic": "alpha",
        }
        other_topic = {
            **_queue_item("oasst1.fixture.beta", "validation"),
            "primary_candidate_topic": "beta",
        }
        order = build_review_order(
            [low_attention, high_attention, other_topic],
            attention_codes_by_id={high_attention["id"]: codes},
        )
        self.assertEqual(order[0]["id"], high_attention["id"])
        self.assertEqual(order[1]["id"], other_topic["id"])
        self.assertEqual(order[2]["id"], low_attention["id"])


if __name__ == "__main__":
    unittest.main()
