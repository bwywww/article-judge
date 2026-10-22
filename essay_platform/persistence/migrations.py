"""Atomic database creation, upgrade, backup and compatibility dataset access."""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import sqlite3

from essay_platform.persistence import connection as dbconn
from essay_platform.persistence.connection import _open, connection
from essay_platform.persistence.legacy_json import (
    _load_assignments, _load_classes, _load_config, _load_history, _load_users,
    _sync_assignments, _sync_classes, _sync_config, _sync_history, _sync_users,
)
from essay_platform.persistence.schema import DB_SCHEMA_VERSION, _create_schema

def _prepare_existing_database():
    """Upgrade an existing database through a verified copy, leaving the source untouched on failure."""
    source = _open()
    try:
        result = source.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"SQLite 完整性检查失败：{result}")
        current_version = int(source.execute("PRAGMA user_version").fetchone()[0] or 0)
        if current_version >= DB_SCHEMA_VERSION:
            _create_schema(source)
            source.execute(f"PRAGMA user_version={DB_SCHEMA_VERSION}")
            source.commit()
            return

        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(dbconn.data_dir(), "backups", f"schema_v{current_version}_to_v{DB_SCHEMA_VERSION}_{stamp}")
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, "app.db")
        backup_conn = sqlite3.connect(backup_path)
        try:
            source.backup(backup_conn)
        finally:
            backup_conn.close()

        temp_path = dbconn.path() + ".upgrading"
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(temp_path + suffix)
        temp_conn = sqlite3.connect(temp_path)
        try:
            source.backup(temp_conn)
        finally:
            temp_conn.close()
    finally:
        source.close()

    migrated = _open(temp_path, wal=False)
    try:
        _create_schema(migrated)
        migrated.execute(f"PRAGMA user_version={DB_SCHEMA_VERSION}")
        migrated.commit()
        result = migrated.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"SQLite 升级后完整性检查失败：{result}")
    except Exception:
        migrated.close()
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(temp_path + suffix)
        raise
    else:
        migrated.close()
    for suffix in ("-wal", "-shm"):
        with contextlib.suppress(FileNotFoundError):
            os.remove(dbconn.path() + suffix)
    os.replace(temp_path, dbconn.path())


def ensure_database(source_data: dict[str, dict]) -> dict:
    """Open an existing DB or atomically import validated JSON-shaped data."""
    if not dbconn.path():
        raise RuntimeError("database.configure() must be called first")
    os.makedirs(dbconn.data_dir(), exist_ok=True)
    with dbconn.state_lock():
        if os.path.exists(dbconn.path()):
            _prepare_existing_database()
            dbconn.mark_ready()
            return {"created": False, "path": dbconn.path()}

        temp_path = dbconn.path() + ".migrating"
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(temp_path + suffix)
            except FileNotFoundError:
                pass
        conn = _open(temp_path, wal=False)
        try:
            conn.execute("BEGIN IMMEDIATE")
            _create_schema(conn)
            _sync_users(conn, source_data.get("users", {"users": []}))
            _sync_classes(conn, source_data.get("classes", {"classes": []}))
            _sync_assignments(conn, source_data.get("assignments", {"assignments": []}))
            _sync_history(conn, source_data.get("history", {"items": []}))
            _sync_config(conn, source_data.get("config", {}))
            _create_schema(conn)
            counts = {
                "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "classes": conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0],
                "assignments": conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0],
                "essays": conn.execute("SELECT COUNT(*) FROM essays").fetchone()[0],
                "history": conn.execute("SELECT COUNT(*) FROM history").fetchone()[0],
            }
            expected = {
                "users": len(source_data.get("users", {}).get("users", [])),
                "classes": len(source_data.get("classes", {}).get("classes", [])),
                "assignments": len(source_data.get("assignments", {}).get("assignments", [])),
                "essays": sum(len(a.get("essays", [])) for a in source_data.get("assignments", {}).get("assignments", [])),
                "history": len(source_data.get("history", {}).get("items", [])),
            }
            if counts != expected:
                raise RuntimeError(f"SQLite 迁移数量校验失败：expected={expected}, actual={counts}")
            conn.commit()
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("SQLite 迁移后完整性检查失败")
        except Exception:
            conn.rollback()
            conn.close()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(temp_path + suffix)
                except FileNotFoundError:
                    pass
            raise
        else:
            conn.close()
        os.replace(temp_path, dbconn.path())
        dbconn.mark_ready()
        with connection(write=True) as final_conn:
            final_conn.execute(f"PRAGMA user_version={DB_SCHEMA_VERSION}")
        return {"created": True, "path": dbconn.path(), "counts": counts}


def load_dataset(kind: str):
    with connection() as conn:
        if kind == "users":
            return _load_users(conn)
        if kind == "classes":
            return _load_classes(conn)
        if kind == "assignments":
            return _load_assignments(conn)
        if kind == "history":
            return _load_history(conn)
        if kind == "config":
            return _load_config(conn)
    raise KeyError(kind)


def save_dataset(kind: str, data):
    with connection(write=True) as conn:
        if kind == "users":
            return _sync_users(conn, data)
        if kind == "classes":
            return _sync_classes(conn, data)
        if kind == "assignments":
            return _sync_assignments(conn, data)
        if kind == "history":
            return _sync_history(conn, data)
        if kind == "config":
            return _sync_config(conn, data)
    raise KeyError(kind)


def save_users_and_classes(users_data, classes_data):
    """Commit account fields and class membership as one unit."""
    with connection(write=True) as conn:
        _sync_users(conn, users_data)
        _sync_classes(conn, classes_data)


def reset_business_data(users_data, classes_data, assignments_data, history_data):
    with connection(write=True) as conn:
        for table in ("submission_returns", "assignment_showcase", "assignment_insights",
                      "grading_jobs", "history", "submission_versions", "essays", "assignments",
                      "student_enrollments",
                      "class_teachers", "class_students", "sessions",
                      "login_attempts", "login_locks", "audit_logs", "users", "classes",
                      "academic_years"):
            conn.execute(f"DELETE FROM {table}")
        _create_schema(conn)
        _sync_users(conn, users_data)
        _sync_classes(conn, classes_data)
        _sync_assignments(conn, assignments_data)
        _sync_history(conn, history_data)


def backup_database(destination: str):
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with connection() as source:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()

def reset_state_for_tests():
    with dbconn.state_lock():
        dbconn.reset_state()
