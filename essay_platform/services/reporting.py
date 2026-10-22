"""Reusable reporting domain rules."""

import os
import threading
from essay_platform.adapters.ai import manual_grade_of, to_int
from essay_platform.services.directory import class_label
from essay_platform.adapters.ai import request_upgrade_text
from essay_platform.services.operations import ai_quota_error
from essay_platform.repositories import analytics as analytics_repository

def delayed_cleanup(path):
    def clean():
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    timer = threading.Timer(120, clean)
    timer.daemon = True
    timer.start()

def report_grade(feedback):
    feedback = feedback or {}
    explicit = str(feedback.get("level") or "").strip()
    if explicit in ("优", "良", "中", "差"):
        return explicit
    score = to_int(feedback.get("score"))
    return manual_grade_of(score if score is not None else 0)

def assignment_report_item(assignment, essay, users):
    feedback = essay.get("feedback") or {}
    user = users.get(essay.get("submittedBy"), {})
    return {
        "studentName": essay.get("studentName") or user.get("display_name") or "未关联",
        "studentNo": user.get("studentNo") or "",
        "className": class_label(assignment.get("classId", "")),
        "title": essay.get("studentTitle") or assignment.get("title") or "作文",
        "text": essay.get("text", ""),
        "grade": report_grade(feedback),
        "feedback": feedback,
    }
