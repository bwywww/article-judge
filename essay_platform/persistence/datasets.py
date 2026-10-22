"""JSON compatibility facade backed by SQLite plus backup housekeeping."""

import datetime
import json
import os
import re
import shutil
import time

import database as db

from essay_platform.core.config import (
    ASSIGNMENTS_PATH, BACKUPS_DIR, CLASSES_PATH, CONFIG_PATH, DATA_BACKUP_KEEP,
    DATA_SCHEMA_VERSION, DEFAULT_CONFIG, HISTORY_PATH, IMPORTS_DIR,
    IMPORT_PREVIEW_TTL, STAGING_DIR, UPLOAD_STAGING_TTL, USERS_PATH,
)
from essay_platform.core.secrets import get_ai_secret
from essay_platform.core.state import io_lock

class DataFileError(RuntimeError):
    pass


def _load_json_file(path, default):
    with io_lock:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                raise DataFileError(f"数据文件无法读取：{os.path.basename(path)}（{exc}）") from exc
        return default


def _save_json_file(path, data):
    with io_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def _dataset_kind(path):
    target = os.path.abspath(path)
    return {
        os.path.abspath(CONFIG_PATH): "config",
        os.path.abspath(USERS_PATH): "users",
        os.path.abspath(CLASSES_PATH): "classes",
        os.path.abspath(ASSIGNMENTS_PATH): "assignments",
        os.path.abspath(HISTORY_PATH): "history",
    }.get(target)


def load_json(path, default):
    kind = _dataset_kind(path)
    if db.is_ready() and kind:
        return db.load_dataset(kind)
    return _load_json_file(path, default)


def save_json(path, data):
    kind = _dataset_kind(path)
    if db.is_ready() and kind:
        db.save_dataset(kind, data)
        return
    _save_json_file(path, data)


def save_users_and_classes(users_data, classes_data):
    if db.is_ready():
        db.save_users_and_classes(users_data, classes_data)
    else:
        _save_json_file(USERS_PATH, users_data)
        _save_json_file(CLASSES_PATH, classes_data)

def backup_data_snapshot(prefix="manual"):
    """备份 SQLite；首次迁移前备份原始 JSON。"""
    with io_lock:
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(BACKUPS_DIR, f"{prefix}_{stamp}")
        if db.is_ready():
            os.makedirs(dest, exist_ok=True)
            db.backup_database(os.path.join(dest, "app.db"))
            return dest
        json_files = [p for p in (CONFIG_PATH, USERS_PATH, CLASSES_PATH, ASSIGNMENTS_PATH, HISTORY_PATH)
                      if os.path.exists(p)]
        if not json_files:
            return ""
        os.makedirs(dest, exist_ok=True)
        for path in json_files:
            shutil.copy2(path, os.path.join(dest, os.path.basename(path)))
        return dest


def backup_data_on_startup():
    """启动时备份顶层 JSON 数据文件，防止误操作破坏唯一数据源。"""
    with io_lock:
        dest = backup_data_snapshot("auto")
        if not dest:
            return
        backups = sorted(
            [os.path.join(BACKUPS_DIR, n) for n in os.listdir(BACKUPS_DIR)
             if n.startswith("auto_") and os.path.isdir(os.path.join(BACKUPS_DIR, n))],
            key=os.path.getmtime,
            reverse=True,
        )
        for old in backups[DATA_BACKUP_KEEP:]:
            shutil.rmtree(old, ignore_errors=True)


def cleanup_import_previews():
    if db.is_ready():
        db.cleanup_import_previews()
        return
    os.makedirs(IMPORTS_DIR, exist_ok=True)
    cutoff = time.time() - IMPORT_PREVIEW_TTL
    for name in os.listdir(IMPORTS_DIR):
        path = os.path.join(IMPORTS_DIR, name)
        if name.startswith("preview_") and name.endswith(".json") and os.path.isfile(path):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass


def cleanup_staged_uploads():
    os.makedirs(STAGING_DIR, exist_ok=True)
    if db.is_ready():
        db.cleanup_upload_tokens()
    cutoff = time.time() - UPLOAD_STAGING_TTL
    for name in os.listdir(STAGING_DIR):
        path = os.path.join(STAGING_DIR, name)
        if not re.fullmatch(r"[0-9a-f]{32}", name) or not os.path.isdir(path):
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def versioned_data(kind, key, values):
    return {"schemaVersion": DATA_SCHEMA_VERSION, key: values}


def trim_history_items(items):
    """Compatibility hook: formal and practice history is retained permanently."""
    return list(items)


def get_config():
    data = load_json(CONFIG_PATH, {})
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(data or {})
    if (cfg.get("base_url", "").rstrip("/") == "https://api.deepseek.com"
            and cfg.get("model") in ("deepseek-chat", "deepseek-reasoner")):
        cfg["model"] = "deepseek-v4-flash"
    key, source = get_ai_secret("article_judge")
    cfg["api_key"] = key
    cfg["managedExternally"] = bool(key)
    cfg["keySource"] = source
    return cfg


def save_config(cfg):
    save_json(CONFIG_PATH, {
        "base_url": cfg.get("base_url", ""),
        "api_key": "",
        "model": cfg.get("model", ""),
    })
