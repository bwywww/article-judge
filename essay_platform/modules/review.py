"""Teacher review, result publication, and on-demand upgrade routes."""

from .base import RouteSpec, create_blueprint
import copy
from flask import jsonify, request
import database as db
from essay_platform.core.audit import audit_event
from essay_platform.core.auth import teacher_required
from essay_platform.core.config import ASSIGNMENTS_PATH
from essay_platform.core.state import io_lock, upgrade_in_flight
from essay_platform.core.time import content_hash, now_iso
from essay_platform.persistence.datasets import load_json, save_json
from essay_platform.services.assignments import (
    find_essay, find_teacher_assignment, refresh_expired_assignments,
)

MANIFEST = {
    "key": "review", "dependencies": ["assignments", "grading"],
    "permissions": ["teacher", "student"],
    "navigation": ["review"], "tasks": ["upgrade"], "migrations": ["published_results"],
}

ROUTES = (
    RouteSpec("POST", "/api/assignments/<aid>/submissions/<username>/reopen", "api_assignment_submission_reopen"),
    RouteSpec("POST", "/api/assignments/<aid>/submissions/reopen", "api_assignment_submissions_reopen"),
    RouteSpec("POST", "/api/assignments/<aid>/essays/<eid>/publish-result", "api_assignment_publish_result"),
    RouteSpec("PUT", "/api/assignments/<aid>/essays/<eid>/feedback", "api_assignment_feedback_update"),
    RouteSpec("POST", "/api/assignments/<aid>/publish-results", "api_assignment_publish_results"),
    RouteSpec("POST", "/api/assignments/<aid>/essays/<eid>/upgrade", "api_assignment_essay_upgrade"),
    RouteSpec("POST", "/api/student/history/<hid>/upgrade", "api_student_history_upgrade"),
)

from essay_platform.adapters.ai import normalize_teacher_feedback, request_upgrade_text
from essay_platform.services.operations import ai_quota_error
from essay_platform.modules.reporting import api_student_history_upgrade

@teacher_required
def api_assignment_submission_reopen(aid, username):
    refresh_expired_assignments()
    with io_lock:
        result = db.reopen_assignment_submissions(
            aid, request.user["username"], [username])
    if not result.get("ok"):
        if result.get("reason") == "not_found":
            return jsonify({"error": "作业不存在"}), 404
        return jsonify({"error": "作业已关闭，请先重新开放作业"}), 409
    if not result["reopened"]:
        if username in result["skipped"]:
            return jsonify({"error": "该作文正在批改，完成后才能重新开放"}), 409
        return jsonify({"error": "该学生还没有提交记录"}), 404
    audit_event("assignment_submission_reopened", target_type="assignment", target_id=aid,
                detail={"students": [username]})
    return jsonify({"ok": True, "submissionStatus": "resubmission_open"})


@teacher_required
def api_assignment_submissions_reopen(aid):
    refresh_expired_assignments()
    body = request.get_json(silent=True) or {}
    usernames = body.get("usernames")
    if usernames is not None and not isinstance(usernames, list):
        return jsonify({"error": "usernames 必须是数组"}), 400
    with io_lock:
        result = db.reopen_assignment_submissions(
            aid, request.user["username"], usernames)
    if not result.get("ok"):
        if result.get("reason") == "not_found":
            return jsonify({"error": "作业不存在"}), 404
        return jsonify({"error": "作业已关闭，请先重新开放作业"}), 409
    audit_event("assignment_submissions_reopened", target_type="assignment", target_id=aid,
                detail={"count": len(result["reopened"])})
    return jsonify(result)


@teacher_required
def api_assignment_publish_result(aid, eid):
    with io_lock:
        result = db.publish_assignment_results(
            aid, request.user["username"], [eid], history_limit=0)
    if not result.get("ok"):
        if result.get("reason") == "assignment_not_released":
            return jsonify({"error": "请先发布作业，再向学生发布批改结果"}), 409
        return jsonify({"error": "作业不存在"}), 404
    if result["missing"]:
        return jsonify({"error": "作文不存在"}), 404
    if not result["published"]:
        return jsonify({"error": "该作文尚未完成批改、无需发布或已发布"}), 409
    audit_event("assignment_result_published", target_type="assignment", target_id=aid,
                detail={"essayId": eid})
    return jsonify({"ok": True, **result})


@teacher_required
def api_assignment_feedback_update(aid, eid):
    body = request.get_json(silent=True) or {}
    try:
        expected_version = int(body.get("feedbackVersion"))
    except (TypeError, ValueError):
        return jsonify({"error": "缺少有效的批改结果版本号"}), 400
    try:
        feedback = normalize_teacher_feedback(body.get("feedback"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    assignment = find_teacher_assignment(
        load_json(ASSIGNMENTS_PATH, {"assignments": []}), aid, request.user)
    essay = find_essay(assignment, eid) if assignment else None
    if not essay:
        return jsonify({"error": "作文不存在"}), 404
    current_version = int(essay.get("feedbackVersion") or 0)
    if current_version != expected_version:
        return jsonify({
            "error": "该批改结果已被其他页面修改，请刷新后重新编辑",
            "code": "feedback_version_conflict",
            "feedbackVersion": current_version,
        }), 409
    previous = essay.get("feedback") or {}
    changed_fields = []
    for field in ("score", "level", "summary", "dimensions", "strengths",
                  "weaknesses", "suggestions", "modelSnippet", "upgrade"):
        if previous.get(field) != feedback.get(field):
            changed_fields.append(field)
    if not changed_fields:
        return jsonify({
            "ok": True,
            "unchanged": True,
            "feedback": previous,
            "feedbackVersion": current_version,
            "feedbackEditedBy": essay.get("feedbackEditedBy", ""),
            "feedbackEditedAt": essay.get("feedbackEditedAt", ""),
            "resultStatus": essay.get("resultStatus", "pending_review"),
            "wasPublished": False,
        })
    result = db.update_assignment_feedback(
        aid, eid, request.user["username"], feedback, expected_version)
    if not result.get("ok"):
        if result.get("reason") == "version_conflict":
            return jsonify({
                "error": "该批改结果已被其他页面修改，请刷新后重新编辑",
                "code": "feedback_version_conflict",
                "feedbackVersion": result.get("feedbackVersion", 0),
            }), 409
        if result.get("reason") == "not_ready":
            return jsonify({"error": "该作文还没有可编辑的批改结果"}), 409
        return jsonify({"error": "作文不存在"}), 404
    audit_event("assignment_feedback_edited", target_type="essay", target_id=eid,
                detail={"assignmentId": aid, "changedFields": changed_fields,
                        "wasPublished": bool(result.get("wasPublished"))})
    return jsonify({"ok": True, "feedback": feedback, **result})


@teacher_required
def api_assignment_publish_results(aid):
    body = request.get_json(silent=True) or {}
    essay_ids = body.get("essayIds")
    if essay_ids is not None and not isinstance(essay_ids, list):
        return jsonify({"error": "essayIds 必须是数组"}), 400
    with io_lock:
        result = db.publish_assignment_results(
            aid, request.user["username"], essay_ids, history_limit=0)
    if not result.get("ok"):
        if result.get("reason") == "assignment_not_released":
            return jsonify({"error": "请先发布作业，再向学生发布批改结果"}), 409
        return jsonify({"error": "作业不存在"}), 404
    audit_event("assignment_results_published", target_type="assignment", target_id=aid,
                detail={"count": len(result["published"])})
    return jsonify({"ok": True, **result, "count": len(result["published"])})


@teacher_required
def api_assignment_essay_upgrade(aid, eid):
    key = f"essay:{aid}:{eid}"
    with io_lock:
        data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
        assignment = find_teacher_assignment(data, aid, request.user)
        essay = find_essay(assignment, eid) if assignment else None
        if not essay or essay.get("status") != "done":
            return jsonify({"error": "该作文还没有完整批改结果"}), 400
        cached = (essay.get("feedback") or {}).get("upgrade", "")
        if cached:
            return jsonify({"upgrade": cached, "cached": True})
        quota_error = ai_quota_error()
        if quota_error:
            return quota_error
        if key in upgrade_in_flight:
            return jsonify({"error": "润色稿正在生成，请勿重复点击"}), 409
        upgrade_in_flight.add(key)
        expected_hash = content_hash(essay.get("text", ""))
        expected_version = int(essay.get("feedbackVersion") or 0)
        title = assignment.get("title", "")
        prompt = assignment.get("prompt", "")
        text = essay.get("text", "")
    try:
        try:
            upgrade = request_upgrade_text(title, prompt, text)
        except Exception as exc:
            return jsonify({"error": f"生成润色稿失败：{exc}"}), 400
        with io_lock:
            data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
            assignment = find_teacher_assignment(data, aid, request.user)
            essay = find_essay(assignment, eid) if assignment else None
            if (not essay or content_hash(essay.get("text", "")) != expected_hash
                    or int(essay.get("feedbackVersion") or 0) != expected_version):
                return jsonify({"error": "作文或批改结果已变化，润色稿未写入"}), 409
            feedback = copy.deepcopy(essay.get("feedback") or {})
            feedback["upgrade"] = upgrade
            result = db.update_assignment_feedback(
                aid, eid, request.user["username"], feedback, expected_version)
        if not result.get("ok"):
            return jsonify({"error": "批改结果已被其他页面修改，请刷新后重试"}), 409
        return jsonify({"upgrade": upgrade, "cached": False})
    finally:
        with io_lock:
            upgrade_in_flight.discard(key)

def build_blueprint():
    return create_blueprint("review", __name__, ROUTES, default_feature="review", namespace=globals())
