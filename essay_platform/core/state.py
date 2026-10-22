"""Process-local synchronization and worker state."""

import threading
from concurrent.futures import ThreadPoolExecutor

from essay_platform.core.config import GRADE_CONCURRENCY

io_lock = threading.RLock()  # 可重入锁：load->修改->save 序列要原子化，防止并发批改互相覆盖
sessions = {}                # token -> username
in_flight = set()            # 当前进程真正执行中的 jobId，仅用于观测
scheduled_jobs = set()       # 已提交到全局线程池（含排队中）的 jobId
student_submission_in_flight = set()
upgrade_in_flight = set()
grade_executor = ThreadPoolExecutor(max_workers=GRADE_CONCURRENCY,
                                    thread_name_prefix="essay-grade")
ocr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="essay-ocr")
scheduled_ocr_jobs = set()
llm_task_local = threading.local()


def extension_state():
    """Expose process-local state through Flask's extension registry."""
    return {
        "executor": grade_executor,
        "inFlight": in_flight,
        "scheduledJobs": scheduled_jobs,
        "studentSubmissions": student_submission_in_flight,
        "upgrades": upgrade_in_flight,
        "ocrExecutor": ocr_executor,
        "scheduledOcrJobs": scheduled_ocr_jobs,
    }
