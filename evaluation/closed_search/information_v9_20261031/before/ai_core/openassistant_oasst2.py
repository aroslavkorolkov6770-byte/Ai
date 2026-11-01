"""Изолированный intake OASST2: новые семьи и полные человеческие ветви.

Источник и версия фиксированы. Этот модуль не меняет активный корпус и
не присваивает локальное human-approved качество автоматически.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ai_core import openassistant_oasst1 as previous
from ai_core.brain_v3_corpus import content_sha256, file_sha256
from ai_core.openassistant_family_guard import ExistingFamilyIndex, family_fingerprint


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "openassistant-oasst2-apache-v1"
REVISION = "179dd21fc55192153d94adb0e0ce8f69e222bf75"
FILE_NAME = "2023-11-05_oasst2_ready.trees.jsonl.gz"
EXPECTED_BYTES = 54370156
EXPECTED_SHA256 = "7a886a16ccfc1173c4f00a6897523e3c95b2785a86ee44a18a98f4f2807ee29b"
ORIGIN_URL = f"https://huggingface.co/datasets/OpenAssistant/oasst2/tree/{REVISION}"
CARD_URL = f"https://huggingface.co/datasets/OpenAssistant/oasst2/blob/{REVISION}/README.md"
CARD_RAW_URL = f"https://huggingface.co/datasets/OpenAssistant/oasst2/raw/{REVISION}/README.md"
ASSET_URL = f"https://huggingface.co/datasets/OpenAssistant/oasst2/resolve/{REVISION}/{FILE_NAME}"
INTAKE_ROOT = ROOT / "data/brain_v3/intake" / SOURCE_ID
INTAKE_PATH = INTAKE_ROOT / FILE_NAME
DESCRIPTOR_PATH = ROOT / "data/brain_v3/intake/openassistant-oasst2-source-review-v0.1.json"
MAX_DECOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_LINE_BYTES = 2 * 1024 * 1024
MAX_TREES = 20000
MAX_NODES = 160000
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
ALLOWED_HOSTS = frozenset({
    "huggingface.co", "cdn-lfs.huggingface.co", "cdn-lfs.hf.co", "cas-bridge.xethub.hf.co",
    "us.aws.cdn.hf.co",
})


class OASST2PilotError(ValueError):
    """Останавливает intake до появления непроверенного результата."""


def inside(path: Path, root: Path) -> Path:
    """Разрешает выход только в назначенную локальную папку."""

    result = path.resolve()
    try:
        result.relative_to(root.resolve())
    except ValueError as error:
        raise OASST2PilotError("oasst2_path_outside_root") from error
    return result


def allowed_url(url: str) -> bool:
    """Не разрешает downgrade или неизвестный download-сервер."""

    try:
        parsed = urlparse(url)
        return (
            parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS
            and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


class _PinnedRedirects(HTTPRedirectHandler):
    """Разрешает только HTTPS CDN источника; signed URL не попадает в отчёты."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise OASST2PilotError("oasst2_download_redirect_rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_descriptor() -> str:
    """Не позволяет локальным metadata подменить источник, hash или лицензию."""

    try:
        raw = DESCRIPTOR_PATH.read_bytes()
        descriptor = json.loads(raw)
        asset = descriptor["asset"]
        valid = (
            descriptor["source_id"] == SOURCE_ID
            and descriptor["source_revision"] == REVISION
            and descriptor["license_declaration"]["name"] == "Apache-2.0"
            and descriptor["dataset_card_url"] == CARD_URL
            and asset["url"] == ASSET_URL and asset["name"] == FILE_NAME
            and asset["expected_sha256"] == EXPECTED_SHA256
            and asset["expected_compressed_bytes"] == EXPECTED_BYTES
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise OASST2PilotError("oasst2_descriptor_invalid") from error
    if not valid:
        raise OASST2PilotError("oasst2_descriptor_drift")
    return hashlib.sha256(raw).hexdigest()


def verify_intake(path: Path = INTAKE_PATH, *, intake_root: Path = INTAKE_ROOT) -> Path:
    """Проверяет точное имя, размер и bytes hash закреплённого gzip."""

    resolved = inside(path, intake_root)
    if resolved.name != FILE_NAME or not resolved.is_file() or resolved.is_symlink():
        raise OASST2PilotError("oasst2_intake_invalid")
    if resolved.stat().st_size != EXPECTED_BYTES or file_sha256(resolved) != EXPECTED_SHA256:
        raise OASST2PilotError("oasst2_intake_hash_or_size_mismatch")
    return resolved


def download_intake() -> str:
    """Скачивает один fixed asset атомарно и проверяет декларацию издателя."""

    validate_descriptor()
    if INTAKE_PATH.exists():
        verify_intake()
        return "reused_verified_snapshot"
    INTAKE_ROOT.mkdir(parents=True, exist_ok=True)
    opener = build_opener(_PinnedRedirects())
    temporary: Path | None = None
    try:
        with opener.open(Request(CARD_RAW_URL), timeout=40) as response:
            card = response.read(32769)
        if len(card) > 32768 or b"license: apache-2.0" not in card:
            raise OASST2PilotError("oasst2_publisher_license_declaration_changed")
        digest = hashlib.sha256()
        total = 0
        with opener.open(Request(ASSET_URL), timeout=45) as response:
            if not allowed_url(response.geturl()):
                raise OASST2PilotError("oasst2_download_host_rejected")
            with tempfile.NamedTemporaryFile(dir=INTAKE_ROOT, suffix=".part", delete=False) as stream:
                temporary = Path(stream.name)
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > EXPECTED_BYTES:
                        raise OASST2PilotError("oasst2_download_size_exceeded")
                    digest.update(chunk)
                    stream.write(chunk)
        if total != EXPECTED_BYTES or digest.hexdigest() != EXPECTED_SHA256:
            raise OASST2PilotError("oasst2_download_hash_or_size_mismatch")
        # Hard link создаёт новое имя и отказывает, если оно уже занято.
        os.link(temporary, INTAKE_PATH)
    except OASST2PilotError:
        raise
    except (OSError, ValueError) as error:
        # Сетевые exception могут содержать временный signed URL: не печатаем их.
        raise OASST2PilotError("oasst2_download_failed") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return "downloaded_and_verified"


def source_trees(path: Path) -> Iterable[dict[str, Any]]:
    """Читает gzip в памяти, ограничивая строку, распаковку и число деревьев."""

    total = 0
    count = 0
    try:
        with gzip.open(path, "rb") as stream:
            while raw := stream.readline(MAX_LINE_BYTES + 1):
                total += len(raw)
                if len(raw) > MAX_LINE_BYTES or total > MAX_DECOMPRESSED_BYTES:
                    raise OASST2PilotError("oasst2_decompression_limit_exceeded")
                if not raw.strip():
                    continue
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise OASST2PilotError("oasst2_tree_not_object")
                count += 1
                if count > MAX_TREES:
                    raise OASST2PilotError("oasst2_tree_limit_exceeded")
                yield payload
    except (OSError, EOFError, UnicodeError, ValueError) as error:
        if isinstance(error, OASST2PilotError):
            raise
        raise OASST2PilotError("oasst2_invalid_gzip_jsonl") from error


def safe_paths(tree: dict[str, Any], counts: Counter[str]) -> Iterable[list[previous._SafeTurn]]:
    """Отбрасывает небезопасную ветвь целиком, не склеивает её безопасные части."""

    if tree.get("tree_state") != "ready_for_export":
        counts["not_ready_tree"] += 1
        return
    seen_messages: set[str] = set()

    def walk(node: Any, path: list, parent_id: str | None, depth: int):
        counts["nodes_seen"] += 1
        if depth > 16 or counts["nodes_seen"] > MAX_NODES:
            raise OASST2PilotError("oasst2_node_limit_exceeded")
        if not isinstance(node, dict):
            counts["invalid_node"] += 1
            return
        if not isinstance(node.get("role"), str):
            counts["invalid_role"] += 1
            return
        if node.get("model_name") not in (None, ""):
            counts["model_origin"] += 1
            return
        turn, rejection = previous._safe_turn(node)
        if turn is None:
            counts[rejection or "invalid_node"] += 1
            return
        if turn.message_id in seen_messages:
            raise OASST2PilotError("oasst2_repeated_message_id")
        seen_messages.add(turn.message_id)
        if (not path and turn.message_id != tree.get("message_tree_id")) or (
            "parent_id" in node and node["parent_id"] != parent_id
        ):
            counts["broken_parent_link"] += 1
            return
        expected_role = "user" if len(path) % 2 == 0 else "assistant"
        if turn.role != expected_role:
            counts["role_sequence_break"] += 1
            return
        replies = node.get("replies")
        if not isinstance(replies, list) or len(replies) > 32:
            counts["invalid_replies"] += 1
            return
        extended = [*path, turn]
        if not replies:
            counts["safe_leaf_paths_seen"] += 1
            if len(extended) >= 2 and len(extended) % 2 == 0:
                yield extended
            else:
                counts["incomplete_role_path"] += 1
            return
        for reply in replies:
            yield from walk(reply, extended, turn.message_id, depth + 1)

    yield from walk(tree.get("prompt"), [], None, 1)


def make_record(tree_id: str, turns: list[previous._SafeTurn]) -> dict[str, Any]:
    """Создаёт технический candidate с неразмеченными темой, целью и тоном."""

    family = family_fingerprint(tree_id)
    bucket = int(family[:8], 16) % 100
    split = "train" if bucket < 80 else "validation" if bucket < 90 else "test"
    identifier = hashlib.sha256(f"{tree_id}:{turns[-1].message_id}".encode()).hexdigest()[:28]
    record = {
        "format_version": 1, "id": f"oasst2.ru.{identifier}", "record_type": "dialogue",
        "provenance": {
            "source_id": SOURCE_ID, "license": "Apache-2.0", "license_url": CARD_URL,
            "origin_url": ORIGIN_URL, "attribution": "OpenAssistant OASST2 pinned publisher release",
        },
        "language": "ru", "layer": "dialogue", "split": split,
        "topic": "unclassified", "dialogue_goal": "unclassified", "emotional_tone": "unclassified",
        "quality_tier": "license_verified",
        # Это целевое свойство для review, а не подтверждённая оценка ответа.
        "expected_response_properties": ["relevant"],
        "turns": [{"role": turn.role, "text": turn.text} for turn in turns],
    }
    record["content_sha256"] = content_sha256(record)
    return record


def collect_new_records(
    path: Path,
    families: ExistingFamilyIndex,
    reject_path: Callable[[list[dict[str, str]]], str | None],
    *, intake_root: Path = INTAKE_ROOT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Выбирает не более одной полной ветви каждой новой семьи; сохраняет счётчики."""

    source = verify_intake(path, intake_root=intake_root)
    counts: Counter[str] = Counter()
    seen_families: set[str] = set()
    selected = []
    family_items = []
    output_bytes = 0
    for tree in source_trees(source):
        counts["trees_seen"] += 1
        tree_id = tree.get("message_tree_id")
        fingerprint = family_fingerprint(tree_id)
        if fingerprint in seen_families:
            raise OASST2PilotError("oasst2_duplicate_tree")
        seen_families.add(fingerprint)
        if families.contains(tree_id):
            counts["existing_oasst1_family"] += 1
            continue
        counts["new_families_seen"] += 1
        eligible = []
        for turns in safe_paths(tree, counts):
            counts["eligible_safe_paths"] += 1
            if len(turns) < 4:
                counts["two_turn_only_path"] += 1
                continue
            rendered = [{"role": turn.role, "text": turn.text} for turn in turns]
            rejection = reject_path(rendered)
            if rejection is not None:
                counts[rejection] += 1
                continue
            eligible.append(turns)
        if not eligible:
            continue
        # Детерминированный выбор наиболее длинной целой ветви, без склейки.
        best = min(eligible, key=lambda turns: (-len(turns), turns[-1].message_id))
        record = make_record(tree_id, best)
        output_bytes += len(json.dumps(record, ensure_ascii=False).encode("utf-8")) + 1
        if output_bytes > MAX_OUTPUT_BYTES:
            raise OASST2PilotError("oasst2_output_limit_exceeded")
        selected.append(record)
        family_items.append({"id": record["id"], "family_sha256": fingerprint})
    verify_intake(path, intake_root=intake_root)
    return selected, {
        "counts": dict(sorted(counts.items())), "families": families.metadata(),
        "family_items": family_items,
    }
