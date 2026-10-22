"""Temporary compatibility exports for local tools and incremental module imports."""

from essay_platform.core.config import *  # noqa: F401,F403
from essay_platform.core.state import *  # noqa: F401,F403
from essay_platform.core.time import *  # noqa: F401,F403
from essay_platform.core.audit import *  # noqa: F401,F403
from essay_platform.core.auth import *  # noqa: F401,F403
from essay_platform.persistence.datasets import *  # noqa: F401,F403
from essay_platform.persistence.datasets import _load_json_file, _save_json_file  # noqa: F401
from essay_platform.services.assignments import *  # noqa: F401,F403
from essay_platform.services.directory import *  # noqa: F401,F403
from essay_platform.services.migration import *  # noqa: F401,F403

# Third-party and service compatibility names used by route modules while they
# transition to explicit imports.
import copy
import contextlib
import datetime
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import jsonify, redirect, render_template, request, send_file, send_from_directory, has_request_context
from openpyxl import Workbook, load_workbook
from pypdf import PdfReader
try:
    from docx import Document as load_docx
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph as DocxParagraph
except ImportError:
    load_docx = DocxTable = DocxParagraph = None

import database as db
from essay_platform.adapters.reports import build_assignment_report, build_report, safe_filename
from essay_platform.core import modules as feature_modules
from essay_platform.core.secrets import public_secret_status, redact_secrets
from essay_platform.services import academic_years as academic_year_service
from essay_platform.services import analytics as analytics_service
from essay_platform.services import recovery as recovery_service
from essay_platform.services import revisions as revision_service
from essay_platform.services import showcase as showcase_service
