"""Федресурс / ЕФРСБ — торги по банкротству (JSON-бэкенд поиска fedresurs.ru/biddings)."""
from __future__ import annotations

import re
from datetime import date

from .base import Lot, Source, clean

API = "https://fedresurs.ru/backend/biddings"
CARD = "https://fedresurs.ru/biddings/{}"
PAGE = 50          # больше 50 бэкенд не отдаёт
MAX_PAGES = 8


def _date(s: str) -> str:
    return f"{s[8:10]}.{s[5:7]}.{s[0:4]}" if s and len(s) >= 10 else ""


def _subject(item: dict) -> tuple:
    """-> (заголовок, весь текст подсветок)"""
    texts = list(item.get("tradeHighlights") or [])
    for lot in item.get("lotHighlights") or []:
        texts.extend(lot.get("highlights") or [])
    texts = [clean(t.replace("<br/>", "\n")) for t in texts]
    title = ""
    for t in texts:
        m = re.search(r"Предмет торгов:\s*(.+?)(?:\s+(?:Обоснование|Победитель|Порядок|Статус лота|Классификатор)\b|$)", t)
        if m:
            title = m.group(1).strip(" .")
            break
    if not title and texts:
        title = texts[0].strip(" .")
    return title, " ".join(texts)


def parse_page(data: dict, today: str = "") -> list:
    today = today or date.today().isoformat()
    lots = []
    for x in data.get("pageData") or []:
        finish = x.get("dateFinish") or ""
        if finish and finish[:10] < today:
            continue
        title, text = _subject(x)
        bankrupt = x.get("bankrupt") or {}
        place = (x.get("tradePlace") or {}).get("name", "")
        lots.append(Lot(
            source="fedresurs",
            id=x.get("guid", ""),
            title=title or f"{x.get('type', 'Торги')} № {x.get('number', '')}",
            url=CARD.format(x.get("guid", "")),
            kind=" · ".join(p for p in ("Банкротство", x.get("type", ""), clean(place)) if p),
            customer=clean(bankrupt.get("name")),
            customer_inn=bankrupt.get("inn") or "",
            published=_date(x.get("dateStart") or x.get("dateCreate") or ""),
            deadline=_date(finish),
            status=(x.get("status") or {}).get("name", ""),
            text=text,
        ))
    return lots


class Fedresurs(Source):
    name = "fedresurs"
    title = "Федресурс (банкротство)"

    async def search(self, client, keyword, regions):
        today = date.today().isoformat()
        lots = []
        for page in range(MAX_PAGES):
            params = {
                "limit": PAGE,
                "offset": page * PAGE,
                "searchString": keyword,
                "onlyAvailableToParticipate": "true",
                "tradeStartDate": f"{today}T00:00:00",
            }
            if len(regions) == 1:
                params["regionCode"] = regions[0]
            r = await self.get(client, API, params=params, headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://fedresurs.ru/biddings",
            })
            data = r.json()
            lots.extend(parse_page(data, today))
            if (page + 1) * PAGE >= int(data.get("found") or 0):
                break
        return lots
