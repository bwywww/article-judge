"""Single-use import preview persistence and public projection."""

import os
import re
import time
import database as db
from essay_platform.core.config import IMPORTS_DIR, IMPORT_PREVIEW_TTL
from essay_platform.persistence.datasets import _save_json_file, load_json

def save_import_preview(preview):
    token = preview["token"]
    if db.is_ready():
        db.save_import_preview(preview, IMPORT_PREVIEW_TTL)
    else:
        os.makedirs(IMPORTS_DIR, exist_ok=True)
        _save_json_file(os.path.join(IMPORTS_DIR, f"preview_{token}.json"), preview)
    return token

def public_import_preview(preview):
    rows = []
    for row in preview.get("rows", []):
        rows.append({k: v for k, v in row.items()
                     if k not in ("password", "passwordHash", "passwordScheme")})
    return {**preview, "rows": rows}

def load_import_preview(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return None
    if db.is_ready():
        return db.load_import_preview(token)
    path = os.path.join(IMPORTS_DIR, f"preview_{token}.json")
    if not os.path.exists(path):
        return None
    preview = load_json(path, {})
    if preview.get("consumedAt"):
        return None
    if time.time() - float(preview.get("createdAt", 0)) > IMPORT_PREVIEW_TTL:
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    return preview
