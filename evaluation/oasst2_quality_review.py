"""Изолированная выборка OASST2: проверка качества, не разрешение обучения."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

from ai_core.brain_v3_corpus import content_sha256, file_sha256

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "data/brain_v3/staging/openassistant-oasst2-new-families-v0.1.jsonl"
REPORT = ROOT / "evaluation/results/oasst2-new-families-pilot-v0.1/oasst2_pilot.json"
QUEUE = ROOT / "evaluation/results/oasst2-quality-review-v0.1/queue.json"
LEDGER = ROOT / "data/brain_v3/reviews/oasst2-quality-decisions-v0.1.json"
CANDIDATE_HASH = "64bcacece8c877d42dcf16b27247caac0f4ec18b4f9a5295951c19a0f202f891"
REPORT_HASH = "eafbc9a489698e5fea31089aaeaab8f78bd7de34fba497e579c21ea5453e01d1"
REASONS = frozenset({"irrelevant", "context_lost", "unnatural", "unhelpful_length",
                     "factual_uncertainty", "privacy", "third_party_material", "high_risk"})
VERDICTS = frozenset({"acceptable", "needs_check", "exclude"})
LOCK = threading.Lock()


class ReviewError(ValueError):
    """Останавливает проверку при изменении входов или некорректном решении."""


@dataclass(frozen=True)
class ReviewPaths:
    candidate: Path = CANDIDATE
    report: Path = REPORT
    queue: Path = QUEUE
    ledger: Path = LEDGER


def read_json(path: Path) -> object:
    """Повреждённый JSON останавливает просмотр с понятной ошибкой."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ReviewError("stored_json_invalid") from error


def load_inputs(paths: ReviewPaths) -> tuple[dict, dict]:
    """Не доверяет тексту файла без проверки закреплённых контрольных сумм."""
    if file_sha256(paths.candidate) != CANDIDATE_HASH or file_sha256(paths.report) != REPORT_HASH:
        raise ReviewError("input_hash_drift")
    report = read_json(paths.report)
    records = {}
    for line in paths.candidate.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["id"] in records or content_sha256(record) != record["content_sha256"]:
            raise ReviewError("candidate_content_invalid")
        records[record["id"]] = record
    items = report["items"]
    if len(items) != len(records) or len({item["id"] for item in items}) != len(items):
        raise ReviewError("report_items_invalid")
    for item in items:
        record = records.get(item["id"])
        if record is None or any((record["split"] != item["split"],
                                 len(record["turns"]) != item["turn_count"],
                                 record["content_sha256"] != item["content_sha256"])):
            raise ReviewError("report_candidate_mismatch")
    if file_sha256(paths.candidate) != CANDIDATE_HASH or file_sha256(paths.report) != REPORT_HASH:
        raise ReviewError("input_changed_during_read")
    return report, records


def build_queue(items: list[dict]) -> dict:
    """Фиксированные квоты; подсказки тем перекрываются и не являются метками."""
    ranked = sorted(items, key=lambda item: hashlib.sha256(
        ("oasst2-quality-v0.1:" + item["id"]).encode()).hexdigest())
    if len(ranked) < 36 or len({item["id"] for item in items}) != len(items):
        raise ReviewError("sample_population_invalid")
    chosen: dict[str, dict] = {}

    def take(pool: list[dict], count: int, reason: str) -> None:
        matches = [item for item in pool if item["id"] in chosen]
        missing = count - len(matches)
        for item in pool:
            if missing <= 0:
                break
            if item["id"] not in chosen:
                chosen[item["id"]] = {**item, "selection_reason": reason}
                missing -= 1
        if missing > 0:
            raise ReviewError("sample_stratum_insufficient:" + reason)

    for split, count in (("train", 24), ("validation", 6), ("test", 6)):
        take([item for item in ranked if item["split"] == split], count, "split:" + split)
    # Замены внутри split сохраняют квоты и добавляют разнообразие длины/подсказок.
    targets = [("length:6", lambda item: item["turn_count"] == 6, 8),
               ("no_hint", lambda item: not item["candidate_topic_hints"], 4)]
    hints = sorted({hint for item in items for hint in item["candidate_topic_hints"]})
    targets.extend(("hint:" + hint, lambda item, hint=hint: hint in item["candidate_topic_hints"], 1)
                   for hint in hints)
    protected: set[str] = set()
    for reason, predicate, quota in targets:
        pool = [item for item in ranked if predicate(item)]
        quota = min(quota, len(pool))
        matching = [item for item in chosen.values() if predicate(item)]
        protected.update(item["id"] for item in matching[:quota])
        for item in pool:
            if len(matching) >= quota:
                break
            if item["id"] in chosen:
                continue
            victims = [old for old in reversed(list(chosen.values()))
                       if old["split"] == item["split"] and old["id"] not in protected]
            if not victims:
                raise ReviewError("sample_quota_conflict:" + reason)
            del chosen[victims[0]["id"]]
            chosen[item["id"]] = {**item, "selection_reason": reason}
            protected.add(item["id"])
            matching.append(item)
    sample = list(chosen.values())
    return {"format_version": 1, "mode": "oasst2_stratified_quality_review",
            "candidate_sha256": CANDIDATE_HASH, "report_sha256": REPORT_HASH,
            "population_records": len(items), "sample_records": len(sample),
            "split_counts": dict(Counter(item["split"] for item in sample)),
            "turn_counts": dict(Counter(str(item["turn_count"]) for item in sample)),
            "no_hint_count": sum(not item["candidate_topic_hints"] for item in sample),
            "hint_counts": dict(Counter(hint for item in sample for hint in item["candidate_topic_hints"])),
            "hints_are_verified_labels": False, "population_quality_estimate": False,
            "training_candidate_allowed": False, "promotion_allowed": False, "items": sample}


def atomic_json(path: Path, value: object, *, exclusive: bool) -> None:
    """Сначала полностью сохраняет временный файл; исходники не переписывает."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare(paths: ReviewPaths = ReviewPaths()) -> dict:
    report, _ = load_inputs(paths)
    queue = build_queue(report["items"])
    if paths.queue.exists():
        if read_json(paths.queue) != queue:
            raise ReviewError("queue_drift")
    else:
        atomic_json(paths.queue, queue, exclusive=True)
    return queue


def context(paths: ReviewPaths) -> tuple[dict, dict]:
    report, records = load_inputs(paths)
    queue = read_json(paths.queue)
    if queue != build_queue(report["items"]):
        raise ReviewError("queue_drift")
    return queue, records


def decisions(paths: ReviewPaths, queue: dict) -> list[dict]:
    """Ledger хранит только наблюдения; никакого исходного текста или заметок."""
    if not paths.ledger.exists():
        return []
    rows = read_json(paths.ledger)
    if not isinstance(rows, list) or len(rows) > queue["sample_records"]:
        raise ReviewError("ledger_invalid")
    by_id = {item["id"]: item for item in queue["items"]}
    seen = set()
    fields = {"candidate_id", "content_sha256", "family_sha256", "split", "verdict",
              "reason_code", "read_confirmed", "reviewed_at", "queue_sha256"}
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields:
            raise ReviewError("decision_schema_invalid")
        identifier = row["candidate_id"]
        if not isinstance(identifier, str) or identifier not in by_id or identifier in seen:
            raise ReviewError("decision_id_invalid")
        seen.add(identifier)
        item = by_id[identifier]
        if any(row[key] != item[key] for key in ("content_sha256", "family_sha256", "split")):
            raise ReviewError("decision_content_drift")
        if row["queue_sha256"] != file_sha256(paths.queue) or row["read_confirmed"] is not True:
            raise ReviewError("decision_confirmation_invalid")
        validate_verdict(row["verdict"], row["reason_code"])
        if not isinstance(row["reviewed_at"], str) or not row["reviewed_at"].endswith("Z"):
            raise ReviewError("decision_timestamp_invalid")
        try:
            datetime.fromisoformat(row["reviewed_at"].replace("Z", "+00:00"))
        except ValueError as error:
            raise ReviewError("decision_timestamp_invalid") from error
    return rows


def validate_verdict(verdict: str, reason: str) -> None:
    if not isinstance(verdict, str) or verdict not in VERDICTS:
        raise ReviewError("verdict_invalid")
    if not isinstance(reason, str) or (reason != "none_obvious" if verdict == "acceptable" else reason not in REASONS):
        raise ReviewError("reason_invalid")


def status(paths: ReviewPaths = ReviewPaths()) -> dict:
    queue, _ = context(paths)
    rows = decisions(paths, queue)
    return {"sample_records": queue["sample_records"], "reviewed_records": len(rows),
            "pending_records": queue["sample_records"] - len(rows),
            "verdict_counts": dict(Counter(row["verdict"] for row in rows)),
            "training_candidate_allowed": False, "promotion_allowed": False}


def next_case(paths: ReviewPaths = ReviewPaths(), after: str | None = None) -> dict | None:
    queue, records = context(paths)
    order = [item["id"] for item in queue["items"]]
    if after is not None:
        if after not in order:
            raise ReviewError("unknown_candidate")
        pivot = order.index(after) + 1
        order = order[pivot:] + order[:pivot]
    reviewed = {row["candidate_id"] for row in decisions(paths, queue)}
    identifier = next((item for item in order if item not in reviewed), None)
    if identifier is None:
        return None
    item = next(item for item in queue["items"] if item["id"] == identifier)
    return {**item, "turns": records[identifier]["turns"]}


def record_decision(identifier: str, verdict: str, reason: str, read_confirmed: bool,
                    paths: ReviewPaths = ReviewPaths()) -> dict:
    """Один процесс записи; повторное решение не заменяет старое молча."""
    if read_confirmed is not True:
        raise ReviewError("full_read_confirmation_required")
    validate_verdict(verdict, reason)
    with LOCK:
        queue, _ = context(paths)
        rows = decisions(paths, queue)
        item = next((item for item in queue["items"] if item["id"] == identifier), None)
        if item is None:
            raise ReviewError("unknown_candidate")
        if any(row["candidate_id"] == identifier for row in rows):
            raise ReviewError("duplicate_decision")
        row = {"candidate_id": identifier, **{key: item[key] for key in
               ("content_sha256", "family_sha256", "split")}, "verdict": verdict,
               "reason_code": reason, "read_confirmed": True,
               "reviewed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
               "queue_sha256": file_sha256(paths.queue)}
        atomic_json(paths.ledger, rows + [row], exclusive=not paths.ledger.exists())
    return {"saved": True, "candidate_id": identifier, "training_candidate_allowed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", required=True)
    parser.parse_args()
    try:
        result = prepare()
        print(json.dumps({key: value for key, value in result.items() if key != "items"}))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f"OASST2 review stopped: {error}") from error
