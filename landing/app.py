"""Мини-сервис лендинга: отдаёт страницу и пересылает заявки в Telegram.

Отдельный от поискового бота процесс. Токен и chat_id берутся только из
переменных окружения — в коде и в отдаваемом HTML их нет.
"""

from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, field_validator

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
LEAD_CHAT_ID = os.environ.get("LEAD_CHAT_ID", "")
# опционально: если лендинг когда-нибудь переедет на другой домен, а API останется здесь
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "")

STATIC = Path(__file__).parent / "static"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

app = FastAPI(title="Выкуп ООО — лендинг", docs_url=None, redoc_url=None)

# нестрогий лимит: не больше 5 заявок с одного IP за 10 минут
_WINDOW = 600
_LIMIT = 5
_hits: dict[str, deque[float]] = {}


def _rate_ok(ip: str) -> bool:
    now = time.time()
    q = _hits.setdefault(ip, deque())
    while q and now - q[0] > _WINDOW:
        q.popleft()
    if len(q) >= _LIMIT:
        return False
    q.append(now)
    return True


class Lead(BaseModel):
    inn: str = ""
    region: str = ""
    sno: str = ""
    oborot: str = ""
    kontakt: str = ""
    website: str = ""  # honeypot: у живого человека всегда пусто

    @field_validator("inn", "region", "sno", "oborot", "kontakt", "website")
    @classmethod
    def _cap(cls, v: str) -> str:
        # обрезаем длину, чтобы через форму нельзя было прислать «простыню»
        return (v or "").strip()[:200]


def _compose(lead: Lead) -> str:
    inn = lead.inn or "— уточнят"
    kontakt = lead.kontakt or "не указан"
    return (
        "🆕 Заявка с сайта «Выкуп ООО»\n\n"
        f"ИНН: {inn}\n"
        f"Регион: {lead.region or '—'}\n"
        f"Налогообложение: {lead.sno or '—'}\n"
        f"Обороты: {lead.oborot or '—'}\n"
        f"Контакт: {kontakt}"
    )


def _cors_headers() -> dict[str, str]:
    if not ALLOWED_ORIGIN:
        return {}
    return {
        "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
async def health() -> dict[str, bool]:
    # Railway дергает этот путь, чтобы понять, что сервис жив
    return {"ok": True, "configured": bool(BOT_TOKEN and LEAD_CHAT_ID)}


@app.options("/api/lead")
async def lead_options() -> JSONResponse:
    return JSONResponse({}, headers=_cors_headers())


@app.post("/api/lead")
async def lead(payload: Lead, request: Request) -> JSONResponse:
    headers = _cors_headers()

    # спам-бот заполнил скрытое поле — молча отвечаем «ок», ничего не пересылаем
    if payload.website:
        return JSONResponse({"ok": True}, headers=headers)

    ip = (request.headers.get("x-forwarded-for", "") or (request.client.host if request.client else "")).split(",")[0].strip()
    if not _rate_ok(ip):
        return JSONResponse({"ok": False, "error": "too_many"}, status_code=429, headers=headers)

    if not (BOT_TOKEN and LEAD_CHAT_ID):
        # сервис не настроен — честно говорим об этом, фронт уведёт человека в Telegram
        return JSONResponse({"ok": False, "error": "not_configured"}, status_code=503, headers=headers)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                TELEGRAM_API,
                json={
                    "chat_id": LEAD_CHAT_ID,
                    "text": _compose(payload),
                    "disable_web_page_preview": True,
                },
            )
        if resp.status_code != 200:
            return JSONResponse({"ok": False, "error": "telegram"}, status_code=502, headers=headers)
    except httpx.HTTPError:
        return JSONResponse({"ok": False, "error": "network"}, status_code=502, headers=headers)

    return JSONResponse({"ok": True}, headers=headers)
