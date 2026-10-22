"""Academic-year rollover and archived assignments."""

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

def list_academic_years():
    with db.connection() as conn:
        return [{
            "id": row["id"], "label": row["label"], "startYear": row["start_year"],
            "status": row["status"], "createdAt": row["created_at"], "closedAt": row["closed_at"],
        } for row in conn.execute(
            "SELECT * FROM academic_years ORDER BY start_year DESC").fetchall()]


GRADE_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
GRADE_LABELS = {value: f"{key}年级" for key, value in GRADE_NUMBERS.items()}


def _grade_number(value: str):
    text = str(value or "")
    match = re.search(r"([1-6一二三四五六])", text)
    if not match:
        return 0
    return int(match.group(1)) if match.group(1).isdigit() else GRADE_NUMBERS[match.group(1)]


def academic_rollover_preview(target_start_year: int | None = None):
    with db.connection() as conn:
        active = conn.execute(
            "SELECT * FROM academic_years WHERE status='active' ORDER BY start_year DESC LIMIT 1"
        ).fetchone()
        if not active:
            return None
        target_year = int(target_start_year or (active["start_year"] + 1))
        classes = []
        for row in conn.execute("""
            SELECT c.*,COUNT(cs.username) AS student_count
            FROM classes c LEFT JOIN class_students cs ON cs.class_id=c.id
            WHERE c.status='active' AND c.academic_year_id=?
            GROUP BY c.id ORDER BY c.grade,c.klass,c.name
        """, (active["id"],)).fetchall():
            grade_no = _grade_number(row["grade"] or row["name"])
            graduating = grade_no == 6
            next_grade = "毕业" if graduating else GRADE_LABELS.get(grade_no + 1, row["grade"])
            classes.append({
                "sourceClassId": row["id"], "sourceName": row["name"],
                "studentCount": row["student_count"], "graduating": graduating,
                "targetGrade": next_grade, "targetKlass": row["klass"],
                "targetName": "" if graduating else f"{next_grade}{row['klass']}",
                "studentUsernames": [item[0] for item in conn.execute(
                    "SELECT username FROM class_students WHERE class_id=? ORDER BY username",
                    (row["id"],)).fetchall()],
                "teacherUsernames": [item[0] for item in conn.execute(
                    "SELECT username FROM class_teachers WHERE class_id=? ORDER BY username",
                    (row["id"],)).fetchall()],
            })
        blockers = {
            "activeJobs": conn.execute(
                "SELECT COUNT(*) FROM grading_jobs WHERE status IN ('queued','running')").fetchone()[0],
            "draftAssignments": conn.execute("""
                SELECT COUNT(*) FROM assignments
                WHERE academic_year_id=? AND lifecycle_status='draft'
            """, (active["id"],)).fetchone()[0],
            "pendingResults": conn.execute("""
                SELECT COUNT(*) FROM essays e JOIN assignments a ON a.id=e.assignment_id
                WHERE a.academic_year_id=? AND e.result_status='pending_review'
            """, (active["id"],)).fetchone()[0],
        }
        return {
            "current": {"id": active["id"], "label": active["label"],
                        "startYear": active["start_year"]},
            "target": {"id": f"ay{target_year}", "label": f"{target_year}-{target_year + 1}学年",
                       "startYear": target_year},
            "classes": classes, "blockers": blockers,
            "canConfirm": not any(blockers.values()) and target_year > active["start_year"],
        }


def perform_academic_rollover(target_start_year: int, targets: list[dict]):
    now = db._utc_iso()
    preview = academic_rollover_preview(target_start_year)
    if not preview:
        return {"ok": False, "reason": "no_active_year"}
    if any(preview["blockers"].values()):
        return {"ok": False, "reason": "blocked", "blockers": preview["blockers"]}
    if int(target_start_year) <= int(preview["current"]["startYear"]):
        return {"ok": False, "reason": "invalid_year"}
    source = {item["sourceClassId"]: item for item in preview["classes"]}
    requested_targets = targets or [item for item in preview["classes"] if not item["graduating"]]
    valid_students = {
        username for item in preview["classes"] if not item["graduating"]
        for username in item["studentUsernames"]
    }
    graduating_students = {
        username for item in preview["classes"] if item["graduating"]
        for username in item["studentUsernames"]
    }
    assigned = []
    cleaned_targets = []
    for item in requested_targets:
        source_id = str(item.get("sourceClassId") or "")
        if source_id not in source:
            return {"ok": False, "reason": "invalid_source_class"}
        grade = str(item.get("targetGrade") or "").strip()
        klass = str(item.get("targetKlass") or "").strip()
        name = str(item.get("targetName") or f"{grade}{klass}").strip()
        students = list(dict.fromkeys(item.get("studentUsernames") or source[source_id]["studentUsernames"]))
        if not grade or not klass or not name:
            return {"ok": False, "reason": "invalid_target_class"}
        assigned.extend(students)
        cleaned_targets.append({"sourceClassId": source_id, "grade": grade,
                                "klass": klass, "name": name, "students": students})
    if len(assigned) != len(set(assigned)):
        return {"ok": False, "reason": "duplicate_student"}
    if set(assigned) != valid_students:
        return {"ok": False, "reason": "student_set_mismatch"}
    target_id = f"ay{int(target_start_year)}"
    target_label = f"{int(target_start_year)}-{int(target_start_year) + 1}学年"
    with db.connection(write=True) as conn:
        if conn.execute("SELECT 1 FROM academic_years WHERE id=? OR start_year=?",
                        (target_id, int(target_start_year))).fetchone():
            return {"ok": False, "reason": "year_exists"}
        active = conn.execute("SELECT * FROM academic_years WHERE status='active'").fetchone()
        if not active or active["id"] != preview["current"]["id"]:
            return {"ok": False, "reason": "state_changed"}
        conn.execute("UPDATE academic_years SET status='closed',closed_at=? WHERE id=?",
                     (now, active["id"]))
        conn.execute("""
            INSERT INTO academic_years(id,label,start_year,status,created_at)
            VALUES(?,?,?,'active',?)
        """, (target_id, target_label, int(target_start_year), now))
        conn.execute("""
            UPDATE assignments SET lifecycle_status='archived',archived_at=?,
                closed_at=CASE WHEN closed_at='' THEN ? ELSE closed_at END,
                closed_reason=CASE WHEN closed_reason='' THEN 'academic_year' ELSE closed_reason END
            WHERE academic_year_id=?
        """, (now, now, active["id"]))
        conn.execute("UPDATE classes SET status='archived' WHERE academic_year_id=?",
                     (active["id"],))
        conn.execute("""
            UPDATE student_enrollments SET status='archived',ended_at=?
            WHERE academic_year_id=? AND status='active'
        """, (now, active["id"]))
        conn.execute("DELETE FROM class_students WHERE class_id IN (SELECT id FROM classes WHERE academic_year_id=?)",
                     (active["id"],))
        created = []
        for item in cleaned_targets:
            class_id = "c" + secrets.token_hex(8)
            conn.execute("""
                INSERT INTO classes(id,name,grade,klass,created_at,academic_year_id,status,source_class_id)
                VALUES(?,?,?,?,?,?,'active',?)
            """, (class_id, item["name"], item["grade"], item["klass"], now,
                  target_id, item["sourceClassId"]))
            for username in item["students"]:
                conn.execute("INSERT INTO class_students(class_id,username) VALUES(?,?)",
                             (class_id, username))
                conn.execute("""
                    INSERT INTO student_enrollments(
                        username,class_id,academic_year_id,status,started_at)
                    VALUES(?,?,?,'active',?)
                """, (username, class_id, target_id, now))
                conn.execute("""
                    UPDATE users SET grade=?,klass=?,active=1,school_status='active',updated_at=?
                    WHERE username=? AND role='student'
                """, (item["grade"], item["klass"], now, username))
            for teacher_username in source[item["sourceClassId"]].get("teacherUsernames", []):
                conn.execute("""
                    INSERT OR IGNORE INTO class_teachers(class_id,username) VALUES(?,?)
                """, (class_id, teacher_username))
            created.append({"id": class_id, "name": item["name"],
                            "studentCount": len(item["students"]),
                            "teacherCount": len(source[item["sourceClassId"]].get(
                                "teacherUsernames", []))})
        if graduating_students:
            marks = ",".join("?" for _ in graduating_students)
            conn.execute(f"""
                UPDATE users SET active=0,school_status='graduated',updated_at=?
                WHERE username IN ({marks})
            """, (now, *sorted(graduating_students)))
            conn.execute(f"DELETE FROM sessions WHERE username IN ({marks})",
                         tuple(sorted(graduating_students)))
        return {"ok": True, "academicYearId": target_id, "label": target_label,
                "createdClasses": created, "graduated": len(graduating_students)}


def student_assignment_archive(username: str):
    with db.connection() as conn:
        years = list_academic_years()
        rows = conn.execute("""
            SELECT a.id,a.title,a.lifecycle_status,a.published_at,a.deadline_at,
                   a.academic_year_id,a.class_name_snapshot,e.id AS essay_id,e.status,
                   e.result_status,e.submission_open,e.submitted_at
            FROM assignments a
            JOIN student_enrollments se ON se.class_id=a.class_id
                AND se.academic_year_id=a.academic_year_id
            LEFT JOIN essays e ON e.assignment_id=a.id AND e.submitted_by=?
            WHERE se.username=? AND a.lifecycle_status<>'draft'
            ORDER BY a.published_at DESC,a.created_at DESC
        """, (username, username)).fetchall()
        active_year = next((year["id"] for year in years if year["status"] == "active"), "")
        current, historical = [], []
        for row in rows:
            item = dict(row)
            item = {
                "id": row["id"], "title": row["title"], "lifecycleStatus": row["lifecycle_status"],
                "publishedAt": row["published_at"], "deadlineAt": row["deadline_at"],
                "academicYearId": row["academic_year_id"] or "",
                "className": row["class_name_snapshot"], "essayId": row["essay_id"] or "",
                "status": row["status"] or "not_submitted", "resultStatus": row["result_status"] or "not_ready",
                "submissionOpen": bool(row["submission_open"]), "submittedAt": row["submitted_at"] or "",
            }
            (current if row["academic_year_id"] == active_year else historical).append(item)
        return {"academicYears": years, "activeAcademicYearId": active_year,
                "current": current, "historical": historical}
