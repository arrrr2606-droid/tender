"""torgi.gov.ru — продажа государственного и муниципального имущества (публичный JSON API)."""
from __future__ import annotations

from .base import Lot, Source, clean, parse_price

API = "https://torgi.gov.ru/new/api/public/lotcards/search"
LOT_URL = "https://torgi.gov.ru/new/public/lots/lot/{}"
PAGE_SIZE = 100
MAX_PAGES = 5


def _date(s: str) -> str:
    # 2026-10-05T09:00:00.000+00:00 -> 05.10.2026
    if not s or len(s) < 10:
        return ""
    return f"{s[8:10]}.{s[5:7]}.{s[0:4]}"


def parse_page(data: dict) -> list:
    lots = []
    for x in data.get("content") or []:
        lots.append(Lot(
            source="torgi",
            id=str(x.get("id")),
            title=clean(x.get("lotName")),
            url=LOT_URL.format(x.get("id")),
            kind=((x.get("biddType") or {}).get("name") or "Имущество") + (
                f" · {(x.get('biddForm') or {}).get('name')}" if x.get("biddForm") else ""),
            price=parse_price(x.get("priceMin")),
            published=_date(x.get("createDate", "")),
            deadline=_date(x.get("biddEndTime", "")),
            status=x.get("lotStatus", ""),
            region_code=str(x.get("subjectRFCode") or "").zfill(2) if x.get("subjectRFCode") else "",
            text=clean(x.get("lotDescription")),
        ))
    return lots


class TorgiGov(Source):
    name = "torgi"
    title = "torgi.gov.ru (имущество)"
    gov_tls = True

    async def search(self, client, keyword, regions):
        lots = []
        for page in range(MAX_PAGES):
            params = {
                "text": keyword,
                "lotStatus": "PUBLISHED,APPLICATIONS_SUBMISSION",
                "size": PAGE_SIZE,
                "page": page,
                "sort": "firstVersionPublicationDate,desc",
            }
            if len(regions) == 1:
                params["subjRF"] = regions[0]
            r = await self.get(client, API, params=params)
            data = r.json()
            lots.extend(parse_page(data))
            if data.get("last", True) or not data.get("content"):
                break
        return lots
