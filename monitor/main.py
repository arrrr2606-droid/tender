"""Точка входа: цикл опроса площадок + Telegram-бот.

python -m monitor.main                 # рабочий режим
python -m monitor.main --once --dry-run   # один проход, вывод в консоль, без Telegram и без записи в базу
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import matcher, regions
from .bot import Bot
from .config import State, load_config
from .sources import ALL
from .sources.base import SourceError
from .storage import Storage
from .telegram import Telegram, format_lot

log = logging.getLogger("monitor")
ALERT_AFTER_FAILS = 3


async def collect(src, state) -> tuple:
    """Опрашивает площадку по всем ключевым словам. -> (лоты, ошибка или None)"""
    found, error = {}, None
    async with src.client() as client:
        for kw in state.keywords:
            regs = kw["regions"] if kw.get("regions") is not None else state.regions
            try:
                for lot in await src.search(client, kw["word"], regs):
                    found.setdefault(lot.key, lot)
            except SourceError as e:
                error = str(e)
                log.warning("%s / «%s»: %s", src.title, kw["word"], e)
                if "отказала" in error:
                    break
            except Exception as e:  # изменилась вёрстка/формат — не роняем остальные площадки
                error = f"{type(e).__name__}: {e}"
                log.exception("%s / «%s»", src.title, kw["word"])
    return list(found.values()), error


async def run_cycle(cfg, state, storage, tg, dry_run=False):
    if state.paused:
        log.info("На паузе — пропускаю проход")
        return
    for cls in ALL:
        if not cfg.sources.get(cls.name, True):
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
        sent = matched = 0
        for lot in lots:
            if not dry_run and storage.is_seen(lot):
                continue
            ok, _why = matcher.check(lot, state, cfg)
            if dry_run:
                if ok:
                    matched += 1
                    print("\n" + format_lot(lot) + f"\n🔗 {lot.url}")
                continue
            if first_run:
                storage.mark(lot, notified=False)
                matched += ok
                continue
            if not ok:
                continue
            matched += 1
            if tg.enabled and await tg.send_lot(lot):
                sent += 1
                storage.mark(lot, notified=True)
            elif not tg.enabled:
                print(format_lot(lot) + f"\n🔗 {lot.url}\n")
                storage.mark(lot, notified=True)

        if dry_run:
            print(f"\n=== {src.title}: в выдаче {len(lots)}, подходит {matched}"
                  + (f", ошибка: {error}" if error else ""))
            continue
        if first_run:
            state.data["initialized_sources"].append(src.name)
            state.save()
            if tg.enabled:
                await tg.send(f"✅ {src.title} подключена. Сейчас там {matched} актуальных подходящих лотов — "
                              f"они отмечены как известные. Дальше буду присылать только новые.")
        log.info("%s: в выдаче %d, новых подходящих %d, отправлено %d", src.title, len(lots), matched, sent)


async def run_once(cfg, state, storage, tg):
    """Один проход (для запуска по расписанию, например в GitHub Actions):
    сначала команды, пришедшие боту с прошлого раза, потом проверка площадок."""
    try:
        if not tg.enabled:
            log.warning("TELEGRAM_TOKEN не задан — проверку пропускаю, чтобы не потерять лоты")
            return
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
    tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
    if args.dry_run:
        tg.token = ""

    if args.dry_run:
        asyncio.run(run_cycle(cfg, state, storage, tg, dry_run=True))
    elif args.once:
        asyncio.run(run_once(cfg, state, storage, tg))
    else:
        asyncio.run(main_loop(cfg, state, storage, tg))


if __name__ == "__main__":
    main()
