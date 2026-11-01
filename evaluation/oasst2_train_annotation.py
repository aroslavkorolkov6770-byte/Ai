"""Предложения смысловых меток после чтения 24 train-диалогов агентом.

Не человеческое одобрение, не исправленные ответы и не training snapshot.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from evaluation import oasst2_quality_review as review
from evaluation import oasst2_review_audit as audit
from evaluation.oasst1_material_triage import _contains_raw_content

OUTPUT = audit.RESULTS_ROOT / "oasst2-train-annotation-proposals-v0.1"

# Это ручные предложения после чтения всех ходов, не поиск ключевых слов.
# Индексы 1-based указывают сообщения, которые нужно перепроверить.
SPECS = {
    "04ab162d32955e0b6a894e74dc14": ("explanation_and_instruction", "explain_or_plan", "neutral", ["fact_check", "safety_check"], [4, 6], "Технические и биологические утверждения без проверенных источников; вопрос излучения требует проверки безопасности."),
    "1a4b7e6d574666ee59b8c42b56e4": ("safety_boundaries", "safe_boundary", "neutral", ["coercive_advice", "context_lost"], [4], "Ответ предлагает угрозы, шантаж и симуляцию травмы вместо обоснования предыдущего выбора."),
    "1a5debc6f8d833defe57ab60cc66": ("explanation_and_instruction", "explain_or_plan", "neutral", ["source_material_check", "fact_check"], [2, 4], "Есть ссылка словами на Википедию и неподтверждённые числовые оценки рынка."),
    "1eb73700565145b020d0654e7254": ("correction", "acknowledge_and_correct", "neutral", ["correction_not_acknowledged", "tone_check"], [2, 4], "Пользователь указал на ошибочное доказательство; ответ не объясняет недопустимый шаг и уходит в мрачную шутку."),
    "1fd435c775d562cafe9eb4a70b38": ("casual_dialogue", "follow_up_with_context", "concise", [], [], "Творческое продолжение выполняет просьбу о трёх дополнительных вариантах."),
    "28a536bf702c9777821a4785ddfa": ("explanation_and_instruction", "follow_up_with_context", "concise", ["unhelpful_answer"], [2, 4], "Нет практического объяснения запуска на телефоне; вместо продолжения предложена смена темы."),
    "2aa25550210aa0c0a821805849fc": ("safety_boundaries", "safe_boundary", "supportive", ["safety_check", "unhelpful_answer"], [2, 4], "Совет в заявленной ситуации выживания слишком общий; первая рекомендация требует проверки безопасности."),
    "4926ec4e5e35f78de64f6f26c585": ("opinion_and_choice", "reflect_and_decide", "thoughtful", ["safety_check"], [4], "Нужно проверить уместность советов о доминирующем партнёре и достаточность границ безопасности."),
    "4ce8a6e05725beba764d2f028fe9": ("casual_dialogue", "follow_up_with_context", "thoughtful", [], [], "Разговор развивает загадку и переходит к метафорическому уточнению."),
    "55ddb29f974169105ad1823fba56": ("correction", "acknowledge_and_correct", "neutral", ["self_learning_claim", "correction_not_applied"], [6], "Обещано использование замечаний для обучения; исправленная версия сказки не предложена."),
    "6dcb12948e80672c1dd39c4b9b46": ("safety_boundaries", "safe_boundary", "neutral", ["unhelpful_answer", "tone_check", "safety_check"], [2, 4], "Ответ на нападение собаки не даёт нужных шагов; продолжение звучит издевательски."),
    "6ffc7bf5393f7d36df93eefb32b5": ("explanation_and_instruction", "explain_or_plan", "neutral", ["fact_check", "wording_check"], [4], "Техническое объяснение содержит неясные формулировки и смешивает HDD с флешкой."),
    "70d4b86c39b7c295710ca4fc8e80": ("casual_dialogue", "follow_up_with_context", "concise", ["context_response_check"], [4], "После уточнения про белого медведя почти дословно повторён исходный ответ."),
    "86b4c874281e2307648ffd97c363": ("opinion_and_choice", "reflect_and_decide", "thoughtful", ["fact_check", "question_coverage_check"], [2, 4], "Исторические обобщения требуют проверки; несколько вопросов пользователя остаются без прямого ответа."),
    "8eb5ad7e8aea495eae1b3b85bd8e": ("opinion_and_choice", "reflect_and_decide", "neutral", ["unhelpful_answer", "absolute_claim"], [4], "Вопрос о негативных последствиях заменён абсолютным утверждением о пользе развития."),
    "a131e96558ebd93d67a52c868552": ("uncertainty_and_clarification", "handle_uncertainty", "thoughtful", ["fact_check", "analogy_check"], [4], "Сопоставление технологической и физических сингулярностей требует проверки корректности."),
    "aa3621cfe80b430f52c01c216610": ("opinion_and_choice", "reflect_and_decide", "thoughtful", [], [], "Продолжение обсуждает пользу и риски ИИ без категоричного выбора одной стороны."),
    "af6bd031ea01c68b30885a21e60f": ("opinion_and_choice", "reflect_and_decide", "thoughtful", ["fact_check", "tone_check"], [2, 6], "Есть широкие утверждения о музыкальных платформах и пренебрежительная оценка вкусов слушателей."),
    "c9120e636c38e220d00dc9330fce": ("correction", "acknowledge_and_correct", "warm", [], [], "После просьбы о конфликте дана изменённая версия творческого диалога."),
    "c9c997e38918096b1e7412fefb5d": ("uncertainty_and_clarification", "clarify_missing_context", "firm", ["unnecessary_clarification", "tone_check", "repeated_answer"], [2, 4, 6], "Уточнение сопровождается упрёками; следующие ответы повторяют требование правильно задать вопрос."),
    "ce843e2db3153182189854be1677": ("tool_routing", "route_verified_fact", "neutral", ["legacy_capability_claim", "unhelpful_answer"], [4], "Утверждения об отсутствии интернет-инструментов не соответствуют целевой архитектуре ватэсAi."),
    "d4f471be236e4fbc2834077c27e5": ("explanation_and_instruction", "follow_up_with_context", "neutral", ["fact_check"], [4, 6], "Объяснения оптики и согласие с утверждением пользователя требуют предметной проверки."),
    "f46cef90cd95e1d6ba615e388d4a": ("uncertainty_and_clarification", "clarify_missing_context", "concise", [], [], "Диагностические вопросы учитывают уже проверенный кабель и характер звука компьютера."),
    "feeb80c682bb6141bc07fd03f02e": ("uncertainty_and_clarification", "clarify_missing_context", "calm", ["health_check", "wording_check"], [4, 6], "Рекомендации о сне требуют профильной проверки; есть опечатки и неясное название позы."),
}


def build_proposals() -> dict:
    """Привязывает предложения к завершённому аудиту и ровно его train-ID."""
    saved_path = audit.DEFAULT_OUTPUT / "oasst2_quality_audit.json"
    saved_hash = review.file_sha256(saved_path)
    saved = review.read_json(saved_path)
    current = audit.build_audit()
    if current != saved:
        raise review.ReviewError("completed_audit_drift")
    _, records = review.context(review.ReviewPaths())
    identifiers = current["quality_observed_train_ids"]
    if set(identifiers) != {"oasst2.ru." + suffix for suffix in SPECS}:
        raise review.ReviewError("annotation_scope_drift")
    policy = review.read_json(audit.POLICY)
    triage = {item["id"]: item for item in current["material_triage"]["all_items"]}
    rows = []
    for identifier in identifiers:
        topic, goal, tone, concerns, indices, note = SPECS[identifier.removeprefix("oasst2.ru.")]
        if topic not in policy["required_topics"] or goal not in policy["required_dialogue_goals"] or tone not in policy["required_emotional_tones"]:
            raise review.ReviewError("annotation_label_outside_policy")
        record = records[identifier]
        if record["split"] != "train" or any(index < 1 or index > len(record["turns"]) for index in indices):
            raise review.ReviewError("annotation_evidence_invalid")
        rows.append({"id": identifier, "content_sha256": record["content_sha256"],
                     "split": "train", "proposed_topic": topic, "proposed_dialogue_goal": goal,
                     "proposed_emotional_tone": tone, "concern_codes": concerns,
                     "evidence_message_indices": indices, "agent_note": note,
                     "material_attention_codes": triage[identifier]["material_attention_codes"],
                     "agent_disposition": "needs_check" if concerns else "no_obvious_agent_concern",
                     "annotation_author": "agent", "human_annotation_review": "pending",
                     "material_review": "pending", "facts_verified": False,
                     "training_eligible": False})
    if review.file_sha256(saved_path) != saved_hash or audit.input_hashes(review.ReviewPaths()) != current["input_sha256"]:
        raise review.ReviewError("annotation_inputs_changed")
    return {"format_version": 1, "mode": "oasst2_agent_train_annotation_proposals",
            "completed_audit_sha256": saved_hash, "input_sha256": current["input_sha256"],
            "records": len(rows), "agent_disposition_counts": dict(Counter(row["agent_disposition"] for row in rows)),
            "concern_counts": dict(Counter(code for row in rows for code in row["concern_codes"])),
            "proposed_topic_counts": dict(Counter(row["proposed_topic"] for row in rows)),
            "human_decisions_changed": False, "verified_semantic_labels": 0,
            "training_started": False, "promotion_allowed": False, "items": rows}


def write_proposals(report: dict, destination: Path = OUTPUT) -> None:
    destination = destination.resolve()
    try:
        destination.relative_to(audit.RESULTS_ROOT.resolve())
    except ValueError as error:
        raise review.ReviewError("annotation_output_outside_results") from error
    if destination.exists():
        raise review.ReviewError("annotation_output_exists")
    if _contains_raw_content(report):
        raise review.ReviewError("annotation_raw_content_forbidden")
    destination.mkdir(parents=True)
    review.atomic_json(destination / "train_annotation_proposals.json", report, exclusive=True)
    lines = ["# OASST2: предложения разметки train v0.1", "",
             "Автор — агент. Не human-разметка, не проверка фактов или прав.",
             "Все записи имеют training_eligible=false; исходные решения не меняются.", ""]
    for item in report["items"]:
        lines.extend([f"## {item['id']}", "",
                      f"Предложение: {item['proposed_topic']} / {item['proposed_dialogue_goal']} / {item['proposed_emotional_tone']}.",
                      f"Замечания: {', '.join(item['concern_codes']) or 'явных замечаний агента нет'}.",
                      f"Сообщения для проверки (1-based): {item['evidence_message_indices']}.",
                      item["agent_note"], ""])
    with (destination / "TRAIN_ANNOTATION_PROPOSALS.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", required=True)
    parser.parse_args()
    try:
        report = build_proposals()
        write_proposals(report)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f"OASST2 annotation stopped: {error}") from error
    print(json.dumps({key: report[key] for key in ("records", "agent_disposition_counts", "verified_semantic_labels")}))
