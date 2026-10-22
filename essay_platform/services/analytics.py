"""Score trends, teacher-only ranking, and class insight summaries."""

from __future__ import annotations

import json

from essay_platform.repositories import analytics as repository


INSIGHT_FIELDS = ("overall", "commonStrengths", "commonProblems", "teachingSuggestions")


class AnalyticsError(ValueError):
    def __init__(self, message: str, *, code: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def student_trends(username: str):
    return repository.student_trends(username)


def teacher_student_trend(teacher: str, student: str):
    result = repository.teacher_student_trend(teacher, student)
    if result is None:
        raise AnalyticsError("学生不存在", code="not_found", status=404)
    return result


def assignment_ranking(assignment_id: str, teacher: str):
    result = repository.assignment_ranking(assignment_id, teacher)
    if result is None:
        raise AnalyticsError("作业不存在或无权查看", code="not_found", status=404)
    return result


def get_insight(assignment_id: str, teacher: str):
    return repository.get_insight(assignment_id, teacher)


def _validate_insight(value):
    if not isinstance(value, dict):
        raise AnalyticsError("共性问题结果格式不正确", code="invalid_result")
    cleaned = {}
    for field in INSIGHT_FIELDS:
        text = str(value.get(field, "")).strip()
        if not text:
            raise AnalyticsError("共性问题内容不完整", code="invalid_result")
        if len(text) > 3000:
            raise AnalyticsError("共性问题内容过长", code="invalid_result")
        cleaned[field] = text
    return cleaned


def generate_insight(assignment_id: str, teacher: str, call_ai, *, confirm: bool = False):
    existing = repository.get_insight(assignment_id, teacher)
    if existing and not confirm:
        raise AnalyticsError("重新生成会替换当前人工修改，请先确认", code="confirmation_required",
                             status=409)
    source = repository.insight_source(assignment_id, teacher)
    if source is None:
        raise AnalyticsError("作业不存在或无权查看", code="not_found", status=404)
    if not source["feedback"]:
        raise AnalyticsError("还没有可汇总的批改结果", code="no_feedback", status=409)
    messages = [
        {"role": "system", "content": (
            "你是小学语文教研助手。只根据结构化批改意见总结班级共性，不推测学生身份，"
            "不得要求或复述作文原文。必须返回 JSON 对象，字段为 overall、commonStrengths、"
            "commonProblems、teachingSuggestions，字段值均为简洁中文字符串。")},
        {"role": "user", "content": json.dumps({
            "assignmentTitle": source["assignment"]["title"],
            "feedback": source["feedback"],
        }, ensure_ascii=False)},
    ]
    try:
        raw = call_ai(messages, max_tokens=1800, temperature=0.2)
        value = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        raise AnalyticsError(f"班级共性问题生成失败：{exc}", code="ai_failed", status=502) from exc
    insight = _validate_insight(value)
    return repository.save_generated_insight(
        assignment_id, teacher, insight, source["sourceHash"])


def update_insight(assignment_id: str, teacher: str, insight, version: int):
    cleaned = _validate_insight(insight)
    result = repository.update_insight(
        assignment_id, teacher, cleaned, int(version or 0))
    if not result.get("ok"):
        reason = result.get("reason")
        raise AnalyticsError(
            "内容已被其他页面修改，请刷新后重试" if reason == "conflict" else "共性问题不存在",
            code=reason, status=409 if reason == "conflict" else 404)
    return result["record"]
