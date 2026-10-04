"""Точка входа для Yandex Cloud Functions.

Запускается по таймеру (раз в 3 часа): забирает базу лотов и настройки из бакета Object Storage,
обрабатывает накопившиеся команды бота, проверяет все площадки и кладёт базу обратно.

Переменные окружения функции:
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID — бот и чат
  BUCKET — имя бакета Object Storage для базы
Сервисному аккаунту функции нужна роль storage.editor (доступ к бакету по IAM-токену функции).
"""
import logging
import os
from pathlib import Path

import httpx

DATA = Path("/tmp/data")
FILES = ["state.json", "lots.sqlite"]
S3 = "https://storage.yandexcloud.net"
SEED = Path(__file__).parent / "cloud" / "seed_state.json"

log = logging.getLogger("cloud")


def _headers(context) -> dict:
    token = (getattr(context, "token", None) or {}).get("access_token")
    if not token:
        raise RuntimeError("У функции нет сервисного аккаунта — назначьте его в настройках функции")
    return {"X-YaCloud-SubjectToken": token}


def _download(bucket: str, headers: dict):
    DATA.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        r = httpx.get(f"{S3}/{bucket}/{name}", headers=headers, timeout=60)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        (DATA / name).write_bytes(r.content)
    if not (DATA / "state.json").exists() and SEED.exists():
        (DATA / "state.json").write_bytes(SEED.read_bytes())


def _upload(bucket: str, headers: dict):
    for name in FILES:
        path = DATA / name
        if path.exists():
            r = httpx.put(f"{S3}/{bucket}/{name}", content=path.read_bytes(), headers=headers, timeout=60)
            r.raise_for_status()


def handler(event, context):
    bucket = os.environ["BUCKET"]
    headers = _headers(context)
    os.environ["MONITOR_DATA"] = str(DATA)
    os.environ.setdefault("SOURCE_BUDGET", "55")   # 5 площадок × 55 с — укладываемся даже в 5-минутный тест консоли
    _download(bucket, headers)
    from monitor.main import main
    try:
        main(["--once"])
    finally:
        _upload(bucket, headers)
    return {"statusCode": 200, "body": "ok"}
