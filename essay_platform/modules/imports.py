"""Roster Excel import routes."""

from .base import RouteSpec, create_blueprint
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from flask import jsonify, request
import database as db
from essay_platform.core.audit import audit_event
from essay_platform.core.auth import (
    admin_required, make_secure_user, secure_password_hash,
)
from essay_platform.core.config import (
    CLASSES_PATH, DEFAULT_TEMP_PASSWORD, IMPORTS_DIR, USERS_PATH,
)
from essay_platform.core.state import io_lock
from essay_platform.core.time import now_str
from essay_platform.persistence.datasets import (
    _save_json_file, backup_data_snapshot, load_json, save_users_and_classes,
)
from essay_platform.services.directory import (
    class_name, find_class_by_grade_klass, user_by_student_no,
)
from essay_platform.services.student_imports import (
    build_student_import_preview, validate_import_preview_fresh,
)
from essay_platform.services.teacher_imports import (
    build_teacher_import_preview, validate_teacher_import_preview_fresh,
)
from essay_platform.services.import_previews import (
    load_import_preview, public_import_preview, save_import_preview,
)

MANIFEST = {
    "key": "imports", "dependencies": ["directory", "assignments"],
    "permissions": ["admin", "teacher", "student"],
    "navigation": ["student-import", "essay-import"],
    "tasks": ["ai-split", "name-extract"], "migrations": ["import_previews", "upload_tokens"],
}

ROUTES = (
    RouteSpec("POST", "/api/admin/students/import/preview", "api_admin_students_import_preview"),
    RouteSpec("POST", "/api/admin/students/import/confirm", "api_admin_students_import_confirm"),
    RouteSpec("POST", "/api/admin/teachers/import/preview", "api_admin_teachers_import_preview"),
    RouteSpec("POST", "/api/admin/teachers/import/confirm", "api_admin_teachers_import_confirm"),
)

@admin_required
def api_admin_students_import_preview():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "请上传学生 Excel 文件"}), 400
    try:
        preview = build_student_import_preview(file, request.form.get("academicYearStart"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    save_import_preview(preview)
    return jsonify(public_import_preview(preview))

@admin_required
def api_admin_students_import_confirm():
    started_at = time.perf_counter()
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    with io_lock:
        preview = load_import_preview(token)
        if not preview:
            return jsonify({"error": "导入预览已过期或已使用，请重新上传 Excel"}), 409
        if preview.get("summary", {}).get("error"):
            return jsonify({"error": "预览中仍有错误行，请修正 Excel 后重新上传"}), 400
        missing_credentials = [
            row.get("rowNo") for row in preview.get("rows", [])
            if row.get("action") == "new" and not row.get("passwordHash")
        ]
        if missing_credentials:
            return jsonify({"error": "导入预览版本过旧或凭据不完整，请重新上传 Excel",
                            "details": missing_credentials[:20]}), 409
        freshness_errors = validate_import_preview_fresh(preview)
        if freshness_errors:
            return jsonify({"error": "账号数据在预览后已变化，可能前一次导入已经成功；请刷新学生列表后重新预览",
                            "details": freshness_errors[:20]}), 409
        preview["consumedAt"] = time.time()
        if db.is_ready():
            if not db.consume_import_preview(token):
                return jsonify({"error": "导入预览已过期或已使用，请重新上传 Excel"}), 409
        else:
            preview_path = os.path.join(IMPORTS_DIR, f"preview_{token}.json")
            _save_json_file(preview_path, preview)
        try:
            backup_dir = backup_data_snapshot("import")
            for row in preview.get("rows", []):
                row["className"] = class_name(row.get("grade", ""), row.get("klass", ""))
            applied = db.apply_student_import(preview.get("rows", []))
            created, updated = applied["created"], applied["updated"]
        except Exception:
            preview.pop("consumedAt", None)
            if db.is_ready():
                db.unconsume_import_preview(token)
            else:
                _save_json_file(preview_path, preview)
            raise
    duration_seconds = round(time.perf_counter() - started_at, 3)
    audit_event("student_import_confirmed", target_type="import", target_id=token,
                detail={"created": created, "updated": updated,
                        "durationSeconds": duration_seconds,
                        "backup": os.path.basename(backup_dir) if backup_dir else ""})
    print(f"[导入] 学生 {created + updated} 人，新增 {created}，更新 {updated}，"
          f"耗时 {duration_seconds:.3f}s")
    return jsonify({"ok": True, "created": created, "updated": updated,
                    "durationSeconds": duration_seconds,
                    "backup": os.path.basename(backup_dir) if backup_dir else "",
                    "defaultTemporaryPassword": DEFAULT_TEMP_PASSWORD if created else ""})

@admin_required
def api_admin_teachers_import_preview():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "请上传教师 Excel 文件"}), 400
    try:
        preview = build_teacher_import_preview(file)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    save_import_preview(preview)
    return jsonify(public_import_preview(preview))

@admin_required
def api_admin_teachers_import_confirm():
    started_at = time.perf_counter()
    token = str((request.get_json(silent=True) or {}).get("token") or "").strip()
    with io_lock:
        preview = load_import_preview(token)
        if not preview or preview.get("kind") != "teacher":
            return jsonify({"error": "导入预览已过期或已使用，请重新上传 Excel"}), 409
        if preview.get("summary", {}).get("error"):
            return jsonify({"error": "预览中仍有错误行，请修正 Excel 后重新上传"}), 400
        freshness_errors = validate_teacher_import_preview_fresh(preview)
        if freshness_errors:
            return jsonify({"error": "账号或班级数据在预览后已变化，请重新预览",
                            "details": freshness_errors[:20]}), 409
        if not db.consume_import_preview(token):
            return jsonify({"error": "导入预览已过期或已使用，请重新上传 Excel"}), 409
        try:
            backup_dir = backup_data_snapshot("teacher_import")
            new_count = sum(1 for row in preview.get("rows", []) if row.get("action") == "new")
            if new_count:
                with ThreadPoolExecutor(max_workers=min(4, new_count)) as pool:
                    new_teacher_password_hashes = iter(pool.map(
                        secure_password_hash, [DEFAULT_TEMP_PASSWORD] * new_count))
            else:
                new_teacher_password_hashes = iter(())
            import_rows = []
            for row in preview.get("rows", []):
                current = dict(row)
                if row.get("action") == "new":
                    current["passwordHash"] = next(new_teacher_password_hashes)
                import_rows.append(current)
            applied = db.apply_teacher_import(import_rows)
            created, updated = applied["created"], applied["updated"]
        except Exception:
            db.unconsume_import_preview(token)
            raise
    duration_seconds = round(time.perf_counter() - started_at, 3)
    audit_event("teacher_import_confirmed", target_type="import", target_id=token,
                detail={"created": created, "updated": updated,
                        "durationSeconds": duration_seconds,
                        "backup": os.path.basename(backup_dir) if backup_dir else ""})
    return jsonify({"ok": True, "created": created, "updated": updated,
                    "durationSeconds": duration_seconds,
                    "defaultTemporaryPassword": DEFAULT_TEMP_PASSWORD if created else ""})

def build_blueprint():
    return create_blueprint("imports", __name__, ROUTES, default_feature="imports", namespace=globals())
