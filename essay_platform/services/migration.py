"""Legacy data initialization, normalization and demo reset."""

import copy
import hashlib
import json
import os
import secrets
import shutil
from concurrent.futures import ThreadPoolExecutor

from werkzeug.security import check_password_hash

import database as db
from essay_platform.core.auth import make_user, secure_password_hash
from essay_platform.core.config import (
    ASSIGNMENTS_PATH, CLASSES_PATH, CONFIG_PATH, DATA_SCHEMA_VERSION, DEFAULT_TEMP_PASSWORD,
    HISTORY_PATH, UPLOADS_DIR, USERS_PATH,
)
from essay_platform.core.state import io_lock
from essay_platform.core.time import content_hash, now_str
from essay_platform.persistence.datasets import (
    _load_json_file, _save_json_file, load_json, save_json, save_users_and_classes,
    trim_history_items, versioned_data,
)
from essay_platform.services.directory import class_name, first_teacher_class

def init_users():
    """首次运行时创建最小测试账号，统一使用临时密码并强制改密。"""
    if not os.path.exists(USERS_PATH):
        save_json(USERS_PATH, versioned_data("users", "users", [
            make_user("admin", "管理员", "admin", DEFAULT_TEMP_PASSWORD),
            make_user("mr", "MR老师", "teacher", DEFAULT_TEMP_PASSWORD),
            make_user("stu", "STU学生", "student", DEFAULT_TEMP_PASSWORD, student_no="stu"),
        ]))
    if not os.path.exists(CLASSES_PATH):
        save_json(CLASSES_PATH, versioned_data("classes", "classes", [{
            "id": "class",
            "name": "class",
            "grade": "",
            "klass": "class",
            "createdAt": now_str(),
            "teacherUsernames": ["mr"],
            "studentUsernames": ["stu"],
        }]))
    if not os.path.exists(ASSIGNMENTS_PATH):
        save_json(ASSIGNMENTS_PATH, versioned_data("assignments", "assignments", []))
    if not os.path.exists(HISTORY_PATH):
        save_json(HISTORY_PATH, versioned_data("history", "items", []))

def reset_demo_data():
    """清空业务数据并写入最小测试集；保留 AI 配置。"""
    with io_lock:
        users_data = versioned_data("users", "users", [
            make_user("admin", "管理员", "admin", DEFAULT_TEMP_PASSWORD),
            make_user("mr", "MR老师", "teacher", DEFAULT_TEMP_PASSWORD),
            make_user("stu", "STU学生", "student", DEFAULT_TEMP_PASSWORD, student_no="stu"),
        ])
        classes_data = versioned_data("classes", "classes", [{
            "id": "class",
            "name": "class",
            "grade": "",
            "klass": "class",
            "createdAt": now_str(),
            "teacherUsernames": ["mr"],
            "studentUsernames": ["stu"],
        }])
        assignments_data = versioned_data("assignments", "assignments", [])
        history_data = versioned_data("history", "items", [])
        if db.is_ready():
            db.reset_business_data(users_data, classes_data, assignments_data, history_data)
        else:
            _save_json_file(USERS_PATH, users_data)
            _save_json_file(CLASSES_PATH, classes_data)
            _save_json_file(ASSIGNMENTS_PATH, assignments_data)
            _save_json_file(HISTORY_PATH, history_data)
        if os.path.isdir(UPLOADS_DIR):
            shutil.rmtree(UPLOADS_DIR, ignore_errors=True)


def normalize_runtime_data(source_data):
    """在内存中规范旧数据；首次迁移失败时绝不改写原 JSON。"""
    normalized = {
        "config": copy.deepcopy(source_data.get("config") or {}),
        "users": copy.deepcopy(source_data.get("users") or {"users": []}),
        "classes": copy.deepcopy(source_data.get("classes") or {"classes": []}),
        "assignments": copy.deepcopy(source_data.get("assignments") or {"assignments": []}),
        "history": copy.deepcopy(source_data.get("history") or {"items": []}),
    }
    users_data = normalized["users"]
    users_data["schemaVersion"] = DATA_SCHEMA_VERSION
    for user in users_data.setdefault("users", []):
        if user.get("role") not in ("admin", "teacher", "student"):
            user["role"] = "student"
        user.setdefault("grade", "")
        user.setdefault("klass", "")
        user.setdefault("schoolStatus", "active")
        if user.get("role") == "student" and not user.get("studentNo"):
            user["studentNo"] = user.get("username", "")
    system_admin = next((user for user in users_data["users"]
                         if user.get("username") == "admin"), None)
    if not system_admin:
        users_data["users"].insert(0, make_user("admin", "管理员", "admin"))
    else:
        system_admin["role"] = "admin"

    teachers = [user for user in users_data["users"] if user.get("role") == "teacher"]
    students = [user for user in users_data["users"] if user.get("role") == "student"]
    classes_data = normalized["classes"]
    classes_data["schemaVersion"] = DATA_SCHEMA_VERSION
    classes = classes_data.setdefault("classes", [])
    valid_teachers = {user["username"] for user in teachers}
    valid_students = {user["username"] for user in students}
    for cls in classes:
        if not cls.get("id"):
            cls["id"] = "c" + secrets.token_hex(6)
        cls.setdefault("studentUsernames", [])
        if "teacherUsernames" not in cls:
            owner = cls.get("createdBy")
            cls["teacherUsernames"] = [owner] if owner else []
        if not cls.get("name"):
            cls["name"] = class_name(cls.get("grade", ""), cls.get("klass", ""))
        cls["teacherUsernames"] = [username for username in cls["teacherUsernames"]
                                    if username in valid_teachers]
        cls["studentUsernames"] = [username for username in cls["studentUsernames"]
                                    if username in valid_students]
    if not classes and teachers:
        for index, teacher in enumerate(teachers):
            members = []
            for student in students:
                same_grade = teacher.get("grade") and teacher.get("grade") == student.get("grade")
                same_klass = teacher.get("klass") and teacher.get("klass") == student.get("klass")
                if index == 0 or (same_grade and same_klass):
                    members.append(student["username"])
            classes.append({
                "id": "c" + secrets.token_hex(6),
                "name": class_name(teacher.get("grade", ""), teacher.get("klass", "")),
                "grade": teacher.get("grade", ""),
                "klass": teacher.get("klass", ""),
                "createdAt": now_str(),
                "teacherUsernames": [teacher["username"]],
                "studentUsernames": sorted(set(members)),
            })

    memberships = {}
    for cls in classes:
        for username in cls.get("studentUsernames", []):
            memberships.setdefault(username, []).append(cls)
    for student in students:
        owned = memberships.get(student["username"], [])
        if not owned:
            continue
        preferred = [cls for cls in owned
                     if cls.get("grade", "") == student.get("grade", "")
                     and cls.get("klass", "") == student.get("klass", "")]
        chosen = (preferred or owned)[0]
        for cls in owned:
            if cls is not chosen:
                cls["studentUsernames"] = [username for username in cls.get("studentUsernames", [])
                                           if username != student["username"]]
        student["grade"] = chosen.get("grade", "")
        student["klass"] = chosen.get("klass", "")

    assignments_data = normalized["assignments"]
    assignments_data["schemaVersion"] = DATA_SCHEMA_VERSION
    assignments = assignments_data.setdefault("assignments", [])
    default_teacher = next((user["username"] for user in teachers), "mr")
    for assignment in assignments:
        if not assignment.get("createdBy"):
            assignment["createdBy"] = default_teacher
        if not assignment.get("classId"):
            cls = first_teacher_class(classes_data, assignment.get("createdBy"))
            if cls:
                assignment["classId"] = cls["id"]
        assignment.setdefault("lifecycleStatus", "published")
        if assignment["lifecycleStatus"] == "published":
            assignment.setdefault("publishedAt", assignment.get("createdAt", ""))
        assignment.setdefault("deadlineAt", "")
        assignment.setdefault("closedAt", "")
        assignment.setdefault("closedReason", "")
        assignment.setdefault("archivedAt", "")
        for essay in assignment.setdefault("essays", []):
            essay.setdefault("submissionType", "teacher_upload")
            essay.setdefault("lockedByTeacher",
                             essay.get("submissionType") == "teacher_upload"
                             and bool(essay.get("submittedBy")))
            essay.setdefault("submittedAt",
                             essay.get("gradedAt") or assignment.get("createdAt") or now_str())
            defaults = {
                "submissionOpen": False,
                "reopenedAt": "",
                "contentHash": content_hash(essay.get("text", "")),
                "jobId": "",
                "attempt": 0,
                "queuedAt": "",
                "startedAt": "",
                "finishedAt": "",
                "durationSeconds": None,
            }
            for key, value in defaults.items():
                essay.setdefault(key, value)

    history_data = normalized["history"]
    history_data["schemaVersion"] = DATA_SCHEMA_VERSION
    history_items = history_data.setdefault("items", [])
    for assignment in assignments:
        for essay in assignment.get("essays", []):
            if essay.get("status") != "done" or not essay.get("submittedBy") or not essay.get("feedback"):
                continue
            if essay.get("resultStatus") != "published":
                essay["resultStatus"] = "published"
                essay["publishedFeedback"] = copy.deepcopy(essay.get("feedback"))
                essay["resultPublishedAt"] = essay.get("gradedAt") or essay.get("submittedAt") or now_str()
            existing = next((item for item in history_items
                             if item.get("assignmentId") == assignment.get("id")
                             and item.get("username") == essay.get("submittedBy")
                             and item.get("text", "") == essay.get("text", "")), None)
            if existing:
                existing["sourceEssayId"] = essay.get("id", "")
                existing.setdefault("visibleAt", existing.get("gradedAt") or now_str())
                continue
            graded_at = essay.get("gradedAt") or now_str()
            history_items.insert(0, {
                "id": "h" + secrets.token_hex(6),
                "username": essay.get("submittedBy"),
                "title": assignment.get("title", ""),
                "prompt": assignment.get("prompt", ""),
                "criteria": assignment.get("criteria", ""),
                "wordLimit": assignment.get("wordLimit", ""),
                "assignmentId": assignment.get("id", ""),
                "assignmentTitle": assignment.get("title", ""),
                "classId": assignment.get("classId", ""),
                "sourceEssayId": essay.get("id", ""),
                "text": essay.get("text", ""),
                "result": copy.deepcopy(essay.get("feedback") or {}),
                "gradedAt": graded_at,
                "visibleAt": graded_at,
                "createdAt": graded_at,
            })
    history_data["items"] = trim_history_items(history_items)
    return normalized


def migrate_runtime_data():
    """兼容运行数据。SQLite 就绪后，所有变更均在数据库事务中提交。"""
    with io_lock:
        source = {
            "config": load_json(CONFIG_PATH, {}),
            "users": load_json(USERS_PATH, {"users": []}),
            "classes": load_json(CLASSES_PATH, {"classes": []}),
            "assignments": load_json(ASSIGNMENTS_PATH, {"assignments": []}),
            "history": load_json(HISTORY_PATH, {"items": []}),
        }
        normalized = normalize_runtime_data(source)
        if normalized["users"] != source["users"] or normalized["classes"] != source["classes"]:
            save_users_and_classes(normalized["users"], normalized["classes"])
        if normalized["assignments"] != source["assignments"]:
            save_json(ASSIGNMENTS_PATH, normalized["assignments"])
        if normalized["history"] != source["history"]:
            save_json(HISTORY_PATH, normalized["history"])


def _matches_known_default(row, cache):
    key = (row["password_scheme"], row["legacy_salt"], row["password_hash"])
    if key in cache:
        return cache[key]
    if row["password_scheme"] == "pbkdf2_sha256" or not row["legacy_salt"]:
        matched = any(check_password_hash(row["password_hash"], password)
                      for password in ("123456", DEFAULT_TEMP_PASSWORD))
    else:
        matched = any(
            hashlib.sha256((row["legacy_salt"] + password).encode("utf-8")).hexdigest()
            == row["password_hash"]
            for password in ("123456", DEFAULT_TEMP_PASSWORD)
        )
    cache[key] = matched
    return matched


def normalize_default_passwords():
    """One-time conversion of known temporary passwords to unique secure hashes."""
    marker = "temporary_passwords_v1"
    with db.connection() as conn:
        done = conn.execute("SELECT value FROM schema_meta WHERE key=?", (marker,)).fetchone()
        if done:
            return 0
        rows = conn.execute("""
            SELECT username,legacy_salt,password_hash,password_scheme
            FROM users WHERE must_change_password=1
            ORDER BY username
        """).fetchall()
    cache = {}
    targets = [row["username"] for row in rows if _matches_known_default(row, cache)]
    if not targets:
        with db.connection(write=True) as conn:
            conn.execute("INSERT INTO schema_meta(key,value) VALUES(?,?)", (marker, "0"))
        return 0
    with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
        hashes = list(pool.map(lambda _username: secure_password_hash(DEFAULT_TEMP_PASSWORD), targets))
    with db.connection(write=True) as conn:
        now = db._utc_iso()
        for username, password_hash in zip(targets, hashes):
            conn.execute("""
                UPDATE users SET legacy_salt='',password_hash=?,password_scheme='pbkdf2_sha256',
                    must_change_password=1,updated_at=? WHERE username=?
            """, (password_hash, now, username))
            conn.execute("DELETE FROM sessions WHERE username=?", (username,))
        conn.execute("INSERT INTO schema_meta(key,value) VALUES(?,?)", (marker, str(len(targets))))
        conn.execute("""
            INSERT INTO audit_logs(created_at,actor_username,event_type,target_type,target_id,detail_json)
            VALUES(?,NULL,'temporary_passwords_normalized','users','batch',?)
        """, (now, json.dumps({"count": len(targets)}, separators=(",", ":"))))
    return len(targets)
