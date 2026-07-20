"""Persistent daily quota ledger. YouTube grants 10,000 units/day, reset at
midnight Pacific — the ledger is keyed by Pacific date so it rolls over exactly
when Google's does."""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import settings

PACIFIC = ZoneInfo("America/Los_Angeles")

# Per-call unit costs by API resource; everything not listed is a 1-unit read.
COSTS = {"captions": 50, "search": 100}


class QuotaExhausted(Exception):
    pass


def cost_of(resource: str) -> int:
    return COSTS.get(resource, 1)


def _connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(settings.db_path))


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS quota (day TEXT PRIMARY KEY, units INTEGER NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()


def _today() -> str:
    return datetime.now(PACIFIC).date().isoformat()


def used_today() -> int:
    conn = _connect()
    try:
        row = conn.execute("SELECT units FROM quota WHERE day = ?", (_today(),)).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def charge(resource: str) -> int:
    cost = cost_of(resource)
    used = used_today()
    if used + cost > settings.quota_soft_stop:
        raise QuotaExhausted(
            f"Daily quota budget exhausted ({used} used, soft stop "
            f"{settings.quota_soft_stop}/{settings.quota_daily_budget}). "
            "Cached data is still served; fresh fetches resume at midnight Pacific."
        )
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO quota (day, units) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET units = units + excluded.units",
            (_today(), cost),
        )
        conn.commit()
    finally:
        conn.close()
    return cost


def status() -> dict:
    used = used_today()
    return {
        "date_pacific": _today(),
        "used": used,
        "budget": settings.quota_daily_budget,
        "soft_stop": settings.quota_soft_stop,
        "remaining": max(0, settings.quota_soft_stop - used),
    }
