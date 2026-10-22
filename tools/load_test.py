"""Isolated 1000-student capacity smoke test with a fake AI backend."""

from __future__ import annotations

import tempfile
import threading
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from regression_test import (
    aiapp,
    get_json,
    login,
    make_student_workbook,
    post_file,
    post_json,
    reset_runtime,
    wait_for,
)
from essay_platform.adapters import ai as ai_adapter


STUDENT_COUNT = 1000
CLASS_COUNT = 30
SUBMISSION_SAMPLE = 8


def build_rows():
    grade_names = "一二三四五六"
    rows = []
    class_students = {}
    for index in range(STUDENT_COUNT):
        class_index = index % CLASS_COUNT
        grade_number = class_index // 5 + 1
        class_number = class_index % 5 + 1
        seat_number = index // CLASS_COUNT + 1
        student_no = f"2025{grade_number:02d}{class_number:02d}{seat_number:02d}"
        grade = f"{grade_names[grade_number - 1]}年级"
        klass = f"{class_number}班"
        rows.append([grade, klass, student_no, f"容量学生{index + 1:04d}", aiapp.DEFAULT_TEMP_PASSWORD])
        class_students.setdefault((grade, klass), []).append(student_no)
    return rows, class_students


def main():
    started_at = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        reset_runtime(tmp)
        client = aiapp.app.test_client()
        admin = login(client, "admin")
        teacher = login(client, "mr")

        rows, class_students = build_rows()
        workbook = make_student_workbook(rows)
        import_started_at = time.perf_counter()
        preview_response = post_file(
            client, "/api/admin/students/import/preview", "file",
            workbook, "load-test-1000.xlsx", admin,
        )
        assert preview_response.status_code == 200, preview_response.get_data(as_text=True)
        preview = preview_response.get_json()
        assert preview["summary"]["new"] == STUDENT_COUNT
        assert preview["summary"]["createClasses"] == CLASS_COUNT
        assert preview["summary"]["error"] == 0
        confirm_response = post_json(
            client, "/api/admin/students/import/confirm", {"token": preview["token"]}, admin,
        )
        assert confirm_response.status_code == 200, confirm_response.get_data(as_text=True)
        import_duration = time.perf_counter() - import_started_at
        assert import_duration <= 120, f"1000-student import took {import_duration:.2f}s"
        with aiapp.db.connection() as conn:
            password_counts = conn.execute("""
                SELECT COUNT(*) AS total,COUNT(DISTINCT password_hash) AS unique_hashes,
                       SUM(CASE WHEN must_change_password=1 THEN 1 ELSE 0 END) AS must_change
                FROM users WHERE role='student' AND username<>'stu'
            """).fetchone()
        assert password_counts["total"] == STUDENT_COUNT
        assert password_counts["unique_hashes"] == STUDENT_COUNT
        assert password_counts["must_change"] == STUDENT_COUNT

        classes = get_json(client, "/api/classes", admin).get_json()["classes"]
        imported_classes = [
            item for item in classes
            if item.get("grade") in {f"{name}年级" for name in "一二三四五六"}
        ]
        assert len(imported_classes) == CLASS_COUNT
        assert sum(item["studentCount"] for item in imported_classes) == STUDENT_COUNT
        paged = get_json(
            client, "/api/admin/users?role=student&page=1&pageSize=100", admin,
        ).get_json()
        assert paged["total"] == STUDENT_COUNT + 1
        assert len(paged["users"]) == 100

        target_class = next(item for item in imported_classes
                            if item["grade"] == "一年级" and item["klass"] == "1班")
        assert post_json(client, f"/api/classes/{target_class['id']}/teachers", {
            "username": "mr",
        }, admin).status_code == 200
        assignment = post_json(client, "/api/assignments", {
            "title": "容量测试作业", "prompt": "写一篇测试作文", "classId": target_class["id"],
        }, teacher)
        assert assignment.status_code == 200, assignment.get_data(as_text=True)
        assignment_id = assignment.get_json()["id"]
        assert post_json(
            client, f"/api/assignments/{assignment_id}/publish", {}, teacher,
        ).status_code == 200

        lock = threading.Lock()
        release = threading.Event()
        active = 0
        max_active = 0

        def blocking_ai(messages, max_tokens=4096, temperature=0.6):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            release.wait(8)
            with lock:
                active -= 1
            return '{"score":88,"level":"良好","summary":"容量测试",' \
                   '"dimensions":{},"strengths":[],"weaknesses":[],"suggestions":[]}'

        ai_adapter.call_llm = blocking_ai
        sample_usernames = class_students[("一年级", "1班")][:SUBMISSION_SAMPLE]
        job_ids = []
        for username in sample_usernames:
            student_token = login(client, username, aiapp.DEFAULT_TEMP_PASSWORD)
            response = post_json(client, "/api/student/grade", {
                "assignmentId": assignment_id,
                "text": f"这是{username}用于容量测试的作文正文，内容长度足够。",
            }, student_token)
            assert response.status_code == 202, response.get_data(as_text=True)
            job_ids.append(response.get_json()["jobId"])
        assert wait_for(lambda: max_active == aiapp.GRADE_CONCURRENCY, timeout=5)
        assert max_active == 4
        release.set()
        assert wait_for(
            lambda: all(aiapp.db.get_grading_job(job_id)["status"] == "done"
                        for job_id in job_ids),
            timeout=10,
        )

    duration = time.perf_counter() - started_at
    print(
        f"load_test.py: OK ({STUDENT_COUNT} students, {CLASS_COUNT} classes, "
        f"max grading concurrency {max_active}, {duration:.2f}s)"
    )


if __name__ == "__main__":
    main()
