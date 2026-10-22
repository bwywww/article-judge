"""Compatibility entry point for the modular essay platform."""

from essay_platform.application import create_app
from essay_platform.bootstrap import initialize_runtime, main, runtime_initialized_dirs
from essay_platform.platform import *  # noqa: F401,F403 - compatibility for local tools
from essay_platform.adapters.ai import (  # noqa: F401
    ApiKeyError,
    LLMRequestError,
    call_llm,
    request_grade_result,
)
from essay_platform.services.student_imports import ai_review_student_import  # noqa: F401
from essay_platform.services.import_rules import (  # noqa: F401
    default_academic_year_start,
    grade_year_in_stage,
    normalize_import_class,
)
from essay_platform.services.upload_staging import (  # noqa: F401
    match_candidate_from_students,
    match_student_username,
)
from essay_platform.workers.grading import schedule_grade_job  # noqa: F401


app = create_app()


if __name__ == "__main__":
    main()
