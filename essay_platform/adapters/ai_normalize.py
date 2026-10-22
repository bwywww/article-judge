"""Normalize AI output into the platform's canonical grading structures.

Pure functions only: no HTTP, no config, no side effects. Kept separate from
the transport and orchestration code in ``adapters.ai`` so the AI adapter
stays readable and the normalization rules are testable in isolation.
"""

from __future__ import annotations

import json
import re


def parse_llm_json(text):
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("AI 返回内容中未找到 JSON 数据")
    return json.loads(text[start:end + 1])


def to_int(value):
    try:
        return int(float(str(value).replace("分", "").strip()))
    except (TypeError, ValueError):
        return None


def to_list(value):
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[\n；;]", value) if x.strip()]
    return []


def level_of(score):
    for threshold, level in ((90, "优秀"), (80, "良好"), (70, "中等"), (60, "及格")):
        if score >= threshold:
            return level
    return "待提升"


DIMENSION_KEYS = ["立意与内容", "结构与逻辑", "语言表达"]

# AI 输出键名的中文别名（模型偶尔输出中文键）
KEY_ALIASES = {
    "score": ("score", "总分", "得分", "分数"),
    "level": ("level", "等级"),
    "summary": ("summary", "总评", "综合评价"),
    "strengths": ("strengths", "优点", "亮点"),
    "weaknesses": ("weaknesses", "不足", "问题", "缺点"),
    "suggestions": ("suggestions", "建议", "修改意见", "改进意见"),
    "modelSnippet": ("modelSnippet", "model_snippet", "示范", "好句", "示例"),
    "upgrade": ("upgrade", "提升版", "润色版", "提质作文", "提格作文"),
}


def pick(raw, field):
    """按英文键优先、中文别名兜底取值"""
    for k in KEY_ALIASES.get(field, (field,)):
        if isinstance(raw, dict) and raw.get(k) not in (None, ""):
            return raw.get(k)
    return None


def normalize_feedback(raw):
    """把 AI 返回的 JSON 规整成统一结构，缺字段的补默认值"""
    if not isinstance(raw, dict):
        raw = {}
    score = to_int(pick(raw, "score"))
    if score is None:
        score = 0
    score = max(0, min(100, score))
    level = str(pick(raw, "level") or "").strip()
    if not level:
        level = level_of(score)
    dims_raw = raw.get("dimensions") or {}
    dims = {}
    for key in DIMENSION_KEYS:
        val = None
        if isinstance(dims_raw, dict):
            for k, v in dims_raw.items():
                if key in str(k):
                    val = to_int(v)
                    break
        if val is None and isinstance(dims_raw, dict) and key in dims_raw:
            val = to_int(dims_raw[key])
        dims[key] = val if val is not None else None
    return {
        "score": score,
        "level": level,
        "summary": str(pick(raw, "summary") or "").strip(),
        "dimensions": dims,
        "strengths": to_list(pick(raw, "strengths")),
        "weaknesses": to_list(pick(raw, "weaknesses")),
        "suggestions": to_list(pick(raw, "suggestions")),
        "modelSnippet": str(pick(raw, "modelSnippet") or "").strip(),
        "upgrade": str(pick(raw, "upgrade") or "").strip(),
        "studentName": str(raw.get("studentName") or "").strip(),
    }


def manual_grade_of(score):
    if score >= 85:
        return "优"
    if score >= 70:
        return "良"
    if score >= 60:
        return "中"
    return "差"


def normalize_teacher_feedback(raw):
    if not isinstance(raw, dict):
        raise ValueError("批改结果格式无效")
    score = to_int(raw.get("score"))
    if score is None or not 0 <= score <= 100:
        raise ValueError("总分必须是 0 到 100 的整数")
    level = str(raw.get("level") or manual_grade_of(score)).strip()
    if level not in ("优", "良", "中", "差"):
        raise ValueError("等第只能选择优、良、中、差")
    dims_raw = raw.get("dimensions") or {}
    if not isinstance(dims_raw, dict):
        raise ValueError("分项评分格式无效")
    dimensions = {}
    for key in DIMENSION_KEYS:
        value = to_int(dims_raw.get(key))
        if value is None or not 0 <= value <= 100:
            raise ValueError(f"{key}必须是 0 到 100 的整数")
        dimensions[key] = value

    def limited_text(field, limit):
        value = str(raw.get(field) or "").strip()
        if len(value) > limit:
            raise ValueError(f"{field}内容过长")
        return value

    def limited_list(field):
        values = to_list(raw.get(field))
        if len(values) > 30 or any(len(item) > 1000 for item in values):
            raise ValueError(f"{field}条目过多或单条过长")
        return values

    return {
        "score": score,
        "level": level,
        "summary": limited_text("summary", 4000),
        "dimensions": dimensions,
        "strengths": limited_list("strengths"),
        "weaknesses": limited_list("weaknesses"),
        "suggestions": limited_list("suggestions"),
        "modelSnippet": limited_text("modelSnippet", 4000),
        "upgrade": limited_text("upgrade", 20000),
        "studentName": "",
    }
