"""Student growth, rankings and class insights."""

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

def student_trends(username: str):
    with db.connection() as conn:
        formal_rows = conn.execute("""
            SELECT h.assignment_id,h.assignment_title,h.visible_at,h.result_json,
                   h.publication_no,h.academic_year_id,h.class_name_snapshot
            FROM history h
            WHERE h.username=? AND h.assignment_id IS NOT NULL
            ORDER BY h.visible_at ASC,h.id ASC
        """, (username,)).fetchall()
        latest = {}
        for row in formal_rows:
            previous = latest.get(row["assignment_id"])
            if previous is None or int(row["publication_no"] or 0) > int(previous["publication_no"] or 0):
                latest[row["assignment_id"]] = row
        assignments = [{
            "id": row["assignment_id"], "label": row["assignment_title"],
            "date": row["visible_at"], "score": _json(row["result_json"], {}).get("score"),
            "publicationNo": row["publication_no"],
            "academicYearId": row["academic_year_id"] or "",
            "className": row["class_name_snapshot"],
        } for row in latest.values()]
        assignments.sort(key=lambda item: (item["date"], item["id"]))
        practice = []
        for row in conn.execute("""
            SELECT id,title,visible_at,result_json FROM history
            WHERE username=? AND assignment_id IS NULL
            ORDER BY visible_at ASC,id ASC
        """, (username,)).fetchall():
            practice.append({
                "id": row["id"], "label": row["title"] or "自主练习",
                "date": row["visible_at"], "score": _json(row["result_json"], {}).get("score"),
            })
        return {"assignments": assignments, "practice": practice}


def teacher_student_trend(teacher: str, student: str):
    with db.connection() as conn:
        user = conn.execute("""
            SELECT username,display_name,student_no FROM users
            WHERE username=? AND role='student'
        """, (student,)).fetchone()
        if not user:
            return None
        related = conn.execute("""
            SELECT 1 FROM class_teachers ct JOIN class_students cs ON cs.class_id=ct.class_id
            WHERE ct.username=? AND cs.username=?
            UNION ALL
            SELECT 1 FROM essays e JOIN assignments a ON a.id=e.assignment_id
            WHERE a.created_by=? AND e.submitted_by=?
            LIMIT 1
        """, (teacher, student, teacher, student)).fetchone()
        if not related:
            return None
        rows = conn.execute("""
            SELECT h.assignment_id,h.assignment_title,h.visible_at,h.result_json,h.publication_no
            FROM history h JOIN assignments a ON a.id=h.assignment_id
            WHERE h.username=? AND a.created_by=?
            ORDER BY h.visible_at ASC,h.id ASC
        """, (student, teacher)).fetchall()
        latest = {}
        for row in rows:
            previous = latest.get(row["assignment_id"])
            if previous is None or int(row["publication_no"] or 0) > int(previous["publication_no"] or 0):
                latest[row["assignment_id"]] = row
        points = [{
            "id": row["assignment_id"], "label": row["assignment_title"],
            "date": row["visible_at"], "score": _json(row["result_json"], {}).get("score"),
            "publicationNo": row["publication_no"],
        } for row in latest.values()]
        points.sort(key=lambda item: (item["date"], item["id"]))
        return {
            "student": {"username": user["username"], "displayName": user["display_name"],
                        "studentNo": user["student_no"] or user["username"]},
            "assignments": points,
        }


def assignment_ranking(assignment_id: str, teacher: str):
    with db.connection() as conn:
        assignment = conn.execute("SELECT class_id FROM assignments WHERE id=? AND created_by=?",
                                  (assignment_id, teacher)).fetchone()
        if not assignment:
            return None
        rows = conn.execute("""
            SELECT u.username,u.display_name,u.student_no,e.id AS essay_id,e.score,e.level,
                   e.status,e.result_status,e.submission_type
            FROM student_enrollments se JOIN users u ON u.username=se.username
            LEFT JOIN essays e ON e.assignment_id=? AND e.submitted_by=u.username
            WHERE se.class_id=? AND se.academic_year_id=(
                SELECT academic_year_id FROM assignments WHERE id=?)
            ORDER BY CASE WHEN e.score IS NULL THEN 1 ELSE 0 END,e.score DESC,
                     COALESCE(u.student_no,u.username),u.username
        """, (assignment_id, assignment["class_id"], assignment_id)).fetchall()
        result = []
        last_score = object()
        rank = 0
        for index, row in enumerate(rows, 1):
            score = row["score"]
            if score is not None and score != last_score:
                rank = index
                last_score = score
            result.append({
                "rank": rank if score is not None else None,
                "username": row["username"], "displayName": row["display_name"],
                "studentNo": row["student_no"] or row["username"],
                "essayId": row["essay_id"] or "", "score": score,
                "level": row["level"], "status": row["status"] or "not_submitted",
                "resultStatus": row["result_status"] or "not_ready",
                "submissionType": row["submission_type"] or "",
            })
        return result


def insight_source(assignment_id: str, teacher: str):
    with db.connection() as conn:
        assignment = conn.execute("SELECT id,title FROM assignments WHERE id=? AND created_by=?",
                                  (assignment_id, teacher)).fetchone()
        if not assignment:
            return None
        rows = conn.execute("""
            SELECT feedback_json FROM essays
            WHERE assignment_id=? AND status='done' AND feedback_json IS NOT NULL
            ORDER BY sort_order,id
        """, (assignment_id,)).fetchall()
        feedback = []
        for row in rows:
            item = _json(row["feedback_json"], {})
            feedback.append({
                "level": item.get("level", ""), "summary": item.get("summary", ""),
                "strengths": item.get("strengths", []),
                "weaknesses": item.get("weaknesses", []),
                "suggestions": item.get("suggestions", []),
            })
        digest = hashlib.sha256(_dump(feedback).encode("utf-8")).hexdigest()
        return {"assignment": dict(assignment), "feedback": feedback, "sourceHash": digest}


def get_insight(assignment_id: str, teacher: str):
    with db.connection() as conn:
        row = conn.execute("""
            SELECT i.* FROM assignment_insights i JOIN assignments a ON a.id=i.assignment_id
            WHERE i.assignment_id=? AND a.created_by=?
        """, (assignment_id, teacher)).fetchone()
        if not row:
            return None
        return {
            "assignmentId": row["assignment_id"], "insight": _json(row["current_json"], {}),
            "version": row["version"], "generatedAt": row["generated_at"],
            "editedBy": row["edited_by"], "editedAt": row["edited_at"],
            "sourceHash": row["source_hash"],
        }


def save_generated_insight(assignment_id: str, teacher: str, insight: dict,
                           source_hash: str):
    now = db._utc_iso()
    payload = _dump(insight)
    with db.connection(write=True) as conn:
        owner = conn.execute("SELECT 1 FROM assignments WHERE id=? AND created_by=?",
                             (assignment_id, teacher)).fetchone()
        if not owner:
            return None
        conn.execute("""
            INSERT INTO assignment_insights(
                assignment_id,ai_json,current_json,source_hash,version,generated_at)
            VALUES(?,?,?,?,1,?)
            ON CONFLICT(assignment_id) DO UPDATE SET ai_json=excluded.ai_json,
                current_json=excluded.current_json,source_hash=excluded.source_hash,
                version=assignment_insights.version+1,generated_at=excluded.generated_at,
                edited_by='',edited_at=''
        """, (assignment_id, payload, payload, source_hash, now))
    return get_insight(assignment_id, teacher)


def update_insight(assignment_id: str, teacher: str, insight: dict, expected_version: int):
    now = db._utc_iso()
    with db.connection(write=True) as conn:
        result = conn.execute("""
            UPDATE assignment_insights SET current_json=?,version=version+1,
                edited_by=?,edited_at=?
            WHERE assignment_id=? AND version=? AND EXISTS(
                SELECT 1 FROM assignments a
                WHERE a.id=assignment_insights.assignment_id AND a.created_by=?)
        """, (_dump(insight), teacher, now, assignment_id, expected_version, teacher))
        if result.rowcount != 1:
            exists = conn.execute("SELECT version FROM assignment_insights WHERE assignment_id=?",
                                  (assignment_id,)).fetchone()
            return {"ok": False, "reason": "conflict" if exists else "not_found",
                    "version": exists[0] if exists else 0}
    return {"ok": True, "record": get_insight(assignment_id, teacher)}
