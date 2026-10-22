"""SQLite persistence for the grading domain."""

from __future__ import annotations

import hashlib
import secrets
from essay_platform.persistence.storage import connection, _dump, _json, _utc_iso

def _text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def enqueue_assignment_jobs(assignment_id: str, essay_ids=None, *, force: bool = False):
    target_ids = set(essay_ids or [])
    jobs = []
    existing = 0
    queued_at = _utc_iso()
    with connection(write=True) as conn:
        assignment = conn.execute("SELECT id FROM assignments WHERE id=?", (assignment_id,)).fetchone()
        if not assignment:
            return {"jobs": [], "existing": 0}
        rows = conn.execute(
            "SELECT * FROM essays WHERE assignment_id=? ORDER BY sort_order,id",
            (assignment_id,)).fetchall()
        for row in rows:
            if target_ids and row["id"] not in target_ids:
                continue
            current_status = row["status"]
            current_job = row["job_id"]
            if current_status in ("queued", "grading") and current_job:
                active = conn.execute(
                    "SELECT id,status,started_at FROM grading_jobs WHERE id=? AND status IN ('queued','running')",
                    (current_job,)).fetchone()
                if active:
                    # force 重批时，仍未被 worker 领取的 queued job（started_at 空）视为卡死，允许重排；
                    # 已被领取（running 或 started_at 非空）则保留，避免重复批改。
                    if not (force and active["status"] == "queued" and not active["started_at"]):
                        existing += 1
                        continue
                    conn.execute(
                        "UPDATE grading_jobs SET status='error',finished_at=?,error=? "
                        "WHERE id=? AND status='queued'",
                        (queued_at, "已被新的重批任务取代", current_job))
                    conn.execute("UPDATE essays SET status='pending',job_id='',started_at='' WHERE id=?",
                                 (row["id"],))
                    current_status = "pending"
                else:
                    conn.execute("UPDATE essays SET status='pending',job_id='',started_at='' WHERE id=?",
                                 (row["id"],))
                    current_status = "pending"
            if not force and current_status != "pending":
                continue
            job_id = "j" + secrets.token_hex(12)
            expected_hash = _text_hash(row["text"])
            # 维护不可变版本链：老师导入且无版本时建 version 1；force 重批时追加新版本。
            last_version = conn.execute(
                "SELECT COALESCE(MAX(version_no),0) FROM submission_versions WHERE essay_id=?",
                (row["id"],)).fetchone()[0]
            version_no = last_version
            version_id = ""
            if last_version == 0:
                version_no = 1
            elif force:
                version_no = last_version + 1
            if version_no > last_version:
                version_id = "sv" + secrets.token_hex(12)
                conn.execute("""
                    INSERT INTO submission_versions(
                        id,assignment_id,essay_id,username,version_no,text,content_hash,
                        submitted_at,status,job_id)
                    VALUES(?,?,?,?,?,?,?,?, 'submitted',?)
                """, (version_id, assignment_id, row["id"], row["submitted_by"],
                      version_no, row["text"], expected_hash, queued_at, job_id))
            conn.execute("""
                INSERT INTO grading_jobs(
                    id,submission_id,target_type,assignment_id,essay_id,username,
                    content_hash,status,attempt,queued_at,submission_version_id)
                VALUES(?,?,'assignment',?,?,?,?,'queued',0,?,?)
            """, (job_id, row["id"], assignment_id, row["id"], row["submitted_by"],
                  expected_hash, queued_at, version_id or None))
            conn.execute("""
                UPDATE essays SET status='queued',job_id=?,content_hash=?,attempt=0,
                    queued_at=?,started_at='',finished_at='',duration_seconds=NULL,
                    score=NULL,level='',feedback_json=NULL,graded_at='',error='',
                    error_type='',error_advice=''
                WHERE id=?
            """, (job_id, expected_hash, queued_at, row["id"]))
            if version_id:
                conn.execute("""
                    UPDATE submission_versions SET status='grading', job_id=?
                    WHERE id=?
                """, (job_id, version_id))
            jobs.append({"jobId": job_id, "essayId": row["id"],
                         "contentHash": expected_hash})
    return {"jobs": jobs, "existing": existing}


def create_assignment_submission_job(assignment_id: str, username: str, text: str,
                                     student_title: str = ""):
    expected_hash = _text_hash(text)
    queued_at = _utc_iso()
    with connection(write=True) as conn:
        assignment = conn.execute("""
            SELECT a.* FROM assignments a
            JOIN class_students cs ON cs.class_id=a.class_id
            WHERE a.id=? AND cs.username=?
        """, (assignment_id, username)).fetchone()
        if not assignment:
            return {"ok": False, "reason": "not_visible"}
        essay = conn.execute(
            "SELECT * FROM essays WHERE assignment_id=? AND submitted_by=?",
            (assignment_id, username)).fetchone()
        returned = None
        if essay:
            returned = conn.execute("""
                SELECT * FROM submission_returns
                WHERE essay_id=? AND resolved_at='' AND cancelled_at=''
                ORDER BY returned_at DESC LIMIT 1
            """, (essay["id"],)).fetchone()
        if not returned and assignment["lifecycle_status"] != "published":
            return {"ok": False, "reason": "assignment_closed"}
        if (not returned and assignment["deadline_at"]
                and assignment["deadline_at"] <= _utc_iso()):
            conn.execute("""
                UPDATE assignments SET lifecycle_status='closed',closed_at=?,
                    closed_reason='deadline' WHERE id=?
            """, (_utc_iso(), assignment_id))
            return {"ok": False, "reason": "assignment_closed"}
        if (returned and returned["resubmit_deadline_at"]
                and returned["resubmit_deadline_at"] <= _utc_iso()):
            conn.execute("UPDATE essays SET submission_open=0 WHERE id=?", (essay["id"],))
            return {"ok": False, "reason": "resubmit_deadline_passed"}
        user = conn.execute("SELECT * FROM users WHERE username=? AND role='student' AND active=1",
                            (username,)).fetchone()
        if not user:
            return {"ok": False, "reason": "not_visible"}
        if essay and essay["job_id"]:
            active = conn.execute(
                "SELECT * FROM grading_jobs WHERE id=? AND status IN ('queued','running')",
                (essay["job_id"],)).fetchone()
            if active:
                if active["content_hash"] == expected_hash:
                    return {"ok": True, "existing": True, "jobId": active["id"],
                            "submissionId": essay["id"], "status": active["status"]}
                return {"ok": False, "reason": "in_progress"}
        if essay and not essay["submission_open"]:
            return {"ok": False, "reason": "locked"}

        essay_id = essay["id"] if essay else "e" + secrets.token_hex(6)
        job_id = "j" + secrets.token_hex(12)
        version_no = conn.execute(
            "SELECT COALESCE(MAX(version_no),0)+1 FROM submission_versions WHERE essay_id=?",
            (essay_id,)).fetchone()[0]
        version_id = "sv" + secrets.token_hex(12)
        student_name = user["display_name"] or username
        submitted_title = (student_title or assignment["title"]).strip()[:120]
        if essay:
            conn.execute("""
                UPDATE essays SET student_name=?,submission_type='student_upload',
                    locked_by_teacher=0,submission_open=0,reopened_at='',submitted_at=?,
                    text=?,chars=?,content_hash=?,status='queued',score=NULL,level='',
                    feedback_json=NULL,error='',error_type='',error_advice='',graded_at='',
                    student_title=?,student_prompt=?,job_id=?,attempt=0,queued_at=?,
                    started_at='',finished_at='',duration_seconds=NULL,result_status='not_ready'
                WHERE id=?
            """, (student_name, queued_at, text, len(text), expected_hash,
                  submitted_title, assignment["prompt"], job_id, queued_at, essay_id))
        else:
            sort_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order),-1)+1 FROM essays WHERE assignment_id=?",
                (assignment_id,)).fetchone()[0]
            conn.execute("""
                INSERT INTO essays(
                    id,assignment_id,student_name,submitted_by,submission_type,
                    locked_by_teacher,submission_open,submitted_at,text,chars,content_hash,
                    status,student_title,student_prompt,job_id,attempt,queued_at,sort_order)
                VALUES(?,?,?,?,'student_upload',0,0,?,?,?,?, 'queued',?,?,?,0,?,?)
            """, (essay_id, assignment_id, student_name, username, queued_at, text,
                  len(text), expected_hash, submitted_title, assignment["prompt"],
                  job_id, queued_at, sort_order))
        conn.execute("""
            INSERT INTO submission_versions(
                id,assignment_id,essay_id,username,version_no,text,content_hash,
                submitted_at,status,job_id)
            VALUES(?,?,?,?,?,?,?,?, 'grading',?)
        """, (version_id, assignment_id, essay_id, username, version_no, text,
              expected_hash, queued_at, job_id))
        if returned:
            conn.execute("UPDATE submission_returns SET resolved_at=? WHERE id=?",
                         (queued_at, returned["id"]))
        conn.execute("""
            INSERT INTO grading_jobs(
                id,submission_id,target_type,assignment_id,essay_id,username,
                content_hash,status,attempt,queued_at,submission_version_id)
            VALUES(?,?,'assignment',?,?,?,?,'queued',0,?,?)
        """, (job_id, essay_id, assignment_id, essay_id, username,
              expected_hash, queued_at, version_id))
        return {"ok": True, "existing": False, "jobId": job_id,
                "submissionId": essay_id, "status": "queued",
                "assignmentTitle": assignment["title"], "versionNo": version_no,
                "versionId": version_id}


def create_practice_job(username: str, title: str, prompt: str, criteria: str,
                        word_limit: str, text: str):
    expected_hash = hashlib.sha256(
        "\0".join((title or "", prompt or "", criteria or "", word_limit or "", text or ""))
        .encode("utf-8")).hexdigest()
    with connection(write=True) as conn:
        existing = conn.execute("""
            SELECT * FROM grading_jobs
            WHERE target_type='practice' AND username=? AND content_hash=?
                AND status IN ('queued','running')
            ORDER BY queued_at DESC LIMIT 1
        """, (username, expected_hash)).fetchone()
        if existing:
            return {"ok": True, "existing": True, "jobId": existing["id"],
                    "submissionId": existing["submission_id"], "status": existing["status"]}
        job_id = "j" + secrets.token_hex(12)
        submission_id = "p" + secrets.token_hex(12)
        queued_at = _utc_iso()
        conn.execute("""
            INSERT INTO grading_jobs(
                id,submission_id,target_type,username,practice_title,practice_prompt,
                practice_criteria,practice_word_limit,practice_text,content_hash,
                status,attempt,queued_at)
            VALUES(?,?,'practice',?,?,?,?,?,?,?,'queued',0,?)
        """, (job_id, submission_id, username, title, prompt, criteria, word_limit,
              text, expected_hash, queued_at))
        return {"ok": True, "existing": False, "jobId": job_id,
                "submissionId": submission_id, "status": "queued"}


def claim_grading_job(job_id: str):
    started_at = _utc_iso()
    with connection(write=True) as conn:
        job = conn.execute("SELECT * FROM grading_jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["status"] != "queued":
            return None
        if job["target_type"] == "assignment":
            context = conn.execute("""
                SELECT e.text,e.student_name,e.job_id,e.content_hash AS essay_hash,
                       COALESCE(NULLIF(e.student_title,''),a.title) AS title,
                       COALESCE(NULLIF(e.student_prompt,''),a.prompt) AS prompt,
                       a.criteria,a.word_limit
                FROM essays e JOIN assignments a ON a.id=e.assignment_id
                WHERE e.id=? AND e.assignment_id=?
            """, (job["essay_id"], job["assignment_id"])).fetchone()
            if (not context or context["job_id"] != job_id
                    or _text_hash(context["text"]) != job["content_hash"]):
                conn.execute("""
                    UPDATE grading_jobs SET status='error',finished_at=?,error=?,
                        error_type='content_changed',error_advice=? WHERE id=?
                """, (started_at, "作文正文或任务版本已变化，旧任务已取消",
                      "请重新提交批改。", job_id))
                if context and context["job_id"] == job_id:
                    conn.execute("""
                        UPDATE essays SET status='pending',job_id='',error=?,
                            error_type='正文已变化',error_advice=? WHERE id=?
                    """, ("正文已变化，旧任务已取消", "请重新开始批改。", job["essay_id"]))
                return None
            title, prompt = context["title"], context["prompt"]
            criteria, word_limit = context["criteria"], context["word_limit"]
            text, student_name = context["text"], context["student_name"]
            conn.execute("UPDATE essays SET status='grading',started_at=? WHERE id=?",
                         (started_at, job["essay_id"]))
            if job["submission_version_id"]:
                conn.execute("""
                    UPDATE submission_versions SET status='grading'
                    WHERE id=? AND essay_id=? AND content_hash=?
                """, (job["submission_version_id"], job["essay_id"], job["content_hash"]))
        else:
            title, prompt = job["practice_title"], job["practice_prompt"]
            criteria, word_limit = job["practice_criteria"], job["practice_word_limit"]
            text, student_name = job["practice_text"], ""
        changed = conn.execute("""
            UPDATE grading_jobs SET status='running',started_at=?
            WHERE id=? AND status='queued'
        """, (started_at, job_id))
        if changed.rowcount != 1:
            return None
        return {
            "jobId": job_id, "submissionId": job["submission_id"],
            "targetType": job["target_type"], "assignmentId": job["assignment_id"],
            "essayId": job["essay_id"], "username": job["username"],
            "contentHash": job["content_hash"], "title": title, "prompt": prompt,
            "criteria": criteria, "wordLimit": word_limit, "text": text,
            "studentName": student_name,
        }


def update_grading_attempt(job_id: str, attempt: int):
    with connection(write=True) as conn:
        job = conn.execute("SELECT target_type,essay_id FROM grading_jobs WHERE id=?",
                           (job_id,)).fetchone()
        if not job:
            return
        conn.execute("UPDATE grading_jobs SET attempt=? WHERE id=?", (attempt, job_id))
        if job["target_type"] == "assignment" and job["essay_id"]:
            conn.execute("UPDATE essays SET attempt=? WHERE id=? AND job_id=?",
                         (attempt, job["essay_id"], job_id))


def finish_grading_job_success(job_id: str, result: dict, *, duration_seconds: float,
                               history_limit: int = 30, replacement_student_name: str = ""):
    finished_at = _utc_iso()
    payload = _dump(result) or "{}"
    with connection(write=True) as conn:
        job = conn.execute("SELECT * FROM grading_jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["status"] != "running":
            return {"written": False}
        if job["target_type"] == "assignment":
            essay = conn.execute("SELECT * FROM essays WHERE id=?", (job["essay_id"],)).fetchone()
            if (not essay or essay["job_id"] != job_id
                    or _text_hash(essay["text"]) != job["content_hash"]):
                conn.execute("""
                    UPDATE grading_jobs SET status='error',finished_at=?,duration_seconds=?,
                        error='正文已变化，已丢弃旧批改结果',error_type='content_changed',
                        error_advice='请重新提交批改。' WHERE id=?
                """, (finished_at, duration_seconds, job_id))
                if essay and essay["job_id"] == job_id:
                    conn.execute("""
                        UPDATE essays SET status='pending',job_id='',error=?,
                            error_type='正文已变化',error_advice=?,finished_at=?,duration_seconds=?
                        WHERE id=?
                    """, ("正文已变化，已丢弃旧批改结果", "请重新开始批改。",
                          finished_at, duration_seconds, essay["id"]))
                return {"written": False, "stale": True}
            conn.execute("""
                UPDATE essays SET status='done',score=?,level=?,feedback_json=?,
                    ai_feedback_json=?,feedback_version=feedback_version+1,
                    feedback_edited_by='',feedback_edited_at='',error='',
                    error_type='',error_advice='',graded_at=?,finished_at=?,duration_seconds=?,
                    result_status='pending_review',student_name=CASE WHEN ?<>'' THEN ? ELSE student_name END
                WHERE id=? AND job_id=?
            """, (result.get("score"), result.get("level", ""), payload, payload, finished_at,
                  finished_at, duration_seconds, replacement_student_name,
                  replacement_student_name, essay["id"], job_id))
            if job["submission_version_id"]:
                conn.execute("""
                    UPDATE submission_versions SET status='pending_review',score=?,level=?,
                        feedback_json=?,job_id=? WHERE id=? AND content_hash=?
                """, (result.get("score"), result.get("level", ""), payload, job_id,
                      job["submission_version_id"], job["content_hash"]))
            history_id = None
        else:
            history_id = "h" + secrets.token_hex(6)
            title = job["practice_title"] or "自主练习"
            conn.execute("""
                INSERT INTO history(id,username,title,prompt,criteria,word_limit,text,
                                    result_json,graded_at,visible_at,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (history_id, job["username"], title, job["practice_prompt"],
                  job["practice_criteria"], job["practice_word_limit"],
                  job["practice_text"], payload, finished_at, finished_at, finished_at))
        conn.execute("""
            UPDATE grading_jobs SET status='done',finished_at=?,duration_seconds=?,
                result_json=?,history_id=?,error='',error_type='',error_advice=''
            WHERE id=?
        """, (finished_at, duration_seconds, payload, history_id, job_id))
        return {"written": True, "historyId": history_id,
                "targetType": job["target_type"]}


def finish_grading_job_error(job_id: str, error: str, error_type: str, error_advice: str,
                             *, duration_seconds: float):
    finished_at = _utc_iso()
    with connection(write=True) as conn:
        job = conn.execute("SELECT * FROM grading_jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["status"] not in ("running", "queued"):
            return False
        conn.execute("""
            UPDATE grading_jobs SET status='error',finished_at=?,duration_seconds=?,
                error=?,error_type=?,error_advice=? WHERE id=?
        """, (finished_at, duration_seconds, error, error_type, error_advice, job_id))
        if job["target_type"] == "assignment" and job["essay_id"]:
            conn.execute("""
                UPDATE essays SET status='error',finished_at=?,duration_seconds=?,error=?,
                    error_type=?,error_advice=? WHERE id=? AND job_id=?
            """, (finished_at, duration_seconds, error, error_type, error_advice,
                  job["essay_id"], job_id))
            if job["submission_version_id"]:
                conn.execute("UPDATE submission_versions SET status='error',job_id=? WHERE id=?",
                             (job_id, job["submission_version_id"]))
        return True


def recover_grading_jobs():
    with connection(write=True) as conn:
        conn.execute("UPDATE grading_jobs SET status='queued',started_at='' WHERE status='running'")
        conn.execute("""
            UPDATE essays SET status='queued',started_at=''
            WHERE job_id IN (SELECT id FROM grading_jobs WHERE status='queued')
        """)
        conn.execute("""
            UPDATE essays SET status='pending',job_id='',started_at=''
            WHERE status IN ('queued','grading')
              AND NOT EXISTS (
                  SELECT 1 FROM grading_jobs j
                  WHERE j.id=essays.job_id AND j.status IN ('queued','running'))
        """)
        return [row[0] for row in conn.execute(
            "SELECT id FROM grading_jobs WHERE status='queued' ORDER BY queued_at,id").fetchall()]


def get_grading_job(job_id: str):
    with connection() as conn:
        row = conn.execute("SELECT * FROM grading_jobs WHERE id=?", (job_id,)).fetchone()
        essay_result_status = ""
        published_history_id = ""
        if row and row["target_type"] == "assignment" and row["essay_id"]:
            essay = conn.execute(
                "SELECT result_status FROM essays WHERE id=?", (row["essay_id"],)).fetchone()
            essay_result_status = essay["result_status"] if essay else ""
            history = conn.execute("""
                SELECT id FROM history WHERE source_essay_id=?
                ORDER BY visible_at DESC,id DESC LIMIT 1
            """, (row["essay_id"],)).fetchone()
            published_history_id = history["id"] if history else ""
    if not row:
        return None
    return {
        "jobId": row["id"], "submissionId": row["submission_id"],
        "targetType": row["target_type"], "assignmentId": row["assignment_id"] or "",
        "essayId": row["essay_id"] or "", "username": row["username"] or "",
        "status": row["status"], "attempt": row["attempt"],
        "queuedAt": row["queued_at"], "startedAt": row["started_at"],
        "finishedAt": row["finished_at"], "durationSeconds": row["duration_seconds"],
        "error": row["error"], "errorType": row["error_type"],
        "errorAdvice": row["error_advice"], "historyId": row["history_id"] or "",
        "result": _json(row["result_json"]),
        "resultStatus": essay_result_status,
        "publishedHistoryId": published_history_id,
    }


def count_running_jobs():
    with connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM grading_jobs WHERE status='running'").fetchone()[0]
