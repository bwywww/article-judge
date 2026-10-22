"""Submission returns and immutable revision timelines."""

from __future__ import annotations

import hashlib
import json
import re
import secrets

from essay_platform.persistence import storage as db

def _json(value, default=None):
    if value in (None, ""):
        return default
    return json.loads(value)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _version_row(row):
    return {
        "id": row["id"],
        "versionNo": row["version_no"],
        "text": row["text"],
        "submittedAt": row["submitted_at"],
        "status": row["status"],
        "score": row["score"],
        "level": row["level"],
        "feedback": _json(row["feedback_json"], {}),
        "publishedAt": row["published_at"],
    }


def return_submission(assignment_id: str, essay_id: str, teacher: str,
                      comment: str, deadline_at: str):
    now = db._utc_iso()
    with db.connection(write=True) as conn:
        essay = conn.execute("""
            SELECT e.*,a.lifecycle_status,a.created_by
            FROM essays e JOIN assignments a ON a.id=e.assignment_id
            WHERE e.id=? AND e.assignment_id=? AND a.created_by=?
        """, (essay_id, assignment_id, teacher)).fetchone()
        if not essay:
            return {"ok": False, "reason": "not_found"}
        if essay["lifecycle_status"] == "archived":
            return {"ok": False, "reason": "archived"}
        if essay["submission_type"] != "student_upload" or essay["locked_by_teacher"]:
            return {"ok": False, "reason": "teacher_upload"}
        if essay["status"] in ("queued", "grading"):
            return {"ok": False, "reason": "in_progress"}
        if essay["result_status"] not in ("pending_review", "published"):
            return {"ok": False, "reason": "not_reviewable"}
        active = conn.execute("""
            SELECT id FROM submission_returns
            WHERE essay_id=? AND resolved_at='' AND cancelled_at=''
        """, (essay_id,)).fetchone()
        if active:
            return {"ok": False, "reason": "already_returned"}
        version = conn.execute("""
            SELECT * FROM submission_versions WHERE essay_id=?
            ORDER BY version_no DESC LIMIT 1
        """, (essay_id,)).fetchone()
        if not version:
            return {"ok": False, "reason": "version_missing"}
        return_id = "ret" + secrets.token_hex(10)
        conn.execute("""
            INSERT INTO submission_returns(
                id,assignment_id,essay_id,submission_version_id,teacher_username,
                comment,returned_at,resubmit_deadline_at)
            VALUES(?,?,?,?,?,?,?,?)
        """, (return_id, assignment_id, essay_id, version["id"], teacher,
              comment, now, deadline_at))
        conn.execute("UPDATE submission_versions SET status='returned' WHERE id=?",
                     (version["id"],))
        conn.execute("""
            UPDATE essays SET submission_open=1,reopened_at=? WHERE id=?
        """, (now, essay_id))
        return {"ok": True, "returnId": return_id, "returnedAt": now,
                "resubmitDeadlineAt": deadline_at}


def cancel_return(assignment_id: str, essay_id: str, teacher: str):
    now = db._utc_iso()
    with db.connection(write=True) as conn:
        record = conn.execute("""
            SELECT sr.id,sr.submission_version_id,e.submission_open
            FROM submission_returns sr
            JOIN essays e ON e.id=sr.essay_id
            JOIN assignments a ON a.id=sr.assignment_id
            WHERE sr.assignment_id=? AND sr.essay_id=? AND a.created_by=?
                AND sr.resolved_at='' AND sr.cancelled_at=''
        """, (assignment_id, essay_id, teacher)).fetchone()
        if not record:
            return {"ok": False, "reason": "not_found"}
        if not record["submission_open"]:
            return {"ok": False, "reason": "already_resubmitted"}
        conn.execute("UPDATE submission_returns SET cancelled_at=? WHERE id=?",
                     (now, record["id"]))
        conn.execute("UPDATE submission_versions SET status=CASE WHEN published_at<>'' "
                     "THEN 'published' ELSE 'pending_review' END WHERE id=?",
                     (record["submission_version_id"],))
        conn.execute("UPDATE essays SET submission_open=0,reopened_at='' WHERE id=?", (essay_id,))
        return {"ok": True, "cancelledAt": now}


def active_return(conn, essay_id: str):
    return conn.execute("""
        SELECT * FROM submission_returns
        WHERE essay_id=? AND resolved_at='' AND cancelled_at=''
        ORDER BY returned_at DESC LIMIT 1
    """, (essay_id,)).fetchone()


def student_active_returns(username: str):
    now = db._utc_iso()
    with db.connection() as conn:
        return {row["assignment_id"]: {
            "returnId": row["id"], "essayId": row["essay_id"],
            "comment": row["comment"], "returnedAt": row["returned_at"],
            "resubmitDeadlineAt": row["resubmit_deadline_at"],
            "expired": bool(row["resubmit_deadline_at"]
                            and row["resubmit_deadline_at"] <= now),
        } for row in conn.execute("""
            SELECT sr.* FROM submission_returns sr
            JOIN essays e ON e.id=sr.essay_id
            WHERE e.submitted_by=? AND sr.resolved_at='' AND sr.cancelled_at=''
        """, (username,)).fetchall()}


def list_submission_timeline(assignment_id: str, essay_id: str, username: str, role: str):
    with db.connection() as conn:
        essay = conn.execute("""
            SELECT e.*,a.created_by,a.title,a.lifecycle_status
            FROM essays e JOIN assignments a ON a.id=e.assignment_id
            WHERE e.id=? AND e.assignment_id=?
        """, (essay_id, assignment_id)).fetchone()
        if not essay:
            return None
        if role == "teacher" and essay["created_by"] != username:
            return None
        if role == "student" and essay["submitted_by"] != username:
            return None
        if role not in ("teacher", "student"):
            return None
        versions = [_version_row(row) for row in conn.execute("""
            SELECT * FROM submission_versions WHERE essay_id=?
            ORDER BY version_no ASC
        """, (essay_id,)).fetchall()]
        returns = [{
            "id": row["id"], "versionId": row["submission_version_id"],
            "comment": row["comment"], "returnedAt": row["returned_at"],
            "resubmitDeadlineAt": row["resubmit_deadline_at"],
            "resolvedAt": row["resolved_at"], "cancelledAt": row["cancelled_at"],
        } for row in conn.execute("""
            SELECT * FROM submission_returns WHERE essay_id=? ORDER BY returned_at ASC
        """, (essay_id,)).fetchall()]
        return {
            "assignmentId": assignment_id, "assignmentTitle": essay["title"],
            "essayId": essay_id, "studentName": essay["student_name"],
            "submittedBy": essay["submitted_by"] or "", "versions": versions,
            "returns": returns, "archived": essay["lifecycle_status"] == "archived",
        }
