"""Flask application factory for the modular monolith."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from essay_platform.modules import build_blueprints, manifests


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def create_app() -> Flask:
    from essay_platform.bootstrap import ensure_runtime_initialized
    from essay_platform.core.config import MAX_UPLOAD_BYTES
    from essay_platform.core.errors import handle_data_file_error, handle_upload_too_large
    from essay_platform.core.state import extension_state
    from essay_platform.persistence.datasets import DataFileError

    app = Flask(
        "essay_platform",
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    if os.environ.get("TRUST_PROXY_HEADERS", "").strip() == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    app.register_error_handler(DataFileError, handle_data_file_error)
    app.register_error_handler(413, handle_upload_too_large)
    for blueprint in build_blueprints():
        app.register_blueprint(blueprint)
    app.before_request(ensure_runtime_initialized)
    app.extensions["module_manifests"] = manifests()
    app.extensions["essay_platform"] = {
        "runtime": extension_state(),
        "modules": app.extensions["module_manifests"],
    }
    return app
