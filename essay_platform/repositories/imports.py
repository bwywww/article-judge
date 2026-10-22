"""SQLite persistence for the imports domain."""

from __future__ import annotations

import time
import secrets
from essay_platform.persistence.storage import connection, _dump, _json, _utc_iso

def save_import_preview(preview: dict, ttl: float):
    created = float(preview.get("createdAt") or time.time())
    with connection(write=True) as conn:
        conn.execute("""
            INSERT INTO import_previews(token,payload_json,created_at,expires_at,consumed_at)
            VALUES(?,?,?,?,NULL)
            ON CONFLICT(token) DO UPDATE SET payload_json=excluded.payload_json,
                created_at=excluded.created_at,expires_at=excluded.expires_at,consumed_at=NULL
        """, (preview["token"], _dump(preview), created, created + ttl))


def load_import_preview(token: str):
    with connection() as conn:
        row = conn.execute("SELECT * FROM import_previews WHERE token=?", (token,)).fetchone()
    if not row or row["consumed_at"] is not None or row["expires_at"] < time.time():
        return None
    return _json(row["payload_json"], {})


def consume_import_preview(token: str):
    now = time.time()
    with connection(write=True) as conn:
        result = conn.execute("""
            UPDATE import_previews SET consumed_at=?
            WHERE token=? AND consumed_at IS NULL AND expires_at>=?
        """, (now, token, now))
        return result.rowcount == 1


def unconsume_import_preview(token: str):
    with connection(write=True) as conn:
        conn.execute("UPDATE import_previews SET consumed_at=NULL WHERE token=?", (token,))


def cleanup_import_previews():
    with connection(write=True) as conn:
        conn.execute("DELETE FROM import_previews WHERE expires_at<? OR consumed_at IS NOT NULL", (time.time(),))


def save_upload_token(token: str, assignment_id: str, username: str, filename: str, ttl: float):
    created = time.time()
    with connection(write=True) as conn:
        conn.execute("""
            INSERT INTO upload_tokens(token,assignment_id,username,filename,created_at,expires_at,consumed_at)
            VALUES(?,?,?,?,?,?,NULL)
        """, (token, assignment_id, username, filename, created, created + ttl))


def get_upload_token(token: str):
    with connection() as conn:
        row = conn.execute("SELECT * FROM upload_tokens WHERE token=?", (token,)).fetchone()
    if not row or row["consumed_at"] is not None or row["expires_at"] < time.time():
        return None
    return dict(row)


def consume_upload_token(token: str):
    with connection(write=True) as conn:
        conn.execute("UPDATE upload_tokens SET consumed_at=? WHERE token=?", (time.time(), token))


def delete_upload_token(token: str):
    with connection(write=True) as conn:
        conn.execute("DELETE FROM upload_tokens WHERE token=?", (token,))


def cleanup_upload_tokens():
    with connection(write=True) as conn:
        conn.execute("DELETE FROM upload_tokens WHERE expires_at<? OR consumed_at IS NOT NULL", (time.time(),))


def apply_student_import(rows: list[dict]):
    """Apply a validated preview in one targeted transaction."""
    now = _utc_iso()
    created = updated = 0
    reset_usernames = []
    with connection(write=True) as conn:
        active_year = conn.execute(
            "SELECT id FROM academic_years WHERE status='active' LIMIT 1").fetchone()
        active_year_id = active_year[0] if active_year else ""
        for row in rows:
            grade, klass = row.get("grade", ""), row.get("klass", "")
            cls = conn.execute("""
                SELECT * FROM classes WHERE grade=? AND klass=? AND status='active'
                ORDER BY created_at LIMIT 1
            """, (grade, klass)).fetchone()
            if not cls:
                class_id = "c" + secrets.token_hex(6)
                conn.execute("""
                    INSERT INTO classes(id,name,grade,klass,created_at,academic_year_id,status)
                    VALUES(?,?,?,?,?,?,'active')
                """, (class_id, row.get("className") or f"{grade}{klass}", grade, klass,
                      now, active_year_id or None))
                cls = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
            user = conn.execute("""
                SELECT * FROM users WHERE role='student' AND (student_no=? OR username=?)
                ORDER BY CASE WHEN student_no=? THEN 0 ELSE 1 END LIMIT 1
            """, (row.get("studentNo", ""), row.get("username", ""),
                  row.get("studentNo", ""))).fetchone()
            if user:
                if row.get("passwordProvided"):
                    conn.execute("""
                        UPDATE users SET display_name=?,student_no=?,grade=?,klass=?,active=1,
                            school_status='active',legacy_salt='',password_hash=?,
                            password_scheme='pbkdf2_sha256',must_change_password=1,updated_at=?
                        WHERE username=?
                    """, (row.get("display_name") or user["display_name"],
                          row.get("studentNo", ""), grade, klass,
                          row.get("passwordHash", ""), now, user["username"]))
                    reset_usernames.append(user["username"])
                else:
                    conn.execute("""
                        UPDATE users SET display_name=?,student_no=?,grade=?,klass=?,active=1,
                            school_status='active',updated_at=? WHERE username=?
                    """, (row.get("display_name") or user["display_name"],
                          row.get("studentNo", ""), grade, klass, now, user["username"]))
                username = user["username"]
                updated += 1
            else:
                username = row["username"]
                conn.execute("""
                    INSERT INTO users(username,display_name,role,student_no,grade,klass,
                        password_hash,password_scheme,must_change_password,active,school_status,
                        created_at,updated_at)
                    VALUES(?,?,'student',?,?,?,?,'pbkdf2_sha256',1,1,'active',?,?)
                """, (username, row.get("display_name") or username,
                      row.get("studentNo", ""), grade, klass,
                      row.get("passwordHash", ""), now, now))
                created += 1
            conn.execute("DELETE FROM class_students WHERE username=?", (username,))
            conn.execute("INSERT INTO class_students(class_id,username) VALUES(?,?)",
                         (cls["id"], username))
            if cls["academic_year_id"]:
                conn.execute("""
                    INSERT INTO student_enrollments(
                        username,class_id,academic_year_id,status,started_at)
                    VALUES(?,?,?,'active',?)
                    ON CONFLICT(username,academic_year_id) DO UPDATE SET
                        class_id=excluded.class_id,status='active',started_at=excluded.started_at,
                        ended_at=''
                """, (username, cls["id"], cls["academic_year_id"], now))
        for username in reset_usernames:
            conn.execute("DELETE FROM sessions WHERE username=?", (username,))
    return {"created": created, "updated": updated}


def apply_teacher_import(rows: list[dict]):
    now = _utc_iso()
    created = updated = 0
    with connection(write=True) as conn:
        for row in rows:
            employee_no = row["employeeNo"]
            user = conn.execute("""
                SELECT * FROM users WHERE role='teacher' AND (employee_no=? OR username=?)
                LIMIT 1
            """, (employee_no, employee_no)).fetchone()
            if user:
                username = user["username"]
                conn.execute("""
                    UPDATE users SET display_name=?,employee_no=?,subject=?,active=1,updated_at=?
                    WHERE username=?
                """, (row["display_name"], employee_no, row["subject"], now, username))
                updated += 1
            else:
                username = employee_no
                conn.execute("""
                    INSERT INTO users(username,display_name,role,employee_no,subject,
                        password_hash,password_scheme,must_change_password,active,created_at,updated_at)
                    VALUES(?,?,'teacher',?,?,?,'pbkdf2_sha256',1,1,?,?)
                """, (username, row["display_name"], employee_no, row["subject"],
                      row["passwordHash"], now, now))
                created += 1
            conn.execute("DELETE FROM class_teachers WHERE username=?", (username,))
            for class_id in dict.fromkeys(row.get("classIds") or []):
                if not conn.execute("SELECT 1 FROM classes WHERE id=?", (class_id,)).fetchone():
                    raise ValueError(f"班级不存在：{class_id}")
                conn.execute("INSERT INTO class_teachers(class_id,username) VALUES(?,?)",
                             (class_id, username))
    return {"created": created, "updated": updated}
