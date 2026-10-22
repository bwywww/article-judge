"""Scan OCR via the ECNU multimodal model (ecnu-plus).

Independent of the global DeepSeek base_url: OCR needs image understanding
which only ecnu-plus provides, so this module carries its own base_url,
credential and usage recording and never touches the platform-wide
cfg["base_url"].
"""

from __future__ import annotations

import base64
import io
import time

import requests

from essay_platform.core.ai_endpoints import AiEndpointError, validate_ocr_endpoint
from essay_platform.core.config import (
    MAX_OCR_PAGES, OCR_IMAGE_MAX_DIMENSION, OCR_IMAGE_MAX_PIXELS, OCR_TIMEOUT,
)
from essay_platform.core.secrets import get_ai_secret, redact_secrets

ECNU_BASE_URL = "https://chat.ecnu.edu.cn/open/api/v1"
ECNU_MODEL = "ecnu-plus"
OCR_PROMPT = (
    "你是一名 OCR 文字识别助手。请把这张学生作文图片里的文字完整、准确地转录出来。"
    "要求：\n"
    "1. 逐字转录，不改写、不润色、不补充原文没有的内容。\n"
    "2. 保留原文的换行和段落结构（手写换行不确定时按语义分段）。\n"
    "3. 只输出转录的文字本身，不要任何解释、前言或后记。"
)


class OcrError(RuntimeError):
    def __init__(self, message, *, category="ocr_failed", transient=False, status_code=None):
        super().__init__(message)
        self.category = category
        self.transient = transient
        self.status_code = status_code


def _get_ecnu_key():
    value, _source = get_ai_secret("ecnu_plus")
    if not value:
        raise OcrError("OCR 服务未配置：缺少 ecnu_plus 密钥",
                       category="config")
    return value


def _encode_image(pil_image) -> str:
    """把 PIL 图片缩略到最大边长后转 PNG base64 data URL。"""
    try:
        from PIL import Image
    except ImportError as exc:
        raise OcrError("缺少图片处理依赖（Pillow）", category="config") from exc
    image = pil_image.convert("RGB")
    width, height = image.size
    if width <= 0 or height <= 0 or width * height > OCR_IMAGE_MAX_PIXELS:
        raise OcrError(
            f"图片像素过大（{width}×{height}），请压缩后重试",
            category="image_too_large",
        )
    longest = max(width, height)
    if longest > OCR_IMAGE_MAX_DIMENSION:
        scale = OCR_IMAGE_MAX_DIMENSION / longest
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                             Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def image_to_data_url(file_bytes: bytes, filename: str = ""):
    """把上传的图片文件转成 PNG data URL（自动缩略）。"""
    try:
        from PIL import Image
    except ImportError as exc:
        raise OcrError("缺少图片处理依赖（Pillow）", category="config") from exc
    previous_pixel_limit = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = OCR_IMAGE_MAX_PIXELS
        try:
            image = Image.open(io.BytesIO(file_bytes))
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > OCR_IMAGE_MAX_PIXELS:
                raise OcrError(
                    f"图片像素过大（{width}×{height}），请压缩后重试",
                    category="image_too_large",
                )
            image.load()
        except OcrError:
            raise
        except Image.DecompressionBombError as exc:
            raise OcrError("图片像素超过安全上限", category="image_too_large") from exc
        except Exception as exc:
            raise OcrError(f"图片解码失败（{filename or '未知文件'}）：{redact_secrets(exc)}",
                           category="decode") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_pixel_limit
    return _encode_image(image)


def pdf_page_count(source, max_pages: int = MAX_OCR_PAGES) -> int:
    """Return a validated PDF page count without rendering any page."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise OcrError("缺少 PDF 渲染依赖（pypdfium2）", category="config") from exc
    try:
        document = pdfium.PdfDocument(source)
    except Exception as exc:
        raise OcrError(f"PDF 解析失败：{redact_secrets(exc)}", category="decode") from exc
    try:
        count = len(document)
        if count > max_pages:
            raise OcrError(f"扫描件最多支持 {max_pages} 页，当前 {count} 页",
                           category="too_many_pages")
        return count
    finally:
        document.close()


def pdf_page_to_data_url(source, page_index: int) -> str:
    """Render exactly one PDF page and release it before returning."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise OcrError("缺少 PDF 渲染依赖（pypdfium2）", category="config") from exc
    try:
        document = pdfium.PdfDocument(source)
    except Exception as exc:
        raise OcrError(f"PDF 解析失败：{redact_secrets(exc)}", category="decode") from exc
    try:
        if page_index < 0 or page_index >= len(document):
            raise OcrError("PDF 页码越界", category="decode")
        try:
            bitmap = document[page_index].render(scale=2.0)
            return _encode_image(bitmap.to_pil())
        except OcrError:
            raise
        except Exception as exc:
            raise OcrError(
                f"第 {page_index + 1} 页渲染失败：{redact_secrets(exc)}",
                category="decode",
            ) from exc
    finally:
        document.close()


def call_ocr_vision(image_data_url: str) -> str:
    """调用 ecnu-plus 识别单张图片，返回转录文本。"""
    try:
        endpoint = validate_ocr_endpoint(ECNU_BASE_URL)
    except AiEndpointError as exc:
        raise OcrError("OCR 接口地址不在允许范围内", category="config") from exc
    key = _get_ecnu_key()
    started = time.perf_counter()
    try:
        response = requests.post(
            endpoint + "/chat/completions",
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"},
            json={
                "model": ECNU_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OCR_PROMPT},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                }],
                "stream": False,
            },
            timeout=OCR_TIMEOUT,
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        _record_ocr_usage(started, "error", "timeout")
        raise OcrError("OCR 接口调用超时，可稍后重试", category="timeout",
                       transient=True) from exc
    except requests.ConnectionError as exc:
        _record_ocr_usage(started, "error", "connection")
        raise OcrError("无法连接 OCR 服务", category="connection",
                       transient=True) from exc
    except requests.RequestException as exc:
        _record_ocr_usage(started, "error", "request")
        raise OcrError(f"OCR 接口请求失败：{redact_secrets(exc)}",
                       category="request", transient=True) from exc
    if 300 <= response.status_code < 400:
        _record_ocr_usage(started, "error", f"http_{response.status_code}")
        raise OcrError("OCR 接口拒绝重定向", category="http_error",
                       status_code=response.status_code)
    if response.status_code != 200:
        transient = response.status_code == 429 or response.status_code >= 500
        _record_ocr_usage(started, "error", f"http_{response.status_code}")
        raise OcrError(
            f"OCR 接口返回 {response.status_code}：{redact_secrets(response.text[:200])}",
            category="http_error", transient=transient, status_code=response.status_code,
        )
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        _record_ocr_usage(started, "error", "invalid_response")
        raise OcrError("OCR 接口响应缺少有效内容", category="invalid_response") from exc
    if not isinstance(content, str) or not content.strip():
        _record_ocr_usage(started, "error", "empty_content")
        raise OcrError("OCR 未识别出文字", category="no_text")
    _record_ocr_usage(started, "ok", response_data=payload)
    return content.strip()


def _record_ocr_usage(started, status, error_type="", response_data=None):
    try:
        from essay_platform.adapters.ai import record_ai_usage
        record_ai_usage(
            {"profile": "ecnu_plus", "taskType": "ocr", "model": ECNU_MODEL},
            response_data=response_data,
            duration=round(time.perf_counter() - started, 2),
            status=status,
            error_type=error_type,
        )
    except Exception as exc:
        print(f"[OCR用量] 写入失败：{redact_secrets(exc)}")


def classify_ocr_error(error) -> tuple[str, str]:
    """把底层异常转成老师能理解的中文分类与处理建议。"""
    category = getattr(error, "category", "")
    message = str(error or "")
    if category == "config" or "未配置" in message or "缺少" in message:
        return "配置缺失", "OCR 服务尚未配置，请联系管理员检查 ecnu_plus 密钥。"
    if category == "too_many_pages":
        return "页数过多", "扫描件页数超过上限，请拆分后分批上传。"
    if category == "image_too_large":
        return "图片像素过大", "图片超过安全像素上限，请压缩或降低分辨率后重试。"
    if category == "quota":
        return "AI 配额已用尽", "已完成页面会保留；管理员调整配额后可点击重试。"
    if category == "timeout" or "超时" in message:
        return "接口超时", "网络或模型响应过慢，可稍后重试。"
    if category == "http_error" and (error.status_code == 429 or "429" in message):
        return "频率限制", "OCR 调用过于频繁或配额受限，请稍后重试。"
    if category == "http_error" and error.status_code and error.status_code >= 500:
        return "模型服务繁忙", "OCR 服务端临时不可用，请稍后重试。"
    if category == "no_text" or "未识别" in message:
        return "未识别到文字", "图片里没有可识别的文字，请确认是清晰的作文扫描件。"
    if category == "decode" or "解码" in message or "解析" in message:
        return "文件无法识别", "文件损坏或格式不支持，请改用清晰的 PDF、图片或文字版文档。"
    return "识别失败", "请先重试；若仍失败，查看启动窗口中的详细错误。"
