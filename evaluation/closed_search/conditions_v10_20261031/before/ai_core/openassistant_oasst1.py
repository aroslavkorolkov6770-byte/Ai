"""Готовит безопасный staging-кандидат из закреплённого OASST1 tree snapshot.

Модуль не использует модели, не исполняет внешние инструкции и не меняет active
corpus. Он читает только один заранее проверенный локальный gzip snapshot,
берёт русские, human-reviewed, не удалённые и несинтетические ветви вида
``prompter -> assistant``. URL, e-mail, телефоны, похожие на секреты строки и
небезопасные prompt-like фрагменты отбрасываются до появления Brain v3 JSONL.

В отчёты намеренно попадают лишь версия, hashes и счётчики. Текст внешних
диалогов, user IDs и внутренние поля OASST1 не копируются в отчёты.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .brain_v3_corpus import (
    BrainV3RecordError,
    MAX_TEXT_LENGTH,
    NearDuplicateIndex,
    content_sha256,
    duplicate_signature,
    file_sha256,
    text_risk_code,
)


ROOT = Path(__file__).resolve().parent.parent
SOURCE_ID = "openassistant-oasst1-apache-v1"
SOURCE_LICENSE = "Apache-2.0"
SOURCE_REVISION = "fdf72ae0827c1cda404aff25b6603abec9e3399b"
SOURCE_FILE_NAME = "2023-04-12_oasst_ready.trees.jsonl.gz"
SOURCE_URL = (
    "https://huggingface.co/datasets/OpenAssistant/oasst1/resolve/"
    f"{SOURCE_REVISION}/{SOURCE_FILE_NAME}?download=true"
)
SOURCE_LICENSE_URL = (
    "https://huggingface.co/datasets/OpenAssistant/oasst1/blob/"
    f"{SOURCE_REVISION}/LICENSE"
)
SOURCE_ORIGIN_URL = f"https://huggingface.co/datasets/OpenAssistant/oasst1/tree/{SOURCE_REVISION}"
SOURCE_ATTRIBUTION = "OpenAssistant Conversations Dataset (OASST1), Apache-2.0 fixed tree snapshot"

# Значения взяты только из опубликованных immutable HTTP headers pinned revision:
# X-Linked-Size и X-Linked-ETag. Import повторно вычисляет SHA-256 локального
# snapshot до распаковки gzip, поэтому подмена или неполная загрузка останавливают
# pipeline до staging output.
EXPECTED_SOURCE_BYTES = 34_145_252
EXPECTED_SOURCE_SHA256 = "2a9a8fd343e9b28e04a895a669d3253f82d93e9c174d440199ae19d5fafbdff7"

DEFAULT_INTAKE_ROOT = ROOT / "data" / "brain_v3" / "intake" / SOURCE_ID
DEFAULT_INTAKE_PATH = DEFAULT_INTAKE_ROOT / SOURCE_FILE_NAME
DEFAULT_STAGING_ROOT = ROOT / "data" / "brain_v3" / "staging"
DEFAULT_CANDIDATE = DEFAULT_STAGING_ROOT / "openassistant-oasst1-apache-v0.1.jsonl"
DEFAULT_REPORT_DIRECTORY = ROOT / "evaluation" / "results" / "openassistant-oasst1-staging-v0.1"

MAX_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 2 * 1024 * 1024
MAX_TREES = 20_000
MAX_TREE_DEPTH = 16
MAX_TREE_NODES = 150_000
MAX_REPLIES_PER_NODE = 32
MAX_CANDIDATE_PATHS = 40_000
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_RECORDS = 20_000
TOXICITY_THRESHOLD = 0.30
TOXICITY_FIELDS = (
    "toxicity",
    "severe_toxicity",
    "obscene",
    "identity_attack",
    "insult",
    "threat",
    "sexual_explicit",
)
ROLE_MAP = {"prompter": "user", "assistant": "assistant"}


class OpenAssistantOASST1Error(ValueError):
    """Останавливает import до создания непроверенного staging-кандидата."""


@dataclass(frozen=True)
class _SafeTurn:
    """Хранит лишь подготовленный turn до сериализации кандидата."""

    role: str
    text: str
    message_id: str


@dataclass(frozen=True)
class OpenAssistantOASST1Summary:
    """Фиксирует provenance и safety counters без текста исходных сообщений."""

    intake_path: str
    intake_sha256: str
    intake_bytes: int
    output_path: str
    output_sha256: str
    output_bytes: int
    trees_seen: int
    nodes_seen: int
    leaf_paths_seen: int
    eligible_leaf_paths: int
    records_written: int
    records_rejected: int
    split_counts: dict[str, int]
    node_filter_counts: dict[str, int]
    record_rejections: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        """Сериализует только безопасные метаданные staging-операции."""
        return {
            "format_version": 1,
            "source_id": SOURCE_ID,
            "source_revision": SOURCE_REVISION,
            "source_url": SOURCE_URL,
            "origin_url": SOURCE_ORIGIN_URL,
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "attribution": SOURCE_ATTRIBUTION,
            "intake_path": self.intake_path,
            "intake_sha256": self.intake_sha256,
            "intake_bytes": self.intake_bytes,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "output_bytes": self.output_bytes,
            "trees_seen": self.trees_seen,
            "nodes_seen": self.nodes_seen,
            "leaf_paths_seen": self.leaf_paths_seen,
            "eligible_leaf_paths": self.eligible_leaf_paths,
            "records_written": self.records_written,
            "records_rejected": self.records_rejected,
            "split_counts": self.split_counts,
            "node_filter_counts": self.node_filter_counts,
            "record_rejections": self.record_rejections,
            "candidate_is_active": False,
            "active_manifest_updated": False,
            "training_started": False,
            "chat_changed": False,
            "github_changed": False,
        }


def _safe_child(path: Path, root: Path, *, code: str) -> Path:
    """Разрешает работу только внутри явно указанной локальной папки."""
    resolved_root = root.expanduser().resolve()
    resolved_path = path.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise OpenAssistantOASST1Error(code) from error
    return resolved_path


def _safe_intake(path: Path, intake_root: Path) -> Path:
    """Проверяет fixed, immutable OASST1 gzip snapshot до его распаковки."""
    source_path = _safe_child(
        path,
        intake_root,
        code="OASST1 import читает snapshot только внутри data/brain_v3/intake/.",
    )
    if source_path.name != SOURCE_FILE_NAME or source_path.suffix.lower() != ".gz":
        raise OpenAssistantOASST1Error("OASST1 import ожидает fixed tree snapshot filename.")
    if source_path.is_symlink() or not source_path.is_file():
        raise OpenAssistantOASST1Error("OASST1 intake snapshot должен быть обычным локальным файлом.")
    source_bytes = source_path.stat().st_size
    if source_bytes != EXPECTED_SOURCE_BYTES or source_bytes > MAX_COMPRESSED_BYTES:
        raise OpenAssistantOASST1Error("OASST1 intake snapshot не совпадает с зафиксированным размером.")
    if file_sha256(source_path) != EXPECTED_SOURCE_SHA256:
        raise OpenAssistantOASST1Error("OASST1 intake snapshot не прошёл SHA-256 verification.")
    return source_path


def _prepare_destination(path: Path, staging_root: Path) -> Path:
    """Не позволяет записать candidate вне staging или перезаписать старый файл."""
    destination = _safe_child(
        path,
        staging_root,
        code="OASST1 candidate разрешён только внутри data/brain_v3/staging/.",
    )
    if destination.suffix.lower() != ".jsonl":
        raise OpenAssistantOASST1Error("OASST1 staging output должен иметь расширение JSONL.")
    if destination.exists():
        raise OpenAssistantOASST1Error("OASST1 staging output уже существует; перезапись запрещена.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def _normalise_text(value: str) -> str:
    """Убирает лишние пробелы, не добавляя и не перефразируя исходный текст."""
    return " ".join(value.split())


def _safe_turn(node: Any) -> tuple[_SafeTurn | None, str | None]:
    """Отделяет пригодный anonymized human-reviewed turn от небезопасной ноды."""
    if not isinstance(node, dict):
        return None, "invalid_node"
    source_role = node.get("role")
    if source_role not in ROLE_MAP:
        return None, "invalid_role"
    if node.get("lang") != "ru":
        return None, "non_russian"
    if node.get("deleted") is not False:
        return None, "deleted"
    if node.get("review_result") is not True:
        return None, "unreviewed"
    if node.get("synthetic") is not False:
        return None, "synthetic"
    raw_text = node.get("text")
    if not isinstance(raw_text, str):
        return None, "invalid_text"
    raw_risk = text_risk_code(raw_text, maximum=MAX_TEXT_LENGTH)
    if raw_risk is not None:
        return None, raw_risk
    text = _normalise_text(raw_text)
    cleaned_risk = text_risk_code(text, maximum=MAX_TEXT_LENGTH)
    if cleaned_risk is not None:
        return None, cleaned_risk
    detoxify = node.get("detoxify")
    if detoxify is not None:
        if not isinstance(detoxify, dict):
            return None, "invalid_detoxify"
        for field in TOXICITY_FIELDS:
            score = detoxify.get(field)
            if score is None:
                continue
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0.0 <= float(score) <= 1.0:
                return None, "invalid_detoxify"
            if float(score) >= TOXICITY_THRESHOLD:
                return None, "toxicity_threshold"
    message_id = node.get("message_id")
    if not isinstance(message_id, str) or not message_id or len(message_id) > 128:
        return None, "invalid_message_id"
    return _SafeTurn(role=ROLE_MAP[source_role], text=text, message_id=message_id), None


def _tree_id(payload: Any) -> str | None:
    """Берёт только bounded opaque tree ID, не сохраняя user ID или labels."""
    if not isinstance(payload, dict):
        return None
    value = payload.get("message_tree_id")
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    return value


def _deterministic_split(tree_id: str) -> str:
    """Держит все ветви одного tree в одном split, чтобы не было leakage prefix."""
    bucket = hashlib.sha256(f"{SOURCE_ID}:{tree_id}".encode("utf-8")).digest()[0] % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "validation"
    return "test"


def _audit_equivalent_content(turns: Iterable[_SafeTurn]) -> str:
    """Строит то же role-prefixed представление, что использует corpus audit."""
    return "\n".join(f"{turn.role}:{turn.text.casefold()}" for turn in turns)


def _record(tree_id: str, leaf_id: str, turns: list[_SafeTurn]) -> dict[str, Any]:
    """Создаёт Brain v3 record без внешних IDs, labels и служебных metadata."""
    record_digest = hashlib.sha256(f"{tree_id}:{leaf_id}".encode("utf-8")).hexdigest()[:28]
    response_properties = ["relevant", "natural_tone"]
    if len(turns) >= 4:
        response_properties.append("context_retained")
    payload: dict[str, Any] = {
        "format_version": 1,
        "id": f"oasst1.ru.{record_digest}",
        "record_type": "dialogue",
        "provenance": {
            "source_id": SOURCE_ID,
            "license": SOURCE_LICENSE,
            "license_url": SOURCE_LICENSE_URL,
            "origin_url": SOURCE_ORIGIN_URL,
            "attribution": SOURCE_ATTRIBUTION,
        },
        "language": "ru",
        "layer": "dialogue",
        "split": _deterministic_split(tree_id),
        "topic": "open_assistant_general",
        "quality_tier": "human_reviewed",
        "expected_response_properties": response_properties,
        "dialogue_goal": "general_assistant_exchange",
        "emotional_tone": "unclassified",
        "turns": [{"role": turn.role, "text": turn.text} for turn in turns],
    }
    payload["content_sha256"] = content_sha256(payload)
    return payload


def _iter_tree_paths(
    payload: Any,
    *,
    node_filter_counts: Counter[str],
    leaf_path_counter: Counter[str],
    node_counter: Counter[str],
) -> Iterable[tuple[str, list[_SafeTurn]]]:
    """Потоково выдаёт чистые root-to-leaf paths без сохранения дерева на диск."""
    tree_id = _tree_id(payload)
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if tree_id is None or not isinstance(prompt, dict):
        node_filter_counts["invalid_tree"] += 1
        return

    def walk(node: Any, path: list[_SafeTurn], depth: int) -> Iterable[list[_SafeTurn]]:
        if depth > MAX_TREE_DEPTH:
            raise OpenAssistantOASST1Error("OASST1 tree превысило безопасную глубину.")
        node_counter["nodes_seen"] += 1
        if node_counter["nodes_seen"] > MAX_TREE_NODES:
            raise OpenAssistantOASST1Error("OASST1 tree snapshot превысил лимит nodes.")
        turn, rejection = _safe_turn(node)
        if turn is None:
            if rejection is not None:
                node_filter_counts[rejection] += 1
            next_path: list[_SafeTurn] = []
        elif not path or path[-1].role != turn.role:
            next_path = [*path, turn]
        else:
            node_filter_counts["role_sequence_break"] += 1
            next_path = [turn]

        replies = node.get("replies") if isinstance(node, dict) else None
        if not isinstance(replies, list) or len(replies) > MAX_REPLIES_PER_NODE:
            node_filter_counts["invalid_replies"] += 1
            return
        if not replies:
            leaf_path_counter["leaf_paths_seen"] += 1
            if len(next_path) >= 2 and len(next_path) % 2 == 0 and next_path[0].role == "user":
                leaf_path_counter["eligible_leaf_paths"] += 1
                yield next_path
            elif next_path:
                node_filter_counts["incomplete_role_path"] += 1
            return
        for reply in replies:
            yield from walk(reply, next_path, depth + 1)

    for turns in walk(prompt, [], 1):
        yield tree_id, turns


def _iter_source_trees(path: Path) -> Iterable[Any]:
    """Читает gzip JSONL с bounded decompression и без распаковки файлов на диск."""
    decompressed_bytes = 0
    try:
        with gzip.open(path, mode="rb") as source:
            for line_number, raw_line in enumerate(source, start=1):
                decompressed_bytes += len(raw_line)
                if decompressed_bytes > MAX_DECOMPRESSED_BYTES:
                    raise OpenAssistantOASST1Error("OASST1 gzip превысил лимит распакованных bytes.")
                if len(raw_line) > MAX_JSONL_LINE_BYTES:
                    raise OpenAssistantOASST1Error("OASST1 JSONL line превысила безопасный лимит.")
                if not raw_line.strip():
                    continue
                try:
                    yield json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise OpenAssistantOASST1Error(
                        f"OASST1 intake содержит invalid UTF-8 or JSON at line {line_number}."
                    ) from error
    except (OSError, EOFError, gzip.BadGzipFile) as error:
        raise OpenAssistantOASST1Error("OASST1 intake snapshot не является корректным gzip JSONL.") from error


def import_openassistant_oasst1(
    intake: Path = DEFAULT_INTAKE_PATH,
    destination: Path = DEFAULT_CANDIDATE,
    *,
    intake_root: Path = DEFAULT_INTAKE_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> OpenAssistantOASST1Summary:
    """Создаёт fixed OASST1 candidate, который остаётся вне active manifest.

    Все проверки source выполняются до открытия gzip. Candidate создаётся через
    temporary file и atomically появляется только при успешном завершении.
    """
    intake_path = _safe_intake(intake, intake_root)
    output_path = _prepare_destination(destination, staging_root)
    intake_sha256 = file_sha256(intake_path)
    exact_fingerprints: set[str] = set()
    near_duplicates = NearDuplicateIndex()
    split_counts: Counter[str] = Counter()
    node_filter_counts: Counter[str] = Counter()
    record_rejections: Counter[str] = Counter()
    leaf_path_counter: Counter[str] = Counter()
    node_counter: Counter[str] = Counter()
    trees_seen = 0
    records_written = 0
    output_bytes = 0
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=".oasst1-import-",
            suffix=".jsonl.tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            for source_tree in _iter_source_trees(intake_path):
                trees_seen += 1
                if trees_seen > MAX_TREES:
                    raise OpenAssistantOASST1Error("OASST1 intake превысил лимит tree records.")
                for tree_id, turns in _iter_tree_paths(
                    source_tree,
                    node_filter_counts=node_filter_counts,
                    leaf_path_counter=leaf_path_counter,
                    node_counter=node_counter,
                ):
                    if leaf_path_counter["eligible_leaf_paths"] > MAX_CANDIDATE_PATHS:
                        raise OpenAssistantOASST1Error("OASST1 intake превысил лимит candidate paths.")
                    rendered = _audit_equivalent_content(turns)
                    fingerprint = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
                    if fingerprint in exact_fingerprints:
                        record_rejections["exact_duplicate"] += 1
                        continue
                    try:
                        signature = duplicate_signature(rendered)
                    except BrainV3RecordError as error:
                        record_rejections[error.code] += 1
                        continue
                    if near_duplicates.find(signature) is not None:
                        record_rejections["near_duplicate_candidate"] += 1
                        continue
                    record = _record(tree_id, turns[-1].message_id, turns)
                    encoded_record = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                    encoded_bytes = len(encoded_record.encode("utf-8")) + 1
                    if output_bytes + encoded_bytes > MAX_OUTPUT_BYTES:
                        raise OpenAssistantOASST1Error("OASST1 candidate превысил безопасный byte limit.")
                    if records_written >= MAX_OUTPUT_RECORDS:
                        raise OpenAssistantOASST1Error("OASST1 candidate превысил безопасный record limit.")
                    temporary.write(encoded_record)
                    temporary.write("\n")
                    output_bytes += encoded_bytes
                    records_written += 1
                    split_counts[record["split"]] += 1
                    exact_fingerprints.add(fingerprint)
                    near_duplicates.add(tree_id, signature)

        if records_written == 0:
            raise OpenAssistantOASST1Error("OASST1 import не получил ни одного безопасного dialogue record.")
        if output_path.exists():
            raise OpenAssistantOASST1Error("OASST1 output появился во время import; overwrite запрещён.")
        assert temporary_path is not None
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return OpenAssistantOASST1Summary(
        intake_path=str(intake_path),
        intake_sha256=intake_sha256,
        intake_bytes=intake_path.stat().st_size,
        output_path=str(output_path),
        output_sha256=file_sha256(output_path),
        output_bytes=output_path.stat().st_size,
        trees_seen=trees_seen,
        nodes_seen=node_counter["nodes_seen"],
        leaf_paths_seen=leaf_path_counter["leaf_paths_seen"],
        eligible_leaf_paths=leaf_path_counter["eligible_leaf_paths"],
        records_written=records_written,
        records_rejected=sum(record_rejections.values()),
        split_counts=dict(sorted(split_counts.items())),
        node_filter_counts=dict(sorted(node_filter_counts.items())),
        record_rejections=dict(sorted(record_rejections.items())),
    )


def summary_markdown(summary: OpenAssistantOASST1Summary) -> str:
    """Создаёт staging record без текстов, ответов или внутреннего tree metadata."""
    return "\n".join(
        [
            "# OpenAssistant OASST1 — staging import",
            "",
            f"Источник: [{SOURCE_ID}]({SOURCE_ORIGIN_URL}).",
            f"Pinned revision: `{SOURCE_REVISION}`; asset: `{SOURCE_FILE_NAME}`.",
            f"Лицензия: [{SOURCE_LICENSE}]({SOURCE_LICENSE_URL}).",
            f"SHA-256 intake: `{summary.intake_sha256}`; bytes: {summary.intake_bytes}.",
            f"SHA-256 staging JSONL: `{summary.output_sha256}`; bytes: {summary.output_bytes}.",
            f"Trees: {summary.trees_seen}; nodes: {summary.nodes_seen}; leaf paths: {summary.leaf_paths_seen}; eligible paths: {summary.eligible_leaf_paths}.",
            f"Dialogue records: {summary.records_written}; rejected candidates: {summary.records_rejected}; split: {summary.split_counts}.",
            f"Node filters: {summary.node_filter_counts}; candidate rejections: {summary.record_rejections}.",
            "",
            "Candidate остаётся вне active manifest. До promotion нужны independent isolated audit, active preflight и отдельное решение по Apache-2.0 source policy.",
            "",
        ]
    )


def create_staging_candidate(
    intake: Path = DEFAULT_INTAKE_PATH,
    destination: Path = DEFAULT_CANDIDATE,
    report_directory: Path = DEFAULT_REPORT_DIRECTORY,
    *,
    intake_root: Path = DEFAULT_INTAKE_ROOT,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> OpenAssistantOASST1Summary:
    """Выполняет fixed local import и сохраняет immutable summary без activation."""
    destination_report = report_directory.expanduser().resolve()
    if destination_report.exists():
        raise OpenAssistantOASST1Error("OASST1 staging report directory уже существует; overwrite запрещён.")
    summary = import_openassistant_oasst1(
        intake,
        destination,
        intake_root=intake_root,
        staging_root=staging_root,
    )
    destination_report.mkdir(parents=True)
    (destination_report / "openassistant_oasst1_staging.json").write_text(
        json.dumps(summary.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (destination_report / "OPENASSISTANT_OASST1_STAGING_REPORT.md").write_text(
        summary_markdown(summary),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    """Создаёт ровно один fixed OASST1 staging candidate без network/API/training."""
    try:
        summary = create_staging_candidate()
    except (OSError, OpenAssistantOASST1Error, ValueError) as error:
        raise SystemExit(f"OpenAssistant OASST1 staging остановлен: {error}") from error
    print(json.dumps(summary.as_dict(), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
