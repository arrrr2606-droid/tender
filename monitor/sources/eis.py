"""ЕИС zakupki.gov.ru — госзакупки 44-ФЗ и 223-ФЗ (RSS расширенного поиска)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .base import Lot, Source, SourceError, clean, parse_price

RSS = "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html"


def _fields(desc: str) -> dict:
    out = {}
    for label, value in re.findall(r"<strong>\s*([^<:]+?):\s*</strong>(.*?)(?=<br|<strong>|$)", desc, re.S):
        out.setdefault(label.strip(), clean(value))
    return out


def parse_rss(xml_text: str) -> list:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise SourceError(f"ЕИС: не удалось разобрать RSS ({e})")
    lots = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        m = re.search(r"regNumber=(\d+)", link)
        if not m:
            continue
        reg = m.group(1)
        f = _fields(item.findtext("description") or "")
        method = re.sub(r"\s*№.*$", "", item.findtext("title") or "").strip()
        law = f.get("Размещение выполняется по", "")
        price_key = next((k for k in f if k.startswith("Начальная")), None)
        ikz = f.get("Идентификационный код закупки (ИКЗ)", "")
        inn = ikz[3:13] if len(ikz) >= 13 and ikz.isdigit() else ""
        lots.append(Lot(
            source="eis",
            id=reg,
            eis_number=reg,
            title=f.get("Наименование объекта закупки") or method,
            url=link,
            kind=" · ".join(x for x in (law, method) if x),
            customer=f.get("Наименование Заказчика", "") or clean(item.findtext("author")),
            customer_inn=inn,
            price=parse_price(f.get(price_key)) if price_key else None,
            published=f.get("Размещено", ""),
            status=f.get("Этап размещения", ""),
        ))
    return lots


class EIS(Source):
    name = "eis"
    title = "ЕИС (44/223-ФЗ)"
    gov_tls = True

    async def search(self, client, keyword, regions):
        params = {
            "searchString": keyword,
            "morphology": "on",
            "sortBy": "PUBLISH_DATE",
            "sortDirection": "false",
            "fz44": "on",
            "fz223": "on",
            "af": "on",          # только этап «Подача заявок»
        }
        r = await self.get(client, RSS, params=params)
        return parse_rss(r.text)
