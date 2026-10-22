"""Immutable history records and separately stored derived artifacts."""

from __future__ import annotations

import secrets

from essay_platform.persistence.storage import connection, _dump, _json, _utc_iso


def get_history_artifact(history_id: str, username: str, kind: str, source_hash: str):
    with connection() as conn:
        row = conn.execute("""
            SELECT ha.content_json FROM history_artifacts ha
            JOIN history h ON h.id=ha.history_id
            WHERE ha.history_id=? AND h.username=? AND ha.kind=? AND ha.source_hash=?
            ORDER BY ha.created_at DESC LIMIT 1
        """, (history_id, username, kind, source_hash)).fetchone()
    return _json(row[0], {}) if row else None


def save_history_artifact(history_id: str, username: str, kind: str,
                          source_hash: str, content: dict, *, model: str = ""):
    artifact_id = "ha" + secrets.token_hex(12)
    now = _utc_iso()
    with connection(write=True) as conn:
        history = conn.execute(
            "SELECT id,text FROM history WHERE id=? AND username=?", (history_id, username)
        ).fetchone()
        if not history:
            return {"ok": False, "reason": "not_found"}
        import hashlib
        current_hash = hashlib.sha256((history["text"] or "").encode("utf-8")).hexdigest()
        if current_hash != source_hash:
            return {"ok": False, "reason": "content_changed"}
        conn.execute("""
            INSERT INTO history_artifacts(
                id,history_id,kind,source_hash,content_json,model,created_by,created_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(history_id,kind,source_hash) DO UPDATE SET
                content_json=excluded.content_json,model=excluded.model,
                created_by=excluded.created_by,created_at=excluded.created_at
        """, (artifact_id, history_id, kind, source_hash, _dump(content), model,
              username, now))
    return {"ok": True, "artifactId": artifact_id}


def history_artifacts_for(username: str, history_ids: list[str]):
    ids = list(dict.fromkeys(history_ids))
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    with connection() as conn:
        rows = conn.execute(f"""
            SELECT ha.history_id,ha.kind,ha.content_json FROM history_artifacts ha
            JOIN history h ON h.id=ha.history_id
            WHERE h.username=? AND ha.history_id IN ({marks})
            ORDER BY ha.created_at
        """, (username, *ids)).fetchall()
    result = {}
    for row in rows:
        result.setdefault(row["history_id"], {})[row["kind"]] = _json(row["content_json"], {})
    return result
