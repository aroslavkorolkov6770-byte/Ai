"""Создаёт metadata-only очередь ручной проверки project-owned dialogue candidate.

Скрипт не меняет active manifest, raw corpus, API, веса или обучение. Он
создаёт новый versioned отчёт только с идентификаторами, hashes и semantic
метаданными: сами реплики из candidate в отчёт намеренно не попадают.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from ai_core.brain_v3_owned_dialogues import DEFAULT_CANDIDATE_PATH, RESULTS_ROOT


REVIEW_FRACTION = 0.05
DEFAULT_REPORT_DIRECTORY = (
    RESULTS_ROOT / "brain-v3-owned-dialogues-review-queue-v0.7"
)


class OwnedDialogueReviewError(ValueError):
    """Останавливает создание непроверяемой или раскрывающей текст очереди."""


def _stable_rank(identifier: str) -> str:
    """Возвращает воспроизводимый rank без псевдослучайного global state."""
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()


def _read_jsonl_candidate(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Читает только JSONL candidate и его SHA-256; код из архива не исполняется."""
    candidate = path.expanduser().resolve()
    if candidate.suffix.lower() != ".jsonl" or not candidate.is_file():
        raise OwnedDialogueReviewError("owned_dialogue_review_candidate_not_found")
    try:
        raw = candidate.read_bytes()
        records = [
            json.loads(line)
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OwnedDialogueReviewError("owned_dialogue_review_candidate_invalid") from error
    if not records or not all(isinstance(record, dict) for record in records):
        raise OwnedDialogueReviewError("owned_dialogue_review_candidate_invalid")
    return records, hashlib.sha256(raw).hexdigest()


def _metadata_for_review(record: dict[str, Any]) -> dict[str, Any]:
    """Оставляет только безопасные метаданные, исключая текст user/assistant."""
    identifier = record.get("id")
    content_hash = record.get("content_sha256")
    split = record.get("split")
    topic = record.get("topic")
    goal = record.get("dialogue_goal")
    tone = record.get("emotional_tone")
    properties = record.get("expected_response_properties")
    turns = record.get("turns")
    if (
        not isinstance(identifier, str)
        or not isinstance(content_hash, str)
        or not isinstance(split, str)
        or not isinstance(topic, str)
        or not isinstance(goal, str)
        or not isinstance(tone, str)
        or not isinstance(properties, list)
        or not all(isinstance(item, str) for item in properties)
        or not isinstance(turns, list)
        or not turns
    ):
        raise OwnedDialogueReviewError("owned_dialogue_review_record_invalid")
    return {
        "id": identifier,
        "content_sha256": content_hash,
        "split": split,
        "topic": topic,
        "dialogue_goal": goal,
        "emotional_tone": tone,
        "expected_response_properties": sorted(properties),
        "turn_count": len(turns),
        "review_status": "unreviewed",
    }


def _coverage_snapshot(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Считает покрытия queue только по labels, без текстов candidate."""
    splits: Counter[str] = Counter()
    topics: Counter[str] = Counter()
    goals: Counter[str] = Counter()
    tones: Counter[str] = Counter()
    properties: Counter[str] = Counter()
    turn_counts: Counter[str] = Counter()
    for item in items:
        splits[item["split"]] += 1
        topics[item["topic"]] += 1
        goals[item["dialogue_goal"]] += 1
        tones[item["emotional_tone"]] += 1
        turn_counts[str(item["turn_count"])] += 1
        properties.update(item["expected_response_properties"])
    return {
        "splits": dict(sorted(splits.items())),
        "topics": dict(sorted(topics.items())),
        "dialogue_goals": dict(sorted(goals.items())),
        "emotional_tones": dict(sorted(tones.items())),
        "response_properties": dict(sorted(properties.items())),
        "turn_counts": dict(sorted(turn_counts.items())),
    }


def _select_best_coverage_candidate(
    candidates: Iterable[dict[str, Any]],
    *,
    missing_goals: set[str],
    missing_tones: set[str],
    missing_properties: set[str],
) -> dict[str, Any] | None:
    """Находит одну запись с максимальным новым semantic coverage."""
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for item in candidates:
        gain = int(item["dialogue_goal"] in missing_goals)
        gain += int(item["emotional_tone"] in missing_tones)
        gain += sum(
            property_name in missing_properties
            for property_name in item["expected_response_properties"]
        )
        if gain:
            ranked.append((gain, _stable_rank(item["id"]), item))
    if not ranked:
        return None
    return max(ranked, key=lambda value: (value[0], value[1]))[2]


def select_review_queue(
    records: Iterable[dict[str, Any]],
    *,
    fraction: float = REVIEW_FRACTION,
) -> dict[str, Any]:
    """Строит детерминированную 5%-очередь по split/topic/длине и labels."""
    if not 0 < fraction <= 1:
        raise OwnedDialogueReviewError("owned_dialogue_review_fraction_invalid")
    metadata = [_metadata_for_review(record) for record in records]
    identifiers = [item["id"] for item in metadata]
    if len(identifiers) != len(set(identifiers)):
        raise OwnedDialogueReviewError("owned_dialogue_review_duplicate_identifier")

    strata: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for item in metadata:
        strata[(item["split"], item["topic"], item["turn_count"])].append(item)
    if not strata:
        raise OwnedDialogueReviewError("owned_dialogue_review_candidate_invalid")

    target_records = max(math.ceil(len(metadata) * fraction), len(strata))
    selected: dict[str, dict[str, Any]] = {}
    for key in sorted(strata):
        first = min(strata[key], key=lambda item: _stable_rank(item["id"]))
        selected[first["id"]] = first

    all_goals = {item["dialogue_goal"] for item in metadata}
    all_tones = {item["emotional_tone"] for item in metadata}
    all_properties = {
        property_name
        for item in metadata
        for property_name in item["expected_response_properties"]
    }
    while True:
        covered = _coverage_snapshot(selected.values())
        missing_goals = all_goals - set(covered["dialogue_goals"])
        missing_tones = all_tones - set(covered["emotional_tones"])
        missing_properties = all_properties - set(covered["response_properties"])
        if not (missing_goals or missing_tones or missing_properties):
            break
        candidate = _select_best_coverage_candidate(
            (item for item in metadata if item["id"] not in selected),
            missing_goals=missing_goals,
            missing_tones=missing_tones,
            missing_properties=missing_properties,
        )
        if candidate is None:
            raise OwnedDialogueReviewError("owned_dialogue_review_coverage_incomplete")
        selected[candidate["id"]] = candidate

    cursors = {key: 0 for key in strata}
    ordered_strata = sorted(strata)
    for key in ordered_strata:
        strata[key].sort(key=lambda item: _stable_rank(item["id"]))
    while len(selected) < target_records:
        made_progress = False
        for key in ordered_strata:
            candidates = strata[key]
            while cursors[key] < len(candidates):
                candidate = candidates[cursors[key]]
                cursors[key] += 1
                if candidate["id"] in selected:
                    continue
                selected[candidate["id"]] = candidate
                made_progress = True
                break
            if len(selected) >= target_records:
                break
        if not made_progress:
            break
    if len(selected) < target_records:
        raise OwnedDialogueReviewError("owned_dialogue_review_selection_incomplete")

    selected_items = sorted(selected.values(), key=lambda item: item["id"])
    return {
        "format_version": 1,
        "mode": "owned_dialogue_stratified_review_queue",
        "review_fraction_requested": fraction,
        "candidate_records": len(metadata),
        "target_records": target_records,
        "selected_records": len(selected_items),
        "selected_ratio": round(len(selected_items) / len(metadata), 6),
        "review_status": "unreviewed",
        "selection_strategy": [
            "one deterministic item from every non-empty split/topic/turn-count stratum",
            "semantic backfill for every observed dialogue goal, tone and response property",
            "deterministic round-robin fill across strata",
        ],
        "coverage": _coverage_snapshot(selected_items),
        "items": selected_items,
        "candidate_is_active": False,
        "active_manifest_updated": False,
        "training_started": False,
    }


def review_queue_markdown(report: dict[str, Any]) -> str:
    """Рендерит Markdown без текстов candidate и без скрытого prompt content."""
    coverage = report["coverage"]
    lines = [
        "# Brain v3 — project-owned dialogues v0.7: review queue",
        "",
        "## Статус",
        "",
        "- Статус проверки: `unreviewed`.",
        f"- В candidate: {report['candidate_records']} dialogue-записей.",
        f"- В очередь выбрано: {report['selected_records']} из {report['target_records']} ({report['selected_ratio']:.3f}).",
        "- Queue содержит только IDs, SHA-256 и semantic metadata; реплики candidate не записываются в отчёты.",
        "- Active manifest, raw, API, веса и обучение не меняются.",
        "",
        "## Покрытие очереди",
        "",
        f"- Split: {coverage['splits']}.",
        f"- Длины диалогов: {coverage['turn_counts']}.",
        f"- Темы: {coverage['topics']}.",
        f"- Goals: {coverage['dialogue_goals']}.",
        f"- Tones: {coverage['emotional_tones']}.",
        f"- Response properties: {coverage['response_properties']}.",
        "",
        "## Следующее решение",
        "",
        "Каждая выбранная запись требует проверки естественности русского языка, связности follow-up, уместности ответа, отсутствия ложной памяти, лишних URL и небезопасных советов. Положительный технический audit не заменяет это решение.",
        "",
    ]
    return "\n".join(lines)


def create_review_queue(
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    report_directory: Path = DEFAULT_REPORT_DIRECTORY,
    *,
    fraction: float = REVIEW_FRACTION,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    """Создаёт один новый metadata-only review report, не перезаписывая историю."""
    records, candidate_sha256 = _read_jsonl_candidate(candidate_path)
    report = select_review_queue(records, fraction=fraction)
    report["candidate_path"] = str(candidate_path.expanduser().resolve())
    report["candidate_sha256"] = candidate_sha256

    destination = report_directory.expanduser().resolve()
    resolved_results_root = results_root.expanduser().resolve()
    try:
        destination.relative_to(resolved_results_root)
    except ValueError as error:
        raise OwnedDialogueReviewError("owned_dialogue_review_report_outside_results") from error
    if destination.exists():
        raise OwnedDialogueReviewError("owned_dialogue_review_report_exists")
    destination.mkdir(parents=True)
    (destination / "brain_v3_owned_dialogue_review_queue.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (destination / "BRAIN_V3_OWNED_DIALOGUE_REVIEW_QUEUE.md").write_text(
        review_queue_markdown(report),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Запускает создание review queue без promotion и обучения."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE_PATH)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    parser.add_argument("--fraction", type=float, default=REVIEW_FRACTION)
    args = parser.parse_args()
    try:
        report = create_review_queue(
            args.candidate,
            args.report_directory,
            fraction=args.fraction,
        )
    except (OwnedDialogueReviewError, OSError, ValueError) as error:
        raise SystemExit(f"Owned dialogue review queue остановлена: {error}") from error
    print(
        json.dumps(
            {
                "candidate_sha256": report["candidate_sha256"],
                "candidate_records": report["candidate_records"],
                "selected_records": report["selected_records"],
                "selected_ratio": report["selected_ratio"],
                "review_status": report["review_status"],
                "candidate_is_active": report["candidate_is_active"],
                "training_started": report["training_started"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
