"""Telegram Bot API: отправка карточек лотов и приём команд (long polling)."""
from __future__ import annotations

import asyncio
import html
import logging
import os
from typing import Optional

import httpx

from . import regions
from .models import Lot

log = logging.getLogger(__name__)

SOURCE_TITLES = {
    "eis": "ЕИС", "torgi": "torgi.gov.ru", "fedresurs": "Федресурс",
    "b2b": "B2B-Center", "fabrikant": "Фабрикант",
}


def _e(s) -> str:
    return html.escape(str(s or ""), quote=False)


def fmt_price(p: Optional[float]) -> str:
    if p is None:
        return "цена не указана"
    return f"{p:,.0f}".replace(",", " ") + " ₽"


def format_lot(lot: Lot) -> str:
    region = regions.name(lot.region_code) if lot.region_code else (lot.region_hint or "регион не определён")
    lines = [f"🚜 <b>{_e(lot.title[:300])}</b>", ""]
    lines.append(f"📍 {_e(region)}")
    lines.append(f"💰 {fmt_price(lot.price)}")
    if lot.deadline:
        lines.append(f"⏰ Заявки до: {_e(lot.deadline)}")
    if lot.customer:
        lines.append(f"🏢 {_e(lot.customer[:200])}")
    lines.append(f"🏷 {_e(SOURCE_TITLES.get(lot.source, lot.source))} · {_e(lot.kind[:150])}")
    if lot.published:
        lines.append(f"🗓 Опубликовано: {_e(lot.published)}")
    if lot.matched:
        lines.append(f"🔎 {_e(', '.join(lot.matched))}")
    return "\n".join(lines)


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = str(chat_id or "")
        base = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")
        self.base = f"{base}/bot{token}"
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(70.0, connect=15.0),
            proxy=os.environ.get("TELEGRAM_PROXY") or None,
            trust_env=False,
        )
        self._send_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def call(self, method: str, **payload) -> dict:
        for attempt in range(5):
            try:
                r = await self.client.post(f"{self.base}/{method}", json=payload)
                data = r.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("Telegram %s: %s", method, e)
                await asyncio.sleep(5 * (attempt + 1))
                continue
            if data.get("ok"):
                return data
            retry = (data.get("parameters") or {}).get("retry_after")
            if retry:
                await asyncio.sleep(int(retry) + 1)
                continue
            log.error("Telegram %s отказал: %s", method, data.get("description"))
            return data
        return {"ok": False}

    async def send(self, text: str, chat_id: Optional[str] = None, url: Optional[str] = None) -> bool:
        payload = {
            "chat_id": chat_id or self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if url:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": "Открыть на площадке", "url": url}]]}
        async with self._send_lock:
            res = await self.call("sendMessage", **payload)
            await asyncio.sleep(1.1)   # лимит Telegram — ~1 сообщение в секунду в один чат
        return bool(res.get("ok"))

    async def send_lot(self, lot: Lot) -> bool:
        return await self.send(format_lot(lot), url=lot.url)

    async def updates(self, offset: int) -> list:
        res = await self.call("getUpdates", offset=offset, timeout=50, allowed_updates=["message"])
        return res.get("result") or []

    async def close(self):
        await self.client.aclose()
