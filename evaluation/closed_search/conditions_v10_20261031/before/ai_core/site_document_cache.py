"""Приватные контрольные точки чтения PDF: атомарная запись и проверка целостности."""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

# Ограничения ресурсов явные: превышение не обозначается как готовый документ.
MAX_PDF_PAGES = 500
MAX_PDF_CHARACTERS = 2_000_000


class ExtractionCancelled(Exception):
    """Снимок сменился; старое задание прекращаем между страницами."""


def read_checkpoint(path: Path):
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
        body = json.dumps(saved['value'], ensure_ascii=False, sort_keys=True)
        if saved['digest'] == hashlib.sha256(body.encode()).hexdigest():
            return saved['value']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def write_checkpoint(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, sort_keys=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        json.dump({'value': value, 'digest': hashlib.sha256(body.encode()).hexdigest()}, handle, ensure_ascii=False)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
