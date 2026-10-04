"""«Почтовый ящик» вместо Telegram: сообщения копятся и сохраняются в хранилище,
а отправляет их другой исполнитель (cloud/relay.py на GitHub).

Нужен, когда сборщик работает там, откуда Telegram недоступен (облако в России).
"""
from __future__ import annotations

from typing import Callable, Optional

from .models import Lot
from .telegram import format_lot


class Outbox:
    enabled = True

    def __init__(self, chat_id: str, writer: Callable[[list], None]):
        self.chat_id = chat_id or "outbox"
        self.items = []
        self.writer = writer

    async def send(self, text: str, chat_id: Optional[str] = None, url: Optional[str] = None,
                   markup: Optional[dict] = None) -> bool:
        self.items.append({"text": text, "url": url})
        return True

    async def send_lot(self, lot: Lot) -> bool:
        return await self.send(format_lot(lot), url=lot.url)

    def flush(self):
        if self.items:
            self.writer(self.items)
            self.items = []

    async def close(self):
        self.flush()
