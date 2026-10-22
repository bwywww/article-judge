"""Select and, when safe, recover the canonical runtime database."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


BUSINESS_TABLES = (
    "users", "classes", "assignments", "essays", "history",
    "grading_jobs", "submission_versions",
)


def inspect_candidate(path: str | Path) -> dict:
    candidate = Path(path).resolve()
    if not candidate.is_file():
        return {"exists": False, "path": str(candidate), "counts": {}}
    conn = sqlite3.connect("file:" + candidate.as_posix() + "?mode=ro", uri=True)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {
            table: (int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    if table in tables else 0)
            for table in BUSINESS_TABLES
        }
        return {
            "exists": True,
            "path": str(candidate),
            "bytes": candidate.stat().st_size,
            "userVersion": int(conn.execute("PRAGMA user_version").fetchone()[0] or 0),
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "foreignKeyViolations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
            "counts": counts,
        }
    finally:
        conn.close()


def _business_score(info: dict) -> int:
    counts = info.get("counts") or {}
    return sum(int(counts.get(key, 0)) for key in (
        "assignments", "essays", "history", "grading_jobs", "submission_versions"))


def _is_demo_only(info: dict) -> bool:
    counts = info.get("counts") or {}
    return (_business_score(info) == 0
            and int(counts.get("users", 0)) <= 3
            and int(counts.get("classes", 0)) <= 1)


def _backup_copy(source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(str(destination) + ".recovering")
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(temp) + suffix).unlink()
        except FileNotFoundError:
            pass
    source_conn = sqlite3.connect("file:" + source.resolve().as_posix() + "?mode=ro", uri=True)
    target_conn = sqlite3.connect(temp)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    check = inspect_candidate(temp)
    if check.get("integrity") != "ok" or check.get("foreignKeyViolations"):
        temp.unlink(missing_ok=True)
        raise RuntimeError("旧数据库恢复副本未通过完整性检查")
    os.replace(temp, destination)


def prepare_runtime_database(data_dir: str, legacy_data_dir: str, *,
                             allow_demo: bool, explicit_data_dir: bool) -> dict:
    """Fail closed instead of silently booting a demo DB at the wrong path."""
    canonical = Path(data_dir).resolve() / "app.db"
    legacy = Path(legacy_data_dir).resolve() / "app.db"
    canonical_info = inspect_candidate(canonical)
    legacy_info = inspect_candidate(legacy)

    if canonical_info.get("exists"):
        if canonical_info.get("integrity") != "ok" or canonical_info.get("foreignKeyViolations"):
            raise RuntimeError(f"正式数据库完整性异常：{canonical}")
        candidates_conflict = (
            not explicit_data_dir and legacy_info.get("exists")
            and canonical_info.get("counts") != legacy_info.get("counts")
            and not (_is_demo_only(legacy_info)
                     and _business_score(canonical_info) > _business_score(legacy_info))
        )
        if candidates_conflict:
            raise RuntimeError(
                "检测到正式路径与旧路径数据库内容不同，已拒绝启动；"
                f"正式路径={canonical} counts={canonical_info.get('counts', {})}；"
                f"旧路径={legacy} counts={legacy_info.get('counts', {})}"
            )
        return {"path": str(canonical), "created": False, "recovered": False,
                "counts": canonical_info.get("counts", {})}

    if not explicit_data_dir and legacy_info.get("exists"):
        if legacy_info.get("integrity") != "ok" or legacy_info.get("foreignKeyViolations"):
            raise RuntimeError(f"旧数据库完整性异常，不能自动恢复：{legacy}")
        _backup_copy(legacy, canonical)
        restored = inspect_candidate(canonical)
        return {"path": str(canonical), "created": False, "recovered": True,
                "source": str(legacy), "counts": restored.get("counts", {})}

    if allow_demo:
        return {"path": str(canonical), "created": True, "recovered": False, "counts": {}}
    raise RuntimeError(
        "未找到正式数据库，已拒绝创建空白演示库。"
        "如确需初始化演示数据，请设置 ESSAY_BOOTSTRAP_DEMO=1。"
        f"目标路径={canonical}"
    )
