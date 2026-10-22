"""Teacher assignment lifecycle and detail routes."""

from .base import RouteSpec, create_blueprint
import os
import re
import secrets
from flask import jsonify, request, send_from_directory
import database as db
from essay_platform.adapters.reports import safe_filename
from essay_platform.core.audit import audit_event
from essay_platform.core.auth import login_required, teacher_required
from essay_platform.core.config import ASSIGNMENTS_PATH, CLASSES_PATH, UPLOADS_DIR
from essay_platform.core.state import io_lock
from essay_platform.core.time import deadline_is_future, now_str, parse_deadline
from essay_platform.persistence.datasets import load_json, save_json
from essay_platform.services.assignments import (
    assignment_accepts_submission, assignment_visible_to_student, essay_summary,
    find_teacher_assignment, find_user_submission, is_assignment_owner,
    refresh_expired_assignments, remove_uploads, submission_state_for,
)
from essay_platform.services.directory import (
    class_label, class_students, ensure_teacher_default_class, find_class,
    is_class_teacher,
)
from essay_platform.repositories import revisions as revision_repository

MANIFEST = {
    "key": "assignments", "dependencies": ["directory"],
    "permissions": ["teacher", "student"], "navigation": ["assignments"],
    "tasks": [], "migrations": ["assignments", "essays"],
}

ROUTES = (
    RouteSpec("GET", "/api/assignments", "api_assignments_list"),
    RouteSpec("POST", "/api/assignments", "api_assignments_create"),
    RouteSpec("PUT", "/api/assignments/<aid>", "api_assignments_update"),
    RouteSpec("DELETE", "/api/assignments/<aid>", "api_assignments_delete"),
    RouteSpec("POST", "/api/assignments/<aid>/publish", "api_assignment_publish"),
    RouteSpec("POST", "/api/assignments/<aid>/unpublish", "api_assignment_unpublish"),
    RouteSpec("POST", "/api/assignments/<aid>/close", "api_assignment_close"),
    RouteSpec("POST", "/api/assignments/<aid>/reopen", "api_assignment_reopen"),
    RouteSpec("POST", "/api/assignments/<aid>/archive", "api_assignment_archive"),
    RouteSpec("POST", "/api/assignments/<aid>/clone", "api_assignment_clone"),
    RouteSpec("GET", "/api/assignments/<aid>", "api_assignments_detail"),
    RouteSpec("GET", "/api/assignments/<aid>/source", "api_assignments_source"),
    RouteSpec("POST", "/api/assignments/<aid>/clear", "api_assignments_clear"),
)

@login_required
def api_assignments_list():
    """作业列表：老师看到统计信息；学生看到题目与要求（用于提交练习，看不到其他学生内容）"""
    refresh_expired_assignments()
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    is_teacher = request.user.get("role") == "teacher"
    active_returns = ({} if is_teacher else
                      revision_repository.student_active_returns(request.user["username"]))
    out = []
    for a in data["assignments"]:
        if is_teacher and not is_assignment_owner(a, request.user):
            continue
        if not is_teacher and not assignment_visible_to_student(a, request.user):
            continue
        item = {
            "id": a["id"], "title": a.get("title", ""), "createdAt": a.get("createdAt", ""),
            "lifecycleStatus": a.get("lifecycleStatus", "published"),
            "publishedAt": a.get("publishedAt", ""), "deadlineAt": a.get("deadlineAt", ""),
            "closedAt": a.get("closedAt", ""), "closedReason": a.get("closedReason", ""),
            "archivedAt": a.get("archivedAt", ""),
        }
        cname = class_label(a.get("classId"))
        if is_teacher:
            essays = a.get("essays", [])
            done = [e for e in essays if e.get("status") == "done"]
            scores = [e.get("score") for e in done if e.get("score") is not None]
            item.update({
                "total": len(essays), "done": len(done),
                "pendingReview": len([e for e in done if e.get("resultStatus") == "pending_review"]),
                "publishedResults": len([e for e in done if e.get("resultStatus") == "published"]),
                "avg": round(sum(scores) / len(scores), 1) if scores else None,
                "createdBy": a.get("createdBy", ""),
                "classId": a.get("classId", ""), "className": cname,
            })
        else:
            submission_state = submission_state_for(a, request.user["username"])
            student_submission = find_user_submission(a, request.user["username"])
            return_state = active_returns.get(a["id"], {})
            if (return_state.get("expired")
                    and submission_state["submissionStatus"] == "resubmission_open"):
                submission_state["submissionStatus"] = "resubmission_expired"
                submission_state["submissionLocked"] = True
            item.update({
                "prompt": a.get("prompt", ""), "criteria": a.get("criteria", ""),
                "wordLimit": a.get("wordLimit", ""),
                "studentTitle": (student_submission or {}).get("studentTitle", ""),
                "classId": a.get("classId", ""), "className": cname,
                "canSubmit": (assignment_accepts_submission(a, request.user)
                              or submission_state["submissionStatus"] == "resubmission_open"),
                **submission_state,
                "returnComment": return_state.get("comment", ""),
                "returnedAt": return_state.get("returnedAt", ""),
                "resubmitDeadlineAt": return_state.get("resubmitDeadlineAt", ""),
                "essayId": return_state.get("essayId", "") or (
                    student_submission or {}).get("id", ""),
            })
        out.append(item)
    out.sort(key=lambda item: (item.get("publishedAt") or item.get("createdAt") or "", item["id"]),
             reverse=True)
    return jsonify({"assignments": out})

@teacher_required
def api_assignments_create():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "请填写作业主题"}), 400
    class_id = (body.get("classId") or "").strip()
    if class_id:
        cls = find_class(load_json(CLASSES_PATH, {"classes": []}), class_id)
        if not is_class_teacher(cls, request.user):
            return jsonify({"error": "请选择自己的班级"}), 400
    else:
        cls = ensure_teacher_default_class(request.user)
        if not cls:
            return jsonify({"error": "请先联系管理员分配任教班级"}), 400
        class_id = cls["id"]
    try:
        deadline_at = parse_deadline(body.get("deadlineAt"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if deadline_at and not deadline_is_future(deadline_at):
        return jsonify({"error": "截止时间必须晚于当前时间"}), 400
    with io_lock:
        data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
        assignment = {
            "id": "a" + secrets.token_hex(6),
            "title": title,
            "prompt": (body.get("prompt") or "").strip(),
            "criteria": (body.get("criteria") or "").strip(),
            "wordLimit": (body.get("wordLimit") or "").strip(),
            "createdAt": now_str(),
            "createdBy": request.user["username"],
            "classId": class_id,
            "lifecycleStatus": "draft",
            "publishedAt": "",
            "deadlineAt": deadline_at,
            "closedAt": "",
            "closedReason": "",
            "archivedAt": "",
            "essays": [],
        }
        data["assignments"].insert(0, assignment)
        save_json(ASSIGNMENTS_PATH, data)
    audit_event("assignment_created", target_type="assignment", target_id=assignment["id"],
                detail={"classId": class_id})
    return jsonify({"id": assignment["id"], "lifecycleStatus": "draft"})

@teacher_required
def api_assignments_update(aid):
    body = request.get_json(silent=True) or {}
    with io_lock:
        data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
        assignment = find_teacher_assignment(data, aid, request.user)
        if not assignment:
            return jsonify({"error": "作业不存在"}), 404
        if assignment.get("lifecycleStatus") in ("closed", "archived"):
            return jsonify({"error": "已关闭或归档作业不可直接修改，请先重新开放"}), 409
        frozen_fields = ("title", "prompt", "criteria", "wordLimit", "classId")
        if assignment.get("essays") and any(body.get(key) is not None for key in frozen_fields):
            return jsonify({"error": "作业已有作文或提交，题目、要求、评分标准、字数和班级已冻结"}), 409
        if body.get("title") is not None:
            if not str(body["title"]).strip():
                return jsonify({"error": "作业主题不能为空"}), 400
            assignment["title"] = str(body["title"]).strip()
        for key in ("prompt", "criteria", "wordLimit"):
            if body.get(key) is not None:
                assignment[key] = str(body[key]).strip()
        if body.get("classId") is not None:
            class_id = str(body.get("classId") or "").strip()
            if not class_id:
                return jsonify({"error": "作业必须选择所属班级"}), 400
            cls = find_class(load_json(CLASSES_PATH, {"classes": []}), class_id)
            if not is_class_teacher(cls, request.user):
                return jsonify({"error": "请选择自己的班级"}), 400
            assignment["classId"] = class_id
        if body.get("deadlineAt") is not None:
            try:
                deadline_at = parse_deadline(body.get("deadlineAt"))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            if deadline_at and not deadline_is_future(deadline_at):
                return jsonify({"error": "截止时间必须晚于当前时间"}), 400
            assignment["deadlineAt"] = deadline_at
        save_json(ASSIGNMENTS_PATH, data)
    audit_event("assignment_updated", target_type="assignment", target_id=aid,
                detail={"fields": sorted(key for key in body if key in (*frozen_fields, "deadlineAt"))})
    return jsonify({"ok": True})

@teacher_required
def api_assignments_delete(aid):
    with io_lock:
        data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
        assignment = find_teacher_assignment(data, aid, request.user)
        if not assignment:
            return jsonify({"error": "作业不存在"}), 404
        if assignment.get("lifecycleStatus", "published") != "draft" or assignment.get("essays"):
            return jsonify({"error": "只有没有作文的草稿作业可以删除；其他作业请关闭或归档"}), 409
        data["assignments"] = [a for a in data["assignments"] if a["id"] != aid]
        save_json(ASSIGNMENTS_PATH, data)
    remove_uploads(aid)
    audit_event("assignment_deleted", target_type="assignment", target_id=aid)
    return jsonify({"ok": True})

@teacher_required
def api_assignment_clone(aid):
    body = request.get_json(silent=True) or {}
    with io_lock:
        data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
        source = find_teacher_assignment(data, aid, request.user)
        if not source:
            return jsonify({"error": "作业不存在"}), 404
        class_id = str(body.get("classId") or source.get("classId") or "").strip()
        cls = find_class(load_json(CLASSES_PATH, {"classes": []}), class_id)
        if not is_class_teacher(cls, request.user):
            return jsonify({"error": "请选择自己的当前任教班级"}), 400
        clone = {
            "id": "a" + secrets.token_hex(6),
            "title": str(body.get("title") or f"{source.get('title', '')}（副本）").strip(),
            "prompt": source.get("prompt", ""), "criteria": source.get("criteria", ""),
            "wordLimit": source.get("wordLimit", ""), "createdAt": now_str(),
            "createdBy": request.user["username"], "classId": class_id,
            "lifecycleStatus": "draft", "publishedAt": "", "deadlineAt": "",
            "closedAt": "", "closedReason": "", "archivedAt": "", "essays": [],
        }
        data["assignments"].insert(0, clone)
        save_json(ASSIGNMENTS_PATH, data)
    audit_event("assignment_cloned", target_type="assignment", target_id=clone["id"],
                detail={"sourceAssignmentId": aid, "classId": class_id})
    return jsonify({"ok": True, "id": clone["id"], "lifecycleStatus": "draft"})

def assignment_transition_response(aid, action):
    refresh_expired_assignments()
    body = request.get_json(silent=True) or {}
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    if not assignment:
        return jsonify({"error": "作业不存在"}), 404
    if assignment.get("lifecycleStatus") == "archived":
        return jsonify({"error": "已归档作业不可再变更状态"}), 409
    deadline_at = None
    if body.get("deadlineAt") is not None:
        try:
            deadline_at = parse_deadline(body.get("deadlineAt"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    resulting_deadline = assignment.get("deadlineAt", "") if deadline_at is None else deadline_at
    if action in ("publish", "reopen") and resulting_deadline and not deadline_is_future(resulting_deadline):
        return jsonify({"error": "发布或重新开放时，截止时间必须晚于当前时间"}), 400
    if (action == "reopen" and assignment.get("closedReason") == "deadline"
            and not deadline_at):
        return jsonify({"error": "到期作业重新开放时必须设置新的未来截止时间"}), 400
    with io_lock:
        result = db.transition_assignment(
            aid, request.user["username"], action, deadline_at=deadline_at)
    if not result.get("ok"):
        messages = {
            "not_found": ("作业不存在", 404),
            "has_submissions": ("作业已有提交，不能撤回为草稿", 409),
            "deadline_required": ("到期作业重新开放时必须设置新的截止时间", 400),
            "active_jobs": ("仍有作文正在排队或批改，暂时不能归档", 409),
            "pending_results": ("仍有批改结果等待审核发布，暂时不能归档", 409),
            "invalid_state": ("当前作业状态不允许此操作", 409),
        }
        message, status = messages.get(result.get("reason"), ("作业状态已变化，请刷新后重试", 409))
        return jsonify({"error": message, "lifecycleStatus": result.get("status", "")}), status
    audit_event(f"assignment_{action}", target_type="assignment", target_id=aid,
                detail={"status": result["status"], "deadlineAt": result.get("deadlineAt", "")})
    return jsonify({"ok": True, **result})

@teacher_required
def api_assignment_publish(aid):
    return assignment_transition_response(aid, "publish")

@teacher_required
def api_assignment_unpublish(aid):
    return assignment_transition_response(aid, "unpublish")

@teacher_required
def api_assignment_close(aid):
    return assignment_transition_response(aid, "close")

@teacher_required
def api_assignment_reopen(aid):
    return assignment_transition_response(aid, "reopen")

@teacher_required
def api_assignment_archive(aid):
    return assignment_transition_response(aid, "archive")

@teacher_required
def api_assignments_detail(aid):
    refresh_expired_assignments()
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    if not assignment:
        return jsonify({"error": "作业不存在"}), 404
    essays = assignment.get("essays", [])
    done = [e for e in essays if e.get("status") == "done"]
    scores = [e.get("score") for e in done if e.get("score") is not None]
    submitted_usernames = {essay.get("submittedBy") for essay in essays if essay.get("submittedBy")}
    missing_students = [student for student in class_students(assignment.get("classId", ""))
                        if student.get("username") not in submitted_usernames]
    return jsonify({
        "id": assignment["id"], "title": assignment.get("title", ""),
        "prompt": assignment.get("prompt", ""), "criteria": assignment.get("criteria", ""),
        "wordLimit": assignment.get("wordLimit", ""), "createdAt": assignment.get("createdAt", ""),
        "createdBy": assignment.get("createdBy", ""),
        "classId": assignment.get("classId", ""), "className": class_label(assignment.get("classId")),
        "hasSource": bool(assignment.get("sourceFile")),
        "lifecycleStatus": assignment.get("lifecycleStatus", "published"),
        "publishedAt": assignment.get("publishedAt", ""),
        "deadlineAt": assignment.get("deadlineAt", ""),
        "closedAt": assignment.get("closedAt", ""),
        "closedReason": assignment.get("closedReason", ""),
        "archivedAt": assignment.get("archivedAt", ""),
        "canDelete": (assignment.get("lifecycleStatus", "published") == "draft" and not essays),
        "stats": {
            "total": len(essays), "done": len(done),
            "error": len([e for e in essays if e.get("status") == "error"]),
            "queued": len([e for e in essays if e.get("status") == "queued"]),
            "grading": len([e for e in essays if e.get("status") == "grading"]),
            "pendingReview": len([e for e in done if e.get("resultStatus") == "pending_review"]),
            "published": len([e for e in done if e.get("resultStatus") == "published"]),
            "missing": len(missing_students),
            "avg": round(sum(scores) / len(scores), 1) if scores else None,
        },
        "missingStudents": missing_students,
        "essays": [essay_summary(e) for e in essays],
    })

@teacher_required
def api_assignments_source(aid):
    """下载该作业上传的原始 PDF/Word/TXT 文件。"""
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    if not assignment:
        return jsonify({"error": "作业不存在"}), 404
    fname = assignment.get("sourceFile")
    if not fname or not re.fullmatch(r"source_[0-9a-f]{8}\.(?:pdf|docx|txt|md|png|jpg|jpeg)", fname):
        return jsonify({"error": "该作业没有保存原始文件"}), 404
    folder = os.path.join(UPLOADS_DIR, aid)
    path = os.path.join(folder, fname)
    if not os.path.exists(path):
        return jsonify({"error": "原始文件已丢失"}), 404
    return send_from_directory(
        folder, fname, as_attachment=True,
        download_name=f"{safe_filename(assignment.get('title', '作业'))}_原稿.{fname.rsplit('.', 1)[-1]}",
    )

@teacher_required
def api_assignments_clear(aid):
    with io_lock:
        data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
        assignment = find_teacher_assignment(data, aid, request.user)
        if not assignment:
            return jsonify({"error": "作业不存在"}), 404
        if assignment.get("lifecycleStatus", "published") != "draft":
            return jsonify({"error": "只有草稿作业可以清空作文；已发布作业请关闭或归档"}), 409
        if any(essay.get("status") in ("queued", "grading")
               for essay in assignment.get("essays", [])):
            return jsonify({"error": "仍有作文正在批改，暂时不能清空"}), 409
        assignment["essays"] = []
        assignment.pop("sourceFile", None)
        save_json(ASSIGNMENTS_PATH, data)
    remove_uploads(aid)
    audit_event("assignment_essays_cleared", target_type="assignment", target_id=aid)
    return jsonify({"ok": True})

def build_blueprint():
    return create_blueprint("assignments", __name__, ROUTES, default_feature="assignments", namespace=globals())
