"""Load AI credentials without persisting or exposing their values."""

from __future__ import annotations

import os
import re
from pathlib import Path


PROFILE_ENV_VARS = {
    "article_judge": "ARTICLE_JUDGE_API_KEY",
    "code_develop": "CODE_DEVELOP_API_KEY",
    "ecnu_plus": "ECNU_PLUS_API_KEY",
}
LEGACY_RUNTIME_ENV = "AI_API_KEY"
KEY_PATTERN = re.compile(r"^sk-[A-Za-z0-9_-]{20,}$")
LINE_PATTERN = re.compile(
    r"^\s*([A-Za-z0-9_-]+)\s*(?::|=)?\s*(sk-[A-Za-z0-9_-]+)\s*$"
)


class SecretConfigError(RuntimeError):
    pass


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def app_environment() -> str:
    return (os.environ.get("APP_ENV") or "development").strip().lower()


def key_file_path() -> Path:
    configured = (os.environ.get("DEEPSEEK_KEYS_FILE") or "").strip()
    return Path(configured).expanduser().resolve() if configured else project_root() / "deepseekv4.txt"


def parse_key_file(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise SecretConfigError("无法读取开发环境 DeepSeek 密钥文件") from exc

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = LINE_PATTERN.fullmatch(line)
        if not match:
            raise SecretConfigError(f"DeepSeek 密钥文件第 {line_number} 行格式无效")
        profile, key = match.groups()
        if profile not in PROFILE_ENV_VARS:
            raise SecretConfigError(f"DeepSeek 密钥文件包含未知配置：{profile}")
        if profile in values:
            raise SecretConfigError(f"DeepSeek 密钥配置重复：{profile}")
        if not KEY_PATTERN.fullmatch(key):
            raise SecretConfigError(f"DeepSeek 密钥格式无效：{profile}")
        values[profile] = key
    return values


def load_ai_secrets() -> dict[str, dict[str, str]]:
    """Return profile values and sources without ever logging the values."""
    environment = app_environment()
    path = key_file_path()
    if environment == "production" and path.exists():
        raise SecretConfigError(
            "生产环境检测到 deepseekv4.txt，请迁移到环境变量后删除该文件"
        )

    file_values: dict[str, str] = {}
    if environment == "development" and path.exists():
        file_values = parse_key_file(path)

    result: dict[str, dict[str, str]] = {}
    for profile, env_name in PROFILE_ENV_VARS.items():
        value = (os.environ.get(env_name) or "").strip()
        source = "environment" if value else ""
        if not value and environment == "development":
            value = file_values.get(profile, "")
            source = "file" if value else ""
        if profile == "article_judge" and not value:
            value = (os.environ.get(LEGACY_RUNTIME_ENV) or "").strip()
            source = "legacy_environment" if value else ""
        if value and not KEY_PATTERN.fullmatch(value):
            raise SecretConfigError(f"{env_name} 格式无效")
        result[profile] = {"value": value, "source": source}
    return result


def get_ai_secret(profile: str) -> tuple[str, str]:
    if profile not in PROFILE_ENV_VARS:
        raise SecretConfigError(f"未知 AI 配置：{profile}")
    item = load_ai_secrets()[profile]
    return item["value"], item["source"]


def public_secret_status() -> dict[str, dict[str, object]]:
    return {
        profile: {"configured": bool(item["value"]), "source": item["source"]}
        for profile, item in load_ai_secrets().items()
    }


def redact_secrets(value: object) -> str:
    text = str(value or "")
    try:
        secrets = load_ai_secrets().values()
    except SecretConfigError:
        secrets = []
    for item in secrets:
        secret = item.get("value", "")
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[REDACTED]", text)
