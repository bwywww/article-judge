"""Classes and membership routes."""

from .base import RouteSpec, create_blueprint
import secrets
from flask import jsonify, request
import database as db
from essay_platform.core.audit import audit_event
from essay_platform.core.auth import (
    admin_required, login_required, teacher_required,
)
from essay_platform.core.config import (
    ASSIGNMENTS_PATH, CLASSES_PATH, USERS_PATH,
)
from essay_platform.core.state import io_lock
from essay_platform.core.time import now_str
from essay_platform.persistence.datasets import (
    load_json, save_json, save_users_and_classes,
)
from essay_platform.services.directory import (
    class_name, class_sort_key, find_class, is_class_teacher, paginate, pagination_args,
    public_student, public_user,
)

MANIFEST = {
    "key": "directory", "dependencies": [], "permissions": ["admin", "teacher"],
    "navigation": ["teachers", "students", "classes"], "tasks": [], "migrations": ["users", "classes"],
}

ROUTES = (
    RouteSpec("GET", "/api/classes", "api_classes_list"),
    RouteSpec("POST", "/api/classes", "api_classes_create"),
    RouteSpec("PUT", "/api/classes/<cid>", "api_classes_update"),
    RouteSpec("DELETE", "/api/classes/<cid>", "api_classes_delete"),
    RouteSpec("GET", "/api/students", "api_students_list"),
    RouteSpec("POST", "/api/classes/<cid>/members", "api_class_member_add"),
    RouteSpec("DELETE", "/api/classes/<cid>/members/<username>", "api_class_member_remove"),
    RouteSpec("GET", "/api/classes/<cid>/members", "api_class_members"),
    RouteSpec("POST", "/api/classes/<cid>/teachers", "api_class_teacher_add"),
    RouteSpec("DELETE", "/api/classes/<cid>/teachers/<username>", "api_class_teacher_remove"),
)

@login_required
def api_classes_list():
    cdata = load_json(CLASSES_PATH, {"classes": []})
    include_archived = (request.args.get("includeArchived") == "1"
                        and request.user.get("role") == "admin")
    active_classes = [c for c in cdata.get("classes", [])
                      if include_archived or c.get("status", "active") == "active"]
    if request.user.get("role") == "admin":
        classes = active_classes
    elif request.user.get("role") == "teacher":
        classes = [c for c in active_classes if is_class_teacher(c, request.user)]
    else:
        classes = [c for c in active_classes
                   if request.user["username"] in (c.get("studentUsernames") or [])]
    classes.sort(key=class_sort_key)
    if request.user.get("role") in ("admin", "teacher"):
        return jsonify({"classes": [{
            "id": c["id"], "name": c.get("name", ""), "grade": c.get("grade", ""),
            "klass": c.get("klass", ""), "createdAt": c.get("createdAt", ""),
            "academicYearId": c.get("academicYearId", ""),
            "status": c.get("status", "active"),
            "studentCount": len(c.get("studentUsernames") or []),
            "teacherCount": len(c.get("teacherUsernames") or []),
        } for c in classes]})
    return jsonify({"classes": [{
        "id": c["id"], "name": c.get("name", ""), "grade": c.get("grade", ""),
        "klass": c.get("klass", ""),
    } for c in classes]})

@admin_required
def api_classes_create():
    body = request.get_json(silent=True) or {}
    grade = (body.get("grade") or "").strip()
    klass = (body.get("klass") or "").strip()
    name = (body.get("name") or class_name(grade, klass)).strip()
    if not name:
        return jsonify({"error": "请填写班级名称"}), 400
    class_id = "c" + secrets.token_hex(6)
    result = db.create_class_record(class_id, name, grade, klass)
    if not result.get("ok"):
        return jsonify({"error": "该年级和班级已存在"}), 409
    audit_event("class_created", target_type="class", target_id=class_id,
                detail={"name": name})
    return jsonify({"id": class_id})

@admin_required
def api_classes_update(cid):
    body = request.get_json(silent=True) or {}
    data = load_json(CLASSES_PATH, {"classes": []})
    cls = find_class(data, cid)
    if not cls:
        return jsonify({"error": "班级不存在"}), 404
    next_grade = str(body.get("grade", cls.get("grade", ""))).strip()
    next_klass = str(body.get("klass", cls.get("klass", ""))).strip()
    next_name = str(body.get("name", cls.get("name", ""))).strip()
    next_name = next_name or class_name(next_grade, next_klass)
    result = db.update_class_record(cid, next_name, next_grade, next_klass)
    if not result.get("ok"):
        if result.get("reason") == "not_found":
            return jsonify({"error": "班级不存在"}), 404
        if result.get("reason") == "archived":
            return jsonify({"error": "历史班级已归档，不能修改"}), 409
        return jsonify({"error": "该年级和班级已存在"}), 409
    audit_event("class_updated", target_type="class", target_id=cid,
                detail={"name": next_name})
    return jsonify({"ok": True})

@admin_required
def api_classes_delete(cid):
    result = db.delete_class_record(cid)
    if not result.get("ok") and result.get("reason") == "not_found":
        return jsonify({"error": "班级不存在"}), 404
    if not result.get("ok") and result.get("reason") == "has_assignments":
        return jsonify({"error": "该班级已有作业，不能删除；可先删除或改班级作业"}), 409
    audit_event("class_deleted", target_type="class", target_id=cid,
                detail={"name": result.get("name", "")})
    return jsonify({"ok": True})

@teacher_required
def api_students_list():
    users = load_json(USERS_PATH, {"users": []})["users"]
    cdata = load_json(CLASSES_PATH, {"classes": []})
    owned = [c for c in cdata.get("classes", []) if is_class_teacher(c, request.user)]
    owned_students = set()
    class_by_student = {}
    for c in owned:
        for username in c.get("studentUsernames") or []:
            owned_students.add(username)
            class_by_student.setdefault(username, []).append({"id": c["id"], "name": c.get("name", "")})
    return jsonify({"students": [
        {**public_student(u), "classes": class_by_student.get(u["username"], [])}
        for u in users if u.get("role") == "student" and u.get("username") in owned_students
    ]})

@admin_required
def api_class_member_add(cid):
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    result = db.assign_class_student(cid, username)
    if not result.get("ok"):
        return jsonify({"error": "班级不存在" if result.get("reason") == "class_not_found"
                        else "学生不存在"}), 404
    audit_event("class_student_assigned", target_type="user", target_id=username,
                detail={"classId": cid})
    return jsonify({"ok": True})

@admin_required
def api_class_member_remove(cid, username):
    result = db.remove_class_student(cid, username)
    if not result.get("ok"):
        return jsonify({"error": "班级不存在"}), 404
    audit_event("class_student_removed", target_type="user", target_id=username,
                detail={"classId": cid})
    return jsonify({"ok": True})

@admin_required
def api_class_members(cid):
    role = (request.args.get("role") or "student").strip()
    if role not in ("student", "teacher"):
        return jsonify({"error": "成员权限无效"}), 400
    cdata = load_json(CLASSES_PATH, {"classes": []})
    cls = find_class(cdata, cid)
    if not cls:
        return jsonify({"error": "班级不存在"}), 404
    member_key = "studentUsernames" if role == "student" else "teacherUsernames"
    usernames = set(cls.get(member_key) or [])
    users = [u for u in load_json(USERS_PATH, {"users": []})["users"]
             if u.get("role") == role and u.get("username") in usernames]
    query = (request.args.get("q") or "").strip().lower()
    if query:
        users = [u for u in users if query in " ".join([
            str(u.get("username", "")), str(u.get("display_name", "")),
            str(u.get("studentNo", "")), str(u.get("employeeNo", "")),
            str(u.get("subject", "")), str(u.get("grade", "")), str(u.get("klass", "")),
        ]).lower()]
    users.sort(key=lambda u: (u.get("display_name", ""), u.get("username", "")))
    page, page_size = pagination_args(request.args)
    page_users, total = paginate(users, page, page_size)
    return jsonify({
        "members": [public_user(u) for u in page_users],
        "total": total, "page": page, "pageSize": page_size,
    })

@admin_required
def api_class_teacher_add(cid):
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    result = db.add_class_teacher(cid, username)
    if not result.get("ok"):
        return jsonify({"error": "班级不存在" if result.get("reason") == "class_not_found"
                        else "老师不存在"}), 404
    if result.get("ok"):
        audit_event("class_teacher_assigned", target_type="user", target_id=username,
                    detail={"classId": cid})
    return jsonify({"ok": True})

@admin_required
def api_class_teacher_remove(cid, username):
    result = db.remove_class_teacher(cid, username)
    if not result.get("ok"):
        return jsonify({"error": "班级不存在"}), 404
    audit_event("class_teacher_removed", target_type="user", target_id=username,
                detail={"classId": cid})
    return jsonify({"ok": True})

def build_blueprint():
    return create_blueprint("directory", __name__, ROUTES, default_feature="directory", namespace=globals())
