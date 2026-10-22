"""Authentication, account self-service, and non-disableable control routes."""

from .base import RouteSpec, create_blueprint
import hashlib
import secrets
import time
from flask import jsonify, request
import database as db
from essay_platform.core.audit import audit_event, client_ip
from essay_platform.core.auth import (
    login_required, secure_password_hash, validate_new_password,
    verify_user_password,
)
from essay_platform.core.config import (
    LOGIN_FAILURE_LIMIT, LOGIN_FAILURE_WINDOW, LOGIN_LOCK_DURATION,
    SESSION_TTL,
)
from essay_platform.modules.operations import (
    api_admin_audit,
    api_admin_module_state,
    api_admin_modules,
    api_features,
)

MANIFEST = {
    "key": "auth", "dependencies": [], "permissions": ["public", "login", "admin"],
    "navigation": [], "tasks": [], "migrations": [],
}

ROUTES = (
    RouteSpec("POST", "/api/login", "api_login"),
    RouteSpec("POST", "/api/register", "api_register"),
    RouteSpec("GET", "/api/me", "api_me"),
    RouteSpec("POST", "/api/logout", "api_logout"),
    RouteSpec("POST", "/api/account/password", "api_account_password"),
    RouteSpec("GET", "/api/features", "api_features"),
    RouteSpec("GET", "/api/admin/modules", "api_admin_modules"),
    RouteSpec("POST", "/api/admin/modules/<module_key>/state", "api_admin_module_state"),
    RouteSpec("GET", "/api/admin/audit", "api_admin_audit"),
)



def api_login():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    ip_address = client_ip()
    remaining = db.login_lock_remaining(username, ip_address)
    if remaining:
        audit_event("login_blocked", target_type="user", target_id=username,
                    detail={"retryAfter": remaining})
        return jsonify({"error": "登录失败次数过多，请稍后重试",
                        "retryAfter": remaining}), 429
    user = db.get_user(username)
    if not user or not user.get("active", True) or not verify_user_password(user, password):
        remaining = db.record_login_failure(
            username, ip_address, window_seconds=LOGIN_FAILURE_WINDOW,
            max_failures=LOGIN_FAILURE_LIMIT, lock_seconds=LOGIN_LOCK_DURATION)
        audit_event("login_failed", target_type="user", target_id=username,
                    detail={"locked": bool(remaining)})
        status = 429 if remaining else 401
        payload = {"error": ("登录失败次数过多，请稍后重试"
                             if remaining else "用户名或密码错误")}
        if remaining:
            payload["retryAfter"] = remaining
        return jsonify(payload), status

    db.clear_login_failures(username, ip_address)
    if user.get("passwordScheme") != "pbkdf2_sha256":
        db.update_user_password(
            username, secure_password_hash(password),
            must_change=bool(user.get("mustChangePassword")))
        user = db.get_user(username)
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + SESSION_TTL
    db.create_session(
        hashlib.sha256(token.encode("utf-8")).hexdigest(), username, expires_at,
        ip_address, request.headers.get("User-Agent", ""))
    audit_event("login_succeeded", actor=username, target_type="session", target_id=username)
    return jsonify({
        "token": token, "username": username,
        "display_name": user.get("display_name", username), "role": user["role"],
        "mustChangePassword": bool(user.get("mustChangePassword")),
        "expiresAt": int(expires_at),
    })


def api_register():
    return jsonify({"error": "系统已关闭自助注册，请联系管理员创建账号"}), 403


@login_required
def api_me():
    u = request.user
    return jsonify({"username": u["username"], "display_name": u.get("display_name"),
                    "role": u["role"], "grade": u.get("grade", ""),
                    "klass": u.get("klass", ""),
                    "employeeNo": u.get("employeeNo", ""),
                    "subject": u.get("subject", ""),
                    "schoolStatus": u.get("schoolStatus", "active"),
                    "mustChangePassword": bool(u.get("mustChangePassword"))})


@login_required
def api_logout():
    audit_event("logout", target_type="session", target_id=request.user["username"])
    db.revoke_session(getattr(request, "auth_token_hash", ""))
    return jsonify({"ok": True})


@login_required
def api_account_password():
    body = request.get_json(silent=True) or {}
    current_password = body.get("currentPassword") or ""
    new_password = body.get("newPassword") or ""
    policy_error = validate_new_password(new_password)
    if policy_error:
        return jsonify({"error": policy_error}), 400
    if not verify_user_password(request.user, current_password):
        audit_event("password_change_failed", target_type="user",
                    target_id=request.user["username"], detail={"reason": "current_password"})
        return jsonify({"error": "当前密码不正确"}), 400
    if secrets.compare_digest(current_password, new_password):
        return jsonify({"error": "新密码不能与当前密码相同"}), 400
    username = request.user["username"]
    db.update_user_password(username, secure_password_hash(new_password), must_change=False)
    db.revoke_user_sessions(username)
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + SESSION_TTL
    db.create_session(
        hashlib.sha256(token.encode("utf-8")).hexdigest(), username, expires_at,
        client_ip(), request.headers.get("User-Agent", ""))
    audit_event("password_changed", actor=username, target_type="user", target_id=username)
    return jsonify({"ok": True, "token": token, "expiresAt": int(expires_at)})

def build_blueprint():
    return create_blueprint("auth", __name__, ROUTES, namespace=globals())
