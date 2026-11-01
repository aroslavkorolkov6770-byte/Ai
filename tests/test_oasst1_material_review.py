"""Проверяет отдельный human material review без решений за владельца."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from evaluation import oasst1_human_review as quality
from evaluation.oasst1_material_review import (
    DECISIONS_PATH,
    MaterialReviewPaths,
    OASST1MaterialReviewError,
    TRIAGE_PATH,
    next_case,
    record_decision,
    review_status,
)
from evaluation.oasst1_material_review_web import create_material_review_app


class OASST1MaterialReviewTests(unittest.TestCase):
    """Проверяет новое metadata ledger только во временном каталоге."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = MaterialReviewPaths(
            decisions_path=self.root / "reviews" / "decisions.jsonl",
            reviews_root=self.root / "reviews",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_local_case_and_status_do_not_write_source_text(self) -> None:
        """Один полный диалог доступен для чтения, status остаётся metadata."""

        first = next_case(self.paths)
        self.assertEqual(first["triage_group"], "priority_signal")
        self.assertGreaterEqual(len(first["turns"]), 4)
        snapshot = review_status(self.paths)
        self.assertEqual(snapshot["sample_records"], 24)
        self.assertEqual(snapshot["pending_records"], 24)
        self.assertFalse(snapshot["training_candidate_allowed"])
        self.assertNotIn('"turns"', json.dumps(snapshot))
        self.assertFalse(self.paths.decisions_path.exists())

    def test_explicit_decision_is_metadata_only_and_duplicate_rejected(self) -> None:
        """Флажок чтения обязателен; прошлые данные не меняются."""

        first = next_case(self.paths)
        inputs = [quality.DEFAULT_SOURCE_PATH, quality.DEFAULT_DECISIONS_PATH, TRIAGE_PATH]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        with self.assertRaisesRegex(OASST1MaterialReviewError, "full_read_required"):
            record_decision(first["id"], "no_obvious_excerpt", reason_code="none_obvious", paths=self.paths)
        result = record_decision(
            first["id"], "needs_source_check", reason_code="quoted_span",
            read_confirmed=True, paths=self.paths,
        )
        self.assertEqual(result["verdict"], "needs_source_check")
        self.assertEqual(review_status(self.paths)["pending_records"], 23)
        ledger = self.paths.decisions_path.read_text(encoding="utf-8")
        self.assertNotIn(first["turns"][0]["text"], ledger)
        self.assertNotIn('"turns"', ledger)
        with self.assertRaisesRegex(OASST1MaterialReviewError, "duplicate_decision"):
            record_decision(
                first["id"], "exclude_candidate", reason_code="quoted_span",
                read_confirmed=True, paths=self.paths,
            )
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs})

    def test_wrong_reason_and_unknown_id_fail_closed(self) -> None:
        """Нельзя записать свободный текст или решение вне 24-ID выборки."""

        first = next_case(self.paths)
        with self.assertRaisesRegex(OASST1MaterialReviewError, "reason_invalid"):
            record_decision(
                first["id"], "no_obvious_excerpt", reason_code="quoted_span",
                read_confirmed=True, paths=self.paths,
            )
        with self.assertRaisesRegex(OASST1MaterialReviewError, "reason_invalid"):
            record_decision(
                first["id"], "needs_source_check", reason_code="free text from dialogue",
                read_confirmed=True, paths=self.paths,
            )
        with self.assertRaisesRegex(OASST1MaterialReviewError, "reason_invalid"):
            record_decision(
                first["id"], "needs_source_check", reason_code=["quoted_span"],
                read_confirmed=True, paths=self.paths,
            )
        with self.assertRaisesRegex(OASST1MaterialReviewError, "unknown_candidate"):
            record_decision(
                "not-in-sample", "no_obvious_excerpt", reason_code="none_obvious",
                read_confirmed=True, paths=self.paths,
            )
        self.assertFalse(self.paths.decisions_path.exists())

    def test_routes_and_real_ledger_are_not_modified(self) -> None:
        """Создание browser app сохраняет рабочий ledger даже после начала review."""

        before = DECISIONS_PATH.read_bytes() if DECISIONS_PATH.exists() else None
        routes = {route.path for route in create_material_review_app(self.paths).routes}
        self.assertEqual(routes, {"/", "/health", "/api/status", "/api/next", "/api/decisions"})
        after = DECISIONS_PATH.read_bytes() if DECISIONS_PATH.exists() else None
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
