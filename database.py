"""Compatibility facade for the modular SQLite persistence layer."""

from essay_platform.persistence.storage import *  # noqa: F401,F403
from essay_platform.persistence.storage import (  # noqa: F401
    _current_academic_year, _dump, _json, _open, _utc_iso,
)
from essay_platform.repositories.assignments import *  # noqa: F401,F403
from essay_platform.repositories.auth import *  # noqa: F401,F403
from essay_platform.repositories.directory import *  # noqa: F401,F403
from essay_platform.repositories.grading import *  # noqa: F401,F403
from essay_platform.repositories.imports import *  # noqa: F401,F403
from essay_platform.repositories.operations import *  # noqa: F401,F403
from essay_platform.repositories.ocr import *  # noqa: F401,F403
from essay_platform.repositories.history import *  # noqa: F401,F403
