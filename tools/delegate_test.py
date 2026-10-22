"""Offline checks for the restricted DeepSeek delegation boundary."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import deepseek_delegate as delegate


def expect_error(fn, text):
    try:
        fn()
    except ValueError as exc:
        assert text in str(exc), exc
    else:
        raise AssertionError(f"Expected ValueError containing {text!r}")


def main():
    assert delegate.validate_target_path("static/js/student_progress.js") == "static/js/student_progress.js"
    expect_error(lambda: delegate.validate_target_path("../data/app.db"), "路径无效")
    expect_error(lambda: delegate.validate_target_path("data/output.js"), "禁止生成")
    expect_error(lambda: delegate.validate_target_path("deepseekv4.txt"), "禁止生成")

    valid = """合同理解\n```diff\n--- a/static/js/student.js\n+++ b/static/js/student.js\n@@\n-old\n+new\n```\n"""
    delegate.validate_draft_code(valid, ["static/js/student.js"])
    expect_error(
        lambda: delegate.validate_draft_code(
            valid.replace("static/js/student.js", "database.py"),
            ["static/js/student.js"],
        ),
        "越出目标文件",
    )
    expect_error(lambda: delegate.validate_draft_code("没有补丁", ["static/js/student.js"]), "缺少统一diff")

    prompt = delegate.build_prompt(
        "draft-code", [("static/js/student.js", "const ok = true;")],
        "只增加空状态", ["static/js/student.js"],
    )
    assert "允许修改的目标文件：static/js/student.js" in prompt
    assert "不得出现任何diff文件头" in prompt
    print("delegate_test.py: OK")


if __name__ == "__main__":
    main()
