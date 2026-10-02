"""Конфиг (config.yaml, неизменяемый) и состояние (data/state.json, меняется командами бота)."""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from . import regions

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger(__name__)


@dataclass
class Config:
    interval_minutes: int = 30
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    strict_regions: bool = False
    max_age_days: int = 90
    sources: dict = field(default_factory=dict)
    ca_bundle: Optional[str] = None
    insecure_gov: bool = False
    seed: dict = field(default_factory=dict)  # keywords/minus_words/regions для первого запуска
    data_dir: Path = ROOT / "data"
    telegram_token: str = ""
    telegram_chat_id: str = ""


def load_config(path: Optional[str] = None) -> Config:
    path = Path(path or os.environ.get("MONITOR_CONFIG", ROOT / "config.yaml"))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tls = raw.get("tls") or {}
    return Config(
        interval_minutes=int(raw.get("interval_minutes", 30)),
        min_price=raw.get("min_price"),
        max_price=raw.get("max_price"),
        strict_regions=bool(raw.get("strict_regions", False)),
        max_age_days=int(raw.get("max_age_days", 90)),
        sources=raw.get("sources") or {},
        ca_bundle=str(ROOT / tls["ca_bundle"]) if tls.get("ca_bundle") else None,
        insecure_gov=bool(tls.get("insecure_gov", False)) or os.environ.get("INSECURE_GOV_TLS") == "1",
        seed={k: raw.get(k) or [] for k in ("keywords", "minus_words", "regions")},
        data_dir=Path(os.environ.get("MONITOR_DATA", ROOT / "data")),
        telegram_token=os.environ.get("TELEGRAM_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )


def _norm_keyword(item) -> dict:
    if isinstance(item, str):
        return {"word": item.strip(), "regions": None}
    word = str(item.get("word", "")).strip()
    regs = item.get("regions")
    if regs is not None:
        regs, bad = regions.resolve_many(regs)
        if bad:
            raise ValueError(f"Не распознаны регионы для «{word}»: {', '.join(bad)}")
    return {"word": word, "regions": regs}


class State:
    """Ключевые слова, минус-слова, регионы, пауза. Хранится в JSON, правится из бота."""

    def __init__(self, path: Path, seed: dict):
        self.path = path
        self._lock = threading.Lock()
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            codes, bad = regions.resolve_many(seed.get("regions") or [])
            if bad:
                raise ValueError(f"Не распознаны регионы в config.yaml: {', '.join(bad)}")
            self.data = {
                "keywords": [_norm_keyword(k) for k in seed.get("keywords") or []],
                "minus_words": [str(w) for w in seed.get("minus_words") or []],
                "regions": codes,
                "paused": False,
                "initialized_sources": [],
            }
            self.save()

    SHARED_KEYS = ("keywords", "minus_words", "regions", "paused")

    def merge_remote(self, url: str) -> Optional[bool]:
        """Подтянуть слова/регионы/паузу из общего состояния (ветка state на GitHub).

        Нужно второму исполнителю (Mac), который проверяет часть площадок: команды бота
        обрабатывает GitHub, а Mac только читает результат. -> True, если запрошен /resend.
        """
        import httpx

        try:
            remote = httpx.get(url, timeout=20, follow_redirects=True).json()
        except Exception as e:
            log.warning("Не удалось загрузить общие настройки (%s) — работаю с локальными", e)
            return None
        for key in self.SHARED_KEYS:
            if key in remote:
                self.data[key] = remote[key]
        resend = remote.get("resend_seq", 0) != self.data.get("resend_seq", 0)
        self.data["resend_seq"] = remote.get("resend_seq", 0)
        self.save()
        return resend

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    # удобные геттеры
    @property
    def keywords(self) -> list:
        return self.data["keywords"]

    @property
    def minus_words(self) -> list:
        return self.data["minus_words"]

    @property
    def regions(self) -> list:
        return self.data["regions"]

    @property
    def paused(self) -> bool:
        return self.data.get("paused", False)

    def find_keyword(self, word: str) -> Optional[dict]:
        w = word.strip().lower()
        return next((k for k in self.keywords if k["word"].lower() == w), None)
