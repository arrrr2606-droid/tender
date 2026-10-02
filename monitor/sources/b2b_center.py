"""B2B-Center — коммерческие закупки (JSON API поиска по рынку)."""
from __future__ import annotations

from .base import Lot, Source, clean, parse_price

API = "https://www.b2b-center.ru/site/api/v1/market-search/"
SITE = "https://www.b2b-center.ru"
MAX_PAGES = 5      # площадка отдаёт по 20 на страницу


def parse_page(data: dict) -> list:
    lots = []
    for x in data.get("trades") or []:
        url = (x.get("url") or "").split("#")[0]
        lots.append(Lot(
            source="b2b",
            id=str(x.get("trade_id")),
            title=clean(x.get("description")),
            url=SITE + url if url.startswith("/") else url,
            kind="B2B-Center" + (f" · {clean(x.get('tags'))}" if x.get("tags") else ""),
            customer=clean(x.get("org_name_short")),
            price=parse_price(x.get("price")),
            published=(x.get("date_published") or "")[:10],
            deadline=x.get("date_actual") or "",
            region_hint=clean(x.get("region")),
            text=clean(x.get("snippet")),
        ))
    return lots


class B2BCenter(Source):
    name = "b2b"
    title = "B2B-Center"

    async def search(self, client, keyword, regions):
        lots = []
        for page in range(1, MAX_PAGES + 1):
            params = {
                "query": keyword,
                "company_type": 2,
                "mod_derivatives": "false",
                "mod_phrase": "false",
                "page": page,
                "page_size": 20,
                "sort": "date_desc",
                "macro_trade_type": "buy",
                "tab": "actual",
                "is_seo": 0,
            }
            r = await self.get(client, API, params=params, headers={
                "Accept": "application/json",
                "Referer": SITE + "/app/next/market-search/",
            })
            data = r.json()
            lots.extend(parse_page(data))
            if page >= int(data.get("page_count") or 1):
                break
        return lots
