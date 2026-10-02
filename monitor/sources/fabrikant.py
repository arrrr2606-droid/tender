"""Фабрикант — коммерческие закупки и 223-ФЗ.

Страница поиска — Next.js с серверным рендерингом: данные лежат в RSC-потоке
self.__next_f.push(...) прямо в HTML, отдельного JSON API нет.
"""
from __future__ import annotations

import json
import re

from .base import Lot, Source, SourceError, parse_price

SEARCH = "https://www.fabrikant.ru/procedure/search/purchases"
LABELS = {"Организатор", "Заказчик", "Дата публикации", "Дата окончания приёма заявок",
          "начальная цена", "Платит победитель"}
CLOSED = re.compile(r"заверш|законч|заключ|отмен|итог|архив", re.I)
OPEN = re.compile(r"при[её]м|подач", re.I)

_STR = r'"((?:[^"\\]|\\.)*)"'


def _unescape(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except ValueError:
        return s


def rsc_payload(html: str) -> str:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    return "".join(_unescape(c) for c in chunks)


def _after(texts: list, label: str) -> str:
    if label in texts:
        i = texts.index(label)
        for t in texts[i + 1:]:
            if t not in LABELS:
                return t
    return ""


def parse_html(html: str) -> list:
    payload = rsc_payload(html)
    if not payload:
        raise SourceError("Фабрикант: в странице нет данных поиска (изменилась вёрстка?)")
    starts = [m.start() for m in re.finditer(r'"data-id":\d+', payload)]
    lots = []
    for n, start in enumerate(starts):
        seg = payload[start: starts[n + 1] if n + 1 < len(starts) else len(payload)]
        head = re.search(r'"url":' + _STR + r',"name":' + _STR + r',"lotId":(\d+)', seg)
        if not head:
            continue
        url, title, lot_id = _unescape(head.group(1)), _unescape(head.group(2)), head.group(3)
        oos = re.search(r'"purchaseOosNumber":"(\d+)"', seg)
        texts = [_unescape(t) for t in re.findall(r'"children":' + _STR, seg)]
        status = next((t for t in texts if CLOSED.search(t) or OPEN.search(t)), "")
        if status and CLOSED.search(status) and not OPEN.search(status):
            continue
        organizer = re.search(r'\{"name":' + _STR + r"\}", seg)
        price = re.search(r'font-bold","children":\["([\d  ]+)"', seg)
        customer = _after(texts, "Заказчик") or (_unescape(organizer.group(1)) if organizer else "")
        kind = " · ".join(t for t in texts[:3] if t not in LABELS and t != status and not t.startswith("№"))
        lots.append(Lot(
            source="fabrikant",
            id=lot_id,
            title=title,
            url=url,
            kind="Фабрикант" + (f" · {kind}" if kind else ""),
            customer=customer,
            price=parse_price(price.group(1)) if price else None,
            published=_after(texts, "Дата публикации"),
            deadline=_after(texts, "Дата окончания приёма заявок"),
            status=status,
            eis_number=oos.group(1) if oos else "",
        ))
    return lots


class Fabrikant(Source):
    name = "fabrikant"
    title = "Фабрикант"

    async def search(self, client, keyword, regions):
        r = await self.get(client, SEARCH, params={"query": keyword})
        return parse_html(r.text)
