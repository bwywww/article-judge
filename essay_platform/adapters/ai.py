"""DeepSeek-compatible AI calls and grading normalization."""

import contextlib
import json
import re
import time

import requests
from flask import has_request_context, request

import database as db
from essay_platform.core.config import (
    AI_TASK_ROUTE_DEFAULTS, DEFAULT_CONFIG, GRADE_RETRY_DELAY, LLM_TIMEOUT,
    MAX_AI_SPLIT_CHARS, MAX_TEXT_LEN,
)
from essay_platform.core.ai_endpoints import (
    AiEndpointError, SAFE_ERROR_MESSAGE, validate_text_endpoint,
)
from essay_platform.core.secrets import get_ai_secret, redact_secrets
from essay_platform.core.state import llm_task_local
from essay_platform.persistence.datasets import get_config
from essay_platform.adapters.documents import inline_identity
from essay_platform.adapters.ai_normalize import (  # noqa: F401 - re-export
    DIMENSION_KEYS, KEY_ALIASES, level_of, manual_grade_of, normalize_feedback,
    normalize_teacher_feedback, parse_llm_json, pick, to_int, to_list,
)
from essay_platform.adapters.ai_prompts import (  # noqa: F401 - re-export
    AI_SPLIT_SYSTEM, GRADE_SYSTEM, NAME_EXTRACT_SYSTEM, build_grade_messages,
    build_grade_repair_messages, build_upgrade_messages, trim_text,
)

class ApiKeyError(Exception):
    pass


class LLMRequestError(RuntimeError):
    def __init__(self, message, *, status_code=None, transient=False):
        super().__init__(message)
        self.status_code = status_code
        self.transient = transient


def test_connection(base_url, api_key, model, *, timeout=30):
    """Test a runtime AI profile without exposing its credential."""
    try:
        endpoint = validate_text_endpoint(base_url)
    except AiEndpointError:
        return {"ok": False, "error": SAFE_ERROR_MESSAGE,
                "reason": "invalid_endpoint"}
    try:
        response = requests.post(
            endpoint + "/chat/completions",
            headers={"Authorization": "Bearer " + api_key},
            json={"model": model, "messages": [{"role": "user", "content": "你好"}],
                  "max_tokens": 8},
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        return {"ok": False, "error": f"连接失败：{redact_secrets(exc)}",
                "reason": type(exc).__name__}
    if response.status_code != 200:
        if 300 <= response.status_code < 400:
            return {"ok": False, "error": "AI 接口拒绝重定向",
                    "statusCode": response.status_code, "reason": "redirect"}
        detail = redact_secrets(response.text[:200])
        return {"ok": False, "error": f"接口返回 {response.status_code}：{detail}",
                "statusCode": response.status_code}
    payload = response.json()
    return {"ok": True, "model": payload.get("model") or model}


def ai_route_defaults(task_type):
    return next((dict(item) for item in AI_TASK_ROUTE_DEFAULTS
                 if item["taskType"] == task_type), dict(AI_TASK_ROUTE_DEFAULTS[1]))


def get_ai_task_route(task_type):
    if db.is_ready():
        route = db.get_ai_task_route(task_type)
        if route:
            return route
    return ai_route_defaults(task_type)


@contextlib.contextmanager
def ai_task_scope(task_type, *, json_mode=None):
    previous = getattr(llm_task_local, "context", None)
    route = get_ai_task_route(task_type)
    if json_mode is not None:
        route["jsonMode"] = bool(json_mode)
    llm_task_local.context = route
    try:
        yield route
    finally:
        llm_task_local.context = previous


def current_ai_task_route():
    return getattr(llm_task_local, "context", None) or get_ai_task_route("utility")


def record_ai_usage(route, *, response_data=None, duration=0, status="ok", error_type=""):
    if not db.is_ready():
        return
    usage = response_data.get("usage", {}) if isinstance(response_data, dict) else {}
    actor = ""
    if has_request_context():
        actor = getattr(request, "user", {}).get("username", "")
    try:
        db.write_ai_usage(
            profile=route.get("profile", "article_judge"),
            task_type=route.get("taskType", "utility"),
            model=route.get("model", DEFAULT_CONFIG["model"]),
            actor_username=actor,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cache_hit_tokens=(usage.get("prompt_cache_hit_tokens", 0)
                              or usage.get("cache_read_input_tokens", 0)),
            duration_seconds=duration, status=status, error_type=error_type,
        )
    except Exception as exc:
        print(f"[AI用量] 写入失败：{redact_secrets(exc)}")


def call_llm(messages, max_tokens=4096, temperature=0.6):
    route = current_ai_task_route()
    cfg = get_config()
    profile = route.get("profile", "article_judge")
    api_key, _source = get_ai_secret(profile)
    if not api_key:
        raise ApiKeyError("AI 接口尚未配置，请联系管理员")
    model = route.get("model") or cfg["model"]
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": min(int(max_tokens), int(route.get("maxTokens") or max_tokens)),
        "stream": False,
    }
    if route.get("jsonMode"):
        payload["response_format"] = {"type": "json_object"}
    started = time.perf_counter()
    try:
        base_url = validate_text_endpoint(cfg["base_url"])
    except AiEndpointError as exc:
        record_ai_usage(route, duration=time.perf_counter() - started,
                        status="error", error_type="invalid_endpoint")
        raise LLMRequestError(SAFE_ERROR_MESSAGE) from exc
    if base_url in ("https://api.deepseek.com", "https://api.deepseek.com/v1"):
        payload["thinking"] = {"type": "enabled" if route.get("thinking") else "disabled"}
    try:
        resp = requests.post(
            base_url + "/chat/completions",
            headers={"Authorization": "Bearer " + api_key,
                     "Content-Type": "application/json"},
            json=payload,
            timeout=LLM_TIMEOUT,
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        record_ai_usage(route, duration=time.perf_counter() - started,
                        status="error", error_type="timeout")
        raise LLMRequestError("AI 接口调用超时", transient=True) from exc
    except requests.ConnectionError as exc:
        record_ai_usage(route, duration=time.perf_counter() - started,
                        status="error", error_type="connection")
        raise LLMRequestError("无法连接 AI 接口", transient=True) from exc
    except requests.RequestException as exc:
        record_ai_usage(route, duration=time.perf_counter() - started,
                        status="error", error_type="request")
        raise LLMRequestError(
            f"AI 接口请求失败：{redact_secrets(exc)}", transient=True) from exc
    if resp.status_code != 200:
        if 300 <= resp.status_code < 400:
            record_ai_usage(route, duration=time.perf_counter() - started,
                            status="error", error_type=f"http_{resp.status_code}")
            raise LLMRequestError("AI 接口拒绝重定向", status_code=resp.status_code)
        transient = resp.status_code == 429 or resp.status_code >= 500
        record_ai_usage(route, duration=time.perf_counter() - started,
                        status="error", error_type=f"http_{resp.status_code}")
        raise LLMRequestError(
            f"AI 接口返回 {resp.status_code}：{redact_secrets(resp.text[:200])}",
            status_code=resp.status_code,
            transient=transient,
        )
    try:
        response_data = resp.json()
        content = response_data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        record_ai_usage(route, duration=time.perf_counter() - started,
                        status="error", error_type="invalid_response")
        raise LLMRequestError("AI 接口响应缺少有效内容") from exc
    if not isinstance(content, str) or not content.strip():
        record_ai_usage(route, response_data=response_data,
                        duration=time.perf_counter() - started,
                        status="error", error_type="empty_content")
        raise LLMRequestError("AI 接口返回了空内容")
    record_ai_usage(route, response_data=response_data,
                    duration=time.perf_counter() - started)
    return content


def classify_grade_error(error):
    """把底层异常转成老师能理解的失败类型和处理建议。"""
    msg = str(error or "").strip()
    low = msg.lower()
    if "api key" in low or "尚未配置" in msg or "401" in msg or "403" in msg:
        return "配置/鉴权失败", "请联系管理员检查 API Key、接口地址和模型权限。"
    if "timeout" in low or "timed out" in low or "超时" in msg:
        return "接口超时", "网络或模型响应过慢，可稍后重试，或换用更稳定的模型。"
    if "429" in msg or "rate" in low or "频率" in msg:
        return "频率限制", "接口调用过于频繁或额度受限，请稍后重试。"
    if any(code in msg for code in ("500", "502", "503", "504")) or "繁忙" in msg:
        return "模型服务繁忙", "服务端临时不可用，请稍后重试。"
    if "json" in low or "解析" in msg or "未找到 JSON" in msg:
        return "AI 返回格式异常", "模型输出不完整或格式不对，建议重试；若频繁出现可切换到非推理模型。"
    return "批改失败", "请先重试；若仍失败，查看启动窗口中的详细错误。"


def validate_grade_result(content):
    raw = parse_llm_json(content)
    if not isinstance(raw, dict):
        raise ValueError("AI 返回的批改结果不是 JSON 对象")
    if to_int(pick(raw, "score")) is None:
        raise ValueError("AI 返回的批改结果缺少有效分数")
    if not str(pick(raw, "summary") or "").strip():
        raise ValueError("AI 返回的批改结果缺少总评")
    result = normalize_feedback(raw)
    result["upgrade"] = ""
    return result


def is_transient_grade_error(exc):
    return isinstance(exc, LLMRequestError) and exc.transient


def request_grade_result(title, prompt, criteria, word_limit, text, on_attempt=None):
    """主批改最多请求两次：瞬时接口错误重试一次，JSON 异常修复一次。"""
    if on_attempt:
        on_attempt(1)
    with ai_task_scope("grading"):
        try:
            content = call_llm(
                build_grade_messages(title, prompt, criteria, word_limit, trim_text(text)),
                max_tokens=2600,
            )
        except Exception as exc:
            if not is_transient_grade_error(exc):
                raise
            time.sleep(GRADE_RETRY_DELAY)
            if on_attempt:
                on_attempt(2)
            content = call_llm(
                build_grade_messages(title, prompt, criteria, word_limit, trim_text(text)),
                max_tokens=2600,
            )
            return validate_grade_result(content)
    try:
        return validate_grade_result(content)
    except (ValueError, json.JSONDecodeError):
        if on_attempt:
            on_attempt(2)
        with ai_task_scope("utility"):
            repaired = call_llm(build_grade_repair_messages(content), max_tokens=2600, temperature=0)
        return validate_grade_result(repaired)


def request_upgrade_text(title, prompt, text):
    with ai_task_scope("upgrade"):
        content = call_llm(build_upgrade_messages(title, prompt, text),
                           max_tokens=4200, temperature=0.4)
    raw = parse_llm_json(content)
    upgrade = str(raw.get("upgrade") or "").strip() if isinstance(raw, dict) else ""
    if len(upgrade) < 10:
        raise ValueError("AI 未返回有效的润色全文")
    return upgrade


def extract_name_call(text):
    """用 AI 从作文文本中识别学生姓名，失败或无法确定时返回空字符串。
    接口偶发繁忙（503/超时）时重试一次；max_tokens 太小会让模型截断成空内容，需留足余量。"""
    for attempt in range(2):
        try:
            with ai_task_scope("utility"):
                content = call_llm([
                    {"role": "system", "content": NAME_EXTRACT_SYSTEM},
                    {"role": "user",
                     "content": f"【作文内容】\n{trim_text(text)}\n\n请只返回 JSON：{{\"studentName\": \"姓名或空字符串\"}}"},
                ], max_tokens=1024, temperature=0)
            parsed = parse_llm_json(content)
            name = str(parsed.get("studentName") or "").strip()[:10]
            if name in ("姓名或空字符串", "未知", "无名"):  # 模型回显了示例占位符
                name = ""
            return name
        except Exception:
            if attempt == 0:
                time.sleep(1.5)  # 接口繁忙时稍候重试
    return ""


def request_ai_split(text, fallback_name=""):
    """让 AI 只返回原文边界，再由服务端按精确位置无损切分。"""
    source = (text or "").strip()
    if len(source) < 20:
        raise ValueError("作文内容太短，无法判断切分位置")
    if len(source) > MAX_AI_SPLIT_CHARS:
        raise ValueError(f"单篇内容超过 {MAX_AI_SPLIT_CHARS} 字，请先人工拆分后再使用 AI")
    with ai_task_scope("utility"):
        content = call_llm([
            {"role": "system", "content": AI_SPLIT_SYSTEM},
            {"role": "user", "content": (
                "请判断下面文本是否误合并了多篇学生作文。只输出 JSON："
                '{"segments":[{"studentName":"姓名或空字符串",'
                '"startMarker":"从原文逐字复制的作文起始片段"}]}。'
                "segments 必须按原文顺序排列；不要返回正文全文。\n\n"
                f"【原文】\n{source}"
            )},
        ], max_tokens=1800, temperature=0)
    parsed = parse_llm_json(content)
    raw_segments = parsed.get("segments") if isinstance(parsed, dict) else None
    if not isinstance(raw_segments, list):
        raise ValueError("AI 未返回有效的切分边界")

    markers = []
    search_from = 0
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        marker = str(item.get("startMarker") or "").strip()
        if len(marker) < 4 or len(marker) > 80:
            continue
        position = source.find(marker, search_from)
        if position < 0:
            continue
        if markers and position <= markers[-1][0]:
            continue
        name = str(item.get("studentName") or "").strip()[:10]
        if name in ("姓名或空字符串", "未知", "无名"):
            name = ""
        markers.append((position, name))
        search_from = position + len(marker)

    if len(markers) < 2:
        return [{
            "studentName": fallback_name or "第1篇",
            "studentNoHint": "",
            "text": source,
            "chars": len(source),
        }]

    markers[0] = (0, markers[0][1])
    essays = []
    for index, (start, ai_name) in enumerate(markers):
        end = markers[index + 1][0] if index + 1 < len(markers) else len(source)
        segment = source[start:end].strip()
        if len(segment) < 10:
            continue
        inline_name, student_no = inline_identity(segment[:500])
        name = ai_name or inline_name or (fallback_name if index == 0 else f"第{index + 1}篇")
        essays.append({
            "studentName": name,
            "studentNoHint": student_no,
            "text": segment,
            "chars": len(segment),
        })
    return essays if len(essays) >= 2 else [{
        "studentName": fallback_name or "第1篇",
        "studentNoHint": "",
        "text": source,
        "chars": len(source),
    }]
