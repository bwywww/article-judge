"""Assignment essay upload, parsing and AI-assisted splitting routes.

Split out of ``imports`` (which now owns only roster Excel import). The
``imports`` feature key stays the same so module dependencies and
permissions are unchanged.
"""

from .base import RouteSpec, create_blueprint
import secrets
import hashlib
from concurrent.futures import ThreadPoolExecutor
from flask import jsonify, request
import database as db
from essay_platform.core import modules as feature_modules
from essay_platform.core.audit import audit_event
from essay_platform.core.auth import student_required, teacher_required
from essay_platform.core.config import ASSIGNMENTS_PATH, MAX_ESSAY_CHARS
from essay_platform.core.state import io_lock
from essay_platform.core.time import content_hash, now_str
from essay_platform.persistence.datasets import get_config, load_json, save_json
from essay_platform.services.assignments import find_teacher_assignment
from essay_platform.services.directory import (
    class_student_usernames, class_students,
)
from essay_platform.services.upload_staging import (
    cleanup_assignment_sources, discard_staged_source, match_student_candidate,
    match_student_username, promote_staged_source, stage_source_file,
    staged_source_info,
)
from essay_platform.adapters.ai import classify_grade_error, extract_name_call, request_ai_split
from essay_platform.adapters.documents import (
    ALLOWED_IMAGE_EXTS, is_placeholder_name, parse_upload,
)
from essay_platform.services.operations import ai_quota_error
from essay_platform.workers.ocr import schedule_ocr_job

MANIFEST = {
    "key": "imports", "dependencies": ["directory", "assignments"],
    "permissions": ["admin", "teacher", "student"],
    "navigation": ["student-import", "essay-import"],
    "tasks": ["ai-split", "name-extract"], "migrations": ["import_previews", "upload_tokens"],
}

ROUTES = (
    RouteSpec("POST", "/api/assignments/<aid>/upload", "api_assignments_upload"),
    RouteSpec("POST", "/api/assignments/<aid>/upload/cancel", "api_assignments_upload_cancel",
              feature=""),
    RouteSpec("POST", "/api/assignments/<aid>/extract-names", "api_extract_names"),
    RouteSpec("POST", "/api/assignments/<aid>/ai-split", "api_ai_split_essay"),
    RouteSpec("POST", "/api/assignments/<aid>/import-jobs", "api_assignments_import_job"),
    RouteSpec("GET", "/api/assignments/<aid>/import-jobs/<job_id>", "api_assignments_import_job_get",
              feature=""),
    RouteSpec("POST", "/api/assignments/<aid>/import-jobs/<job_id>/cancel", "api_assignments_import_job_cancel",
              feature=""),
    RouteSpec("POST", "/api/assignments/<aid>/import-jobs/<job_id>/retry", "api_assignments_import_job_retry",
              feature=""),
    RouteSpec("POST", "/api/assignments/<aid>/ocr", "api_assignments_import_job",
              endpoint="api_assignments_ocr_compat"),
    RouteSpec("POST", "/api/assignments/<aid>/essays", "api_assignments_save_essays"),
    RouteSpec("POST", "/api/student/parse", "api_student_parse"),
)


@teacher_required
def api_assignments_upload(aid):
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    if not assignment:
        return jsonify({"error": "作业不存在"}), 404
    if assignment.get("lifecycleStatus") == "archived":
        return jsonify({"error": "已归档作业不可再导入作文"}), 409
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "没有收到文件"}), 400
    try:
        essays = parse_upload(file)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not essays:
        return jsonify({"error": "未从文件中识别出作文内容，请检查文件格式"}), 400
    for essay in essays:
        essay["submittedBy"] = match_student_candidate(essay, assignment.get("classId"))
    upload_token = stage_source_file(aid, request.user["username"], file)
    return jsonify({"essays": essays, "uploadToken": upload_token,
                    "students": class_students(assignment.get("classId"))})


@teacher_required
def api_assignments_import_job(aid):
    """Stage an upload and enqueue durable, single-threaded extraction/OCR."""
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    if not assignment:
        return jsonify({"error": "作业不存在"}), 404
    if assignment.get("lifecycleStatus") == "archived":
        return jsonify({"error": "已归档作业不可再导入作文"}), 409
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "没有收到文件"}), 400
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_IMAGE_EXTS and ext not in ("pdf", "docx", "txt", "md"):
        return jsonify({"error": "不支持的文件类型，请使用 PDF、Word（.docx）、TXT 或图片（PNG/JPG）"}), 400
    if feature_modules.state("ocr") != "enabled":
        return jsonify({
            "error": "智能导入已停用，请联系管理员",
            "code": "module_disabled", "module": "ocr",
            "state": feature_modules.state("ocr"),
        }), 503
    split_mode = (request.form.get("splitMode") or "auto").strip()
    if split_mode not in ("auto", "one_page", "two_pages"):
        return jsonify({"error": "作文纸拆分方式无效"}), 400
    if ext != "pdf" and split_mode != "auto":
        return jsonify({"error": "按页拆分只适用于 PDF 文件"}), 400
    quota_error = ai_quota_error()
    if quota_error:
        return quota_error
    try:
        upload_token = stage_source_file(aid, request.user["username"], file)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    job = db.create_ocr_job(
        aid, request.user["username"], upload_token, filename, ext, split_mode)
    if not job or job.get("error"):
        try:
            discard_staged_source(upload_token, aid, request.user["username"])
        except ValueError:
            pass
        status = 409 if job and job.get("error") == "archived" else 404
        return jsonify({"error": "作业状态已变化，请刷新后重试"}), status
    schedule_ocr_job(job["jobId"])
    return jsonify({
        "jobId": job["jobId"], "status": job["status"], "stage": job["stage"],
        "totalPages": job["totalPages"], "completedPages": job["completedPages"],
        "splitMode": job["splitMode"],
    }), 202


@teacher_required
def api_assignments_import_job_get(aid, job_id):
    job = db.get_ocr_job_owned(job_id, aid, request.user["username"])
    if not job:
        return jsonify({"error": "导入任务不存在"}), 404
    response = {key: job[key] for key in (
        "jobId", "status", "stage", "totalPages", "completedPages", "attempt",
        "splitMode",
        "queuedAt", "startedAt", "finishedAt", "error", "errorType", "errorAdvice",
    )}
    if job["status"] == "done":
        data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
        assignment = find_teacher_assignment(data, aid, request.user)
        if not assignment:
            return jsonify({"error": "作业不存在"}), 404
        essays = list((job.get("result") or {}).get("essays") or [])
        for essay in essays:
            essay["submittedBy"] = match_student_candidate(essay, assignment.get("classId"))
        response.update({
            "essays": essays, "students": class_students(assignment.get("classId")),
            "uploadToken": job["uploadToken"],
        })
    return jsonify(response)


@teacher_required
def api_assignments_import_job_cancel(aid, job_id):
    job = db.get_ocr_job_owned(job_id, aid, request.user["username"])
    if not job:
        return jsonify({"error": "导入任务不存在"}), 404
    result = db.request_ocr_cancel(job_id, aid, request.user["username"])
    if result and result["status"] == "cancelled":
        try:
            discard_staged_source(job["uploadToken"], aid, request.user["username"])
        except ValueError:
            pass
    if not result or not result["changed"]:
        return jsonify({"error": "当前任务状态不能取消", "status": job["status"]}), 409
    return jsonify({"ok": True, "status": result["status"]}), 202


@teacher_required
def api_assignments_import_job_retry(aid, job_id):
    if feature_modules.state("ocr") != "enabled":
        return jsonify({"error": "智能导入已停用", "code": "module_disabled"}), 503
    quota_error = ai_quota_error()
    if quota_error:
        return quota_error
    job = db.retry_ocr_job(job_id, aid, request.user["username"])
    if not job:
        return jsonify({"error": "导入任务不存在"}), 404
    if job.get("error") == "invalid_state":
        return jsonify({"error": "仅失败任务可以重试", "status": job["status"]}), 409
    schedule_ocr_job(job_id)
    return jsonify({
        "jobId": job_id, "status": job["status"], "stage": job["stage"],
        "totalPages": job["totalPages"], "completedPages": job["completedPages"],
        "splitMode": job["splitMode"],
    }), 202


@teacher_required
def api_assignments_upload_cancel(aid):
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    if not assignment:
        return jsonify({"error": "作业不存在"}), 404
    token = ((request.get_json(silent=True) or {}).get("uploadToken") or "").strip()
    if not token:
        return jsonify({"ok": True})
    try:
        discard_staged_source(token, aid, request.user["username"])
    except ValueError:
        pass
    return jsonify({"ok": True})


@teacher_required
def api_extract_names(aid):
    """批量用 AI 识别作文中的学生姓名（只处理还没识别出名字的篇目）"""
    body = request.get_json(silent=True) or {}
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    if not assignment:
        return jsonify({"error": "作业不存在"}), 404
    if assignment.get("lifecycleStatus") == "archived":
        return jsonify({"error": "已归档作业不可再识别名单"}), 409
    items = body.get("essays") or []
    if not items:
        return jsonify({"error": "没有可识别的作文"}), 400
    if not get_config()["api_key"]:
        return jsonify({"error": "AI 接口尚未配置，请联系管理员"}), 400
    quota_error = ai_quota_error()
    if quota_error:
        return quota_error

    already = sum(1 for it in items if not is_placeholder_name(it.get("studentName")))

    def work(it):
        if not is_placeholder_name(it.get("studentName")):
            return None
        name = extract_name_call(it.get("text") or "")
        return {"index": it.get("index"), "studentName": name} if name else None

    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for r in pool.map(work, items):
            if r:
                results.append(r)
    return jsonify({"names": results, "alreadyNamed": already})


@teacher_required
def api_ai_split_essay(aid):
    """AI 只识别单个候选作文的边界，正文切分由服务端完成。"""
    body = request.get_json(silent=True) or {}
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    if not assignment:
        return jsonify({"error": "作业不存在"}), 404
    if assignment.get("lifecycleStatus") == "archived":
        return jsonify({"error": "已归档作业不可再调整名单"}), 409
    if not get_config()["api_key"]:
        return jsonify({"error": "AI 接口尚未配置，请联系管理员"}), 400
    quota_error = ai_quota_error()
    if quota_error:
        return quota_error
    text = str(body.get("text") or "").strip()
    fallback_name = str(body.get("studentName") or "").strip()[:10]
    try:
        essays = request_ai_split(text, fallback_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        category, advice = classify_grade_error(exc)
        return jsonify({"error": f"AI 辅助切分失败（{category}）：{advice}"}), 502
    return jsonify({"essays": essays, "split": len(essays) > 1})


@teacher_required
def api_assignments_save_essays(aid):
    body = request.get_json(silent=True) or {}
    items = body.get("essays") or []
    if not items:
        return jsonify({"error": "名单为空"}), 400
    if "fileKey" in body:
        return jsonify({"error": "旧版文件标识已失效，请重新上传文件"}), 400
    upload_token = (body.get("uploadToken") or "").strip()
    with io_lock:
        data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
        assignment = find_teacher_assignment(data, aid, request.user)
        if not assignment:
            return jsonify({"error": "作业不存在"}), 404
        if assignment.get("lifecycleStatus") == "archived":
            return jsonify({"error": "已归档作业不可再修改名单"}), 409
        if any(e.get("status") in ("queued", "grading") for e in assignment.get("essays", [])):
            return jsonify({"error": "批改正在进行中，请等待完成"}), 409
        if upload_token:
            try:
                _staging_folder, staged_path, _staged_ext = staged_source_info(
                    upload_token, aid, request.user["username"])
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            with open(staged_path, "rb") as staged_file:
                source_hash = hashlib.sha256(staged_file.read()).hexdigest()
        else:
            source_hash = hashlib.sha256(
                "\0".join(str(item.get("text") or "") for item in items).encode("utf-8")
            ).hexdigest()

        normalized = []
        valid_students = class_student_usernames(assignment.get("classId"))
        seen_students = set()
        for i, item in enumerate(items, 1):
            name = (item.get("studentName") or "").strip() or f"学生{i}"
            text = (item.get("text") or "").strip()
            if not text:
                continue
            if len(text) > MAX_ESSAY_CHARS:
                return jsonify({"error": f"{name} 的作文超过 {MAX_ESSAY_CHARS:,} 个字符"}), 400
            submitted_by = ((item.get("submittedBy") or "").strip()
                            or match_student_username(name, assignment.get("classId")))
            if submitted_by and submitted_by not in valid_students:
                return jsonify({"error": f"{name} 绑定的学生不属于该班级"}), 400
            if submitted_by in seen_students:
                return jsonify({"error": f"同一名单不能重复绑定学生 {submitted_by}"}), 409
            if submitted_by:
                seen_students.add(submitted_by)
            normalized.append({
                "studentName": name, "text": text, "submittedBy": submitted_by,
                "studentTitle": str(item.get("studentTitle") or "").strip()[:120],
                "sourceFingerprint": f"{source_hash}:{i}",
            })
        if not normalized:
            return jsonify({"error": "没有有效的作文内容"}), 400

        existing_by_student = {
            essay.get("submittedBy"): essay
            for essay in assignment.get("essays", []) if essay.get("submittedBy")
        }
        existing_unbound_by_fingerprint = {
            essay.get("sourceFingerprint"): essay
            for essay in assignment.get("essays", [])
            if not essay.get("submittedBy") and essay.get("sourceFingerprint")
        }
        conflicts = [
            item["submittedBy"] for item in normalized
            if item["submittedBy"]
            and existing_by_student.get(item["submittedBy"], {}).get("submissionType") == "student_upload"
        ]
        if conflicts and body.get("overrideStudentSubmissions") is not True:
            return jsonify({
                "error": "名单包含学生自行提交的作文，必须明确确认覆盖并锁定",
                "conflicts": sorted(set(conflicts)),
            }), 409

        essays = []
        for item in normalized:
            submitted_by = item["submittedBy"]
            essay = (existing_by_student.get(submitted_by) if submitted_by else
                     existing_unbound_by_fingerprint.get(item["sourceFingerprint"]))
            if not essay:
                essay = {"id": "e" + secrets.token_hex(6)}
            essay.update({
                "studentName": item["studentName"], "text": item["text"],
                "chars": len(item["text"]), "status": "pending", "score": None, "level": "",
                "feedback": None, "aiFeedback": None, "feedbackVersion": 0,
                "feedbackEditedBy": "", "feedbackEditedAt": "",
                "error": "", "errorType": "", "errorAdvice": "", "gradedAt": "",
                "submittedBy": submitted_by,
                "submissionType": "teacher_upload",
                "lockedByTeacher": bool(submitted_by),
                "submissionOpen": False,
                "reopenedAt": "",
                "submittedAt": now_str(),
                "studentTitle": item["studentTitle"] or assignment.get("title", ""),
                "studentPrompt": assignment.get("prompt", ""),
                "contentHash": content_hash(item["text"]),
                "jobId": "",
                "attempt": 0,
                "queuedAt": "",
                "startedAt": "",
                "finishedAt": "",
                "durationSeconds": None,
                "resultStatus": "not_ready",
                "publishedFeedback": None,
                "resultPublishedAt": "",
                "sourceFingerprint": item["sourceFingerprint"],
            })
            essays.append(essay)
        imported_students = {essay.get("submittedBy") for essay in essays if essay.get("submittedBy")}
        imported_ids = {essay["id"] for essay in essays}
        unbound_existing = [
            essay for essay in assignment.get("essays", [])
            if (not essay.get("submittedBy") and essay.get("id") not in imported_ids
                and not str(essay.get("sourceFingerprint") or "").startswith(source_hash + ":"))
        ]
        untouched = [
            essay for essay in assignment.get("essays", [])
            if essay.get("submittedBy") and essay.get("submittedBy") not in imported_students
        ]
        assignment["essays"] = untouched + unbound_existing + essays

        new_source = ""
        if upload_token:
            new_source = promote_staged_source(upload_token, aid, request.user["username"])
            assignment["sourceFile"] = new_source
        save_json(ASSIGNMENTS_PATH, data)
        db.record_import_submission_versions(aid, [essay["id"] for essay in essays])
        if new_source:
            cleanup_assignment_sources(aid, keep=new_source)
    return jsonify({"ok": True, "count": len(essays)})


@student_required
def api_student_parse():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "没有收到文件"}), 400
    try:
        essays = parse_upload(file)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if len(essays) > 1:
        return jsonify({
            "error": f"文件中识别到 {len(essays)} 篇作文，学生端一次只能上传一篇，请改用单篇文件",
        }), 400
    text = essays[0]["text"] if essays else ""
    if not text:
        return jsonify({"error": "未从文件中识别出作文内容"}), 400
    if len(text) > MAX_ESSAY_CHARS:
        return jsonify({"error": f"作文正文不能超过 {MAX_ESSAY_CHARS:,} 个字符"}), 400
    return jsonify({"text": text})


def build_blueprint():
    return create_blueprint("uploads", __name__, ROUTES, default_feature="imports", namespace=globals())
