"""Защищает семейства OpenAssistant при смене версии исходного набора.

Индекс читает уже сохранённый OASST1 intake. Новые release должны исключать
старое дерево целиком: смена source_id не должна обходить разделение данных.
Тексты и пользовательские идентификаторы в индексе не сохраняются.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ai_core import openassistant_oasst1 as oasst1
from ai_core.brain_v3_corpus import BrainV3CorpusError, file_sha256


FAMILY_NAMESPACE = "openassistant-message-tree-v1"


class OpenAssistantFamilyGuardError(ValueError):
    """Останавливает проверку при повреждённом или изменившемся intake."""


def family_fingerprint(tree_id: str) -> str:
    """Даёт один hash для одного дерева независимо от release и source_id."""

    if (
        not isinstance(tree_id, str)
        or not tree_id
        or len(tree_id) > 128
        or tree_id != tree_id.strip()
        or any(ord(character) < 32 for character in tree_id)
    ):
        raise OpenAssistantFamilyGuardError("openassistant_family_invalid_tree_id")
    return hashlib.sha256(f"{FAMILY_NAMESPACE}:{tree_id}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExistingFamilyIndex:
    """Хранит только отпечатки всех старых семейств и hash проверенного архива."""

    source_sha256: str
    family_hashes: frozenset[str]

    def contains(self, tree_id: str) -> bool:
        """Определяет старое дерево до чтения и отбора его ветвей."""

        return family_fingerprint(tree_id) in self.family_hashes

    def metadata(self) -> dict[str, str | int]:
        """Возвращает компактную воспроизводимую сводку без самих ID."""

        index_hash = hashlib.sha256(
            "\n".join(sorted(self.family_hashes)).encode("ascii")
        ).hexdigest()
        return {
            "family_namespace": FAMILY_NAMESPACE,
            "source_sha256": self.source_sha256,
            "families_indexed": len(self.family_hashes),
            "family_index_sha256": index_hash,
        }


def load_existing_oasst1_families(
    path: Path = oasst1.DEFAULT_INTAKE_PATH,
    *, intake_root: Path = oasst1.DEFAULT_INTAKE_ROOT,
) -> ExistingFamilyIndex:
    """Индексирует все старые деревья, включая не прошедшие фильтры диалоги.

    Это намеренно шире active corpus: даже ранее отклонённая семья не должна
    случайно вернуться в train под другим release или новой ветвью.
    """

    try:
        verified_path = oasst1._safe_intake(path, intake_root)
        before = file_sha256(verified_path)
        families: set[str] = set()
        for payload in oasst1._iter_source_trees(verified_path):
            if not isinstance(payload, dict):
                raise OpenAssistantFamilyGuardError("openassistant_family_invalid_tree")
            fingerprint = family_fingerprint(payload.get("message_tree_id"))
            if fingerprint in families:
                raise OpenAssistantFamilyGuardError("openassistant_family_duplicate_tree")
            families.add(fingerprint)
            if len(families) > oasst1.MAX_TREES:
                raise OpenAssistantFamilyGuardError("openassistant_family_tree_limit_exceeded")
        if not families:
            raise OpenAssistantFamilyGuardError("openassistant_family_empty_index")
        if before != file_sha256(verified_path):
            raise OpenAssistantFamilyGuardError("openassistant_family_intake_drift")
    except (oasst1.OpenAssistantOASST1Error, BrainV3CorpusError, OSError) as error:
        raise OpenAssistantFamilyGuardError("openassistant_family_intake_invalid") from error
    return ExistingFamilyIndex(source_sha256=before, family_hashes=frozenset(families))
