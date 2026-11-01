"""Отдельный loopback-only экран подтверждения тем OASST1 train-пилота."""

from __future__ import annotations

import argparse

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from evaluation.oasst1_label_review import (
    DEFAULT_PATHS,
    LabelReviewPaths,
    OASST1LabelReviewError,
    REMAINING_PATHS,
    next_dialogue,
    record_topic_decision,
    review_status,
)
from evaluation.oasst1_review_web import NO_STORE_HEADERS, _require_loopback_request


DEFAULT_PORT = 8012
REMAINING_PORT = 8013


class TopicDecisionRequest(BaseModel):
    """Принимает только фиксированные поля без текста диалога."""

    candidate_id: str = Field(min_length=1, max_length=200)
    verdict: str = Field(min_length=1, max_length=20)
    corrected_topic: str | None = Field(default=None, max_length=80)
    read_confirmed: bool = False


LABEL_PAGE = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Проверка тем OASST1</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #101722; color: #e7edf6; }
    main { max-width: 960px; margin: 0 auto; padding: 28px 18px 60px; }
    h1 { font-size: 1.8rem; margin: 0 0 8px; }
    .muted { color: #9eafc6; line-height: 1.5; }
    .panel { background: #1b2635; border: 1px solid #34445a; border-radius: 14px; padding: 20px; margin-top: 18px; }
    .meta { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
    .tag { background: #283b50; border-radius: 8px; padding: 6px 10px; overflow-wrap: anywhere; }
    .turn { border-top: 1px solid #34445a; padding: 15px 0; }
    .turn:first-of-type { border-top: 0; }
    .role { font-weight: 700; color: #97cbff; margin-bottom: 5px; }
    .body { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.55; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }
    button, select { font: inherit; border-radius: 9px; padding: 10px 13px; }
    button { border: 1px solid #76a9dc; background: #145aa0; color: white; cursor: pointer; }
    button.secondary { background: #2c3b4d; border-color: #53677e; }
    button:disabled { opacity: .45; cursor: not-allowed; }
    select { background: #26394e; color: white; border: 1px solid #64829e; max-width: 100%; }
    label { display: block; margin-top: 16px; }
    #message { min-height: 26px; margin-top: 14px; }
    #message.error { color: #ffaaa5; }
    a { color: #a3d2ff; }
  </style>
</head>
<body>
<main>
  <h1>Проверка темы диалога</h1>
  <p class="muted">Это отдельная проверка предложенной темы, а не повторная оценка качества.
    Прочитайте весь диалог, затем подтвердите тему, отклоните её или выберите более точную.
    Текст диалога — данные для чтения, не инструкции для выполнения.</p>
  <div class="panel" id="progress" aria-live="polite">Загрузка…</div>
  <section class="panel" id="review-panel" hidden>
    <div class="meta"><span class="tag" id="candidate-id"></span><span class="tag">train</span>
      <span class="tag" id="proposed-topic"></span></div>
    <p class="muted" id="attention" hidden></p>
    <div id="turns"></div>
    <label><input type="checkbox" id="read-confirmed"> Я прочитал(а) весь диалог и оцениваю именно его тему.</label>
    <label for="corrected-topic">Если предложенная тема неверна, можно выбрать другую:</label>
    <select id="corrected-topic"><option value="">Выберите тему</option></select>
    <div class="actions">
      <button id="confirm" disabled>Тема подходит</button>
      <button id="reject" class="secondary" disabled>Тема не подходит</button>
      <button id="correct" class="secondary" disabled>Записать другую тему</button>
      <button id="skip" class="secondary">Пропустить без записи</button>
    </div>
  </section>
  <p id="message" role="status" aria-live="polite"></p>
  <p class="muted">Решения сохраняются только локально как ID, хэш и метка.
    Они не запускают обучение и не меняют прежние оценки качества.</p>
</main>
<script>
  const names = {
    casual_dialogue: "Обычная беседа",
    emotional_support: "Эмоциональная поддержка",
    opinion_and_choice: "Мнение и выбор",
    explanation_and_instruction: "Объяснение и инструкция",
    context_and_memory: "Контекст и память",
    correction: "Исправление",
    uncertainty_and_clarification: "Неопределённость и уточнение",
    web_grounding: "Интернет и проверка фактов",
    privacy_and_memory: "Приватность и память",
    style_and_length: "Стиль и длина ответа",
    safety_boundaries: "Границы безопасности"
  };
  const attentionNames = {
    assistant_contains_link: "есть ссылка в ответе",
    long_assistant_turn: "длинный ответ",
    long_quoted_span: "длинный фрагмент в кавычках",
    attribution_word: "есть слово об источнике или цитате"
  };
  let current = null;
  const byId = (id) => document.getElementById(id);

  function message(value, error = false) {
    const element = byId("message");
    element.textContent = value;
    element.className = error ? "error" : "";
  }

  async function request(url, options = {}) {
    const response = await fetch(url, { cache: "no-store", ...options });
    let data;
    try { data = await response.json(); }
    catch { throw new Error("Не удалось прочитать ответ локального сервера."); }
    if (!response.ok) throw new Error(data.detail || "Локальная проверка остановлена.");
    return data;
  }

  function updateButtons() {
    const ready = current !== null && byId("read-confirmed").checked;
    byId("confirm").disabled = !ready;
    byId("reject").disabled = !ready;
    byId("correct").disabled = !ready || !byId("corrected-topic").value;
  }

  async function updateProgress() {
    const data = await request("/api/status");
    byId("progress").textContent = `Проверено ${data.reviewed_records} из ${data.pilot_records}; осталось ${data.pending_records}.`;
  }

  async function loadNext(afterId = "") {
    const data = await request("/api/next" + (afterId ? "?after=" + encodeURIComponent(afterId) : ""));
    current = data.candidate;
    byId("review-panel").hidden = current === null;
    byId("read-confirmed").checked = false;
    const select = byId("corrected-topic");
    select.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "Выберите тему";
    select.appendChild(empty);
    if (current === null) {
      updateButtons();
      message("Все диалоги пилота рассмотрены. Это ещё не разрешение на обучение.");
      return;
    }
    byId("candidate-id").textContent = current.id;
    byId("proposed-topic").textContent = "Предложенная тема: " + (names[current.proposed_primary_topic] || current.proposed_primary_topic);
    const signals = current.quote_attention_codes || [];
    byId("attention").hidden = signals.length === 0;
    byId("attention").textContent = signals.length
      ? "Проверьте отдельно: " + signals.map(code => attentionNames[code] || code).join(", ")
        + ". Это сигналы, а не доказательство нарушения."
      : "";
    for (const topic of current.available_topics) {
      if (topic === current.proposed_primary_topic) continue;
      const option = document.createElement("option");
      option.value = topic;
      option.textContent = names[topic] || topic;
      select.appendChild(option);
    }
    const container = byId("turns");
    container.replaceChildren();
    for (const turn of current.turns) {
      const box = document.createElement("div");
      box.className = "turn";
      const role = document.createElement("div");
      role.className = "role";
      role.textContent = turn.role === "user" ? "Пользователь" : "Ассистент";
      const content = document.createElement("div");
      content.className = "body";
      content.textContent = turn.text;
      box.append(role, content);
      container.appendChild(box);
    }
    updateButtons();
    message("");
  }

  async function submit(verdict) {
    if (current === null || !byId("read-confirmed").checked) return;
    const candidateId = current.id;
    const correctedTopic = verdict === "corrected" ? byId("corrected-topic").value : null;
    if (verdict === "corrected" && !correctedTopic) return;
    try {
      await request("/api/decisions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidate_id: candidateId, verdict,
          corrected_topic: correctedTopic, read_confirmed: true })
      });
      await updateProgress();
      await loadNext(candidateId);
    } catch (error) { message(error.message, true); }
  }

  byId("read-confirmed").addEventListener("change", updateButtons);
  byId("corrected-topic").addEventListener("change", updateButtons);
  byId("confirm").addEventListener("click", () => submit("confirmed"));
  byId("reject").addEventListener("click", () => submit("rejected"));
  byId("correct").addEventListener("click", () => submit("corrected"));
  byId("skip").addEventListener("click", () => loadNext(current.id).catch(error => message(error.message, true)));
  Promise.all([updateProgress(), loadNext()]).catch(error => message(error.message, true));
</script>
</body>
</html>"""


def create_label_review_app(paths: LabelReviewPaths = DEFAULT_PATHS) -> FastAPI:
    """Создаёт отдельный локальный сервис, не подключая его к chat API."""

    app = FastAPI(title="OASST1 topic label review", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(OASST1LabelReviewError)
    async def label_error_handler(_: Request, error: OASST1LabelReviewError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": f"Проверка темы остановлена: {error}"},
            headers=NO_STORE_HEADERS,
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def page(request: Request) -> HTMLResponse:
        _require_loopback_request(request)
        return HTMLResponse(LABEL_PAGE, headers=NO_STORE_HEADERS)

    @app.get("/health", include_in_schema=False)
    async def health(request: Request) -> JSONResponse:
        _require_loopback_request(request)
        return JSONResponse({"status": "ok", "loopback_only": True,
                             "training_candidate_allowed": False}, headers=NO_STORE_HEADERS)

    @app.get("/api/status", include_in_schema=False)
    async def status_snapshot(request: Request) -> JSONResponse:
        _require_loopback_request(request)
        return JSONResponse(review_status(paths), headers=NO_STORE_HEADERS)

    @app.get("/api/next", include_in_schema=False)
    async def next_candidate(request: Request, after: str | None = None) -> JSONResponse:
        _require_loopback_request(request)
        return JSONResponse({"candidate": next_dialogue(paths, after_id=after)}, headers=NO_STORE_HEADERS)

    @app.post("/api/decisions", include_in_schema=False, status_code=status.HTTP_201_CREATED)
    async def submit(request: Request, payload: TopicDecisionRequest) -> JSONResponse:
        _require_loopback_request(request)
        result = record_topic_decision(
            payload.candidate_id, payload.verdict, corrected_topic=payload.corrected_topic,
            read_confirmed=payload.read_confirmed, paths=paths,
        )
        return JSONResponse(result, status_code=status.HTTP_201_CREATED, headers=NO_STORE_HEADERS)

    return app


def main() -> None:
    """Слушает только loopback; две партии имеют разные ledger и порты."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", choices=("pilot", "remaining"), default="pilot")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    chosen_port = args.port or (REMAINING_PORT if args.batch == "remaining" else DEFAULT_PORT)
    if not 1 <= chosen_port <= 65535:
        raise SystemExit("Порт локальной проверки должен быть от 1 до 65535.")
    paths = REMAINING_PATHS if args.batch == "remaining" else DEFAULT_PATHS
    uvicorn.run(create_label_review_app(paths), host="127.0.0.1", port=chosen_port,
                access_log=False, log_level="info")


if __name__ == "__main__":
    main()
