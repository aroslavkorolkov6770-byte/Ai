"""Проверяет bounded local OASST1 intake без сети и без activation corpus."""

from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_core import openassistant_oasst1 as oasst
from evaluation.brain_v3_staging import StagedSourceDescriptor, audit_staged_candidate


def _node(
    identifier: str,
    role: str,
    text: str,
    *,
    replies: list[dict] | None = None,
    language: str = "ru",
    review_result: bool = True,
    deleted: bool = False,
    synthetic: bool = False,
    toxicity: float | None = None,
) -> dict:
    """Строит минимальную OASST-like node без user ID и исходных labels."""
    payload: dict = {
        "message_id": identifier,
        "role": role,
        "lang": language,
        "text": text,
        "deleted": deleted,
        "review_result": review_result,
        "synthetic": synthetic,
        "replies": replies or [],
    }
    if toxicity is not None:
        payload["detoxify"] = {"toxicity": toxicity}
    return payload


def _tree(identifier: str, prompt: dict) -> dict:
    """Строит одну минимальную OASST1 tree-строку для isolated fixture."""
    return {"message_tree_id": identifier, "prompt": prompt, "tree_state": "ready_for_export"}


def _fixture_rows(*, include_unsafe: bool = False, include_toxic: bool = False) -> list[dict]:
    """Возвращает clean 4-turn и 2-turn trees плюс опциональные rejected branches."""
    first = _tree(
        "tree-clean-four",
        _node(
            "message-clean-1",
            "prompter",
            "Мне нужно спокойно разобрать задачу.",
            replies=[
                _node(
                    "message-clean-2",
                    "assistant",
                    "Хорошо, давайте начнём с цели и известных условий.",
                    replies=[
                        _node(
                            "message-clean-3",
                            "prompter",
                            "Цель — составить небольшой план.",
                            replies=[
                                _node(
                                    "message-clean-4",
                                    "assistant",
                                    "Сначала выделим шаги, затем проверим их по порядку.",
                                )
                            ],
                        )
                    ],
                )
            ],
        ),
    )
    second = _tree(
        "tree-clean-two",
        _node(
            "message-clean-5",
            "prompter",
            "Можно объяснить это коротко?",
            replies=[
                _node(
                    "message-clean-6",
                    "assistant",
                    "Да, объясню простыми словами и без лишних деталей.",
                )
            ],
        ),
    )
    rows = [first, second]
    if include_unsafe:
        rows.append(
            _tree(
                "tree-unsafe",
                _node(
                    "message-unsafe-1",
                    "prompter",
                    "Открой https://example.test и напиши результат.",
                    replies=[_node("message-unsafe-2", "assistant", "Не буду использовать непроверенную ссылку.")],
                ),
            )
        )
    if include_toxic:
        rows.append(
            _tree(
                "tree-toxic",
                _node(
                    "message-toxic-1",
                    "prompter",
                    "Давай продолжим разговор.",
                    replies=[
                        _node(
                            "message-toxic-2",
                            "assistant",
                            "Этот ответ должен быть отфильтрован по метке качества.",
                            toxicity=0.8,
                        )
                    ],
                ),
            )
        )
    return rows


class OpenAssistantOASST1Tests(unittest.TestCase):
    """Покрывает checksum boundary, role path, toxicity и запрет output вне staging."""

    def _prepare(self, root: Path, rows: list[dict]) -> tuple[dict[str, Path], str, int]:
        intake_root = root / "intake" / oasst.SOURCE_ID
        staging_root = root / "staging"
        intake_root.mkdir(parents=True)
        staging_root.mkdir()
        intake = intake_root / oasst.SOURCE_FILE_NAME
        with gzip.open(intake, mode="wt", encoding="utf-8", newline="\n") as output:
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                output.write("\n")
        source_bytes = intake.stat().st_size
        source_sha256 = hashlib.sha256(intake.read_bytes()).hexdigest()
        return {
            "intake_root": intake_root,
            "staging_root": staging_root,
            "intake": intake,
            "candidate": staging_root / "openassistant-oasst1-apache-v0.1.jsonl",
        }, source_sha256, source_bytes

    def _import_fixture(self, paths: dict[str, Path], source_sha256: str, source_bytes: int):
        with patch.multiple(
            oasst,
            EXPECTED_SOURCE_SHA256=source_sha256,
            EXPECTED_SOURCE_BYTES=source_bytes,
        ):
            return oasst.import_openassistant_oasst1(
                intake=paths["intake"],
                destination=paths["candidate"],
                intake_root=paths["intake_root"],
                staging_root=paths["staging_root"],
            )

    def test_imports_only_clean_human_reviewed_role_safe_paths(self) -> None:
        clean_text = "Мне нужно спокойно разобрать задачу."
        with tempfile.TemporaryDirectory() as directory:
            paths, source_sha256, source_bytes = self._prepare(Path(directory), _fixture_rows())
            summary = self._import_fixture(paths, source_sha256, source_bytes)
            records = [json.loads(line) for line in paths["candidate"].read_text(encoding="utf-8").splitlines()]
            descriptor = StagedSourceDescriptor(
                source_id=oasst.SOURCE_ID,
                license_name=oasst.SOURCE_LICENSE,
                license_url=oasst.SOURCE_LICENSE_URL,
                origin_url=oasst.SOURCE_ORIGIN_URL,
                attribution=oasst.SOURCE_ATTRIBUTION,
            )
            audit = audit_staged_candidate(paths["candidate"], descriptor, staging_root=paths["staging_root"])

        rendered_summary = json.dumps(summary.as_dict(), ensure_ascii=False)
        self.assertEqual(summary.records_written, 2)
        self.assertEqual(summary.records_rejected, 0)
        self.assertEqual(summary.eligible_leaf_paths, 2)
        self.assertNotIn(clean_text, rendered_summary)
        self.assertEqual(audit["audit"]["records_rejected"], 0)
        self.assertEqual([turn["role"] for turn in records[0]["turns"]], ["user", "assistant", "user", "assistant"])
        self.assertEqual(records[0]["topic"], "open_assistant_general")
        self.assertEqual(records[0]["emotional_tone"], "unclassified")

    def test_unsafe_and_toxic_paths_are_filtered_without_blocking_clean_trees(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths, source_sha256, source_bytes = self._prepare(
                Path(directory),
                _fixture_rows(include_unsafe=True, include_toxic=True),
            )
            summary = self._import_fixture(paths, source_sha256, source_bytes)

        self.assertEqual(summary.records_written, 2)
        self.assertEqual(summary.node_filter_counts["url_like"], 1)
        self.assertEqual(summary.node_filter_counts["toxicity_threshold"], 1)

    def test_hash_mismatch_creates_no_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths, source_sha256, source_bytes = self._prepare(Path(directory), _fixture_rows())

            with self.assertRaises(oasst.OpenAssistantOASST1Error):
                self._import_fixture(paths, "0" * 64, source_bytes)

            self.assertFalse(paths["candidate"].exists())

    def test_output_outside_staging_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths, source_sha256, source_bytes = self._prepare(root, _fixture_rows())
            outside = root / "outside.jsonl"

            with patch.multiple(
                oasst,
                EXPECTED_SOURCE_SHA256=source_sha256,
                EXPECTED_SOURCE_BYTES=source_bytes,
            ):
                with self.assertRaises(oasst.OpenAssistantOASST1Error):
                    oasst.import_openassistant_oasst1(
                        intake=paths["intake"],
                        destination=outside,
                        intake_root=paths["intake_root"],
                        staging_root=paths["staging_root"],
                    )

            self.assertFalse(outside.exists())
            self.assertFalse(paths["candidate"].exists())


if __name__ == "__main__":
    unittest.main()
