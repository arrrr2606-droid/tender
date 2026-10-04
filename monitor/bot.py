"""Команды Telegram-бота: управление ключевыми словами, минус-словами и регионами."""
from __future__ import annotations

import asyncio
import html
import logging
import os
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
/minus — список минус-слов (убрать — кнопкой)
/exclude слово — добавить минус-слово

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


# Постоянная клавиатура внизу чата: текст кнопки -> команда
BUTTONS = {
    "📋 Настройки": "/list",
    "📊 Статус": "/status",
    "➕ Слово": "/add",
    "➖ Слово": "/remove",
    "📍 Добавить регион": "/addregion",
    "🗺 Убрать регион": "/removeregion",
    "🚫 Минус-слова": "/minus",
    "🔄 Прислать заново": "/resend",
    "⏸ Пауза": "/pause",
    "▶️ Продолжить": "/resume",
}
KEYBOARD = {
    "keyboard": [
        [{"text": "📋 Настройки"}, {"text": "📊 Статус"}],
        [{"text": "➕ Слово"}, {"text": "➖ Слово"}],
        [{"text": "📍 Добавить регион"}, {"text": "🗺 Убрать регион"}],
        [{"text": "🚫 Минус-слова"}, {"text": "🔄 Прислать заново"}],
        [{"text": "⏸ Пауза"}, {"text": "▶️ Продолжить"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}
# Список в кнопке «Меню» у поля ввода
MENU_COMMANDS = [
    ("list", "Мои слова и регионы"),
    ("add", "Добавить ключевое слово"),
    ("remove", "Убрать ключевое слово"),
    ("addregion", "Добавить регион"),
    ("removeregion", "Убрать регион"),
    ("allregions", "Искать по всей России"),
    ("minus", "Минус-слова"),
    ("status", "Состояние площадок"),
    ("resend", "Прислать актуальные торги заново"),
    ("pause", "Пауза"),
    ("resume", "Продолжить"),
    ("help", "Справка"),
]
MENU_VERSION = 1

# команды, которым нужен текст: если его нет — просим прислать следующим сообщением
ASKS = {
    "/add": "Какие слова добавить? Напишите следующим сообщением, можно несколько через запятую.",
    "/exclude": "Какое минус-слово добавить? Напишите следующим сообщением.",
    "/addregion": "Какие регионы добавить? Нажмите федеральный округ ниже или напишите регионы "
                  "следующим сообщением через запятую, например: Татарстан, Башкортостан, Самарская область",
    "/kwregions": "Напишите следующим сообщением: слово: регионы\nНапример: погрузчик: Татарстан, Самарская",
}
DISTRICT_NAMES = {"цфо": "Центральный", "сзфо": "Северо-Западный", "юфо": "Южный", "скфо": "Северо-Кавказский",
                  "пфо": "Приволжский", "уфо": "Уральский", "сфо": "Сибирский", "дфо": "Дальневосточный"}


def _e(s) -> str:
    return html.escape(str(s or ""), quote=False)


def _split(arg: str) -> list:
    parts = (p.strip().strip(".!?\"«»'") .strip() for p in arg.replace(";", ",").replace("\n", ",").split(","))
    return [p for p in parts if p]


def _inline(buttons: list, per_row: int = 2) -> dict:
    """[(текст, callback_data)] -> inline-клавиатура."""
    rows = [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]
    return {"inline_keyboard": [[{"text": t, "callback_data": d[:64]} for t, d in row] for row in rows]}


class Bot:
    def __init__(self, tg: Telegram, state, storage, check_now: asyncio.Event):
        self.tg = tg
        self.state = state
        self.storage = storage
        self.check_now = check_now

    async def run(self):
        offset = int(self.state.data.get("tg_offset", 0))
        await self.setup_menu()
        while True:
            try:
                for upd in await self.tg.updates(offset):
                    offset = upd["update_id"] + 1
                    self.state.data["tg_offset"] = offset
                    self.state.save()
                    await self.dispatch(upd)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Ошибка обработки команд бота")
                await asyncio.sleep(10)

    async def process_pending(self):
        """Для режима «один проход» (GitHub Actions): разобрать команды, накопившиеся с прошлого запуска."""
        await self.setup_menu()
        offset = int(self.state.data.get("tg_offset", 0))
        res = await self.tg.call("getUpdates", offset=offset, timeout=0,
                                 allowed_updates=["message", "callback_query"])
        for upd in res.get("result") or []:
            offset = upd["update_id"] + 1
            try:
                await self.dispatch(upd)
            except Exception:
                log.exception("Ошибка обработки %s", upd.get("update_id"))
        if offset != self.state.data.get("tg_offset"):
            self.state.data["tg_offset"] = offset
            self.state.save()

    async def setup_menu(self):
        """Один раз заполнить кнопку «Меню» списком команд."""
        if self.state.data.get("menu_version") == MENU_VERSION:
            return
        res = await self.tg.call("setMyCommands",
                                 commands=[{"command": c, "description": d} for c, d in MENU_COMMANDS])
        if res.get("ok"):
            self.state.data["menu_version"] = MENU_VERSION
            self.state.save()

    async def dispatch(self, upd: dict):
        cb = upd.get("callback_query")
        if cb:
            await self.tg.call("answerCallbackQuery", callback_query_id=cb["id"])
            chat = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
            await self.on_button(chat, cb.get("data") or "")
            return
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        if text:
            await self.handle(str((msg.get("chat") or {}).get("id", "")), text)

    def _allowed(self, chat: str) -> bool:
        if chat != self.tg.chat_id:
            log.warning("Сообщение из чужого чата %s проигнорировано", chat)
            return False
        return True

    async def reply(self, result):
        """Ответ обработчика: строка (с основной клавиатурой) или (строка, inline-клавиатура)."""
        if not result:
            return
        if isinstance(result, tuple):
            await self.tg.send(result[0], markup=result[1])
        else:
            await self.tg.send(result, markup=KEYBOARD)

    async def handle(self, chat: str, text: str):
        if not self.tg.chat_id:
            await self.tg.send(
                f"Ваш chat id: <code>{chat}</code>\nДобавьте его как TELEGRAM_CHAT_ID в секреты репозитория на GitHub "
                "(Settings → Secrets and variables → Actions) или в файл .env на сервере.",
                chat_id=chat)
            return
        if not self._allowed(chat):
            return

        text = BUTTONS.get(text, text)
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()

        # «/addregion», а регионы — следующим сообщением
        if not text.startswith("/"):
            pending = self.state.data.pop("pending_cmd", None)
            self.state.save()
            if not pending:
                await self.reply("Выберите действие кнопками внизу или в «Меню». /help — справка.")
                return
            cmd, arg = pending, text
        elif not arg and cmd in ASKS:
            self.state.data["pending_cmd"] = cmd
            self.state.save()
            if cmd == "/addregion":
                await self.reply((ASKS[cmd], _inline(
                    [(f"{k.upper()} — {v}", f"ar:{k}") for k, v in DISTRICT_NAMES.items()])))
            else:
                await self.reply(ASKS[cmd])
            return
        else:
            self.state.data.pop("pending_cmd", None)

        handler = getattr(self, "cmd_" + cmd.lstrip("/"), None)
        result = handler(arg) if handler else HELP
        await self.reply(result)

    async def on_button(self, chat: str, data: str):
        """Нажатие inline-кнопки: rw:N — убрать слово, rr:код — убрать регион,
        rm:N — убрать минус-слово, ar:округ — добавить федеральный округ."""
        if not self._allowed(chat):
            return
        kind, _, val = data.partition(":")
        self.state.data.pop("pending_cmd", None)
        if kind == "rw" and val.isdigit() and int(val) < len(self.state.keywords):
            result = self.cmd_remove(self.state.keywords[int(val)]["word"])
        elif kind == "rr":
            result = self.cmd_removeregion(val)
        elif kind == "rm" and val.isdigit() and int(val) < len(self.state.minus_words):
            result = self.cmd_include(self.state.minus_words[int(val)])
        elif kind == "ar":
            result = self.cmd_addregion(val)
        else:
            result = "Кнопка устарела — откройте список заново."
        await self.reply(result)

    # --- ключевые слова ---
    def cmd_start(self, arg):
        return HELP + "\n\nКнопки быстрого доступа — внизу экрана, полный список — в «Меню»."

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
        if not arg:
            if not self.state.keywords:
                return "Ключевых слов нет. Добавьте: ➕ Слово"
            return ("Какое слово убрать? Нажмите на него:",
                    _inline([(f"❌ {k['word']}", f"rw:{i}") for i, k in enumerate(self.state.keywords)]))
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

    def cmd_minus(self, arg):
        mw = self.state.minus_words
        text = ("<b>Минус-слова</b> — лоты с ними в названии не приходят.\n"
                "Чтобы добавить, отправьте: <code>/exclude слово</code>\nЧтобы убрать — нажмите на слово:")
        if not mw:
            return "Минус-слов нет. Чтобы добавить, отправьте: <code>/exclude слово</code>"
        return text, _inline([(f"❌ {w}", f"rm:{i}") for i, w in enumerate(mw)], per_row=3)

    def cmd_include(self, arg):
        if not arg:
            return self.cmd_minus("")
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
        if not arg:
            if not self.state.regions:
                return "Регионы не выбраны — ищем по всей России."
            buttons = [(f"❌ {regions.name(c)}", f"rr:{c}") for c in self.state.regions]
            buttons.append(("🌍 Вся Россия (убрать все)", "rr:all"))
            return "Какой регион убрать? Нажмите на него:", _inline(buttons, per_row=1)
        if arg == "all":
            return self.cmd_allregions("")
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
        if os.environ.get("RELAY_MODE") == "1":
            return "Площадки проверяются автоматически раз в 3 часа — новые торги придут после ближайшей проверки."
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
