"""Адресное распределение agent concerns, без изменения human ledger и raw."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from evaluation import oasst2_train_annotation as annotation
from evaluation import oasst2_quality_review as review
from evaluation.oasst1_material_triage import _contains_raw_content

OUTPUT = annotation.audit.RESULTS_ROOT / "oasst2-targeted-review-v0.1"
DO_NOT_USE = {
    "1a4b7e6d574666ee59b8c42b56e4", "1eb73700565145b020d0654e7254",
    "55ddb29f974169105ad1823fba56", "6dcb12948e80672c1dd39c4b9b46",
    "8eb5ad7e8aea495eae1b3b85bd8e", "af6bd031ea01c68b30885a21e60f",
    "c9c997e38918096b1e7412fefb5d", "ce843e2db3153182189854be1677",
}
EDITORIAL = {"28a536bf702c9777821a4785ddfa", "70d4b86c39b7c295710ca4fc8e80"}
# Свидетельства прочитаны через read-only web 2026-10-06. Это ручной snapshot,
# не результат автоматического повторного обращения к сайтам при запуске runner.
SOURCES = {
    "nasa_ice_halo": {
        "title": "NASA APOD: A Halo Around the Moon",
        "url": "https://science.nasa.gov/image-article/apod-2008-december-7-a-halo-around-the-moon/",
        "checked_on": "2026-10-06", "read_scope": "article explanation",
        "finding": "Кристаллы льда преломляют свет и могут создавать гало; гало не тождественно радуге в каплях воды.",
    },
    "epa_sound_repellents": {
        "title": "US EPA: What is an Insect Repellent?",
        "url": "https://www.epa.gov/insect-repellents/what-insect-repellent",
        "checked_on": "2026-10-06", "read_scope": "types and effectiveness data",
        "finding": "EPA не располагает данными безопасности/эффективности звуковых устройств как зарегистрированных репеллентов; это не доказательство отсутствия слуха у комаров.",
    },
    "seagate_diagnostics": {
        "title": "Seagate SeaTools for Windows User Guide",
        "url": "https://www.seagate.com/content/dam/seagate/migrated-assets/www-content/support-content/downloads/seatools/_shared/downloads/pdf/SeaTools-for-windows-en-us.pdf",
        "checked_on": "2026-10-06", "read_scope": "diagnostic tests and logs, pages 4-7",
        "finding": "Руководство описывает SMART/DST-тесты и журналы результатов; это основа более конкретного ответа о диагностике, а не доказательство неисправности диска пользователя.",
        "limitation": "Руководство версии 2015 года; текущие интерфейс и доступность функций не проверены.",
    },
}
CHECKS = {
    "04ab162d32955e0b6a894e74dc14": ("epa_sound_repellents", "partial_support_not_biological_verification",
        "Необоснованную уверенность в механизме заменить осторожной формулировкой; слух, зрение и безопасность излучения требуют отдельных источников."),
    "d4f471be236e4fbc2834077c27e5": ("nasa_ice_halo", "scientific_precision_revision_required",
        "Ясно различать радугу в каплях и ледяное гало; не создавать впечатление, что кристаллы льда не преломляют свет."),
    "6ffc7bf5393f7d36df93eefb32b5": ("seagate_diagnostics", "repair_basis_not_full_fact_verification",
        "Убрать неясные формулировки, дать диагностические шаги с учётом ОС/модели; не советовать автоматическую замену по одному симптому."),
}


def build_review() -> dict:
    """Все 24 записи получают действие; никаких скрытых одобрений остатка."""
    path = annotation.OUTPUT / "train_annotation_proposals.json"
    before = review.file_sha256(path)
    saved = review.read_json(path)
    if saved != annotation.build_proposals():
        raise review.ReviewError("annotation_proposals_drift")
    available = {item["id"].removeprefix("oasst2.ru.") for item in saved["items"]}
    if DO_NOT_USE & EDITORIAL or not (DO_NOT_USE | EDITORIAL | set(CHECKS)).issubset(available):
        raise review.ReviewError("targeted_scope_invalid")
    rows = []
    for item in saved["items"]:
        suffix = item["id"].removeprefix("oasst2.ru.")
        if suffix in DO_NOT_USE:
            action = "do_not_use_as_positive_in_current_form"
        elif suffix in EDITORIAL:
            action = "editorial_revision_review"
        elif item["concern_codes"]:
            action = "subject_or_source_verification"
        else:
            action = "material_and_label_review_pending"
        finding = None
        if suffix in CHECKS:
            source, result, note = CHECKS[suffix]
            finding = {"source_key": source, "result": result, "repair_direction": note,
                       "whole_dialogue_fact_verified": False}
        rows.append({"id": item["id"], "content_sha256": item["content_sha256"],
                     "split": item["split"], "agent_action": action,
                     "concern_codes": item["concern_codes"],
                     "evidence_message_indices": item["evidence_message_indices"],
                     "source_check": finding, "human_quality_verdict": "acceptable",
                     "agent_recommendation_is_human_decision": False,
                     "human_annotation_review": "pending", "material_review": "pending",
                     "training_eligible": False})
    if review.file_sha256(path) != before or annotation.audit.input_hashes(review.ReviewPaths()) != saved["input_sha256"]:
        raise review.ReviewError("targeted_inputs_changed")
    return {"format_version": 1, "mode": "oasst2_targeted_agent_review",
            "review_date": "2026-10-06", "annotation_sha256": before,
            "input_sha256": saved["input_sha256"], "records": len(rows),
            "action_counts": dict(Counter(item["agent_action"] for item in rows)),
            "source_checks": len(CHECKS), "sources": SOURCES,
            "priority_repair_ids": ["oasst2.ru.1a4b7e6d574666ee59b8c42b56e4",
                                    "oasst2.ru.6dcb12948e80672c1dd39c4b9b46"],
            "human_ledger_changed": False, "raw_corpus_changed": False,
            "training_started": False, "promotion_allowed": False,
            "all_facts_verified": False, "all_material_rights_verified": False,
            "verified_semantic_labels": 0, "items": rows}


def write_review(report: dict, destination: Path = OUTPUT) -> None:
    destination = destination.resolve()
    try:
        destination.relative_to(annotation.audit.RESULTS_ROOT.resolve())
    except ValueError as error:
        raise review.ReviewError("targeted_output_outside_results") from error
    if destination.exists():
        raise review.ReviewError("targeted_output_exists")
    if _contains_raw_content(report):
        raise review.ReviewError("targeted_raw_content_forbidden")
    destination.mkdir(parents=True)
    review.atomic_json(destination / "targeted_review.json", report, exclusive=True)
    lines = ["# OASST2: адресный разбор v0.1", "", "Дата: 2026-10-06.",
             "Рекомендации агента не заменяют решения человека. Все training_eligible=false.", ""]
    lines.extend(f"- {action}: {count}." for action, count in report["action_counts"].items())
    for item in report["items"]:
        lines.extend(["", f"## {item['id']}", "", item["agent_action"],
                      f"Причины: {', '.join(item['concern_codes']) or 'явных agent concerns нет'}."])
        if item["source_check"]:
            check = item["source_check"]
            source = report["sources"][check["source_key"]]
            lines.extend([check["repair_direction"], source["finding"],
                          f"Источник: [{source['title']}]({source['url']}).",
                          "Проверен отдельный вопрос, не весь диалог."])
    with (destination / "TARGETED_REVIEW.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", action="store_true", required=True)
    parser.parse_args()
    try:
        result = build_review()
        write_review(result)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f"OASST2 targeted review stopped: {error}") from error
    print(json.dumps({"records": result["records"], "action_counts": result["action_counts"]}))
