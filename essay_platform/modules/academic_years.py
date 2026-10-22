"""Academic-year archives and current-class record routes."""

from .base import RouteSpec, create_blueprint
import os
from flask import jsonify, request
from essay_platform.core.audit import audit_event
from essay_platform.core.auth import admin_required, student_required
from essay_platform.persistence.datasets import backup_data_snapshot
from essay_platform.services import academic_years as academic_year_service

MANIFEST = {
    "key": "academic_years", "dependencies": ["directory", "assignments", "reporting"],
    "permissions": ["admin", "student"], "navigation": ["archives"],
    "tasks": [], "migrations": ["academic_years", "student_enrollments"],
}

ROUTES = (
    RouteSpec("GET", "/api/student/assignments/archive", "api_student_assignment_archive"),
    RouteSpec("GET", "/api/admin/academic-years", "api_admin_academic_years"),
    RouteSpec("POST", "/api/admin/academic-years/rollover/preview", "api_admin_academic_year_preview"),
    RouteSpec("POST", "/api/admin/academic-years/rollover/confirm", "api_admin_academic_year_confirm"),
)

from essay_platform.core.errors import service_error_response as _service_error
from essay_platform.repositories import academic_years as academic_year_repository

@student_required
def api_student_assignment_archive():
    return jsonify(academic_year_repository.student_assignment_archive(request.user["username"]))


@admin_required
def api_admin_academic_years():
    return jsonify({"academicYears": academic_year_repository.list_academic_years()})


@admin_required
def api_admin_academic_year_preview():
    body = request.get_json(silent=True) or {}
    try:
        result = academic_year_service.preview(body.get("targetStartYear"))
    except academic_year_service.AcademicYearError as error:
        return _service_error(error)
    return jsonify(result)


@admin_required
def api_admin_academic_year_confirm():
    body = request.get_json(silent=True) or {}
    try:
        backup = backup_data_snapshot("academic_year")
        result = academic_year_service.confirm(
            body.get("targetStartYear"), body.get("classes"))
    except academic_year_service.AcademicYearError as error:
        return _service_error(error)
    audit_event("academic_year_rolled_over", target_type="academic_year",
                target_id=result["academicYearId"], detail={
                    "createdClasses": len(result.get("createdClasses", [])),
                    "graduated": result.get("graduated", 0),
                    "backup": os.path.basename(backup) if backup else "",
                })
    return jsonify(result)

def build_blueprint():
    return create_blueprint("academic_years", __name__, ROUTES, default_feature="academic_years", namespace=globals())
