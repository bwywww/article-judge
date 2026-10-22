"""Shared service and HTTP error types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from flask import jsonify

from essay_platform.core.config import MAX_UPLOAD_BYTES


@dataclass
class ServiceError(Exception):
    message: str
    code: str = "service_error"
    status: int = 400
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


def service_error_response(exc):
    return jsonify({"error": str(exc), "code": getattr(exc, "code", "service_error")}), getattr(exc, "status", 400)


def handle_data_file_error(error):
    return jsonify({"error": str(error), "code": "data_file_error"}), 500


def handle_upload_too_large(_error):
    return jsonify({"error": f"上传文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB"}), 413
