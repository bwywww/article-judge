"""Production WSGI entry point.

Run one process only because grading jobs are persisted in SQLite but dispatched
by one in-process global worker pool, for example:
    waitress-serve --listen=127.0.0.1:8080 --threads=8 \
      --trusted-proxy=127.0.0.1 \
      --trusted-proxy-headers='x-forwarded-for x-forwarded-proto' wsgi:app
"""

import os


os.environ.setdefault("APP_ENV", "production")

from app import app, initialize_runtime


initialize_runtime()
