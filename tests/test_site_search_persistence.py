"""Рестарт, актуальность и изоляция сохранённого поискового контекста."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_core.site_search import Principal
from ai_core.site_search_runtime import DurableSearch, CONTEXT_TTL
from ai_core.site_search_store import encode

ORIGIN = "http://127.0.0.1:3040"
ACTOR = Principal("employee-a", "staff")


def record(identifier="north", revision="v1", **extra):
    return {"id": identifier, "revision": revision, "title": "Заявка Север", "kind": "document",
            "url": ORIGIN + "/materials/" + identifier, "access": {"scope": "workspace"},
            "sections": [{"heading": "Отправка", "text": "Заявка Север передаётся старшему сотруднику."},
                         {"heading": "Срок", "text": "Заявка Север обрабатывается 3 рабочих дня."}], **extra}


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vates-persistence-test-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "site.sqlite3"
        self.now = 10000.0
        self.runtime = self.open()
        self.runtime.publish([record()], self.runtime.begin("initial"))

    def open(self, path=None):
        return DurableSearch([ORIGIN], path or self.path, clock=lambda: self.now)

    def ask(self, text, actor=ACTOR, chat="chat", revisions=None, runtime=None):
        current = runtime or self.runtime
        return current.query(text, actor, chat, None, {"north": "v1"} if revisions is None else revisions)

    def seed(self):
        self.assertEqual(self.ask("Кому направить заявку Север?")["status"], "answer")

    def test_index_and_context_restore_without_tokenizing_documents(self):
        self.seed()
        with patch("ai_core.site_search.terms", side_effect=AssertionError("retokenized")):
            self.runtime = self.open()
        self.assertTrue(self.runtime.status()["restored_from_disk"])
        self.assertEqual(self.runtime.status()["passages"], 2)
        answer = self.ask("А сколько дней?")
        self.assertEqual(answer["status"], "answer")
        self.assertTrue(answer["context_used"])
        self.assertIn("3 рабочих", answer["text"])

    def test_two_users_and_two_chats_stay_separate_after_restart(self):
        self.seed()
        self.runtime = self.open()
        self.assertEqual(self.ask("А сколько дней?", actor=Principal("employee-b", "staff"))["status"], "clarify")
        self.assertEqual(self.ask("А сколько дней?", chat="another")["status"], "clarify")
        self.assertEqual(self.ask("А сколько дней?", actor=Principal("employee-a", "admin"))["status"], "clarify")
        self.assertEqual(self.ask("А сколько дней?")["status"], "answer")

    def test_site_storage_is_separate(self):
        self.seed()
        other = self.open(Path(self.directory.name) / "other.sqlite3")
        other.publish([record()], other.begin("initial"))
        self.assertEqual(self.ask("А сколько дней?", runtime=other)["status"], "clarify")

    def test_context_survives_next_day_but_expires_after_thirty_days(self):
        self.seed()
        self.now += 86400
        self.runtime = self.open()
        self.assertEqual(self.ask("А сколько дней?")["status"], "answer")
        self.now += CONTEXT_TTL + 1
        self.runtime = self.open()
        self.assertEqual(self.ask("А сколько дней?")["status"], "clarify")
        self.assertEqual(self.runtime.store.contexts(), [])

    def test_live_manifest_revocation_erases_restored_context(self):
        self.seed()
        self.runtime = self.open()
        result = self.ask("А сколько дней?", revisions={})
        self.assertNotEqual(result["status"], "answer")
        self.assertEqual(result["evidence"], [])
        self.runtime = self.open()
        self.assertEqual(self.ask("А сколько дней?")["status"], "clarify")

    def test_new_revision_cannot_use_stale_saved_answer(self):
        self.seed()
        self.runtime = self.open()
        self.assertEqual(self.ask("Сколько обрабатывается заявка Север?", revisions={"north": "v2"})["status"], "processing")
        updated = record(revision="v2")
        updated["sections"][1]["text"] = "Заявка Север обрабатывается 8 рабочих дней."
        self.runtime.publish([updated], self.runtime.begin("updated"))
        self.assertEqual(self.ask("А сколько дней?", revisions={"north": "v2"})["status"], "clarify")
        self.assertIn("8 рабочих", self.ask("Сколько обрабатывается заявка Север?", revisions={"north": "v2"})["text"])

    def test_unrelated_snapshot_preserves_unchanged_chat(self):
        self.seed()
        self.runtime.publish([record(), record("extra")], self.runtime.begin("additional"))
        self.runtime = self.open()
        self.assertEqual(self.ask("А сколько дней?", revisions={"north": "v1", "extra": "v1"})["status"], "answer")

    def test_pending_snapshot_cannot_restore_old_index_after_crash(self):
        self.seed()
        self.runtime.begin("pending-delete")
        self.runtime = self.open()
        self.assertEqual(self.runtime.status()["sources"], 0)
        self.assertNotEqual(self.ask("А сколько дней?")["status"], "answer")
        self.runtime.publish([], self.runtime.begin("pending-delete"))
        self.runtime = self.open()
        self.assertEqual(self.runtime.store.contexts(), [])

    def test_expired_and_hidden_sources_invalidate_memory(self):
        for change in ({"expires_at": 9999}, {"access": {"scope": "admin"}}):
            self.runtime.publish([record()], self.runtime.begin("visible-" + str(change)))
            self.seed()
            self.runtime.publish([record(**change)], self.runtime.begin("hidden-" + str(change)))
            self.runtime = self.open()
            self.assertEqual(self.runtime.store.contexts(), [])
            self.assertNotEqual(self.ask("А сколько дней?")["status"], "answer")

    def test_old_worker_cannot_replace_new_snapshot(self):
        old = self.runtime.begin("old")
        new = self.runtime.begin("new")
        self.assertFalse(self.runtime.publish([record("stale")], old))
        self.runtime.publish([record()], new)
        self.assertEqual(set(self.open().index.sources), {"north"})

    def test_corrupted_payload_is_not_served(self):
        with self.runtime.store.connection() as db:
            db.execute("UPDATE index_state SET payload='damaged'")
        self.runtime = self.open()
        self.assertEqual(self.runtime.status()["state"], "unavailable")
        self.assertNotEqual(self.ask("Кому направить заявку Север?")["status"], "answer")

    def test_changed_engine_rebuilds_saved_text(self):
        self.seed()
        with self.runtime.store.connection() as db:
            payload = json.loads(db.execute("SELECT payload FROM index_state").fetchone()[0])
            payload["engine"] = "previous-algorithm"
            payload = encode(payload)
            db.execute("UPDATE index_state SET payload=?,digest=?", (payload, hashlib.sha256(payload.encode()).hexdigest()))
        self.runtime = self.open()
        self.assertTrue(self.runtime.status()["rebuilt_from_text"])
        self.assertEqual(self.ask("А сколько дней?")["status"], "answer")

    def test_failed_commit_does_not_report_success(self):
        with patch.object(self.runtime.store, "save_context", side_effect=OSError("disk full")):
            self.assertEqual(self.ask("Кому направить заявку Север?")["status"], "unavailable")
        self.assertEqual(self.runtime.index.sessions, {})

    def test_storage_does_not_contain_original_messages_or_answers(self):
        self.assertEqual(self.ask("Кому направить заявку Север? confidential-custom-marker")["status"], "answer")
        with self.runtime.store.connection() as db:
            payloads = " ".join(row[0] for row in db.execute("SELECT payload FROM chat_context"))
        self.assertNotIn("confidential-custom-marker", payloads)
        self.assertTrue(payloads)
        self.assertNotIn("Кому направить", payloads)
        self.assertNotIn("старшему сотруднику", payloads)

    def test_unneeded_source_fields_are_not_persisted(self):
        item = record(file_data="raw-upload-marker", extra_private_metadata="unneeded-marker")
        self.runtime.publish([item], self.runtime.begin("extra-metadata"))
        with self.runtime.store.connection() as db:
            payload = db.execute("SELECT payload FROM index_state").fetchone()[0]
        self.assertNotIn("raw-upload-marker", payload)
        self.assertNotIn("unneeded-marker", payload)

    def test_changed_origin_does_not_reuse_old_chat(self):
        self.seed()
        changed = DurableSearch(["http://127.0.0.1:9999"], self.path, clock=lambda: self.now)
        self.assertEqual(changed.status()["state"], "unavailable")
        self.assertEqual(changed.store.contexts(), [])

    def test_context_retention_is_bounded(self):
        with self.runtime.store.connection() as db:
            for number in range(1005):
                scope = encode(("reader", "staff", (), "chat-" + str(number)))
                context = encode({"time": self.now, "terms": ["север"], "references": {"north": "v1"}})
                db.execute("INSERT INTO chat_context VALUES(?,?,?)", (scope, context, self.now))
            db.execute("INSERT INTO chat_context VALUES(?,?,?)", ('["expired","staff",[],"chat"]', '{}', self.now-CONTEXT_TTL-1))
        self.seed()
        with self.runtime.store.connection() as db:
            count = db.execute("SELECT count(*) FROM chat_context").fetchone()[0]
            old = db.execute("SELECT count(*) FROM chat_context WHERE updated<?", (self.now-CONTEXT_TTL,)).fetchone()[0]
        self.assertEqual(count, 1000)
        self.assertEqual(old, 0)


if __name__ == "__main__":
    unittest.main()
