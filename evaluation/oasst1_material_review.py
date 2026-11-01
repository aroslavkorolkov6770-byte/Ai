"""Локальный human review материала OASST1 без изменения исходного корпуса.

Выборка triage показывает полный диалог по одному ID. Решения содержат только
metadata и не являются юридическим заключением или разрешением на обучение.
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from evaluation import oasst1_human_review as quality
from evaluation.oasst1_material_triage import build_material_triage


TRIAGE_PATH = (
    quality.RESULTS_ROOT / "oasst1-material-triage-v0.3" / "oasst1_material_triage.json"
)
DECISIONS_PATH = quality.REVIEW_ROOT / "oasst1-material-review-decisions-v0.1.jsonl"
DECISION_MODE = "oasst1_approved_train_material_human_review_decision"
VERDICTS = frozenset({"no_obvious_excerpt", "needs_source_check", "exclude_candidate"})
HOLD_REASON_CODES = frozenset({
    "quoted_span", "attributed_source", "user_supplied_material", "other_uncertainty"
})
_WRITE_LOCK = threading.Lock()
MAX_LEDGER_LINE_BYTES = 4096


class OASST1MaterialReviewError(ValueError):
    """Останавливает запись при drift, чужом ID или невалидном решении."""


@dataclass(frozen=True)
class MaterialReviewPaths:
    """Явно задаёт контролируемые локальные пути для одного review."""

    triage_path: Path = TRIAGE_PATH
    decisions_path: Path = DECISIONS_PATH
    queue_path: Path = quality.DEFAULT_QUEUE_PATH
    source_path: Path = quality.DEFAULT_SOURCE_PATH
    results_root: Path = quality.RESULTS_ROOT
    reviews_root: Path = quality.REVIEW_ROOT
    raw_root: Path = quality.RAW_ROOT


DEFAULT_PATHS = MaterialReviewPaths()


def _inside(path: Path, root: Path, code: str) -> Path:
    """Не позволяет читать или писать вне назначенного каталога."""

    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root.expanduser().resolve())
    except ValueError as error:
        raise OASST1MaterialReviewError(code) from error
    return resolved


def _load_context(paths: MaterialReviewPaths) -> tuple[dict[str, Any], dict[str, Any]]:
    """Сверяет сохранённый triage с повторным расчётом и исходной queue."""

    triage_path = _inside(
        paths.triage_path, paths.results_root, "oasst1_material_review_triage_outside_results"
    )
    try:
        saved = json.loads(triage_path.read_text(encoding="utf-8"))
        expected = build_material_triage()
        queue = quality.load_review_queue(paths.queue_path, results_root=paths.results_root)
    except (OSError, ValueError, quality.OASST1HumanReviewError) as error:
        raise OASST1MaterialReviewError("oasst1_material_review_triage_invalid") from error
    if not isinstance(saved, dict) or saved != expected:
        raise OASST1MaterialReviewError("oasst1_material_review_triage_drift")
    triage = saved["triage"]
    all_by_id = {item["id"]: item for item in triage["all_items"]}
    sample_ids = triage["suggested_review_sample_ids"]
    if (
        len(sample_ids) != triage["suggested_review_sample_count"]
        or len(sample_ids) != len(set(sample_ids))
        or any(identifier not in all_by_id for identifier in sample_ids)
    ):
        raise OASST1MaterialReviewError("oasst1_material_review_sample_invalid")
    return saved, queue


def _validate_decision(
    record: Any, sample_by_id: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Требует точную metadata schema без текста или свободного комментария."""

    required = {
        "format_version", "mode", "candidate_id", "content_sha256", "split",
        "triage_group", "verdict", "reason_code", "reviewer_role", "reviewed_at",
    }
    if not isinstance(record, dict) or set(record) != required:
        raise OASST1MaterialReviewError("oasst1_material_review_decision_invalid")
    identifier = record["candidate_id"]
    verdict = record["verdict"]
    if not isinstance(identifier, str) or not isinstance(verdict, str):
        raise OASST1MaterialReviewError("oasst1_material_review_decision_invalid")
    item = sample_by_id.get(identifier)
    if (
        item is None
        or record["format_version"] != 1
        or record["mode"] != DECISION_MODE
        or record["content_sha256"] != item["content_sha256"]
        or record["split"] != "train"
        or record["triage_group"] != item["triage_group"]
        or verdict not in VERDICTS
        or record["reviewer_role"] != "local_human_reviewer"
    ):
        raise OASST1MaterialReviewError("oasst1_material_review_decision_invalid")
    reason = record["reason_code"]
    if not isinstance(reason, str):
        raise OASST1MaterialReviewError("oasst1_material_review_reason_invalid")
    if verdict == "no_obvious_excerpt":
        if reason != "none_obvious":
            raise OASST1MaterialReviewError("oasst1_material_review_reason_invalid")
    elif reason not in HOLD_REASON_CODES:
        raise OASST1MaterialReviewError("oasst1_material_review_reason_invalid")
    timestamp = record["reviewed_at"]
    if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
        raise OASST1MaterialReviewError("oasst1_material_review_timestamp_invalid")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise OASST1MaterialReviewError("oasst1_material_review_timestamp_invalid") from error
    return record


def _load_decisions(paths: MaterialReviewPaths, triage: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Проверяет каждую строку отдельного metadata-only ledger."""

    path = _inside(
        paths.decisions_path, paths.reviews_root, "oasst1_material_review_ledger_outside_reviews"
    )
    if not path.exists():
        return []
    if not path.is_file():
        raise OASST1MaterialReviewError("oasst1_material_review_ledger_invalid")
    all_by_id = {item["id"]: item for item in triage["all_items"]}
    sample_by_id = {
        identifier: all_by_id[identifier]
        for identifier in triage["suggested_review_sample_ids"]
    }
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                if len(line.encode("utf-8")) > MAX_LEDGER_LINE_BYTES:
                    raise OASST1MaterialReviewError("oasst1_material_review_ledger_line_too_large")
                record = _validate_decision(json.loads(line), sample_by_id)
                if record["candidate_id"] in seen:
                    raise OASST1MaterialReviewError("oasst1_material_review_duplicate_decision")
                seen.add(record["candidate_id"])
                decisions.append(record)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OASST1MaterialReviewError("oasst1_material_review_ledger_invalid") from error
    return decisions


def load_review_snapshot(
    paths: MaterialReviewPaths = DEFAULT_PATHS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Возвращает проверенные metadata triage и ledger для отдельного аудита."""

    report, _ = _load_context(paths)
    return report, _load_decisions(paths, report["triage"])


def review_status(paths: MaterialReviewPaths = DEFAULT_PATHS) -> dict[str, Any]:
    """Даёт только progress counts без исходного текста."""

    triage, decisions = load_review_snapshot(paths)
    total = triage["triage"]["suggested_review_sample_count"]
    counts = {verdict: sum(item["verdict"] == verdict for item in decisions) for verdict in sorted(VERDICTS)}
    return {
        "sample_records": total,
        "reviewed_records": len(decisions),
        "pending_records": total - len(decisions),
        "verdict_counts": counts,
        "training_candidate_allowed": False,
        "promotion_allowed": False,
    }


def next_case(
    paths: MaterialReviewPaths = DEFAULT_PATHS, *, after_id: str | None = None
) -> dict[str, Any] | None:
    """Показывает один полный диалог локально; на диск его не копирует."""

    triage, queue = _load_context(paths)
    triage_data = triage["triage"]
    order = triage_data["suggested_review_sample_ids"]
    if after_id is not None and after_id not in order:
        raise OASST1MaterialReviewError("oasst1_material_review_unknown_candidate")
    reviewed = {item["candidate_id"] for item in _load_decisions(paths, triage_data)}
    if len(reviewed) == len(order):
        return None
    if after_id is None:
        identifier = next(item for item in order if item not in reviewed)
    else:
        pivot = order.index(after_id)
        identifier = next(item for item in order[pivot + 1:] + order[:pivot + 1] if item not in reviewed)
    metadata = next(item for item in triage_data["all_items"] if item["id"] == identifier)
    source = quality.load_source_records_for_queue(queue, paths.source_path, raw_root=paths.raw_root)
    record = source[identifier]
    if record["split"] != "train" or record["content_sha256"] != metadata["content_sha256"]:
        raise OASST1MaterialReviewError("oasst1_material_review_source_mismatch")
    return {
        "id": identifier,
        "triage_group": metadata["triage_group"],
        "attention_codes": metadata["attention_codes"],
        "turns": [{"role": turn["role"], "text": turn["text"]} for turn in record["turns"]],
    }


def record_decision(
    candidate_id: str,
    verdict: str,
    *,
    reason_code: str,
    read_confirmed: bool = False,
    paths: MaterialReviewPaths = DEFAULT_PATHS,
) -> dict[str, Any]:
    """Добавляет явное решение человека без изменения source или training."""

    if read_confirmed is not True:
        raise OASST1MaterialReviewError("oasst1_material_review_full_read_required")
    with _WRITE_LOCK:
        triage, _ = _load_context(paths)
        triage_data = triage["triage"]
        all_by_id = {item["id"]: item for item in triage_data["all_items"]}
        sample_by_id = {
            identifier: all_by_id[identifier]
            for identifier in triage_data["suggested_review_sample_ids"]
        }
        if candidate_id not in sample_by_id:
            raise OASST1MaterialReviewError("oasst1_material_review_unknown_candidate")
        decisions = _load_decisions(paths, triage_data)
        if candidate_id in {item["candidate_id"] for item in decisions}:
            raise OASST1MaterialReviewError("oasst1_material_review_duplicate_decision")
        metadata = sample_by_id[candidate_id]
        record = _validate_decision({
            "format_version": 1,
            "mode": DECISION_MODE,
            "candidate_id": candidate_id,
            "content_sha256": metadata["content_sha256"],
            "split": "train",
            "triage_group": metadata["triage_group"],
            "verdict": verdict,
            "reason_code": reason_code,
            "reviewer_role": "local_human_reviewer",
            "reviewed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }, sample_by_id)
        ledger = _inside(
            paths.decisions_path, paths.reviews_root, "oasst1_material_review_ledger_outside_reviews"
        )
        if ledger.exists() and not ledger.is_file():
            raise OASST1MaterialReviewError("oasst1_material_review_ledger_invalid")
        temporary = ledger.parent / f".{ledger.name}.{secrets.token_hex(8)}.tmp"
        try:
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as output:
                for previous in decisions + [record]:
                    output.write(json.dumps(previous, ensure_ascii=False, separators=(",", ":")) + "\n")
            temporary.replace(ledger)
        except OSError as error:
            raise OASST1MaterialReviewError("oasst1_material_review_write_failed") from error
        finally:
            temporary.unlink(missing_ok=True)
        return {"candidate_id": candidate_id, "verdict": verdict, "reviewed_at": record["reviewed_at"]}
