"""Application startup, recovery and local development launcher."""

import os
import socket
import threading
import webbrowser

import database as db
from essay_platform.core import modules as feature_modules
from essay_platform.core.auth import apply_admin_password_override, make_user
from essay_platform.core.config import (
    AI_TASK_ROUTE_DEFAULTS, ASSIGNMENTS_PATH, BACKUPS_DIR, CLASSES_PATH,
    CONFIG_PATH, DATA_DIR, EXPLICIT_DATA_DIR, HISTORY_PATH, LEGACY_DATA_DIR, USERS_PATH,
)
from essay_platform.core.secrets import public_secret_status, redact_secrets
from essay_platform.core.time import now_str
from essay_platform.persistence.datasets import (
    _load_json_file, backup_data_on_startup, backup_data_snapshot,
    cleanup_import_previews, cleanup_staged_uploads, load_json, save_json,
    versioned_data,
)
from essay_platform.services import recovery as recovery_service
from essay_platform.services.database_location import prepare_runtime_database
from essay_platform.services.assignments import refresh_expired_assignments
from essay_platform.services.migration import migrate_runtime_data, normalize_runtime_data
from essay_platform.workers.grading import reset_stale_grading
from essay_platform.workers.ocr import reset_stale_ocr

runtime_init_lock = threading.Lock()
runtime_initialized_dirs = set()


def initialize_runtime():
    """幂等初始化；首次启动原子导入 JSON，之后只使用 SQLite。"""
    global DB_PATH
    secret_status = public_secret_status()
    key = os.path.abspath(DATA_DIR)
    with runtime_init_lock:
        if key in runtime_initialized_dirs:
            return
        test_mode = (os.environ.get("APP_ENV") or "").lower() == "test"
        location = prepare_runtime_database(
            DATA_DIR, LEGACY_DATA_DIR,
            allow_demo=(os.environ.get("ESSAY_BOOTSTRAP_DEMO") == "1"
                        or test_mode),
            explicit_data_dir=(EXPLICIT_DATA_DIR or test_mode),
        )
        if location.get("recovered"):
            print(f"[数据恢复] 已从旧路径恢复到正式数据库：{location.get('path')}")
        else:
            print(f"[数据库] 使用：{location.get('path')} counts={location.get('counts', {})}")
        DB_PATH = db.configure(DATA_DIR)
        try:
            restore_result = recovery_service.apply_pending_restore(DATA_DIR, BACKUPS_DIR, DB_PATH)
            if restore_result and restore_result.get("skipped"):
                print("[恢复] 上次数据恢复未完成，已跳过自动重试；请检查备份或手动处理恢复。"
                      f"原因：{restore_result.get('reason')}")
            elif restore_result:
                print(f"[恢复] 数据恢复成功：backup={restore_result.get('backupId')}")
        except Exception as exc:
            print(f"[恢复] 数据恢复失败，将继续使用当前数据库：{redact_secrets(exc)}")
        if not os.path.exists(DB_PATH):
            default_users = versioned_data("users", "users", [
                make_user("admin", "管理员", "admin"),
                make_user("mr", "MR老师", "teacher"),
                make_user("stu", "STU学生", "student", student_no="stu"),
            ])
            default_classes = versioned_data("classes", "classes", [{
                "id": "class", "name": "class", "grade": "", "klass": "class",
                "createdAt": now_str(), "teacherUsernames": ["mr"],
                "studentUsernames": ["stu"],
            }])
            source_data = {
                "config": _load_json_file(CONFIG_PATH, {}),
                "users": _load_json_file(USERS_PATH, default_users),
                "classes": _load_json_file(CLASSES_PATH, default_classes),
                "assignments": _load_json_file(
                    ASSIGNMENTS_PATH, versioned_data("assignments", "assignments", [])),
                "history": _load_json_file(
                    HISTORY_PATH, versioned_data("history", "items", [])),
            }
            backup_data_snapshot("sqlite_source")
            db.ensure_database(normalize_runtime_data(source_data))
        else:
            db.ensure_database({})
        feature_modules.ensure_registry()
        db.ensure_ai_task_routes(AI_TASK_ROUTE_DEFAULTS)
        stored_config = load_json(CONFIG_PATH, {})
        config_changed = False
        if stored_config.get("api_key") and secret_status["article_judge"]["configured"]:
            stored_config["api_key"] = ""
            config_changed = True
        if (stored_config.get("base_url", "").rstrip("/") == "https://api.deepseek.com"
                and stored_config.get("model") in ("deepseek-chat", "deepseek-reasoner")):
            stored_config["model"] = "deepseek-v4-flash"
            config_changed = True
        if config_changed:
            save_json(CONFIG_PATH, stored_config)
        backup_data_on_startup()
        migrate_runtime_data()
        from essay_platform.services.migration import normalize_default_passwords
        normalized_passwords = normalize_default_passwords()
        if normalized_passwords:
            print(f"[密码迁移] 已统一 {normalized_passwords} 个待改密账号")
        apply_admin_password_override()
        db.cleanup_auth_state()
        refresh_expired_assignments()
        cleanup_import_previews()
        cleanup_staged_uploads()
        reset_stale_grading()
        reset_stale_ocr()
        runtime_initialized_dirs.add(key)


def ensure_runtime_initialized():
    initialize_runtime()
    feature_modules.refresh_draining_modules()

def find_free_port(start=5000):
    for port in range(start, start + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 5010

def main():
    from app import app

    initialize_runtime()
    port = int(os.environ.get("PORT") or find_free_port(5000))
    host = os.environ.get("HOST") or "127.0.0.1"
    if os.environ.get("OPEN_BROWSER", "1") == "1":
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
