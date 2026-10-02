"""Команды Telegram-бота: управление ключевыми словами, минус-словами и регионами."""
from __future__ import annotations

import asyncio
import html
import logging
import time

from . import regions
from .telegram import SOURCE_TITLES, Telegram

log = logging.getLogger(__name__)

HELP = """<b>Мониторинг торгов</b>

<b>Ключевые слова</b>
/list — что ищем сейчас
/add слово — добавить (можно несколько через запятую)
/remove слово — убрать

<b>Минус-слова</b> (лот с таким словом в названии не придёт)
/exclude слово — добавить минус-слово
/include слово — убрать минус-слово

<b>Регионы</b>
/regions — текущие регионы
/addregion Татарстан, Самарская область — добавить (понимает МО, СПб, ХМАО, коды, округа ЦФО/ПФО/УФО…)
/removeregion Татарстан — убрать
/allregions — искать по всей России
/kwregions погрузчик: Татарстан, Самарская — свои регионы для одного слова
/kwregions погрузчик: общие — вернуть слову общие регионы

<b>Прочее</b>
/status — состояние площадок и статистика
/check — проверить площадки прямо сейчас
/resend — прислать актуальные торги заново (до 40 самых свежих с каждой площадки; уже присланные не повторяются)
/pause и /resume — приостановить и возобновить уведомления"""


def _e(s) -> str:
    return html.escape(str(s or ""), quote=False)


def _split(arg: str) -> list:
    return [p.strip() for p in arg.replace(";", ",").split(",") if p.strip()]


class Bot:
    def __init__(self, tg: Telegram, state, storage, check_now: asyncio.Event):
        self.tg = tg
        self.state = state
        self.storage = storage
        self.check_now = check_now

    async def run(self):
        offset = 0
        while True:
            try:
                for upd in await self.tg.updates(offset):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    text = (msg.get("text") or "").strip()
                    chat = str((msg.get("chat") or {}).get("id", ""))
                    if text.startswith("/"):
                        await self.handle(chat, text)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Ошибка обработки команд бота")
                await asyncio.sleep(10)

    async def process_pending(self):
        """Для режима «один проход» (GitHub Actions): разобрать команды, накопившиеся с прошлого запуска."""
        offset = int(self.state.data.get("tg_offset", 0))
        res = await self.tg.call("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])
        for upd in res.get("result") or []:
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            text = (msg.get("text") or "").strip()
            if text.startswith("/"):
                try:
                    await self.handle(str((msg.get("chat") or {}).get("id", "")), text)
                except Exception:
                    log.exception("Ошибка команды %s", text)
        if offset != self.state.data.get("tg_offset"):
            self.state.data["tg_offset"] = offset
            self.state.save()

    async def handle(self, chat: str, text: str):
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()

        if not self.tg.chat_id:
            await self.tg.send(
                f"Ваш chat id: <code>{chat}</code>\nДобавьте его как TELEGRAM_CHAT_ID в секреты репозитория на GitHub "
                "(Settings → Secrets and variables → Actions) или в файл .env на сервере.",
                chat_id=chat)
            return
        if chat != self.tg.chat_id:
            log.warning("Команда из чужого чата %s проигнорирована", chat)
            return

        handler = getattr(self, "cmd_" + cmd.lstrip("/"), None)
        reply = handler(arg) if handler else HELP
        if asyncio.iscoroutine(reply):
            reply = await reply
        if reply:
            await self.tg.send(reply)

    # --- ключевые слова ---
    def cmd_start(self, arg):
        return HELP

    cmd_help = cmd_start

    def cmd_list(self, arg):
        lines = ["<b>Ключевые слова:</b>"]
        for k in self.state.keywords:
            own = "" if k.get("regions") is None else f" — <i>{_e(regions.fmt(k['regions']))}</i>"
            lines.append(f"• {_e(k['word'])}{own}")
        lines.append("")
        lines.append(f"<b>Минус-слова:</b> {_e(', '.join(self.state.minus_words)) or '—'}")
        lines.append(f"<b>Регионы:</b> {_e(regions.fmt(self.state.regions))}")
        if self.state.paused:
            lines.append("\n⏸ Уведомления на паузе (/resume)")
        return "\n".join(lines)

    def cmd_add(self, arg):
        words = _split(arg)
        if not words:
            return "Напишите слово: /add бульдозер"
        added = []
        for w in words:
            if not self.state.find_keyword(w):
                self.state.keywords.append({"word": w, "regions": None})
                added.append(w)
        self.state.save()
        return f"Добавлено: {_e(', '.join(added)) or 'ничего нового'}"

    def cmd_remove(self, arg):
        removed = []
        for w in _split(arg):
            k = self.state.find_keyword(w)
            if k:
                self.state.keywords.remove(k)
                removed.append(k["word"])
        self.state.save()
        return f"Удалено: {_e(', '.join(removed))}" if removed else "Таких слов нет. /list — текущий список"

    def cmd_exclude(self, arg):
        words = _split(arg)
        if not words:
            return "Напишите минус-слово: /exclude аренда"
        mw = self.state.minus_words
        mw.extend(w for w in words if w.lower() not in (x.lower() for x in mw))
        self.state.save()
        return f"Минус-слова: {_e(', '.join(mw))}"

    def cmd_include(self, arg):
        drop = {w.lower() for w in _split(arg)}
        self.state.data["minus_words"] = [w for w in self.state.minus_words if w.lower() not in drop]
        self.state.save()
        return f"Минус-слова: {_e(', '.join(self.state.minus_words)) or '—'}"

    # --- регионы ---
    def cmd_regions(self, arg):
        own = [k for k in self.state.keywords if k.get("regions") is not None]
        lines = [f"<b>Регионы:</b> {_e(regions.fmt(self.state.regions))}"]
        for k in own:
            lines.append(f"• {_e(k['word'])}: {_e(regions.fmt(k['regions']))}")
        return "\n".join(lines)

    def cmd_addregion(self, arg):
        codes, bad = regions.resolve_many(_split(arg))
        if not codes and not bad:
            return "Напишите регион: /addregion Татарстан"
        for c in codes:
            if c not in self.state.regions:
                self.state.regions.append(c)
        self.state.save()
        msg = f"<b>Регионы:</b> {_e(regions.fmt(self.state.regions))}"
        if bad:
            msg += f"\n⚠️ Не распознал: {_e(', '.join(bad))}"
        return msg

    def cmd_removeregion(self, arg):
        codes, bad = regions.resolve_many(_split(arg))
        self.state.data["regions"] = [c for c in self.state.regions if c not in codes]
        self.state.save()
        msg = f"<b>Регионы:</b> {_e(regions.fmt(self.state.regions))}"
        if bad:
            msg += f"\n⚠️ Не распознал: {_e(', '.join(bad))}"
        return msg

    def cmd_allregions(self, arg):
        self.state.data["regions"] = []
        self.state.save()
        return "Ищем по всей России."

    def cmd_kwregions(self, arg):
        word, sep, rest = arg.partition(":")
        k = self.state.find_keyword(word)
        if not sep or not k:
            return "Формат: /kwregions погрузчик: Татарстан, Самарская\n(слово должно быть в /list)"
        if rest.strip().lower() in ("общие", "все", "сброс", ""):
            k["regions"] = None
            self.state.save()
            return f"«{_e(k['word'])}» — общие регионы ({_e(regions.fmt(self.state.regions))})"
        if rest.strip().lower() in ("россия", "вся россия"):
            k["regions"] = []
            self.state.save()
            return f"«{_e(k['word'])}» — вся Россия"
        codes, bad = regions.resolve_many(_split(rest))
        if bad:
            return f"⚠️ Не распознал: {_e(', '.join(bad))}"
        k["regions"] = codes
        self.state.save()
        return f"«{_e(k['word'])}» — {_e(regions.fmt(codes))}"

    # --- прочее ---
    def cmd_status(self, arg):
        total, sent, day = self.storage.stats()
        lines = [f"Отправлено за сутки: <b>{day}</b>, всего: {sent}. Известных лотов: {total}.",
                 "⏸ На паузе" if self.state.paused else "▶️ Работает", ""]
        for source, last_ok, err, streak, count in self.storage.health():
            name = SOURCE_TITLES.get(source, source)
            ago = f"{int((time.time() - last_ok) / 60)} мин назад" if last_ok else "ещё не было"
            mark = "✅" if streak == 0 else "⚠️"
            line = f"{mark} {name}: успешно {ago}, лотов в выдаче {count}"
            if streak:
                line += f"\n    ошибок подряд: {streak} — {_e(err)}"
            lines.append(line)
        return "\n".join(lines)

    def cmd_check(self, arg):
        self.check_now.set()
        return "Запускаю проверку площадок…"

    def cmd_resend(self, arg):
        n = self.storage.forget_unsent()
        self.state.data["initialized_sources"] = []
        self.state.data["resend_seq"] = self.state.data.get("resend_seq", 0) + 1  # сигнал для Mac
        self.state.save()
        self.check_now.set()
        return (f"Хорошо. При следующей проверке пришлю актуальные торги по текущим словам и регионам "
                f"(до 40 самых свежих с каждой площадки). Забыто непрочитанных: {n}.")

    def cmd_pause(self, arg):
        self.state.data["paused"] = True
        self.state.save()
        return "⏸ Уведомления приостановлены. /resume — возобновить."

    def cmd_resume(self, arg):
        self.state.data["paused"] = False
        self.state.save()
        return "▶️ Уведомления возобновлены."
