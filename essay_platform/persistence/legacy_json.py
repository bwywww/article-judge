"""Legacy JSON-shaped dataset synchronization used by migration and compatibility APIs."""

from __future__ import annotations

import hashlib

from essay_platform.persistence.connection import _current_academic_year, _dump, _json, _utc_iso
from essay_platform.persistence.schema import DATA_SHAPE_VERSION

def _sync_users(conn, data):
    users = data.get("users", [])
    keep = []
    now = _utc_iso()
    for item in users:
        username = item.get("username", "")
        if not username:
            continue
        keep.append(username)
        scheme = item.get("passwordScheme") or (
            "legacy_sha256" if item.get("salt") else "pbkdf2_sha256")
        must_change = item.get("mustChangePassword")
        if must_change is None and item.get("salt"):
            must_change = any(
                hashlib.sha256((item.get("salt", "") + password).encode("utf-8")).hexdigest()
                == item.get("password_hash")
                for password in ("123456", "ChangeMe123!")
            )
        conn.execute("""
            INSERT INTO users(username,display_name,role,student_no,employee_no,subject,grade,klass,
                legacy_salt,password_hash,password_scheme,must_change_password,
                active,school_status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(username) DO UPDATE SET
                display_name=excluded.display_name, role=excluded.role,
                student_no=excluded.student_no, employee_no=excluded.employee_no,
                subject=excluded.subject, grade=excluded.grade, klass=excluded.klass,
                legacy_salt=excluded.legacy_salt, password_hash=excluded.password_hash,
                password_scheme=excluded.password_scheme,
                must_change_password=excluded.must_change_password,
                active=excluded.active, school_status=excluded.school_status,
                updated_at=excluded.updated_at
        """, (
            username, item.get("display_name") or username, item.get("role", "student"),
            item.get("studentNo") or None,
            (item.get("employeeNo") or username) if item.get("role") == "teacher" else None,
            item.get("subject", "") if item.get("role") == "teacher" else "",
            item.get("grade", ""), item.get("klass", ""),
            item.get("salt", ""), item.get("password_hash", ""), scheme,
            int(bool(must_change)), int(item.get("active", True)),
            item.get("schoolStatus", "active"),
            item.get("createdAt", "") or now, now,
        ))
    if keep:
        marks = ",".join("?" for _ in keep)
        conn.execute(f"DELETE FROM users WHERE username NOT IN ({marks})", keep)
    else:
        conn.execute("DELETE FROM users")


def _load_users(conn):
    rows = conn.execute("SELECT * FROM users ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'teacher' THEN 1 ELSE 2 END, username").fetchall()
    out = []
    for row in rows:
        item = {
            "username": row["username"], "display_name": row["display_name"],
            "role": row["role"], "grade": row["grade"], "klass": row["klass"],
            "salt": row["legacy_salt"], "password_hash": row["password_hash"],
            "passwordScheme": row["password_scheme"],
            "mustChangePassword": bool(row["must_change_password"]),
            "active": bool(row["active"]), "schoolStatus": row["school_status"],
            "createdAt": row["created_at"],
        }
        if row["role"] == "student":
            item["studentNo"] = row["student_no"] or row["username"]
        elif row["role"] == "teacher":
            item["employeeNo"] = row["employee_no"] or row["username"]
            item["subject"] = row["subject"]
        out.append(item)
    return {"schemaVersion": DATA_SHAPE_VERSION, "users": out}

def _load_user_row(row):
    item = {
        "username": row["username"], "display_name": row["display_name"],
        "role": row["role"], "grade": row["grade"], "klass": row["klass"],
        "salt": row["legacy_salt"], "password_hash": row["password_hash"],
        "passwordScheme": row["password_scheme"],
        "mustChangePassword": bool(row["must_change_password"]),
        "active": bool(row["active"]), "schoolStatus": row["school_status"],
        "createdAt": row["created_at"],
    }
    if row["role"] == "student":
        item["studentNo"] = row["student_no"] or row["username"]
    elif row["role"] == "teacher":
        item["employeeNo"] = row["employee_no"] or row["username"]
        item["subject"] = row["subject"]
    return item

def _sync_classes(conn, data):
    classes = data.get("classes", [])
    keep = []
    active_year = conn.execute(
        "SELECT id FROM academic_years WHERE status='active' ORDER BY start_year DESC LIMIT 1"
    ).fetchone()
    active_year_id = active_year[0] if active_year else _current_academic_year()["id"]
    for item in classes:
        cid = item.get("id", "")
        if not cid:
            continue
        keep.append(cid)
        conn.execute("""
            INSERT INTO classes(id,name,grade,klass,created_at,academic_year_id,status,source_class_id)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,grade=excluded.grade,
                klass=excluded.klass,created_at=excluded.created_at,
                academic_year_id=COALESCE(classes.academic_year_id,excluded.academic_year_id),
                status=excluded.status,source_class_id=COALESCE(classes.source_class_id,excluded.source_class_id)
        """, (cid, item.get("name", ""), item.get("grade", ""),
              item.get("klass", ""), item.get("createdAt", ""),
              item.get("academicYearId") or active_year_id,
              item.get("status", "active"), item.get("sourceClassId") or None))
    if keep:
        marks = ",".join("?" for _ in keep)
        conn.execute(f"DELETE FROM classes WHERE id NOT IN ({marks})", keep)
    else:
        conn.execute("DELETE FROM classes")
    conn.execute("DELETE FROM class_teachers")
    conn.execute("DELETE FROM class_students")
    seen_students = set()
    for item in classes:
        cid = item.get("id", "")
        for username in dict.fromkeys(item.get("teacherUsernames", [])):
            conn.execute("INSERT OR IGNORE INTO class_teachers(class_id,username) VALUES(?,?)",
                         (cid, username))
        for username in dict.fromkeys(item.get("studentUsernames", [])):
            if username in seen_students:
                raise ValueError(f"学生 {username} 同时属于多个班级")
            seen_students.add(username)
            conn.execute("INSERT INTO class_students(class_id,username) VALUES(?,?)",
                         (cid, username))
            class_year = conn.execute(
                "SELECT academic_year_id FROM classes WHERE id=?", (cid,)).fetchone()[0]
            conn.execute("""
                INSERT INTO student_enrollments(
                    username,class_id,academic_year_id,status,started_at)
                VALUES(?,?,?,'active',?)
                ON CONFLICT(username,academic_year_id) DO UPDATE SET
                    class_id=excluded.class_id,status='active',ended_at=''
            """, (username, cid, class_year or active_year_id, _utc_iso()))


def _load_classes(conn):
    classes = []
    for row in conn.execute("SELECT * FROM classes ORDER BY created_at,id").fetchall():
        teachers = [r[0] for r in conn.execute(
            "SELECT username FROM class_teachers WHERE class_id=? ORDER BY username", (row["id"],))]
        students = [r[0] for r in conn.execute(
            "SELECT username FROM class_students WHERE class_id=? ORDER BY username", (row["id"],))]
        classes.append({
            "id": row["id"], "name": row["name"], "grade": row["grade"],
            "klass": row["klass"], "createdAt": row["created_at"],
            "academicYearId": row["academic_year_id"] or "", "status": row["status"],
            "sourceClassId": row["source_class_id"] or "",
            "teacherUsernames": teachers, "studentUsernames": students,
        })
    return {"schemaVersion": DATA_SHAPE_VERSION, "classes": classes}


def _essay_values(assignment_id, item, order):
    feedback = item.get("feedback")
    ai_feedback = item.get("aiFeedback")
    if ai_feedback is None and feedback is not None:
        ai_feedback = feedback
    published = item.get("publishedFeedback")
    result_status = item.get("resultStatus")
    if not result_status:
        result_status = "published" if item.get("status") == "done" and item.get("submittedBy") else "not_ready"
    if result_status == "published" and published is None:
        published = feedback
    return (
        item.get("id"), assignment_id, item.get("studentName", ""),
        item.get("submittedBy") or None, item.get("submissionType", "teacher_upload"),
        int(bool(item.get("lockedByTeacher"))), int(bool(item.get("submissionOpen"))),
        item.get("reopenedAt", ""), item.get("submittedAt", ""), item.get("text", ""),
        int(item.get("chars") or len(item.get("text", ""))), item.get("contentHash", ""),
        item.get("status", "pending"), item.get("score"), item.get("level", ""),
        _dump(feedback), _dump(ai_feedback),
        int(item.get("feedbackVersion") or (1 if feedback is not None else 0)),
        item.get("feedbackEditedBy", ""), item.get("feedbackEditedAt", ""),
        item.get("error", ""), item.get("errorType", ""),
        item.get("errorAdvice", ""), item.get("gradedAt", ""),
        item.get("studentTitle", ""), item.get("studentPrompt", ""), item.get("jobId", ""),
        int(item.get("attempt") or 0), item.get("queuedAt", ""), item.get("startedAt", ""),
        item.get("finishedAt", ""), item.get("durationSeconds"), result_status,
        _dump(published), item.get("resultPublishedAt", "") or (
            item.get("gradedAt", "") if result_status == "published" else ""),
        item.get("sourceFingerprint", ""), order,
    )


def _sync_assignments(conn, data):
    assignments = data.get("assignments", [])
    keep_assignments = []
    keep_essays = []
    for order, item in enumerate(assignments):
        aid = item.get("id", "")
        if not aid:
            continue
        keep_assignments.append(aid)
        lifecycle = item.get("lifecycleStatus") or "published"
        conn.execute("""
            INSERT INTO assignments(id,title,prompt,criteria,word_limit,created_at,created_by,
                class_id,source_file,lifecycle_status,published_at,deadline_at,closed_at,
                closed_reason,archived_at,sort_order,academic_year_id,class_name_snapshot,
                created_by_name_snapshot)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET title=excluded.title,prompt=excluded.prompt,
                criteria=excluded.criteria,word_limit=excluded.word_limit,
                created_at=excluded.created_at,created_by=excluded.created_by,
                class_id=excluded.class_id,source_file=excluded.source_file,
                lifecycle_status=excluded.lifecycle_status,published_at=excluded.published_at,
                deadline_at=excluded.deadline_at,closed_at=excluded.closed_at,
                closed_reason=excluded.closed_reason,archived_at=excluded.archived_at,
                sort_order=excluded.sort_order,
                academic_year_id=COALESCE(assignments.academic_year_id,excluded.academic_year_id),
                class_name_snapshot=CASE WHEN assignments.class_name_snapshot=''
                    THEN excluded.class_name_snapshot ELSE assignments.class_name_snapshot END,
                created_by_name_snapshot=CASE WHEN assignments.created_by_name_snapshot=''
                    THEN excluded.created_by_name_snapshot ELSE assignments.created_by_name_snapshot END
        """, (
            aid, item.get("title", ""), item.get("prompt", ""), item.get("criteria", ""),
            item.get("wordLimit", ""), item.get("createdAt", ""), item.get("createdBy", ""),
            item.get("classId", ""), item.get("sourceFile", ""), lifecycle,
            item.get("publishedAt", "") or (item.get("createdAt", "") if lifecycle == "published" else ""),
            item.get("deadlineAt", ""), item.get("closedAt", ""),
            item.get("closedReason", ""), item.get("archivedAt", ""), order,
            item.get("academicYearId") or conn.execute(
                "SELECT academic_year_id FROM classes WHERE id=?", (item.get("classId", ""),)
            ).fetchone()[0],
            item.get("classNameSnapshot") or conn.execute(
                "SELECT name FROM classes WHERE id=?", (item.get("classId", ""),)
            ).fetchone()[0],
            item.get("createdByNameSnapshot") or conn.execute(
                "SELECT display_name FROM users WHERE username=?", (item.get("createdBy", ""),)
            ).fetchone()[0],
        ))
        for essay_order, essay in enumerate(item.get("essays", [])):
            if not essay.get("id"):
                continue
            keep_essays.append(essay["id"])
            conn.execute("""
                INSERT INTO essays(id,assignment_id,student_name,submitted_by,submission_type,
                    locked_by_teacher,submission_open,reopened_at,submitted_at,text,chars,
                    content_hash,status,score,level,feedback_json,ai_feedback_json,
                    feedback_version,feedback_edited_by,feedback_edited_at,error,error_type,error_advice,
                    graded_at,student_title,student_prompt,job_id,attempt,queued_at,started_at,
                    finished_at,duration_seconds,result_status,published_feedback_json,
                    result_published_at,source_fingerprint,sort_order)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET assignment_id=excluded.assignment_id,
                    student_name=excluded.student_name,submitted_by=excluded.submitted_by,
                    submission_type=excluded.submission_type,locked_by_teacher=excluded.locked_by_teacher,
                    submission_open=excluded.submission_open,reopened_at=excluded.reopened_at,
                    submitted_at=excluded.submitted_at,text=excluded.text,chars=excluded.chars,
                    content_hash=excluded.content_hash,status=excluded.status,score=excluded.score,
                    level=excluded.level,feedback_json=excluded.feedback_json,
                    ai_feedback_json=excluded.ai_feedback_json,
                    feedback_version=excluded.feedback_version,
                    feedback_edited_by=excluded.feedback_edited_by,
                    feedback_edited_at=excluded.feedback_edited_at,error=excluded.error,
                    error_type=excluded.error_type,error_advice=excluded.error_advice,
                    graded_at=excluded.graded_at,student_title=excluded.student_title,
                    student_prompt=excluded.student_prompt,job_id=excluded.job_id,
                    attempt=excluded.attempt,queued_at=excluded.queued_at,
                    started_at=excluded.started_at,finished_at=excluded.finished_at,
                    duration_seconds=excluded.duration_seconds,result_status=excluded.result_status,
                    published_feedback_json=excluded.published_feedback_json,
                    result_published_at=excluded.result_published_at,
                    source_fingerprint=excluded.source_fingerprint,sort_order=excluded.sort_order
            """, _essay_values(aid, essay, essay_order))
    if keep_essays:
        marks = ",".join("?" for _ in keep_essays)
        conn.execute(f"DELETE FROM essays WHERE id NOT IN ({marks})", keep_essays)
    else:
        conn.execute("DELETE FROM essays")
    if keep_assignments:
        marks = ",".join("?" for _ in keep_assignments)
        conn.execute(f"DELETE FROM assignments WHERE id NOT IN ({marks})", keep_assignments)
    else:
        conn.execute("DELETE FROM assignments")


def _row_to_essay(row):
    return {
        "id": row["id"], "studentName": row["student_name"], "submittedBy": row["submitted_by"] or "",
        "submissionType": row["submission_type"], "lockedByTeacher": bool(row["locked_by_teacher"]),
        "submissionOpen": bool(row["submission_open"]), "reopenedAt": row["reopened_at"],
        "submittedAt": row["submitted_at"], "text": row["text"], "chars": row["chars"],
        "contentHash": row["content_hash"], "status": row["status"], "score": row["score"],
        "level": row["level"], "feedback": _json(row["feedback_json"]), "error": row["error"],
        "aiFeedback": _json(row["ai_feedback_json"]),
        "feedbackVersion": row["feedback_version"],
        "feedbackEditedBy": row["feedback_edited_by"],
        "feedbackEditedAt": row["feedback_edited_at"],
        "errorType": row["error_type"], "errorAdvice": row["error_advice"],
        "gradedAt": row["graded_at"], "studentTitle": row["student_title"],
        "studentPrompt": row["student_prompt"], "jobId": row["job_id"],
        "attempt": row["attempt"], "queuedAt": row["queued_at"], "startedAt": row["started_at"],
        "finishedAt": row["finished_at"], "durationSeconds": row["duration_seconds"],
        "resultStatus": row["result_status"],
        "publishedFeedback": _json(row["published_feedback_json"]),
        "resultPublishedAt": row["result_published_at"],
        "sourceFingerprint": row["source_fingerprint"],
    }


def _load_assignments(conn):
    assignments = []
    rows = conn.execute("SELECT * FROM assignments ORDER BY sort_order,id").fetchall()
    for row in rows:
        essays = [_row_to_essay(e) for e in conn.execute(
            "SELECT * FROM essays WHERE assignment_id=? ORDER BY sort_order,id", (row["id"],)).fetchall()]
        item = {
            "id": row["id"], "title": row["title"], "prompt": row["prompt"],
            "criteria": row["criteria"], "wordLimit": row["word_limit"],
            "createdAt": row["created_at"], "createdBy": row["created_by"],
            "classId": row["class_id"], "essays": essays,
            "lifecycleStatus": row["lifecycle_status"], "publishedAt": row["published_at"],
            "deadlineAt": row["deadline_at"], "closedAt": row["closed_at"],
            "closedReason": row["closed_reason"], "archivedAt": row["archived_at"],
            "academicYearId": row["academic_year_id"] or "",
            "classNameSnapshot": row["class_name_snapshot"],
            "createdByNameSnapshot": row["created_by_name_snapshot"],
        }
        if row["source_file"]:
            item["sourceFile"] = row["source_file"]
        assignments.append(item)
    return {"schemaVersion": DATA_SHAPE_VERSION, "assignments": assignments}


def _sync_history(conn, data):
    items = data.get("items", [])
    keep = []
    for item in items:
        hid = item.get("id", "")
        if not hid:
            continue
        keep.append(hid)
        result = item.get("result") or {}
        visible_at = item.get("visibleAt")
        if visible_at is None:
            visible_at = item.get("gradedAt", "")
        conn.execute("""
            INSERT INTO history(id,username,title,prompt,criteria,word_limit,assignment_id,
                assignment_title,class_id,source_essay_id,text,result_json,graded_at,visible_at,created_at,
                submission_version_id,publication_no,academic_year_id,class_name_snapshot)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET username=excluded.username,title=excluded.title,
                prompt=excluded.prompt,criteria=excluded.criteria,word_limit=excluded.word_limit,
                assignment_id=excluded.assignment_id,assignment_title=excluded.assignment_title,
                class_id=excluded.class_id,source_essay_id=excluded.source_essay_id,text=excluded.text,
                result_json=excluded.result_json,graded_at=excluded.graded_at,
                visible_at=excluded.visible_at,created_at=excluded.created_at,
                submission_version_id=COALESCE(history.submission_version_id,excluded.submission_version_id),
                publication_no=excluded.publication_no,
                academic_year_id=COALESCE(history.academic_year_id,excluded.academic_year_id),
                class_name_snapshot=CASE WHEN history.class_name_snapshot=''
                    THEN excluded.class_name_snapshot ELSE history.class_name_snapshot END
        """, (
            hid, item.get("username", ""), item.get("title", ""), item.get("prompt", ""),
            item.get("criteria", ""), item.get("wordLimit", ""), item.get("assignmentId") or None,
            item.get("assignmentTitle", ""), item.get("classId") or None,
            item.get("sourceEssayId") or None, item.get("text", ""), _dump(result) or "{}",
            item.get("gradedAt", ""), visible_at, item.get("createdAt", "") or item.get("gradedAt", ""),
            item.get("submissionVersionId") or None, int(item.get("publicationNo") or 1),
            item.get("academicYearId") or None, item.get("classNameSnapshot", ""),
        ))
    if keep:
        marks = ",".join("?" for _ in keep)
        conn.execute(f"DELETE FROM history WHERE id NOT IN ({marks})", keep)
    else:
        conn.execute("DELETE FROM history")


def _load_history(conn):
    rows = conn.execute("SELECT * FROM history ORDER BY graded_at DESC,id DESC").fetchall()
    items = []
    for row in rows:
        items.append({
            "id": row["id"], "username": row["username"], "title": row["title"],
            "prompt": row["prompt"], "criteria": row["criteria"],
            "wordLimit": row["word_limit"], "assignmentId": row["assignment_id"] or "",
            "assignmentTitle": row["assignment_title"], "classId": row["class_id"] or "",
            "sourceEssayId": row["source_essay_id"] or "", "text": row["text"],
            "result": _json(row["result_json"], {}), "gradedAt": row["graded_at"],
            "visibleAt": row["visible_at"], "createdAt": row["created_at"],
            "submissionVersionId": row["submission_version_id"] or "",
            "publicationNo": row["publication_no"],
            "academicYearId": row["academic_year_id"] or "",
            "classNameSnapshot": row["class_name_snapshot"],
        })
    return {"schemaVersion": DATA_SHAPE_VERSION, "items": items}


def _sync_config(conn, data):
    for key in ("base_url", "api_key", "model"):
        if key in data:
            conn.execute("INSERT INTO app_config(key,value) VALUES(?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, str(data.get(key, ""))))


def _load_config(conn):
    return {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM app_config")}
