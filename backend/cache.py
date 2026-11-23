"""SQLite TTL cache.

Rows store their `kind`; TTLs are looked up from settings at read time so config
changes apply to existing rows."""

import json
import sqlite3
import time
from datetime import datetime, timezone

from .config import settings


def _connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(settings.db_path))


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            "key TEXT PRIMARY KEY, kind TEXT NOT NULL, "
            "payload TEXT NOT NULL, fetched_at REAL NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()


def ttl_for(kind: str) -> int:
    return {
        "snapshot": settings.ttl_snapshot_s,
        "list": settings.ttl_list_s,
        "captions": settings.ttl_captions_s,
        "static": settings.ttl_static_s,
    }[kind]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def get(key: str):
    """Return (payload, fetched_at_iso) if present and fresh, else None."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT kind, payload, fetched_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    kind, payload, fetched_at = row
    if time.time() - fetched_at > ttl_for(kind):
        return None
    return json.loads(payload), _iso(fetched_at)


def set(key: str, kind: str, payload) -> None:
    ttl_for(kind)  # fail fast on unknown kinds
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, kind, payload, fetched_at) VALUES (?, ?, ?, ?)",
            (key, kind, json.dumps(payload), time.time()),
        )
        conn.commit()
    finally:
        conn.close()
