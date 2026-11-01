"""Проверяет безопасный локальный импорт Tatoeba CC0 без сети и реального архива."""

from __future__ import annotations

import bz2
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ai_core.brain_v3_corpus import audit_corpus
from ai_core.tatoeba_rus_cc0 import (
    CC0_LICENSE_URL,
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    TATOEBA_ORIGIN_URL,
    TatoebaImportError,
    import_tatoeba_rus_cc0,
)


def _write_archive(path: Path, lines: list[str]) -> None:
    """Записывает маленький искусственный BZip2 TSV для изолированного теста."""
    with bz2.open(path, mode="wt", encoding="utf-8", newline="\n") as output:
        for line in lines:
            output.write(line + "\n")


def _manifest_for_output(root: Path, output: Path) -> Path:
    """Создаёт временный manifest, чтобы проверить совместимость импорта и audit."""
    raw_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest = {
        "format_version": 1,
        "corpus_name": "tatoeba-import-test",
        "language_scope": ["ru"],
        "accepted_licenses": ["CC0-1.0"],
        "quality_gate": {
            "minimum_unique_dialogue_turns": 0,
            "minimum_language_characters": 1,
            "maximum_rejected_record_ratio": 0.0,
            "require_all_splits": False,
        },
        "sources": [
            {
                "source_id": SOURCE_ID,
                "raw_path": "raw/tatoeba-rus-cc0-v1.jsonl",
                "raw_sha256": raw_hash,
                "source_kind": "open_licensed",
                "license": SOURCE_LICENSE,
                "license_url": CC0_LICENSE_URL,
                "origin_url": TATOEBA_ORIGIN_URL,
                "attribution": SOURCE_ATTRIBUTION,
                "approved_for_training": True,
            }
        ],
    }
    path = root / "corpus_manifest.v1.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


class TatoebaRusCc0Tests(unittest.TestCase):
    """Проверки TSV, фильтров, разделения split и запрета перезаписи."""

    def test_imports_only_safe_russian_unique_sentences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            raw.mkdir()
            archive = raw / "tatoeba-rus-cc0.tsv.bz2"
            output = raw / "tatoeba-rus-cc0-v1.jsonl"
            _write_archive(
                archive,
                [
                    "1\trus\tСегодня ясная погода.",
                    "2\trus\tСегодня ясная погода.",
                    "3\trus\tПосмотри https://example.test.",
                    "4\teng\tA harmless English line.",
                    "broken line",
                    "5\trus\tМы спокойно обсудили план на завтра.\ttrusted-metadata",
                    "6\trus\tМы спокойно обсудили план на завтра!\ttrusted-metadata",
                ],
            )
            summary = import_tatoeba_rus_cc0(archive, output)
            report = summary.as_dict()
            manifest = _manifest_for_output(root, output)
            audit = audit_corpus(manifest)

        self.assertEqual(summary.records_read, 7)
        self.assertEqual(summary.records_written, 2)
        self.assertEqual(summary.rejections["exact_duplicate"], 1)
        self.assertEqual(summary.rejections["url_like"], 1)
        self.assertEqual(summary.rejections["unexpected_language"], 1)
        self.assertEqual(summary.rejections["invalid_tsv"], 1)
        self.assertEqual(summary.rejections["near_duplicate_candidate"], 1)
        self.assertEqual(audit["records_accepted"], 2)
        self.assertTrue(audit["training_allowed"])
        self.assertNotIn("Сегодня ясная", json.dumps(report, ensure_ascii=False))

    def test_output_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "input.tsv.bz2"
            output = root / "output.jsonl"
            _write_archive(archive, ["1\trus\tКороткое безопасное предложение."])
            import_tatoeba_rus_cc0(archive, output)

            with self.assertRaises(TatoebaImportError):
                import_tatoeba_rus_cc0(archive, output)

    def test_invalid_archive_extension_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "input.txt"
            archive.write_text("1\trus\tТекст", encoding="utf-8")

            with self.assertRaises(TatoebaImportError):
                import_tatoeba_rus_cc0(archive, root / "output.jsonl")


if __name__ == "__main__":
    unittest.main()
