"""Compatibility exports for the split persistence implementation."""

from essay_platform.persistence.connection import *  # noqa: F401,F403
from essay_platform.persistence.connection import (  # noqa: F401
    _current_academic_year, _dump, _json, _open, _utc_iso,
)
from essay_platform.persistence.schema import *  # noqa: F401,F403
from essay_platform.persistence.schema import _create_schema  # noqa: F401
from essay_platform.persistence.legacy_json import *  # noqa: F401,F403
from essay_platform.persistence.legacy_json import _load_user_row  # noqa: F401
from essay_platform.persistence.migrations import *  # noqa: F401,F403
