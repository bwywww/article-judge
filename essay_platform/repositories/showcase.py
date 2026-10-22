"""Excellent essay showcase persistence."""

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

def set_showcase(assignment_id: str, teacher: str, essay_ids: list[str], confirm: bool):
    now = db._utc_iso()
    requested = list(dict.fromkeys(essay_ids))
    with db.connection(write=True) as conn:
        assignment = conn.execute("""
            SELECT * FROM assignments WHERE id=? AND created_by=?
        """, (assignment_id, teacher)).fetchone()
        if not assignment:
            return {"ok": False, "reason": "not_found"}
        if assignment["lifecycle_status"] == "archived":
            return {"ok": False, "reason": "archived"}
        existing = {row[0] for row in conn.execute(
            "SELECT essay_id FROM assignment_showcase WHERE assignment_id=?", (assignment_id,))}
        published_exists = bool(conn.execute("""
            SELECT 1 FROM essays
            WHERE assignment_id=? AND result_status='published' LIMIT 1
        """, (assignment_id,)).fetchone())
        if existing != set(requested) and (existing or published_exists) and not confirm:
            return {"ok": False, "reason": "confirmation_required"}
        if requested:
            marks = ",".join("?" for _ in requested)
            valid = {row[0] for row in conn.execute(f"""
                SELECT id FROM essays WHERE assignment_id=? AND id IN ({marks})
                    AND status='done' AND submitted_by IS NOT NULL
            """, (assignment_id, *requested)).fetchall()}
            if valid != set(requested):
                return {"ok": False, "reason": "invalid_essay"}
        conn.execute("DELETE FROM assignment_showcase WHERE assignment_id=?", (assignment_id,))
        for essay_id in requested:
            result_published = conn.execute(
                "SELECT result_status FROM essays WHERE id=?", (essay_id,)).fetchone()[0]
            visible_at = now if result_published == "published" else ""
            conn.execute("""
                INSERT INTO assignment_showcase(
                    assignment_id,essay_id,selected_by,selected_at,published_at)
                VALUES(?,?,?,?,?)
            """, (assignment_id, essay_id, teacher, now, visible_at))
        return {"ok": True, "essayIds": requested, "changed": existing != set(requested)}


def publish_showcase_for_essays(assignment_id: str, essay_ids: list[str], published_at: str):
    if not essay_ids:
        return
    with db.connection(write=True) as conn:
        marks = ",".join("?" for _ in essay_ids)
        conn.execute(f"""
            UPDATE assignment_showcase SET published_at=?
            WHERE assignment_id=? AND essay_id IN ({marks})
        """, (published_at, assignment_id, *essay_ids))


def list_showcase(assignment_id: str, username: str, role: str):
    with db.connection() as conn:
        assignment = conn.execute("SELECT * FROM assignments WHERE id=?", (assignment_id,)).fetchone()
        if not assignment:
            return None
        if role == "teacher" and assignment["created_by"] != username:
            return None
        if role == "student":
            allowed = conn.execute("""
                SELECT 1 FROM student_enrollments
                WHERE username=? AND class_id=? AND academic_year_id=?
            """, (username, assignment["class_id"], assignment["academic_year_id"])).fetchone()
            if not allowed:
                return None
        rows = conn.execute("""
            SELECT s.essay_id,s.published_at,e.student_name,e.student_title,e.text,u.student_no
            FROM assignment_showcase s JOIN essays e ON e.id=s.essay_id
            LEFT JOIN users u ON u.username=e.submitted_by
            WHERE s.assignment_id=? AND (?<>'student' OR s.published_at<>'')
            ORDER BY e.sort_order,e.id
        """, (assignment_id, role)).fetchall()
        return [{
            "essayId": row["essay_id"], "studentName": row["student_name"],
            "studentNo": row["student_no"] or "", "title": row["student_title"] or assignment["title"],
            "text": row["text"], "flower": True, "publishedAt": row["published_at"],
        } for row in rows]
