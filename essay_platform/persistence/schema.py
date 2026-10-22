"""SQLite schema definition and additive schema upgrades."""

from __future__ import annotations

import sqlite3

from essay_platform.persistence.connection import _current_academic_year, _utc_iso

DB_SCHEMA_VERSION = 8
DATA_SHAPE_VERSION = 5

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'teacher', 'student')),
    student_no TEXT UNIQUE,
    employee_no TEXT,
    subject TEXT NOT NULL DEFAULT '',
    grade TEXT NOT NULL DEFAULT '',
    klass TEXT NOT NULL DEFAULT '',
    legacy_salt TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    password_scheme TEXT NOT NULL DEFAULT 'legacy_sha256',
    must_change_password INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    school_status TEXT NOT NULL DEFAULT 'active'
        CHECK (school_status IN ('active', 'transferred', 'graduated')),
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS classes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    grade TEXT NOT NULL DEFAULT '',
    klass TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    academic_year_id TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    source_class_id TEXT
);

CREATE TABLE IF NOT EXISTS academic_years (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    start_year INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('active', 'closed')),
    created_at TEXT NOT NULL,
    closed_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS student_enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    academic_year_id TEXT NOT NULL REFERENCES academic_years(id),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL DEFAULT '',
    UNIQUE(username, academic_year_id)
);

CREATE TABLE IF NOT EXISTS class_teachers (
    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    PRIMARY KEY (class_id, username)
);

CREATE TABLE IF NOT EXISTS class_students (
    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    username TEXT PRIMARY KEY REFERENCES users(username) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS assignments (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    criteria TEXT NOT NULL DEFAULT '',
    word_limit TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL REFERENCES users(username),
    class_id TEXT NOT NULL REFERENCES classes(id),
    source_file TEXT NOT NULL DEFAULT '',
    lifecycle_status TEXT NOT NULL DEFAULT 'published'
        CHECK (lifecycle_status IN ('draft', 'published', 'closed', 'archived')),
    published_at TEXT NOT NULL DEFAULT '',
    deadline_at TEXT NOT NULL DEFAULT '',
    closed_at TEXT NOT NULL DEFAULT '',
    closed_reason TEXT NOT NULL DEFAULT '',
    archived_at TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    academic_year_id TEXT,
    class_name_snapshot TEXT NOT NULL DEFAULT '',
    created_by_name_snapshot TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS essays (
    id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    student_name TEXT NOT NULL DEFAULT '',
    submitted_by TEXT REFERENCES users(username) ON DELETE SET NULL,
    submission_type TEXT NOT NULL DEFAULT 'teacher_upload',
    locked_by_teacher INTEGER NOT NULL DEFAULT 0,
    submission_open INTEGER NOT NULL DEFAULT 0,
    reopened_at TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    chars INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    score INTEGER,
    level TEXT NOT NULL DEFAULT '',
    feedback_json TEXT,
    ai_feedback_json TEXT,
    feedback_version INTEGER NOT NULL DEFAULT 0,
    feedback_edited_by TEXT NOT NULL DEFAULT '',
    feedback_edited_at TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    error_type TEXT NOT NULL DEFAULT '',
    error_advice TEXT NOT NULL DEFAULT '',
    graded_at TEXT NOT NULL DEFAULT '',
    student_title TEXT NOT NULL DEFAULT '',
    student_prompt TEXT NOT NULL DEFAULT '',
    job_id TEXT NOT NULL DEFAULT '',
    attempt INTEGER NOT NULL DEFAULT 0,
    queued_at TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    duration_seconds REAL,
    result_status TEXT NOT NULL DEFAULT 'not_ready'
        CHECK (result_status IN ('not_ready', 'pending_review', 'published')),
    published_feedback_json TEXT,
    result_published_at TEXT NOT NULL DEFAULT '',
    source_fingerprint TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_assignment_bound_student
ON essays(assignment_id, submitted_by) WHERE submitted_by IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_essays_assignment ON essays(assignment_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_essays_job ON essays(job_id) WHERE job_id <> '';

CREATE TABLE IF NOT EXISTS history (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL DEFAULT '',
    criteria TEXT NOT NULL DEFAULT '',
    word_limit TEXT NOT NULL DEFAULT '',
    assignment_id TEXT REFERENCES assignments(id) ON DELETE SET NULL,
    assignment_title TEXT NOT NULL DEFAULT '',
    class_id TEXT REFERENCES classes(id) ON DELETE SET NULL,
    source_essay_id TEXT REFERENCES essays(id) ON DELETE SET NULL,
    text TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL,
    graded_at TEXT NOT NULL DEFAULT '',
    visible_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    submission_version_id TEXT,
    publication_no INTEGER NOT NULL DEFAULT 1,
    academic_year_id TEXT,
    class_name_snapshot TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_history_user_time ON history(username, graded_at DESC);

CREATE TABLE IF NOT EXISTS history_artifacts (
    id TEXT PRIMARY KEY,
    history_id TEXT NOT NULL REFERENCES history(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('upgrade')),
    source_hash TEXT NOT NULL,
    content_json TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    created_by TEXT REFERENCES users(username) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    UNIQUE(history_id, kind, source_hash)
);
CREATE INDEX IF NOT EXISTS idx_history_artifacts_history
ON history_artifacts(history_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(username);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS login_attempts (
    username TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    failed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup
ON login_attempts(username, ip_address, failed_at);

CREATE TABLE IF NOT EXISTS login_locks (
    username TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    locked_until REAL NOT NULL,
    PRIMARY KEY (username, ip_address)
);

CREATE TABLE IF NOT EXISTS import_previews (
    token TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    consumed_at REAL
);

CREATE TABLE IF NOT EXISTS upload_tokens (
    token TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    consumed_at REAL
);

CREATE TABLE IF NOT EXISTS ocr_jobs (
    id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    upload_token TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    split_mode TEXT NOT NULL DEFAULT 'auto'
        CHECK (split_mode IN ('auto', 'one_page', 'two_pages')),
    status TEXT NOT NULL
        CHECK (status IN ('queued', 'extracting', 'ocr', 'done', 'error', 'cancelled')),
    stage TEXT NOT NULL DEFAULT 'queued',
    total_pages INTEGER NOT NULL DEFAULT 0,
    completed_pages INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    queued_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    error_type TEXT NOT NULL DEFAULT '',
    error_advice TEXT NOT NULL DEFAULT '',
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_ocr_jobs_status_time ON ocr_jobs(status, queued_at);
CREATE INDEX IF NOT EXISTS idx_ocr_jobs_owner ON ocr_jobs(username, assignment_id, queued_at DESC);

CREATE TABLE IF NOT EXISTS ocr_job_pages (
    job_id TEXT NOT NULL REFERENCES ocr_jobs(id) ON DELETE CASCADE,
    page_no INTEGER NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'text_extracted', 'ocr_done', 'error')),
    text TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (job_id, page_no)
);
CREATE INDEX IF NOT EXISTS idx_ocr_pages_status ON ocr_job_pages(job_id, status, page_no);

CREATE TABLE IF NOT EXISTS grading_jobs (
    id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL DEFAULT '',
    target_type TEXT NOT NULL CHECK (target_type IN ('assignment', 'practice')),
    assignment_id TEXT REFERENCES assignments(id) ON DELETE CASCADE,
    essay_id TEXT REFERENCES essays(id) ON DELETE CASCADE,
    username TEXT REFERENCES users(username) ON DELETE CASCADE,
    practice_title TEXT NOT NULL DEFAULT '',
    practice_prompt TEXT NOT NULL DEFAULT '',
    practice_criteria TEXT NOT NULL DEFAULT '',
    practice_word_limit TEXT NOT NULL DEFAULT '',
    practice_text TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'error')),
    attempt INTEGER NOT NULL DEFAULT 0,
    queued_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    duration_seconds REAL,
    error TEXT NOT NULL DEFAULT '',
    error_type TEXT NOT NULL DEFAULT '',
    error_advice TEXT NOT NULL DEFAULT '',
    history_id TEXT REFERENCES history(id) ON DELETE SET NULL,
    result_json TEXT,
    submission_version_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_time ON grading_jobs(status, queued_at);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON grading_jobs(username, queued_at DESC);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor_username TEXT,
    event_type TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    ip_address TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_logs(event_type, created_at DESC);

CREATE TABLE IF NOT EXISTS feature_modules (
    module_key TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'enabled'
        CHECK (status IN ('enabled', 'draining', 'disabled')),
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ai_task_routes (
    task_type TEXT PRIMARY KEY,
    profile TEXT NOT NULL DEFAULT 'article_judge',
    model TEXT NOT NULL DEFAULT 'deepseek-v4-flash',
    thinking INTEGER NOT NULL DEFAULT 0,
    json_mode INTEGER NOT NULL DEFAULT 1,
    max_tokens INTEGER NOT NULL DEFAULT 2600,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    profile TEXT NOT NULL,
    task_type TEXT NOT NULL,
    model TEXT NOT NULL,
    actor_username TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_type TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_time ON ai_usage(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_usage_task ON ai_usage(task_type,created_at DESC);

CREATE TABLE IF NOT EXISTS submission_versions (
    id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    essay_id TEXT NOT NULL REFERENCES essays(id) ON DELETE CASCADE,
    username TEXT REFERENCES users(username) ON DELETE SET NULL,
    version_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'grading', 'pending_review', 'published', 'returned', 'error')),
    score INTEGER,
    level TEXT NOT NULL DEFAULT '',
    feedback_json TEXT,
    published_at TEXT NOT NULL DEFAULT '',
    job_id TEXT NOT NULL DEFAULT '',
    UNIQUE(essay_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_submission_versions_essay
ON submission_versions(essay_id, version_no DESC);
CREATE INDEX IF NOT EXISTS idx_submission_versions_student
ON submission_versions(username, submitted_at DESC);

CREATE TABLE IF NOT EXISTS submission_returns (
    id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    essay_id TEXT NOT NULL REFERENCES essays(id) ON DELETE CASCADE,
    submission_version_id TEXT NOT NULL REFERENCES submission_versions(id) ON DELETE CASCADE,
    teacher_username TEXT NOT NULL REFERENCES users(username),
    comment TEXT NOT NULL,
    returned_at TEXT NOT NULL,
    resubmit_deadline_at TEXT NOT NULL DEFAULT '',
    resolved_at TEXT NOT NULL DEFAULT '',
    cancelled_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_submission_returns_essay
ON submission_returns(essay_id, returned_at DESC);

CREATE TABLE IF NOT EXISTS assignment_insights (
    assignment_id TEXT PRIMARY KEY REFERENCES assignments(id) ON DELETE CASCADE,
    ai_json TEXT,
    current_json TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    generated_at TEXT NOT NULL,
    edited_by TEXT NOT NULL DEFAULT '',
    edited_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS assignment_showcase (
    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    essay_id TEXT NOT NULL REFERENCES essays(id) ON DELETE CASCADE,
    selected_by TEXT NOT NULL REFERENCES users(username),
    selected_at TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (assignment_id, essay_id)
);
CREATE INDEX IF NOT EXISTS idx_showcase_assignment
ON assignment_showcase(assignment_id, published_at);
"""

def _create_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA_SQL)
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    for name, definition in (
        ("employee_no", "TEXT"),
        ("subject", "TEXT NOT NULL DEFAULT ''"),
        ("school_status", "TEXT NOT NULL DEFAULT 'active'"),
    ):
        if name not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
    conn.execute("""
        UPDATE users SET employee_no=username
        WHERE role='teacher' AND (employee_no IS NULL OR employee_no='')
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_users_employee_no
        ON users(employee_no) WHERE employee_no IS NOT NULL AND employee_no <> ''
    """)
    essay_columns = {row[1] for row in conn.execute("PRAGMA table_info(essays)")}
    for name, definition in (
        ("ai_feedback_json", "TEXT"),
        ("feedback_version", "INTEGER NOT NULL DEFAULT 0"),
        ("feedback_edited_by", "TEXT NOT NULL DEFAULT ''"),
        ("feedback_edited_at", "TEXT NOT NULL DEFAULT ''"),
        ("source_fingerprint", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in essay_columns:
            conn.execute(f"ALTER TABLE essays ADD COLUMN {name} {definition}")
    conn.execute("""
        UPDATE essays SET ai_feedback_json=feedback_json
        WHERE ai_feedback_json IS NULL AND feedback_json IS NOT NULL
    """)
    conn.execute("""
        UPDATE essays SET feedback_version=1
        WHERE feedback_json IS NOT NULL AND feedback_version < 1
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_assignment_source_fingerprint
        ON essays(assignment_id, source_fingerprint)
        WHERE submitted_by IS NULL AND source_fingerprint <> ''
    """)
    job_columns = {row[1] for row in conn.execute("PRAGMA table_info(grading_jobs)")}
    for name, definition in (
        ("submission_id", "TEXT NOT NULL DEFAULT ''"),
        ("practice_criteria", "TEXT NOT NULL DEFAULT ''"),
        ("practice_word_limit", "TEXT NOT NULL DEFAULT ''"),
        ("submission_version_id", "TEXT"),
    ):
        if name not in job_columns:
            conn.execute(f"ALTER TABLE grading_jobs ADD COLUMN {name} {definition}")

    class_columns = {row[1] for row in conn.execute("PRAGMA table_info(classes)")}
    for name, definition in (
        ("academic_year_id", "TEXT"),
        ("status", "TEXT NOT NULL DEFAULT 'active'"),
        ("source_class_id", "TEXT"),
    ):
        if name not in class_columns:
            conn.execute(f"ALTER TABLE classes ADD COLUMN {name} {definition}")

    assignment_columns = {row[1] for row in conn.execute("PRAGMA table_info(assignments)")}
    for name, definition in (
        ("academic_year_id", "TEXT"),
        ("class_name_snapshot", "TEXT NOT NULL DEFAULT ''"),
        ("created_by_name_snapshot", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in assignment_columns:
            conn.execute(f"ALTER TABLE assignments ADD COLUMN {name} {definition}")

    ocr_job_columns = {row[1] for row in conn.execute("PRAGMA table_info(ocr_jobs)")}
    if "split_mode" not in ocr_job_columns:
        conn.execute("ALTER TABLE ocr_jobs ADD COLUMN split_mode TEXT NOT NULL DEFAULT 'auto'")

    history_columns = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
    for name, definition in (
        ("submission_version_id", "TEXT"),
        ("publication_no", "INTEGER NOT NULL DEFAULT 1"),
        ("academic_year_id", "TEXT"),
        ("class_name_snapshot", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in history_columns:
            conn.execute(f"ALTER TABLE history ADD COLUMN {name} {definition}")

    academic_year = _current_academic_year()
    now = _utc_iso()
    active_year = conn.execute(
        "SELECT id FROM academic_years WHERE status='active' ORDER BY start_year DESC LIMIT 1"
    ).fetchone()
    if active_year:
        active_year_id = active_year[0]
    else:
        conn.execute("""
            INSERT OR IGNORE INTO academic_years(id,label,start_year,status,created_at)
            VALUES(?,?,?,'active',?)
        """, (academic_year["id"], academic_year["label"], academic_year["startYear"], now))
        active_year_id = academic_year["id"]
    conn.execute("UPDATE classes SET academic_year_id=? WHERE academic_year_id IS NULL OR academic_year_id=''",
                 (active_year_id,))
    conn.execute("""
        INSERT OR IGNORE INTO student_enrollments(
            username,class_id,academic_year_id,status,started_at)
        SELECT cs.username,cs.class_id,COALESCE(c.academic_year_id,?),'active',?
        FROM class_students cs JOIN classes c ON c.id=cs.class_id
    """, (active_year_id, now))
    conn.execute("""
        UPDATE assignments SET
            academic_year_id=COALESCE(NULLIF(academic_year_id,''),
                (SELECT c.academic_year_id FROM classes c WHERE c.id=assignments.class_id), ?),
            class_name_snapshot=CASE WHEN class_name_snapshot='' THEN
                COALESCE((SELECT c.name FROM classes c WHERE c.id=assignments.class_id),'')
                ELSE class_name_snapshot END,
            created_by_name_snapshot=CASE WHEN created_by_name_snapshot='' THEN
                COALESCE((SELECT u.display_name FROM users u WHERE u.username=assignments.created_by),'')
                ELSE created_by_name_snapshot END
    """, (active_year_id,))
    conn.execute("""
        INSERT OR IGNORE INTO submission_versions(
            id,assignment_id,essay_id,username,version_no,text,content_hash,submitted_at,
            status,score,level,feedback_json,published_at,job_id)
        SELECT 'sv-' || e.id || '-1',e.assignment_id,e.id,e.submitted_by,1,e.text,e.content_hash,
            COALESCE(NULLIF(e.submitted_at,''),NULLIF(e.graded_at,''),?),
            CASE
                WHEN e.result_status='published' THEN 'published'
                WHEN e.result_status='pending_review' THEN 'pending_review'
                WHEN e.status IN ('queued','grading') THEN 'grading'
                WHEN e.status='error' THEN 'error'
                ELSE 'submitted'
            END,
            e.score,e.level,e.feedback_json,e.result_published_at,e.job_id
        FROM essays e WHERE e.submitted_by IS NOT NULL
    """, (now,))
    conn.execute("""
        UPDATE grading_jobs SET submission_version_id=(
            SELECT sv.id FROM submission_versions sv
            WHERE sv.essay_id=grading_jobs.essay_id
            ORDER BY sv.version_no DESC LIMIT 1)
        WHERE target_type='assignment' AND submission_version_id IS NULL
    """)
    conn.execute("""
        UPDATE history SET
            submission_version_id=COALESCE(submission_version_id,
                (SELECT sv.id FROM submission_versions sv
                 WHERE sv.essay_id=history.source_essay_id
                 ORDER BY sv.version_no DESC LIMIT 1)),
            academic_year_id=COALESCE(NULLIF(academic_year_id,''),
                (SELECT a.academic_year_id FROM assignments a WHERE a.id=history.assignment_id)),
            class_name_snapshot=CASE WHEN class_name_snapshot='' THEN
                COALESCE((SELECT a.class_name_snapshot FROM assignments a WHERE a.id=history.assignment_id),'')
                ELSE class_name_snapshot END
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_academic_year
        ON academic_years(status) WHERE status='active'
    """)
    conn.execute("DELETE FROM ai_task_routes WHERE task_type='development' OR profile='code_develop'")
    conn.execute(
        "INSERT INTO schema_meta(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(DB_SCHEMA_VERSION),),
    )
