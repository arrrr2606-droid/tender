"""ТЭК-Торг (tektorg.ru) — закупки Роснефти, РЖД, Транснефти, Интер РАО и др.

Страница поиска — Next.js: список процедур лежит в __NEXT_DATA__ прямо в HTML.
"""
from __future__ import annotations

import json
import re

from .base import Lot, Source, SourceError, clean, parse_price

SEARCH = "https://www.tektorg.ru/procedures"
MAX_PAGES = 2   # по 15 самых свежих на страницу
OPEN_STATUS = "Приём заявок"
# Разделы продажи имущества и 44-ФЗ (он есть в ЕИС) пропускаем
SKIP_SECTIONS = {"sale", "rosneft_selling", "sale178", "arrested_sale", "sale_arrest", "44fz", "mirea_44"}


SECTION_NAMES = {
    "rosneft": "Роснефть", "rosnefttkp": "Роснефть (ТКП)", "rzd": "РЖД", "zakupki": "Закупки",
    "market": "Маркет", "transneft": "Транснефть", "interrao": "Интер РАО", "mosenergo": "Мосэнерго",
    "nornikel": "Норникель", "vostok_oil": "Восток Ойл", "vostok_oil_tkp": "Восток Ойл (ТКП)",
    "rusgazburenie": "Русгазбурение", "rusgasdob": "Русгаздобыча", "enplusgroup": "Эн+",
    "oboronenergo": "Оборонэнерго", "spb_metro": "Метрополитен СПб", "les": "Лесные ресурсы",
}


def _date(s: str) -> str:
    return f"{s[8:10]}.{s[5:7]}.{s[0:4]} {s[11:16]}".strip() if s and len(s) >= 10 else ""


def parse_html(html: str) -> list:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise SourceError("ТЭК-Торг: в странице нет данных поиска (изменилась вёрстка?)")
    try:
        listing = json.loads(m.group(1))["props"]["pageProps"]["initialReduxState"]["listingProcedures"]
    except (KeyError, ValueError) as e:
        raise SourceError(f"ТЭК-Торг: неожиданный формат данных ({e})")
    lots = []
    for x in listing.get("data") or []:
        if x.get("statusName") != OPEN_STATUS or x.get("sectionAlias") in SKIP_SECTIONS:
            continue
        reg = str(x.get("registryNumber") or "")
        dates = x.get("dates") or {}
        lots.append(Lot(
            source="tektorg",
            id=str(x.get("id")),
            title=clean(x.get("title")),
            url=x.get("etpLink") or f"https://www.tektorg.ru/procedures?name={reg}",
            kind=" · ".join(p for p in (SECTION_NAMES.get(x.get("sectionAlias"), x.get("sectionAlias")),
                                        x.get("typeName")) if p),
            customer=clean(x.get("organizerName")),
            customer_inn=str(x.get("inn") or ""),
            price=parse_price(x.get("sumPrice")),
            published=_date(dates.get("datePublished") or ""),
            deadline=_date(dates.get("dateEndRegistration") or ""),
            status=x.get("statusName", ""),
            # 11 цифр (223-ФЗ) или 19 (44-ФЗ) — номер ЕИС, по нему склеиваем дубли с ЕИС
            eis_number=reg if reg.isdigit() and len(reg) in (11, 19) else "",
        ))
    return lots


class TekTorg(Source):
    name = "tektorg"
    title = "ТЭК-Торг (Роснефть и др.)"

    async def search(self, client, keyword, regions):
        lots = []
        for page in range(1, MAX_PAGES + 1):
            r = await self.get(client, SEARCH, params={"name": keyword, "sort": "datePublished_desc", "page": page})
            lots.extend(parse_html(r.text))
        return lots
