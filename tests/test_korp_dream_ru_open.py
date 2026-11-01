"""Проверяет изолированный DReaM staging-preview без реальных сетевых запросов."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ai_core.korp_dream_ru_open import (
    KORP_API_HOST,
    KORP_CQP,
    KORP_CORPUS_ID,
    KORP_SHOW_STRUCT,
    KorpDreaMPreviewError,
    build_korp_query_url,
    collect_dream_ru_open,
    preview_dream_ru_open,
    summary_markdown,
    validate_fixed_korp_url,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXED_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def _hit(
    words: list[str],
    *,
    sentence_id: str = "fixture-sentence-1",
    corpus: str = KORP_CORPUS_ID,
) -> dict:
    """Создаёт минимальную искусственную KWIC-запись, а не текст реального корпуса."""
    return {
        "corpus": corpus,
        "match": {"start": 0, "end": 1, "position": 0},
        "structs": {KORP_SHOW_STRUCT: sentence_id},
        "tokens": [{"word": word} for word in words],
    }


def _payload(
    kwic: list[dict],
    *,
    hits: int = 1_020_355,
    query_data: str | None = None,
) -> bytes:
    """Упаковывает изолированный JSON fixture, который не обращается к сети."""
    payload: dict[str, object] = {"hits": hits, "kwic": kwic}
    if query_data is not None:
        payload["query_data"] = query_data
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class KorpDreaMPreviewTests(unittest.TestCase):
    """Проверки allowlist, дедупликации, запрета active-данных и retry."""

    def test_preview_writes_only_safe_unique_staging_records(self) -> None:
        source_phrase = "Сегодня тихо"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"
            manifest = PROJECT_ROOT / "data" / "brain_v3" / "corpus_manifest.v1.json"
            manifest_before = hashlib.sha256(manifest.read_bytes()).hexdigest()
            requested_urls: list[str] = []

            def fetcher(url: str, timeout: float) -> bytes:
                requested_urls.append(url)
                self.assertEqual(timeout, 15.0)
                return _payload(
                    [
                        _hit(["Сегодня", "тихо", "."], sentence_id="fixture-sentence-1"),
                        _hit(["Сегодня", "тихо", "."], sentence_id="fixture-sentence-2"),
                        _hit(["Завтра", "будет", "ветер", "."], sentence_id="fixture-sentence-3"),
                        _hit(["https", ":", "/", "/", "пример", "."], sentence_id="fixture-sentence-4"),
                    ]
                )

            summary = preview_dream_ru_open(
                output,
                max_pages=1,
                matches_per_page=4,
                staging_root=staging,
                fetcher=fetcher,
                sleeper=lambda _: None,
                clock=lambda: FIXED_NOW,
            )
            report_json = json.dumps(summary.as_dict(), ensure_ascii=False)
            report_markdown = summary_markdown(summary)
            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            manifest_after = hashlib.sha256(manifest.read_bytes()).hexdigest()

        self.assertEqual(summary.records_written, 2)
        self.assertEqual(summary.rejections["exact_duplicate_context"], 1)
        self.assertEqual(summary.rejections["url_like"], 1)
        self.assertEqual(summary.records_rejected, 2)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record["record_type"] == "language_document" for record in records))
        self.assertTrue(all(record["provenance"]["source_id"] == "dream-ru-open-cc-by-v1" for record in records))
        self.assertNotIn(source_phrase, report_json)
        self.assertNotIn(source_phrase, report_markdown)
        self.assertFalse(summary.as_dict()["active_manifest_updated"])
        self.assertFalse(summary.as_dict()["training_started"])
        self.assertEqual(manifest_before, manifest_after)
        self.assertEqual(len(requested_urls), 1)
        self.assertEqual(urlparse(requested_urls[0]).hostname, KORP_API_HOST)

    def test_fixed_url_rejects_external_and_restricted_targets(self) -> None:
        url = build_korp_query_url(0, 2)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        validate_fixed_korp_url(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, KORP_API_HOST)
        self.assertEqual(query["corpus"], [KORP_CORPUS_ID])
        self.assertEqual(query["cqp"], [KORP_CQP])
        self.assertEqual(query["show_struct"], [KORP_SHOW_STRUCT])
        cached_url = build_korp_query_url(3, 5, query_data="A" * 32 + "=")
        self.assertEqual(parse_qs(urlparse(cached_url).query)["query_data"], ["A" * 32 + "="])
        with self.assertRaises(KorpDreaMPreviewError):
            validate_fixed_korp_url(
                "https://example.test/ws/korp/v8/query?corpus=DREAM-RU-OPEN&cqp=%5B%5D&start=0&end=2&default_context=1%20sentence&default_within=sentence"
            )
        with self.assertRaises(KorpDreaMPreviewError):
            validate_fixed_korp_url(
                "https://ws.spraakbanken.gu.se/ws/korp/v8/query?corpus=DREAM-RU-RESTRICTED&cqp=%5B%5D&start=0&end=2&default_context=1%20sentence&default_within=sentence"
            )

    def test_output_outside_staging_is_rejected_before_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            called = False

            def fetcher(url: str, timeout: float) -> bytes:
                nonlocal called
                called = True
                return _payload([_hit(["Безопасный", "тест", "."])])

            with self.assertRaises(KorpDreaMPreviewError):
                preview_dream_ru_open(
                    root / "outside.jsonl",
                    staging_root=staging,
                    fetcher=fetcher,
                )

        self.assertFalse(called)

    def test_restricted_or_malformed_hit_cannot_create_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"

            with self.assertRaises(KorpDreaMPreviewError):
                preview_dream_ru_open(
                    output,
                    staging_root=staging,
                    fetcher=lambda _url, _timeout: _payload(
                        [_hit(["Тест", "."], corpus="DREAM-RU-RESTRICTED")]
                    ),
                    sleeper=lambda _: None,
                )

            self.assertFalse(output.exists())

    def test_near_duplicate_context_is_filtered_before_split(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"

            summary = preview_dream_ru_open(
                output,
                max_pages=1,
                matches_per_page=2,
                staging_root=staging,
                fetcher=lambda _url, _timeout: _payload(
                    [
                        _hit(["Мы", "обсудили", "план", "."], sentence_id="fixture-sentence-1"),
                        _hit(["Мы", "обсудили", "план", "!"], sentence_id="fixture-sentence-2"),
                    ],
                    hits=2,
                ),
                sleeper=lambda _: None,
            )

        self.assertEqual(summary.records_written, 1)
        self.assertEqual(summary.rejections["near_duplicate_candidate"], 1)

    def test_complete_collection_reads_all_pages_and_uses_server_cache_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"
            requested_urls: list[str] = []

            def fetcher(url: str, timeout: float) -> bytes:
                requested_urls.append(url)
                start = int(parse_qs(urlparse(url).query)["start"][0])
                if start == 0:
                    return _payload(
                        [
                            _hit(["Первый", "тест", "."], sentence_id="fixture-sentence-1"),
                            _hit(["Второй", "тест", "."], sentence_id="fixture-sentence-2"),
                        ],
                        hits=3,
                        query_data="A" * 32 + "=",
                    )
                if start == 2:
                    return _payload(
                        [_hit(["Третий", "тест", "."], sentence_id="fixture-sentence-3")],
                        hits=3,
                        query_data="A" * 32 + "=",
                    )
                self.fail("Collection запросил страницу за пределами заявленного hits.")

            summary = collect_dream_ru_open(
                output,
                max_pages=2,
                matches_per_page=2,
                staging_root=staging,
                fetcher=fetcher,
                sleeper=lambda _: None,
                clock=lambda: FIXED_NOW,
            )

        self.assertTrue(summary.collection_complete)
        self.assertEqual(summary.requests_made, 2)
        self.assertEqual(summary.kwic_hits_received, 3)
        self.assertEqual(summary.records_written, 3)
        self.assertIn("query_data", parse_qs(urlparse(requested_urls[1]).query))

    def test_complete_collection_fails_if_page_cap_cannot_cover_hits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"

            with self.assertRaises(KorpDreaMPreviewError):
                collect_dream_ru_open(
                    output,
                    max_pages=1,
                    matches_per_page=2,
                    staging_root=staging,
                    fetcher=lambda _url, _timeout: _payload(
                        [
                            _hit(["Первый", "тест", "."], sentence_id="fixture-sentence-1"),
                            _hit(["Второй", "тест", "."], sentence_id="fixture-sentence-2"),
                        ],
                        hits=3,
                    ),
                    sleeper=lambda _: None,
                )

            self.assertFalse(output.exists())

    def test_network_failure_is_retried_with_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"
            calls = 0
            sleeps: list[float] = []

            def fetcher(url: str, timeout: float) -> bytes:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("fixture network failure")
                return _payload([_hit(["Тест", "после", "повтора", "."])])

            summary = preview_dream_ru_open(
                output,
                staging_root=staging,
                fetcher=fetcher,
                sleeper=sleeps.append,
                clock=lambda: FIXED_NOW,
            )

        self.assertEqual(calls, 2)
        self.assertEqual(sleeps, [1.0])
        self.assertEqual(summary.records_written, 1)

    def test_network_exhaustion_leaves_no_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"
            calls = 0
            sleeps: list[float] = []

            def fetcher(url: str, timeout: float) -> bytes:
                nonlocal calls
                calls += 1
                raise OSError("fixture persistent network failure")

            with self.assertRaises(KorpDreaMPreviewError):
                preview_dream_ru_open(
                    output,
                    staging_root=staging,
                    fetcher=fetcher,
                    sleeper=sleeps.append,
                )

            self.assertFalse(output.exists())

        self.assertEqual(calls, 4)
        self.assertEqual(sleeps, [1.0, 2.0, 4.0])

    def test_existing_staging_output_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            staging.mkdir()
            output = staging / "candidate.jsonl"
            output.write_text("existing", encoding="utf-8")

            with self.assertRaises(KorpDreaMPreviewError):
                preview_dream_ru_open(
                    output,
                    staging_root=staging,
                    fetcher=lambda _url, _timeout: _payload([_hit(["Тест", "."])]),
                )

    def test_missing_sentence_id_cannot_create_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = staging / "candidate.jsonl"
            payload = _hit(["Тест", "."])
            payload.pop("structs")

            with self.assertRaises(KorpDreaMPreviewError):
                preview_dream_ru_open(
                    output,
                    staging_root=staging,
                    fetcher=lambda _url, _timeout: _payload([payload]),
                    sleeper=lambda _: None,
                )

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
