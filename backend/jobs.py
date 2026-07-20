"""Job store: one row per research job, with per-job storage on disk.

A job moves queued → collecting → analyzing → synthesizing → done (or error).
State is persisted so the frontend can poll and survive restarts."""

import json
import re
import sqlite3
import time
from pathlib import Path

from .config import settings

# Sequential human-readable job IDs. Job #1 is ABC0001, counting up to ABC9999,
# then the 3-letter prefix advances like an odometer (ABC -> ABD -> ... -> ABZ
# -> ACA -> ... -> ZZZ), and past ZZZ9999 it widens to a 4th letter (BAAA0001).
_ID_RE = re.compile(r"^([A-Z]{3,})(\d{4})$")
_PREFIX_BASE = 28  # positional base-26 value of "ABC" (A=0, B=1, C=2)


def _encode_id(n: int) -> str:
    """1-based ordinal -> job id (n=1 -> 'ABC0001')."""
    block, within = divmod(n - 1, 9999)
    idx, letters = _PREFIX_BASE + block, ""
    for _ in range(3):
        idx, r = divmod(idx, 26)
        letters = chr(65 + r) + letters
    while idx:  # overflow past ZZZ -> extra leading letters
        idx, r = divmod(idx, 26)
        letters = chr(65 + r) + letters
    return f"{letters}{within + 1:04d}"


def _decode_id(job_id: str) -> int | None:
    """job id -> 1-based ordinal, or None if it isn't one of ours."""
    m = _ID_RE.match(job_id)
    if not m:
        return None
    prefix, num = m.group(1), int(m.group(2))
    if not 1 <= num <= 9999:
        return None
    idx = 0
    for ch in prefix:
        idx = idx * 26 + (ord(ch) - 65)
    block = idx - _PREFIX_BASE
    return block * 9999 + num if block >= 0 else None

QUEUED = "queued"
COLLECTING = "collecting"
ANALYZING = "analyzing"
SYNTHESIZING = "synthesizing"
DONE = "done"
ERROR = "error"


def _connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(settings.db_path))


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            "id TEXT PRIMARY KEY, status TEXT NOT NULL, inputs TEXT NOT NULL, "
            "result TEXT, dossier_path TEXT, error TEXT, "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()


def job_dir(job_id: str) -> Path:
    d = settings.db_path.parent / "jobs" / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _next_id(conn: sqlite3.Connection) -> str:
    highest = 0
    for (jid,) in conn.execute("SELECT id FROM jobs").fetchall():
        n = _decode_id(jid)
        if n and n > highest:
            highest = n
    return _encode_id(highest + 1)


def create(inputs: dict) -> str:
    now = time.time()
    payload = json.dumps(inputs)
    conn = _connect()
    try:
        # Recompute+retry on the off chance two creates race for the same id.
        for _ in range(5):
            job_id = _next_id(conn)
            try:
                conn.execute(
                    "INSERT INTO jobs (id, status, inputs, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (job_id, QUEUED, payload, now, now),
                )
                conn.commit()
                return job_id
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("could not allocate a unique job id")
    finally:
        conn.close()


def update(job_id: str, **fields) -> None:
    if "result" in fields and fields["result"] is not None:
        fields["result"] = json.dumps(fields["result"])
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = _connect()
    try:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?",
                     (*fields.values(), job_id))
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row) -> dict:
    d = {
        "id": row[0], "status": row[1], "inputs": json.loads(row[2]),
        "result": json.loads(row[3]) if row[3] else None,
        "dossier_path": row[4], "error": row[5],
        "created_at": row[6], "updated_at": row[7],
    }
    return d


def get(job_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def recent(limit: int = 25) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]
