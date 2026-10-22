"""Excellent essay showcase routes."""

from .base import RouteSpec, create_blueprint
from flask import jsonify, request
from essay_platform.core.audit import audit_event
from essay_platform.core.auth import login_required, teacher_required
from essay_platform.services import showcase as showcase_service

MANIFEST = {
    "key": "showcase", "dependencies": ["assignments", "review"],
    "permissions": ["teacher", "student"], "navigation": ["showcase"],
    "tasks": [], "migrations": ["assignment_showcase"],
}

ROUTES = (
    RouteSpec("GET", "/api/assignments/<aid>/showcase", "api_assignment_showcase"),
    RouteSpec("PUT", "/api/assignments/<aid>/showcase", "api_assignment_showcase_update"),
)

from essay_platform.core.errors import service_error_response as _service_error

@login_required
def api_assignment_showcase(aid):
    try:
        items = showcase_service.list_showcase(
            aid, request.user["username"], request.user["role"])
    except showcase_service.ShowcaseError as error:
        return _service_error(error)
    return jsonify({"items": items})


@teacher_required
def api_assignment_showcase_update(aid):
    body = request.get_json(silent=True) or {}
    try:
        result = showcase_service.set_showcase(
            aid, request.user["username"], body.get("essayIds"), bool(body.get("confirm")))
    except showcase_service.ShowcaseError as error:
        return _service_error(error)
    audit_event("assignment_showcase_updated", target_type="assignment", target_id=aid,
                detail={"essayIds": result.get("essayIds", [])})
    return jsonify(result)

def build_blueprint():
    return create_blueprint("showcase", __name__, ROUTES, default_feature="showcase", namespace=globals())
