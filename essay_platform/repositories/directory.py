"""SQLite persistence for the directory domain."""

from __future__ import annotations

from essay_platform.persistence.storage import connection, _load_user_row, _utc_iso

def get_user(username: str):
    with connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            return None
        return _load_user_row(row)


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


def update_user_password(username: str, password_hash: str, *, must_change: bool):
    with connection(write=True) as conn:
        result = conn.execute("""
            UPDATE users SET legacy_salt='', password_hash=?, password_scheme='pbkdf2_sha256',
                must_change_password=?, updated_at=? WHERE username=?
        """, (password_hash, int(bool(must_change)), _utc_iso(), username))
        if result.rowcount != 1:
            raise KeyError(username)


def set_user_must_change(username: str, must_change: bool):
    with connection(write=True) as conn:
        conn.execute("UPDATE users SET must_change_password=?,updated_at=? WHERE username=?",
                     (int(bool(must_change)), _utc_iso(), username))


def create_teacher_user(*, employee_no: str, display_name: str, subject: str,
                        password_hash: str, class_ids: list[str]):
    now = _utc_iso()
    with connection(write=True) as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=? OR employee_no=?",
                        (employee_no, employee_no)).fetchone():
            return {"ok": False, "reason": "duplicate_employee_no"}
        if class_ids:
            marks = ",".join("?" for _ in class_ids)
            found = {row[0] for row in conn.execute(
                f"SELECT id FROM classes WHERE id IN ({marks})", class_ids).fetchall()}
            if found != set(class_ids):
                return {"ok": False, "reason": "class_not_found"}
        conn.execute("""
            INSERT INTO users(username,display_name,role,employee_no,subject,
                password_hash,password_scheme,must_change_password,active,created_at,updated_at)
            VALUES(?,?,'teacher',?,?,?,'pbkdf2_sha256',1,1,?,?)
        """, (employee_no, display_name, employee_no, subject, password_hash, now, now))
        for class_id in dict.fromkeys(class_ids):
            conn.execute("INSERT INTO class_teachers(class_id,username) VALUES(?,?)",
                         (class_id, employee_no))
        return {"ok": True, "username": employee_no}


def create_student_user(*, username: str, student_no: str, display_name: str,
                        password_hash: str):
    now = _utc_iso()
    with connection(write=True) as conn:
        if conn.execute(
            "SELECT 1 FROM users WHERE username=? OR student_no=?", (username, student_no)
        ).fetchone():
            return {"ok": False, "reason": "duplicate"}
        conn.execute("""
            INSERT INTO users(username,display_name,role,student_no,password_hash,
                password_scheme,must_change_password,active,created_at,updated_at)
            VALUES(?,?,'student',?,?,'pbkdf2_sha256',1,1,?,?)
        """, (username, display_name, student_no, password_hash, now, now))
    return {"ok": True, "username": username}


def update_student_user(username: str, *, display_name: str, student_no: str,
                        active: bool):
    now = _utc_iso()
    with connection(write=True) as conn:
        current = conn.execute(
            "SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if not current or current["role"] != "student":
            return {"ok": False, "reason": "not_found"}
        collision = conn.execute(
            "SELECT username FROM users WHERE student_no=? AND username<>?",
            (student_no, username),).fetchone()
        if collision:
            return {"ok": False, "reason": "duplicate_student_no"}
        conn.execute("""
            UPDATE users SET display_name=?,student_no=?,active=?,updated_at=?
            WHERE username=?
        """, (display_name, student_no, int(bool(active)), now, username))
        if not active:
            conn.execute("DELETE FROM sessions WHERE username=?", (username,))
    return {"ok": True}


def create_class_record(class_id: str, name: str, grade: str, klass: str):
    now = _utc_iso()
    with connection(write=True) as conn:
        duplicate = conn.execute("""
            SELECT id FROM classes WHERE status='active' AND grade=? AND klass=?
        """, (grade, klass)).fetchone()
        if duplicate:
            return {"ok": False, "reason": "duplicate"}
        year = conn.execute(
            "SELECT id FROM academic_years WHERE status='active' LIMIT 1").fetchone()
        conn.execute("""
            INSERT INTO classes(id,name,grade,klass,created_at,academic_year_id,status)
            VALUES(?,?,?,?,?,?,'active')
        """, (class_id, name, grade, klass, now, year[0] if year else None))
    return {"ok": True, "id": class_id}


def update_class_record(class_id: str, name: str, grade: str, klass: str):
    now = _utc_iso()
    with connection(write=True) as conn:
        current = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        if not current:
            return {"ok": False, "reason": "not_found"}
        if current["status"] != "active":
            return {"ok": False, "reason": "archived"}
        duplicate = conn.execute("""
            SELECT id FROM classes WHERE id<>? AND status='active' AND grade=? AND klass=?
        """, (class_id, grade, klass)).fetchone()
        if duplicate:
            return {"ok": False, "reason": "duplicate"}
        conn.execute("UPDATE classes SET name=?,grade=?,klass=? WHERE id=?",
                     (name, grade, klass, class_id))
        conn.execute("""
            UPDATE users SET grade=?,klass=?,updated_at=? WHERE role='student' AND username IN
                (SELECT username FROM class_students WHERE class_id=?)
        """, (grade, klass, now, class_id))
    return {"ok": True}


def delete_class_record(class_id: str):
    now = _utc_iso()
    with connection(write=True) as conn:
        current = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        if not current:
            return {"ok": False, "reason": "not_found"}
        if conn.execute("SELECT 1 FROM assignments WHERE class_id=? LIMIT 1",
                        (class_id,)).fetchone():
            return {"ok": False, "reason": "has_assignments"}
        members = [row[0] for row in conn.execute(
            "SELECT username FROM class_students WHERE class_id=?", (class_id,)).fetchall()]
        conn.execute("DELETE FROM classes WHERE id=?", (class_id,))
        if members:
            marks = ",".join("?" for _ in members)
            conn.execute(f"""
                UPDATE users SET grade='',klass='',updated_at=? WHERE username IN ({marks})
            """, (now, *members))
    return {"ok": True, "name": current["name"]}


def assign_class_student(class_id: str, username: str):
    now = _utc_iso()
    with connection(write=True) as conn:
        cls = conn.execute(
            "SELECT * FROM classes WHERE id=? AND status='active'", (class_id,)).fetchone()
        user = conn.execute(
            "SELECT 1 FROM users WHERE username=? AND role='student'", (username,)).fetchone()
        if not cls:
            return {"ok": False, "reason": "class_not_found"}
        if not user:
            return {"ok": False, "reason": "student_not_found"}
        conn.execute("DELETE FROM class_students WHERE username=?", (username,))
        conn.execute("INSERT INTO class_students(class_id,username) VALUES(?,?)",
                     (class_id, username))
        conn.execute("""
            UPDATE users SET grade=?,klass=?,active=1,school_status='active',updated_at=?
            WHERE username=?
        """, (cls["grade"], cls["klass"], now, username))
        if cls["academic_year_id"]:
            conn.execute("""
                INSERT INTO student_enrollments(
                    username,class_id,academic_year_id,status,started_at)
                VALUES(?,?,?,'active',?)
                ON CONFLICT(username,academic_year_id) DO UPDATE SET
                    class_id=excluded.class_id,status='active',started_at=excluded.started_at,ended_at=''
            """, (username, class_id, cls["academic_year_id"], now))
    return {"ok": True}


def remove_class_student(class_id: str, username: str):
    now = _utc_iso()
    with connection(write=True) as conn:
        if not conn.execute("SELECT 1 FROM classes WHERE id=?", (class_id,)).fetchone():
            return {"ok": False, "reason": "class_not_found"}
        conn.execute("DELETE FROM class_students WHERE class_id=? AND username=?",
                     (class_id, username))
        conn.execute("""
            UPDATE student_enrollments SET status='archived',ended_at=?
            WHERE class_id=? AND username=? AND status='active'
        """, (now, class_id, username))
        conn.execute("UPDATE users SET grade='',klass='',updated_at=? WHERE username=?",
                     (now, username))
    return {"ok": True}


def add_class_teacher(class_id: str, username: str):
    with connection(write=True) as conn:
        if not conn.execute("SELECT 1 FROM classes WHERE id=?", (class_id,)).fetchone():
            return {"ok": False, "reason": "class_not_found"}
        if not conn.execute(
            "SELECT 1 FROM users WHERE username=? AND role='teacher'", (username,)
        ).fetchone():
            return {"ok": False, "reason": "teacher_not_found"}
        conn.execute("INSERT OR IGNORE INTO class_teachers(class_id,username) VALUES(?,?)",
                     (class_id, username))
    return {"ok": True}


def remove_class_teacher(class_id: str, username: str):
    with connection(write=True) as conn:
        if not conn.execute("SELECT 1 FROM classes WHERE id=?", (class_id,)).fetchone():
            return {"ok": False, "reason": "class_not_found"}
        conn.execute("DELETE FROM class_teachers WHERE class_id=? AND username=?",
                     (class_id, username))
    return {"ok": True}


def delete_student_user(username: str):
    with connection(write=True) as conn:
        user = conn.execute(
            "SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if not user or user["role"] != "student":
            return {"ok": False, "reason": "not_found"}
        count = _count_user_business_records(conn, username)
        if count:
            return {"ok": False, "reason": "has_records", "count": count}
        conn.execute("DELETE FROM users WHERE username=?", (username,))
    return {"ok": True}


def _count_user_business_records(conn, username: str):
    count = conn.execute(
        "SELECT COUNT(*) FROM history WHERE username=?", (username,)).fetchone()[0]
    for table, column in (
        ("essays", "submitted_by"), ("grading_jobs", "username"),
        ("submission_versions", "username"), ("student_enrollments", "username"),
        ("class_students", "username"),
    ):
        count += conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (username,)).fetchone()[0]
    return count


def update_teacher_profile(username: str, *, employee_no: str, display_name: str,
                           subject: str, class_ids: list[str], active: bool):
    """Update a teacher and atomically rename the login account when the employee number changes."""
    now = _utc_iso()
    class_ids = list(dict.fromkeys(class_ids))
    with connection(write=True) as conn:
        current = conn.execute(
            "SELECT * FROM users WHERE username=? AND role='teacher'", (username,)).fetchone()
        if not current:
            return {"ok": False, "reason": "not_found"}
        collision = conn.execute(
            "SELECT username FROM users WHERE (username=? OR employee_no=?) AND username<>?",
            (employee_no, employee_no, username)).fetchone()
        if collision:
            return {"ok": False, "reason": "duplicate_employee_no"}
        if class_ids:
            marks = ",".join("?" for _ in class_ids)
            found = {row[0] for row in conn.execute(
                f"SELECT id FROM classes WHERE id IN ({marks})", class_ids).fetchall()}
            if found != set(class_ids):
                return {"ok": False, "reason": "class_not_found"}

        target = employee_no
        if target != username:
            conn.execute("""
                INSERT INTO users(username,display_name,role,student_no,employee_no,subject,
                    grade,klass,legacy_salt,password_hash,password_scheme,must_change_password,
                    active,created_at,updated_at)
                VALUES(?,?,'teacher',NULL,?,?,?, ?,?,?,?,?,?,?,?)
            """, (
                target, display_name, employee_no, subject, current["grade"], current["klass"],
                current["legacy_salt"], current["password_hash"], current["password_scheme"],
                current["must_change_password"], int(bool(active)), current["created_at"], now,
            ))
            for table, column in (
                ("class_teachers", "username"), ("assignments", "created_by"),
                ("essays", "submitted_by"), ("history", "username"),
                ("sessions", "username"), ("upload_tokens", "username"),
                ("grading_jobs", "username"), ("submission_versions", "username"),
                ("student_enrollments", "username"),
                ("submission_returns", "teacher_username"),
                ("assignment_showcase", "selected_by"),
                ("ocr_jobs", "username"),
                ("history_artifacts", "created_by"),
            ):
                conn.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (target, username))
            for table, column in (
                ("login_attempts", "username"), ("login_locks", "username"),
                ("audit_logs", "actor_username"), ("feature_modules", "updated_by"),
                ("ai_task_routes", "updated_by"), ("ai_usage", "actor_username"),
                ("assignment_insights", "edited_by"),
            ):
                conn.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (target, username))
            conn.execute("DELETE FROM users WHERE username=?", (username,))
        else:
            conn.execute("""
                UPDATE users SET display_name=?,employee_no=?,subject=?,active=?,updated_at=?
                WHERE username=?
            """, (display_name, employee_no, subject, int(bool(active)), now, username))

        conn.execute("DELETE FROM class_teachers WHERE username=?", (target,))
        for class_id in class_ids:
            conn.execute("INSERT INTO class_teachers(class_id,username) VALUES(?,?)",
                         (class_id, target))
        if not active:
            conn.execute("DELETE FROM sessions WHERE username=?", (target,))
        return {"ok": True, "username": target, "renamed": target != username}


def safely_remove_teachers(usernames: list[str]):
    deleted, deactivated, failed = [], [], []
    with connection(write=True) as conn:
        for username in dict.fromkeys(usernames):
            user = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
            if not user or user["role"] != "teacher":
                failed.append({"username": username, "reason": "老师账号不存在"})
                continue
            business_count = conn.execute(
                "SELECT COUNT(*) FROM assignments WHERE created_by=?", (username,)).fetchone()[0]
            business_count += conn.execute(
                "SELECT COUNT(*) FROM history WHERE username=?", (username,)).fetchone()[0]
            business_count += conn.execute(
                "SELECT COUNT(*) FROM grading_jobs WHERE username=?", (username,)).fetchone()[0]
            if business_count:
                conn.execute("UPDATE users SET active=0,updated_at=? WHERE username=?",
                             (_utc_iso(), username))
                conn.execute("DELETE FROM sessions WHERE username=?", (username,))
                deactivated.append(username)
            else:
                conn.execute("DELETE FROM users WHERE username=?", (username,))
                deleted.append(username)
    return {"deleted": deleted, "deactivated": deactivated, "failed": failed}


def count_user_business_records(username: str):
    """返回该用户关联的不可级联删除的业务记录数（作业/历史/批改任务）。

    学生账号硬删会触发 history/versions/enrollments 的 ON DELETE CASCADE，
    因此有关联记录时应引导停用而非删除。
    """
    with connection() as conn:
        return _count_user_business_records(conn, username)


def transfer_teacher_assignments(source_username: str, target_username: str,
                                 assignment_ids: list[str] | None = None):
    requested = list(dict.fromkeys(assignment_ids or []))
    with connection(write=True) as conn:
        source = conn.execute(
            "SELECT 1 FROM users WHERE username=? AND role='teacher'", (source_username,)).fetchone()
        target = conn.execute(
            "SELECT 1 FROM users WHERE username=? AND role='teacher' AND active=1", (target_username,)).fetchone()
        if not source or not target or source_username == target_username:
            return {"ok": False, "reason": "invalid_teacher"}
        where = "created_by=?"
        params = [source_username]
        if requested:
            marks = ",".join("?" for _ in requested)
            where += f" AND id IN ({marks})"
            params.extend(requested)
        rows = conn.execute(f"SELECT id,class_id FROM assignments WHERE {where}", params).fetchall()
        if requested and {row["id"] for row in rows} != set(requested):
            return {"ok": False, "reason": "assignment_not_found"}
        for row in rows:
            conn.execute("UPDATE assignments SET created_by=? WHERE id=?",
                         (target_username, row["id"]))
            conn.execute("INSERT OR IGNORE INTO class_teachers(class_id,username) VALUES(?,?)",
                         (row["class_id"], target_username))
        return {"ok": True, "assignmentIds": [row["id"] for row in rows]}


def set_student_school_status(username: str, status: str, class_id: str = ""):
    now = _utc_iso()
    with connection(write=True) as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND role='student'", (username,)).fetchone()
        if not user:
            return {"ok": False, "reason": "not_found"}
        if status == "active":
            cls = conn.execute("""
                SELECT * FROM classes WHERE id=? AND status='active'
            """, (class_id,)).fetchone()
            if not cls:
                return {"ok": False, "reason": "class_required"}
            active_year = cls["academic_year_id"]
            conn.execute("DELETE FROM class_students WHERE username=?", (username,))
            conn.execute("INSERT INTO class_students(class_id,username) VALUES(?,?)",
                         (class_id, username))
            conn.execute("""
                INSERT INTO student_enrollments(
                    username,class_id,academic_year_id,status,started_at)
                VALUES(?,?,?,'active',?)
                ON CONFLICT(username,academic_year_id) DO UPDATE SET class_id=excluded.class_id,
                    status='active',started_at=excluded.started_at,ended_at=''
            """, (username, class_id, active_year, now))
            conn.execute("""
                UPDATE users SET active=1,school_status='active',grade=?,klass=?,updated_at=?
                WHERE username=?
            """, (cls["grade"], cls["klass"], now, username))
        else:
            conn.execute("DELETE FROM class_students WHERE username=?", (username,))
            conn.execute("""
                UPDATE student_enrollments SET status='archived',ended_at=?
                WHERE username=? AND status='active'
            """, (now, username))
            conn.execute("""
                UPDATE users SET active=0,school_status=?,updated_at=? WHERE username=?
            """, (status, now, username))
            conn.execute("DELETE FROM sessions WHERE username=?", (username,))
        return {"ok": True, "username": username, "status": status, "classId": class_id}
