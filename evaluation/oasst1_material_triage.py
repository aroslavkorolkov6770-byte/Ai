"""Read-only triage возможных сторонних фрагментов в 75 train-диалогах.

Читает оба конца каждого диалога только в память. Фиксированные сигналы и
контрольная выборка помогают организовать human review, но не решают вопрос
авторских прав, не очищают данные и не запускают обучение.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from evaluation import oasst1_human_review as quality
from evaluation.oasst1_full_train_label_audit import build_full_train_label_audit


RESULTS_ROOT = quality.RESULTS_ROOT
FULL_AUDIT_PATH = (
    RESULTS_ROOT / "oasst1-full-train-label-audit-v0.2" / "oasst1_full_train_label_audit.json"
)
DEFAULT_REPORT_DIRECTORY = RESULTS_ROOT / "oasst1-material-triage-v0.3"
PINNED_REVISION = "fdf72ae0827c1cda404aff25b6603abec9e3399b"
DATASET_CARD_URL = f"https://huggingface.co/datasets/OpenAssistant/oasst1/blob/{PINNED_REVISION}/README.md"
LICENSE_URL = f"https://huggingface.co/datasets/OpenAssistant/oasst1/blob/{PINNED_REVISION}/LICENSE"
URL_PATTERN = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
LONG_QUOTE_PATTERN = re.compile(r"[«\"][^»\"]{120,}[»\"]")
ATTRIBUTION_PATTERN = re.compile(r"\b(?:цитата|источник|согласно|по данным)\b", re.IGNORECASE)
RAW_CONTENT_KEYS = frozenset({"text", "turns", "conversation", "message", "user", "assistant"})
LONG_TURN_CHARACTERS = 700
REPEATED_USER_SPAN_CHARACTERS = 160
CONTROL_SAMPLE_SIZE = 5
PRIORITY_SUFFIXES = frozenset({
    "contains_url", "long_quoted_span", "attribution_word", "repeats_long_user_span"
})


class OASST1MaterialTriageError(ValueError):
    """Останавливает triage при drift, утечке текста или неверной partition."""


def _sha256(path: Path) -> str:
    """Связывает отчёт с байтами исходных файлов."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise OASST1MaterialTriageError("oasst1_material_triage_input_unreadable") from error


def _contains_raw_content(value: Any) -> bool:
    """Запрещает сохранять любые диалоговые поля в metadata-only отчёте."""

    if isinstance(value, Mapping):
        return bool(RAW_CONTENT_KEYS.intersection(value)) or any(
            _contains_raw_content(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_raw_content(item) for item in value)
    return False


def _normalise(value: str) -> str:
    """Нормализует пробелы только в памяти для точного поиска повтора."""

    return " ".join(value.casefold().split())


def material_attention_codes(record: Mapping[str, Any]) -> list[str]:
    """Ищет поверхностные сигналы в user и assistant без вывода совпавшего текста."""

    codes: set[str] = set()
    user_texts = [
        _normalise(turn["text"])
        for turn in record["turns"] if turn["role"] == "user"
    ]
    for turn in record["turns"]:
        role = turn["role"]
        content = turn["text"]
        normalised = _normalise(content)
        if URL_PATTERN.search(content):
            codes.add(f"{role}_contains_url")
        if LONG_QUOTE_PATTERN.search(content):
            codes.add(f"{role}_long_quoted_span")
        if ATTRIBUTION_PATTERN.search(content):
            codes.add(f"{role}_attribution_word")
        if len(content) >= LONG_TURN_CHARACTERS:
            codes.add(f"{role}_long_turn")
        if role == "assistant" and any(
            len(user_text) >= REPEATED_USER_SPAN_CHARACTERS
            and user_text[:REPEATED_USER_SPAN_CHARACTERS] in normalised
            for user_text in user_texts
        ):
            codes.add("assistant_repeats_long_user_span")
    return sorted(codes)


def _rank(identifier: str) -> tuple[str, str]:
    """Даёт стабильную выборку без global random state."""

    return hashlib.sha256(identifier.encode("utf-8")).hexdigest(), identifier


def _is_priority(code: str) -> bool:
    """Отделяет явные структурные сигналы от одной лишь длины текста."""

    return any(code.endswith(suffix) for suffix in PRIORITY_SUFFIXES)


def build_material_triage() -> dict[str, Any]:
    """Формирует полный metadata-index и проверяемую выборку для human review."""

    try:
        saved = json.loads(FULL_AUDIT_PATH.read_text(encoding="utf-8"))
        current = build_full_train_label_audit()
        queue = quality.load_review_queue(quality.DEFAULT_QUEUE_PATH)
        source = quality.load_source_records_for_queue(queue, quality.DEFAULT_SOURCE_PATH)
    except (OSError, ValueError, quality.OASST1HumanReviewError) as error:
        raise OASST1MaterialTriageError("oasst1_material_triage_inputs_invalid") from error
    if not isinstance(saved, dict) or saved != current:
        raise OASST1MaterialTriageError("oasst1_material_triage_full_audit_drift")
    reviewed = saved["review"]["items"]
    if len(reviewed) != 75 or len({item["id"] for item in reviewed}) != 75:
        raise OASST1MaterialTriageError("oasst1_material_triage_partition_invalid")
    items = []
    for reviewed_item in reviewed:
        identifier = reviewed_item["id"]
        source_record = source[identifier]
        if (
            reviewed_item["split"] != "train"
            or source_record["content_sha256"] != reviewed_item["content_sha256"]
        ):
            raise OASST1MaterialTriageError("oasst1_material_triage_source_mismatch")
        codes = material_attention_codes(source_record)
        priority = any(_is_priority(code) for code in codes)
        group = "priority_signal" if priority else "length_only" if codes else "no_signal"
        items.append({
            "id": identifier,
            "content_sha256": reviewed_item["content_sha256"],
            "split": "train",
            "batch_id": reviewed_item["batch_id"],
            "attention_codes": codes,
            "triage_group": group,
            "human_material_verdict": "pending",
        })
    groups = {
        name: sorted((item for item in items if item["triage_group"] == name), key=lambda item: _rank(item["id"]))
        for name in ("priority_signal", "length_only", "no_signal")
    }
    if any(len(groups[name]) < CONTROL_SAMPLE_SIZE for name in ("length_only", "no_signal")):
        raise OASST1MaterialTriageError("oasst1_material_triage_controls_unavailable")
    review_sample = (
        groups["priority_signal"]
        + groups["length_only"][:CONTROL_SAMPLE_SIZE]
        + groups["no_signal"][:CONTROL_SAMPLE_SIZE]
    )
    code_counts = Counter(code for item in items for code in item["attention_codes"])
    report = {
        "format_version": 1,
        "mode": "oasst1_approved_train_material_attention_triage",
        "source_id": saved["source_id"],
        "source_revision": PINNED_REVISION,
        "inputs": {
            "source_sha256": _sha256(quality.DEFAULT_SOURCE_PATH),
            "quality_queue_sha256": _sha256(quality.DEFAULT_QUEUE_PATH),
            "full_label_audit_sha256": _sha256(FULL_AUDIT_PATH),
        },
        "primary_source_references": {
            "dataset_card": DATASET_CARD_URL,
            "license_file": LICENSE_URL,
            "dataset_card_license_tag": "apache-2.0",
            "record_level_rights_confirmed": False,
        },
        "triage": {
            "train_records": len(items),
            "group_counts": {name: len(group) for name, group in groups.items()},
            "attention_code_counts": dict(sorted(code_counts.items())),
            "all_items": sorted(items, key=lambda item: item["id"]),
            "suggested_review_sample_ids": [item["id"] for item in review_sample],
            "suggested_review_sample_count": len(review_sample),
            "control_sample_per_group": CONTROL_SAMPLE_SIZE,
            "heuristic_only": True,
            "not_a_copyright_or_license_verdict": True,
        },
        "boundaries": {
            "raw_dialogue_content_in_report": False,
            "user_turns_scanned": True,
            "assistant_turns_scanned": True,
            "source_or_ledgers_modified": False,
            "active_corpus_modified": False,
            "staging_modified": False,
            "training_started": False,
            "promotion_allowed": False,
        },
        "next_gate": (
            "Review the suggested sample in full, investigate provenance of suspected "
            "third-party material, and check additional no-signal controls. "
            "The source-level Apache-2.0 tag does not prove rights to every embedded excerpt."
        ),
    }
    if (
        report["inputs"]["source_sha256"] != saved["inputs"]["source_sha256"]
        or report["inputs"]["quality_queue_sha256"] != saved["inputs"]["quality_queue_sha256"]
    ):
        raise OASST1MaterialTriageError("oasst1_material_triage_input_drift")
    if _contains_raw_content(report):
        raise OASST1MaterialTriageError("oasst1_material_triage_not_metadata_only")
    return report


def triage_markdown(report: Mapping[str, Any]) -> str:
    """Объясняет статус источника и границы review без исходных реплик."""

    triage = report["triage"]
    groups = triage["group_counts"]
    lines = [
        "# Brain v3 — OASST1 material triage v0.3",
        "",
        "## Источник",
        "",
        "Закреплённая карточка OASST1 указывает Apache-2.0 и содержит",
        f"[README]({DATASET_CARD_URL}) и [LICENSE]({LICENSE_URL}).",
        "Это уровень публикации набора, а не проверка прав на каждый фрагмент,",
        "который участник мог вставить в сообщение. Отдельный human review нужен",
        "до использования конкретных примеров в новом training snapshot.",
        "",
        "## Сигналы по обеим ролям",
        "",
        f"- Проверено `{triage['train_records']}` ранее одобренных train-ID; ",
        f"  более конкретный сигнал: `{groups['priority_signal']}`, только длина: `{groups['length_only']}`, без сигнала: `{groups['no_signal']}`.",
        f"- Предложена выборка для чтения: `{triage['suggested_review_sample_count']}` ID — все с конкретным сигналом плюс",
        f"  по `{triage['control_sample_per_group']}` из групп «только длина» и «без сигнала».",
    ]
    for code, count in triage["attention_code_counts"].items():
        lines.append(f"- `{code}`: `{count}`.")
    lines.extend([
        "",
        "URL, цитаты и повторы найдены лишь эвристически; отсутствие кода не",
        "означает чистоту. Здесь нет исходных реплик или цитируемых URL.",
        "Все human material verdict остаются `pending`.",
        "",
        "## Gate",
        "",
        "Показать выбранные диалоги целиком в локальном review, установить",
        "происхождение подозрительных фрагментов и решить include/hold/exclude",
        "без удаления source. До этого training snapshot и promotion не разрешены.",
        "",
    ])
    return "\n".join(lines)


def write_triage(
    report: Mapping[str, Any], directory: Path = DEFAULT_REPORT_DIRECTORY,
    *, results_root: Path = RESULTS_ROOT,
) -> None:
    """Создаёт новую immutable metadata-only папку внутри results."""

    destination = directory.expanduser().resolve()
    try:
        destination.relative_to(results_root.expanduser().resolve())
    except ValueError as error:
        raise OASST1MaterialTriageError("oasst1_material_triage_output_outside_results") from error
    if destination.exists():
        raise OASST1MaterialTriageError("oasst1_material_triage_report_exists")
    if _contains_raw_content(report):
        raise OASST1MaterialTriageError("oasst1_material_triage_not_metadata_only")
    try:
        destination.mkdir(parents=True)
        (destination / "oasst1_material_triage.json").write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (destination / "OASST1_MATERIAL_TRIAGE.md").write_text(
            triage_markdown(report), encoding="utf-8"
        )
    except OSError as error:
        raise OASST1MaterialTriageError("oasst1_material_triage_write_failed") from error


def main() -> None:
    """Строит только read-only triage по явной команде --audit."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", required=True)
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    args = parser.parse_args()
    try:
        report = build_material_triage()
        write_triage(report, args.report_directory)
    except OASST1MaterialTriageError as error:
        raise SystemExit(f"OASST1 material triage остановлен: {error}") from error
    print(json.dumps({"train_records": report["triage"]["train_records"],
                      "group_counts": report["triage"]["group_counts"],
                      "review_sample": report["triage"]["suggested_review_sample_count"],
                      "training_started": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
