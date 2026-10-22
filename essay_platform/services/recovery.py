"""Constrained backup inspection and restart-time SQLite restoration."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3


BACKUP_ID_RE = re.compile(r"[A-Za-z0-9_-]{3,120}")


class RecoveryError(ValueError):
    def __init__(self, message: str, *, code: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _backup_path(backups_dir: str, backup_id: str):
    if not BACKUP_ID_RE.fullmatch(str(backup_id or "")):
        raise RecoveryError("备份编号无效", code="invalid_backup")
    root = os.path.realpath(backups_dir)
    path = os.path.realpath(os.path.join(root, backup_id, "app.db"))
    if os.path.commonpath((root, path)) != root or not os.path.isfile(path):
        raise RecoveryError("备份不存在", code="backup_not_found", status=404)
    return path


def inspect_database(path: str):
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RecoveryError(f"备份完整性检查失败：{integrity}", code="invalid_database")
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            required = {"users", "classes", "assignments", "essays", "history"}
            if not required <= tables:
                raise RecoveryError("备份缺少平台核心数据表", code="invalid_database")
            counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in sorted(required)}
            return {"integrity": "ok", "schemaVersion": conn.execute(
                "PRAGMA user_version").fetchone()[0], "counts": counts}
        finally:
            conn.close()
    except RecoveryError:
        raise
    except sqlite3.Error as exc:
        raise RecoveryError(f"无法读取备份：{exc}", code="invalid_database") from exc


def list_backups(backups_dir: str):
    if not os.path.isdir(backups_dir):
        return []
    items = []
    for name in os.listdir(backups_dir):
        if not BACKUP_ID_RE.fullmatch(name):
            continue
        path = os.path.join(backups_dir, name, "app.db")
        if not os.path.isfile(path):
            continue
        stat = os.stat(path)
        items.append({"id": name, "size": stat.st_size,
                      "modifiedAt": dt.datetime.fromtimestamp(
                          stat.st_mtime, dt.timezone.utc).isoformat().replace("+00:00", "Z")})
    return sorted(items, key=lambda item: item["modifiedAt"], reverse=True)


def preview(backups_dir: str, backup_id: str):
    path = _backup_path(backups_dir, backup_id)
    return {"id": backup_id, **inspect_database(path)}


def schedule_restore(data_dir: str, backups_dir: str, backup_id: str):
    result = preview(backups_dir, backup_id)
    pending = os.path.join(data_dir, "pending_restore.json")
    temp = pending + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump({"backupId": backup_id, "scheduledAt": dt.datetime.now(
            dt.timezone.utc).isoformat().replace("+00:00", "Z")}, handle,
            ensure_ascii=False, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, pending)
    return result


def apply_pending_restore(data_dir: str, backups_dir: str, db_path: str):
    pending = os.path.join(data_dir, "pending_restore.json")
    if not os.path.isfile(pending):
        return None
    try:
        with open(pending, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        # 上次自动恢复失败后保留 pending 供人工处理，不再每次启动都重试，避免自锁。
        if payload.get("lastAttemptFailedAt"):
            return {"skipped": True, "reason": payload.get("lastError", "previous attempt failed"),
                    "scheduledAt": payload.get("scheduledAt", ""),
                    "lastAttemptFailedAt": payload["lastAttemptFailedAt"]}
        backup_id = str(payload.get("backupId") or "")
        source_path = _backup_path(backups_dir, backup_id)
        inspection = inspect_database(source_path)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        if os.path.isfile(db_path):
            emergency_dir = os.path.join(backups_dir, f"pre_restore_{stamp}")
            os.makedirs(emergency_dir, exist_ok=False)
            source = sqlite3.connect(db_path)
            target = sqlite3.connect(os.path.join(emergency_dir, "app.db"))
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        temp_path = db_path + ".restoring"
        if os.path.exists(temp_path):
            os.remove(temp_path)
        source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        target = sqlite3.connect(temp_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        inspect_database(temp_path)
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(db_path + suffix)
            except FileNotFoundError:
                pass
        os.replace(temp_path, db_path)
        os.remove(pending)
        return {"backupId": backup_id, **inspection}
    except Exception as exc:
        try:
            os.remove(db_path + ".restoring")
        except FileNotFoundError:
            pass
        # 保留 pending 文件供人工诊断，但记录失败时间与原因，防止每次启动重复自锁重试。
        try:
            with open(pending, "r", encoding="utf-8") as handle:
                failed_payload = json.load(handle)
            failed_payload["lastAttemptFailedAt"] = dt.datetime.now(
                dt.timezone.utc).isoformat().replace("+00:00", "Z")
            failed_payload["lastError"] = str(exc)[:300]
            temp = pending + ".tmp"
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(failed_payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, pending)
        except Exception:
            pass
        raise

