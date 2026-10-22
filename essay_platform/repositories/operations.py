"""SQLite persistence for the operations domain."""

from __future__ import annotations

from essay_platform.persistence.storage import connection, _dump, _json, _utc_iso

def get_app_settings(keys: list[str]):
    if not keys:
        return {}
    marks = ",".join("?" for _ in keys)
    with connection() as conn:
        values = {row["key"]: row["value"] for row in conn.execute(
            f"SELECT key,value FROM app_config WHERE key IN ({marks})", keys).fetchall()}
    return {key: values.get(key, "") for key in keys}


def set_app_settings(values: dict[str, str]):
    with connection(write=True) as conn:
        for key, value in values.items():
            conn.execute("""
                INSERT INTO app_config(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (key, str(value)))


def ai_token_totals(day_start: str, month_start: str):
    with connection() as conn:
        day = conn.execute("""
            SELECT COALESCE(SUM(prompt_tokens+completion_tokens),0)
            FROM ai_usage WHERE created_at>=?
        """, (day_start,)).fetchone()[0]
        month = conn.execute("""
            SELECT COALESCE(SUM(prompt_tokens+completion_tokens),0)
            FROM ai_usage WHERE created_at>=?
        """, (month_start,)).fetchone()[0]
        return {"dailyTokens": int(day or 0), "monthlyTokens": int(month or 0)}

def write_audit(event_type: str, *, actor_username: str | None = None,
                target_type: str = "", target_id: str = "", ip_address: str = "",
                detail: dict | None = None):
    with connection(write=True) as conn:
        conn.execute("""
            INSERT INTO audit_logs(created_at,actor_username,event_type,target_type,target_id,
                                   ip_address,detail_json)
            VALUES(?,?,?,?,?,?,?)
        """, (_utc_iso(), actor_username, event_type, target_type, target_id,
              (ip_address or "")[:128], _dump(detail or {}) or "{}"))


def query_audit(*, query: str = "", event_type: str = "", page: int = 1,
                page_size: int = 30):
    clauses = []
    params = []
    if event_type:
        clauses.append("event_type=?")
        params.append(event_type)
    if query:
        clauses.append("(actor_username LIKE ? OR event_type LIKE ? OR target_type LIKE ? "
                       "OR target_id LIKE ? OR detail_json LIKE ?)")
        needle = f"%{query}%"
        params.extend([needle] * 5)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    offset = (page - 1) * page_size
    with connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM audit_logs" + where, params).fetchone()[0]
        rows = conn.execute("""
            SELECT id,created_at,actor_username,event_type,target_type,target_id,
                   ip_address,detail_json
            FROM audit_logs
        """ + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
                            [*params, page_size, offset]).fetchall()
    items = [{
        "id": row["id"], "createdAt": row["created_at"],
        "actorUsername": row["actor_username"] or "",
        "type": row["event_type"], "targetType": row["target_type"],
        "targetId": row["target_id"], "ipAddress": row["ip_address"],
        "detail": _json(row["detail_json"], {}),
    } for row in rows]
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def ensure_feature_modules(defaults: list[dict]):
    now = _utc_iso()
    with connection(write=True) as conn:
        for item in defaults:
            conn.execute(
                "INSERT OR IGNORE INTO feature_modules(module_key,status,updated_at) "
                "VALUES(?,? ,?)",
                (item["key"], item.get("status", "enabled"), now),
            )


def list_feature_modules():
    with connection() as conn:
        rows = conn.execute(
            "SELECT module_key,status,updated_by,updated_at "
            "FROM feature_modules ORDER BY module_key"
        ).fetchall()
    return [{
        "key": row["module_key"], "status": row["status"],
        "updatedBy": row["updated_by"], "updatedAt": row["updated_at"],
    } for row in rows]


def get_feature_module(module_key: str):
    with connection() as conn:
        row = conn.execute(
            "SELECT module_key,status,updated_by,updated_at "
            "FROM feature_modules WHERE module_key=?", (module_key,),
        ).fetchone()
    if not row:
        return None
    return {
        "key": row["module_key"], "status": row["status"],
        "updatedBy": row["updated_by"], "updatedAt": row["updated_at"],
    }


def set_feature_module_state(module_key: str, status: str, username: str = ""):
    if status not in ("enabled", "draining", "disabled"):
        raise ValueError("无效的模块状态")
    with connection(write=True) as conn:
        conn.execute(
            "INSERT INTO feature_modules(module_key,status,updated_by,updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(module_key) DO UPDATE SET "
            "status=excluded.status,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
            (module_key, status, username, _utc_iso()),
        )
    return get_feature_module(module_key)


def compare_and_set_feature_module_state(module_key: str, expected_status: str,
                                         status: str, username: str = ""):
    """Atomically change a module state only while its persisted state is unchanged."""
    valid = {"enabled", "draining", "disabled"}
    if expected_status not in valid or status not in valid:
        raise ValueError("无效的模块状态")
    with connection(write=True) as conn:
        cursor = conn.execute(
            "UPDATE feature_modules SET status=?,updated_by=?,updated_at=? "
            "WHERE module_key=? AND status=?",
            (status, username, _utc_iso(), module_key, expected_status),
        )
        row = conn.execute(
            "SELECT module_key,status,updated_by,updated_at "
            "FROM feature_modules WHERE module_key=?", (module_key,),
        ).fetchone()
    record = None if not row else {
        "key": row["module_key"], "status": row["status"],
        "updatedBy": row["updated_by"], "updatedAt": row["updated_at"],
    }
    return record, cursor.rowcount == 1


def count_active_grading_jobs(target_type: str = "") -> int:
    where = "status IN ('queued','running')"
    params: list[str] = []
    if target_type:
        where += " AND target_type=?"
        params.append(target_type)
    with connection() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM grading_jobs WHERE " + where, params
        ).fetchone()[0])


def count_user_active_grading_jobs(username: str) -> int:
    """该用户当前 queued/running 的批改任务数（用于防单用户刷爆共享队列）。"""
    with connection() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM grading_jobs WHERE username=? "
            "AND status IN ('queued','running')", (username,)
        ).fetchone()[0])


def ensure_ai_task_routes(defaults: list[dict]):
    now = _utc_iso()
    with connection(write=True) as conn:
        for item in defaults:
            conn.execute("""
                INSERT OR IGNORE INTO ai_task_routes(
                    task_type,profile,model,thinking,json_mode,max_tokens,updated_at
                ) VALUES(?,?,?,?,?,?,?)
            """, (
                item["taskType"], item.get("profile", "article_judge"),
                item.get("model", "deepseek-v4-flash"),
                int(bool(item.get("thinking", False))),
                int(bool(item.get("jsonMode", True))),
                int(item.get("maxTokens", 2600)), now,
            ))


def list_ai_task_routes():
    with connection() as conn:
        rows = conn.execute("""
            SELECT task_type,profile,model,thinking,json_mode,max_tokens,
                   updated_by,updated_at
            FROM ai_task_routes ORDER BY task_type
        """).fetchall()
    return [{
        "taskType": row["task_type"], "profile": row["profile"],
        "model": row["model"], "thinking": bool(row["thinking"]),
        "jsonMode": bool(row["json_mode"]), "maxTokens": row["max_tokens"],
        "updatedBy": row["updated_by"], "updatedAt": row["updated_at"],
    } for row in rows]


def get_ai_task_route(task_type: str):
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM ai_task_routes WHERE task_type=?", (task_type,)
        ).fetchone()
    if not row:
        return None
    return {
        "taskType": row["task_type"], "profile": row["profile"],
        "model": row["model"], "thinking": bool(row["thinking"]),
        "jsonMode": bool(row["json_mode"]), "maxTokens": row["max_tokens"],
        "updatedBy": row["updated_by"], "updatedAt": row["updated_at"],
    }


def update_ai_task_route(task_type: str, *, profile: str, model: str,
                         thinking: bool, json_mode: bool, max_tokens: int,
                         username: str = ""):
    with connection(write=True) as conn:
        conn.execute("""
            INSERT INTO ai_task_routes(
                task_type,profile,model,thinking,json_mode,max_tokens,updated_by,updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(task_type) DO UPDATE SET
                profile=excluded.profile,model=excluded.model,
                thinking=excluded.thinking,json_mode=excluded.json_mode,
                max_tokens=excluded.max_tokens,updated_by=excluded.updated_by,
                updated_at=excluded.updated_at
        """, (
            task_type, profile, model, int(bool(thinking)), int(bool(json_mode)),
            int(max_tokens), username, _utc_iso(),
        ))
    return get_ai_task_route(task_type)


def write_ai_usage(*, profile: str, task_type: str, model: str,
                   actor_username: str = "", prompt_tokens: int = 0,
                   completion_tokens: int = 0, cache_hit_tokens: int = 0,
                   duration_seconds: float = 0, status: str = "ok",
                   error_type: str = ""):
    with connection(write=True) as conn:
        conn.execute("""
            INSERT INTO ai_usage(
                created_at,profile,task_type,model,actor_username,prompt_tokens,
                completion_tokens,cache_hit_tokens,duration_seconds,status,error_type
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (
            _utc_iso(), profile, task_type, model, actor_username,
            max(0, int(prompt_tokens or 0)), max(0, int(completion_tokens or 0)),
            max(0, int(cache_hit_tokens or 0)), max(0, float(duration_seconds or 0)),
            status, error_type,
        ))


def query_ai_usage(*, page: int = 1, page_size: int = 30, task_type: str = "",
                   profile: str = ""):
    clauses = []
    params = []
    if task_type:
        clauses.append("task_type=?")
        params.append(task_type)
    if profile:
        clauses.append("profile=?")
        params.append(profile)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    offset = (page - 1) * page_size
    with connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM ai_usage" + where, params).fetchone()[0]
        rows = conn.execute("""
            SELECT id,created_at,profile,task_type,model,actor_username,
                   prompt_tokens,completion_tokens,cache_hit_tokens,
                   duration_seconds,status,error_type
            FROM ai_usage
        """ + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
                            [*params, page_size, offset]).fetchall()
        totals = conn.execute("""
            SELECT COUNT(*) AS requests,
                   COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens),0) AS completion_tokens,
                   COALESCE(SUM(cache_hit_tokens),0) AS cache_hit_tokens,
                   COALESCE(SUM(CASE WHEN status<>'ok' THEN 1 ELSE 0 END),0) AS failures
            FROM ai_usage
        """ + where, params).fetchone()
    return {
        "items": [{
            "id": row["id"], "createdAt": row["created_at"],
            "profile": row["profile"], "taskType": row["task_type"],
            "model": row["model"], "actorUsername": row["actor_username"],
            "promptTokens": row["prompt_tokens"],
            "completionTokens": row["completion_tokens"],
            "cacheHitTokens": row["cache_hit_tokens"],
            "durationSeconds": row["duration_seconds"],
            "status": row["status"], "errorType": row["error_type"],
        } for row in rows],
        "total": int(total), "page": page, "pageSize": page_size,
        "summary": {
            "requests": int(totals["requests"]),
            "promptTokens": int(totals["prompt_tokens"]),
            "completionTokens": int(totals["completion_tokens"]),
            "cacheHitTokens": int(totals["cache_hit_tokens"]),
            "failures": int(totals["failures"]),
        },
    }
