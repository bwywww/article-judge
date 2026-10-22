"""Class-visible excellent essays with non-cumulative flowers."""

from __future__ import annotations

from essay_platform.repositories import showcase as repository


class ShowcaseError(ValueError):
    def __init__(self, message: str, *, code: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def set_showcase(assignment_id: str, teacher: str, essay_ids, confirm: bool):
    if not isinstance(essay_ids, list) or len(essay_ids) > 20:
        raise ShowcaseError("优秀作文名单格式不正确", code="invalid_items")
    result = repository.set_showcase(
        assignment_id, teacher, [str(item) for item in essay_ids], bool(confirm))
    if not result.get("ok"):
        messages = {
            "not_found": ("作业不存在或无权操作", 404),
            "archived": ("历史作业已归档，优秀作文名单已冻结", 409),
            "confirmation_required": ("修改已保存的优秀作文名单需要确认", 409),
            "invalid_essay": ("名单包含不可选的作文", 400),
        }
        message, status = messages.get(result.get("reason"), ("无法保存优秀作文", 400))
        raise ShowcaseError(message, code=result.get("reason", "save_failed"), status=status)
    return result


def list_showcase(assignment_id: str, username: str, role: str):
    result = repository.list_showcase(assignment_id, username, role)
    if result is None:
        raise ShowcaseError("作业不存在或无权查看", code="not_found", status=404)
    return result
