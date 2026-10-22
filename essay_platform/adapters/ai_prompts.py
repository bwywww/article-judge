"""System prompts and message builders for the grading AI.

Pure functions only. The builders must stay side-effect free so the
transport/orchestration layer in ``adapters.ai`` can call them directly.
"""

from __future__ import annotations

from essay_platform.core.config import MAX_TEXT_LEN

GRADE_SYSTEM = (
    "你是一位拥有二十年教学经验的资深语文教师和作文评阅专家。"
    "你的评语专业、具体、有温度：既准确指出问题，也给出可操作的修改建议，"
    "既肯定亮点，也提出恰如其分的期待。你对分数有严格而稳定的标准，宽严适中。"
)

NAME_EXTRACT_SYSTEM = (
    "你是文档整理助手。从学生作文中找出作者姓名："
    "姓名通常是文中独立成行的 2-4 个汉字，或「姓名：XXX」「作者：XXX」「名字：XXX」等形式。"
    "若确实无法确定，返回空字符串。"
)

AI_SPLIT_SYSTEM = (
    "你是作文文档切分助手。你的任务只是在原文中识别多篇作文的起始边界，绝不能改写、"
    "补充或删减正文。startMarker 必须逐字复制自原文，长度为 8-40 个字符，并且位于对应作文开头。"
    "若原文只有一篇作文，只返回一个 segment。姓名无法确定时返回空字符串。"
)


def build_grade_messages(title, prompt, criteria, word_limit, text):
    user = (
        "请评阅下面这篇学生作文。\n\n"
        f"【作文题目】{title or '（未注明）'}\n"
        f"【写作要求】{prompt or '（未注明）'}\n"
        f"【评分标准】{criteria or '（未注明）'}\n"
        f"【字数要求】{word_limit or '（未注明）'}\n\n"
        f"【学生作文】\n{text}\n\n"
        "请严格按照下面的 JSON 格式输出（只输出 JSON，不要任何其他文字）：\n"
        '{"score": 总分0-100的整数, "level": "优秀/良好/中等/及格/待提升", '
        '"summary": "总评，2-3句话概括整体水平", '
        '"dimensions": {"立意与内容": 整数分, "结构与逻辑": 整数分, "语言表达": 整数分}, '
        '"strengths": ["优点1", "优点2", "优点3"], '
        '"weaknesses": ["不足1", "不足2"], '
        '"suggestions": ["具体修改意见1，说明怎么改", "修改意见2"], '
        '"modelSnippet": "1-2句示范性改写句子或好句示范", '
        '"studentName": "从作文中识别出的学生姓名（通常为开头或结尾独立成行的姓名；无法确定则填空字符串）"}'
    )
    return [{"role": "system", "content": GRADE_SYSTEM},
            {"role": "user", "content": user}]


def build_grade_repair_messages(content):
    return [
        {"role": "system", "content": "你是 JSON 格式修复助手，只修复格式，不改变评分内容。"},
        {"role": "user", "content": (
            "请把下面内容修复为一个合法 JSON 对象，只输出 JSON。必须包含 score、level、summary、"
            "dimensions、strengths、weaknesses、suggestions、modelSnippet、studentName。\n\n"
            + (content or "")[:12000]
        )},
    ]


def build_upgrade_messages(title, prompt, text):
    return [
        {"role": "system", "content": (
            "你是资深语文教师。请在不改变原文事实、中心和情感的前提下润色全文，"
            "改善用词、病句、衔接和结构，不凭空增加经历。"
        )},
        {"role": "user", "content": (
            f"【作文题目】{title or '（未注明）'}\n"
            f"【写作要求】{prompt or '（未注明）'}\n"
            f"【原文】\n{trim_text(text)}\n\n"
            "请只输出 JSON：{\"upgrade\": \"润色后的完整作文\"}"
        )},
    ]


def trim_text(text):
    text = (text or "").strip()
    if len(text) > MAX_TEXT_LEN:
        text = text[:MAX_TEXT_LEN] + f"\n\n（作文过长，已截取前 {MAX_TEXT_LEN} 字评阅）"
    return text
