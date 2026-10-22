"""Persistent global grading worker pool."""

import time

import database as db
from essay_platform.core import modules as feature_modules
from essay_platform.core.state import grade_executor, in_flight, io_lock, scheduled_jobs
from essay_platform.adapters.ai import classify_grade_error, request_grade_result
from essay_platform.adapters.documents import is_placeholder_name

def discard_scheduled_job(job_id):
    with io_lock:
        scheduled_jobs.discard(job_id)
    if db.is_ready():
        feature_modules.refresh_draining_modules()


def _grading_future_done(future, job_id):
    """线程池回调：丢弃调度标记，并暴露未被 run_grading_job 捕获的异常。"""
    try:
        future.exception()
    except Exception as exc:  # pragma: no cover - 防御性，exception() 本身极少抛
        print(f"[批改] job={job_id[:8]} future 状态检查异常：{exc}")
    discard_scheduled_job(job_id)


def run_grading_job(job_id):
    """从持久队列原子领取一个任务，并在版本校验后写回结果。

    整个执行（领取、AI 调用、写回）都包在异常兜底里：任何一步失败都把
    job 落为 error，避免任务卡死在 running/grading 只能靠重启恢复。
    """
    elapsed = 0.0
    try:
        with io_lock:
            context = db.claim_grading_job(job_id)
        if not context:
            return
        with io_lock:
            in_flight.add(job_id)
        started = time.time()
        try:
            result = request_grade_result(
                context["title"], context["prompt"], context["criteria"],
                context["wordLimit"], context["text"],
                on_attempt=lambda attempt: update_persistent_job_attempt(job_id, attempt),
            )
            error = ""
        except Exception as exc:
            result = None
            error = str(exc)
        finally:
            with io_lock:
                in_flight.discard(job_id)

        elapsed = round(time.time() - started, 1)
        print(f"[批改] {context['submissionId']} job={job_id[:8]} 耗时 {elapsed:.1f}s "
              f"{'成功' if not error else '失败: ' + error[:120]}")
        if error:
            error_type, advice = classify_grade_error(error)
            with io_lock:
                db.finish_grading_job_error(
                    job_id, error, error_type, advice, duration_seconds=elapsed)
            return
        replacement_name = ""
        if (context["targetType"] == "assignment"
                and is_placeholder_name(context.get("studentName", ""))
                and result.get("studentName")):
            replacement_name = result["studentName"]
        with io_lock:
            db.finish_grading_job_success(
                job_id, result, duration_seconds=elapsed,
                history_limit=0,
                replacement_student_name=replacement_name,
            )
    except Exception as exc:
        # 写回/分类等路径的兜底：不让 job 永久卡在 running。
        error_type, advice = classify_grade_error(str(exc))
        try:
            with io_lock:
                db.finish_grading_job_error(
                    job_id, str(exc), error_type, advice, duration_seconds=elapsed)
        except Exception as writeback_error:
            print(f"[批改] job={job_id[:8]} 兜底写回失败：{writeback_error}")
        print(f"[批改] job={job_id[:8]} 未捕获异常，已置 error：{str(exc)[:200]}")


def update_persistent_job_attempt(job_id, attempt):
    with io_lock:
        db.update_grading_attempt(job_id, attempt)


def schedule_grade_job(job_id):
    with io_lock:
        if job_id in scheduled_jobs:
            return False
        scheduled_jobs.add(job_id)
    try:
        future = grade_executor.submit(run_grading_job, job_id)
        future.add_done_callback(
            lambda f, jid=job_id: _grading_future_done(f, jid))
        return True
    except RuntimeError as exc:
        with io_lock:
            scheduled_jobs.discard(job_id)
        error_type, advice = classify_grade_error(exc)
        with io_lock:
            db.finish_grading_job_error(
                job_id, str(exc), error_type, advice, duration_seconds=0)
        return False


def reserve_grade_jobs(assignment_id, essay_ids=None, force=False):
    """原子预留任务后提交到全局线程池，返回 (新增任务数, 已存在任务数)。"""
    with io_lock:
        reserved = db.enqueue_assignment_jobs(
            assignment_id, essay_ids=essay_ids, force=force)
    for job in reserved["jobs"]:
        schedule_grade_job(job["jobId"])
    return len(reserved["jobs"]), reserved["existing"]


def start_batch(assignment_id, force=False):
    return reserve_grade_jobs(assignment_id, force=force)


def reset_stale_grading():
    """服务启动时恢复持久任务；运行中任务重新排队，旧结果不会丢失。"""
    with io_lock:
        scheduled_jobs.clear()
        in_flight.clear()
        recovered = db.recover_grading_jobs()
    for job_id in recovered:
        schedule_grade_job(job_id)
