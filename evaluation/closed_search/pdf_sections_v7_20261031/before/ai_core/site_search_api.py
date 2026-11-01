"""Внутренний API поискового ядра. Пользовательские роли подтверждает сервер сайта."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .site_documents import extract_document, PARSER_VERSION
from .site_pdf_locations import extract_pdf_layout, LOCATION_VERSION
from .site_search import Principal
from .site_search_runtime import DurableSearch
from .site_document_cache import ExtractionCancelled

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get("VATES_SEARCH_CONFIG", ROOT / ".runtime" / "closed-search" / "sites.json"))
router = APIRouter(prefix="/closed-search", tags=["closed-site-search"])
indexes: dict[str, DurableSearch] = {}
jobs: set[asyncio.Task] = set()
runtime_lock = asyncio.Lock()
build_slots = asyncio.Semaphore(2)
STORAGE = Path(os.environ.get("VATES_SEARCH_STORAGE", ROOT / ".runtime" / "closed-search" / "indexes"))
EXTRACTION_VERSION = PARSER_VERSION + '-' + LOCATION_VERSION


async def runtime_for(site: str, host: dict) -> DurableSearch:
    async with runtime_lock:
        current = indexes.get(site)
        if current is None or current.index.origins != set(host["origins"]):
            try:
                # Имя уже проверено authenticate; каждому сайту принадлежит
                # отдельная база. Чтение диска не блокирует цикл HTTP-сервера.
                current = await asyncio.to_thread(DurableSearch, host["origins"], STORAGE / (site + ".sqlite3"), extraction_version=EXTRACTION_VERSION)
                indexes[site] = current
            except Exception:
                logging.getLogger(__name__).error("closed_search_storage_unavailable")
                raise HTTPException(503, "Не удалось открыть сохранённые материалы") from None
        return current


def authenticate(request: Request, site: str) -> dict:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", site):
        raise HTTPException(404, "Сайт недоступен")
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(503, "Закрытый поиск не настроен") from None
    supplied = hashlib.sha256(request.headers.get("X-Vates-Search-Key", "").encode()).hexdigest()
    host = next((item for item in config.get("sites", []) if item.get("id") == site), None)
    if not host or not hmac.compare_digest(supplied, host.get("token_sha256", "")):
        raise HTTPException(403, "Недостаточно прав доступа")
    return host


async def body(request: Request, maximum: int) -> dict:
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > maximum:
            raise HTTPException(413, "Слишком большой запрос")
    try:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Некорректный запрос") from None


@router.post("/{site}/snapshot")
async def snapshot(site: str, request: Request):
    host = authenticate(request, site)
    payload = await body(request, 24 * 1024 * 1024)
    sources = payload.get("sources")
    if not isinstance(sources, list) or len(sources) > 3000 or not all(isinstance(item, dict) for item in sources):
        raise HTTPException(400, "Некорректный снимок материалов")
    runtime = await runtime_for(site, host)
    fingerprint = hashlib.sha256(EXTRACTION_VERSION.encode() + json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    try:
        generation = await asyncio.to_thread(runtime.begin, fingerprint)
    except Exception:
        logging.getLogger(__name__).error("closed_search_snapshot_save_failed")
        raise HTTPException(503, "Не удалось сохранить обновление материалов") from None
    if generation is None:
        current = await asyncio.to_thread(runtime.status)
        return {"state": current["state"], "snapshot": fingerprint}

    for raw in sources:
        if raw.get('file_data') and isinstance(raw.get('id'), str):
            runtime.update_document_progress(raw['id'], raw.get('revision'), generation, {'state': 'queued'})

    def prepare():
        prepared = []
        for raw in sources:
            if runtime.index.generation != generation:
                return None
            source = dict(raw)
            if not isinstance(source.get("id"), str) or len(source["id"]) > 150:
                raise ValueError("source_id_invalid")
            if source.get("file_data"):
                data = source.pop("file_data")
                details = {'state': 'reading'}

                def progress(values):
                    details.update(values)
                    if not runtime.update_document_progress(source['id'], source.get('revision'), generation, details.copy()):
                        raise ExtractionCancelled()

                progress({})
                try:
                    text, complete = extract_document(data, source.get("file_name", ""), progress=progress)
                    source["sections"] = [{"heading": source.get("title", ""), "text": text}]
                    source["complete"] = complete
                    final_state = 'partial' if not complete else 'review' if any(marker in text for marker in ['[STRUCTURE_UNCERTAIN]', '[LOCAL_STRUCTURE_UNCERTAIN]']) else 'ready'
                except ExtractionCancelled:
                    return None
                except Exception:
                    source["sections"], source["complete"] = [], False
                    final_state = 'failed'
                    logging.getLogger(__name__).warning("closed_search_document_partial")
                if final_state != 'failed' and str(source.get('file_name', '')).lower().endswith('.pdf'):
                    try:
                        progress({'state': 'locating'})
                        source['pdf_layout'] = extract_pdf_layout(data, progress=progress)
                        details['positions_complete'] = source['pdf_layout'].get('complete', False)
                    except ExtractionCancelled:
                        return None
                    except Exception:
                        source['pdf_layout'] = None
                        details['positions_complete'] = False
                        logging.getLogger(__name__).warning('closed_search_pdf_position_unavailable')
                progress({'state': final_state})
                source['processing'] = details.copy()
            if not isinstance(source.get("sections"), list) or len(source["sections"]) > 300:
                raise ValueError("source_sections_invalid")
            prepared.append(source)
        return prepared

    async def build():
        try:
            # Не более двух извлечений одновременно. Устаревшие ожидающие
            # задания прекращаются до OCR, текущий снимок публикуется атомарно.
            async with build_slots:
                prepared = await asyncio.to_thread(prepare)
                if prepared is not None:
                    await asyncio.to_thread(runtime.publish, prepared, generation, payload.get("aliases", {}))
        except Exception:
            await asyncio.to_thread(runtime.fail, generation)
            logging.getLogger(__name__).error("closed_search_snapshot_failed")
    task = asyncio.create_task(build())
    jobs.add(task)
    task.add_done_callback(jobs.discard)
    return JSONResponse({"state": "processing", "snapshot": fingerprint}, status_code=202, headers={"Cache-Control": "no-store"})


@router.get("/{site}/status")
async def status(site: str, request: Request):
    host = authenticate(request, site)
    runtime = await runtime_for(site, host)
    return JSONResponse(await asyncio.to_thread(runtime.status), headers={"Cache-Control": "no-store"})


@router.post("/{site}/query")
async def query(site: str, request: Request):
    host = authenticate(request, site)
    payload = await body(request, 1024 * 1024)
    question = payload.get("question")
    identity = payload.get("principal")
    chat = payload.get("chat_id", "default")
    if not isinstance(question, str) or not isinstance(identity, dict) or not isinstance(identity.get("user_id"), str) or identity.get("role") not in {"admin", "staff"} or not isinstance(chat, str) or len(chat) > 150:
        raise HTTPException(400, "Не удалось проверить контекст пользователя")
    groups = identity.get("groups", [])
    if not isinstance(groups, list) or not all(isinstance(item, str) for item in groups):
        raise HTTPException(400, "Не удалось проверить контекст пользователя")
    runtime = await runtime_for(site, host)
    # Роль и user_id принимаются только от аутентифицированного серверного адаптера.
    selected = payload.get("selected_source")
    if selected is not None and (not isinstance(selected, str) or len(selected) > 150):
        raise HTTPException(400, "Не удалось определить материал")
    revisions = payload.get("current_revisions")
    if not isinstance(revisions, dict) or len(revisions) > 3000 or any(not isinstance(key, str) or not isinstance(value, str) for key, value in revisions.items()):
        raise HTTPException(400, "Не удалось подтвердить актуальность материалов")
    generation = runtime.index.generation
    try:
        result = await asyncio.to_thread(runtime.query, question, Principal(identity["user_id"], identity["role"], tuple(groups)), chat, selected, revisions)
    except Exception:
        logging.getLogger(__name__).error("closed_search_query_failed")
        result = {"status": "unavailable", "text": "Не удалось проверить доступные материалы. Повторите вопрос позже.", "links": [], "evidence": []}
    if runtime.index.generation != generation or runtime.index.state == "processing":
        result = {"status": "processing", "text": "Материалы сейчас обновляются. Повторите вопрос после завершения обработки.", "links": [], "evidence": []}
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post('/{site}/fragment')
async def fragment(site: str, request: Request):
    host = authenticate(request, site)
    payload = await body(request, 1024 * 1024)
    identity, revisions = payload.get('principal'), payload.get('current_revisions')
    fields = [payload.get(name) for name in ('source', 'revision', 'fragment')]
    if (not isinstance(identity, dict) or not isinstance(identity.get('user_id'), str)
            or identity.get('role') not in {'admin', 'staff'}
            or not all(isinstance(value, str) and 0 < len(value) <= 150 for value in fields)
            or not re.fullmatch('[a-f0-9]{64}', fields[2])
            or not isinstance(revisions, dict) or len(revisions) > 3000
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in revisions.items())):
        raise HTTPException(400, 'Не удалось определить материал')
    groups = identity.get('groups', [])
    if not isinstance(groups, list) or not all(isinstance(value, str) for value in groups):
        raise HTTPException(400, 'Не удалось проверить пользователя')
    runtime = await runtime_for(site, host)
    try:
        result = await asyncio.to_thread(runtime.fragment, *fields,
            Principal(identity['user_id'], identity['role'], tuple(groups)), revisions)
    except Exception:
        logging.getLogger(__name__).error('closed_search_fragment_failed')
        result = {'status': 'unavailable', 'message': 'Не удалось открыть материал. Попробуйте позже.'}
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


# Тот же компонент можно запускать отдельно для изолированной приёмки.
app = FastAPI(title="ВатэсAi — закрытый поиск")
app.include_router(router)
