"""Точка входа: цикл опроса площадок + Telegram-бот.

python -m monitor.main                 # рабочий режим
python -m monitor.main --once --dry-run   # один проход, вывод в консоль, без Telegram и без записи в базу
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time

from . import matcher, regions
from .bot import Bot
from .config import State, load_config
from .sources import ALL
from .sources.base import SourceError
from .storage import Storage
from .telegram import Telegram, format_lot

log = logging.getLogger("monitor")
ALERT_AFTER_FAILS = 3
# ЕИС проверяет другой исполнитель (Mac) — дубли закупок ЕИС с Фабриканта здесь не присылаем
EIS_ELSEWHERE = os.environ.get("EIS_ELSEWHERE") == "1"
FIRST_RUN_MAX = 40      # сколько уже открытых торгов прислать при первом подключении площадки


def _date_key(s: str) -> str:
    """«02.10.2026 15:21» -> «2026-10-02» для сортировки; без даты — в конец."""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


SOURCE_BUDGET = int(os.environ.get("SOURCE_BUDGET", "150"))  # сек на одну площадку
# Общий лимит на проход (сек, 0 — без лимита). Когда время выходит, оставшиеся лоты не отправляются
# и не отмечаются — их пришлёт следующий проход. Нужен для облачных функций с таймаутом.
RUN_BUDGET = int(os.environ.get("RUN_BUDGET", "0"))
AFTER_SOURCE = None   # колбэк после каждой площадки (облачная функция сохраняет базу)


async def collect(src, state) -> tuple:
    """Опрашивает площадку по всем ключевым словам. -> (лоты, ошибка или None)

    Жёсткий лимит SOURCE_BUDGET: зависшую площадку обрываем на ходу, собранное сохраняем.
    """
    found, errors = {}, []
    try:
        await asyncio.wait_for(_collect(src, state, found, errors), timeout=SOURCE_BUDGET)
    except asyncio.TimeoutError:
        errors.append(f"{src.title}: не уложилась в {SOURCE_BUDGET} с — взято то, что успели собрать")
        log.warning(errors[-1])
    return list(found.values()), (errors[-1] if errors else None)


async def _collect(src, state, found: dict, errors: list):
    async with src.client() as client:
        for kw in state.keywords:
            regs = kw["regions"] if kw.get("regions") is not None else state.regions
            try:
                for lot in await src.search(client, kw["word"], regs):
                    found.setdefault(lot.key, lot)
            except SourceError as e:
                errors.append(str(e))
                log.warning("%s / «%s»: %s", src.title, kw["word"], e)
                # площадка недоступна (блокировка, таймауты) — не мучаем её остальными словами
                if "отказала" in str(e) or not found:
                    break
            except Exception as e:  # изменилась вёрстка/формат — не роняем остальные площадки
                errors.append(f"{type(e).__name__}: {e}")
                log.exception("%s / «%s»", src.title, kw["word"])


async def run_cycle(cfg, state, storage, tg, dry_run=False):
    if state.paused:
        log.info("На паузе — пропускаю проход")
        return
    deadline = time.monotonic() + RUN_BUDGET if RUN_BUDGET else None

    def out_of_time() -> bool:
        return bool(deadline and time.monotonic() > deadline)

    for cls in ALL:
        if not cfg.sources.get(cls.name, True) or cls.name in state.data.get("sources_off", []):
            continue
        if out_of_time():
            log.warning("Время прохода вышло — %s проверю в следующий раз", cls.title)
            continue
        src = cls(cfg)
        lots, error = await collect(src, state)
        if error and not lots:
            if dry_run:
                print(f"\n!!! {src.title}: {error}")
                continue
            streak = storage.source_failed(src.name, error)
            if streak == ALERT_AFTER_FAILS and tg.enabled:
                await tg.send(f"⚠️ {src.title} не отвечает {streak} раза подряд:\n<code>{error[:300]}</code>")
            continue
        if not dry_run:
            storage.source_ok(src.name, len(lots))

        first_run = src.name not in state.data["initialized_sources"]
        fresh = []
        for lot in lots:
            if not dry_run and storage.is_seen(lot):
                continue
            if EIS_ELSEWHERE and lot.eis_number and lot.source != "eis":
                continue  # закупку из ЕИС пришлёт исполнитель, который проверяет ЕИС (Mac)
            ok, _why = matcher.check(lot, state, cfg)
            if ok:
                fresh.append(lot)
            elif first_run and not dry_run:
                storage.mark(lot, notified=False)

        if dry_run:
            for lot in fresh:
                print("\n" + format_lot(lot) + f"\n🔗 {lot.url}")
            print(f"\n=== {src.title}: в выдаче {len(lots)}, подходит {len(fresh)}"
                  + (f", ошибка: {error}" if error else ""))
            continue

        to_send = fresh
        if first_run:
            # при первом запуске — только самые свежие, остальные просто запоминаем
            fresh.sort(key=lambda l: _date_key(l.published), reverse=True)
            to_send, rest = fresh[:FIRST_RUN_MAX], fresh[FIRST_RUN_MAX:]
            for lot in rest:
                storage.mark(lot, notified=False)
            if tg.enabled:
                tail = (f" Присылаю {len(to_send)} самых свежих, остальные {len(rest)} отмечены как известные."
                        if rest else (" Присылаю их." if to_send else ""))
                await tg.send(f"✅ {src.title}: сейчас {len(fresh)} актуальных подходящих торгов.{tail}")

        sent = 0
        for lot in to_send:
            if out_of_time():
                log.warning("%s: время вышло, %d лотов пришлю в следующий раз", src.title, len(to_send) - sent)
                break
            if tg.enabled and await tg.send_lot(lot):
                sent += 1
                storage.mark(lot, notified=True)
            elif not tg.enabled:
                print(format_lot(lot) + f"\n🔗 {lot.url}\n")
                storage.mark(lot, notified=True)

        if first_run:
            state.data["initialized_sources"].append(src.name)
            state.save()
        matched = len(fresh)
        log.warning("%s: в выдаче %d, новых подходящих %d, отправлено %d", src.title, len(lots), matched, sent)
        if AFTER_SOURCE:
            AFTER_SOURCE()


async def run_once(cfg, state, storage, tg, commands=True):
    """Один проход (для запуска по расписанию, например в GitHub Actions):
    сначала команды, пришедшие боту с прошлого раза, потом проверка площадок."""
    try:
        if not tg.enabled:
            log.warning("TELEGRAM_TOKEN не задан — проверку пропускаю, чтобы не потерять лоты")
            return
        if commands:
            await Bot(tg, state, storage, asyncio.Event()).process_pending()
        if not tg.chat_id:
            log.warning("TELEGRAM_CHAT_ID не задан — напишите боту /start и впишите ответ в секреты")
            return
        await run_cycle(cfg, state, storage, tg)
    finally:
        await tg.close()


async def main_loop(cfg, state, storage, tg):
    check_now = asyncio.Event()
    bot_task = None
    if tg.enabled:
        bot_task = asyncio.create_task(Bot(tg, state, storage, check_now).run())
        if tg.chat_id:
            await tg.send("▶️ Мониторинг торгов запущен. /help — список команд.\n"
                          f"Регионы: {regions.fmt(state.regions)}")
    else:
        log.warning("TELEGRAM_TOKEN не задан — лоты будут печататься в консоль")
    try:
        while True:
            try:
                await run_cycle(cfg, state, storage, tg)
            except Exception:
                log.exception("Ошибка в проходе")
            check_now.clear()
            try:
                await asyncio.wait_for(check_now.wait(), timeout=cfg.interval_minutes * 60)
            except asyncio.TimeoutError:
                pass
    finally:
        if bot_task:
            bot_task.cancel()
        await tg.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Мониторинг торгов → Telegram")
    ap.add_argument("--config", help="путь к config.yaml")
    ap.add_argument("--once", action="store_true", help="один проход и выход")
    ap.add_argument("--dry-run", action="store_true", help="только показать найденное (без Telegram и базы)")
    ap.add_argument("--no-commands", action="store_true",
                    help="не читать команды бота (их обрабатывает другой исполнитель)")
    ap.add_argument("--remote-state", help="URL общего state.json: брать оттуда слова, регионы, паузу")
    ap.add_argument("--only", help="опросить только эти площадки, через запятую: eis,torgi,fedresurs,b2b,fabrikant")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    # httpx пишет в лог полный адрес запроса — а в адресе Telegram API есть токен бота
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = load_config(args.config)
    if args.only:
        only = {s.strip() for s in args.only.split(",")}
        cfg.sources = {cls.name: cls.name in only for cls in ALL}
    state = State(cfg.data_dir / "state.json", cfg.seed)
    storage = Storage(cfg.data_dir / "lots.sqlite")
    if args.remote_state and state.merge_remote(args.remote_state):
        storage.forget_unsent()
        state.data["initialized_sources"] = []
        state.save()
    tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
    if args.dry_run:
        tg.token = ""

    if args.dry_run:
        asyncio.run(run_cycle(cfg, state, storage, tg, dry_run=True))
    elif args.once:
        asyncio.run(run_once(cfg, state, storage, tg, commands=not args.no_commands))
    else:
        asyncio.run(main_loop(cfg, state, storage, tg))


if __name__ == "__main__":
    main()
