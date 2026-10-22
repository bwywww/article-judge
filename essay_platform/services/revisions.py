"""Submission return and immutable revision-chain rules."""

from __future__ import annotations

import datetime as dt

from essay_platform.repositories import revisions as repository


class RevisionError(ValueError):
    def __init__(self, message: str, *, code: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


RETURN_ERRORS = {
    "not_found": ("作文不存在或不属于当前老师", 404),
    "archived": ("历史作业已归档，不能再退回", 409),
    "teacher_upload": ("老师导入并锁定的作文不能退回重交", 409),
    "in_progress": ("作文正在排队或批改，暂时不能退回", 409),
    "not_reviewable": ("作文尚未形成可审核结果", 409),
    "already_returned": ("这篇作文已经退回，正在等待学生重交", 409),
    "version_missing": ("作文版本记录缺失，请联系管理员检查数据", 409),
}


def normalize_deadline(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RevisionError("重交截止时间格式不正确", code="invalid_deadline") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    parsed = parsed.astimezone(dt.timezone.utc).replace(microsecond=0)
    now = dt.datetime.now(dt.timezone.utc)
    if parsed <= now:
        raise RevisionError("重交截止时间必须晚于当前时间", code="deadline_not_future")
    return parsed.isoformat().replace("+00:00", "Z")


def return_submission(assignment_id: str, essay_id: str, teacher: str,
                      comment: str, deadline: str = ""):
    comment = str(comment or "").strip()
    if not 1 <= len(comment) <= 1000:
        raise RevisionError("退回评语需为 1–1000 字", code="invalid_comment")
    deadline_at = normalize_deadline(deadline)
    result = repository.return_submission(
        assignment_id, essay_id, teacher, comment, deadline_at)
    if not result.get("ok"):
        message, status = RETURN_ERRORS.get(
            result.get("reason"), ("无法退回这篇作文", 409))
        raise RevisionError(message, code=result.get("reason", "return_failed"), status=status)
    return result


def cancel_return(assignment_id: str, essay_id: str, teacher: str):
    result = repository.cancel_return(assignment_id, essay_id, teacher)
    if not result.get("ok"):
        reason = result.get("reason", "cancel_failed")
        messages = {
            "not_found": ("没有可撤销的退回记录", 404),
            "already_resubmitted": ("学生已经重新提交，不能撤销退回", 409),
        }
        message, status = messages.get(reason, ("无法撤销退回", 409))
        raise RevisionError(message, code=reason, status=status)
    return result


def timeline(assignment_id: str, essay_id: str, username: str, role: str):
    result = repository.list_submission_timeline(
        assignment_id, essay_id, username, role)
    if result is None:
        raise RevisionError("作文不存在或无权查看", code="not_found", status=404)
    return result
