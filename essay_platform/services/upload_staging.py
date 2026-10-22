"""Teacher upload matching, staging and atomic source promotion."""

import os
import re
import secrets
import shutil
import time
import database as db
from essay_platform.core.config import (
    CLASSES_PATH, MAX_UPLOAD_BYTES, STAGING_DIR, UPLOADS_DIR,
    UPLOAD_STAGING_TTL, USERS_PATH,
)
from essay_platform.persistence.datasets import _load_json_file, _save_json_file, load_json
from essay_platform.services.directory import class_students, find_class

def match_student_username(identifier, class_id):
    identifier = (identifier or "").strip()
    if not identifier or not class_id:
        return ""
    users = {u["username"]: u for u in load_json(USERS_PATH, {"users": []})["users"]}
    cls = find_class(load_json(CLASSES_PATH, {"classes": []}), class_id)
    display_matches = []
    for username in (cls or {}).get("studentUsernames", []):
        u = users.get(username)
        if not u:
            continue
        if identifier in (u.get("studentNo"), u.get("username")):
            return username
        if identifier == u.get("display_name"):
            display_matches.append(username)
    if len(display_matches) == 1:
        return display_matches[0]
    return ""

def match_candidate_from_students(candidate, students):
    """优先匹配本班唯一姓名，再匹配完整学号；短班内号不参与自动绑定。"""
    student_no_hint = str((candidate or {}).get("studentNoHint") or "").strip()
    name = str((candidate or {}).get("studentName") or "").strip()
    identity_key = student_no_hint or name
    number_match = next((student["username"] for student in students
                         if identity_key
                         and identity_key in (student.get("studentNo"), student.get("username"))), "")
    name_matches = [student["username"] for student in students
                    if name and student.get("display_name") == name]
    name_match = name_matches[0] if len(name_matches) == 1 else ""
    return name_match or number_match

def match_student_candidate(candidate, class_id):
    return match_candidate_from_students(candidate, class_students(class_id))

def stage_source_file(aid, username, file):
    """保存待确认原件；令牌只能用于同一老师、同一作业。"""
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "pdf"
    if ext not in ("pdf", "docx", "txt", "md", "png", "jpg", "jpeg"):
        raise ValueError("仅支持 PDF、Word（.docx）或 TXT 文件")
    token = secrets.token_hex(16)
    folder = os.path.join(STAGING_DIR, token)
    os.makedirs(folder, exist_ok=False)
    fname = f"source.{ext}"
    file.stream.seek(0)
    content = file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        shutil.rmtree(folder, ignore_errors=True)
        raise ValueError(f"文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    with open(os.path.join(folder, fname), "wb") as f:
        f.write(content)
    if db.is_ready():
        db.save_upload_token(token, aid, username, fname, UPLOAD_STAGING_TTL)
    else:
        _save_json_file(os.path.join(folder, "meta.json"), {
            "assignmentId": aid,
            "username": username,
            "filename": fname,
            "createdAt": time.time(),
        })
    return token

def staged_source_info(token, aid, username):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise ValueError("上传令牌无效，请重新上传文件")
    folder = os.path.join(STAGING_DIR, token)
    if db.is_ready():
        row = db.get_upload_token(token)
        meta = ({"assignmentId": row["assignment_id"], "username": row["username"],
                 "filename": row["filename"], "createdAt": row["created_at"]}
                if row else None)
    else:
        meta_path = os.path.join(folder, "meta.json")
        meta = _load_json_file(meta_path, {}) if os.path.isfile(meta_path) else None
    if not meta:
        raise ValueError("上传令牌已过期，请重新上传文件")
    if meta.get("assignmentId") != aid or meta.get("username") != username:
        raise ValueError("上传令牌与当前作业不匹配")
    if time.time() - float(meta.get("createdAt", 0)) > UPLOAD_STAGING_TTL:
        shutil.rmtree(folder, ignore_errors=True)
        if db.is_ready():
            db.delete_upload_token(token)
        raise ValueError("上传令牌已过期，请重新上传文件")
    fname = meta.get("filename", "")
    if not re.fullmatch(r"source\.(?:pdf|docx|txt|md|png|jpg|jpeg)", fname):
        raise ValueError("上传文件标识无效")
    path = os.path.join(folder, fname)
    if not os.path.isfile(path):
        raise ValueError("临时上传文件已丢失，请重新上传")
    return folder, path, fname.rsplit(".", 1)[-1]

def promote_staged_source(token, aid, username):
    staging_folder, source_path, ext = staged_source_info(token, aid, username)
    folder = os.path.join(UPLOADS_DIR, aid)
    os.makedirs(folder, exist_ok=True)
    fname = f"source_{secrets.token_hex(4)}.{ext}"
    os.replace(source_path, os.path.join(folder, fname))
    shutil.rmtree(staging_folder, ignore_errors=True)
    if db.is_ready():
        db.consume_upload_token(token)
    return fname

def cleanup_assignment_sources(aid, keep=""):
    folder = os.path.join(UPLOADS_DIR, aid)
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        if name == keep:
            continue
        if re.fullmatch(r"source_[0-9a-f]{8}\.(?:pdf|docx|txt|md|png|jpg|jpeg)", name):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass

def discard_staged_source(token, aid, username):
    folder, _, _ = staged_source_info(token, aid, username)
    shutil.rmtree(folder, ignore_errors=True)
    if db.is_ready():
        db.delete_upload_token(token)
