"""ЭТП ГПБ (etpgpb.ru) — закупки Газпрома и других заказчиков, включая тех, кто под санкциями
не публикуется в ЕИС (ПП №301). Открытый JSON API поиска."""
from __future__ import annotations

from .base import Lot, Source, clean, parse_price

API = "https://etpgpb.ru/api/v2/procedures/"
SITE = "https://etpgpb.ru"
PER_PAGE = 50
MAX_PAGES = 3
# Разделы и способы продажи имущества — нам нужны только закупки
SALE_SECTIONS = {"ЭТП Торги"}
SALE_TYPES = {"Публичное предложение"}


def _date(s: str) -> str:
    return f"{s[8:10]}.{s[5:7]}.{s[0:4]} {s[11:16]}".strip() if s and len(s) >= 10 else ""


def parse_page(data: dict) -> list:
    lots = []
    for x in data.get("data") or []:
        a = x.get("attributes") or {}
        if a.get("stage") != "accepting":
            continue
        if a.get("section_category_name") in SALE_SECTIONS or a.get("procedure_type_name") in SALE_TYPES:
            continue
        if a.get("kind") == "fz44":
            continue  # 44-ФЗ всегда есть в ЕИС — не дублируем
        path = a.get("rebranding_truncated_path") or a.get("truncated_path") or ""
        regions = a.get("lot_regions") or []
        lots.append(Lot(
            source="etpgpb",
            id=str(x.get("id")),
            title=clean(a.get("title")),
            url=SITE + path if path.startswith("/") else (
                a.get("platform_url") if str(a.get("platform_url") or "").startswith("http") else SITE),
            kind=" · ".join(p for p in (a.get("section_category_name"), a.get("procedure_type_name")) if p),
            customer=clean(a.get("company_name")),
            price=parse_price(a.get("amount")),
            published=_date(a.get("date_published") or ""),
            deadline=_date(a.get("end_registration") or ""),
            status="Приём заявок",
            region_hint=", ".join(regions[:3]),
        ))
    return lots


class EtpGpb(Source):
    name = "etpgpb"
    title = "ЭТП ГПБ (Газпром)"

    async def search(self, client, keyword, regions):
        lots = []
        for page in range(1, MAX_PAGES + 1):
            params = {
                "page": page,
                "per": PER_PAGE,
                "search": keyword,
                "sort": "by_published_desc",
                "procedure[stage][0]": "accepting",
            }
            r = await self.get(client, API, params=params, headers={"Accept": "application/json"})
            data = r.json()
            lots.extend(parse_page(data))
            if page >= int((data.get("meta") or {}).get("total_pages") or 1):
                break
        return lots
