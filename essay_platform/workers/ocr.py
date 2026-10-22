"""Single-threaded, restart-safe OCR/import worker."""

from __future__ import annotations

import os
import re
import time

from pypdf import PdfReader

import database as db
from essay_platform.adapters.ai_ocr import (
    OcrError, call_ocr_vision, classify_ocr_error, image_to_data_url,
    pdf_page_count, pdf_page_to_data_url,
)
from essay_platform.adapters.documents import (
    ALLOWED_IMAGE_EXTS, parse_docx_content, split_lines_to_essays,
    split_pdf_pages, split_pages_to_essays,
)
from essay_platform.core import modules as feature_modules
from essay_platform.core.config import MAX_OCR_PAGES, MAX_UPLOAD_BYTES
from essay_platform.core.state import io_lock, ocr_executor, scheduled_ocr_jobs
from essay_platform.services.operations import ai_quota_status
from essay_platform.services.upload_staging import discard_staged_source, staged_source_info


def _cancel_and_cleanup(context):
    with io_lock:
        db.mark_ocr_cancelled(context["jobId"])
    try:
        discard_staged_source(
            context["uploadToken"], context["assignmentId"], context["username"])
    except ValueError:
        pass


def _check_cancel(context):
    if db.ocr_cancel_requested(context["jobId"]):
        _cancel_and_cleanup(context)
        return True
    return False


def _check_quota():
    if ai_quota_status()["blocked"]:
        raise OcrError("AI 配额已用尽", category="quota")


def _ocr_page_with_retry(job_id, page_no, data_url):
    last_error = None
    for retry in range(3):
        try:
            _check_quota()
            return call_ocr_vision(data_url)
        except OcrError as exc:
            last_error = exc
            if not exc.transient or retry >= 2:
                raise
            db.record_ocr_page_retry(job_id, page_no, str(exc))
            time.sleep(2 ** retry)
    raise last_error  # pragma: no cover


def _parse_direct(path, ext):
    with open(path, "rb") as source:
        content = source.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    if ext == "docx":
        return parse_docx_content(content)
    text = content.decode("utf-8", errors="replace")
    return split_lines_to_essays(text.splitlines())


def _process_pdf(context, path):
    job_id = context["jobId"]
    count = pdf_page_count(path, MAX_OCR_PAGES)
    db.initialize_ocr_pages(job_id, count)
    existing = {page["pageNo"]: page for page in db.list_ocr_pages(job_id)}
    reader = PdfReader(path)
    for index in range(count):
        page_no = index + 1
        cached = existing.get(page_no, {})
        if cached.get("status") in ("text_extracted", "ocr_done"):
            continue
        if _check_cancel(context):
            return None
        try:
            layer_text = reader.pages[index].extract_text() or ""
            if len(re.sub(r"\s+", "", layer_text)) >= 20:
                db.finish_ocr_page(job_id, page_no, layer_text, used_ocr=False)
                continue
            db.set_ocr_stage(job_id, "ocr")
            data_url = pdf_page_to_data_url(path, index)
            page_text = _ocr_page_with_retry(job_id, page_no, data_url)
            db.finish_ocr_page(job_id, page_no, page_text, used_ocr=True)
        except Exception as exc:
            db.fail_ocr_page(job_id, page_no, str(exc))
            raise
    pages = [page["text"] for page in db.list_ocr_pages(job_id)]
    return split_pdf_pages(pages, context.get("splitMode", "auto"))


def _process_image(context, path):
    job_id = context["jobId"]
    db.initialize_ocr_pages(job_id, 1)
    page = db.list_ocr_pages(job_id)[0]
    if page["status"] in ("text_extracted", "ocr_done"):
        return split_pages_to_essays([page["text"]])
    if _check_cancel(context):
        return None
    with open(path, "rb") as source:
        content = source.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    db.set_ocr_stage(job_id, "ocr")
    try:
        data_url = image_to_data_url(content, context["filename"])
        page_text = _ocr_page_with_retry(job_id, 1, data_url)
        db.finish_ocr_page(job_id, 1, page_text, used_ocr=True)
    except Exception as exc:
        db.fail_ocr_page(job_id, 1, str(exc))
        raise
    return split_pages_to_essays([page_text])


def run_ocr_job(job_id):
    context = None
    try:
        with io_lock:
            context = db.claim_ocr_job(job_id)
        if not context:
            return
        if _check_cancel(context):
            return
        _folder, path, ext = staged_source_info(
            context["uploadToken"], context["assignmentId"], context["username"])
        if os.path.getsize(path) > MAX_UPLOAD_BYTES:
            raise ValueError(f"文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
        if ext == "pdf":
            essays = _process_pdf(context, path)
        elif ext in ALLOWED_IMAGE_EXTS:
            essays = _process_image(context, path)
        else:
            essays = _parse_direct(path, ext)
            db.initialize_ocr_pages(job_id, 1)
            joined = "\n\n".join(item.get("text", "") for item in essays)
            db.finish_ocr_page(job_id, 1, joined, used_ocr=False)
        if essays is None or _check_cancel(context):
            return
        if not essays:
            raise OcrError("未从文件中识别出任何文字", category="no_text")
        db.finish_ocr_job(job_id, {"essays": essays})
    except Exception as exc:
        if context and db.ocr_cancel_requested(job_id):
            _cancel_and_cleanup(context)
        else:
            error_type, advice = classify_ocr_error(exc)
            db.fail_ocr_job(job_id, str(exc), error_type, advice)
    finally:
        with io_lock:
            scheduled_ocr_jobs.discard(job_id)
        if db.is_ready():
            feature_modules.refresh_draining_modules()


def schedule_ocr_job(job_id):
    with io_lock:
        if job_id in scheduled_ocr_jobs:
            return False
        scheduled_ocr_jobs.add(job_id)
    try:
        ocr_executor.submit(run_ocr_job, job_id)
        return True
    except RuntimeError as exc:
        with io_lock:
            scheduled_ocr_jobs.discard(job_id)
        error_type, advice = classify_ocr_error(exc)
        db.fail_ocr_job(job_id, str(exc), error_type, advice)
        return False


def reset_stale_ocr():
    with io_lock:
        scheduled_ocr_jobs.clear()
        recovered = db.recover_ocr_jobs()
    for job_id in recovered:
        schedule_ocr_job(job_id)
