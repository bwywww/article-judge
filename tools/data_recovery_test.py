"""Isolated startup database selection and SQLite backup migration tests."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from essay_platform.services.database_location import inspect_candidate, prepare_runtime_database


def make_candidate(path: Path, *, users: int, classes: int, assignments: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE users(id INTEGER PRIMARY KEY);
            CREATE TABLE classes(id INTEGER PRIMARY KEY);
            CREATE TABLE assignments(id INTEGER PRIMARY KEY);
            CREATE TABLE essays(id INTEGER PRIMARY KEY);
            CREATE TABLE history(id INTEGER PRIMARY KEY);
            CREATE TABLE grading_jobs(id INTEGER PRIMARY KEY);
            CREATE TABLE submission_versions(id INTEGER PRIMARY KEY);
        """)
        for table, count in (("users", users), ("classes", classes),
                             ("assignments", assignments)):
            conn.executemany(f"INSERT INTO {table} DEFAULT VALUES", [()] * count)
        conn.commit()
    finally:
        conn.close()


def expect_refusal(fn):
    try:
        fn()
    except RuntimeError:
        return
    raise AssertionError("startup selection should have refused")


def main():
    with tempfile.TemporaryDirectory(prefix="essay-location-") as tmp:
        root = Path(tmp)
        canonical_dir = root / "root-data"
        legacy_dir = root / "legacy-data"

        expect_refusal(lambda: prepare_runtime_database(
            canonical_dir, legacy_dir, allow_demo=False, explicit_data_dir=False))
        allowed = prepare_runtime_database(
            canonical_dir, legacy_dir, allow_demo=True, explicit_data_dir=True)
        assert allowed["created"] and not (canonical_dir / "app.db").exists()

        make_candidate(legacy_dir / "app.db", users=20, classes=3, assignments=2)
        recovered = prepare_runtime_database(
            canonical_dir, legacy_dir, allow_demo=False, explicit_data_dir=False)
        assert recovered["recovered"] and recovered["counts"]["users"] == 20
        assert inspect_candidate(legacy_dir / "app.db")["counts"]["assignments"] == 2

    with tempfile.TemporaryDirectory(prefix="essay-conflict-") as tmp:
        root = Path(tmp)
        canonical_dir, legacy_dir = root / "root", root / "legacy"
        make_candidate(canonical_dir / "app.db", users=3, classes=1, assignments=0)
        make_candidate(legacy_dir / "app.db", users=20, classes=3, assignments=2)
        expect_refusal(lambda: prepare_runtime_database(
            canonical_dir, legacy_dir, allow_demo=False, explicit_data_dir=False))

    with tempfile.TemporaryDirectory(prefix="essay-formal-wins-") as tmp:
        root = Path(tmp)
        canonical_dir, legacy_dir = root / "root", root / "legacy"
        make_candidate(canonical_dir / "app.db", users=20, classes=3, assignments=2)
        make_candidate(legacy_dir / "app.db", users=3, classes=1, assignments=0)
        selected = prepare_runtime_database(
            canonical_dir, legacy_dir, allow_demo=False, explicit_data_dir=False)
        assert not selected["recovered"] and selected["counts"]["assignments"] == 2

    print("data_recovery_test.py: OK")


if __name__ == "__main__":
    main()
