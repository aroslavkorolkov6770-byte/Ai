"""Готовит отдельный OASST2 candidate и metadata отчёт, не запускает обучение."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from ai_core import brain_v3_corpus as corpus
from ai_core import openassistant_oasst2 as source
from ai_core.openassistant_family_guard import load_existing_oasst1_families
from evaluation import oasst1_semantic_review as semantics
from evaluation.brain_v3_staging import StagedSourceDescriptor, audit_staged_candidate


STAGING_ROOT = source.ROOT / "data/brain_v3/staging"
RESULTS_ROOT = source.ROOT / "evaluation/results"
DEFAULT_CANDIDATE = STAGING_ROOT / "openassistant-oasst2-new-families-v0.1.jsonl"
DEFAULT_REPORT = RESULTS_ROOT / "oasst2-new-families-pilot-v0.1"
CONTRACT_PATH = source.ROOT / "evaluation/brain_v3_acceptance.json"


def normalise(value: str) -> str:
    """Сравнивает пробелы и регистр, не изменяя сохранённый исходный диалог."""

    return " ".join(value.casefold().split())


def digest(value: str) -> str:
    return hashlib.sha256(normalise(value).encode("utf-8")).hexdigest()


def render(turns: list[dict[str, str]]) -> str:
    return "\n".join(f"{turn['role']}:{turn['text']}" for turn in turns)


class PilotOverlapGuard:
    """Хранит только hashes и SimHash, а не реплики корпуса или holdout."""

    def __init__(self) -> None:
        self.exact: set[str] = set()
        self.user_prompts: set[str] = set()
        self.pairs: set[str] = set()
        self.near = corpus.NearDuplicateIndex()
        self.contract_prompts: set[str] = set()
        self.contract_near = corpus.NearDuplicateIndex()

    def add_dialogue(self, turns: list[dict[str, str]],
                     signature: corpus.DuplicateSignature | None = None) -> None:
        """Учитывает полную запись, длинные user-реплики и пары последовательных ходов."""

        rendered = render(turns)
        self.exact.add(digest(rendered))
        self.near.add("known_dialogue", signature or corpus.duplicate_signature(rendered))
        for turn in turns:
            if turn["role"] == "user" and len(normalise(turn["text"])) >= 40:
                self.user_prompts.add(digest(turn["text"]))
        for offset in range(0, len(turns) - 1, 2):
            pair = render(turns[offset:offset + 2])
            if len(normalise(pair)) >= 80:
                self.pairs.add(digest(pair))

    def add_contract_prompt(self, text: str) -> None:
        """Короткие общие приветствия не используются как ключ исключения."""

        normalised = normalise(text)
        if len(normalised) >= 40:
            self.contract_prompts.add(digest(text))
            signature = corpus.duplicate_signature(text)
            if signature.token_count >= 8:
                self.contract_near.add("closed_contract", signature)

    def rejection(self, turns: list[dict[str, str]]) -> str | None:
        """Возвращает только код причины; holdout и совпавший текст не раскрывает."""

        for turn in turns:
            if turn["role"] != "user":
                continue
            normalised = normalise(turn["text"])
            if digest(turn["text"]) in self.contract_prompts:
                return "closed_contract_exact_prompt"
            if len(normalised) >= 40:
                signature = corpus.duplicate_signature(turn["text"])
                if signature.token_count >= 8 and self.contract_near.find(signature) is not None:
                    return "closed_contract_near_prompt"
            if digest(turn["text"]) in self.user_prompts:
                return "active_or_candidate_user_prompt_overlap"
        for offset in range(0, len(turns) - 1, 2):
            if digest(render(turns[offset:offset + 2])) in self.pairs:
                return "active_or_candidate_pair_overlap"
        rendered = render(turns)
        if digest(rendered) in self.exact:
            return "active_or_candidate_exact_duplicate"
        if self.near.find(corpus.duplicate_signature(rendered)) is not None:
            return "active_or_candidate_near_duplicate"
        quality = semantics._assistant_quality_rejection_codes(turns)
        quality.extend(semantics._user_rejection_of_previous_answer_codes(turns))
        return f"quality:{quality[0]}" if quality else None


def build_overlap_guard() -> tuple[PilotOverlapGuard, dict[str, str], dict[str, int]]:
    """Сверяет все source hashes, но сравнивает content только dialogue-слоя.

    Языковые документы и остальные evaluation-наборы остаются для полного
    preflight перед promotion. Эта граница явно записывается в отчёт.
    """

    manifest = corpus.load_corpus_manifest()
    inputs = {"manifest_sha256": corpus.file_sha256(corpus.DEFAULT_MANIFEST),
              "acceptance_contract_sha256": corpus.file_sha256(CONTRACT_PATH)}
    guard = PilotOverlapGuard()
    counts: Counter[str] = Counter()
    for entry in manifest.sources:
        if corpus.file_sha256(entry.raw_path) != entry.raw_sha256:
            raise source.OASST2PilotError("oasst2_active_source_drift")
        inputs[f"active_source:{entry.source_id}"] = entry.raw_sha256
        for _, payload in corpus._read_jsonl(entry.raw_path):
            if payload.get("record_type") != "dialogue":
                counts["language_documents_not_content_compared"] += 1
                continue
            verified = corpus._parse_record(payload, entry)
            guard.add_dialogue(payload["turns"], corpus.DuplicateSignature(
                simhash=verified.simhash, token_count=verified.token_count,
            ))
            counts["active_dialogue_records_checked"] += 1
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if contract.get("evaluation_only") is not True or contract.get("training_eligible") is not False:
        raise source.OASST2PilotError("oasst2_closed_contract_invalid")
    for scenario in contract["scenarios"]:
        guard.add_contract_prompt(scenario["prompt"])
        history = scenario["history"]
        for turn in history:
            if turn["role"] == "user":
                guard.add_contract_prompt(turn["content"])
        counts["closed_contract_scenarios_checked"] += 1
    return guard, inputs, dict(counts)


def validate_unchanged_inputs(inputs: dict[str, str]) -> None:
    """Перед сохранением повторно проверяет источник индекса и закрытый contract."""

    if corpus.file_sha256(corpus.DEFAULT_MANIFEST) != inputs["manifest_sha256"]:
        raise source.OASST2PilotError("oasst2_manifest_drift")
    if corpus.file_sha256(CONTRACT_PATH) != inputs["acceptance_contract_sha256"]:
        raise source.OASST2PilotError("oasst2_contract_drift")
    for entry in corpus.load_corpus_manifest().sources:
        if corpus.file_sha256(entry.raw_path) != inputs[f"active_source:{entry.source_id}"]:
            raise source.OASST2PilotError("oasst2_active_source_drift")


def prepare_pilot(*, download: bool = False, candidate: Path = DEFAULT_CANDIDATE,
                  report_directory: Path = DEFAULT_REPORT) -> dict[str, Any]:
    """Создаёт новый review-only candidate и отчёт без перезаписи прошлых файлов."""

    candidate = source.inside(candidate, STAGING_ROOT)
    report_directory = source.inside(report_directory, RESULTS_ROOT)
    if candidate.exists() or report_directory.exists():
        raise source.OASST2PilotError("oasst2_pilot_output_exists")
    descriptor_hash = source.validate_descriptor()
    download_state = source.download_intake() if download else "existing_local_snapshot"
    families = load_existing_oasst1_families()
    guard, inputs, comparison_counts = build_overlap_guard()
    records, intake_stats = source.collect_new_records(source.INTAKE_PATH, families, guard.rejection)
    selected = []
    final_rejections: Counter[str] = Counter()
    for record in sorted(records, key=lambda value: value["id"]):
        rejection = guard.rejection(record["turns"])
        if rejection:
            final_rejections[rejection] += 1
            continue
        selected.append(record)
        guard.add_dialogue(record["turns"])
    validate_unchanged_inputs(inputs)
    if descriptor_hash != source.validate_descriptor():
        raise source.OASST2PilotError("oasst2_descriptor_drift")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=candidate.parent, mode="w", encoding="utf-8",
                                         suffix=".jsonl.tmp", delete=False) as stream:
            temporary = Path(stream.name)
            for record in selected:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.link(temporary, candidate)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    descriptor = StagedSourceDescriptor(
        source_id=source.SOURCE_ID, license_name="Apache-2.0", license_url=source.CARD_URL,
        origin_url=source.ORIGIN_URL, attribution="OpenAssistant OASST2 pinned publisher release",
    )
    isolated = audit_staged_candidate(candidate, descriptor)
    if isolated["audit"]["records_rejected"]:
        raise source.OASST2PilotError("oasst2_isolated_audit_rejected_records")
    family_by_id = {item["id"]: item["family_sha256"] for item in intake_stats.pop("family_items")}
    hints: Counter[str] = Counter()
    items = []
    for record in selected:
        topic_hints = sorted(semantics._candidate_reason_codes(record["turns"]))
        hints.update(topic_hints)
        items.append({
            "id": record["id"], "content_sha256": record["content_sha256"],
            "family_sha256": family_by_id[record["id"]], "split": record["split"],
            "turn_count": len(record["turns"]), "candidate_topic_hints": topic_hints,
            "quality_review": "pending", "material_review": "pending",
        })
    report = {
        "format_version": 1, "mode": "oasst2_new_family_isolated_pilot",
        "source_id": source.SOURCE_ID, "revision": source.REVISION,
        "download_state": download_state, "intake_sha256": source.EXPECTED_SHA256,
        "descriptor_sha256": descriptor_hash, "candidate_sha256": corpus.file_sha256(candidate),
        "inputs": inputs, "intake": intake_stats, "comparison": comparison_counts,
        "records_written": len(selected), "turns_written": sum(len(r["turns"]) for r in selected),
        "split_counts": dict(sorted(Counter(r["split"] for r in selected).items())),
        "turn_length_counts": dict(sorted(Counter(str(len(r["turns"])) for r in selected).items())),
        "final_candidate_rejections": dict(final_rejections), "heuristic_topic_hint_counts": dict(hints),
        "items": items, "isolated_schema_duplicate_audit": isolated["audit"],
        "human_approved_records": 0, "candidate_topic_hints_are_verified_labels": False,
        "active_language_document_content_comparison_complete": False,
        "all_evaluation_sets_checked": False, "full_active_preflight_required": True,
        "active_manifest_updated": False, "training_started": False,
        "promotion_allowed": False, "training_candidate_allowed": False,
    }
    report_directory.mkdir(parents=True)
    (report_directory / "oasst2_pilot.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (report_directory / "OASST2_PILOT.md").write_text(pilot_markdown(report), encoding="utf-8")
    return report


def pilot_markdown(report: dict[str, Any]) -> str:
    """Пишет счётчики и границы вместо исходных реплик."""

    lines = ["# OASST2 — изолированный пилот новых семейств", "",
             f"Записей: {report['records_written']}; ходов: {report['turns_written']}; human-approved: 0.",
             f"Split: `{report['split_counts']}`; длины: `{report['turn_length_counts']}`.",
             "", "## Отбор", ""]
    lines.extend(f"- `{code}`: {count}." for code, count in report["intake"]["counts"].items())
    lines.extend(["", "## Граница", "",
                  "Старые OASST1 деревья исключены целиком. Одна ветвь на новое дерево.",
                  "Сравнен dialogue-слой active corpus и расширенный brain_v3_acceptance contract.",
                  "Языковые документы и остальные evaluation-наборы требуют полного preflight.",
                  "Найденные topic hints — эвристика для очереди просмотра, не истинные метки.",
                  "Тема/цель/тон записей unclassified, локальная human quality-приёмка pending.",
                  "Далее: изучить выход и сформировать стратифицированный quality review.",
                  "Active corpus, обучение и promotion не изменены.", ""])
    return "\n".join(lines)


def main() -> None:
    """Запускает intake только по явной команде CLI."""

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        report = prepare_pilot(download=args.download, candidate=args.candidate,
                               report_directory=args.report_directory)
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise SystemExit(f"OASST2 pilot остановлен: {error}") from error
    print(json.dumps({"records": report["records_written"], "turns": report["turns_written"],
                      "training_started": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
