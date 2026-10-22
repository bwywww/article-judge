"""Delegate low-risk, read-only development chores to DeepSeek.

The tool never edits tracked files. Results are written below .ai-work/ for
Codex review and are deliberately excluded from Git.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from essay_platform.core.secrets import SecretConfigError, get_ai_secret, redact_secrets


ALLOWED_SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".bat"}
FORBIDDEN_PARTS = {
    ".git", ".ai-work", "data", "testdata", "uploads", "backups", "__pycache__",
}
FORBIDDEN_NAMES = {"deepseekv4.txt", "app.db", ".env", ".env.local"}
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{12,}")

MODE_PROMPTS = {
    "inventory": (
        "整理路由、模块、共享状态和重复代码清单。只描述事实和低风险拆分候选，"
        "不要设计认证、数据库迁移、并发或部署方案。"
    ),
    "draft-tests": (
        "根据源码起草测试场景和测试代码候选。不要修改业务代码，不要声称测试已经运行。"
    ),
    "docs": (
        "根据源码起草简洁中文文档。不要虚构尚未实现的功能。"
    ),
    "review": (
        "进行只读静态审阅，列出可复现问题和相关文件。权限、数据库、并发问题只能标记给Codex复核，"
        "不要给出可直接应用的高风险补丁。"
    ),
    "draft-code": (
        "只为明确允许的目标文件起草完整统一diff。不得直接修改项目，不得运行Git或测试，"
        "不得改变给定接口合同；遇到冲突时停止写补丁并报告。"
    ),
}


def git_tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return {item for item in result.stdout.decode("utf-8").split("\0") if item}


def path_is_allowed(relative_path: str) -> bool:
    path = Path(relative_path)
    if path.name in FORBIDDEN_NAMES or path.suffix.lower() not in ALLOWED_SUFFIXES:
        return False
    return not any(part.lower() in FORBIDDEN_PARTS for part in path.parts)


def select_files(requested: list[str], *, max_bytes: int) -> list[tuple[str, str]]:
    tracked = git_tracked_files()
    candidates = sorted(tracked if not requested else {Path(item).as_posix() for item in requested})
    selected: list[tuple[str, str]] = []
    total = 0
    for relative in candidates:
        if relative not in tracked:
            raise ValueError(f"不是Git跟踪文件：{relative}")
        if not path_is_allowed(relative):
            raise ValueError(f"禁止发送该文件：{relative}")
        absolute = (ROOT / relative).resolve()
        if ROOT not in absolute.parents:
            raise ValueError(f"文件越出项目目录：{relative}")
        content = absolute.read_text(encoding="utf-8-sig")
        if SECRET_PATTERN.search(content):
            raise ValueError(f"文件疑似包含密钥，已停止：{relative}")
        size = len(content.encode("utf-8"))
        if total + size > max_bytes:
            if requested:
                raise ValueError(f"所选源码超过 {max_bytes} 字节限制")
            continue
        selected.append((relative, content))
        total += size
    if not selected:
        raise ValueError("没有符合安全规则的源码文件")
    return selected


def validate_target_path(value: str) -> str:
    raw = Path(value).as_posix()
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"目标文件路径无效：{value}")
    relative = raw[2:] if raw.startswith("./") else raw
    if not path_is_allowed(relative):
        raise ValueError(f"禁止生成该目标文件：{relative}")
    return relative


def build_prompt(mode: str, files: list[tuple[str, str]], task: str,
                 targets: list[str]) -> str:
    source = "\n\n".join(
        f"===== FILE: {path} =====\n{content}" for path, content in files
    )
    target_text = "、".join(targets) if targets else "不允许生成代码补丁"
    code_rules = ""
    if mode == "draft-code":
        code_rules = (
            "\n允许修改的目标文件：" + target_text + "\n"
            "候选代码必须使用一个```diff代码块输出完整统一diff。"
            "除允许目标外不得出现任何diff文件头；不得使用伪代码、省略号或‘其余类似’。"
            "输出顺序固定为：合同理解、修改文件、统一diff、测试草稿、风险与未完成项。\n"
        )
    return (
        "你是开发辅助整理员，输出是未经信任的草稿，最终由Codex逐行审核。\n"
        "禁止请求、推断或输出任何密钥、真实学生资料、作文内容、数据库内容或部署凭据。\n"
        f"任务类型：{mode}\n规则：{MODE_PROMPTS[mode]}\n"
        f"补充任务：{task or '无'}\n{code_rules}\n{source}"
    )


def call_deepseek(prompt: str, *, timeout: int = 120) -> tuple[str, dict]:
    key, source = get_ai_secret("code_develop")
    if not key:
        raise SecretConfigError("code_develop 密钥尚未配置")
    base_url = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("CODE_DEVELOP_MODEL") or "deepseek-v4-flash"
    response = requests.post(
        base_url + "/chat/completions",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "只完成低风险开发杂务，不直接修改项目。"},
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": 6000,
            "stream": False,
        },
        timeout=timeout,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"DeepSeek 返回 {response.status_code}：{redact_secrets(response.text[:300])}"
        )
    payload = response.json()
    content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DeepSeek 未返回有效草稿")
    return content, {
        "profile": "code_develop", "source": source, "model": payload.get("model", model),
        "usage": payload.get("usage", {}),
    }


def validate_draft_code(content: str, targets: list[str]):
    if "```diff" not in content:
        raise ValueError("DeepSeek候选代码缺少统一diff代码块")
    allowed = set(targets)
    headers = re.findall(r"^(?:---|\+\+\+)\s+([^\t\r\n]+)", content, flags=re.MULTILINE)
    changed = set()
    for header in headers:
        value = header.strip()
        if value == "/dev/null":
            continue
        if value.startswith(("a/", "b/")):
            value = value[2:]
        relative = Path(value).as_posix()
        if relative not in allowed:
            raise ValueError(f"DeepSeek补丁越出目标文件：{relative}")
        changed.add(relative)
    if not changed:
        raise ValueError("DeepSeek候选代码没有有效目标文件")


def write_result(mode: str, files: list[tuple[str, str]], content: str, metadata: dict,
                 targets: list[str]) -> Path:
    task_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    output_dir = ROOT / ".ai-work" / task_id
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "result.md").write_text(content, encoding="utf-8")
    manifest = {
        "taskId": task_id, "mode": mode, "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": [path for path, _content in files], "targets": targets, **metadata,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return output_dir


def parse_args():
    parser = argparse.ArgumentParser(description="只读DeepSeek开发杂务委派工具")
    parser.add_argument("mode", choices=sorted(MODE_PROMPTS))
    parser.add_argument("--files", nargs="*", default=[], help="仅允许Git跟踪源码")
    parser.add_argument("--task", default="", help="补充任务说明")
    parser.add_argument("--targets", nargs="*", default=[], help="draft-code允许修改的目标文件")
    parser.add_argument("--max-bytes", type=int, default=240_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if (os.environ.get("APP_ENV") or "development").lower() != "development":
        print("开发委派工具只允许在 APP_ENV=development 下运行", file=sys.stderr)
        return 2
    try:
        targets = [validate_target_path(item) for item in args.targets]
        if args.mode == "draft-code" and not targets:
            raise ValueError("draft-code必须通过 --targets 指定目标文件")
        if args.mode != "draft-code" and targets:
            raise ValueError("只有draft-code模式可以指定 --targets")
        files = select_files(args.files, max_bytes=max(10_000, min(args.max_bytes, 500_000)))
        content, metadata = call_deepseek(build_prompt(args.mode, files, args.task, targets))
        if args.mode == "draft-code":
            validate_draft_code(content, targets)
        output_dir = write_result(args.mode, files, content, metadata, targets)
    except Exception as exc:
        print(redact_secrets(exc), file=sys.stderr)
        return 1
    print(f"DeepSeek草稿已保存：{output_dir.relative_to(ROOT)}")
    print("该结果必须由Codex逐行审核，不能直接应用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
