"""Feature module registry and safe runtime state transitions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import wraps

from flask import jsonify

import database as db


@dataclass(frozen=True)
class ModuleDefinition:
    key: str
    name: str
    description: str
    dependencies: tuple[str, ...] = ()
    toggleable: bool = True
    implemented: bool = True
    drain_target_type: str = ""
    drain_all_jobs: bool = False


CORE_MODULES = (
    ModuleDefinition("auth", "登录与会话", "登录、密码、会话和限流", toggleable=False),
    ModuleDefinition("permissions", "权限控制", "权限和数据范围校验", toggleable=False),
    ModuleDefinition("storage", "数据存储", "SQLite、文件和事务", toggleable=False),
    ModuleDefinition("audit", "审计日志", "关键操作留痕", toggleable=False),
    ModuleDefinition("scheduler", "任务调度", "持久化任务恢复和工作线程", toggleable=False),
    ModuleDefinition("module_registry", "模块注册", "模块状态和依赖管理", toggleable=False),
    ModuleDefinition("recovery", "备份与恢复", "备份、恢复预检和维护状态", toggleable=False),
)

BUSINESS_MODULES = (
    ModuleDefinition("directory", "账号与班级", "老师、学生、年级和班级管理"),
    ModuleDefinition("imports", "名单与文件导入", "Excel名单及PDF、Word作文导入",
                     dependencies=("directory", "assignments")),
    ModuleDefinition("assignments", "作业生命周期", "创建、发布、关闭和归档作业",
                     drain_target_type="assignment"),
    ModuleDefinition("grading", "AI批改", "统一批改队列和结果生成",
                     dependencies=("assignments",), drain_all_jobs=True),
    ModuleDefinition("review", "审核与发布", "老师审核、重批和发布结果",
                     dependencies=("assignments", "grading")),
    ModuleDefinition("practice", "自主练习", "学生独立练习和即时结果",
                     dependencies=("grading",), drain_target_type="practice"),
    ModuleDefinition("reporting", "历史与报告", "历史记录、PDF报告和导出"),
    ModuleDefinition("academic_years", "学年与档案", "学年归档、重新分班和历史班级",
                     dependencies=("directory", "assignments", "reporting")),
    ModuleDefinition("revisions", "退回与重交", "作文版本、退回评语和单人重交",
                     dependencies=("assignments", "grading", "review")),
    ModuleDefinition("analytics", "成绩与班级分析", "成绩趋势、班级排名和共性问题",
                     dependencies=("assignments", "reporting")),
    ModuleDefinition("showcase", "优秀作文", "班级优秀作文和本次小红花",
                     dependencies=("assignments", "review")),
    ModuleDefinition("ocr", "OCR 扫描识别", "扫描版 PDF 和图片作文识别（ecnu-plus）",
                     dependencies=("imports",), drain_target_type="ocr"),
    ModuleDefinition("operations", "运维管理", "AI设置、备份、恢复和运行状态"),
)

DEFINITIONS = {item.key: item for item in (*CORE_MODULES, *BUSINESS_MODULES)}


def persisted_defaults():
    return [{"key": item.key, "status": "enabled" if item.implemented else "disabled"}
            for item in BUSINESS_MODULES]


def ensure_registry():
    db.ensure_feature_modules(persisted_defaults())


def definition(module_key: str) -> ModuleDefinition | None:
    return DEFINITIONS.get(module_key)


def active_job_count(module_key: str) -> int:
    item = definition(module_key)
    if not item or not item.toggleable:
        return 0
    if item.drain_all_jobs:
        return db.count_active_grading_jobs()
    if item.drain_target_type == "ocr":
        return db.count_active_ocr_jobs()
    if item.drain_target_type:
        return db.count_active_grading_jobs(item.drain_target_type)
    return 0


def state(module_key: str) -> str:
    item = definition(module_key)
    if not item:
        return "disabled"
    if not item.implemented:
        return "disabled"
    if not item.toggleable:
        return "enabled"
    saved = db.get_feature_module(module_key)
    return saved["status"] if saved else "enabled"


def list_modules():
    saved = {item["key"]: item for item in db.list_feature_modules()}
    result = []
    for item in (*CORE_MODULES, *BUSINESS_MODULES):
        record = saved.get(item.key, {})
        public = asdict(item)
        public.update({
            "dependencies": list(item.dependencies),
            "status": ("disabled" if not item.implemented else
                       "enabled" if not item.toggleable else record.get("status", "enabled")),
            "activeJobs": active_job_count(item.key),
            "updatedBy": record.get("updatedBy", ""),
            "updatedAt": record.get("updatedAt", ""),
        })
        public.pop("drain_target_type", None)
        public.pop("drain_all_jobs", None)
        result.append(public)
    return result


def refresh_draining_modules():
    changed = []
    for record in db.list_feature_modules():
        if record["status"] == "draining" and active_job_count(record["key"]) == 0:
            current, updated = db.compare_and_set_feature_module_state(
                record["key"], "draining", "disabled", record.get("updatedBy", ""))
            if updated:
                changed.append(current)
    return changed


def transition(module_key: str, target: str, username: str):
    item = definition(module_key)
    if not item:
        raise KeyError("模块不存在")
    if not item.toggleable:
        raise ValueError("核心模块不可停用")
    if not item.implemented:
        raise ValueError("该模块尚未实现，不能启用")
    if target not in ("enabled", "disabled"):
        raise ValueError("目标状态只能是 enabled 或 disabled")

    if target == "enabled":
        missing = [key for key in item.dependencies if state(key) != "enabled"]
        if missing:
            raise ValueError("请先启用依赖模块：" + "、".join(missing))
        return db.set_feature_module_state(module_key, "enabled", username)

    next_state = "draining" if active_job_count(module_key) else "disabled"
    result = db.set_feature_module_state(module_key, next_state, username)
    affected = {module_key}
    while True:
        discovered = {
            dependent.key for dependent in BUSINESS_MODULES
            if any(key in affected for key in dependent.dependencies)
        }
        expanded = affected | discovered
        if expanded == affected:
            break
        affected = expanded
    for dependent_key in affected - {module_key}:
        if state(dependent_key) != "disabled":
            dependent_state = "draining" if active_job_count(dependent_key) else "disabled"
            db.set_feature_module_state(dependent_key, dependent_state, username)
    return result


def feature_required(module_key: str, *, write: bool = False):
    """Guard a business route while allowing reads during queue draining."""
    if module_key not in DEFINITIONS:
        raise KeyError(f"Unknown module: {module_key}")

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            current = state(module_key)
            if current == "disabled" or (write and current == "draining"):
                return jsonify({
                    "error": "该功能当前已停用，请联系管理员",
                    "code": "module_disabled",
                    "module": module_key,
                    "state": current,
                }), 503
            return fn(*args, **kwargs)
        return wrapped
    return decorator
