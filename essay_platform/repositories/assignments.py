"""SQLite persistence for the assignments domain."""

from __future__ import annotations

import secrets
from essay_platform.persistence.storage import connection, _dump, _utc_iso


def record_import_submission_versions(assignment_id: str, essay_ids: list[str]):
    """Append a version for imported content without rewriting earlier versions."""
    now = _utc_iso()
    created = []
    with connection(write=True) as conn:
        for essay_id in dict.fromkeys(essay_ids):
            essay = conn.execute(
                "SELECT * FROM essays WHERE id=? AND assignment_id=?",
                (essay_id, assignment_id),).fetchone()
            if not essay:
                continue
            latest = conn.execute("""
                SELECT * FROM submission_versions WHERE essay_id=?
                ORDER BY version_no DESC LIMIT 1
            """, (essay_id,)).fetchone()
            if latest and latest["content_hash"] == essay["content_hash"]:
                continue
            version_no = int(latest["version_no"] if latest else 0) + 1
            version_id = "sv" + secrets.token_hex(12)
            conn.execute("""
                INSERT INTO submission_versions(
                    id,assignment_id,essay_id,username,version_no,text,content_hash,
                    submitted_at,status)
                VALUES(?,?,?,?,?,?,?,?,'submitted')
            """, (version_id, assignment_id, essay_id, essay["submitted_by"], version_no,
                  essay["text"], essay["content_hash"], essay["submitted_at"] or now))
            created.append(version_id)
    return created

def close_expired_assignments(now_iso: str):
    with connection(write=True) as conn:
        rows = conn.execute("""
            SELECT id FROM assignments
            WHERE lifecycle_status='published' AND deadline_at<>'' AND deadline_at<=?
        """, (now_iso,)).fetchall()
        ids = [row["id"] for row in rows]
        if ids:
            marks = ",".join("?" for _ in ids)
            conn.execute(f"""
                UPDATE assignments SET lifecycle_status='closed',closed_at=?,
                    closed_reason='deadline' WHERE id IN ({marks})
            """, [now_iso, *ids])
        return ids


def transition_assignment(assignment_id: str, username: str, action: str,
                          *, deadline_at: str | None = None):
    now = _utc_iso()
    with connection(write=True) as conn:
        assignment = conn.execute(
            "SELECT * FROM assignments WHERE id=? AND created_by=?",
            (assignment_id, username)).fetchone()
        if not assignment:
            return {"ok": False, "reason": "not_found"}
        status = assignment["lifecycle_status"]
        essay_count = conn.execute(
            "SELECT COUNT(*) FROM essays WHERE assignment_id=?", (assignment_id,)).fetchone()[0]
        next_deadline = assignment["deadline_at"] if deadline_at is None else deadline_at
        if action == "publish":
            if status != "draft":
                return {"ok": False, "reason": "invalid_state", "status": status}
            conn.execute("""
                UPDATE assignments SET lifecycle_status='published',published_at=?,
                    deadline_at=?,closed_at='',closed_reason='',archived_at=''
                WHERE id=?
            """, (now, next_deadline, assignment_id))
            next_status = "published"
        elif action == "unpublish":
            if status != "published":
                return {"ok": False, "reason": "invalid_state", "status": status}
            if essay_count:
                return {"ok": False, "reason": "has_submissions"}
            conn.execute("""
                UPDATE assignments SET lifecycle_status='draft',published_at='',closed_at='',
                    closed_reason='' WHERE id=?
            """, (assignment_id,))
            next_status = "draft"
        elif action == "close":
            if status != "published":
                return {"ok": False, "reason": "invalid_state", "status": status}
            conn.execute("""
                UPDATE assignments SET lifecycle_status='closed',closed_at=?,
                    closed_reason='manual',deadline_at=? WHERE id=?
            """, (now, next_deadline, assignment_id))
            next_status = "closed"
        elif action == "reopen":
            if status != "closed":
                return {"ok": False, "reason": "invalid_state", "status": status}
            if assignment["closed_reason"] == "deadline" and deadline_at is None:
                return {"ok": False, "reason": "deadline_required"}
            conn.execute("""
                UPDATE assignments SET lifecycle_status='published',closed_at='',
                    closed_reason='',deadline_at=?,published_at=CASE WHEN published_at=''
                    THEN ? ELSE published_at END WHERE id=?
            """, (next_deadline, now, assignment_id))
            next_status = "published"
        elif action == "archive":
            if status != "closed":
                return {"ok": False, "reason": "invalid_state", "status": status}
            active_jobs = conn.execute("""
                SELECT COUNT(*) FROM grading_jobs
                WHERE assignment_id=? AND status IN ('queued','running')
            """, (assignment_id,)).fetchone()[0]
            pending_results = conn.execute("""
                SELECT COUNT(*) FROM essays
                WHERE assignment_id=? AND result_status='pending_review'
            """, (assignment_id,)).fetchone()[0]
            if active_jobs:
                return {"ok": False, "reason": "active_jobs"}
            if pending_results:
                return {"ok": False, "reason": "pending_results"}
            conn.execute("""
                UPDATE assignments SET lifecycle_status='archived',archived_at=? WHERE id=?
            """, (now, assignment_id))
            next_status = "archived"
        else:
            raise ValueError(action)
        return {"ok": True, "status": next_status, "deadlineAt": next_deadline,
                "essayCount": essay_count}


def publish_assignment_results(assignment_id: str, username: str, essay_ids=None,
                               *, history_limit: int = 30):
    requested = set(essay_ids or [])
    now = _utc_iso()
    published = []
    skipped = []
    with connection(write=True) as conn:
        assignment = conn.execute(
            "SELECT * FROM assignments WHERE id=? AND created_by=?",
            (assignment_id, username)).fetchone()
        if not assignment:
            return {"ok": False, "reason": "not_found"}
        if assignment["lifecycle_status"] not in ("published", "closed"):
            return {"ok": False, "reason": "assignment_not_released"}
        rows = conn.execute(
            "SELECT * FROM essays WHERE assignment_id=? ORDER BY sort_order,id",
            (assignment_id,)).fetchall()
        found = set()
        for essay in rows:
            if requested and essay["id"] not in requested:
                continue
            found.add(essay["id"])
            if (essay["status"] != "done" or not essay["feedback_json"]
                    or essay["result_status"] != "pending_review"):
                skipped.append(essay["id"])
                continue
            conn.execute("""
                UPDATE essays SET result_status='published',published_feedback_json=?,
                    result_published_at=? WHERE id=?
            """, (essay["feedback_json"], now, essay["id"]))
            version = conn.execute("""
                SELECT id FROM submission_versions WHERE essay_id=?
                ORDER BY version_no DESC LIMIT 1
            """, (essay["id"],)).fetchone()
            version_id = version["id"] if version else None
            if version_id:
                conn.execute("""
                    UPDATE submission_versions SET status='published',score=?,level=?,
                        feedback_json=?,published_at=? WHERE id=?
                """, (essay["score"], essay["level"], essay["feedback_json"], now, version_id))
            history_id = ""
            if essay["submitted_by"]:
                history_id = "h" + secrets.token_hex(6)
                publication_no = conn.execute("""
                    SELECT COALESCE(MAX(publication_no),0)+1 FROM history
                    WHERE username=? AND assignment_id=?
                """, (essay["submitted_by"], assignment_id)).fetchone()[0]
                conn.execute("""
                    INSERT INTO history(
                        id,username,title,prompt,criteria,word_limit,assignment_id,
                        assignment_title,class_id,source_essay_id,text,result_json,
                        graded_at,visible_at,created_at,submission_version_id,publication_no,
                        academic_year_id,class_name_snapshot)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    history_id, essay["submitted_by"], assignment["title"],
                    assignment["prompt"], assignment["criteria"], assignment["word_limit"],
                    assignment_id, assignment["title"], assignment["class_id"], essay["id"],
                    essay["text"], essay["feedback_json"], essay["graded_at"] or now,
                    now, now, version_id, publication_no, assignment["academic_year_id"],
                    assignment["class_name_snapshot"],
                ))
            conn.execute("""
                UPDATE assignment_showcase SET published_at=?
                WHERE assignment_id=? AND essay_id=?
            """, (now, assignment_id, essay["id"]))
            published.append({"essayId": essay["id"], "historyId": history_id})
        missing = sorted(requested - found)
        return {"ok": True, "published": published, "skipped": skipped,
                "missing": missing, "publishedAt": now}


def update_assignment_feedback(assignment_id: str, essay_id: str, username: str,
                               feedback: dict, expected_version: int):
    now = _utc_iso()
    payload = _dump(feedback) or "{}"
    with connection(write=True) as conn:
        row = conn.execute("""
            SELECT e.*,a.created_by FROM essays e
            JOIN assignments a ON a.id=e.assignment_id
            WHERE e.id=? AND e.assignment_id=?
        """, (essay_id, assignment_id)).fetchone()
        if not row or row["created_by"] != username:
            return {"ok": False, "reason": "not_found"}
        if row["status"] != "done" or not row["feedback_json"]:
            return {"ok": False, "reason": "not_ready"}
        current_version = int(row["feedback_version"] or 0)
        if current_version != expected_version:
            return {"ok": False, "reason": "version_conflict",
                    "feedbackVersion": current_version}
        ai_payload = row["ai_feedback_json"] or row["feedback_json"]
        changed = conn.execute("""
            UPDATE essays SET score=?,level=?,feedback_json=?,ai_feedback_json=?,
                feedback_version=feedback_version+1,feedback_edited_by=?,feedback_edited_at=?,
                result_status='pending_review'
            WHERE id=? AND assignment_id=? AND feedback_version=?
        """, (
            feedback.get("score"), feedback.get("level", ""), payload, ai_payload,
            username, now, essay_id, assignment_id, expected_version,
        ))
        if changed.rowcount != 1:
            latest = conn.execute(
                "SELECT feedback_version FROM essays WHERE id=?", (essay_id,)).fetchone()
            return {"ok": False, "reason": "version_conflict",
                    "feedbackVersion": int(latest[0] or 0) if latest else 0}
        conn.execute("""
            UPDATE submission_versions SET score=?,level=?,feedback_json=?,
                status='pending_review'
            WHERE id=(SELECT id FROM submission_versions WHERE essay_id=?
                      ORDER BY version_no DESC LIMIT 1)
        """, (feedback.get("score"), feedback.get("level", ""), payload, essay_id))
        return {
            "ok": True,
            "feedbackVersion": expected_version + 1,
            "feedbackEditedBy": username,
            "feedbackEditedAt": now,
            "resultStatus": "pending_review",
            "wasPublished": row["result_status"] == "published",
        }


def reopen_assignment_submissions(assignment_id: str, username: str, student_usernames=None):
    requested = set(student_usernames or [])
    now = _utc_iso()
    reopened = []
    skipped = []
    with connection(write=True) as conn:
        assignment = conn.execute(
            "SELECT * FROM assignments WHERE id=? AND created_by=?",
            (assignment_id, username)).fetchone()
        if not assignment:
            return {"ok": False, "reason": "not_found"}
        if assignment["lifecycle_status"] != "published":
            return {"ok": False, "reason": "assignment_closed"}
        rows = conn.execute(
            "SELECT * FROM essays WHERE assignment_id=? AND submitted_by IS NOT NULL",
            (assignment_id,)).fetchall()
        for essay in rows:
            if requested and essay["submitted_by"] not in requested:
                continue
            if essay["status"] in ("queued", "grading"):
                skipped.append(essay["submitted_by"])
                continue
            conn.execute("""
                UPDATE essays SET submission_open=1,reopened_at=? WHERE id=?
            """, (now, essay["id"]))
            reopened.append(essay["submitted_by"])
        return {"ok": True, "reopened": reopened, "skipped": skipped}
