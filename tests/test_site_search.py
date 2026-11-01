"""Функциональный контракт закрытого поиска, без сети и частных рабочих данных."""
import unittest
from ai_core.site_search import ClosedSiteIndex, Principal, NO_DATA, plain_text

ORIGIN = "http://127.0.0.1:3040"


def source(identifier, text, title="Порядок обработки заявок", **extra):
    return {"id": identifier, "revision": "v1", "title": title, "url": ORIGIN + "/tasks?tab=docs&documentId=" + identifier,
            "access": {"scope": "workspace"}, "sections": [{"heading": title, "text": text}], **extra}


class ClosedSearchTests(unittest.TestCase):
    def make(self, sources):
        index = ClosedSiteIndex([ORIGIN], clock=lambda: 1000)
        index.publish(sources, index.begin("fixture"))
        return index

    def test_russian_forms_and_typo(self):
        index = self.make([source("a", "Заявка Север обрабатывается в течение трёх рабочих дней.")])
        result = index.search("Как обрабатываются заявки Север?", Principal("a", "staff"))
        self.assertEqual(result["status"], "answer")
        self.assertIn("трёх", result["text"])
        self.assertEqual(index.search("Как обрабатываются заяки Север?", Principal("a", "staff"))["status"], "answer")

    def test_no_data_has_no_internet_or_sources(self):
        index = self.make([source("a", "Заявка Север обрабатывается три дня.")])
        result = index.search("Какая погода на Марсе?", Principal("a", "staff"))
        self.assertEqual(result["text"], NO_DATA)
        self.assertEqual(result["links"], [])

    def test_private_source_does_not_affect_visible_answer(self):
        normal = source("a", "Заявка Север обрабатывается три дня.")
        hidden = source("b", "Заявка Север обрабатывается сто дней.", access={"scope": "private", "owner_id": "other"})
        index = self.make([normal, hidden])
        answer = index.search("Как обрабатывается заявка Север?", Principal("a", "staff"))
        self.assertNotIn("сто", answer["text"])
        self.assertEqual(answer["evidence"][0]["id"], "a")
        self.assertEqual(index.search("Север", Principal("a", "staff"), selected_source="b")["status"], "not_found")

    def test_parent_permission_and_expiry(self):
        parent = {"access": {"scope": "admin"}}
        index = self.make([source("a", "Заявка Север обрабатывается три дня.", parents=[parent]),
                           source("b", "Заявка Юг обрабатывается пять дней.", expires_at=999)])
        self.assertEqual(index.search("Заявка Север", Principal("a", "staff"))["status"], "not_found")
        self.assertEqual(index.search("Заявка Юг", Principal("admin", "admin"))["status"], "not_found")

    def test_followup_does_not_cross_chat_or_user(self):
        index = self.make([source("a", "Заявка Север обрабатывается в течение трёх рабочих дней.")])
        person = Principal("a", "staff")
        index.search("Как обрабатывается заявка Север?", person, "chat-one")
        self.assertTrue(index.search("А сколько дней?", person, "chat-one")["context_used"])
        self.assertEqual(index.search("А сколько дней?", person, "chat-two")["status"], "clarify")
        self.assertEqual(index.search("А сколько дней?", Principal("b", "staff"), "chat-one")["status"], "clarify")

    def test_deletion_and_invalid_snapshot_remove_old_evidence(self):
        index = self.make([source("a", "Заявка Север обрабатывается три дня.")])
        index.publish([], index.begin("deleted"))
        self.assertEqual(index.search("Заявка Север", Principal("a", "staff"))["status"], "not_found")
        generation = index.begin("invalid")
        with self.assertRaises(ValueError):
            index.publish([source("a", "text", url="https://outside.invalid/page")], generation)
        self.assertEqual(index.search("Заявка Север", Principal("a", "staff"))["status"], "unavailable")

    def test_newer_snapshot_cannot_be_overwritten_by_old_ocr(self):
        index = self.make([])
        old = index.begin("older")
        new = index.begin("newer")
        self.assertFalse(index.publish([source("a", "Старое правило")], old))
        index.publish([source("b", "Заявка Север обрабатывается три дня.")], new)
        self.assertNotIn("a", index.sources)

    def test_current_manifest_prevents_stale_permissions_and_text(self):
        index = self.make([source("a", "Заявка Север обрабатывается три дня.")])
        actor = Principal("a", "staff")
        self.assertEqual(index.search("Заявка Север", actor, current_revisions={})["status"], "not_found")
        self.assertEqual(index.search("Заявка Север", actor, current_revisions={"a": "new-version"})["status"], "processing")

    def test_unambiguous_numeric_conflict(self):
        index = self.make([source("a", "Срок процесса Орион составляет 5 рабочих дней.", title="Регламент Орион"),
                           source("b", "Срок процесса Орион составляет 7 рабочих дней.", title="Уточнение Орион")])
        self.assertEqual(index.search("Какой срок процесса Орион?", Principal("a", "staff"))["status"], "conflict")

    def test_negation_is_preserved(self):
        index = self.make([source("a", "Образцы Лира нельзя хранить рядом с бытовой химией.", title="Хранение образцов")])
        self.assertIn("нельзя", index.search("Как хранить образцы Лира?", Principal("a", "staff"))["text"])

    def test_visible_html_and_table_text(self):
        text = plain_text('<nav>Старое правило 99</nav><p>Новое правило 12 дней.</p><div hidden>секрет</div><script>fetch("outside")</script>')
        self.assertIn("12 дней", text)
        self.assertNotIn("99", text)
        self.assertNotIn("секрет", text)
        self.assertNotIn("fetch", text)

    def test_recipient_is_not_replaced_with_deadline(self):
        index = self.make([source("a", "Заявка Север передаётся старшему сотруднику через внутреннюю форму.\nЗаявка Север обрабатывается три дня.")])
        for question in ["Кому направить заявку Север?", "Как отправить заявку Север?"]:
            result = index.search(question, Principal("a", "staff"))
            self.assertIn("старшему", result["text"])
            self.assertNotIn("три дня", result["text"])

    def test_steps_are_not_replaced_with_opening_time(self):
        record = source("a", "", title="Открытие смены")
        record["sections"] = [{"heading": "Время открытия", "text": "Кассовая смена открывается в 8.00. Закрывается смена в 20.00."},
                              {"heading": "Первые действия при открытии", "text": "Осмотрите помещение. Затем снимите помещение с охраны."}]
        result = self.make([record]).search("Что сделать при открытии смены?", Principal("a", "staff"))
        self.assertIn("Осмотрите", result["text"])
        self.assertIn("охраны", result["text"])
        self.assertNotIn("20.00", result["text"])

    def test_unrelated_extra_sentence_is_not_returned(self):
        record = source("a", "При уборке улицы используются хлопчатобумажные и резиновые перчатки. При мытье полов в помещении — только резиновые перчатки.", title="Гигиена")
        result = self.make([record]).search("В каких перчатках моют пол в помещении?", Principal("a", "staff"))
        self.assertIn("только резиновые", result["text"])
        self.assertNotIn("улицы", result["text"])

    def test_unknown_duration_followup_does_not_repeat_previous_answer(self):
        index = self.make([source("a", "Заявка Север передаётся старшему сотруднику через внутреннюю форму.")])
        actor = Principal("a", "staff")
        index.search("Кому направить заявку Север?", actor, "one")
        self.assertEqual(index.search("А сколько дней?", actor, "one")["status"], "not_found")

    def test_new_topic_does_not_inherit_previous_subject(self):
        index = self.make([source("a", "Заявка Север передаётся старшему сотруднику."),
                           source("b", "Образцы Лира хранятся при температуре 16–18 °C.", title="Хранение образцов")])
        actor = Principal("a", "staff")
        index.search("Кому направить заявку Север?", actor, "one")
        result = index.search("А как хранить образцы Лира?", actor, "one")
        self.assertIn("16–18", result["text"])
        self.assertFalse(result["context_used"])

    def test_single_digit_and_entity_name_are_not_lost(self):
        index = self.make([source("a", "Число этикеток для набора Янтарь: 2.", title="Нормы упаковки"),
                           source("b", "Вкусовой профиль: семечки.", title="Лунцзин")])
        self.assertIn("2", index.search("Сколько этикеток для набора Янтарь?", Principal("a", "staff"))["text"])
        self.assertIn("Лунцзин", index.search("Какой чай имеет вкус семечек?", Principal("a", "staff"))["text"])

    def test_long_sentence_keeps_condition_and_negation(self):
        sentence = "Если " + "условие " * 260 + "выполняется, образцы Лира нельзя хранить рядом с химией."
        index = self.make([source("a", sentence, title="Хранение образцов Лира")])
        answer = index.search("Как хранить образцы Лира рядом с химией?", Principal("a", "staff"))
        self.assertIn("нельзя", answer["text"])
        self.assertTrue(answer["text"].startswith("Если"))

    def test_price_question_cannot_be_answered_with_repair_procedure(self):
        index = self.make([source("a", "При поломке кофемашины позвоните администратору. Самостоятельный ремонт запрещён.", title="Поломка кофемашины")])
        self.assertEqual(index.search("Сколько стоит ремонт кофемашины?", Principal("a", "staff"))["status"], "not_found")

    def test_unspecified_price_is_not_a_positive_price_fact(self):
        index = self.make([source("a", "Отмена заявки Альфа оформляется через форму. Стоимость отмены заявки Альфа не указана.", title="Отмена Альфа")])
        self.assertEqual(index.search("Сколько стоит отмена заявки Альфа?", Principal("a", "staff"))["status"], "not_found")

    def test_hypothetical_weight_preserves_rule_and_does_not_require_same_number(self):
        index = self.make([source("a", "Минимальное взвешивание — 80 г. Взвешивание менее 80 г не производится.", title="Взвешивание чая")])
        answer = index.search("Можно отвесить 15 граммов чая?", Principal("a", "staff"))
        self.assertEqual(answer["status"], "answer")
        self.assertIn("80", answer["text"])
        self.assertIn("не производится", answer["text"])

    def test_question_after_full_stop_is_not_a_named_entity(self):
        index = self.make([source("a", "Если кофемашина сломалась, сотрудник звонит администратору.", title="Неисправность")])
        answer = index.search("Кофемашина перестала работать. Кому сообщить?", Principal("a", "staff"))
        self.assertEqual(answer["status"], "answer")
        self.assertIn("администратору", answer["text"])

    def test_new_topic_works_after_snapshot_without_answer_catalogue(self):
        index = self.make([])
        question = "Какой срок хранения заказа Факел?"
        actor = Principal("a", "staff")
        self.assertEqual(index.search(question, actor)["status"], "not_found")
        index.publish([source("fresh", "Заказ Факел хранится 11 календарных дней.", title="Хранение заказа Факел")], index.begin("new-topic"))
        self.assertIn("11 календарных", index.search(question, actor)["text"])


if __name__ == "__main__":
    unittest.main()
