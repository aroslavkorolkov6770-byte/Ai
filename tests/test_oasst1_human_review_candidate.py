"""Изолированные проверки approved-only OASST1 candidate workflow."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ai_core.brain_v3_corpus import content_sha256, file_sha256
from ai_core.openassistant_oasst1 import (
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
)
from evaluation.brain_v3_staging import audit_staged_candidate
from evaluation.oasst1_human_review import (
    REQUIRED_APPROVAL_REASON_CODES,
    create_review_decision,
)
from evaluation.oasst1_human_review_candidate import (
    OASST1HumanReviewCandidateError,
    SUBSET_SOURCE_ID,
    audit_human_reviewed_candidate,
    build_human_reviewed_candidate,
    human_reviewed_descriptor,
)


def source_record(identifier: str, split: str, user_text: str) -> dict[str, object]:
    """Создаёт минимальную валидную OASST1-like запись без внешних данных."""
    record: dict[str, object] = {
        "format_version": 1,
        "id": identifier,
        "record_type": "dialogue",
        "provenance": {
            "source_id": SOURCE_ID,
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "origin_url": SOURCE_ORIGIN_URL,
            "attribution": SOURCE_ATTRIBUTION,
        },
        "language": "ru",
        "layer": "dialogue",
        "split": split,
        "topic": "everyday_dialogue",
        "quality_tier": "human_reviewed",
        "expected_response_properties": ["relevant", "natural_tone"],
        "dialogue_goal": "supportive_short_dialogue",
        "emotional_tone": "neutral",
        "turns": [
            {"role": "user", "text": user_text},
            {
                "role": "assistant",
                "text": "Спасибо за вопрос. Давай спокойно разберёмся вместе.",
            },
            {"role": "user", "text": "Хорошо, уточни следующий шаг."},
            {
                "role": "assistant",
                "text": "Сначала назови цель, а затем выберем простой первый шаг.",
            },
        ],
        "content_sha256": "",
    }
    record["content_sha256"] = content_sha256(record)
    return record


class OASST1HumanReviewCandidateTests(unittest.TestCase):
    """Проверяет связь human decisions, immutable candidate и metadata reports."""

    def _layout(self, root: Path) -> dict[str, Path]:
        results = root / "results"
        raw = root / "raw"
        reviews = root / "reviews"
        staging = root / "staging"
        for directory in (results, raw, reviews, staging):
            directory.mkdir(parents=True, exist_ok=True)
        return {
            "results": results,
            "raw": raw,
            "reviews": reviews,
            "staging": staging,
        }

    def _queue_for_records(self, records: list[dict[str, object]]) -> dict[str, object]:
        return {
            "format_version": 1,
            "mode": "oasst1_semantic_candidate_review_queue",
            "source_id": SOURCE_ID,
            "review_status": "unreviewed",
            "selected_records": len(records),
            "items": [
                {
                    "id": record["id"],
                    "content_sha256": record["content_sha256"],
                    "split": record["split"],
                    "turn_count": len(record["turns"]),
                    "candidate_topic_labels": ["everyday_dialogue"],
                    "review_status": "unreviewed",
                }
                for record in records
            ],
        }

    def _write_inputs(
        self,
        layout: dict[str, Path],
        records: list[dict[str, object]],
        decisions: list[dict[str, object]],
    ) -> tuple[Path, Path, Path]:
        queue_path = layout["results"] / "queue.json"
        source_path = layout["raw"] / "source.jsonl"
        decisions_path = layout["reviews"] / "decisions.jsonl"
        queue_path.write_text(
            json.dumps(self._queue_for_records(records), ensure_ascii=False),
            encoding="utf-8",
        )
        source_path.write_text(
            "".join(
                f"{json.dumps(record, ensure_ascii=False, sort_keys=True)}\n"
                for record in records
            ),
            encoding="utf-8",
        )
        decisions_path.write_text(
            "".join(
                f"{json.dumps(decision, ensure_ascii=False, sort_keys=True)}\n"
                for decision in decisions
            ),
            encoding="utf-8",
        )
        return queue_path, source_path, decisions_path

    def test_builds_only_approved_records_and_keeps_reports_metadata_only(self) -> None:
        approved = source_record(
            "oasst1-candidate-001",
            "train",
            "Проверочная фраза для approved записи.",
        )
        rejected = source_record(
            "oasst1-candidate-002",
            "validation",
            "Проверочная фраза для rejected записи.",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            layout = self._layout(Path(temporary_directory))
            queue = self._queue_for_records([approved, rejected])
            approved_decision = create_review_decision(
                queue["items"][0],
                decision="approved",
                reason_codes=REQUIRED_APPROVAL_REASON_CODES,
                reviewer_role="local_reviewer",
                reviewed_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
            )
            rejected_decision = create_review_decision(
                queue["items"][1],
                decision="rejected",
                reason_codes=["not_target_semantics"],
                reviewer_role="local_reviewer",
                reviewed_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
            )
            queue_path, source_path, decisions_path = self._write_inputs(
                layout,
                [approved, rejected],
                [approved_decision, rejected_decision],
            )
            candidate_path = layout["staging"] / "approved.jsonl"
            report_directory = layout["results"] / "candidate-report"

            report = build_human_reviewed_candidate(
                queue_path=queue_path,
                source_path=source_path,
                decisions_path=decisions_path,
                destination=candidate_path,
                report_directory=report_directory,
                results_root=layout["results"],
                raw_root=layout["raw"],
                reviews_root=layout["reviews"],
                staging_root=layout["staging"],
            )

            self.assertEqual(report["approved_records"], 1)
            self.assertEqual(report["rejected_records"], 1)
            self.assertEqual(report["candidate_records"], 1)
            self.assertFalse(report["candidate_is_active"])
            self.assertFalse(report["training_started"])
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            self.assertEqual(candidate["id"], approved["id"])
            self.assertEqual(candidate["provenance"]["source_id"], SUBSET_SOURCE_ID)
            self.assertEqual(candidate["content_sha256"], content_sha256(candidate))
            isolated = audit_staged_candidate(
                candidate_path,
                human_reviewed_descriptor(),
                staging_root=layout["staging"],
            )
            self.assertEqual(isolated["audit"]["records_accepted"], 1)
            self.assertEqual(isolated["audit"]["records_rejected"], 0)
            self.assertFalse(isolated["candidate_is_active"])
            corpus_root = Path(temporary_directory) / "active-corpus"
            active_raw_directory = corpus_root / "raw"
            active_raw_directory.mkdir(parents=True)
            active_raw = active_raw_directory / "original.jsonl"
            active_raw.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
            active_manifest = {
                "format_version": 1,
                "corpus_name": "candidate-test-active-corpus",
                "language_scope": ["ru"],
                "accepted_licenses": [SOURCE_LICENSE],
                "quality_gate": {
                    "minimum_unique_dialogue_turns": 1,
                    "minimum_language_characters": 1,
                    "maximum_rejected_record_ratio": 0.0,
                    "require_all_splits": False,
                },
                "sources": [
                    {
                        "source_id": SOURCE_ID,
                        "raw_path": "raw/original.jsonl",
                        "raw_sha256": file_sha256(active_raw),
                        "source_kind": "open_licensed",
                        "license": SOURCE_LICENSE,
                        "license_url": SOURCE_LICENSE_URL,
                        "origin_url": SOURCE_ORIGIN_URL,
                        "attribution": SOURCE_ATTRIBUTION,
                        "approved_for_training": True,
                    }
                ],
            }
            (corpus_root / "corpus_manifest.v1.json").write_text(
                json.dumps(active_manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            audit_report = audit_human_reviewed_candidate(
                candidate=candidate_path,
                report_directory=layout["results"] / "audit-report",
                corpus_root=corpus_root,
                staging_root=layout["staging"],
                results_root=layout["results"],
            )
            self.assertEqual(
                audit_report["active_corpus_preflight"]["status"],
                "blocked_parent_source_already_active",
            )
            self.assertFalse(audit_report["active_corpus_preflight"]["clean_merge"])
            self.assertEqual(
                audit_report["prospective_dialogue_coverage"]["status"],
                "skipped_parent_source_already_active",
            )
            json_report = (report_directory / "oasst1_human_review_candidate.json").read_text(
                encoding="utf-8"
            )
            markdown_report = (report_directory / "OASST1_HUMAN_REVIEW_CANDIDATE.md").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("Проверочная фраза", json_report)
            self.assertNotIn("Проверочная фраза", markdown_report)

    def test_blocks_candidate_when_any_queue_item_has_no_human_decision(self) -> None:
        first = source_record("oasst1-candidate-003", "train", "Первый текст.")
        second = source_record("oasst1-candidate-004", "test", "Второй текст.")
        with tempfile.TemporaryDirectory() as temporary_directory:
            layout = self._layout(Path(temporary_directory))
            queue = self._queue_for_records([first, second])
            single_decision = create_review_decision(
                queue["items"][0],
                decision="approved",
                reason_codes=REQUIRED_APPROVAL_REASON_CODES,
                reviewer_role="local_reviewer",
                reviewed_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
            )
            queue_path, source_path, decisions_path = self._write_inputs(
                layout,
                [first, second],
                [single_decision],
            )
            candidate_path = layout["staging"] / "blocked.jsonl"
            report_directory = layout["results"] / "blocked-report"

            with self.assertRaisesRegex(
                OASST1HumanReviewCandidateError,
                "oasst1_human_review_candidate_review_incomplete",
            ):
                build_human_reviewed_candidate(
                    queue_path=queue_path,
                    source_path=source_path,
                    decisions_path=decisions_path,
                    destination=candidate_path,
                    report_directory=report_directory,
                    results_root=layout["results"],
                    raw_root=layout["raw"],
                    reviews_root=layout["reviews"],
                    staging_root=layout["staging"],
                )

            self.assertFalse(candidate_path.exists())
            self.assertFalse(report_directory.exists())


if __name__ == "__main__":
    unittest.main()
