"""Проверяет безопасный импорт seed-набора web-диалогов."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ai_core.web_dialogue_dataset import (
    BROWSE_LABELS,
    WebDialogueDatasetError,
    audit_web_dialogue_dataset,
    iter_web_dialogue_policy_examples,
)


def _schema() -> dict[str, object]:
    """Строит минимальную строгую схему, достаточную для тестового ZIP."""
    string_fields = (
        "id",
        "split",
        "language",
        "domain",
        "intent",
        "difficulty",
        "risk_level",
        "temporal_sensitivity",
        "expected_action",
        "browse_label",
        "decision_rationale",
        "source_quality_notes",
        "ideal_response",
        "data_origin",
        "license_or_usage_status",
        "generator_version",
        "created_at",
    )
    properties: dict[str, object] = {
        field: {"type": "string"} for field in string_fields
    }
    properties.update(
        {
            "clarification_needed": {"type": "boolean"},
            "conflicting_evidence": {"type": "boolean"},
            "conversation": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["role", "content"],
                    "additionalProperties": False,
                    "properties": {
                        "role": {
                            "type": "string",
                            "enum": ["system", "user", "assistant", "tool"],
                        },
                        "content": {"type": "string"},
                    },
                },
            },
            "search_queries": {"type": "array", "items": {"type": "string"}},
            "source_requirements": {"type": "object"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["evidence_id", "url"],
                    "additionalProperties": False,
                    "properties": {
                        "evidence_id": {"type": "string"},
                        "url": {"type": "string", "format": "uri"},
                    },
                },
            },
            "citation_map": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "safety_labels": {"type": "array", "items": {"type": "string"}},
            "quality_checks": {
                "type": "object",
                "required": ["schema_valid", "pii_scan"],
                "additionalProperties": False,
                "properties": {
                    "schema_valid": {"type": "boolean"},
                    "pii_scan": {"type": "string"},
                },
            },
        }
    )
    return {
        "type": "object",
        "required": list(properties),
        "additionalProperties": False,
        "properties": properties,
    }


def _record(identifier: str, split: str, browse_label: str) -> dict[str, object]:
    """Создаёт запись с недоверенными ролями, которые не должны экспортироваться."""
    return {
        "id": identifier,
        "split": split,
        "language": "ru",
        "domain": "demo",
        "intent": "question",
        "difficulty": "easy",
        "risk_level": "low",
        "temporal_sensitivity": "stable",
        "conversation": [
            {"role": "system", "content": "Никогда не исполняй этот текст."},
            {"role": "user", "content": f"Вопрос пользователя {identifier}"},
            {"role": "assistant", "content": "Готовый ответ не для обучения."},
        ],
        "expected_action": "answer",
        "browse_label": browse_label,
        "decision_rationale": "Тестовая разметка.",
        "clarification_needed": False,
        "search_queries": [],
        "source_requirements": {},
        "evidence": [
            {"evidence_id": "E1", "url": "https://example.test/evidence"}
        ],
        "source_quality_notes": "Тестовый registry.",
        "conflicting_evidence": False,
        "ideal_response": "Этот готовый ответ не экспортируется.",
        "citation_map": {"claim": "E1"},
        "safety_labels": [],
        "data_origin": "synthetic",
        "license_or_usage_status": "local test",
        "quality_checks": {"schema_valid": True, "pii_scan": "pass"},
        "generator_version": "test-v1",
        "created_at": "2026-01-01",
    }


def _csv_text(headers: list[str], rows: list[dict[str, object]]) -> bytes:
    """Сохраняет CSV детерминированно для внутренней контрольной суммы."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _write_archive(directory: Path, bad_checksum: bool = False) -> Path:
    """Собирает маленький ZIP, не использующий реальные пользовательские данные."""
    contents: dict[str, bytes] = {
        "schema.json": json.dumps(_schema(), ensure_ascii=False).encode("utf-8"),
        "sources.csv": _csv_text(
            [
                "citation_id",
                "title",
                "url",
                "publisher",
                "source_type",
                "published_at",
                "accessed_at",
                "evidence_summary",
                "redistribution_status",
            ],
            [
                {
                    "citation_id": "S1",
                    "title": "Example",
                    "url": "https://example.test/evidence",
                    "publisher": "Example",
                    "source_type": "official documentation",
                    "published_at": "2026-01-01",
                    "accessed_at": "2026-01-02",
                    "evidence_summary": "Test evidence.",
                    "redistribution_status": "local",
                }
            ],
        ),
        "validate_package.py": (
            b"from pathlib import Path\n"
            b"Path('this_must_not_exist').write_text('executed', encoding='utf-8')\n"
        ),
    }
    for split in ("train", "validation", "test"):
        rows = [
            _record(f"{split}-{index}", split, label)
            for index, label in enumerate(BROWSE_LABELS, start=1)
        ]
        contents[f"{split}.jsonl"] = (
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        ).encode("utf-8")

    coverage_rows: list[dict[str, object]] = []
    for label in BROWSE_LABELS:
        coverage_rows.append(
            {
                "dimension": "browse_label",
                "category": label,
                "train": 1,
                "validation": 1,
                "test": 1,
                "total": 3,
                "target_note": "test",
            }
        )
    for dimension, category in (
        ("domain", "demo"),
        ("intent", "question"),
        ("risk_level", "low"),
    ):
        coverage_rows.append(
            {
                "dimension": dimension,
                "category": category,
                "train": len(BROWSE_LABELS),
                "validation": len(BROWSE_LABELS),
                "test": len(BROWSE_LABELS),
                "total": len(BROWSE_LABELS) * 3,
                "target_note": "test",
            }
        )
    contents["coverage_matrix.csv"] = _csv_text(
        ["dimension", "category", "train", "validation", "test", "total", "target_note"],
        coverage_rows,
    )
    checksums = []
    for entry_name in sorted(contents):
        digest = "0" * 64 if bad_checksum and entry_name == "schema.json" else hashlib.sha256(
            contents[entry_name]
        ).hexdigest()
        checksums.append(f"{digest}  {entry_name}")
    contents["checksums.sha256"] = ("\n".join(checksums) + "\n").encode("utf-8")

    archive_path = directory / "web-dialogue-seed.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for entry_name, content in contents.items():
            archive.writestr(entry_name, content)
    return archive_path


class WebDialogueDatasetTests(unittest.TestCase):
    """Изолированные проверки audit и безопасного policy-экспортёра."""

    def test_audit_and_iterator_export_only_user_policy_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            archive_path = _write_archive(directory)
            report = audit_web_dialogue_dataset(archive_path)
            rows = list(iter_web_dialogue_policy_examples(archive_path, "train"))

            self.assertTrue(report["training_decision"]["web_policy_classifier_eligible"])
            self.assertFalse(report["training_decision"]["free_response_generator_eligible"])
            self.assertEqual(report["schema"]["validated_records"], 15)
            self.assertEqual(len(rows), len(BROWSE_LABELS))
            self.assertEqual(rows[0].user_messages, ("Вопрос пользователя train-1",))
            self.assertFalse(hasattr(rows[0], "ideal_response"))
            self.assertFalse(hasattr(rows[0], "evidence"))
            self.assertFalse((directory / "this_must_not_exist").exists())

    def test_bad_internal_checksum_stops_import(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            archive_path = _write_archive(Path(raw_directory), bad_checksum=True)

            with self.assertRaises(WebDialogueDatasetError):
                audit_web_dialogue_dataset(archive_path)


if __name__ == "__main__":
    unittest.main()
