"""Admin account management routes (users, teachers, students).

Split out of ``directory`` (class membership) so each blueprint owns a
single domain: this file owns ``/api/admin/users*``, teacher transfers,
batch deletion and student school status. The ``directory`` feature key
stays the same so module dependencies and permissions are unchanged.
"""

from .base import RouteSpec, create_blueprint
import os
from flask import jsonify, request
import database as db
from essay_platform.core.audit import audit_event
from essay_platform.core.auth import (
    admin_required, make_secure_user, secure_password_hash, validate_new_password,
)
from essay_platform.core.config import (
    CLASSES_PATH, DEFAULT_TEMP_PASSWORD, USERS_PATH,
)
from essay_platform.persistence.datasets import (
    backup_data_snapshot, load_json, save_json, save_users_and_classes,
)
from essay_platform.services.directory import (
    find_class, paginate, pagination_args, public_user,
    student_no_exists, validate_employee_no, validate_student_no,
    validate_username,
)

MANIFEST = {
    "key": "directory", "dependencies": [], "permissions": ["admin"],
    "navigation": ["teachers", "students", "classes"], "tasks": [],
    "migrations": ["users", "classes"],
}

ROUTES = (
    RouteSpec("GET", "/api/admin/users", "api_admin_users"),
    RouteSpec("POST", "/api/admin/users", "api_admin_user_create"),
    RouteSpec("PUT", "/api/admin/users/<username>", "api_admin_user_update"),
    RouteSpec("POST", "/api/admin/users/<username>/reset-password", "api_admin_user_reset_password"),
    RouteSpec("DELETE", "/api/admin/users/<username>", "api_admin_user_delete"),
    RouteSpec("POST", "/api/admin/teachers/batch-delete", "api_admin_teachers_batch_delete"),
    RouteSpec("POST", "/api/admin/teachers/<username>/transfer-assignments", "api_admin_teacher_transfer_assignments"),
    RouteSpec("POST", "/api/admin/students/<username>/status", "api_admin_student_status"),
)


@admin_required
def api_admin_users():
    users = load_json(USERS_PATH, {"users": []})["users"]
    role = (request.args.get("role") or "").strip()
    if role and role not in ("admin", "teacher", "student"):
        return jsonify({"error": "权限筛选无效"}), 400
    if role:
        users = [u for u in users if u.get("role") == role]
    query = (request.args.get("q") or "").strip().lower()
    if query:
        users = [u for u in users if query in " ".join([
            str(u.get("username", "")), str(u.get("display_name", "")),
            str(u.get("studentNo", "")), str(u.get("employeeNo", "")),
            str(u.get("subject", "")), str(u.get("grade", "")), str(u.get("klass", "")),
        ]).lower()]
    users.sort(key=lambda u: (u.get("role", ""), u.get("display_name", ""), u.get("username", "")))
    page, page_size = pagination_args(request.args, default_size=30)
    page_users, total = paginate(users, page, page_size)
    cdata = load_json(CLASSES_PATH, {"classes": []})
    class_by_user = {}
    for c in cdata.get("classes", []):
        for username in c.get("teacherUsernames") or []:
            class_by_user.setdefault(username, []).append({"id": c["id"], "name": c.get("name", "")})
        for username in c.get("studentUsernames") or []:
            class_by_user.setdefault(username, []).append({"id": c["id"], "name": c.get("name", "")})
    return jsonify({"users": [
        {**public_user(u), "classes": class_by_user.get(u["username"], [])}
        for u in page_users
    ], "total": total, "page": page, "pageSize": page_size})


@admin_required
def api_admin_user_create():
    body = request.get_json(silent=True) or {}
    role = (body.get("role") or "").strip()
    student_no = (body.get("studentNo") or "").strip()
    employee_no = (body.get("employeeNo") or "").strip()
    username = (body.get("username") or "").strip()
    if role == "student" and not username:
        username = student_no
    elif role == "teacher":
        username = employee_no or username
    display_name = (body.get("display_name") or username).strip() or username
    generated_password = not bool(body.get("password"))
    password = str(body.get("password") or DEFAULT_TEMP_PASSWORD)
    if role not in ("teacher", "student"):
        return jsonify({"error": "这里只能创建老师或学生账号"}), 400
    if role == "student":
        student_no = student_no or username
        if not validate_student_no(student_no):
            return jsonify({"error": "学号需为 2-32 位字母、数字、下划线或短横线"}), 400
    else:
        employee_no = employee_no or username
        subject = str(body.get("subject") or "").strip()
        class_ids = body.get("classIds") or []
        if not validate_employee_no(employee_no):
            return jsonify({"error": "工号需为 2-32 位字母、数字、下划线或短横线"}), 400
        if not display_name or not subject:
            return jsonify({"error": "老师姓名和学科不能为空"}), 400
        if not isinstance(class_ids, list) or not class_ids:
            return jsonify({"error": "请至少选择一个任教班级"}), 400
    if not validate_username(username):
        return jsonify({"error": "用户名需为 2-32 位字母、数字、下划线、短横线或中文"}), 400
    policy_error = validate_new_password(password)
    if policy_error:
        return jsonify({"error": policy_error}), 400
    if role == "teacher":
        result = db.create_teacher_user(
            employee_no=employee_no, display_name=display_name, subject=subject,
            password_hash=secure_password_hash(password), class_ids=class_ids,
        )
        if not result.get("ok"):
            message = "工号或登录账号已存在" if result.get("reason") == "duplicate_employee_no" else "所选班级不存在"
            return jsonify({"error": message}), 400
        audit_event("teacher_created", target_type="user", target_id=result["username"],
                    detail={"classCount": len(set(class_ids))})
        payload = {"ok": True, "username": result["username"], "mustChangePassword": True}
        if generated_password:
            payload["temporaryPassword"] = password
        return jsonify(payload)
    result = db.create_student_user(
        username=username, student_no=student_no, display_name=display_name,
        password_hash=secure_password_hash(password))
    if not result.get("ok"):
        return jsonify({"error": "用户名或学号已存在"}), 400
    audit_event("user_created", target_type="user", target_id=username,
                detail={"role": role})
    payload = {"ok": True, "mustChangePassword": True}
    if generated_password:
        payload["temporaryPassword"] = password
    return jsonify(payload)


@admin_required
def api_admin_user_update(username):
    body = request.get_json(silent=True) or {}
    data = load_json(USERS_PATH, {"users": []})
    user = next((u for u in data["users"] if u["username"] == username), None)
    if not user:
        return jsonify({"error": "账号不存在"}), 404
    if username == "admin" or user.get("role") == "admin":
        return jsonify({"error": "系统管理员账号不可修改"}), 400
    if user.get("role") == "teacher":
        employee_no = str(body.get("employeeNo") or user.get("employeeNo") or username).strip()
        display_name = str(body.get("display_name") or user.get("display_name") or username).strip()
        subject = str(body.get("subject") if body.get("subject") is not None
                      else user.get("subject", "")).strip()
        cdata = load_json(CLASSES_PATH, {"classes": []})
        current_class_ids = [c["id"] for c in cdata.get("classes", [])
                             if username in (c.get("teacherUsernames") or [])]
        class_ids = body.get("classIds", current_class_ids)
        if not validate_employee_no(employee_no):
            return jsonify({"error": "工号需为 2-32 位字母、数字、下划线或短横线"}), 400
        if not display_name:
            return jsonify({"error": "老师姓名不能为空"}), 400
        if body.get("subject") is not None and not subject:
            return jsonify({"error": "学科不能为空"}), 400
        if not isinstance(class_ids, list) or not class_ids:
            return jsonify({"error": "请至少选择一个任教班级"}), 400
        result = db.update_teacher_profile(
            username, employee_no=employee_no, display_name=display_name,
            subject=subject, class_ids=class_ids,
            active=bool(body.get("active", user.get("active", True))),
        )
        if not result.get("ok"):
            if result.get("reason") == "duplicate_employee_no":
                return jsonify({"error": "工号或登录账号已存在"}), 400
            if result.get("reason") == "class_not_found":
                return jsonify({"error": "所选班级不存在"}), 400
            return jsonify({"error": "老师账号不存在"}), 404
        audit_event("teacher_updated", target_type="user", target_id=result["username"],
                    detail={"renamed": bool(result.get("renamed")),
                            "active": bool(body.get("active", user.get("active", True))),
                            "classCount": len(set(class_ids))})
        return jsonify({"ok": True, "username": result["username"]})
    next_display_name = str(body.get("display_name", user.get("display_name") or username)).strip() or username
    next_student_no = str(user.get("studentNo") or username)
    if body.get("studentNo") is not None:
        if user.get("role") != "student":
            return jsonify({"error": "只有学生账号可以设置学号"}), 400
        student_no = str(body["studentNo"]).strip()
        if not validate_student_no(student_no):
            return jsonify({"error": "学号需为 2-32 位字母、数字、下划线或短横线"}), 400
        next_student_no = student_no
    if body.get("role") is not None:
        role = str(body["role"]).strip()
        if role not in ("teacher", "student"):
            return jsonify({"error": "权限无效"}), 400
        if role != user.get("role"):
            return jsonify({"error": "账号权限不可直接转换，请删除后按正确权限重新创建"}), 409
    if body.get("grade") is not None or body.get("klass") is not None:
        return jsonify({"error": "年级和班级必须通过班级管理分配"}), 400
    if body.get("password") is not None:
        return jsonify({"error": "请使用专用的重置密码操作"}), 400
    next_active = bool(body.get("active", user.get("active", True)))
    result = db.update_student_user(
        username, display_name=next_display_name, student_no=next_student_no,
        active=next_active)
    if not result.get("ok"):
        if result.get("reason") == "duplicate_student_no":
            return jsonify({"error": "学号已存在"}), 400
        return jsonify({"error": "学生账号不存在"}), 404
    audit_event("user_updated", target_type="user", target_id=username,
                detail={"active": next_active})
    return jsonify({"ok": True})


@admin_required
def api_admin_user_reset_password(username):
    if username == "admin":
        return jsonify({"error": "系统管理员请登录该账号后通过账号密码接口修改"}), 400
    user = db.get_user(username)
    if not user:
        return jsonify({"error": "账号不存在"}), 404
    body = request.get_json(silent=True) or {}
    generated_password = not bool(body.get("password"))
    password = str(body.get("password") or DEFAULT_TEMP_PASSWORD)
    policy_error = validate_new_password(password)
    if policy_error:
        return jsonify({"error": policy_error}), 400
    db.update_user_password(username, secure_password_hash(password), must_change=True)
    db.revoke_user_sessions(username)
    audit_event("password_reset", target_type="user", target_id=username)
    payload = {"ok": True, "mustChangePassword": True}
    if generated_password:
        payload["temporaryPassword"] = password
    return jsonify(payload)


@admin_required
def api_admin_user_delete(username):
    data = load_json(USERS_PATH, {"users": []})
    user = next((u for u in data["users"] if u["username"] == username), None)
    if not user:
        return jsonify({"error": "账号不存在"}), 404
    if username == "admin" or user.get("role") == "admin":
        return jsonify({"error": "系统管理员账号不可删除"}), 400
    if user.get("role") == "teacher":
        backup_data_snapshot("teacher_delete")
        result = db.safely_remove_teachers([username])
        if result["failed"]:
            return jsonify({"error": result["failed"][0]["reason"]}), 400
        action = "deleted" if result["deleted"] else "deactivated"
        audit_event("teacher_removed", target_type="user", target_id=username,
                    detail={"action": action})
        return jsonify({"ok": True, **result})
    if db.is_ready():
        # 学生账号关联作业/历史/版本记录时禁止硬删，避免 ON DELETE CASCADE 级联清空历史。
        business_count = db.count_user_business_records(username)
        if business_count:
            return jsonify({"error": "该账号仍关联作业、成绩或历史记录，不能删除；可改为停用",
                            "details": business_count}), 409
        backup_data_snapshot("student_delete")
    result = db.delete_student_user(username)
    if not result.get("ok"):
        if result.get("reason") == "has_records":
            return jsonify({"error": "该账号仍关联班级、作业、成绩或历史记录，不能删除；可改为转学、毕业或停用",
                            "details": result.get("count", 0)}), 409
        return jsonify({"error": "账号不存在"}), 404
    audit_event("user_deleted", target_type="user", target_id=username,
                detail={"role": user.get("role", "")})
    return jsonify({"ok": True})


@admin_required
def api_admin_teachers_batch_delete():
    body = request.get_json(silent=True) or {}
    usernames = body.get("usernames")
    if not isinstance(usernames, list) or not usernames:
        return jsonify({"error": "请选择要删除的老师"}), 400
    usernames = [str(item).strip() for item in usernames if str(item).strip()]
    if len(usernames) > 100:
        return jsonify({"error": "一次最多处理 100 位老师"}), 400
    backup_data_snapshot("teacher_batch_delete")
    result = db.safely_remove_teachers(usernames)
    audit_event("teachers_batch_removed", target_type="user", target_id="batch",
                detail={"requested": len(usernames), "deleted": len(result["deleted"]),
                        "deactivated": len(result["deactivated"]),
                        "failed": len(result["failed"])})
    return jsonify({"ok": True, **result})


@admin_required
def api_admin_teacher_transfer_assignments(username):
    body = request.get_json(silent=True) or {}
    target = str(body.get("targetUsername") or "").strip()
    assignment_ids = body.get("assignmentIds")
    if assignment_ids is not None and not isinstance(assignment_ids, list):
        return jsonify({"error": "作业名单格式无效"}), 400
    backup = backup_data_snapshot("teacher_transfer")
    result = db.transfer_teacher_assignments(username, target, assignment_ids)
    if not result.get("ok"):
        messages = {
            "invalid_teacher": "原老师或接收老师账号无效",
            "assignment_not_found": "部分作业不属于原老师",
        }
        return jsonify({"error": messages.get(result.get("reason"), "作业移交失败")}), 409
    audit_event("teacher_assignments_transferred", target_type="teacher", target_id=username,
                detail={"targetUsername": target, "count": len(result["assignmentIds"]),
                        "backup": os.path.basename(backup) if backup else ""})
    return jsonify(result)


@admin_required
def api_admin_student_status(username):
    body = request.get_json(silent=True) or {}
    status = str(body.get("status") or "").strip()
    if status not in ("active", "transferred", "graduated"):
        return jsonify({"error": "学生状态无效"}), 400
    result = db.set_student_school_status(
        username, status, str(body.get("classId") or "").strip())
    if not result.get("ok"):
        return jsonify({"error": "学生不存在" if result.get("reason") == "not_found"
                        else "恢复在校状态时必须选择当前班级"}), 409
    audit_event("student_school_status_changed", target_type="student", target_id=username,
                detail={"status": status, "classId": result.get("classId", "")})
    return jsonify(result)


def build_blueprint():
    return create_blueprint("users", __name__, ROUTES, default_feature="directory", namespace=globals())
