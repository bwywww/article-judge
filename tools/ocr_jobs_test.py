"""Isolated async OCR/import state-machine tests; no real AI calls."""

from __future__ import annotations

import io
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

import database as db
from essay_platform.adapters import ai_ocr
from essay_platform.adapters.ai_ocr import OcrError
from essay_platform.adapters.documents import split_pdf_pages
from essay_platform.core.config import MAX_OCR_PAGES
from essay_platform.services import upload_staging
from essay_platform.services.upload_staging import staged_source_info
from essay_platform.workers import ocr as ocr_worker
from tools.regression_test import (
    get_json, login, post_file, post_json, reset_runtime, sample_docx_bytes,
)


def wait_job(client, assignment_id, job_id, token, terminal=("done", "error", "cancelled")):
    deadline = time.time() + 10
    latest = None
    while time.time() < deadline:
        response = get_json(
            client, f"/api/assignments/{assignment_id}/import-jobs/{job_id}", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        latest = response.get_json()
        if latest["status"] in terminal:
            return latest
        time.sleep(0.03)
    raise AssertionError(f"OCR job timeout: {latest}")


def image_bytes():
    image = Image.new("RGB", (180, 90), "white")
    ImageDraw.Draw(image).text((10, 30), "MOCK ESSAY", fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def mixed_pdf_bytes(*, blank_pages=1):
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(72, 760, "This text-layer page contains enough characters for direct extraction.")
    pdf.showPage()
    for _ in range(blank_pages):
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def blank_pdf_bytes(page_count):
    output = io.BytesIO()
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=595, height=842)
    writer.write(output)
    return output.getvalue()


def main():
    original_call = ocr_worker.call_ocr_vision
    original_quota = ocr_worker.ai_quota_status
    original_pixel_limit = ai_ocr.OCR_IMAGE_MAX_PIXELS
    original_upload_limit = upload_staging.MAX_UPLOAD_BYTES
    with tempfile.TemporaryDirectory(prefix="essay-ocr-") as tmp:
        reset_runtime(tmp)
        from app import app
        client = app.test_client()
        admin = login(client, "admin")
        teacher = login(client, "mr")

        created = post_json(client, "/api/assignments", {
            "title": "异步导入测试", "prompt": "写一件事", "classId": "class",
        }, teacher)
        assert created.status_code == 200, created.get_data(as_text=True)
        aid = created.get_json()["id"]

        direct = post_file(
            client, f"/api/assignments/{aid}/import-jobs", "file",
            "姓名：测试生\n这是一篇用于本地异步导入测试的完整作文正文。".encode(),
            "essays.txt", teacher)
        assert direct.status_code == 202, direct.get_data(as_text=True)
        direct_job = wait_job(client, aid, direct.get_json()["jobId"], teacher)
        assert direct_job["status"] == "done" and direct_job["essays"]
        assert direct_job["completedPages"] == direct_job["totalPages"] == 1

        docx = post_file(
            client, f"/api/assignments/{aid}/import-jobs", "file",
            sample_docx_bytes(), "essays.docx", teacher)
        assert docx.status_code == 202, docx.get_data(as_text=True)
        docx_job = wait_job(client, aid, docx.get_json()["jobId"], teacher)
        assert docx_job["status"] == "done" and len(docx_job["essays"]) == 2

        mixed_calls = {"count": 0}

        def mixed_ocr(_data_url):
            mixed_calls["count"] += 1
            return "姓名：扫描页学生\n这是只对扫描页调用 OCR 后返回的完整作文正文。"

        ocr_worker.call_ocr_vision = mixed_ocr
        mixed = post_file(
            client, f"/api/assignments/{aid}/import-jobs", "file",
            mixed_pdf_bytes(), "mixed.pdf", teacher)
        assert mixed.status_code == 202, mixed.get_data(as_text=True)
        mixed_job = wait_job(client, aid, mixed.get_json()["jobId"], teacher)
        assert mixed_job["status"] == "done"
        assert mixed_job["totalPages"] == mixed_job["completedPages"] == 2
        assert mixed_calls["count"] == 1

        page_mode = post_file(
            client, f"/api/assignments/{aid}/import-jobs", "file",
            mixed_pdf_bytes(), "page-mode.pdf", teacher,
            fields={"splitMode": "one_page"})
        assert page_mode.status_code == 202 and page_mode.get_json()["splitMode"] == "one_page"
        page_mode_job = wait_job(client, aid, page_mode.get_json()["jobId"], teacher)
        assert page_mode_job["status"] == "done" and len(page_mode_job["essays"]) == 2

        too_many = post_file(
            client, f"/api/assignments/{aid}/import-jobs", "file",
            blank_pdf_bytes(MAX_OCR_PAGES + 1), "too-many-pages.pdf", teacher)
        assert too_many.status_code == 202
        too_many_job = wait_job(client, aid, too_many.get_json()["jobId"], teacher)
        assert too_many_job["status"] == "error" and too_many_job["errorType"] == "页数过多"

        paper_pages = [
            "姓名：张三\n我的一天\n第一页正文内容足够用于测试。",
            "第二页续写内容足够用于测试。",
            "姓名：李四\n春天来了\n第三页正文内容足够用于测试。",
            "第四页续写内容足够用于测试。",
        ]
        paired = split_pdf_pages(paper_pages, "two_pages")
        assert len(paired) == 2 and paired[0]["sourcePages"] == [1, 2]
        assert paired[0]["studentName"] == "张三" and paired[0]["studentTitle"] == "我的一天"
        one_page = split_pdf_pages(paper_pages, "one_page")
        assert len(one_page) == 4 and one_page[2]["sourcePages"] == [3]

        ai_ocr.OCR_IMAGE_MAX_PIXELS = 100
        try:
            try:
                ai_ocr.image_to_data_url(image_bytes(), "pixel-bomb.png")
                raise AssertionError("pixel limit must reject an oversized decoded image")
            except OcrError as exc:
                assert exc.category == "image_too_large"
        finally:
            ai_ocr.OCR_IMAGE_MAX_PIXELS = original_pixel_limit

        upload_staging.MAX_UPLOAD_BYTES = 64
        try:
            oversized = post_file(
                client, f"/api/assignments/{aid}/import-jobs", "file",
                b"x" * 65, "oversized.txt", teacher)
            assert oversized.status_code == 400
        finally:
            upload_staging.MAX_UPLOAD_BYTES = original_upload_limit

        created_teacher = post_json(client, "/api/admin/users", {
            "role": "teacher", "employeeNo": "ocr2", "display_name": "OCR二号",
            "subject": "语文", "classIds": ["class"],
        }, admin)
        assert created_teacher.status_code == 200
        teacher2 = login(client, "ocr2")
        denied = get_json(
            client, f"/api/assignments/{aid}/import-jobs/{direct_job['jobId']}", teacher2)
        assert denied.status_code == 404

        calls = {"count": 0}
        def fail_once(_data_url):
            calls["count"] += 1
            raise OcrError("mock 503", category="http_error", transient=False, status_code=503)
        ocr_worker.call_ocr_vision = fail_once
        failed = post_file(
            client, f"/api/assignments/{aid}/import-jobs", "file",
            image_bytes(), "scan.png", teacher)
        assert failed.status_code == 202
        failed_job = wait_job(client, aid, failed.get_json()["jobId"], teacher)
        assert failed_job["status"] == "error" and failed_job["completedPages"] == 0

        ocr_worker.call_ocr_vision = lambda _data_url: "姓名：测试生\n这是重试后识别成功的完整作文正文。"
        retried = post_json(
            client, f"/api/assignments/{aid}/import-jobs/{failed_job['jobId']}/retry",
            {}, teacher)
        assert retried.status_code == 202, retried.get_data(as_text=True)
        done = wait_job(client, aid, failed_job["jobId"], teacher)
        assert done["status"] == "done" and done["completedPages"] == 1

        transient_calls = {"count": 0}

        def transient_twice(_data_url):
            transient_calls["count"] += 1
            if transient_calls["count"] < 3:
                raise OcrError(
                    "mock 503", category="http_error", transient=True, status_code=503)
            return "姓名：退避学生\n这是经历两次页级退避后成功识别的作文正文。"

        ocr_worker.call_ocr_vision = transient_twice
        transient = post_file(
            client, f"/api/assignments/{aid}/import-jobs", "file",
            image_bytes(), "retry.png", teacher)
        transient_job = wait_job(client, aid, transient.get_json()["jobId"], teacher)
        assert transient_job["status"] == "done" and transient_calls["count"] == 3
        assert db.list_ocr_pages(transient_job["jobId"])[0]["retryCount"] == 2

        quota_checks = {"count": 0}
        quota_ocr_calls = {"count": 0}

        def quota_status():
            quota_checks["count"] += 1
            return {"blocked": quota_checks["count"] >= 2}

        def quota_ocr(_data_url):
            quota_ocr_calls["count"] += 1
            return (
                f"姓名：配额学生{quota_ocr_calls['count']}\n"
                "这是配额续跑测试的扫描作文正文。"
            )

        ocr_worker.ai_quota_status = quota_status
        ocr_worker.call_ocr_vision = quota_ocr
        quota = post_file(
            client, f"/api/assignments/{aid}/import-jobs", "file",
            blank_pdf_bytes(2), "quota.pdf", teacher)
        quota_job = wait_job(client, aid, quota.get_json()["jobId"], teacher)
        assert quota_job["status"] == "error" and quota_job["completedPages"] == 1
        assert quota_ocr_calls["count"] == 1
        ocr_worker.ai_quota_status = lambda: {"blocked": False}
        retried_quota = post_json(
            client, f"/api/assignments/{aid}/import-jobs/{quota_job['jobId']}/retry",
            {}, teacher)
        assert retried_quota.status_code == 202
        quota_done = wait_job(client, aid, quota_job["jobId"], teacher)
        assert quota_done["status"] == "done" and quota_done["completedPages"] == 2
        assert quota_ocr_calls["count"] == 2

        entered = threading.Event()
        release = threading.Event()

        def blocking_ocr(_data_url):
            entered.set()
            assert release.wait(5)
            return "姓名：取消学生\n这段文字会在取消请求后返回，但任务必须进入取消状态。"

        ocr_worker.call_ocr_vision = blocking_ocr
        cancelling = post_file(
            client, f"/api/assignments/{aid}/import-jobs", "file",
            image_bytes(), "cancel.png", teacher)
        cancelling_id = cancelling.get_json()["jobId"]
        assert entered.wait(5)
        cancelling_record = db.get_ocr_job_owned(cancelling_id, aid, "mr")
        _folder, cancelling_path, _ext = staged_source_info(
            cancelling_record["uploadToken"], aid, "mr")
        draining = post_json(
            client, "/api/admin/modules/ocr/state", {"state": "disabled"}, admin)
        assert draining.status_code == 200
        module = draining.get_json()["module"]
        assert module["status"] == "draining"
        module_list = get_json(client, "/api/admin/modules", admin).get_json()["modules"]
        assert next(item for item in module_list if item["key"] == "ocr")["activeJobs"] >= 1
        cancelled = post_json(
            client, f"/api/assignments/{aid}/import-jobs/{cancelling_id}/cancel",
            {}, teacher)
        assert cancelled.status_code == 202
        release.set()
        cancelled_job = wait_job(client, aid, cancelling_id, teacher)
        assert cancelled_job["status"] == "cancelled"
        assert not Path(cancelling_path).exists()
        modules = get_json(client, "/api/admin/modules", admin).get_json()["modules"]
        assert next(item for item in modules if item["key"] == "ocr")["status"] == "disabled"
        assert post_json(
            client, "/api/admin/modules/ocr/state", {"state": "enabled"}, admin
        ).status_code == 200

        from essay_platform.modules import uploads as uploads_module
        original_schedule = uploads_module.schedule_ocr_job
        uploads_module.schedule_ocr_job = lambda _job_id: False
        try:
            stale = post_file(
                client, f"/api/assignments/{aid}/import-jobs", "file",
                image_bytes(), "restart.png", teacher)
            stale_id = stale.get_json()["jobId"]
        finally:
            uploads_module.schedule_ocr_job = original_schedule
        with db.connection(write=True) as conn:
            conn.execute(
                "UPDATE ocr_jobs SET status='ocr',stage='ocr' WHERE id=?", (stale_id,))
        db.initialize_ocr_pages(stale_id, 2)
        db.finish_ocr_page(stale_id, 1, "已完成页", used_ocr=True)
        assert stale_id in db.recover_ocr_jobs()
        recovered = db.get_ocr_job_owned(stale_id, aid, "mr")
        assert recovered["status"] == "queued" and recovered["completedPages"] == 1
        pages = db.list_ocr_pages(stale_id)
        assert pages[0]["status"] == "ocr_done" and pages[1]["status"] == "pending"
        assert post_json(
            client, f"/api/assignments/{aid}/import-jobs/{stale_id}/cancel", {}, teacher
        ).status_code == 202

        disabled = post_json(client, "/api/admin/modules/ocr/state", {"state": "disabled"}, admin)
        assert disabled.status_code == 200
        blocked = post_file(
            client, f"/api/assignments/{aid}/import-jobs", "file",
            b"local text is still a new smart-import job", "blocked.txt", teacher)
        assert blocked.status_code == 503
        assert post_json(client, "/api/admin/modules/ocr/state", {"state": "enabled"}, admin).status_code == 200
    ocr_worker.call_ocr_vision = original_call
    ocr_worker.ai_quota_status = original_quota
    ai_ocr.OCR_IMAGE_MAX_PIXELS = original_pixel_limit
    upload_staging.MAX_UPLOAD_BYTES = original_upload_limit
    print("ocr_jobs_test.py: OK")


if __name__ == "__main__":
    main()
