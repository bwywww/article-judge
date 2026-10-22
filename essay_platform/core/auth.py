"""Authentication, password policy, sessions and permission decorators."""

import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from functools import wraps

from flask import jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

import database as db
from essay_platform.core.audit import audit_event, client_ip
from essay_platform.core.config import (
    DEFAULT_TEMP_PASSWORD, PASSWORD_HASH_METHOD, PASSWORD_MIN_LENGTH, USERS_PATH,
)
from essay_platform.core.state import io_lock, sessions
from essay_platform.core.time import now_iso
from essay_platform.persistence.datasets import load_json, save_json


@dataclass(frozen=True)
class Actor:
    username: str
    permission: str

    @classmethod
    def from_user(cls, user):
        return cls(str(user.get("username") or ""), str(user.get("role") or ""))

def hash_password(password, salt=None):
    """返回 (salt, hash)，salt 为空时自动生成"""
    if not salt:
        salt = secrets.token_hex(8)
    h = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return salt, h


def secure_password_hash(password):
    return generate_password_hash(password, method=PASSWORD_HASH_METHOD, salt_length=16)


def verify_user_password(user, password):
    if not user or not password:
        return False
    if user.get("passwordScheme") == "pbkdf2_sha256" or not user.get("salt"):
        try:
            return check_password_hash(user.get("password_hash", ""), password)
        except (ValueError, TypeError):
            return False
    _, candidate = hash_password(password, user.get("salt"))
    return secrets.compare_digest(candidate, user.get("password_hash", ""))


def validate_new_password(password):
    if len(password or "") < PASSWORD_MIN_LENGTH:
        return f"密码至少需要 {PASSWORD_MIN_LENGTH} 位"
    if len(password) > 256:
        return "密码过长"
    return ""


def make_user(username, display_name, role, password=None, student_no="",
              employee_no="", subject=""):
    password = password or DEFAULT_TEMP_PASSWORD
    salt, h = hash_password(password)
    user = {"username": username, "display_name": display_name, "role": role,
            "salt": salt, "password_hash": h}
    if role == "student":
        user["studentNo"] = student_no or username
    elif role == "teacher":
        user["employeeNo"] = employee_no or username
        user["subject"] = subject
    return user


def make_secure_user(username, display_name, role, password, student_no="",
                     employee_no="", subject="", *, must_change=True,
                     precomputed_password_hash=""):
    user = {
        "username": username,
        "display_name": display_name,
        "role": role,
        "salt": "",
        "password_hash": precomputed_password_hash or secure_password_hash(password),
        "passwordScheme": "pbkdf2_sha256",
        "mustChangePassword": bool(must_change),
        "active": True,
        "createdAt": now_iso(),
    }
    if role == "student":
        user["studentNo"] = student_no or username
    elif role == "teacher":
        user["employeeNo"] = employee_no or username
        user["subject"] = subject
    return user

def apply_admin_password_override():
    """服务器可通过环境变量轮换系统管理员密码，不开放降权或删除入口。"""
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        return
    policy_error = validate_new_password(password)
    if policy_error:
        raise RuntimeError("ADMIN_PASSWORD " + policy_error)
    with io_lock:
        data = load_json(USERS_PATH, {"users": []})
        admin = next((u for u in data.get("users", [])
                      if u.get("username") == "admin"), None)
        if not admin:
            raise RuntimeError("系统管理员账号不存在")
        admin["role"] = "admin"
        if verify_user_password(admin, password) and admin.get("passwordScheme") == "pbkdf2_sha256":
            return
        if db.is_ready():
            db.update_user_password("admin", secure_password_hash(password), must_change=False)
            db.revoke_user_sessions("admin")
        else:
            admin.update({
                "salt": "", "password_hash": secure_password_hash(password),
                "passwordScheme": "pbkdf2_sha256", "mustChangePassword": False,
            })
            save_json(USERS_PATH, data)
        audit_event("admin_password_environment_override", actor="admin",
                    target_type="user", target_id="admin")

def current_user():
    token = request.headers.get("X-Token", "")
    if not token:
        return None
    if db.is_ready():
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        request.auth_token_hash = token_hash
        return db.resolve_session(token_hash)
    username = sessions.get(token)
    return next((user for user in load_json(USERS_PATH, {"users": []})["users"]
                 if user["username"] == username), None)


PASSWORD_CHANGE_EXEMPT_ENDPOINTS = {"api_me", "api_logout", "api_account_password"}


def authorize_request(required_role=""):
    user = current_user()
    if not user:
        return None, (jsonify({"error": "未登录或登录已过期，请重新登录"}), 401)
    endpoint = (request.endpoint or "").rsplit(".", 1)[-1]
    if user.get("mustChangePassword") and endpoint not in PASSWORD_CHANGE_EXEMPT_ENDPOINTS:
        return None, (jsonify({
            "error": "首次登录或密码重置后必须先修改密码",
            "mustChangePassword": True,
        }), 428)
    if required_role and user.get("role") != required_role:
        labels = {"admin": "管理员", "teacher": "老师", "student": "学生"}
        return None, (jsonify({"error": f"该功能仅{labels[required_role]}账号可用"}), 403)
    request.user = user
    return user, None


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        _, error = authorize_request()
        if error:
            return error
        return fn(*args, **kwargs)
    return wrapper


def teacher_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        _, error = authorize_request("teacher")
        if error:
            return error
        return fn(*args, **kwargs)
    return wrapper


def student_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        _, error = authorize_request("student")
        if error:
            return error
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        _, error = authorize_request("admin")
        if error:
            return error
        return fn(*args, **kwargs)
    return wrapper
