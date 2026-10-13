"""Tiny SQLite helper (stdlib only)."""
from __future__ import annotations

import sqlite3
import threading
import time

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT, source TEXT,
  title TEXT, duration REAL,
  status TEXT, stage_label TEXT, progress REAL DEFAULT 0, error TEXT,
  path TEXT, audio_path TEXT, transcript_path TEXT, thumb_path TEXT,
  created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS clips(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER, video_title TEXT, rank INTEGER, score REAL,
  title TEXT, hook TEXT, reason TEXT,
  start REAL, end REAL, selected INTEGER DEFAULT 0,
  status TEXT DEFAULT 'candidate', progress REAL DEFAULT 0, error TEXT,
  path TEXT, thumb_path TEXT, width INTEGER, height INTEGER, size INTEGER,
  render_opts TEXT,
  yt_status TEXT, yt_progress REAL DEFAULT 0, yt_id TEXT, yt_error TEXT,
  created REAL, updated REAL
);
CREATE INDEX IF NOT EXISTS idx_clips_video ON clips(video_id);
"""


def init() -> None:
    global _conn
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(str(config.DATA_DIR / "clipforge.db"), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()


def q(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        cur = _conn.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]


def one(sql: str, args: tuple = ()) -> dict | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql: str, args: tuple = ()) -> int:
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur.lastrowid


def insert(table: str, **fields) -> int:
    now = time.time()
    fields.setdefault("created", now)
    fields.setdefault("updated", now)
    cols = ",".join(fields)
    marks = ",".join("?" for _ in fields)
    return ex(f"INSERT INTO {table}({cols}) VALUES({marks})", tuple(fields.values()))


def update(table: str, row_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated"] = time.time()
    sets = ",".join(f"{k}=?" for k in fields)
    ex(f"UPDATE {table} SET {sets} WHERE id=?", (*fields.values(), row_id))
