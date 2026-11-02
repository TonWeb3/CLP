"""Tiny SQLite helper (stdlib only)."""
from __future__ import annotations

import shutil
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
  tiktok_status TEXT, tiktok_progress REAL DEFAULT 0, tiktok_id TEXT, tiktok_error TEXT,
  uploads_json TEXT DEFAULT '{}',
  social_copy TEXT,
  created REAL, updated REAL
);
CREATE INDEX IF NOT EXISTS idx_clips_video ON clips(video_id);

CREATE TABLE IF NOT EXISTS social_accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  name TEXT NOT NULL,
  client_id TEXT DEFAULT '',
  client_secret TEXT DEFAULT '',
  privacy TEXT DEFAULT '',
  add_shorts_tag INTEGER DEFAULT 1,
  created REAL,
  updated REAL
);
CREATE INDEX IF NOT EXISTS idx_social_platform ON social_accounts(platform);

CREATE TABLE IF NOT EXISTS scheduled_uploads(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  clip_id INTEGER,
  platforms TEXT,
  accounts TEXT DEFAULT '[]',
  scheduled_time REAL,
  status TEXT DEFAULT 'pending',
  options TEXT,
  results TEXT,
  error TEXT,
  created REAL,
  updated REAL
);
CREATE INDEX IF NOT EXISTS idx_sched_status_time ON scheduled_uploads(status, scheduled_time);
"""


def init() -> None:
    global _conn
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "tokens").mkdir(parents=True, exist_ok=True)
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(str(config.DATA_DIR / "clipforge.db"), check_same_thread=False, timeout=30.0)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.execute("PRAGMA synchronous=NORMAL;")
        _conn.executescript(SCHEMA)

        # Migrations for existing databases
        clip_cols = {r["name"] for r in _conn.execute("PRAGMA table_info(clips)").fetchall()}
        for col, ctype in [
            ("tiktok_status", "TEXT"),
            ("tiktok_progress", "REAL DEFAULT 0"),
            ("tiktok_id", "TEXT"),
            ("tiktok_error", "TEXT"),
            ("uploads_json", "TEXT DEFAULT '{}'"),
            ("social_copy", "TEXT"),
        ]:
            if col not in clip_cols:
                _conn.execute(f"ALTER TABLE clips ADD COLUMN {col} {ctype};")

        sched_cols = {r["name"] for r in _conn.execute("PRAGMA table_info(scheduled_uploads)").fetchall()}
        if "accounts" not in sched_cols:
            _conn.execute("ALTER TABLE scheduled_uploads ADD COLUMN accounts TEXT DEFAULT '[]';")

        # Seed or migrate default accounts if none exist for a platform
        yt_count = _conn.execute("SELECT COUNT(*) AS n FROM social_accounts WHERE platform='youtube'").fetchone()["n"]
        if yt_count == 0:
            cfg = config.get()
            now = time.time()
            old_yt_token = config.DATA_DIR / "youtube_token.json"
            cur = _conn.execute(
                "INSERT INTO social_accounts(platform, name, client_id, client_secret, privacy, add_shorts_tag, created, updated) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "youtube",
                    "YouTube #1",
                    cfg.get("yt_client_id", ""),
                    cfg.get("yt_client_secret", ""),
                    cfg.get("yt_privacy", "private"),
                    1 if cfg.get("yt_add_shorts_tag", True) else 0,
                    now,
                    now,
                ),
            )
            if old_yt_token.exists():
                shutil.copy2(old_yt_token, config.DATA_DIR / "tokens" / f"youtube_{cur.lastrowid}.json")

        tt_count = _conn.execute("SELECT COUNT(*) AS n FROM social_accounts WHERE platform='tiktok'").fetchone()["n"]
        if tt_count == 0:
            cfg = config.get()
            now = time.time()
            old_tt_token = config.DATA_DIR / "tiktok_token.json"
            cur = _conn.execute(
                "INSERT INTO social_accounts(platform, name, client_id, client_secret, privacy, add_shorts_tag, created, updated) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "tiktok",
                    "TikTok #1",
                    cfg.get("tiktok_client_key", ""),
                    cfg.get("tiktok_client_secret", ""),
                    cfg.get("tiktok_privacy", "PUBLIC_TO_EVERYONE"),
                    0,
                    now,
                    now,
                ),
            )
            if old_tt_token.exists():
                shutil.copy2(old_tt_token, config.DATA_DIR / "tokens" / f"tiktok_{cur.lastrowid}.json")

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
