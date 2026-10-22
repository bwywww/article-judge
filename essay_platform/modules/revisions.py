"""Submission return and revision history routes."""

from .base import RouteSpec, create_blueprint
from flask import jsonify, request
from essay_platform.core.audit import audit_event
from essay_platform.core.auth import login_required, teacher_required
from essay_platform.services import revisions as revision_service

MANIFEST = {
    "key": "revisions", "dependencies": ["assignments", "grading", "review"],
    "permissions": ["teacher", "student"], "navigation": ["revisions"],
    "tasks": ["resubmission"], "migrations": ["submission_versions", "submission_returns"],
}

ROUTES = (
    RouteSpec("POST", "/api/assignments/<aid>/essays/<eid>/return", "api_submission_return"),
    RouteSpec("POST", "/api/assignments/<aid>/essays/<eid>/return/cancel", "api_submission_return_cancel"),
    RouteSpec("GET", "/api/assignments/<aid>/essays/<eid>/versions", "api_submission_versions"),
)

from essay_platform.core.errors import service_error_response as _service_error

@teacher_required
def api_submission_return(aid, eid):
    body = request.get_json(silent=True) or {}
    try:
        result = revision_service.return_submission(
            aid, eid, request.user["username"], body.get("comment", ""),
            body.get("resubmitDeadlineAt", ""))
    except revision_service.RevisionError as error:
        return _service_error(error)
    audit_event("submission_returned", target_type="essay", target_id=eid,
                detail={"assignmentId": aid,
                        "hasDeadline": bool(result.get("resubmitDeadlineAt"))})
    return jsonify(result)


@teacher_required
def api_submission_return_cancel(aid, eid):
    try:
        result = revision_service.cancel_return(aid, eid, request.user["username"])
    except revision_service.RevisionError as error:
        return _service_error(error)
    audit_event("submission_return_cancelled", target_type="essay", target_id=eid,
                detail={"assignmentId": aid})
    return jsonify(result)


@login_required
def api_submission_versions(aid, eid):
    try:
        result = revision_service.timeline(
            aid, eid, request.user["username"], request.user["role"])
    except revision_service.RevisionError as error:
        return _service_error(error)
    return jsonify(result)

def build_blueprint():
    return create_blueprint("revisions", __name__, ROUTES, default_feature="revisions", namespace=globals())
