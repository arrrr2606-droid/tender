"""Совпадение лота с ключевыми словами (с учётом словоформ), минус-словами, ценой и регионом."""
from __future__ import annotations

import re
from datetime import date, timedelta
from functools import lru_cache
from typing import Optional

from . import regions
from .models import Lot

# Окончания, которые срезаем, чтобы «погрузчик» нашёл «погрузчика», «погрузчиков» и т.п.
_ENDINGS = sorted(
    ["ого", "его", "ому", "ему", "ыми", "ими", "ая", "яя", "ое", "ее", "ые", "ие", "ый", "ий", "ой",
     "ую", "юю", "ов", "ев", "ам", "ям", "ах", "ях", "ом", "ем", "а", "я", "ы", "и", "у", "ю", "о", "е", "ь"],
    key=len, reverse=True,
)


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def stem(word: str) -> str:
    w = _norm(word)
    for end in _ENDINGS:
        if w.endswith(end) and len(w) - len(end) >= 4:
            return w[: -len(end)]
    return w


@lru_cache(maxsize=512)
def phrase_regex(phrase: str) -> re.Pattern:
    """«фронтальный погрузчик» -> фронтальн\\w*[\\s-]+погрузчик\\w*

    Левой границы слова нет намеренно: «грейдер» находит и «автогрейдер».
    """
    words = [w for w in re.split(r"[\s\-]+", _norm(phrase)) if w]
    parts = [re.escape(stem(w)) + r"\w*" for w in words]
    return re.compile(r"[\s\-]+".join(parts))


def matched_keywords(text: str, keywords: list) -> list:
    t = _norm(text)
    return [k for k in keywords if k["word"] and phrase_regex(k["word"]).search(t)]


def has_minus(text: str, minus_words: list) -> Optional[str]:
    t = _norm(text)
    for w in minus_words:
        if w and phrase_regex(w).search(t):
            return w
    return None


def _parse_date(s: str):
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def is_stale(lot: Lot, max_age_days: int, today=None) -> bool:
    """Срок подачи прошёл или лот «висит» дольше max_age_days (в ЕИС бывают закупки 2014 года
    в статусе «Подача заявок»)."""
    today = today or date.today()
    deadline = _parse_date(lot.deadline)
    if deadline:
        return deadline < today
    published = _parse_date(lot.published)
    return bool(published and max_age_days and published < today - timedelta(days=max_age_days))


def resolve_region(lot: Lot) -> str:
    """Порядок: регион от площадки → место из названия лота («…для нужд Томской области»)
    → название заказчика → ИНН заказчика (у филиалов крупных компаний ИНН московский,
    поэтому он ниже текста) → прочий текст."""
    if lot.region_code:
        return lot.region_code
    return (regions.detect_in_text(lot.region_hint, lot.title, lot.customer)
            or regions.from_inn(lot.customer_inn)
            or regions.detect_in_text(lot.text))


def check(lot: Lot, state, cfg) -> tuple:
    """-> (подходит?, причина отказа). Заполняет lot.matched и lot.region_code."""
    kws = matched_keywords(lot.search_text, state.keywords)
    if not kws:
        return False, "нет ключевых слов"
    if is_stale(lot, getattr(cfg, "max_age_days", 90)):
        return False, "устарел"
    bad = has_minus(lot.title, state.minus_words)
    if bad:
        return False, f"минус-слово «{bad}»"
    if lot.price is not None:
        if cfg.min_price is not None and lot.price < cfg.min_price:
            return False, "цена ниже минимума"
        if cfg.max_price is not None and lot.price > cfg.max_price:
            return False, "цена выше максимума"

    lot.region_code = resolve_region(lot)
    passed = []
    for k in kws:
        allowed = k["regions"] if k.get("regions") is not None else state.regions
        if not allowed:
            passed.append(k)
        elif lot.region_code:
            if lot.region_code in allowed:
                passed.append(k)
        elif not cfg.strict_regions:
            passed.append(k)
    if not passed:
        return False, "не тот регион"
    lot.matched = [k["word"] for k in passed]
    return True, ""
