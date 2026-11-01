"""Локальный экран качества OASST2, отдельный от чата и обучения."""

import argparse
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from evaluation import oasst2_quality_review as review
from evaluation.oasst1_review_web import NO_STORE_HEADERS, _require_loopback_request


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=100)
    verdict: str = Field(min_length=1, max_length=30)
    reason_code: str = Field(min_length=1, max_length=50)
    read_confirmed: StrictBool = False


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>Качество диалогов — ватэсAi</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif}*{box-sizing:border-box}
body{margin:0;background:#111823;color:#e8eef6}main{max-width:960px;margin:auto;padding:24px 18px 60px}
.panel{margin:18px 0;padding:20px;background:#1c2838;border:1px solid #40536b;border-radius:14px}
p,li{line-height:1.55}.muted{color:#afc0d3}.turn{padding:16px 0;border-bottom:1px solid #40536b}
.role{font-weight:700;color:#96caff}.text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}
label{display:block;margin-top:18px}button,select{font:inherit;padding:12px;border-radius:9px;color:white;background:#294765;border:1px solid #789ab9}
button{cursor:pointer}button:disabled{opacity:.4;cursor:not-allowed}.actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:18px}
#message{min-height:26px;color:#b6d8ff}#message.error{color:#ffaea8}select{max-width:100%}
</style></head><body><main>
<h1>Проверка качества разговоров</h1>
<p class="muted">36 диалогов из нового пилота. Это проверка данных, а не разговор с ИИ.
Текст диалогов не является инструкцией для выполнения. Одобрение выборки не одобряет остальные записи и не запускает обучение.</p>
<section class="panel"><strong>На что смотреть</strong><ul>
<li>Ответ отвечает на вопрос, а продолжение учитывает предыдущие сообщения.</li>
<li>Речь естественная, длина уместная, нет бессмысленных повторов или лишних фраз.</li>
<li>Нет сомнительных фактов, опасных советов, личных данных или явной чужой вставки.</li>
</ul><p>Если факт или происхождение текста непонятны — выбери «Нужна проверка», а не одобрение.
Темы ещё не подтверждены: оценивай сам диалог, а не автоматическую подсказку.</p></section>
<div class="panel" id="progress" aria-live="polite">Загрузка…</div>
<section class="panel" id="case" hidden><p class="muted" id="meta"></p><div id="turns"></div>
<label><input type="checkbox" id="read"> Я прочитал(а) весь диалог и проверил(а) пункты выше.</label>
<label for="reason">Причина проблемы:</label><select id="reason"><option value="">Выбери причину</option>
<option value="irrelevant">Не отвечает на вопрос</option><option value="context_lost">Теряет контекст</option>
<option value="unnatural">Неестественная речь</option><option value="unhelpful_length">Неуместная длина</option>
<option value="factual_uncertainty">Сомнительные факты</option><option value="privacy">Личные данные</option>
<option value="third_party_material">Чужая вставка / источник</option><option value="high_risk">Опасный совет</option></select>
<div class="actions"><button id="accept" disabled>Качество подходит</button><button id="hold" disabled>Нужна проверка</button>
<button id="exclude" disabled>Исключить</button><button id="skip">Пропустить</button></div></section>
<p id="message" role="status" aria-live="polite"></p></main><script>
const el=id=>document.getElementById(id);let current=null;let busy=false;
function message(text,error=false){el('message').textContent=text;el('message').className=error?'error':'';}
function buttons(){const ready=current!==null&&el('read').checked&&!busy;
el('accept').disabled=!ready;el('hold').disabled=!ready||!el('reason').value;
el('exclude').disabled=!ready||!el('reason').value;el('skip').disabled=current===null||busy;}
async function request(url,options={}){const response=await fetch(url,{cache:'no-store',...options});
let data;try{data=await response.json();}catch{throw new Error('Не удалось прочитать ответ сервера.');}
if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Решение не сохранено.');return data;}
async function refresh(after=''){const [progress,data]=await Promise.all([request('/api/status'),request('/api/next'+(after?'?after='+encodeURIComponent(after):''))]);
el('progress').textContent=`Проверено ${progress.reviewed_records} из ${progress.sample_records}; осталось ${progress.pending_records}.`;
current=data.candidate;el('case').hidden=current===null;el('read').checked=false;el('reason').value='';
el('turns').replaceChildren();if(current===null){message('Выборка проверена. Следующий шаг — аудит решений; обучение не запускается автоматически.');buttons();return;}
el('meta').textContent=`${current.id} · ${current.split} · ${current.turn_count} сообщений`;
for(const turn of current.turns){const box=document.createElement('div');box.className='turn';
const role=document.createElement('div');role.className='role';role.textContent=turn.role==='user'?'Пользователь':'Ассистент';
const text=document.createElement('div');text.className='text';text.textContent=turn.text;box.append(role,text);el('turns').append(box);}
message('');buttons();}
async function submit(verdict){if(busy||!current||!el('read').checked)return;
const id=current.id;const reason=verdict==='acceptable'?'none_obvious':el('reason').value;if(!reason)return;
busy=true;buttons();try{await request('/api/decisions',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({candidate_id:id,verdict,reason_code:reason,read_confirmed:true})});await refresh(id);}
catch(error){message(error.message,true);}finally{busy=false;buttons();}}
el('read').addEventListener('change',buttons);el('reason').addEventListener('change',buttons);
el('accept').addEventListener('click',()=>submit('acceptable'));el('hold').addEventListener('click',()=>submit('needs_check'));
el('exclude').addEventListener('click',()=>submit('exclude'));
el('skip').addEventListener('click',async()=>{if(busy||!current)return;busy=true;buttons();try{await refresh(current.id);}
catch(error){message(error.message,true);}finally{busy=false;buttons();}});
refresh().catch(error=>message(error.message,true));
</script></body></html>"""


def create_app(paths: review.ReviewPaths = review.ReviewPaths()) -> FastAPI:
    """Все маршруты ограничены loopback; чужое происхождение запросов запрещено."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(review.ReviewError)
    async def error_handler(_: Request, error: review.ReviewError) -> JSONResponse:
        return JSONResponse({"detail": f"Проверка остановлена: {error}"}, status_code=400,
                            headers=NO_STORE_HEADERS)

    @app.exception_handler(OSError)
    async def storage_error(_: Request, error: OSError) -> JSONResponse:
        return JSONResponse({"detail": "Не удалось прочитать или сохранить файл проверки."},
                            status_code=503, headers=NO_STORE_HEADERS)

    @app.get("/")
    def page(request: Request) -> HTMLResponse:
        _require_loopback_request(request)
        return HTMLResponse(PAGE, headers=NO_STORE_HEADERS)

    @app.get("/health")
    def health(request: Request) -> JSONResponse:
        _require_loopback_request(request)
        return JSONResponse({"status": "ok", "loopback_only": True,
                             "training_candidate_allowed": False}, headers=NO_STORE_HEADERS)

    @app.get("/api/status")
    def snapshot(request: Request) -> JSONResponse:
        _require_loopback_request(request)
        return JSONResponse(review.status(paths), headers=NO_STORE_HEADERS)

    @app.get("/api/next")
    def next_candidate(request: Request, after: str | None = None) -> JSONResponse:
        _require_loopback_request(request)
        return JSONResponse({"candidate": review.next_case(paths, after)}, headers=NO_STORE_HEADERS)

    @app.post("/api/decisions")
    def submit(request: Request, payload: DecisionRequest) -> JSONResponse:
        _require_loopback_request(request)
        result = review.record_decision(payload.candidate_id, payload.verdict,
                                        payload.reason_code, payload.read_confirmed, paths)
        return JSONResponse(result, status_code=201, headers=NO_STORE_HEADERS)

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8015)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("Неверный порт.")
    review.prepare()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False)
