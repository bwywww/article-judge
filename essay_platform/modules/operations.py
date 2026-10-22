"""AI configuration and usage operations routes."""

from .base import RouteSpec, create_blueprint
import os
from flask import jsonify, request
import database as db
from essay_platform.core import modules as feature_modules
from essay_platform.core.audit import audit_event
from essay_platform.core.ai_endpoints import SAFE_ERROR_MESSAGE, validate_text_endpoint
from essay_platform.core.auth import admin_required, current_user, login_required
from essay_platform.core.config import BACKUPS_DIR, DATA_DIR
from essay_platform.core.secrets import public_secret_status
from essay_platform.adapters.ai import test_connection
from essay_platform.persistence.datasets import (
    backup_data_snapshot, get_config, save_config,
)
from essay_platform.services import recovery as recovery_service
from essay_platform.services.directory import pagination_args

MANIFEST = {
    "key": "operations", "dependencies": [], "permissions": ["admin"],
    "navigation": ["ai-settings", "operations"], "tasks": [],
    "migrations": ["ai_task_routes", "ai_usage"],
}

ROUTES = (
    RouteSpec("GET", "/api/config", "api_get_config", feature=""),
    RouteSpec("POST", "/api/config", "api_set_config"),
    RouteSpec("POST", "/api/config/test", "api_test_config", endpoint="api_test_config_legacy"),
    RouteSpec("POST", "/api/admin/ai/profiles/<profile>/test", "api_test_config",
              endpoint="api_test_config_profile"),
    RouteSpec("GET", "/api/admin/ai/routes", "api_admin_ai_routes"),
    RouteSpec("PUT", "/api/admin/ai/routes", "api_admin_ai_routes_update",
              endpoint="api_admin_ai_routes_update"),
    RouteSpec("GET", "/api/admin/ai/usage", "api_admin_ai_usage"),
    RouteSpec("GET", "/api/admin/recovery/backups", "api_admin_recovery_backups", feature=""),
    RouteSpec("POST", "/api/admin/recovery/backup", "api_admin_recovery_backup", feature=""),
    RouteSpec("POST", "/api/admin/recovery/preview", "api_admin_recovery_preview", feature=""),
    RouteSpec("POST", "/api/admin/recovery/schedule", "api_admin_recovery_schedule", feature=""),
    RouteSpec("GET", "/api/admin/school-settings", "api_admin_school_settings", feature=""),
    RouteSpec("PUT", "/api/admin/school-settings", "api_admin_school_settings_update", feature=""),
)

from essay_platform.core.errors import service_error_response as _service_error
from essay_platform.services.operations import SCHOOL_SETTING_KEYS, ai_quota_status

# 网页 AI 管理可配置的业务 profile（不含 code_develop 开发专用）。
WEB_AI_PROFILES = ("article_judge", "ecnu_plus")

def api_get_config():
    cfg = get_config()
    user = current_user()
    is_admin = bool(user and user.get("role") == "admin")
    secret_status = public_secret_status() if is_admin else {}
    profiles = ({key: secret_status.get(key, {}) for key in WEB_AI_PROFILES}
                if is_admin else {})
    return jsonify({"base_url": cfg["base_url"], "model": cfg["model"],
                    "has_key": bool(cfg["api_key"]),
                    "managedExternally": bool(cfg.get("managedExternally")) if is_admin else False,
                    "keySource": cfg.get("keySource", "") if is_admin else "",
                    "profiles": profiles})


@admin_required
def api_set_config():
    body = request.get_json(silent=True) or {}
    cfg = get_config()
    if body.get("base_url") is not None:
        try:
            cfg["base_url"] = validate_text_endpoint(body["base_url"])
        except ValueError:
            return jsonify({"error": SAFE_ERROR_MESSAGE}), 400
    if body.get("model") is not None:
        cfg["model"] = str(body["model"]).strip()
    try:
        cfg["base_url"] = validate_text_endpoint(cfg.get("base_url"))
    except ValueError:
        return jsonify({"error": SAFE_ERROR_MESSAGE}), 400
    if not cfg.get("base_url") or not cfg.get("model"):
        return jsonify({"error": "接口地址和模型不能为空"}), 400
    save_config(cfg)
    if body.get("model") is not None and db.is_ready():
        for route in db.list_ai_task_routes():
            if route["profile"] == "article_judge":
                db.update_ai_task_route(
                    route["taskType"], profile=route["profile"], model=cfg["model"],
                    thinking=route["thinking"], json_mode=route["jsonMode"],
                    max_tokens=route["maxTokens"], username=request.user["username"],
                )
    audit_event("ai_config_updated", target_type="config", target_id="ai",
                detail={"managedExternally": bool(cfg.get("managedExternally")),
                        "baseUrlChanged": body.get("base_url") is not None,
                        "modelChanged": body.get("model") is not None})
    return jsonify({"ok": True, "has_key": bool(cfg["api_key"]),
                    "managedExternally": bool(cfg.get("managedExternally"))})


@admin_required
def api_test_config(profile="article_judge"):
    body = request.get_json(silent=True) or {}
    if profile not in WEB_AI_PROFILES:
        return jsonify({"error": "未知的 AI 配置"}), 400
    cfg = get_config()
    if profile == "ecnu_plus":
        # 用程序生成、绝不包含学生材料的最小图片验证真实多模态能力。
        import io
        from PIL import Image, ImageDraw
        from essay_platform.adapters.ai_ocr import (
            ECNU_MODEL, call_ocr_vision, classify_ocr_error, image_to_data_url,
        )
        from essay_platform.core.secrets import get_ai_secret
        key, _source = get_ai_secret("ecnu_plus")
        if not key:
            return jsonify({"error": "ecnu_plus 密钥尚未配置"}), 400
        image = Image.new("RGB", (96, 40), "white")
        ImageDraw.Draw(image).text((8, 10), "TEST 123", fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        try:
            recognized = call_ocr_vision(image_to_data_url(buffer.getvalue(), "connection-test.png"))
            result = {"ok": True, "message": "多模态连接测试成功",
                      "recognized": recognized[:80]}
        except Exception as exc:
            error_type, advice = classify_ocr_error(exc)
            result = {"ok": False, "error": f"{error_type}：{advice}", "reason": error_type}
        if not result["ok"]:
            audit_event("ai_config_test_failed", target_type="config", target_id="ai",
                        detail={k: result[k] for k in ("statusCode", "reason") if k in result})
            return jsonify({"error": result["error"]}), 400
        audit_event("ai_config_test_succeeded", target_type="config", target_id="ai")
        return jsonify({**result, "model": ECNU_MODEL})
    requested_base_url = body["base_url"] if "base_url" in body else cfg["base_url"]
    try:
        base_url = validate_text_endpoint(requested_base_url)
    except ValueError:
        return jsonify({"error": SAFE_ERROR_MESSAGE}), 400
    api_key = cfg.get("api_key", "")
    model = body.get("model") or cfg["model"]
    if not api_key:
        return jsonify({"error": "article_judge 密钥尚未配置"}), 400
    result = test_connection(base_url, api_key, model)
    if not result["ok"]:
        audit_event("ai_config_test_failed", target_type="config", target_id="ai",
                    detail={key: result[key] for key in ("statusCode", "reason") if key in result})
        return jsonify({"error": result["error"]}), 400
    audit_event("ai_config_test_succeeded", target_type="config", target_id="ai")
    return jsonify(result)


@admin_required
def api_admin_ai_routes():
    secret_status = public_secret_status()
    return jsonify({
        "routes": [item for item in db.list_ai_task_routes()
                   if item["profile"] in WEB_AI_PROFILES],
        "profiles": {key: secret_status.get(key, {}) for key in WEB_AI_PROFILES},
    })

@admin_required
def api_admin_ai_routes_update():
    body = request.get_json(silent=True) or {}
    requested = body.get("routes")
    if not isinstance(requested, list) or not requested:
        return jsonify({"error": "请提供要更新的AI任务路由"}), 400
    existing = {
        item["taskType"]: item for item in db.list_ai_task_routes()
        if item["profile"] == "article_judge"
    }
    updated = []
    for item in requested:
        task_type = str(item.get("taskType") or "").strip()
        current = existing.get(task_type)
        if not current:
            return jsonify({"error": f"未知AI任务：{task_type}"}), 400
        model = str(item.get("model") or "").strip()
        if not model or len(model) > 100:
            return jsonify({"error": f"{task_type} 的模型无效"}), 400
        try:
            max_tokens = int(item.get("maxTokens", current["maxTokens"]))
        except (TypeError, ValueError):
            return jsonify({"error": f"{task_type} 的输出长度无效"}), 400
        if not 64 <= max_tokens <= 12000:
            return jsonify({"error": f"{task_type} 的输出长度须为64到12000"}), 400
        updated.append(db.update_ai_task_route(
            task_type, profile=current["profile"], model=model,
            thinking=bool(item.get("thinking", current["thinking"])),
            json_mode=bool(item.get("jsonMode", current["jsonMode"])),
            max_tokens=max_tokens, username=request.user["username"],
        ))
    audit_event(
        "ai_routes_updated", target_type="config", target_id="ai_routes",
        detail={"taskTypes": [item["taskType"] for item in updated]},
    )
    return jsonify({"ok": True, "routes": updated})


@admin_required
def api_admin_ai_usage():
    page, page_size = pagination_args(request.args, default_size=30, max_size=100)
    profile = (request.args.get("profile") or "").strip()
    return jsonify(db.query_ai_usage(
        page=page, page_size=page_size,
        task_type=(request.args.get("taskType") or "").strip(),
        profile=profile if profile in WEB_AI_PROFILES else "",
    ))

@admin_required
def api_admin_audit():
    page, page_size = pagination_args(request.args, default_size=30, max_size=100)
    return jsonify(db.query_audit(
        query=(request.args.get("q") or "").strip(),
        event_type=(request.args.get("type") or "").strip(),
        page=page, page_size=page_size,
    ))

@login_required
def api_features():
    feature_modules.refresh_draining_modules()
    items = feature_modules.list_modules()
    return jsonify({"features": {
        item["key"]: {
            "status": item["status"],
            "available": item["status"] == "enabled",
        }
        for item in items
    }})


@admin_required
def api_admin_modules():
    feature_modules.refresh_draining_modules()
    return jsonify({"modules": feature_modules.list_modules()})


@admin_required
def api_admin_module_state(module_key):
    body = request.get_json(silent=True) or {}
    target = str(body.get("state") or "").strip()
    try:
        result = feature_modules.transition(
            module_key, target, request.user["username"])
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    audit_event(
        "module_state_changed", target_type="module", target_id=module_key,
        detail={"requestedState": target, "effectiveState": result["status"]},
    )
    return jsonify({"ok": True, "module": result})

@admin_required
def api_admin_recovery_backups():
    return jsonify({"backups": recovery_service.list_backups(BACKUPS_DIR)})


@admin_required
def api_admin_recovery_backup():
    path = backup_data_snapshot("manual")
    if not path:
        return jsonify({"error": "当前没有可备份的数据"}), 409
    audit_event("backup_created", target_type="backup", target_id=os.path.basename(path))
    return jsonify({"ok": True, "backupId": os.path.basename(path)})


@admin_required
def api_admin_recovery_preview():
    body = request.get_json(silent=True) or {}
    try:
        result = recovery_service.preview(BACKUPS_DIR, body.get("backupId", ""))
    except recovery_service.RecoveryError as error:
        return _service_error(error)
    return jsonify(result)


@admin_required
def api_admin_recovery_schedule():
    body = request.get_json(silent=True) or {}
    try:
        result = recovery_service.schedule_restore(
            DATA_DIR, BACKUPS_DIR, body.get("backupId", ""))
    except recovery_service.RecoveryError as error:
        return _service_error(error)
    audit_event("restore_scheduled", target_type="backup", target_id=result["id"])
    return jsonify({"ok": True, "restartRequired": True, "preview": result})


@admin_required
def api_admin_school_settings():
    settings = db.get_app_settings(list(SCHOOL_SETTING_KEYS))
    return jsonify({"settings": settings, "quota": ai_quota_status()})


@admin_required
def api_admin_school_settings_update():
    body = request.get_json(silent=True) or {}
    values = {
        "school_name": str(body.get("schoolName") or "").strip()[:100],
        "school_academic_year_note": str(body.get("academicYearNote") or "").strip()[:500],
    }
    for source, target in (("dailyTokenLimit", "ai_daily_token_limit"),
                           ("monthlyTokenLimit", "ai_monthly_token_limit")):
        try:
            number = int(body.get(source) or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "AI Token 限额必须是非负整数"}), 400
        if number < 0 or number > 10_000_000_000:
            return jsonify({"error": "AI Token 限额超出允许范围"}), 400
        values[target] = str(number)
    db.set_app_settings(values)
    audit_event("school_settings_updated", target_type="settings",
                detail={"fields": sorted(values)})
    return jsonify({"ok": True, "quota": ai_quota_status()})

def build_blueprint():
    return create_blueprint("operations", __name__, ROUTES, default_feature="operations", namespace=globals())
