"""Сборщик для Yandex Cloud Functions: проверяет площадки и складывает найденное в хранилище.

Из облака в России открываются все площадки, но не открывается Telegram. Поэтому сообщения
не отправляются отсюда, а кладутся в бакет (папка outbox/), откуда их забирает и отправляет
cloud/relay.py на GitHub. Он же обрабатывает кнопки бота и пишет настройки в settings.json.

Файлы в бакете:
  settings.json          — слова, регионы, пауза (пишет только relay, здесь только читаем)
  collector/state.json   — служебное состояние сборщика
  collector/lots.sqlite  — какие лоты уже видели
  outbox/<время>.json    — сообщения для отправки

Переменные окружения: BUCKET; у функции должен быть сервисный аккаунт с ролью storage.editor.
Необязательно: GH_TOKEN (+ GH_REPO) — после проверки сразу запустить рассылку на GitHub,
не дожидаясь его расписания.
Запуск — по таймеру (раз в 3 часа). Второй таймер с данными «relay» (каждые 10 минут) только
будит GitHub, чтобы бот быстрее отвечал на кнопки.
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

DATA = Path("/tmp/data")
S3 = "https://storage.yandexcloud.net"
SEED = Path(__file__).parent / "cloud" / "seed_state.json"
FILES = {"state.json": "collector/state.json", "lots.sqlite": "collector/lots.sqlite"}

log = logging.getLogger("collector")


class Bucket:
    def __init__(self, name: str, token: str):
        self.base = f"{S3}/{name}"
        self.headers = {"X-YaCloud-SubjectToken": token}

    def get(self, key: str):
        r = httpx.get(f"{self.base}/{key}", headers=self.headers, timeout=60)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    def put(self, key: str, data: bytes):
        httpx.put(f"{self.base}/{key}", content=data, headers=self.headers, timeout=60).raise_for_status()


def handler(event, context):
    # Второй таймер (каждые 10 мин, данные «relay»): только разбудить GitHub, чтобы бот ответил
    # на кнопки. Площадки при этом не проверяются.
    if '"relay"' in json.dumps(event, ensure_ascii=False):
        start_relay()
        return {"statusCode": 200, "body": "relay"}

    token = (getattr(context, "token", None) or {}).get("access_token")
    if not token:
        raise RuntimeError("У функции нет сервисного аккаунта — назначьте его в настройках функции")
    bucket = Bucket(os.environ["BUCKET"], token)

    os.environ["MONITOR_DATA"] = str(DATA)
    # По таймеру функция может работать до 10 минут; тест в консоли обрывается на 5-й минуте.
    by_timer = isinstance(event, dict) and "messages" in event
    os.environ["SOURCE_BUDGET"] = "100" if by_timer else "45"   # сек на одну площадку
    os.environ["RUN_BUDGET"] = "480" if by_timer else "220"     # сек на весь проход

    DATA.mkdir(parents=True, exist_ok=True)
    for local, key in FILES.items():
        data = bucket.get(key)
        if data is not None:
            (DATA / local).write_bytes(data)
    if not (DATA / "state.json").exists() and SEED.exists():
        (DATA / "state.json").write_bytes(SEED.read_bytes())

    import monitor.main as m
    from monitor import config as mconfig
    from monitor.outbox import Outbox
    from monitor.storage import Storage

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = mconfig.load_config()
    state = mconfig.State(DATA / "state.json", cfg.seed)
    storage = Storage(DATA / "lots.sqlite")

    settings = bucket.get("settings.json")
    if settings is None:
        # первый запуск: публикуем настройки, дальше их меняет бот через relay
        bucket.put("settings.json", json.dumps(state.data, ensure_ascii=False, indent=2).encode())
    elif state.apply_shared(json.loads(settings)):
        storage.forget_unsent()
        state.data["initialized_sources"] = []
        state.save()

    counter = {"n": 0}

    def write_outbox(items):
        counter["n"] += 1
        key = f"outbox/{time.strftime('%Y%m%d-%H%M%S')}-{counter['n']:02d}.json"
        bucket.put(key, json.dumps(items, ensure_ascii=False).encode())

    outbox = Outbox(cfg.telegram_chat_id, write_outbox)

    def save():
        outbox.flush()   # сначала сообщения, потом отметка «отправлено» — чтобы ничего не потерять
        for local, key in FILES.items():
            path = DATA / local
            if path.exists():
                bucket.put(key, path.read_bytes())

    m.AFTER_SOURCE = save
    # модуль мог остаться в памяти с прошлого вызова — лимиты выставляем явно
    m.SOURCE_BUDGET = int(os.environ["SOURCE_BUDGET"])
    m.RUN_BUDGET = int(os.environ["RUN_BUDGET"])
    try:
        asyncio.run(m.run_cycle(cfg, state, storage, outbox))
    finally:
        save()
        start_relay()
    return {"statusCode": 200, "body": "ok"}


def start_relay():
    """Попросить GitHub сразу разослать найденное (иначе он запустится по своему расписанию)."""
    token = os.environ.get("GH_TOKEN")
    if not token:
        return
    repo = os.environ.get("GH_REPO", "arrrr2606-droid/tender")
    try:
        r = httpx.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/monitor.yml/dispatches",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"ref": "main"}, timeout=30,
        )
        if r.status_code != 204:
            log.warning("GitHub не запустил рассылку: HTTP %s %s", r.status_code, r.text[:200])
    except httpx.HTTPError as e:
        log.warning("GitHub недоступен: %s", e)
