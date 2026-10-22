"""Persistent grading queue and student submission routes."""

from .base import RouteSpec, create_blueprint
from flask import jsonify, request
import database as db
from essay_platform.core import modules as feature_modules
from essay_platform.core.auth import student_required, teacher_required
from essay_platform.core.config import ASSIGNMENTS_PATH, MAX_ESSAY_CHARS
from essay_platform.core.state import io_lock
from essay_platform.persistence.datasets import load_json
from essay_platform.services.assignments import (
    assignment_accepts_submission, assignment_visible_to_student,
    find_assignment, find_essay, find_teacher_assignment,
    refresh_expired_assignments,
)

MANIFEST = {
    "key": "grading", "dependencies": ["assignments"],
    "permissions": ["teacher", "student"], "navigation": ["grading"],
    "tasks": ["assignment-grading", "practice-grading"], "migrations": ["grading_jobs"],
}

ROUTES = (
    RouteSpec("POST", "/api/assignments/<aid>/grade", "api_assignments_grade"),
    RouteSpec("POST", "/api/assignments/<aid>/essays/<eid>/regrade", "api_assignments_regrade"),
    RouteSpec("POST", "/api/student/grade", "api_student_grade"),
    RouteSpec("GET", "/api/student/jobs/<job_id>", "api_student_job"),
)

from essay_platform.workers.grading import reserve_grade_jobs, schedule_grade_job, start_batch
from essay_platform.services.operations import ai_quota_error
from essay_platform.repositories import revisions as revision_repository
from essay_platform.repositories.operations import count_user_active_grading_jobs

# 单学生同时排队的自主练习任务上限，防止刷爆全局四路队列。
MAX_STUDENT_ACTIVE_PRACTICE_JOBS = 3

@teacher_required
def api_assignments_grade(aid):
    body = request.get_json(silent=True) or {}
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    if not assignment:
        return jsonify({"error": "作业不存在"}), 404
    if assignment.get("lifecycleStatus") == "archived":
        return jsonify({"error": "已归档作业不可再开始批改"}), 409
    if not assignment.get("essays"):
        return jsonify({"error": "请先上传并确认学生作文名单"}), 400
    quota_error = ai_quota_error()
    if quota_error:
        return quota_error
    started, existing = start_batch(aid, force=bool(body.get("force")))
    if not started and not existing:
        return jsonify({"error": "没有待批改的作文"}), 400
    return jsonify({"ok": True, "started": started, "existing": existing})


@teacher_required
def api_assignments_regrade(aid, eid):
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    essay = find_essay(assignment, eid) if assignment else None
    if not essay:
        return jsonify({"error": "作文不存在"}), 404
    if assignment.get("lifecycleStatus") == "archived":
        return jsonify({"error": "已归档作业不可重新批改"}), 409
    quota_error = ai_quota_error()
    if quota_error:
        return quota_error
    started, existing = reserve_grade_jobs(aid, essay_ids=[eid], force=True)
    return jsonify({"ok": True, "started": started, "existing": existing})

@student_required
def api_student_grade():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    criteria = (body.get("criteria") or "").strip()
    word_limit = (body.get("wordLimit") or "").strip()
    text = (body.get("text") or "").strip()
    assignment_id = (body.get("assignmentId") or "").strip()
    target_feature = "assignments" if assignment_id else "practice"
    target_state = feature_modules.state(target_feature)
    if target_state != "enabled":
        return jsonify({
            "error": "该功能当前已停用，请联系管理员",
            "code": "module_disabled",
            "module": target_feature,
            "state": target_state,
        }), 503
    quota_error = ai_quota_error()
    if quota_error:
        return quota_error
    if not text:
        return jsonify({"error": "请先粘贴或上传作文内容"}), 400
    if len(text) < 10:
        return jsonify({"error": "作文内容太短，无法评阅"}), 400
    if len(text) > MAX_ESSAY_CHARS:
        return jsonify({"error": f"作文正文不能超过 {MAX_ESSAY_CHARS:,} 个字符"}), 400
    if len(title) > 120:
        return jsonify({"error": "作文标题不能超过 120 个字符"}), 400
    user = request.user
    if assignment_id:
        refresh_expired_assignments()
        adata = load_json(ASSIGNMENTS_PATH, {"assignments": []})
        assignment = find_assignment(adata, assignment_id)
        if not assignment:
            return jsonify({"error": "所选作业不存在"}), 400
        if not assignment_visible_to_student(assignment, user):
            return jsonify({"error": "你不在该作业所属班级，不能提交"}), 403
        active_return = revision_repository.student_active_returns(user["username"]).get(assignment_id)
        if not assignment_accepts_submission(assignment, user) and not active_return:
            return jsonify({"error": "作业尚未发布或已经关闭，当前不能提交"}), 409
        with io_lock:
            reserved = db.create_assignment_submission_job(
                assignment_id, user["username"], text, title)
        if not reserved.get("ok"):
            if reserved.get("reason") == "locked":
                return jsonify({"error": "该作业已提交并锁定，请联系老师重新开放后再提交"}), 409
            if reserved.get("reason") == "in_progress":
                return jsonify({"error": "该作文正在批改，暂时不能修改正文"}), 409
            if reserved.get("reason") == "assignment_closed":
                return jsonify({"error": "作业已经关闭，当前不能提交"}), 409
            if reserved.get("reason") == "resubmit_deadline_passed":
                return jsonify({"error": "老师设置的重交截止时间已过，请联系老师"}), 409
            return jsonify({"error": "作业提交状态已变化，请刷新后重试"}), 409
    else:
        if db.is_ready() and count_user_active_grading_jobs(user["username"]) >= MAX_STUDENT_ACTIVE_PRACTICE_JOBS:
            return jsonify({
                "error": "同时排队的自主练习过多，请等待当前批改完成后再提交",
                "code": "too_many_active_jobs",
            }), 429
        with io_lock:
            reserved = db.create_practice_job(
                user["username"], title, prompt, criteria, word_limit, text)
    schedule_grade_job(reserved["jobId"])
    return jsonify({
        "jobId": reserved["jobId"],
        "submissionId": reserved["submissionId"],
        "status": "queued" if reserved["status"] == "queued" else "grading",
        "existing": bool(reserved.get("existing")),
    }), 202


@student_required
def api_student_job(job_id):
    job = db.get_grading_job(job_id)
    if not job or job.get("username") != request.user["username"]:
        return jsonify({"error": "批改任务不存在"}), 404
    status = {"running": "grading"}.get(job["status"], job["status"])
    payload = {
        "jobId": job["jobId"], "submissionId": job["submissionId"],
        "targetType": job["targetType"], "status": status,
        "attempt": job["attempt"], "queuedAt": job["queuedAt"],
        "startedAt": job["startedAt"], "finishedAt": job["finishedAt"],
        "durationSeconds": job["durationSeconds"],
    }
    if job["status"] == "error":
        payload.update({"error": job["error"], "errorType": job["errorType"],
                        "errorAdvice": job["errorAdvice"]})
    elif job["status"] == "done" and job["targetType"] == "practice":
        result = dict(job.get("result") or {})
        result["gradedAt"] = job["finishedAt"]
        payload.update({"result": result, "historyId": job["historyId"]})
    elif job["status"] == "done" and job.get("resultStatus") == "published":
        payload["status"] = "published"
        payload["historyId"] = job.get("publishedHistoryId", "")
    elif job["status"] == "done":
        payload["status"] = "pending_review"
    return jsonify(payload)

def build_blueprint():
    return create_blueprint("grading", __name__, ROUTES, default_feature="grading", namespace=globals())
