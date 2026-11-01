"""Проверки нового review используют временный ledger, не решения пользователя."""

from dataclasses import replace
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from evaluation import oasst2_quality_review as review
from evaluation.oasst2_quality_review_web import create_app, PAGE


def http_request(app, path, *, method="GET", payload=None, host="127.0.0.1", origin=None):
    """Минимальный ASGI-клиент без установки дополнительных HTTP-библиотек."""
    async def run():
        sent = []
        body = json.dumps(payload).encode() if payload is not None else b""
        headers = [(b"host", b"127.0.0.1:8015")]
        if payload is not None:
            headers.append((b"content-type", b"application/json"))
        if origin:
            headers.append((b"origin", origin.encode()))
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
                 "query_string": b"", "root_path": "", "headers": headers,
                 "client": (host, 12345), "server": ("127.0.0.1", 8015)}
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        async def send(message):
            sent.append(message)
        await app(scope, receive, send)
        status_code = next(message["status"] for message in sent if message["type"] == "http.response.start")
        response_body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
        return status_code, response_body
    return asyncio.run(run())


class QualityReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.paths = replace(review.ReviewPaths(), queue=root / "queue.json", ledger=root / "ledger.json")
        self.queue = review.prepare(self.paths)
        self.identifier = self.queue["items"][0]["id"]

    def tearDown(self):
        self.directory.cleanup()

    def test_reproducible_stratification(self):
        report, _ = review.load_inputs(self.paths)
        self.assertEqual(self.queue, review.build_queue(list(reversed(report["items"]))))
        self.assertEqual(self.queue["sample_records"], 36)
        self.assertEqual(self.queue["split_counts"], {"train": 24, "validation": 6, "test": 6})
        self.assertGreaterEqual(self.queue["turn_counts"]["6"], 8)
        self.assertGreaterEqual(self.queue["no_hint_count"], 4)
        hints = {hint for item in report["items"] for hint in item["candidate_topic_hints"]}
        self.assertEqual(set(self.queue["hint_counts"]), hints)

    def test_prepare_does_not_rewrite_queue(self):
        before = self.paths.queue.read_bytes()
        review.prepare(self.paths)
        self.assertEqual(before, self.paths.queue.read_bytes())
        self.assertFalse(self.paths.ledger.exists())

    def test_explicit_read_required(self):
        with self.assertRaisesRegex(review.ReviewError, "confirmation"):
            review.record_decision(self.identifier, "acceptable", "none_obvious", False, self.paths)
        self.assertFalse(self.paths.ledger.exists())

    def test_decision_roundtrip_and_duplicate(self):
        review.record_decision(self.identifier, "needs_check", "factual_uncertainty", True, self.paths)
        self.assertEqual(review.status(self.paths)["reviewed_records"], 1)
        self.assertNotEqual(review.next_case(self.paths)["id"], self.identifier)
        before = self.paths.ledger.read_bytes()
        with self.assertRaisesRegex(review.ReviewError, "duplicate"):
            review.record_decision(self.identifier, "acceptable", "none_obvious", True, self.paths)
        self.assertEqual(before, self.paths.ledger.read_bytes())
        self.assertNotIn('"turns"', before.decode())
        self.assertNotIn('"text"', before.decode())

    def test_unknown_and_invalid_reason(self):
        for identifier, verdict, reason in (("unknown", "acceptable", "none_obvious"),
                                          (self.identifier, "needs_check", "none_obvious"),
                                          (self.identifier, "approve_all", "none_obvious")):
            with self.assertRaises(review.ReviewError):
                review.record_decision(identifier, verdict, reason, True, self.paths)
        self.assertFalse(self.paths.ledger.exists())

    def test_queue_drift_blocks_read(self):
        changed = {**self.queue, "promotion_allowed": True}
        self.paths.queue.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(review.ReviewError, "queue_drift"):
            review.status(self.paths)

    def test_candidate_hash_drift(self):
        altered = Path(self.directory.name) / "altered.jsonl"
        altered.write_bytes(self.paths.candidate.read_bytes() + b"\n")
        with self.assertRaisesRegex(review.ReviewError, "hash_drift"):
            review.load_inputs(replace(self.paths, candidate=altered))

    def test_ledger_rejects_extra_text(self):
        review.record_decision(self.identifier, "exclude", "irrelevant", True, self.paths)
        rows = json.loads(self.paths.ledger.read_text(encoding="utf-8"))
        rows[0]["text"] = "raw text is not allowed"
        self.paths.ledger.write_text(json.dumps(rows), encoding="utf-8")
        with self.assertRaisesRegex(review.ReviewError, "schema"):
            review.status(self.paths)

    def test_http_routes_and_save(self):
        app = create_app(self.paths)
        self.assertEqual(http_request(app, "/")[0], 200)
        self.assertEqual(json.loads(http_request(app, "/api/status")[1])["pending_records"], 36)
        case = json.loads(http_request(app, "/api/next")[1])["candidate"]
        self.assertEqual(len(case["turns"]), case["turn_count"])
        payload = {"candidate_id": case["id"], "verdict": "acceptable",
                   "reason_code": "none_obvious", "read_confirmed": False}
        self.assertEqual(http_request(app, "/api/decisions", method="POST", payload=payload)[0], 400)
        payload["read_confirmed"] = True
        self.assertEqual(http_request(app, "/api/decisions", method="POST", payload=payload)[0], 201)
        self.assertEqual(http_request(app, "/api/decisions", method="POST", payload=payload)[0], 400)
        payload["extra"] = "forbidden"
        self.assertEqual(http_request(app, "/api/decisions", method="POST", payload=payload)[0], 422)
        self.assertEqual(http_request(app, "/api/status", origin="https://example.com")[0], 403)
        self.assertEqual(http_request(app, "/", host="192.0.2.1")[0], 403)

    def test_complete_sample_not_training_permission(self):
        for item in self.queue["items"]:
            review.record_decision(item["id"], "acceptable", "none_obvious", True, self.paths)
        self.assertIsNone(review.next_case(self.paths))
        snapshot = review.status(self.paths)
        self.assertEqual(snapshot["pending_records"], 0)
        self.assertFalse(snapshot["training_candidate_allowed"])
        self.assertFalse(snapshot["promotion_allowed"])

    def test_page_renders_text_not_html(self):
        self.assertIn("text.textContent=turn.text", PAGE)
        self.assertNotIn("innerHTML", PAGE)

    def test_corrupt_ledger_returns_controlled_error(self):
        self.paths.ledger.write_text("{broken", encoding="utf-8")
        code, body = http_request(create_app(self.paths), "/api/status")
        self.assertEqual(code, 400)
        self.assertIn("stored_json_invalid", json.loads(body)["detail"])


if __name__ == "__main__":
    unittest.main()
