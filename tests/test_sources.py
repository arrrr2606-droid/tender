import json
from pathlib import Path

from monitor.sources import b2b_center, eis, fabrikant, fedresurs, torgi_gov

FX = Path(__file__).parent / "fixtures"


def test_eis_rss():
    lots = eis.parse_rss((FX / "eis.rss").read_text(encoding="utf-8"))
    assert len(lots) > 100
    first = lots[0]
    assert first.id == first.eis_number == "32616431027"
    assert first.title.startswith("Поставка автогрейдера")      # подсветка не рвёт слово
    assert first.price == 19499000.0
    assert "223-ФЗ" in first.kind
    with_ikz = next(l for l in lots if l.customer_inn)
    assert len(with_ikz.customer_inn) == 10


def test_torgi():
    lots = torgi_gov.parse_page(json.loads((FX / "torgi.json").read_text(encoding="utf-8")))
    assert lots and all(l.url.startswith("https://torgi.gov.ru/") for l in lots)
    assert all(len(l.region_code) == 2 for l in lots)


def test_fedresurs():
    lots = fedresurs.parse_page(json.loads((FX / "fedresurs.json").read_text(encoding="utf-8")), today="2026-10-02")
    assert len(lots) == 4
    assert lots[0].title.startswith("Автогрейдер ДЗ-122А-1")
    assert lots[0].customer_inn == "7113016460"
    assert lots[0].deadline == "12.10.2026"
    assert "Банкротство" in lots[0].kind
    # прошедшие торги отбрасываются
    assert len(fedresurs.parse_page(json.loads((FX / "fedresurs.json").read_text(encoding="utf-8")),
                                    today="2026-10-13")) == 1


def test_b2b():
    lots = b2b_center.parse_page(json.loads((FX / "b2b.json").read_text(encoding="utf-8")))
    assert len(lots) == 20
    assert lots[0].url.startswith("https://www.b2b-center.ru/market/") and "#" not in lots[0].url
    assert lots[0].region_hint


def test_fabrikant():
    lots = fabrikant.parse_html((FX / "fabrikant.html").read_text(encoding="utf-8"))
    assert len(lots) == 1                      # остальные в выдаче завершены
    l = lots[0]
    assert l.title == "Поставка автогрейдера"
    assert l.eis_number == "0194200000526005085"
    assert l.price == 13327333.0
    assert l.deadline.startswith("05.10.2026")
