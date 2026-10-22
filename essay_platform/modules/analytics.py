"""Student growth and teacher-only class analytics routes."""

from .base import RouteSpec, create_blueprint
from flask import jsonify, request
from essay_platform.core.audit import audit_event
from essay_platform.core.auth import student_required, teacher_required
from essay_platform.services import analytics as analytics_service

MANIFEST = {
    "key": "analytics", "dependencies": ["assignments", "reporting"],
    "permissions": ["teacher", "student"], "navigation": ["growth", "analytics"],
    "tasks": ["class_insight"], "migrations": ["assignment_insights"],
}

ROUTES = (
    RouteSpec("GET", "/api/student/trends", "api_student_trends"),
    RouteSpec("GET", "/api/teacher/students/<username>/trends", "api_teacher_student_trends"),
    RouteSpec("GET", "/api/assignments/<aid>/analytics/ranking", "api_assignment_ranking"),
    RouteSpec("GET", "/api/assignments/<aid>/analytics/insight", "api_assignment_insight"),
    RouteSpec("POST", "/api/assignments/<aid>/analytics/insight/generate", "api_assignment_insight_generate"),
    RouteSpec("PUT", "/api/assignments/<aid>/analytics/insight", "api_assignment_insight_update"),
)

from essay_platform.adapters import ai as ai_adapter
from essay_platform.core.errors import service_error_response as _service_error
from essay_platform.services.operations import ai_quota_error

@student_required
def api_student_trends():
    return jsonify(analytics_service.student_trends(request.user["username"]))


@teacher_required
def api_teacher_student_trends(username):
    try:
        result = analytics_service.teacher_student_trend(request.user["username"], username)
    except analytics_service.AnalyticsError as error:
        return _service_error(error)
    return jsonify(result)


@teacher_required
def api_assignment_ranking(aid):
    try:
        result = analytics_service.assignment_ranking(aid, request.user["username"])
    except analytics_service.AnalyticsError as error:
        return _service_error(error)
    return jsonify({"ranking": result})


@teacher_required
def api_assignment_insight(aid):
    record = analytics_service.get_insight(aid, request.user["username"])
    return jsonify({"insight": record})


@teacher_required
def api_assignment_insight_generate(aid):
    body = request.get_json(silent=True) or {}
    quota_error = ai_quota_error()
    if quota_error:
        return quota_error
    try:
        with ai_adapter.ai_task_scope("class_insight", json_mode=True):
            record = analytics_service.generate_insight(
                aid, request.user["username"], ai_adapter.call_llm,
                confirm=bool(body.get("confirm")))
    except analytics_service.AnalyticsError as error:
        return _service_error(error)
    audit_event("assignment_insight_generated", target_type="assignment", target_id=aid,
                detail={"version": record.get("version", 0)})
    return jsonify({"insight": record})


@teacher_required
def api_assignment_insight_update(aid):
    body = request.get_json(silent=True) or {}
    try:
        record = analytics_service.update_insight(
            aid, request.user["username"], body.get("insight"), body.get("version"))
    except analytics_service.AnalyticsError as error:
        return _service_error(error)
    audit_event("assignment_insight_updated", target_type="assignment", target_id=aid,
                detail={"version": record.get("version", 0)})
    return jsonify({"insight": record})

def build_blueprint():
    return create_blueprint("analytics", __name__, ROUTES, default_feature="analytics", namespace=globals())
