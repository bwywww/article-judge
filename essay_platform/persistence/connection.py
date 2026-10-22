"""SQLite connection state and common serialization helpers."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import sqlite3
import threading

_data_dir = ""
_db_path = ""
_ready = False
_state_lock = threading.RLock()

def configure(data_dir: str) -> str:
    global _data_dir, _db_path
    _data_dir = os.path.abspath(data_dir)
    _db_path = os.path.join(_data_dir, "app.db")
    return _db_path


def path() -> str:
    return _db_path


def is_ready() -> bool:
    return _ready


def _open(db_path: str | None = None, *, wal: bool = True) -> sqlite3.Connection:
    target = db_path or _db_path
    conn = sqlite3.connect(target, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextlib.contextmanager
def connection(*, write: bool = False):
    if not _ready:
        raise RuntimeError("SQLite storage is not initialized")
    conn = _open()
    try:
        if write:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if write:
            conn.commit()
    except Exception:
        if write:
            conn.rollback()
        raise
    finally:
        conn.close()


def _json(value, default=None):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _dump(value):
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _utc_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _current_academic_year():
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    start_year = today.year if today.month >= 9 else today.year - 1
    return {
        "id": f"ay{start_year}",
        "label": f"{start_year}-{start_year + 1}学年",
        "startYear": start_year,
    }

def data_dir() -> str:
    return _data_dir


def state_lock():
    return _state_lock


def mark_ready(value: bool = True):
    global _ready
    _ready = bool(value)


def reset_state():
    mark_ready(False)
