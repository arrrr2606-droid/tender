from datetime import date
from types import SimpleNamespace

from monitor import matcher
from monitor.models import Lot

KW = [{"word": w, "regions": None} for w in
      ["автогрейдер", "грейдер", "экскаватор-погрузчик", "фронтальный погрузчик", "погрузчик", "спецтехника"]]


def state(regions=(), keywords=KW, minus=("вилочный", "электропогрузчик")):
    return SimpleNamespace(keywords=list(keywords), minus_words=list(minus), regions=list(regions))


def cfg(**kw):
    base = dict(min_price=None, max_price=None, strict_regions=False, max_age_days=90)
    base.update(kw)
    return SimpleNamespace(**base)


def lot(title, **kw):
    return Lot(source="t", id="1", title=title, url="u", **kw)


def words(text):
    return [k["word"] for k in matcher.matched_keywords(text, KW)]


def test_word_forms():
    assert "автогрейдер" in words("Поставка автогрейдера ДЗ-98")
    assert "грейдер" in words("Поставка автогрейдеров")          # подстрока внутри слова
    assert "экскаватор-погрузчик" in words("экскаватора-погрузчика JCB 3CX")
    assert "фронтальный погрузчик" in words("Поставка фронтального погрузчика")
    assert "спецтехника" in words("Аренда спецтехники с экипажем")
    assert words("Поставка бумаги") == []


def test_minus_words():
    ok, why = matcher.check(lot("Поставка вилочного погрузчика"), state(), cfg())
    assert not ok and "минус" in why
    ok, _ = matcher.check(lot("Электропогрузчик складской"), state(), cfg())
    assert not ok


def test_price_filter():
    ok, _ = matcher.check(lot("Поставка автогрейдера", price=500.0), state(), cfg(min_price=1000))
    assert not ok
    ok, _ = matcher.check(lot("Поставка автогрейдера"), state(), cfg(min_price=1000))
    assert ok  # без цены не отсекаем


def test_regions_global_and_strict():
    l = lot("Поставка автогрейдера", customer="ДОРОЖНАЯ СЛУЖБА ИРКУТСКОЙ ОБЛАСТИ")
    assert matcher.check(l, state(regions=["38"]), cfg())[0]
    assert l.region_code == "38"
    assert not matcher.check(lot("Поставка автогрейдера", customer="ДОРОЖНАЯ СЛУЖБА ИРКУТСКОЙ ОБЛАСТИ"),
                             state(regions=["16"]), cfg())[0]
    unknown = lot("Поставка автогрейдера", customer="Войсковая часть 55056")
    assert matcher.check(unknown, state(regions=["16"]), cfg())[0]                 # приходит с пометкой
    unknown = lot("Поставка автогрейдера", customer="Войсковая часть 55056")
    assert not matcher.check(unknown, state(regions=["16"]), cfg(strict_regions=True))[0]


def test_region_by_inn_and_title_priority():
    l = lot("Поставка погрузчика", customer_inn="1655000000")
    matcher.check(l, state(), cfg())
    assert l.region_code == "16"
    # филиал московской компании: место из названия важнее ИНН
    l = lot("Поставка погрузчика для нужд филиала в Томской области", customer_inn="7700000000")
    matcher.check(l, state(), cfg())
    assert l.region_code == "70"


def test_per_keyword_regions():
    kws = [{"word": "грейдер", "regions": []}, {"word": "погрузчик", "regions": ["16"]}]
    st = state(regions=["77"], keywords=kws)
    g = lot("Поставка автогрейдера", region_code="38")
    assert matcher.check(g, st, cfg())[0]                       # грейдер — вся Россия
    p = lot("Поставка погрузчика", region_code="38")
    assert not matcher.check(p, st, cfg())[0]                   # погрузчик — только Татарстан
    p = lot("Поставка погрузчика", region_code="16")
    assert matcher.check(p, st, cfg())[0]


def test_stale():
    today = date(2026, 10, 2)
    assert matcher.is_stale(lot("x", published="16.05.2014"), 90, today)
    assert not matcher.is_stale(lot("x", published="01.10.2026"), 90, today)
    assert matcher.is_stale(lot("x", deadline="01.10.2026 10:00"), 90, today)
    assert not matcher.is_stale(lot("x", published="01.01.2026", deadline="12.10.2026"), 90, today)
