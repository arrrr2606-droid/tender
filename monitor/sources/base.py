from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from typing import Optional

import httpx

from ..models import Lot

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


class SourceError(Exception):
    pass


class Source:
    name = ""          # короткий код (совпадает с ключом в config.sources)
    title = ""         # человекочитаемое название
    gov_tls = False    # сайт на сертификатах НУЦ Минцифры
    delay = 0.8        # пауза между запросами к одной площадке, сек

    def __init__(self, cfg):
        self.cfg = cfg

    def client(self) -> httpx.AsyncClient:
        verify = True
        if self.gov_tls:
            if self.cfg.insecure_gov:
                verify = False
            elif self.cfg.ca_bundle and os.path.exists(self.cfg.ca_bundle):
                verify = self.cfg.ca_bundle
            else:
                log.warning("%s: нет бандла сертификатов НУЦ Минцифры (%s) — запрос, скорее всего, "
                            "упадёт по TLS. Запустите в Docker или выполните scripts/make_ca_bundle.py",
                            self.title, self.cfg.ca_bundle)
        return httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            verify=verify,
        )

    async def get(self, client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
        last = None
        for attempt in range(3):
            try:
                r = await client.get(url, **kw)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise SourceError(f"HTTP {r.status_code}")
                if r.status_code >= 400:
                    raise SourceError(f"HTTP {r.status_code} — площадка отказала в доступе")
                await asyncio.sleep(self.delay)
                return r
            except (httpx.HTTPError, SourceError) as e:
                last = e
                if isinstance(e, SourceError) and "отказала" in str(e):
                    break
                await asyncio.sleep(3 * (attempt + 1))
        raise SourceError(f"{self.title}: {type(last).__name__} {last}".strip())

    async def search(self, client: httpx.AsyncClient, keyword: str, regions: list) -> list:
        raise NotImplementedError


def clean(text: Optional[str]) -> str:
    """Убирает HTML-теги и сущности, схлопывает пробелы."""
    if not text:
        return ""
    # подсветка внутри слова («<span>автогрейдер</span>а») не должна рвать слово
    text = re.sub(r"</?(?:span|mark|res|b|strong|i|em)\b[^>]*>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_price(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d,\.]", "", str(value)).replace(",", ".")
    if s.count(".") > 1:
        head, _, tail = s.rpartition(".")
        s = head.replace(".", "") + "." + tail
    try:
        return float(s) if s else None
    except ValueError:
        return None


__all__ = ["Source", "SourceError", "Lot", "clean", "parse_price", "log"]
