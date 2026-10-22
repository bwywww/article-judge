"""Environment-backed paths, limits and default AI routes."""

import os

import database as db

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEGACY_DATA_DIR = os.path.join(BASE_DIR, "essay_platform", "data")
EXPLICIT_DATA_DIR = bool((os.environ.get("ESSAY_DATA_DIR") or "").strip())
DATA_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("ESSAY_DATA_DIR", os.path.join(BASE_DIR, "data"))))
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
STAGING_DIR = os.path.join(UPLOADS_DIR, ".staging")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")
IMPORTS_DIR = os.path.join(DATA_DIR, "imports")
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
USERS_PATH = os.path.join(DATA_DIR, "users.json")
CLASSES_PATH = os.path.join(DATA_DIR, "classes.json")
ASSIGNMENTS_PATH = os.path.join(DATA_DIR, "assignments.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
DB_PATH = db.configure(DATA_DIR)

DEFAULT_CONFIG = {
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-v4-flash",
}
AI_TASK_ROUTE_DEFAULTS = [
    {"taskType": "grading", "profile": "article_judge", "model": "deepseek-v4-flash",
     "thinking": False, "jsonMode": True, "maxTokens": 2600},
    {"taskType": "utility", "profile": "article_judge", "model": "deepseek-v4-flash",
     "thinking": False, "jsonMode": True, "maxTokens": 1800},
    {"taskType": "upgrade", "profile": "article_judge", "model": "deepseek-v4-flash",
     "thinking": False, "jsonMode": True, "maxTokens": 4200},
    {"taskType": "class_insight", "profile": "article_judge", "model": "deepseek-v4-flash",
     "thinking": False, "jsonMode": True, "maxTokens": 1800},
]
MAX_TEXT_LEN = 5000          # 评阅时截断的超长作文字数
GRADE_CONCURRENCY = 4        # 同时批改的作文路数
LLM_TIMEOUT = 60             # 单次 AI 调用超时；瞬时错误最多再重试一次
GRADE_RETRY_DELAY = 1.0
DATA_BACKUP_KEEP = 10        # 自动启动备份最多保留份数
IMPORT_PREVIEW_TTL = 24 * 60 * 60
UPLOAD_STAGING_TTL = 24 * 60 * 60
MAX_UPLOAD_BYTES = 60 * 1024 * 1024
MAX_PDF_PAGES = 150
MAX_OCR_PAGES = 150         # 足够容纳 50+ 人双面作文纸，仍保留资源硬上限
OCR_IMAGE_MAX_DIMENSION = 2000  # 识别前图片最长边像素，超出则缩略
OCR_IMAGE_MAX_PIXELS = 40_000_000
OCR_TIMEOUT = 90            # 单页多模态识别的超时（秒），慢于普通 LLM
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_DOCX_PARAGRAPHS = 10000
MAX_AI_SPLIT_CHARS = 120000
MAX_ESSAY_CHARS = 20000
MAX_EXCEL_ROWS = 5000
MAX_EXCEL_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
DATA_SCHEMA_VERSION = 5
PASSWORD_MIN_LENGTH = 10
PASSWORD_HASH_METHOD = "pbkdf2:sha256:600000"
DEFAULT_TEMP_PASSWORD = "ChangeMe123!"
SESSION_TTL = 12 * 60 * 60
LOGIN_FAILURE_WINDOW = 10 * 60
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_DURATION = 15 * 60
