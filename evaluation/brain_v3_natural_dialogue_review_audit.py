"""Проверяет завершённый human review статичной dialogue-партии v1.5.

Этот модуль не создаёт training JSONL, не меняет active corpus и не запускает
обучение. Он читает review-only source только для проверки и сохраняет в
отчёте лишь hashes и semantic metadata, без реплик диалогов.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from evaluation import brain_v3_scoped_review as scoped_review


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = PROJECT_ROOT / "data" / "brain_v3" / "reviews"
RESULTS_ROOT = PROJECT_ROOT / "evaluation" / "results"

SCOPE_PATH = REVIEW_ROOT / "brain-v3-natural-dialogue-candidate-v1.5.scope.json"
REVIEW_CANDIDATE_PATH = (
    REVIEW_ROOT / "brain-v3-natural-dialogue-candidate-v1.5.review.jsonl"
)
QUEUE_PATH = (
    RESULTS_ROOT
    / "brain-v3-natural-dialogue-candidate-v1.5-review-queue-v0.1"
    / "brain_v3_scoped_review_queue.json"
)
DECISIONS_PATH = (
    REVIEW_ROOT / "brain-v3-natural-dialogue-candidate-v1.5.decisions.jsonl"
)

FORMAT_VERSION = 1
AUDIT_MODE = "brain_v3_natural_dialogue_completed_review_audit"
DEFAULT_AUDIT_REPORT_DIRECTORY = (
    RESULTS_ROOT / "brain-v3-natural-dialogue-candidate-v1.5-review-audit-v0.1"
)
EXPECTED_SCOPE_ID = "brain-v3-natural-dialogue-candidate-v1.5"
EXPECTED_CANDIDATE_IDS = tuple(
    f"brain-v3-natural-dialogue-candidate-v1.5-{number:03d}"
    for number in range(1, 7)
)
RAW_DIALOGUE_KEYS = frozenset({"text", "turns", "conversation", "message", "user", "assistant"})
URL_PATTERN = re.compile(r"(?:https?://|www\.)", flags=re.IGNORECASE)


class NaturalDialogueReviewAuditError(ValueError):
    """Останавливает audit до записи неполного или небезопасного отчёта."""


def _safe_path_inside(path: Path, root: Path, error_code: str) -> Path:
    """Разрешает путь только внутри указанной проектной папки."""

    resolved_path = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise NaturalDialogueReviewAuditError(error_code) from error
    return resolved_path


def _safe_new_report_directory(path: Path, results_root: Path) -> Path:
    """Не разрешает перезапись уже созданного versioned audit-отчёта."""

    destination = _safe_path_inside(
        path,
        results_root,
        "natural_dialogue_review_audit_output_outside_results_root",
    )
    if destination.exists():
        raise NaturalDialogueReviewAuditError(
            "natural_dialogue_review_audit_report_already_exists"
        )
    return destination


def _file_sha256(path: Path, error_code: str) -> str:
    """Возвращает SHA-256 входного файла без раскрытия его содержимого."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise NaturalDialogueReviewAuditError(error_code) from error


def _contains_raw_dialogue_content(value: Any) -> bool:
    """Не допускает реплики или структурные поля переписки в audit-отчёт."""

    if isinstance(value, Mapping):
        if RAW_DIALOGUE_KEYS.intersection(value):
            return True
        return any(_contains_raw_dialogue_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_raw_dialogue_content(item) for item in value)
    return False


def _candidate_is_static(records: list[dict[str, Any]]) -> bool:
    """Проверяет, что review-партия не содержит URL или runtime-marker строк."""

    for record in records:
        scenario = record["scenario"]
        properties = scenario["expected_response_properties"]
        if {"tool_result_returned", "no_action_promise"}.intersection(properties):
            return False
        for turn in record["turns"]:
            text = turn["text"]
            if "[[" in text or "]]" in text or URL_PATTERN.search(text):
                return False
    return True


def _load_completed_review_state() -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    str,
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Загружает source, queue и verdicts через общий проверенный workflow."""

    try:
        scope = scoped_review.load_scope(SCOPE_PATH, review_root=REVIEW_ROOT)
        records, candidate_sha256 = scoped_review.load_candidate(
            REVIEW_CANDIDATE_PATH,
            review_root=REVIEW_ROOT,
        )
        queue = scoped_review.load_queue(
            QUEUE_PATH,
            scope_path=SCOPE_PATH,
            candidate_path=REVIEW_CANDIDATE_PATH,
            results_root=RESULTS_ROOT,
            review_root=REVIEW_ROOT,
        )
        decisions = scoped_review.load_decisions(
            DECISIONS_PATH,
            queue,
            review_root=REVIEW_ROOT,
        )
        summary = scoped_review.build_summary(queue, decisions, scope)
    except scoped_review.ScopedReviewError as error:
        raise NaturalDialogueReviewAuditError(
            "natural_dialogue_review_audit_review_source_invalid"
        ) from error

    if scope.get("scope_id") != EXPECTED_SCOPE_ID:
        raise NaturalDialogueReviewAuditError("natural_dialogue_review_audit_scope_mismatch")
    if scope.get("status") != "human_review_coverage_complete":
        raise NaturalDialogueReviewAuditError(
            "natural_dialogue_review_audit_coverage_incomplete"
        )
    return scope, records, candidate_sha256, queue, decisions, summary


def build_completed_review_audit() -> dict[str, Any]:
    """Строит metadata-only доказательство завершённого review без promotion."""

    scope, records, candidate_sha256, queue, decisions, summary = _load_completed_review_state()
    queue_ids = tuple(item["id"] for item in queue["items"])
    decision_ids = tuple(item["candidate_id"] for item in decisions)
    approved_reason_codes = sorted(scoped_review.APPROVAL_REASON_CODES)

    if queue_ids != EXPECTED_CANDIDATE_IDS or decision_ids != EXPECTED_CANDIDATE_IDS:
        raise NaturalDialogueReviewAuditError(
            "natural_dialogue_review_audit_candidate_id_coverage_invalid"
        )
    if any(
        decision["decision"] != "approved"
        or decision["reason_codes"] != approved_reason_codes
        for decision in decisions
    ):
        raise NaturalDialogueReviewAuditError(
            "natural_dialogue_review_audit_verdict_criteria_invalid"
        )
    if not all(item["requires_full_human_review"] for item in queue["items"]):
        raise NaturalDialogueReviewAuditError(
            "natural_dialogue_review_audit_full_review_scope_invalid"
        )
    if not _candidate_is_static(records):
        raise NaturalDialogueReviewAuditError(
            "natural_dialogue_review_audit_static_boundary_invalid"
        )

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
        raise NaturalDialogueReviewAuditError(
            "natural_dialogue_review_audit_summary_invalid"
        )
    if (
        queue.get("metadata_only") is not True
        or queue.get("candidate_is_active") is not False
        or queue.get("training_eligible") is not False
        or queue.get("active_manifest_updated") is not False
        or queue.get("training_started") is not False
    ):
        raise NaturalDialogueReviewAuditError(
            "natural_dialogue_review_audit_queue_boundary_invalid"
        )

    report = {
        "format_version": FORMAT_VERSION,
        "mode": AUDIT_MODE,
        "scope_id": scope["scope_id"],
        "inputs": {
            "scope_sha256": _file_sha256(
                SCOPE_PATH,
                "natural_dialogue_review_audit_scope_hash_failed",
            ),
            "candidate_sha256": candidate_sha256,
            "queue_sha256": _file_sha256(
                QUEUE_PATH,
                "natural_dialogue_review_audit_queue_hash_failed",
            ),
            "decisions_sha256": _file_sha256(
                DECISIONS_PATH,
                "natural_dialogue_review_audit_decisions_hash_failed",
            ),
        },
        "audit": {
            "records_checked": len(queue["items"]),
            "all_candidate_ids_reviewed": True,
            "all_verdicts_approved": True,
            "full_human_review_coverage_complete": True,
            "static_dialogue_boundary_confirmed": True,
            "queue_metadata_only": True,
            "raw_dialogue_content_in_report": False,
            "training_allowed": False,
            "promotion_allowed": False,
            "training_block_reasons": [
                "review_only_candidate_not_training_snapshot",
                "small_tranche_does_not_close_global_semantic_coverage",
                "separate_owner_decision_required_before_staging_or_training",
            ],
        },
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_eligible": False,
        "training_started": False,
        "github_changed": False,
        "next_gate": "Run a cross-candidate semantic coverage audit before proposing any new staging or training decision.",
    }
    if _contains_raw_dialogue_content(report):
        raise NaturalDialogueReviewAuditError(
            "natural_dialogue_review_audit_report_not_metadata_only"
        )
    return report


def audit_markdown(report: Mapping[str, Any]) -> str:
    """Создаёт читаемый Markdown-отчёт без копирования реплик из candidate."""

    audit = report["audit"]
    return "\n".join(
        [
            "# Brain v3 — natural dialogue v1.5 completed-review audit",
            "",
            "## Результат",
            "",
            f"- Проверено сцен: `{audit['records_checked']}`.",
            f"- Все verdict approved: `{str(audit['all_verdicts_approved']).lower()}`.",
            f"- Полное human review: `{str(audit['full_human_review_coverage_complete']).lower()}`.",
            f"- Статичная граница подтверждена: `{str(audit['static_dialogue_boundary_confirmed']).lower()}`.",
            "",
            "## Граница",
            "",
            "Отчёт содержит только hashes и метаданные. Candidate по-прежнему review-only,",
            "не входит в active corpus, не меняет API и не создаёт права на обучение или promotion.",
            "",
            "## Следующий шаг",
            "",
            "Провести cross-candidate semantic coverage audit. Новый staging или training",
            "возможен только после отдельного решения владельца и прохождения всех gate.",
            "",
        ]
    )


def write_audit_report(
    report: Mapping[str, Any],
    destination: Path = DEFAULT_AUDIT_REPORT_DIRECTORY,
    *,
    results_root: Path = RESULTS_ROOT,
) -> None:
    """Сохраняет новую versioned audit-папку и не перезаписывает историю."""

    output = _safe_new_report_directory(destination, results_root)
    try:
        output.mkdir(parents=True)
        (output / "brain_v3_natural_dialogue_completed_review_audit.json").write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output / "BRAIN_V3_NATURAL_DIALOGUE_COMPLETED_REVIEW_AUDIT.md").write_text(
            audit_markdown(report),
            encoding="utf-8",
        )
    except OSError as error:
        raise NaturalDialogueReviewAuditError(
            "natural_dialogue_review_audit_report_write_failed"
        ) from error


def _parser() -> argparse.ArgumentParser:
    """Предоставляет единственное явное действие: audit завершённого review."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", required=True)
    parser.add_argument(
        "--audit-report-directory",
        type=Path,
        default=DEFAULT_AUDIT_REPORT_DIRECTORY,
    )
    return parser


def _configure_utf8_output() -> None:
    """Сохраняет читаемый русский вывод в Windows PowerShell."""

    import sys

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """Запускает audit без corpus import, API changes или training."""

    _configure_utf8_output()
    args = _parser().parse_args()
    try:
        report = build_completed_review_audit()
        write_audit_report(report, args.audit_report_directory)
        print(
            json.dumps(
                {
                    "records_checked": report["audit"]["records_checked"],
                    "full_human_review_coverage_complete": report["audit"][
                        "full_human_review_coverage_complete"
                    ],
                    "training_started": report["training_started"],
                    "promotion_allowed": report["audit"]["promotion_allowed"],
                },
                ensure_ascii=False,
            )
        )
    except NaturalDialogueReviewAuditError as error:
        raise SystemExit(f"Natural dialogue review audit остановлен: {error}") from error


if __name__ == "__main__":
    main()
