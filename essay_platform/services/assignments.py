"""Assignment visibility, ownership and submission-state helpers."""

import os
import secrets
import database as db
from essay_platform.core.audit import audit_event
from essay_platform.core.config import ASSIGNMENTS_PATH, UPLOADS_DIR
from essay_platform.core.state import io_lock
from essay_platform.core.time import content_hash, deadline_is_future, now_iso, now_str, utc_iso
from essay_platform.persistence.datasets import load_json, save_json
from essay_platform.services.directory import student_class_ids

def find_assignment(data, aid):
    for a in data["assignments"]:
        if a["id"] == aid:
            return a
    return None

def assignment_visible_to_student(assignment, user):
    if not assignment or not user:
        return False
    cid = assignment.get("classId")
    if not cid:
        return False
    return (assignment.get("lifecycleStatus", "published") in ("published", "closed")
            and cid in student_class_ids(user["username"]))


def assignment_accepts_submission(assignment, user):
    return (assignment_visible_to_student(assignment, user)
            and assignment.get("lifecycleStatus", "published") == "published"
            and deadline_is_future(assignment.get("deadlineAt", "")))


def refresh_expired_assignments():
    if not db.is_ready():
        return []
    with io_lock:
        closed = db.close_expired_assignments(utc_iso())
    for assignment_id in closed:
        audit_event("assignment_auto_closed", target_type="assignment",
                    target_id=assignment_id, detail={"reason": "deadline"})
    return closed


def find_user_submission(assignment, username):
    for essay in assignment.get("essays", []):
        if essay.get("submittedBy") == username:
            return essay
    return None


def submission_state_for(assignment, username):
    essay = find_user_submission(assignment, username)
    if not essay:
        return {"submissionStatus": "not_submitted", "submissionType": "",
                "submittedAt": "", "lockedByTeacher": False,
                "submissionLocked": False, "reopenedAt": "", "jobId": "",
                "resultStatus": "not_ready", "publishedScore": None,
                "publishedLevel": "", "resultPublishedAt": ""}
    locked = bool(essay.get("lockedByTeacher"))
    submission_open = bool(essay.get("submissionOpen"))
    if essay.get("status") == "queued":
        status = "queued"
    elif essay.get("status") == "grading":
        status = "grading"
    elif essay.get("status") == "error":
        status = "failed"
    elif submission_open:
        status = "resubmission_open"
    elif essay.get("status") == "done" and essay.get("resultStatus") == "pending_review":
        status = "pending_review"
    elif essay.get("status") == "done" and essay.get("resultStatus") == "published":
        status = "result_published"
    elif locked and essay.get("submissionType") == "teacher_upload":
        status = "teacher_submitted_locked"
    else:
        status = ("student_submitted_locked"
                  if essay.get("submissionType") == "student_upload"
                  else "teacher_submitted_locked")
    published_feedback = essay.get("publishedFeedback") or {}
    return {"submissionStatus": status, "submissionType": essay.get("submissionType", ""),
            "submittedAt": essay.get("submittedAt", ""), "lockedByTeacher": locked,
            "submissionLocked": not submission_open,
            "reopenedAt": essay.get("reopenedAt", ""),
            "jobId": essay.get("jobId", ""),
            "resultStatus": essay.get("resultStatus", "not_ready"),
            "publishedScore": published_feedback.get("score"),
            "publishedLevel": published_feedback.get("level", ""),
            "resultPublishedAt": essay.get("resultPublishedAt", "")}


def is_assignment_owner(assignment, user):
    if not assignment or not user:
        return False
    return assignment.get("createdBy") == user.get("username")


def find_teacher_assignment(data, aid, user):
    assignment = find_assignment(data, aid)
    return assignment if is_assignment_owner(assignment, user) else None


def find_essay(assignment, eid):
    for e in assignment["essays"]:
        if e["id"] == eid:
            return e
    return None

def remove_uploads(aid):
    folder = os.path.join(UPLOADS_DIR, aid)
    try:
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                try:
                    os.remove(os.path.join(folder, f))
                except OSError:
                    pass
            os.rmdir(folder)
    except OSError:
        pass

def essay_summary(e):
    fb = e.get("feedback") or {}
    return {
        "id": e["id"], "studentName": e.get("studentName", ""),
        "chars": e.get("chars", 0), "status": e.get("status", "pending"),
        "textPreview": (e.get("text") or "")[:60],
        "text": e.get("text", ""),
        "score": e.get("score"), "level": e.get("level", ""),
        "error": e.get("error", ""), "errorType": e.get("errorType", ""),
        "errorAdvice": e.get("errorAdvice", ""), "gradedAt": e.get("gradedAt", ""),
        "submittedBy": e.get("submittedBy", ""), "submittedAt": e.get("submittedAt", ""),
        "submissionType": e.get("submissionType", ""),
        "studentTitle": e.get("studentTitle", ""),
        "studentPrompt": e.get("studentPrompt", ""),
        "lockedByTeacher": bool(e.get("lockedByTeacher")),
        "submissionOpen": bool(e.get("submissionOpen")),
        "reopenedAt": e.get("reopenedAt", ""),
        "jobId": e.get("jobId", ""),
        "attempt": int(e.get("attempt") or 0),
        "queuedAt": e.get("queuedAt", ""),
        "startedAt": e.get("startedAt", ""),
        "finishedAt": e.get("finishedAt", ""),
        "durationSeconds": e.get("durationSeconds"),
        "resultStatus": e.get("resultStatus", "not_ready"),
        "resultPublishedAt": e.get("resultPublishedAt", ""),
        "feedbackVersion": int(e.get("feedbackVersion") or 0),
        "feedbackEditedBy": e.get("feedbackEditedBy", ""),
        "feedbackEditedAt": e.get("feedbackEditedAt", ""),
        "sourceFingerprint": e.get("sourceFingerprint", ""),
        "publishedFeedback": (e.get("publishedFeedback")
                              if e.get("resultStatus") == "published" else None),
        "feedback": fb if e.get("status") == "done" else None,
    }

def upsert_assignment_submission(assignment_id, user, title, prompt, text, result,
                                 expected_submission=None):
    """写入学生作业；AI 调用期间提交状态变化时拒绝覆盖，返回是否成功。"""
    with io_lock:
        data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
        assignment = find_assignment(data, assignment_id)
        if not assignment or not assignment_visible_to_student(assignment, user):
            return False
        essays = assignment.setdefault("essays", [])
        essay = next((e for e in essays if e.get("submittedBy") == user["username"]), None)
        expected_submission = expected_submission or {}
        if expected_submission.get("absent"):
            if essay:
                return False
        else:
            if (not essay
                    or essay.get("id") != expected_submission.get("id")
                    or not essay.get("submissionOpen")
                    or essay.get("reopenedAt", "") != expected_submission.get("reopenedAt", "")):
                return False
        if not essay:
            essay = {"id": "e" + secrets.token_hex(6)}
            essays.append(essay)
        student_name = user.get("display_name") or user["username"]
        essay.update({
            "studentName": student_name,
            "submittedBy": user["username"],
            "submissionType": "student_upload",
            "lockedByTeacher": False,
            "submissionOpen": False,
            "reopenedAt": "",
            "submittedAt": now_str(),
            "text": text,
            "chars": len(text),
            "contentHash": content_hash(text),
            "status": "done",
            "score": result.get("score"),
            "level": result.get("level", ""),
            "feedback": result,
            "error": "",
            "errorType": "",
            "errorAdvice": "",
            "gradedAt": now_str(),
            "studentTitle": title,
            "studentPrompt": prompt,
            "jobId": "",
            "attempt": 1,
            "queuedAt": "",
            "startedAt": "",
            "finishedAt": now_iso(),
            "durationSeconds": None,
        })
        save_json(ASSIGNMENTS_PATH, data)
        return True
