"""Start Waitress with isolated data, verify login, then stop it."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request_json(url, payload=None, headers=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = dict(headers or {})
    if data:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=request_headers,
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        raw = response.read()
        return response.status, json.loads(raw.decode("utf-8")) if raw else None


def main():
    waitress = shutil.which("waitress-serve")
    if not waitress:
        raise RuntimeError("缺少 waitress，请先执行 python -m pip install -r requirements.txt")

    port = free_port()
    with tempfile.TemporaryDirectory(prefix="essay-wsgi-") as data_dir:
        env = os.environ.copy()
        env["ESSAY_DATA_DIR"] = data_dir
        env["APP_ENV"] = "test"
        env["ADMIN_PASSWORD"] = "server-test-pass"
        env["TRUST_PROXY_HEADERS"] = "1"
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            [waitress, f"--listen=127.0.0.1:{port}", "--threads=8",
             "--trusted-proxy=127.0.0.1",
             "--trusted-proxy-headers=x-forwarded-for x-forwarded-proto",
             "wsgi:app"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=creationflags,
        )
        try:
            deadline = time.time() + 12
            while time.time() < deadline:
                if process.poll() is not None:
                    output = process.stdout.read() if process.stdout else ""
                    raise RuntimeError(f"Waitress 提前退出：\n{output}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.15)
            else:
                raise RuntimeError("Waitress 启动超时")

            proxy_headers = {
                "X-Forwarded-For": "203.0.113.10",
                "X-Forwarded-Proto": "https",
            }
            status, body = request_json(
                f"http://127.0.0.1:{port}/api/login",
                {"username": "admin", "password": "server-test-pass"},
                proxy_headers,
            )
            assert status == 200 and body["role"] == "admin"
            audit_status, audit = request_json(
                f"http://127.0.0.1:{port}/api/admin/audit?page=1&pageSize=20",
                headers={**proxy_headers, "X-Token": body["token"]},
            )
            login_event = next(item for item in audit["items"]
                               if item["type"] == "login_succeeded")
            assert audit_status == 200 and login_event["ipAddress"] == "203.0.113.10", (
                audit_status, login_event)
            print(f"wsgi_smoke_test.py: OK (127.0.0.1:{port})")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
