from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Lot:
    source: str                     # код источника: eis, torgi, fedresurs, b2b, fabrikant
    id: str                         # уникальный в пределах источника
    title: str
    url: str
    kind: str = ""                  # «44-ФЗ», «223-ФЗ», «Банкротство», «Коммерческая» и т.п.
    customer: str = ""              # заказчик / организатор / должник
    customer_inn: str = ""
    price: Optional[float] = None
    published: str = ""             # дата публикации (как строка для показа)
    deadline: str = ""              # окончание подачи заявок
    status: str = ""
    region_code: str = ""           # код субъекта РФ, «» если не определён
    region_hint: str = ""           # текст региона, как его отдала площадка
    text: str = ""                  # доп. текст для поиска совпадений и региона
    eis_number: str = ""            # реестровый номер ЕИС — для склейки дублей между площадками
    matched: list = field(default_factory=list)  # какие ключевые слова сработали

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def search_text(self) -> str:
        return f"{self.title}\n{self.text}"
