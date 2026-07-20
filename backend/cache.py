"""SQLite TTL cache with negative caching.

Rows store their `kind`; TTLs are looked up from settings at read time so config
changes apply to existing rows. Negative results (not found, comments disabled,
subscriptions private) are cached as payloads with a `__negative__` marker and
re-raised as the original typed error on cache hits — retries are quota-free.
"""

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
        "negative": settings.ttl_negative_s,
        "ig_fail": settings.ttl_negative_s,
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


async def cached_call(key: str, kind: str, produce, force: bool = False):
    """Cache-through wrapper: returns (payload, cached: bool, fetched_at_iso).

    `produce` is an async callable hitting the API. Negative-cacheable
    YouTubeErrors are stored and re-raised on subsequent hits.
    """
    from .youtube.client import YouTubeError  # deferred to avoid import cycle

    if not force:
        hit = get(key)
        if hit is not None:
            payload, fetched_at = hit
            if isinstance(payload, dict) and payload.get("__negative__"):
                raise YouTubeError(payload["status"], payload["reason"], payload["message"])
            return payload, True, fetched_at
    try:
        data = await produce()
    except YouTubeError as exc:
        if exc.negative:
            set(key, "negative", {
                "__negative__": True,
                "status": exc.status,
                "reason": exc.reason,
                "message": exc.message,
            })
        raise
    set(key, kind, data)
    return data, False, _iso(time.time())
