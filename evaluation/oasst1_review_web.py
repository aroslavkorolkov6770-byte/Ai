"""Локальный браузерный экран для ручной проверки OASST1 v0.4.

Сервис намеренно отделён от основного chat API, staging, promotion и обучения.
Он слушает только loopback-адрес, показывает исходный dialogue только по
явному запросу локального браузера и передаёт сохранение verdict существующему
metadata-only workflow из :mod:`evaluation.oasst1_human_review`.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from evaluation.oasst1_human_review import (
    APPROVAL_REASON_CODES,
    DEFAULT_DECISIONS_PATH,
    DEFAULT_QUEUE_PATH,
    DEFAULT_SOURCE_PATH,
    OASST1HumanReviewError,
    RAW_ROOT,
    RESULTS_ROOT,
    REVIEW_ROOT,
    append_review_decision,
    build_review_summary,
    load_review_decisions,
    load_review_queue,
    load_source_records_for_queue,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8011
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})
SPLIT_ORDER = ("train", "validation", "test")
ATTENTION_CODE_SCORES = {
    "contains_email": 4,
    "contains_phone_like_sequence": 4,
    "contains_url": 3,
    "repeated_assistant_turn_within_dialogue": 3,
    "short_user_clarification_after_answer": 2,
    "assistant_over_700_chars": 1,
    "multiline_turn": 1,
}
ATTENTION_URL_PATTERN = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
ATTENTION_EMAIL_PATTERN = re.compile(
    r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b",
    re.IGNORECASE,
)
ATTENTION_PHONE_PATTERN = re.compile(r"(?:\+?\d[\d\s()\-]{7,}\d)")
ATTENTION_CLARIFICATION_PATTERN = re.compile(
    r"\b(?:что|чего|как|почему|зачем|то\s+есть)\b",
    re.IGNORECASE,
)
NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


@dataclass(frozen=True)
class ReviewPaths:
    """Собирает все локальные пути review в одном проверяемом объекте."""

    queue_path: Path = DEFAULT_QUEUE_PATH
    source_path: Path = DEFAULT_SOURCE_PATH
    decisions_path: Path = DEFAULT_DECISIONS_PATH
    results_root: Path = RESULTS_ROOT
    raw_root: Path = RAW_ROOT
    reviews_root: Path = REVIEW_ROOT


DEFAULT_REVIEW_PATHS = ReviewPaths()


class ReviewDecisionRequest(BaseModel):
    """Принимает только минимальные metadata для решения человека."""

    candidate_id: str = Field(min_length=1, max_length=200)
    decision: Literal["approved", "rejected"]
    reason_codes: list[str] = Field(min_length=1, max_length=11)
    reviewer_role: str = Field(min_length=3, max_length=64)
    approval_confirmed: bool = False


def is_loopback_host(host: str | None) -> bool:
    """Не разрешает UI и API review работать на внешнем интерфейсе."""

    return isinstance(host, str) and host.strip().lower() in LOOPBACK_HOSTS


def _require_loopback_request(request: Request) -> None:
    """Проверяет адрес клиента и, при наличии, origin браузерного запроса."""

    client_host = request.client.host if request.client is not None else None
    if not is_loopback_host(client_host):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Review доступен только из локального браузера.",
        )
    origin = request.headers.get("origin")
    if origin:
        origin_host = urlparse(origin).hostname
        if not is_loopback_host(origin_host):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Review не принимает cross-origin запросы.",
            )


def _load_queue_and_decisions(
    paths: ReviewPaths,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Загружает лишь проверенную queue и metadata verdicts без записи на диск."""

    queue = load_review_queue(paths.queue_path, results_root=paths.results_root)
    decisions = load_review_decisions(
        paths.decisions_path,
        queue,
        reviews_root=paths.reviews_root,
    )
    return queue, decisions


def build_review_status(paths: ReviewPaths = DEFAULT_REVIEW_PATHS) -> dict[str, Any]:
    """Возвращает безопасный progress snapshot без идентификаторов и текста диалогов."""

    queue, decisions = _load_queue_and_decisions(paths)
    summary = build_review_summary(queue, decisions)
    return {
        "queue_selected_records": summary["queue_selected_records"],
        "reviewed_records": summary["reviewed_records"],
        "approved_records": summary["approved_records"],
        "rejected_records": summary["rejected_records"],
        "unreviewed_records": summary["unreviewed_records"],
        "review_complete": summary["review_complete"],
        "review_status": summary["review_status"],
        "training_candidate_allowed": summary["training_candidate_allowed"],
        "promotion_allowed": summary["promotion_allowed"],
    }


def review_attention_codes(source_record: Mapping[str, Any]) -> tuple[str, ...]:
    """Отмечает механические сигналы для внимательного чтения без verdict.

    Коды не означают, что dialogue плохой, не меняют queue и не используются
    для отказа. Их назначение — помочь reviewer быстрее увидеть длинный ответ,
    контактные данные, ссылку, повтор или возможный разрыв follow-up.
    """

    turns = source_record.get("turns")
    if not isinstance(turns, list):
        raise OASST1HumanReviewError("oasst1_review_web_source_turns_invalid")
    codes: set[str] = set()
    assistant_texts: list[str] = []
    for index, turn in enumerate(turns):
        if not isinstance(turn, Mapping) or not isinstance(turn.get("text"), str):
            raise OASST1HumanReviewError("oasst1_review_web_source_turn_invalid")
        text = turn["text"].strip()
        if ATTENTION_URL_PATTERN.search(text):
            codes.add("contains_url")
        if ATTENTION_EMAIL_PATTERN.search(text):
            codes.add("contains_email")
        if ATTENTION_PHONE_PATTERN.search(text):
            codes.add("contains_phone_like_sequence")
        if "\n" in text:
            codes.add("multiline_turn")
        if turn.get("role") == "assistant":
            assistant_texts.append(re.sub(r"\s+", " ", text.casefold()))
            if len(text) > 700:
                codes.add("assistant_over_700_chars")
        if (
            index > 0
            and turn.get("role") == "user"
            and isinstance(turns[index - 1], Mapping)
            and turns[index - 1].get("role") == "assistant"
            and len(text) <= 120
            and text.endswith("?")
            and ATTENTION_CLARIFICATION_PATTERN.search(text)
        ):
            codes.add("short_user_clarification_after_answer")
    if len(assistant_texts) != len(set(assistant_texts)):
        codes.add("repeated_assistant_turn_within_dialogue")
    return tuple(sorted(codes))


def review_attention_score(codes: Iterable[str]) -> int:
    """Даёт порядок чтения, но не качество и не право на automatic rejection."""

    return sum(ATTENTION_CODE_SCORES.get(code, 0) for code in set(codes))


def build_review_order(
    queue_items: Iterable[Mapping[str, Any]],
    *,
    attention_codes_by_id: Mapping[str, Iterable[str]] | None = None,
) -> list[dict[str, Any]]:
    """Чередует темы, чтобы ранний review покрывал разные типы диалогов.

    Порядок зависит только от уже имеющейся metadata queue. Он не оценивает
    качество реплик, не меняет split и не может превратить candidate label в
    автоматический verdict.
    """

    attention_scores = {
        identifier: review_attention_score(codes)
        for identifier, codes in (attention_codes_by_id or {}).items()
    }
    items_by_topic_and_split: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for raw_item in queue_items:
        item = dict(raw_item)
        primary_topic = item.get("primary_candidate_topic")
        topic_labels = item.get("candidate_topic_labels")
        if not isinstance(primary_topic, str) or not primary_topic:
            if not isinstance(topic_labels, list) or not topic_labels:
                raise OASST1HumanReviewError("oasst1_review_web_queue_topic_invalid")
            primary_topic = topic_labels[0]
        split = item.get("split")
        if split not in SPLIT_ORDER:
            raise OASST1HumanReviewError("oasst1_review_web_queue_split_invalid")
        items_by_topic_and_split.setdefault(primary_topic, {}).setdefault(split, []).append(item)
    for split_buckets in items_by_topic_and_split.values():
        for topic_items in split_buckets.values():
            topic_items.sort(
                key=lambda item: (
                    -attention_scores.get(item["id"], 0),
                    -item["turn_count"],
                    item["id"],
                )
            )
    ordered_items: list[dict[str, Any]] = []
    ordered_topics = sorted(items_by_topic_and_split)
    round_index = 0
    while any(
        topic_items
        for split_buckets in items_by_topic_and_split.values()
        for topic_items in split_buckets.values()
    ):
        emitted_item = False
        for topic_index, topic in enumerate(ordered_topics):
            split_buckets = items_by_topic_and_split[topic]
            split_offset = (topic_index + round_index) % len(SPLIT_ORDER)
            preferred_splits = (
                SPLIT_ORDER[(split_offset + offset) % len(SPLIT_ORDER)]
                for offset in range(len(SPLIT_ORDER))
            )
            for split in preferred_splits:
                topic_items = split_buckets.get(split, [])
                if topic_items:
                    ordered_items.append(topic_items.pop(0))
                    emitted_item = True
                    break
        if not emitted_item:
            raise OASST1HumanReviewError("oasst1_review_web_queue_order_invalid")
        round_index += 1
    return ordered_items


def _next_unreviewed_item(
    queue_items: Iterable[Mapping[str, Any]],
    reviewed_identifiers: set[str],
    after_candidate_id: str | None,
    *,
    attention_codes_by_id: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Any] | None:
    """Находит следующий unreviewed item по стратифицированному циклическому порядку."""

    ordered_items = build_review_order(
        queue_items,
        attention_codes_by_id=attention_codes_by_id,
    )
    if not ordered_items or all(item["id"] in reviewed_identifiers for item in ordered_items):
        return None
    if after_candidate_id is None:
        return next(item for item in ordered_items if item["id"] not in reviewed_identifiers)
    after_index = next(
        index for index, item in enumerate(ordered_items) if item["id"] == after_candidate_id
    )
    for offset in range(1, len(ordered_items) + 1):
        item = ordered_items[(after_index + offset) % len(ordered_items)]
        if item["id"] not in reviewed_identifiers:
            return item
    return None


def load_next_candidate(
    paths: ReviewPaths = DEFAULT_REVIEW_PATHS,
    *,
    after_candidate_id: str | None = None,
) -> dict[str, Any] | None:
    """Читает один source dialogue только в память для явного local review."""

    queue, decisions = _load_queue_and_decisions(paths)
    queue_items = {item["id"]: item for item in queue["items"]}
    if after_candidate_id is not None and after_candidate_id not in queue_items:
        raise OASST1HumanReviewError("oasst1_review_web_unknown_candidate")
    reviewed_identifiers = {record["candidate_id"] for record in decisions}
    source_records = load_source_records_for_queue(
        queue,
        paths.source_path,
        raw_root=paths.raw_root,
    )
    attention_codes_by_id = {
        identifier: review_attention_codes(record)
        for identifier, record in source_records.items()
    }
    item = _next_unreviewed_item(
        queue["items"],
        reviewed_identifiers,
        after_candidate_id,
        attention_codes_by_id=attention_codes_by_id,
    )
    if item is None:
        return None
    source_record = source_records[item["id"]]
    return {
        "id": item["id"],
        "split": item["split"],
        "turn_count": item["turn_count"],
        "candidate_topic_labels": item["candidate_topic_labels"],
        "review_attention_codes": list(attention_codes_by_id[item["id"]]),
        "turns": [
            {"role": turn["role"], "text": turn["text"].strip()}
            for turn in source_record["turns"]
        ],
    }


def record_human_decision(
    payload: ReviewDecisionRequest,
    paths: ReviewPaths = DEFAULT_REVIEW_PATHS,
) -> dict[str, Any]:
    """Передаёт явный verdict в существующий atomic metadata-only ledger."""

    if payload.decision == "approved" and not payload.approval_confirmed:
        raise OASST1HumanReviewError("oasst1_review_web_approval_not_confirmed")
    if payload.decision == "rejected" and payload.approval_confirmed:
        raise OASST1HumanReviewError("oasst1_review_web_rejection_has_approval_confirmation")

    record = append_review_decision(
        paths.queue_path,
        paths.source_path,
        paths.decisions_path,
        candidate_id=payload.candidate_id,
        decision=payload.decision,
        reason_codes=payload.reason_codes,
        reviewer_role=payload.reviewer_role,
        results_root=paths.results_root,
        raw_root=paths.raw_root,
        reviews_root=paths.reviews_root,
    )
    return {
        "candidate_id": record["candidate_id"],
        "decision": record["decision"],
        "reason_codes": record["reason_codes"],
        "reviewed_at": record["reviewed_at"],
        "status": build_review_status(paths),
    }


REVIEW_PAGE = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Локальная проверка диалогов</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      background: #0d1117;
      color: #e6edf3;
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #0d1117; }
    main { width: min(920px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 44px; }
    h1 { margin: 0; font-size: clamp(24px, 4vw, 34px); }
    h2 { margin: 0; font-size: 19px; }
    p { line-height: 1.5; color: #a9b7c6; }
    .notice { border: 1px solid #2c4054; border-radius: 12px; padding: 14px 16px; background: #111a24; }
    .stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 20px 0; }
    .stat { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 12px; }
    .stat strong { display: block; font-size: 25px; color: #f0f6fc; }
    .stat span { color: #a9b7c6; font-size: 13px; }
    .panel { margin-top: 16px; padding: 20px; border: 1px solid #30363d; border-radius: 14px; background: #161b22; }
    .meta { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 18px; }
    .tag { padding: 5px 8px; border-radius: 999px; background: #1f6feb22; border: 1px solid #1f6feb66; color: #9ecbff; font-size: 13px; }
    .attention { margin: 0 0 16px; padding: 10px 12px; border: 1px solid #9e6a0366; border-radius: 10px; background: #9e6a031c; color: #e3b341; line-height: 1.45; }
    .dialogue { display: grid; gap: 12px; }
    .turn { padding: 13px 15px; border-radius: 12px; max-width: 88%; white-space: pre-wrap; line-height: 1.5; }
    .turn-user { margin-left: auto; background: #1f6feb; color: #fff; }
    .turn-assistant { background: #21262d; color: #e6edf3; }
    .turn-role { display: block; margin-bottom: 5px; font-size: 12px; opacity: .8; }
    .criteria { display: grid; gap: 8px; padding-left: 0; list-style: none; }
    .criteria li { color: #c9d1d9; }
    .criteria label { padding: 10px 12px; border: 1px solid #30363d; border-radius: 9px; background: #0d1117; }
    .rejection-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    label { display: block; color: #c9d1d9; }
    input[type="text"] { width: 100%; margin-top: 6px; padding: 10px 12px; border: 1px solid #30363d; border-radius: 8px; background: #0d1117; color: #e6edf3; }
    input[type="checkbox"] { accent-color: #1f6feb; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }
    button { min-height: 40px; padding: 9px 14px; border: 1px solid transparent; border-radius: 8px; background: #30363d; color: #f0f6fc; cursor: pointer; font: inherit; }
    button:hover { border-color: #8b949e; }
    button:disabled { opacity: .55; cursor: wait; }
    .approve { background: #238636; }
    .reject { background: #b62324; }
    .status { min-height: 24px; margin-top: 14px; color: #a9b7c6; }
    .error { color: #ff7b72; }
    .empty { text-align: center; padding: 38px 12px; color: #a9b7c6; }
    .hidden { display: none; }
    @media (max-width: 680px) {
      main { width: min(100% - 20px, 920px); padding-top: 18px; }
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .rejection-grid { grid-template-columns: 1fr; }
      .turn { max-width: 96%; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Проверка диалогов для Brain v3</h1>
      <p class="notice">Экран доступен только на этом компьютере. Реплики не копируются в журнал: после нажатия кнопки сохраняются только ID, решение, выбранные коды и время.</p>
    </header>

    <section class="stats" aria-label="Статус проверки">
      <div class="stat"><strong id="reviewed-count">—</strong><span>проверено</span></div>
      <div class="stat"><strong id="approved-count">—</strong><span>одобрено</span></div>
      <div class="stat"><strong id="rejected-count">—</strong><span>отклонено</span></div>
      <div class="stat"><strong id="remaining-count">—</strong><span>осталось</span></div>
    </section>

    <section id="candidate-panel" class="panel" aria-live="polite">
      <h2 id="candidate-title">Загрузка локальной очереди</h2>
      <div id="candidate-meta" class="meta"></div>
      <div id="dialogue" class="dialogue"></div>
    </section>

    <section id="decision-panel" class="panel">
      <h2>Зафиксировать решение</h2>
      <p>Одобряй только если весь диалог естественный, связный, отвечает на реплики и не содержит замеченного риска. Если есть проблема — выбери хотя бы одну причину отклонения.</p>
      <label>
        Роль проверяющего
        <input id="reviewer-role" type="text" value="local_human_reviewer" maxlength="64" pattern="[a-z][a-z0-9_-]{2,63}" autocomplete="off">
      </label>
      <h3>Условия одобрения</h3>
      <p>Отметь все пункты только после полного чтения диалога. Без этих отметок одобрение недоступно.</p>
      <ul id="approval-criteria" class="criteria">
        <li><label><input type="checkbox" name="approval-criterion" value="natural_russian"> Естественный русский язык.</label></li>
        <li><label><input type="checkbox" name="approval-criterion" value="coherent_follow_up"> Связный follow-up без потери контекста.</label></li>
        <li><label><input type="checkbox" name="approval-criterion" value="relevant_response"> Уместный и полезный ответ.</label></li>
        <li><label><input type="checkbox" name="approval-criterion" value="no_reviewer_detected_risk"> Нет замеченного риска, лишней ссылки или небезопасного совета.</label></li>
      </ul>
      <h3>Причины отклонения</h3>
      <div id="rejection-codes" class="rejection-grid"></div>
      <div class="actions">
        <button id="approve-button" class="approve" type="button">Одобрить по всем условиям</button>
        <button id="reject-button" class="reject" type="button">Отклонить с выбранными причинами</button>
        <button id="skip-button" type="button">Пропустить без записи</button>
      </div>
      <div id="status-message" class="status" role="status"></div>
    </section>
  </main>
  <script>
    "use strict";

    const approvalCodes = ["natural_russian", "coherent_follow_up", "relevant_response", "no_reviewer_detected_risk"];
    const attentionLabels = {
      assistant_over_700_chars: "длинная реплика ассистента",
      short_user_clarification_after_answer: "короткое уточнение пользователя после ответа",
      contains_url: "ссылка в диалоге",
      contains_email: "похожий на email фрагмент",
      contains_phone_like_sequence: "похожая на телефон последовательность",
      multiline_turn: "многострочная реплика",
      repeated_assistant_turn_within_dialogue: "повтор реплики ассистента"
    };
    const rejectionLabels = {
      bad_russian: "Неестественный или плохой русский",
      incoherent_follow_up: "Нарушен follow-up или контекст",
      irrelevant_response: "Ответ не относится к вопросу",
      template_like: "Шаблонный или повторяющийся ответ",
      unsupported_claim: "Неподтверждённое утверждение",
      unwanted_link_or_promotion: "Лишняя ссылка или продвижение",
      personal_data_risk: "Риск личных данных",
      unsafe_or_unhelpful_advice: "Небезопасный или бесполезный совет",
      legacy_identity_or_capability: "Устаревшая identity или capability",
      not_target_semantics: "Не подходит целевой семантике",
      other_quality_failure: "Другая проблема качества"
    };
    let currentCandidateId = null;
    let busy = false;

    const byId = (id) => document.getElementById(id);

    function setMessage(message, isError = false) {
      const element = byId("status-message");
      element.textContent = message;
      element.className = isError ? "status error" : "status";
    }

    function setBusy(value) {
      busy = value;
      updateDecisionAvailability();
    }

    function selectedApprovalCodes() {
      return Array.from(document.querySelectorAll('input[name="approval-criterion"]:checked')).map((input) => input.value);
    }

    function clearApprovalCodes() {
      document.querySelectorAll('input[name="approval-criterion"]').forEach((input) => {
        input.checked = false;
      });
    }

    function approvalIsConfirmed() {
      const selected = new Set(selectedApprovalCodes());
      return approvalCodes.length === selected.size && approvalCodes.every((code) => selected.has(code));
    }

    function updateDecisionAvailability() {
      const hasCandidate = currentCandidateId !== null;
      byId("approve-button").disabled = busy || !hasCandidate || !approvalIsConfirmed();
      byId("reject-button").disabled = busy || !hasCandidate;
      byId("skip-button").disabled = busy || !hasCandidate;
    }

    function renderRejectionCodes() {
      const root = byId("rejection-codes");
      Object.entries(rejectionLabels).forEach(([code, label]) => {
        const wrapper = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = code;
        checkbox.name = "rejection-code";
        wrapper.append(checkbox, document.createTextNode(" " + label));
        root.append(wrapper);
      });
    }

    function clearRejectionCodes() {
      document.querySelectorAll('input[name="rejection-code"]').forEach((input) => {
        input.checked = false;
      });
    }

    function selectedRejectionCodes() {
      return Array.from(document.querySelectorAll('input[name="rejection-code"]:checked')).map((input) => input.value);
    }

    async function readResponse(response) {
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || "Локальная проверка вернула ошибку.");
      }
      return payload;
    }

    function renderStatus(snapshot) {
      byId("reviewed-count").textContent = snapshot.reviewed_records;
      byId("approved-count").textContent = snapshot.approved_records;
      byId("rejected-count").textContent = snapshot.rejected_records;
      byId("remaining-count").textContent = snapshot.unreviewed_records;
    }

    async function loadStatus() {
      const response = await fetch("/api/status", { cache: "no-store" });
      renderStatus(await readResponse(response));
    }

    function renderCandidate(candidate) {
      const title = byId("candidate-title");
      const metadata = byId("candidate-meta");
      const dialogue = byId("dialogue");
      metadata.replaceChildren();
      dialogue.replaceChildren();
      if (!candidate) {
        currentCandidateId = null;
        title.textContent = "Все кандидаты имеют решение";
        const empty = document.createElement("p");
        empty.className = "empty";
        empty.textContent = "Очередь завершена. Это не запускает обучение: дальше потребуется отдельный audit и quality gate.";
        dialogue.append(empty);
        byId("decision-panel").classList.add("hidden");
        setBusy(false);
        return;
      }
      currentCandidateId = candidate.id;
      title.textContent = "Кандидат для ручной проверки";
      byId("decision-panel").classList.remove("hidden");
      ["ID: " + candidate.id, "Split: " + candidate.split, "Ходов: " + candidate.turn_count]
        .concat(candidate.candidate_topic_labels)
        .forEach((value) => {
          const tag = document.createElement("span");
          tag.className = "tag";
          tag.textContent = value;
          metadata.append(tag);
        });
      if (candidate.review_attention_codes.length > 0) {
        const attention = document.createElement("p");
        attention.className = "attention";
        const labels = candidate.review_attention_codes.map((code) => attentionLabels[code] || code);
        attention.textContent = "Технические сигналы для внимательного чтения, не verdict: " + labels.join("; ") + ".";
        dialogue.append(attention);
      }
      candidate.turns.forEach((turn) => {
        const box = document.createElement("article");
        box.className = "turn " + (turn.role === "user" ? "turn-user" : "turn-assistant");
        const role = document.createElement("span");
        role.className = "turn-role";
        role.textContent = turn.role === "user" ? "Пользователь" : "Ассистент";
        const text = document.createElement("span");
        text.textContent = turn.text;
        box.append(role, text);
        dialogue.append(box);
      });
      clearRejectionCodes();
      clearApprovalCodes();
      setBusy(false);
    }

    async function loadCandidate(afterCandidateId = null) {
      setBusy(true);
      const suffix = afterCandidateId ? "?after=" + encodeURIComponent(afterCandidateId) : "";
      try {
        const response = await fetch("/api/next" + suffix, { cache: "no-store" });
        const payload = await readResponse(response);
        renderCandidate(payload.candidate);
        setMessage(payload.candidate ? "Проверь диалог и выбери решение." : "Очередь не содержит нерешённых кандидатов.");
      } catch (error) {
        currentCandidateId = null;
        setBusy(false);
        setMessage(error.message, true);
      }
    }

    async function submitDecision(decision, reasonCodes, approvalConfirmed = false) {
      if (!currentCandidateId || busy) return;
      const reviewerRole = byId("reviewer-role").value.trim();
      if (!/^[a-z][a-z0-9_-]{2,63}$/.test(reviewerRole)) {
        setMessage("Роль должна состоять из латинских букв, цифр, _ или - и начинаться с буквы.", true);
        return;
      }
      if (reasonCodes.length === 0) {
        setMessage("Для отклонения выбери хотя бы одну причину.", true);
        return;
      }
      if (decision === "approved" && !approvalConfirmed) {
        setMessage("Перед одобрением подтверди все четыре условия после полного чтения диалога.", true);
        return;
      }
      const reviewedId = currentCandidateId;
      setBusy(true);
      try {
        const response = await fetch("/api/decisions", {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            candidate_id: reviewedId,
            decision: decision,
            reason_codes: reasonCodes,
            reviewer_role: reviewerRole,
            approval_confirmed: approvalConfirmed
          })
        });
        const payload = await readResponse(response);
        renderStatus(payload.status);
        setMessage("Решение сохранено без текста диалога.");
        await loadCandidate(reviewedId);
      } catch (error) {
        setBusy(false);
        setMessage(error.message, true);
      }
    }

    byId("approve-button").addEventListener("click", () => submitDecision("approved", approvalCodes, approvalIsConfirmed()));
    byId("reject-button").addEventListener("click", () => submitDecision("rejected", selectedRejectionCodes()));
    byId("skip-button").addEventListener("click", () => loadCandidate(currentCandidateId));
    document.querySelectorAll('input[name="approval-criterion"]').forEach((input) => {
      input.addEventListener("change", updateDecisionAvailability);
    });

    renderRejectionCodes();
    Promise.all([loadStatus(), loadCandidate()]).catch((error) => setMessage(error.message, true));
  </script>
</body>
</html>
"""


def create_review_app(paths: ReviewPaths = DEFAULT_REVIEW_PATHS) -> FastAPI:
    """Создаёт отдельное loopback-only приложение, не затрагивая chat API."""

    app = FastAPI(
        title="OASST1 local human review",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(OASST1HumanReviewError)
    async def review_error_handler(_: Request, error: OASST1HumanReviewError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": f"Локальная проверка остановлена: {error}"},
            headers=NO_STORE_HEADERS,
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def review_page(request: Request) -> HTMLResponse:
        _require_loopback_request(request)
        return HTMLResponse(REVIEW_PAGE, headers=NO_STORE_HEADERS)

    @app.get("/health", include_in_schema=False)
    async def health(request: Request) -> JSONResponse:
        _require_loopback_request(request)
        snapshot = build_review_status(paths)
        return JSONResponse(
            {
                "status": "ok",
                "loopback_only": True,
                "queue_selected_records": snapshot["queue_selected_records"],
                "unreviewed_records": snapshot["unreviewed_records"],
                "training_candidate_allowed": False,
            },
            headers=NO_STORE_HEADERS,
        )

    @app.get("/api/status", include_in_schema=False)
    async def review_status(request: Request) -> JSONResponse:
        _require_loopback_request(request)
        return JSONResponse(build_review_status(paths), headers=NO_STORE_HEADERS)

    @app.get("/api/next", include_in_schema=False)
    async def next_candidate(
        request: Request,
        after: str | None = None,
    ) -> JSONResponse:
        _require_loopback_request(request)
        return JSONResponse(
            {"candidate": load_next_candidate(paths, after_candidate_id=after)},
            headers=NO_STORE_HEADERS,
        )

    @app.post("/api/decisions", include_in_schema=False, status_code=status.HTTP_201_CREATED)
    async def submit_decision(
        request: Request,
        payload: ReviewDecisionRequest,
    ) -> JSONResponse:
        _require_loopback_request(request)
        return JSONResponse(
            record_human_decision(payload, paths),
            status_code=status.HTTP_201_CREATED,
            headers=NO_STORE_HEADERS,
        )

    return app


def _parser() -> argparse.ArgumentParser:
    """Описывает CLI запуска отдельного локального review-сервиса."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST, choices=("127.0.0.1", "::1"))
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    return parser


def main() -> None:
    """Запускает review только на loopback и не открывает внешний порт."""

    args = _parser().parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("Порт локальной проверки должен быть от 1 до 65535.")
    uvicorn.run(
        create_review_app(),
        host=args.host,
        port=args.port,
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
