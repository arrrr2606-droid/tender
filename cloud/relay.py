"""Пересыльщик для GitHub Actions: Telegram ↔ хранилище Yandex Object Storage.

1. Обрабатывает накопившиеся команды и кнопки бота → сохраняет settings.json в бакет
   (оттуда слова и регионы берёт сборщик cloud/index.py в Яндексе).
2. Забирает из бакета outbox/*.json — найденные сборщиком торги — и отправляет их в Telegram.

Переменные окружения: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, YC_BUCKET, YC_KEY_ID, YC_SECRET
(статический ключ доступа сервисного аккаунта с ролью storage.editor).
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.bot import Bot  # noqa: E402
from monitor.config import State, load_config  # noqa: E402
from monitor.storage import Storage  # noqa: E402
from monitor.telegram import Telegram  # noqa: E402

log = logging.getLogger("relay")
DATA = Path(os.environ.get("MONITOR_DATA", "data"))

# Разовые изменения общих настроек (применяются к уже сохранённому settings.json)
SETTINGS_VERSION = 2
PURCHASE_ONLY_MINUS = ["аренда", "услуги", "предоставление", "ремонт", "обслуживание"]
SALE_SOURCES = ["torgi", "fedresurs"]          # продажа/аренда имущества, а не закупки
SALE_MARKERS = ("🏷 torgi.gov.ru", "🏷 Федресурс", "✅ torgi.gov.ru", "✅ Федресурс", "⚠️ torgi.gov.ru", "⚠️ Федресурс")


def migrate(state: State) -> bool:
    d = state.data
    if d.get("settings_version", 0) >= SETTINGS_VERSION:
        return False
    # v2: только закупки (покупка), без продажи/аренды имущества и услуг
    d["sources_off"] = sorted(set(d.get("sources_off", [])) | set(SALE_SOURCES))
    for w in PURCHASE_ONLY_MINUS:
        if w.lower() not in (x.lower() for x in d["minus_words"]):
            d["minus_words"].append(w)
    d["settings_version"] = SETTINGS_VERSION
    state.save()
    return True


def unwanted(item: dict, state: State) -> bool:
    """Сообщение, которое уже лежит в очереди, но по текущим настройкам не нужно."""
    from monitor.matcher import has_minus

    text = item.get("text", "")
    if not item.get("url"):
        return any(m in text for m in SALE_MARKERS)   # служебные сообщения о площадках
    if any(m in text for m in SALE_MARKERS):
        return True
    title = text.split("\n", 1)[0]
    return bool(has_minus(title, state.minus_words))


class Bucket:
    def __init__(self):
        self.name = os.environ["YC_BUCKET"]
        self.s3 = boto3.session.Session().client(
            "s3",
            endpoint_url="https://storage.yandexcloud.net",
            region_name="ru-central1",
            aws_access_key_id=os.environ["YC_KEY_ID"],
            aws_secret_access_key=os.environ["YC_SECRET"],
        )

    def get(self, key: str):
        try:
            return self.s3.get_object(Bucket=self.name, Key=key)["Body"].read()
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise

    def put(self, key: str, data: bytes):
        self.s3.put_object(Bucket=self.name, Key=key, Body=data)

    def delete(self, key: str):
        self.s3.delete_object(Bucket=self.name, Key=key)

    def list(self, prefix: str) -> list:
        keys, token = [], None
        while True:
            kw = {"Bucket": self.name, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            res = self.s3.list_objects_v2(**kw)
            keys += [o["Key"] for o in res.get("Contents", [])]
            if not res.get("IsTruncated"):
                return sorted(keys)
            token = res["NextContinuationToken"]


async def send_outbox(bucket: Bucket, tg: Telegram, state: State) -> int:
    sent = 0
    for key in bucket.list("outbox/"):
        items = [x for x in json.loads(bucket.get(key) or b"[]") if not unwanted(x, state)]
        for i, item in enumerate(items):
            ok = await tg.send(item["text"], url=item.get("url"))
            if not ok and item.get("url"):
                ok = await tg.send(item["text"])   # может, Telegram не понравилась кнопка-ссылка
            if ok:
                sent += 1
                continue
            # Сетевые сбои и лимиты Telegram.call() уже переждал; раз всё равно отказ — проверим,
            # жив ли Telegram вообще. Если жив — сообщение «битое», пропускаем его, а не всю очередь.
            if (await tg.call("getMe")).get("ok"):
                log.warning("Telegram отклонил сообщение, пропускаю его: %s", item["text"][:120])
                continue
            bucket.put(key, json.dumps(items[i:], ensure_ascii=False).encode())
            log.warning("Telegram недоступен, остаток %d оставлен в %s", len(items) - i, key)
            return sent
        bucket.delete(key)
    return sent


async def run():
    cfg = load_config()
    bucket = Bucket()
    tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
    if not tg.enabled:
        log.warning("TELEGRAM_TOKEN не задан — пропускаю")
        return
    DATA.mkdir(parents=True, exist_ok=True)

    settings = bucket.get("settings.json")
    if settings is not None:
        (DATA / "state.json").write_bytes(settings)
    lots = bucket.get("collector/lots.sqlite")   # только для /status — состояние площадок
    if lots is not None:
        (DATA / "lots.sqlite").write_bytes(lots)

    state = State(DATA / "state.json", cfg.seed)
    storage = Storage(DATA / "lots.sqlite")
    if migrate(state):
        log.warning("Настройки обновлены: только закупки, без продажи/аренды имущества и услуг")
    try:
        await Bot(tg, state, storage, asyncio.Event()).process_pending()
        if settings is None or (DATA / "state.json").read_bytes() != settings:
            bucket.put("settings.json", (DATA / "state.json").read_bytes())
        if tg.chat_id:
            n = await send_outbox(bucket, tg, state)
            log.warning("Отправлено сообщений: %d", n)
    finally:
        await tg.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run())
