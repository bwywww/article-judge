"""SQLite persistence for the auth domain."""

from __future__ import annotations

import time
from essay_platform.persistence.storage import connection, _load_user_row

def create_session(token_hash: str, username: str, expires_at: float,
                   ip_address: str = "", user_agent: str = ""):
    now = time.time()
    with connection(write=True) as conn:
        conn.execute("""
            INSERT INTO sessions(token_hash,username,created_at,expires_at,last_seen_at,ip_address,user_agent)
            VALUES(?,?,?,?,?,?,?)
        """, (token_hash, username, now, expires_at, now,
              (ip_address or "")[:128], (user_agent or "")[:500]))


def resolve_session(token_hash: str):
    if not token_hash:
        return None
    now = time.time()
    with connection() as conn:
        row = conn.execute("""
            SELECT u.* FROM sessions s
            JOIN users u ON u.username=s.username
            WHERE s.token_hash=? AND s.expires_at>? AND u.active=1
        """, (token_hash, now)).fetchone()
        return _load_user_row(row) if row else None


def revoke_session(token_hash: str):
    if not token_hash:
        return
    with connection(write=True) as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


def revoke_user_sessions(username: str):
    with connection(write=True) as conn:
        conn.execute("DELETE FROM sessions WHERE username=?", (username,))


def login_lock_remaining(username: str, ip_address: str, *, now: float | None = None) -> int:
    current = time.time() if now is None else now
    with connection() as conn:
        row = conn.execute(
            "SELECT locked_until FROM login_locks WHERE username=? AND ip_address=?",
            (username, ip_address)).fetchone()
    if not row or row["locked_until"] <= current:
        return 0
    return max(1, int(row["locked_until"] - current + 0.999))


def record_login_failure(username: str, ip_address: str, *, window_seconds: int = 600,
                         max_failures: int = 5, lock_seconds: int = 900,
                         now: float | None = None) -> int:
    current = time.time() if now is None else now
    with connection(write=True) as conn:
        conn.execute("DELETE FROM login_attempts WHERE failed_at<?", (current - 86400,))
        conn.execute("INSERT INTO login_attempts(username,ip_address,failed_at) VALUES(?,?,?)",
                     (username, ip_address, current))
        count = conn.execute("""
            SELECT COUNT(*) FROM login_attempts
            WHERE username=? AND ip_address=? AND failed_at>=?
        """, (username, ip_address, current - window_seconds)).fetchone()[0]
        if count < max_failures:
            return 0
        locked_until = current + lock_seconds
        conn.execute("""
            INSERT INTO login_locks(username,ip_address,locked_until) VALUES(?,?,?)
            ON CONFLICT(username,ip_address) DO UPDATE SET locked_until=excluded.locked_until
        """, (username, ip_address, locked_until))
        return lock_seconds


def clear_login_failures(username: str, ip_address: str):
    with connection(write=True) as conn:
        conn.execute("DELETE FROM login_attempts WHERE username=? AND ip_address=?",
                     (username, ip_address))
        conn.execute("DELETE FROM login_locks WHERE username=? AND ip_address=?",
                     (username, ip_address))


def cleanup_auth_state():
    now = time.time()
    with connection(write=True) as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
        conn.execute("DELETE FROM login_attempts WHERE failed_at<?", (now - 86400,))
        conn.execute("DELETE FROM login_locks WHERE locked_until<=?", (now,))
