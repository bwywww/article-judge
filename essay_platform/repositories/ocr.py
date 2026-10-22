"""Persistent OCR/import job repository.

All state transitions are guarded by SQLite transactions so more than one WSGI
process may observe the queue without processing the same job twice.
"""

from __future__ import annotations

import secrets

from essay_platform.persistence.storage import connection, _dump, _json, _utc_iso


def _job(row):
    if not row:
        return None
    return {
        "jobId": row["id"], "assignmentId": row["assignment_id"],
        "username": row["username"], "uploadToken": row["upload_token"],
        "filename": row["filename"], "fileType": row["file_type"],
        "splitMode": row["split_mode"] if "split_mode" in row.keys() else "auto",
        "status": row["status"], "stage": row["stage"],
        "totalPages": int(row["total_pages"] or 0),
        "completedPages": int(row["completed_pages"] or 0),
        "cancelRequested": bool(row["cancel_requested"]),
        "attempt": int(row["attempt"] or 0), "queuedAt": row["queued_at"],
        "startedAt": row["started_at"], "finishedAt": row["finished_at"],
        "error": row["error"], "errorType": row["error_type"],
        "errorAdvice": row["error_advice"],
        "result": _json(row["result_json"], None),
    }


def create_ocr_job(assignment_id: str, username: str, upload_token: str,
                   filename: str, file_type: str, split_mode: str = "auto"):
    if split_mode not in ("auto", "one_page", "two_pages"):
        raise ValueError("不支持的作文纸拆分方式")
    job_id = "ocr" + secrets.token_hex(12)
    now = _utc_iso()
    with connection(write=True) as conn:
        owner = conn.execute(
            "SELECT id,lifecycle_status FROM assignments WHERE id=? AND created_by=?",
            (assignment_id, username),).fetchone()
        token = conn.execute(
            "SELECT token FROM upload_tokens WHERE token=? AND assignment_id=? AND username=? "
            "AND consumed_at IS NULL",
            (upload_token, assignment_id, username),).fetchone()
        if not owner or not token:
            return None
        if owner["lifecycle_status"] == "archived":
            return {"error": "archived"}
        conn.execute("""
            INSERT INTO ocr_jobs(
                id,assignment_id,username,upload_token,filename,file_type,split_mode,
                status,stage,queued_at)
            VALUES(?,?,?,?,?,?,?,'queued','queued',?)
        """, (job_id, assignment_id, username, upload_token, filename, file_type,
              split_mode, now))
        row = conn.execute("SELECT * FROM ocr_jobs WHERE id=?", (job_id,)).fetchone()
    return _job(row)


def get_ocr_job_owned(job_id: str, assignment_id: str, username: str):
    with connection() as conn:
        row = conn.execute("""
            SELECT j.* FROM ocr_jobs j
            JOIN assignments a ON a.id=j.assignment_id
            WHERE j.id=? AND j.assignment_id=? AND j.username=? AND a.created_by=?
        """, (job_id, assignment_id, username, username)).fetchone()
    return _job(row)


def claim_ocr_job(job_id: str):
    now = _utc_iso()
    with connection(write=True) as conn:
        row = conn.execute("""
            SELECT j.* FROM ocr_jobs j
            JOIN assignments a ON a.id=j.assignment_id
            WHERE j.id=? AND j.status='queued' AND j.cancel_requested=0
              AND a.created_by=j.username AND a.lifecycle_status<>'archived'
        """, (job_id,)).fetchone()
        if not row:
            return None
        changed = conn.execute("""
            UPDATE ocr_jobs SET status='extracting',stage='extracting',
                started_at=CASE WHEN started_at='' THEN ? ELSE started_at END,
                attempt=attempt+1,error='',error_type='',error_advice=''
            WHERE id=? AND status='queued' AND cancel_requested=0
        """, (now, job_id))
        if changed.rowcount != 1:
            return None
        row = conn.execute("SELECT * FROM ocr_jobs WHERE id=?", (job_id,)).fetchone()
    return _job(row)


def initialize_ocr_pages(job_id: str, total_pages: int):
    with connection(write=True) as conn:
        conn.execute("UPDATE ocr_jobs SET total_pages=? WHERE id=?", (total_pages, job_id))
        for page_no in range(1, total_pages + 1):
            conn.execute("""
                INSERT OR IGNORE INTO ocr_job_pages(job_id,page_no,status)
                VALUES(?,?,'pending')
            """, (job_id, page_no))


def list_ocr_pages(job_id: str):
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM ocr_job_pages WHERE job_id=? ORDER BY page_no", (job_id,)).fetchall()
    return [{"pageNo": row["page_no"], "status": row["status"], "text": row["text"],
             "retryCount": row["retry_count"], "error": row["error"]} for row in rows]


def set_ocr_stage(job_id: str, stage: str):
    if stage not in ("extracting", "ocr"):
        raise ValueError("invalid OCR stage")
    with connection(write=True) as conn:
        conn.execute("UPDATE ocr_jobs SET status=?,stage=? WHERE id=? AND status NOT IN "
                     "('done','error','cancelled')", (stage, stage, job_id))


def finish_ocr_page(job_id: str, page_no: int, text: str, *, used_ocr: bool):
    status = "ocr_done" if used_ocr else "text_extracted"
    with connection(write=True) as conn:
        previous = conn.execute(
            "SELECT status FROM ocr_job_pages WHERE job_id=? AND page_no=?",
            (job_id, page_no),).fetchone()
        conn.execute("""
            UPDATE ocr_job_pages SET status=?,text=?,error=''
            WHERE job_id=? AND page_no=?
        """, (status, text, job_id, page_no))
        if previous and previous["status"] not in ("text_extracted", "ocr_done"):
            conn.execute("UPDATE ocr_jobs SET completed_pages=completed_pages+1 WHERE id=?",
                         (job_id,))


def record_ocr_page_retry(job_id: str, page_no: int, error: str):
    with connection(write=True) as conn:
        conn.execute("""
            UPDATE ocr_job_pages SET retry_count=retry_count+1,error=?
            WHERE job_id=? AND page_no=?
        """, (str(error)[:1000], job_id, page_no))


def fail_ocr_page(job_id: str, page_no: int, error: str):
    with connection(write=True) as conn:
        conn.execute("""
            UPDATE ocr_job_pages SET status='error',error=?
            WHERE job_id=? AND page_no=?
        """, (str(error)[:1000], job_id, page_no))


def finish_ocr_job(job_id: str, result: dict):
    now = _utc_iso()
    with connection(write=True) as conn:
        conn.execute("""
            UPDATE ocr_jobs SET status='done',stage='done',finished_at=?,
                result_json=?,error='',error_type='',error_advice=''
            WHERE id=? AND status IN ('extracting','ocr') AND cancel_requested=0
        """, (now, _dump(result), job_id))


def fail_ocr_job(job_id: str, error: str, error_type: str, error_advice: str):
    now = _utc_iso()
    with connection(write=True) as conn:
        conn.execute("""
            UPDATE ocr_jobs SET status='error',stage='error',finished_at=?,error=?,
                error_type=?,error_advice=?
            WHERE id=? AND status NOT IN ('done','cancelled')
        """, (now, str(error)[:2000], error_type[:100], error_advice[:1000], job_id))


def ocr_cancel_requested(job_id: str) -> bool:
    with connection() as conn:
        row = conn.execute("SELECT cancel_requested FROM ocr_jobs WHERE id=?", (job_id,)).fetchone()
    return bool(row and row[0])


def request_ocr_cancel(job_id: str, assignment_id: str, username: str):
    now = _utc_iso()
    with connection(write=True) as conn:
        row = conn.execute("""
            SELECT j.status FROM ocr_jobs j JOIN assignments a ON a.id=j.assignment_id
            WHERE j.id=? AND j.assignment_id=? AND j.username=? AND a.created_by=?
        """, (job_id, assignment_id, username, username)).fetchone()
        if not row:
            return None
        if row["status"] in ("done", "error", "cancelled"):
            return {"status": row["status"], "changed": False}
        conn.execute("UPDATE ocr_jobs SET cancel_requested=1 WHERE id=?", (job_id,))
        if row["status"] == "queued":
            conn.execute("""
                UPDATE ocr_jobs SET status='cancelled',stage='cancelled',finished_at=?
                WHERE id=?
            """, (now, job_id))
            return {"status": "cancelled", "changed": True}
        return {"status": row["status"], "changed": True}


def mark_ocr_cancelled(job_id: str):
    now = _utc_iso()
    with connection(write=True) as conn:
        conn.execute("""
            UPDATE ocr_jobs SET status='cancelled',stage='cancelled',finished_at=?
            WHERE id=? AND status NOT IN ('done','cancelled')
        """, (now, job_id))


def retry_ocr_job(job_id: str, assignment_id: str, username: str):
    now = _utc_iso()
    with connection(write=True) as conn:
        row = conn.execute("""
            SELECT j.* FROM ocr_jobs j JOIN assignments a ON a.id=j.assignment_id
            WHERE j.id=? AND j.assignment_id=? AND j.username=? AND a.created_by=?
        """, (job_id, assignment_id, username, username)).fetchone()
        if not row:
            return None
        if row["status"] != "error":
            return {"error": "invalid_state", "status": row["status"]}
        conn.execute("""
            UPDATE ocr_job_pages SET status='pending',error=''
            WHERE job_id=? AND status='error'
        """, (job_id,))
        conn.execute("""
            UPDATE ocr_jobs SET status='queued',stage='queued',cancel_requested=0,
                queued_at=?,finished_at='',error='',error_type='',error_advice=''
            WHERE id=? AND status='error'
        """, (now, job_id))
        row = conn.execute("SELECT * FROM ocr_jobs WHERE id=?", (job_id,)).fetchone()
    return _job(row)


def recover_ocr_jobs():
    now = _utc_iso()
    with connection(write=True) as conn:
        conn.execute("""
            UPDATE ocr_jobs SET status='cancelled',stage='cancelled',finished_at=?
            WHERE status IN ('queued','extracting','ocr') AND cancel_requested=1
        """, (now,))
        conn.execute("""
            UPDATE ocr_jobs SET status='queued',stage='queued',queued_at=?
            WHERE status IN ('extracting','ocr') AND cancel_requested=0
        """, (now,))
        return [row[0] for row in conn.execute(
            "SELECT id FROM ocr_jobs WHERE status='queued' ORDER BY queued_at,id").fetchall()]


def count_active_ocr_jobs() -> int:
    with connection() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM ocr_jobs WHERE status IN ('queued','extracting','ocr')"
        ).fetchone()[0])
