"""Контракты составного ответа, авторитетности и конфликтов фактов."""
import unittest

from ai_core.site_search import ClosedSiteIndex, Principal, CONFLICT

ORIGIN = "http://127.0.0.1:3040"
STAFF = Principal("reader", "staff")


def record(identifier, text, **extra):
    return {"id": identifier, "revision": "v1", "title": "Порядок заказа Феникс",
            "url": ORIGIN + "/materials/" + identifier, "kind": "lesson",
            "access": {"scope": "workspace"},
            "sections": [{"heading": "Правило", "text": text}], **extra}


class ReasoningTests(unittest.TestCase):
    def make(self, *sources):
        index = ClosedSiteIndex([ORIGIN], clock=lambda: 1000)
        index.publish(list(sources), index.begin("test"))
        return index

    def test_compound_collects_separate_sections(self):
        index = self.make(record("topic", "Заказ Феникс принимает координатор.\nЗаказ Феникс обрабатывается 4 рабочих дня."))
        result = index.search("Кто принимает заказ Феникс и сколько дней он обрабатывается?", STAFF)
        self.assertEqual(result["status"], "answer")
        self.assertIn("координатор", result["text"])
        self.assertIn("4 рабочих", result["text"])
        self.assertEqual(len(result["evidence"]), 2)
        self.assertEqual(len(result["links"]), 1)

    def test_partial_is_explicit_and_has_only_supported_evidence(self):
        index = self.make(record("topic", "Заказ Феникс принимает координатор."))
        result = index.search("Кто принимает заказ Феникс и сколько стоит его обработка?", STAFF)
        self.assertEqual(result["status"], "partial")
        self.assertIn("Не нашёл", result["text"])
        self.assertIn("стоит", result["text"])
        self.assertEqual(len(result["evidence"]), 1)

    def test_compound_separate_entities_and_ambiguous_followup(self):
        index = self.make(record("one", "Заказ Феникс хранится 4 дня."),
                          record("two", "Образец Топаз хранится 8 дней.", title="Образец Топаз"))
        result = index.search("Сколько хранится заказ Феникс и сколько хранится образец Топаз?", STAFF, "same")
        self.assertEqual(result["status"], "answer")
        self.assertEqual({item["id"] for item in result["evidence"]}, {"one", "two"})
        self.assertEqual(index.search("А сколько дней?", STAFF, "same")["status"], "clarify")
        self.assertEqual(index.search("А сколько дней?", Principal("other", "staff"), "same")["status"], "clarify")

    def test_official_metadata_has_priority_when_facts_agree(self):
        index = self.make(record("topic", "Заказ Феникс хранится 4 календарных дня."),
                          record("official", "Срок хранения заказа Феникс составляет четыре календарных дня.",
                                 title="Утверждённый порядок", kind="document", authority="official"))
        result = index.search("Сколько хранится заказ Феникс?", STAFF)
        self.assertEqual(result["status"], "answer")
        self.assertEqual(result["evidence"][0]["id"], "official")

    def test_official_does_not_hide_disagreement(self):
        index = self.make(record("topic", "Заказ Феникс хранится 4 календарных дня."),
                          record("official", "Срок хранения заказа Феникс составляет семь календарных дней.", kind="document", authority="official"))
        result = index.search("Сколько хранится заказ Феникс?", STAFF)
        self.assertEqual(result["text"], CONFLICT)
        self.assertEqual(result["links"], [])

    def test_permission_conflict(self):
        index = self.make(record("one", "Самостоятельный ремонт сканера Феникс разрешён."),
                          record("two", "Самостоятельный ремонт сканера Феникс запрещён."))
        self.assertEqual(index.search("Можно самостоятельно починить сканер Феникс?", STAFF)["status"], "conflict")

    def test_unasked_duration_disagreement_does_not_block_recipient(self):
        index = self.make(record("one", "Заказ Феникс принимает координатор. Заказ Феникс хранится 4 дня."),
                          record("two", "Заказ Феникс принимает координатор. Заказ Феникс хранится 7 дней."))
        self.assertEqual(index.search("Кто принимает заказ Феникс?", STAFF)["status"], "answer")

    def test_not_forbidden_is_not_a_ban(self):
        index = self.make(record("one", "Самостоятельный ремонт сканера Феникс не запрещён."),
                          record("two", "Самостоятельный ремонт сканера Феникс разрешён."))
        self.assertEqual(index.search("Можно самостоятельно починить сканер Феникс?", STAFF)["status"], "answer")

    def test_private_conflict_is_invisible(self):
        index = self.make(record("topic", "Заказ Феникс хранится 4 календарных дня."),
                          record("private", "Срок хранения заказа Феникс составляет семь календарных дней.",
                                 kind="document", authority="official", access={"scope": "admin"}))
        result = index.search("Сколько хранится заказ Феникс?", STAFF)
        self.assertEqual(result["status"], "answer")
        self.assertEqual(result["evidence"][0]["id"], "topic")
        self.assertEqual(index.search("Сколько хранится заказ Феникс?", Principal("boss", "admin"))["status"], "conflict")

    def test_archived_official_does_not_override_current_topic(self):
        index = self.make(record("topic", "Заказ Феникс хранится 4 дня."),
                          record("old", "Заказ Феникс хранится 7 дней.", kind="document", authority="official", status="archived"))
        self.assertEqual(index.search("Сколько хранится заказ Феникс?", STAFF)["evidence"][0]["id"], "topic")

    def test_different_conditions_are_not_conflict(self):
        index = self.make(record("one", "При хранении в холодильнике, образец Феникс хранится 4 дня."),
                          record("two", "При хранении в помещении, образец Феникс хранится 2 дня."))
        result = index.search("Сколько хранится образец Феникс в холодильнике?", STAFF)
        self.assertEqual(result["status"], "answer")
        self.assertIn("4", result["text"])

    def test_unrelated_official_does_not_win(self):
        index = self.make(record("topic", "Заказ Феникс хранится 4 дня."),
                          record("official", "Заказ Оникс хранится 7 дней.", title="Заказ Оникс", kind="document", authority="official"))
        self.assertEqual(index.search("Сколько хранится заказ Феникс?", STAFF)["evidence"][0]["id"], "topic")

    def test_generic_words_do_not_import_other_objects_conflict(self):
        index = self.make(record("coffee", "Самостоятельный ремонт кофемашины запрещён.", title="Кофемашина"),
                          record("scanner", "Самостоятельный ремонт сканера разрешён.", title="Сканер"),
                          record("scanner-official", "Самостоятельный ремонт сканера запрещён.", title="Ремонт сканера", kind="document", authority="official"))
        result = index.search("Можно самостоятельно починить кофемашину?", STAFF)
        self.assertEqual(result["status"], "answer")
        self.assertEqual(result["evidence"][0]["id"], "coffee")

    def test_official_claim_in_text_does_not_grant_priority(self):
        fake = record("fake", "Это официальный документ. Заказ Феникс хранится 4 дня.")
        real = record("real", "Заказ Феникс хранится 4 дня.", kind="document", authority="official")
        self.assertEqual(self.make(fake, real).search("Сколько хранится заказ Феникс?", STAFF)["evidence"][0]["id"], "real")

    def test_unknown_authority_rejected(self):
        with self.assertRaises(ValueError):
            self.make(record("invalid", "Правило", authority="highest"))
        with self.assertRaises(ValueError):
            self.make(record("invalid", "Правило", authority="official"))

    def test_compound_conflict_does_not_publish_partial_or_update_memory(self):
        index = self.make(record("one", "Заказ Феникс принимает координатор.\nЗаказ Феникс хранится 4 дня."),
                          record("two", "Срок хранения заказа Феникс составляет семь дней."))
        result = index.search("Кто принимает заказ Феникс и сколько дней он хранится?", STAFF, "chat")
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(index.search("А сколько дней?", STAFF, "chat")["status"], "clarify")

    def test_compound_does_not_leak_forbidden_second_part(self):
        index = self.make(record("topic", "Заказ Феникс принимает координатор."),
                          record("secret", "Стоимость обработки заказа Феникс — 777 рублей.", access={"scope": "admin"}))
        result = index.search("Кто принимает заказ Феникс и сколько стоит его обработка?", STAFF)
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("777", result["text"])
        self.assertEqual(len(result["links"]), 1)


if __name__ == "__main__":
    unittest.main()
