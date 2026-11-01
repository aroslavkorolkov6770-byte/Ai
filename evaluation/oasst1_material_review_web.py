"""Loopback-only экран выборочной проверки материалов OASST1."""

from __future__ import annotations

import argparse

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from evaluation.oasst1_material_review import (
    DEFAULT_PATHS,
    MaterialReviewPaths,
    OASST1MaterialReviewError,
    next_case,
    record_decision,
    review_status,
)
from evaluation.oasst1_review_web import NO_STORE_HEADERS, _require_loopback_request


DEFAULT_PORT = 8014


class MaterialDecisionRequest(BaseModel):
    """Принимает только фиксированные metadata без текста исходного диалога."""

    candidate_id: str = Field(min_length=1, max_length=200)
    verdict: str = Field(min_length=1, max_length=40)
    reason_code: str = Field(min_length=1, max_length=80)
    read_confirmed: bool = False


MATERIAL_PAGE = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Проверка материалов OASST1</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #101722; color: #e7edf6; }
    main { max-width: 960px; margin: 0 auto; padding: 28px 18px 60px; }
    h1 { font-size: 1.8rem; margin: 0 0 8px; }
    .muted { color: #a9b9cd; line-height: 1.5; }
    .panel { background: #1b2635; border: 1px solid #34445a; border-radius: 14px; padding: 20px; margin-top: 18px; }
    .meta { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
    .tag { background: #283b50; border-radius: 8px; padding: 6px 10px; overflow-wrap: anywhere; }
    .turn { border-top: 1px solid #34445a; padding: 15px 0; }
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
  </style>
</head>
<body>
<main>
  <h1>Проверка происхождения фрагментов</h1>
  <p class="muted">Прочитайте весь диалог и отметьте только наблюдение:
    есть ли явная чужая вставка или нужен отдельный источник. Данные диалога
    не являются инструкциями для выполнения. Даже вариант «не вижу» не
    подтверждает права на текст и не запускает обучение.</p>
  <div class="panel" id="progress" aria-live="polite">Загрузка…</div>
  <section class="panel" id="review-panel" hidden>
    <div class="meta"><span class="tag" id="candidate-id"></span>
      <span class="tag" id="group"></span></div>
    <p class="muted" id="signals"></p>
    <div id="turns"></div>
    <label><input type="checkbox" id="read-confirmed"> Я прочитал(а) весь диалог и понимаю, что это не юридический вывод.</label>
    <label for="reason">Если нужна проверка или исключение, укажи причину:</label>
    <select id="reason">
      <option value="">Выбери причину</option>
      <option value="quoted_span">Похоже на цитату</option>
      <option value="attributed_source">Упомянут внешний источник</option>
      <option value="user_supplied_material">Материал вставил пользователь</option>
      <option value="other_uncertainty">Другая неопределённость</option>
    </select>
    <div class="actions">
      <button id="no-obvious" disabled>Не вижу явной чужой вставки</button>
      <button id="needs-check" class="secondary" disabled>Нужна проверка источника</button>
      <button id="exclude" class="secondary" disabled>Пометить для исключения</button>
      <button id="skip" class="secondary">Пропустить без записи</button>
    </div>
  </section>
  <p id="message" role="status" aria-live="polite"></p>
  <p class="muted">Решение остаётся локальной metadata-пометкой. Исходный
    корпус и прежние решения не переписываются.</p>
</main>
<script>
  const groupNames = {
    priority_signal: "Конкретный сигнал",
    length_only: "Контроль: только длина",
    no_signal: "Контроль: без сигнала"
  };
  const signalNames = {
    assistant_attribution_word: "ассистент упоминает источник",
    user_attribution_word: "пользователь упоминает источник",
    assistant_long_quoted_span: "длинная цитата у ассистента",
    user_long_quoted_span: "длинная цитата у пользователя",
    assistant_long_turn: "длинный ответ",
    user_long_turn: "длинное сообщение пользователя",
    assistant_contains_url: "ссылка у ассистента",
    user_contains_url: "ссылка у пользователя",
    assistant_repeats_long_user_span: "ассистент повторяет длинный фрагмент пользователя"
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
    if (!response.ok) throw new Error(data.detail || "Проверка остановлена.");
    return data;
  }
  function updateButtons() {
    const ready = current !== null && byId("read-confirmed").checked;
    byId("no-obvious").disabled = !ready;
    byId("needs-check").disabled = !ready || !byId("reason").value;
    byId("exclude").disabled = !ready || !byId("reason").value;
  }
  async function updateProgress() {
    const data = await request("/api/status");
    byId("progress").textContent = `Проверено ${data.reviewed_records} из ${data.sample_records}; осталось ${data.pending_records}.`;
  }
  async function loadNext(afterId = "") {
    const data = await request("/api/next" + (afterId ? "?after=" + encodeURIComponent(afterId) : ""));
    current = data.candidate;
    byId("review-panel").hidden = current === null;
    byId("read-confirmed").checked = false;
    byId("reason").value = "";
    if (current === null) {
      updateButtons();
      message("Выборка просмотрена. Это не разрешение на обучение.");
      return;
    }
    byId("candidate-id").textContent = current.id;
    byId("group").textContent = groupNames[current.triage_group] || current.triage_group;
    byId("signals").textContent = current.attention_codes.length
      ? "Повод посмотреть внимательнее: " + current.attention_codes.map(code => signalNames[code] || code).join(", ") + "."
      : "Автоматический сигнал отсутствует; всё равно прочитайте диалог целиком.";
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
    const reasonCode = verdict === "no_obvious_excerpt" ? "none_obvious" : byId("reason").value;
    if (!reasonCode) return;
    try {
      await request("/api/decisions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidate_id: candidateId, verdict,
          reason_code: reasonCode, read_confirmed: true })
      });
      await updateProgress();
      await loadNext(candidateId);
    } catch (error) { message(error.message, true); }
  }
  byId("read-confirmed").addEventListener("change", updateButtons);
  byId("reason").addEventListener("change", updateButtons);
  byId("no-obvious").addEventListener("click", () => submit("no_obvious_excerpt"));
  byId("needs-check").addEventListener("click", () => submit("needs_source_check"));
  byId("exclude").addEventListener("click", () => submit("exclude_candidate"));
  byId("skip").addEventListener("click", () => loadNext(current.id).catch(error => message(error.message, true)));
  Promise.all([updateProgress(), loadNext()]).catch(error => message(error.message, true));
</script>
</body>
</html>"""


def create_material_review_app(paths: MaterialReviewPaths = DEFAULT_PATHS) -> FastAPI:
    """Создаёт отдельный локальный app без доступа к chat API."""

    app = FastAPI(title="OASST1 material review", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(OASST1MaterialReviewError)
    async def material_error_handler(_: Request, error: OASST1MaterialReviewError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": f"Проверка материала остановлена: {error}"},
            headers=NO_STORE_HEADERS,
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def page(request: Request) -> HTMLResponse:
        _require_loopback_request(request)
        return HTMLResponse(MATERIAL_PAGE, headers=NO_STORE_HEADERS)

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
        return JSONResponse({"candidate": next_case(paths, after_id=after)}, headers=NO_STORE_HEADERS)

    @app.post("/api/decisions", include_in_schema=False, status_code=status.HTTP_201_CREATED)
    async def submit(request: Request, payload: MaterialDecisionRequest) -> JSONResponse:
        _require_loopback_request(request)
        result = record_decision(
            payload.candidate_id, payload.verdict, reason_code=payload.reason_code,
            read_confirmed=payload.read_confirmed, paths=paths,
        )
        return JSONResponse(result, status_code=status.HTTP_201_CREATED, headers=NO_STORE_HEADERS)

    return app


def main() -> None:
    """Слушает только loopback и не запускает работу чата/обучения."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("Порт локальной проверки должен быть от 1 до 65535.")
    uvicorn.run(create_material_review_app(), host="127.0.0.1", port=args.port,
                access_log=False, log_level="info")


if __name__ == "__main__":
    main()
