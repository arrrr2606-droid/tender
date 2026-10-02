"""SQLite: какие лоты уже видели (дедупликация) и статистика по источникам."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .models import Lot

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    key        TEXT PRIMARY KEY,      -- source:id
    eis_number TEXT,
    title      TEXT,
    url        TEXT,
    region     TEXT,
    notified   INTEGER NOT NULL,      -- 1 = отправлен в Telegram, 0 = просто отмечен
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS seen_eis ON seen(eis_number);
CREATE TABLE IF NOT EXISTS source_health (
    source      TEXT PRIMARY KEY,
    last_ok     REAL,
    last_error  TEXT,
    fail_streak INTEGER NOT NULL DEFAULT 0,
    last_count  INTEGER NOT NULL DEFAULT 0
);
"""


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def is_seen(self, lot: Lot) -> bool:
        if self.db.execute("SELECT 1 FROM seen WHERE key=?", (lot.key,)).fetchone():
            return True
        # тот же номер ЕИС уже пришёл с другой площадки — не дублируем
        if lot.eis_number and self.db.execute(
            "SELECT 1 FROM seen WHERE eis_number=?", (lot.eis_number,)
        ).fetchone():
            return True
        return False

    def mark(self, lot: Lot, notified: bool):
        self.db.execute(
            "INSERT OR IGNORE INTO seen(key, eis_number, title, url, region, notified, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (lot.key, lot.eis_number or None, lot.title[:500], lot.url, lot.region_code,
             int(notified), time.time()),
        )
        self.db.commit()

    def source_ok(self, source: str, count: int):
        self.db.execute(
            "INSERT INTO source_health(source, last_ok, fail_streak, last_count) VALUES (?,?,0,?) "
            "ON CONFLICT(source) DO UPDATE SET last_ok=excluded.last_ok, fail_streak=0, "
            "last_count=excluded.last_count, last_error=NULL",
            (source, time.time(), count),
        )
        self.db.commit()

    def source_failed(self, source: str, error: str) -> int:
        self.db.execute(
            "INSERT INTO source_health(source, last_error, fail_streak) VALUES (?,?,1) "
            "ON CONFLICT(source) DO UPDATE SET last_error=excluded.last_error, "
            "fail_streak=source_health.fail_streak+1",
            (source, error[:500]),
        )
        self.db.commit()
        return self.db.execute(
            "SELECT fail_streak FROM source_health WHERE source=?", (source,)
        ).fetchone()[0]

    def health(self) -> list:
        return self.db.execute(
            "SELECT source, last_ok, last_error, fail_streak, last_count FROM source_health ORDER BY source"
        ).fetchall()

    def stats(self) -> tuple:
        total = self.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
        sent = self.db.execute("SELECT COUNT(*) FROM seen WHERE notified=1").fetchone()[0]
        day = self.db.execute(
            "SELECT COUNT(*) FROM seen WHERE notified=1 AND created_at>?", (time.time() - 86400,)
        ).fetchone()[0]
        return total, sent, day
