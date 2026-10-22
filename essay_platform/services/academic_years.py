"""Academic-year preview and transaction orchestration."""

from __future__ import annotations

from essay_platform.repositories import academic_years as repository


class AcademicYearError(ValueError):
    def __init__(self, message: str, *, code: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def preview(target_start_year=None):
    try:
        year = int(target_start_year) if target_start_year not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise AcademicYearError("目标学年年份无效", code="invalid_year") from exc
    result = repository.academic_rollover_preview(year)
    if result is None:
        raise AcademicYearError("当前没有活动学年", code="no_active_year", status=409)
    return result


def confirm(target_start_year, targets):
    try:
        year = int(target_start_year)
    except (TypeError, ValueError) as exc:
        raise AcademicYearError("目标学年年份无效", code="invalid_year") from exc
    if targets is not None and not isinstance(targets, list):
        raise AcademicYearError("班级调整方案格式无效", code="invalid_targets")
    result = repository.perform_academic_rollover(year, targets or [])
    if result.get("ok"):
        return result
    messages = {
        "blocked": "还有草稿、批改任务或待发布结果，暂不能结转学年",
        "invalid_year": "目标学年必须晚于当前学年",
        "year_exists": "目标学年已经存在",
        "invalid_source_class": "方案包含无效的原班级",
        "invalid_target_class": "新班级名称、年级和班号不能为空",
        "duplicate_student": "同一学生被分配到了多个新班级",
        "student_set_mismatch": "新班级学生名单不完整，请重新预检",
        "state_changed": "学年状态已变化，请重新预检",
    }
    reason = result.get("reason", "rollover_failed")
    raise AcademicYearError(messages.get(reason, "学年结转失败"), code=reason, status=409)
