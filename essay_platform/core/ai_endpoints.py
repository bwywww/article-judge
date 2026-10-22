"""Exact outbound endpoint policy for credential-bearing AI requests."""

from urllib.parse import urlsplit
import unicodedata


TEXT_ENDPOINTS = {
    ("api.deepseek.com", ""): "https://api.deepseek.com",
    ("api.deepseek.com", "/v1"): "https://api.deepseek.com/v1",
}
OCR_ENDPOINT = "https://chat.ecnu.edu.cn/open/api/v1"
OCR_ENDPOINTS = {
    ("chat.ecnu.edu.cn", "/open/api/v1"): OCR_ENDPOINT,
}


class AiEndpointError(ValueError):
    """Raised when an AI endpoint is outside the trusted destination policy."""


SAFE_ERROR_MESSAGE = "AI 接口地址不在允许范围内"


def _validate_endpoint(value, allowed_endpoints):
    if not isinstance(value, str) or not value:
        raise AiEndpointError(SAFE_ERROR_MESSAGE)
    if any(char.isspace() or unicodedata.category(char) == "Cc" for char in value):
        raise AiEndpointError(SAFE_ERROR_MESSAGE)
    if "\\" in value or "%" in value or "?" in value or "#" in value:
        raise AiEndpointError(SAFE_ERROR_MESSAGE)

    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise AiEndpointError(SAFE_ERROR_MESSAGE) from exc

    canonical = allowed_endpoints.get((hostname, parsed.path))
    allowed_authorities = {hostname, hostname + ":443"}
    if (canonical is None or parsed.netloc.lower() not in allowed_authorities
            or parsed.scheme.lower() != "https"
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or parsed.query or parsed.fragment):
        raise AiEndpointError(SAFE_ERROR_MESSAGE)
    return canonical


def validate_text_endpoint(value):
    """Return the canonical DeepSeek base URL or reject the supplied value."""
    return _validate_endpoint(value, TEXT_ENDPOINTS)


def validate_ocr_endpoint(value):
    """Return the fixed ECNU OCR base URL or reject the supplied value."""
    return _validate_endpoint(value, OCR_ENDPOINTS)
