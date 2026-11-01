"""Проверяет MOT Russian importer на маленьких локальных tar-fixtures без сети.

Тесты не содержат реальных материалов корпуса и не вызывают GitHub. Они
проверяют только fixed-source границу, безопасное потоковое чтение archive,
фильтрацию недоверенных полей и совместимость результата с audit Brain v3.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from ai_core.brain_v3_corpus import audit_corpus
from ai_core.mot_russian import (
    ARCHIVE_SNAPSHOT_NAME,
    DEFAULT_ARCHIVE_PATH,
    MOT_RUS_RELEASE_URL,
    SOURCE_ATTRIBUTION,
    SOURCE_ID,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_ORIGIN_URL,
    MOTRussianError,
    download_mot_russian_archive,
    import_mot_russian,
    import_summary_markdown,
    is_allowed_release_redirect,
    validate_fixed_release_url,
)


def _add_file(archive: tarfile.TarFile, name: str, raw: bytes) -> None:
    """Добавляет обычный file member в маленький искусственный gzip tar."""
    member = tarfile.TarInfo(name)
    member.size = len(raw)
    archive.addfile(member, io.BytesIO(raw))


def _write_fixture_archive(path: Path, *, invalid_only: bool = False) -> None:
    """Создаёт безопасный fixture со всеми важными случаями importer-а."""
    accepted = json.dumps(
        {"paragraphs": ["Проверочный документ о спокойном вечере."]},
        ensure_ascii=False,
    ).encode("utf-8")
    near_duplicate = json.dumps(
        {"paragraphs": ["Проверочный документ о спокойном вечере!"]},
        ensure_ascii=False,
    ).encode("utf-8")
    url_like = json.dumps(
        {"paragraphs": ["Перейдите на https://example.test для проверки."]},
        ensure_ascii=False,
    ).encode("utf-8")
    missing_paragraphs = json.dumps({"title": "Fixture"}, ensure_ascii=False).encode("utf-8")
    punctuation_only = json.dumps({"paragraphs": ["!!!"]}, ensure_ascii=False).encode("utf-8")

    with tarfile.open(path, mode="w:gz") as archive:
        if invalid_only:
            _add_file(archive, "articles/missing.json", missing_paragraphs)
            return
        _add_file(archive, "articles/accepted.json", accepted)
        _add_file(archive, "articles/exact-duplicate.json", accepted)
        _add_file(archive, "articles/near-duplicate.json", near_duplicate)
        _add_file(archive, "articles/url-like.json", url_like)
        _add_file(archive, "articles/malformed.json", b"{not valid json")
        _add_file(archive, "articles/missing-paragraphs.json", missing_paragraphs)
        _add_file(archive, "articles/punctuation-only.json", punctuation_only)
        _add_file(archive, "../unsafe.json", accepted)
        _add_file(archive, "notes.txt", b"not an article")
        symlink = tarfile.TarInfo("unsafe-link.json")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "../outside.json"
        archive.addfile(symlink)


def _manifest_for_output(root: Path, output: Path) -> Path:
    """Строит одноразовый manifest для независимого audit с fixture output."""
    raw_directory = root / "raw"
    raw_directory.mkdir()
    audit_input = raw_directory / "candidate.jsonl"
    os.link(output, audit_input)
    manifest = {
        "format_version": 1,
        "corpus_name": "mot-russian-import-test",
        "language_scope": ["ru"],
        "accepted_licenses": [SOURCE_LICENSE],
        "quality_gate": {
            "minimum_unique_dialogue_turns": 0,
            "minimum_language_characters": 1,
            "maximum_rejected_record_ratio": 0.0,
            "require_all_splits": False,
        },
        "sources": [
            {
                "source_id": SOURCE_ID,
                "raw_path": "raw/candidate.jsonl",
                "raw_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "source_kind": "open_licensed",
                "license": SOURCE_LICENSE,
                "license_url": SOURCE_LICENSE_URL,
                "origin_url": SOURCE_ORIGIN_URL,
                "attribution": SOURCE_ATTRIBUTION,
                "approved_for_training": True,
            }
        ],
    }
    path = root / "corpus_manifest.v1.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


class _FixtureResponse:
    """Минимальный HTTP response для проверки download без сетевого запроса."""

    def __init__(self, raw: bytes, url: str, content_type: str = "application/octet-stream") -> None:
        self._stream = io.BytesIO(raw)
        self._url = url
        self.headers = Message()
        self.headers.add_header("Content-Type", content_type)
        self.headers.add_header("Content-Length", str(len(raw)))

    def __enter__(self) -> _FixtureResponse:
        return self

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        self._stream.close()

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


class _FixtureOpener:
    """Имитирует уже проверенный один GitHub redirect внутри unit-теста."""

    def __init__(self, handler: object, response: _FixtureResponse) -> None:
        self._handler = handler
        self._response = response

    def open(self, request: object, timeout: float) -> _FixtureResponse:
        del request, timeout
        self._handler.redirects = 1
        return self._response


class MOTRussianTests(unittest.TestCase):
    """Проверки fixed release, archive boundary и deterministic staging output."""

    def test_import_filters_unsafe_members_and_produces_auditable_record(self) -> None:
        fixture_text = "Проверочный документ о спокойном вечере."
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_root = root / "source_archives"
            staging_root = root / "staging"
            archive_root.mkdir()
            archive = archive_root / ARCHIVE_SNAPSHOT_NAME
            output = staging_root / "candidate.jsonl"
            _write_fixture_archive(archive)

            with patch("ai_core.mot_russian.EXPECTED_ARCHIVE_BYTES", archive.stat().st_size):
                summary = import_mot_russian(
                    archive,
                    output,
                    archive_root=archive_root,
                    staging_root=staging_root,
                )

            audit = audit_corpus(_manifest_for_output(root, output))
            report_json = json.dumps(summary.as_dict(), ensure_ascii=False)
            report_markdown = import_summary_markdown(summary)
            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(summary.archive_members_seen, 10)
        self.assertEqual(summary.json_members_read, 7)
        self.assertEqual(summary.records_written, 1)
        self.assertEqual(summary.records_rejected, 9)
        self.assertEqual(
            summary.rejections,
            {
                "exact_duplicate": 1,
                "empty_content": 1,
                "invalid_article_paragraphs": 1,
                "invalid_json": 1,
                "near_duplicate_candidate": 1,
                "non_json_member": 1,
                "non_regular_member": 1,
                "unsafe_member_name": 1,
                "url_like": 1,
            },
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["record_type"], "language_document")
        self.assertEqual(records[0]["provenance"]["source_id"], SOURCE_ID)
        self.assertEqual(audit["records_accepted"], 1)
        self.assertTrue(audit["training_allowed"])
        self.assertNotIn(fixture_text, report_json)
        self.assertNotIn(fixture_text, report_markdown)

    def test_import_leaves_no_partial_output_if_no_safe_record_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_root = root / "source_archives"
            staging_root = root / "staging"
            archive_root.mkdir()
            archive = archive_root / ARCHIVE_SNAPSHOT_NAME
            output = staging_root / "candidate.jsonl"
            _write_fixture_archive(archive, invalid_only=True)

            with patch("ai_core.mot_russian.EXPECTED_ARCHIVE_BYTES", archive.stat().st_size):
                with self.assertRaises(MOTRussianError):
                    import_mot_russian(
                        archive,
                        output,
                        archive_root=archive_root,
                        staging_root=staging_root,
                    )

            self.assertFalse(output.exists())

    def test_import_progress_exposes_only_counters(self) -> None:
        fixture_text = "Проверочный документ о спокойном вечере."
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_root = root / "source_archives"
            staging_root = root / "staging"
            archive_root.mkdir()
            archive = archive_root / ARCHIVE_SNAPSHOT_NAME
            output = staging_root / "candidate.jsonl"
            _write_fixture_archive(archive)
            progress = []

            with patch("ai_core.mot_russian.EXPECTED_ARCHIVE_BYTES", archive.stat().st_size):
                import_mot_russian(
                    archive,
                    output,
                    archive_root=archive_root,
                    staging_root=staging_root,
                    progress_callback=progress.append,
                    progress_every_members=2,
                )

        self.assertEqual([item.archive_members_seen for item in progress], [2, 4, 6, 8, 10])
        self.assertTrue(all(item.records_written >= 0 for item in progress))
        self.assertNotIn(
            fixture_text,
            json.dumps([item.as_dict() for item in progress], ensure_ascii=False),
        )

    def test_import_refuses_archive_or_output_outside_declared_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_root = root / "source_archives"
            staging_root = root / "staging"
            archive_root.mkdir()
            archive = archive_root / ARCHIVE_SNAPSHOT_NAME
            _write_fixture_archive(archive)

            with patch("ai_core.mot_russian.EXPECTED_ARCHIVE_BYTES", archive.stat().st_size):
                with self.assertRaises(MOTRussianError):
                    import_mot_russian(
                        root / ARCHIVE_SNAPSHOT_NAME,
                        staging_root / "candidate.jsonl",
                        archive_root=archive_root,
                        staging_root=staging_root,
                    )
                with self.assertRaises(MOTRussianError):
                    import_mot_russian(
                        archive,
                        root / "outside.jsonl",
                        archive_root=archive_root,
                        staging_root=staging_root,
                    )

    def test_download_creates_only_verified_immutable_snapshot(self) -> None:
        fixture_body = b"fixture-mot-archive-content"
        redirect_url = (
            "https://release-assets.githubusercontent.com/"
            "github-production-release-asset/fixture?token=fixture"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_root = root / "source_archives"
            destination = archive_root / ARCHIVE_SNAPSHOT_NAME

            def fake_build_opener(handler: object) -> _FixtureOpener:
                return _FixtureOpener(handler, _FixtureResponse(fixture_body, redirect_url))

            with (
                patch("ai_core.mot_russian.EXPECTED_ARCHIVE_BYTES", len(fixture_body)),
                patch("ai_core.mot_russian.build_opener", side_effect=fake_build_opener),
            ):
                summary = download_mot_russian_archive(destination, archive_root=archive_root)

            report = json.dumps(summary.as_dict(), ensure_ascii=False)
            self.assertEqual(destination.read_bytes(), fixture_body)
            self.assertEqual(summary.archive_bytes, len(fixture_body))
            self.assertEqual(summary.archive_sha256, hashlib.sha256(fixture_body).hexdigest())
            self.assertFalse(summary.as_dict()["active_manifest_updated"])
            self.assertNotIn("token=fixture", report)

    def test_download_rejects_bad_response_without_creating_snapshot(self) -> None:
        fixture_body = b"fixture-mot-archive-content"
        redirect_url = (
            "https://release-assets.githubusercontent.com/"
            "github-production-release-asset/fixture?token=fixture"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_root = root / "source_archives"
            destination = archive_root / ARCHIVE_SNAPSHOT_NAME

            def fake_build_opener(handler: object) -> _FixtureOpener:
                return _FixtureOpener(handler, _FixtureResponse(fixture_body, redirect_url, content_type="text/plain"))

            with (
                patch("ai_core.mot_russian.EXPECTED_ARCHIVE_BYTES", len(fixture_body)),
                patch("ai_core.mot_russian.build_opener", side_effect=fake_build_opener),
            ):
                with self.assertRaises(MOTRussianError):
                    download_mot_russian_archive(destination, archive_root=archive_root)

            self.assertFalse(destination.exists())

    def test_fixed_release_and_redirect_allowlists_reject_untrusted_targets(self) -> None:
        validate_fixed_release_url(MOT_RUS_RELEASE_URL)
        self.assertEqual(DEFAULT_ARCHIVE_PATH.name, ARCHIVE_SNAPSHOT_NAME)
        self.assertTrue(
            is_allowed_release_redirect(
                "https://release-assets.githubusercontent.com/"
                "github-production-release-asset/example?token=fixture"
            )
        )
        self.assertFalse(
            is_allowed_release_redirect(
                "https://release-assets.githubusercontent.com/github-production-release-asset/example"
            )
        )
        self.assertFalse(
            is_allowed_release_redirect("https://example.test/github-production-release-asset/example?token=fixture")
        )
        with self.assertRaises(MOTRussianError):
            validate_fixed_release_url(MOT_RUS_RELEASE_URL + "?unexpected=1")
        with self.assertRaises(MOTRussianError):
            validate_fixed_release_url("https://example.test/archive.tgz")


if __name__ == "__main__":
    unittest.main()
