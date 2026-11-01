"""Проверяет завершённый review v1.6, не открывая обучение или staging."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evaluation import brain_v3_scoped_review as review


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = PROJECT_ROOT / "data" / "brain_v3" / "reviews"
RESULTS_ROOT = PROJECT_ROOT / "evaluation" / "results"
SCOPE_ID = "brain-v3-core-dialogue-candidate-v1.6"
SCOPE_PATH = REVIEW_ROOT / f"{SCOPE_ID}.scope.json"
CANDIDATE_PATH = REVIEW_ROOT / f"{SCOPE_ID}.review.jsonl"
DECISIONS_PATH = REVIEW_ROOT / f"{SCOPE_ID}.decisions.jsonl"
QUEUE_PATH = (
    RESULTS_ROOT
    / f"{SCOPE_ID}-review-queue-v0.3"
    / "brain_v3_scoped_review_queue.json"
)
DEFAULT_REPORT_DIRECTORY = RESULTS_ROOT / f"{SCOPE_ID}-review-audit-v0.1"
EXPECTED_IDS = tuple(f"{SCOPE_ID}-{number:03d}" for number in range(1, 7))
EXPECTED_TOPICS = {
    "context_and_memory": 2,
    "explanation_and_instruction": 2,
    "safety_boundaries": 2,
}
MEDICAL_ID = EXPECTED_IDS[-1]
RAW_CONTENT_KEYS = frozenset({"text", "turns", "conversation", "message"})
URL_PATTERN = re.compile(r"https?://|www\.", re.IGNORECASE)


class CoreDialogueReviewAuditError(ValueError):
    """Останавливает запись, если статус review или граница отчёта неверны."""


def audit_input_paths() -> tuple[Path, ...]:
    """Возвращает файлы, которые аудит вправе только читать."""

    return SCOPE_PATH, CANDIDATE_PATH, QUEUE_PATH, DECISIONS_PATH


def _sha256(path: Path) -> str:
    """Связывает metadata-отчёт с точной версией каждого входного файла."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise CoreDialogueReviewAuditError("core_dialogue_audit_input_unreadable") from error


def _has_raw_content(value: Any) -> bool:
    """Не допускает реплики диалога в машиночитаемый audit snapshot."""

    if isinstance(value, Mapping):
        return bool(RAW_CONTENT_KEYS.intersection(value)) or any(
            _has_raw_content(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_has_raw_content(item) for item in value)
    return False


def _static_dialogue(records: list[dict[str, Any]]) -> bool:
    """Оставляет текущие факты и результаты инструментов вне training path."""

    for record in records:
        properties = record["scenario"]["expected_response_properties"]
        if {"tool_result_returned", "no_action_promise"}.intersection(properties):
            return False
        for turn in record["turns"]:
            content = turn["text"]
            if "[[" in content or "]]" in content or URL_PATTERN.search(content):
                return False
    return True


def build_audit() -> dict[str, Any]:
    """Проверяет шесть verdict и сохраняет только hashes и агрегаты."""

    try:
        scope = review.load_scope(SCOPE_PATH)
        records, candidate_sha256 = review.load_candidate(CANDIDATE_PATH)
        queue = review.load_queue(
            QUEUE_PATH,
            scope_path=SCOPE_PATH,
            candidate_path=CANDIDATE_PATH,
        )
        decisions = review.load_decisions(DECISIONS_PATH, queue)
        summary = review.build_summary(queue, decisions, scope)
    except review.ScopedReviewError as error:
        raise CoreDialogueReviewAuditError("core_dialogue_audit_review_invalid") from error

    if scope.get("scope_id") != SCOPE_ID or scope.get("status") != "human_review_coverage_complete":
        raise CoreDialogueReviewAuditError("core_dialogue_audit_scope_not_complete")
    if (
        scope.get("candidate_is_active") is not False
        or scope.get("training_eligible") is not False
        or scope.get("medical_domain_review")
        != {
            "record_id": MEDICAL_ID,
            "status": "pending",
            "required_before_training_snapshot": True,
        }
        or "medical_domain_review_or_exclude_scene_006_before_training_snapshot"
        not in scope.get("promotion_requirements", [])
    ):
        raise CoreDialogueReviewAuditError("core_dialogue_audit_domain_gate_invalid")

    queue_ids = tuple(item["id"] for item in queue["items"])
    decision_ids = tuple(item["candidate_id"] for item in decisions)
    if queue_ids != EXPECTED_IDS or decision_ids != EXPECTED_IDS:
        raise CoreDialogueReviewAuditError("core_dialogue_audit_id_coverage_invalid")
    if any(
        item["decision"] != "approved"
        or set(item["reason_codes"]) != review.APPROVAL_REASON_CODES
        for item in decisions
    ):
        raise CoreDialogueReviewAuditError("core_dialogue_audit_verdict_invalid")

    topics = Counter(record["scenario"]["topic"] for record in records)
    if (
        dict(topics) != EXPECTED_TOPICS
        or not all(item["requires_full_human_review"] for item in queue["items"])
        or not _static_dialogue(records)
    ):
        raise CoreDialogueReviewAuditError("core_dialogue_audit_candidate_invalid")
    medical_record = next(record for record in records if record["id"] == MEDICAL_ID)
    if medical_record["scenario"]["topic"] != "safety_boundaries":
        raise CoreDialogueReviewAuditError("core_dialogue_audit_medical_scenario_invalid")

    expected_summary = {
        "queue_records": 6,
        "reviewed_records": 6,
        "approved_records": 6,
        "rejected_records": 0,
        "unreviewed_records": 0,
        "full_review_required_records": 6,
        "full_reviewed_records": 6,
        "required_human_review_coverage_complete": True,
        "training_candidate_allowed": False,
        "promotion_allowed": False,
    }
    if any(summary.get(key) != value for key, value in expected_summary.items()):
        raise CoreDialogueReviewAuditError("core_dialogue_audit_summary_invalid")
    if (
        queue.get("metadata_only") is not True
        or queue.get("candidate_is_active") is not False
        or queue.get("training_eligible") is not False
        or queue.get("active_manifest_updated") is not False
        or queue.get("training_started") is not False
    ):
        raise CoreDialogueReviewAuditError("core_dialogue_audit_queue_boundary_invalid")

    report = {
        "format_version": 1,
        "mode": "brain_v3_core_dialogue_completed_review_audit",
        "scope_id": SCOPE_ID,
        "inputs": {
            "scope_sha256": _sha256(SCOPE_PATH),
            "candidate_sha256": candidate_sha256,
            "queue_sha256": _sha256(QUEUE_PATH),
            "decisions_sha256": _sha256(DECISIONS_PATH),
        },
        "audit": {
            "records_checked": 6,
            "approved_records": 6,
            "full_human_review_coverage_complete": True,
            "static_dialogue_boundary_confirmed": True,
            "topic_counts": dict(sorted(topics.items())),
            "queue_metadata_only": True,
            "medical_domain_review_status": "pending",
            "medical_scene_excluded_from_training_until_domain_review": True,
            "raw_dialogue_content_in_report": False,
            "training_allowed": False,
            "promotion_allowed": False,
            "training_block_reasons": [
                "medical_domain_review_or_exclusion_required_for_scene_006",
                "full_raw_corpus_and_cross_candidate_duplicate_audit_required",
                "global_semantic_coverage_not_closed",
                "separate_owner_decision_required_before_staging_or_training",
            ],
        },
        "active_manifest_updated": False,
        "staging_created": False,
        "training_started": False,
        "github_changed": False,
        "next_gate": "Confirm medical scene with qualified domain review or exclude it; then re-audit corpus and coverage before any separate owner decision about staging or training.",
    }
    if _has_raw_content(report):
        raise CoreDialogueReviewAuditError("core_dialogue_audit_report_not_metadata_only")
    return report


def audit_markdown(report: Mapping[str, Any]) -> str:
    """Формирует короткое объяснение без текста сцен."""

    audit = report["audit"]
    return "\n".join(
        [
            "# Brain v3 — core dialogue v1.6 completed-review audit",
            "",
            "## Результат",
            "",
            "- Human review: `6/6` сцен approved; обязательное покрытие завершено.",
            "- Темы: по две сцены на объяснение, контекст и безопасные границы.",
            "- Статичная граница: `true`; исходные реплики в отчёт не копировались.",
            "",
            "## Непройденный gate",
            "",
            f"- Медицинская сцена `006`: domain review `{audit['medical_domain_review_status']}`.",
            "- До профильной проверки или исключения сцена не входит в training snapshot.",
            "- Нужны полный аудит корпуса, проверка межкандидатных повторов,",
            "  достаточное смысловое покрытие и отдельное решение владельца.",
            "- Staging, active corpus, API, веса, обучение и GitHub не менялись.",
            "",
            "## Следующий шаг",
            "",
            "Проверить медицинскую сцену у профильного специалиста либо исключить её;",
            "затем обновить сводный coverage audit. `6/6` review не равно training ready.",
            "",
        ]
    )


def write_audit_report(
    report: Mapping[str, Any],
    directory: Path = DEFAULT_REPORT_DIRECTORY,
    *,
    results_root: Path = RESULTS_ROOT,
) -> None:
    """Создаёт новую immutable папку внутри evaluation/results."""

    destination = directory.expanduser().resolve()
    root = results_root.expanduser().resolve()
    try:
        destination.relative_to(root)
    except ValueError as error:
        raise CoreDialogueReviewAuditError("core_dialogue_audit_output_outside_results") from error
    if destination.exists():
        raise CoreDialogueReviewAuditError("core_dialogue_audit_report_exists")
    if _has_raw_content(report):
        raise CoreDialogueReviewAuditError("core_dialogue_audit_report_not_metadata_only")
    try:
        destination.mkdir(parents=True)
        (destination / "brain_v3_core_dialogue_completed_review_audit.json").write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (destination / "BRAIN_V3_CORE_DIALOGUE_COMPLETED_REVIEW_AUDIT.md").write_text(
            audit_markdown(report), encoding="utf-8"
        )
    except OSError as error:
        raise CoreDialogueReviewAuditError("core_dialogue_audit_write_failed") from error


def main() -> None:
    """Запускает только локальный metadata-audit после явного ключа --audit."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", required=True)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    args = parser.parse_args()
    try:
        report = build_audit()
        write_audit_report(report, args.report_directory)
    except CoreDialogueReviewAuditError as error:
        raise SystemExit(f"Core dialogue review audit остановлен: {error}") from error
    print(
        json.dumps(
            {
                "approved_records": report["audit"]["approved_records"],
                "medical_domain_review_status": report["audit"]["medical_domain_review_status"],
                "training_allowed": report["audit"]["training_allowed"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
