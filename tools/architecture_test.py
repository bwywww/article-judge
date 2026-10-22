"""Static dependency checks for the modular monolith."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def main():
    package = ROOT / "essay_platform"
    assert not (package / "runtime.py").exists(), "runtime.py must not return"
    assert len((ROOT / "database.py").read_text(encoding="utf-8").splitlines()) <= 30
    assert len((ROOT / "reporting.py").read_text(encoding="utf-8").splitlines()) <= 10

    for path in (package / "modules").glob("*.py"):
        if path.name in {"__init__.py", "base.py"}:
            continue
        names = set(imports(path))
        assert "essay_platform.platform" not in names, f"route imports compatibility facade: {path}"
        assert "sqlite3" not in names and "requests" not in names, f"route owns adapter work: {path}"
        assert ".execute(" not in path.read_text(encoding="utf-8"), f"route contains SQL: {path}"
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else ""
                assert name not in {"save_users_and_classes"}, (
                    f"HTTP route calls full directory snapshot sync: {path}:{node.lineno}"
                )

    for path in (package / "services").glob("*.py"):
        names = set(imports(path))
        assert not any(name == "flask" or name.startswith("flask.") for name in names), (
            f"service depends on Flask: {path}"
        )

    forbidden_repository_imports = {"flask", "requests", "openpyxl", "pypdf", "docx"}
    for path in (package / "repositories").glob("*.py"):
        names = set(imports(path))
        assert not any(name.split(".")[0] in forbidden_repository_imports for name in names), (
            f"repository crosses persistence boundary: {path}"
        )

    for path in (package / "core").glob("*.py"):
        names = set(imports(path))
        assert not any(name.startswith("essay_platform.modules") for name in names), (
            f"core depends on HTTP module: {path}"
        )

    route_specs = []
    for path in (package / "modules").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "RouteSpec" and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[1], ast.Constant)):
                route_specs.append((node.args[0].value, node.args[1].value, path.name))
    assert len(route_specs) == 108, f"expected 108 RouteSpec entries, got {len(route_specs)}"
    document = (ROOT / "docs" / "web-state-machine.md").read_text(encoding="utf-8")
    document_lines = document.splitlines()
    for method, route, owner in route_specs:
        token = f"`{method} {route}`"
        assert token in document, f"route missing from state machine doc: {method} {route} ({owner})"
        if method != "GET":
            contract_lines = [line for line in document_lines if token in line]
            required = ("权限=", "守卫=", "写入=", "跨账户=", "失败=")
            assert any(all(field in line for field in required) for line in contract_lines), (
                f"write route contract incomplete: {method} {route}"
            )

    static = ROOT / "static"
    common_js = static / "js" / "common.js"
    style_css = static / "css" / "style.css"
    assert len(common_js.read_text(encoding="utf-8-sig").splitlines()) <= 5, (
        "common.js must remain a compatibility barrel"
    )
    style_text = style_css.read_text(encoding="utf-8-sig")
    assert len(style_text.splitlines()) <= 20 and "@import" in style_text, (
        "style.css must remain a stylesheet manifest"
    )

    required_frontend_modules = [
        "shared/api.js", "shared/modal.js", "shared/page.js",
        "teacher/assignments.js", "teacher/imports.js", "teacher/reporting.js",
        "teacher/revisions.js", "teacher/analytics.js",
        "admin/directory.js", "admin/classes.js", "admin/import-preview.js",
        "admin/modules.js", "admin/ai.js",
    ]
    js_root = static / "js"
    for relative in required_frontend_modules:
        assert (js_root / relative).is_file(), f"missing frontend module: {relative}"

    import_pattern = re.compile(r'(?:from\s+|import\s*)["\'](\.[^"\']+)["\']')
    for path in js_root.rglob("*.js"):
        source = path.read_text(encoding="utf-8-sig")
        for relative in import_pattern.findall(source):
            target = (path.parent / relative).resolve()
            assert target.is_file(), f"missing JavaScript import {relative}: {path}"

    for name in ["admin", "teacher", "student", "student_practice", "student_history"]:
        template = (ROOT / "templates" / f"{name}.html").read_text(encoding="utf-8")
        assert "common.js" not in template, f"legacy common.js loaded by {name}.html"
        assert 'type="module"' in template, f"ES modules not enabled in {name}.html"

    teacher_source = (js_root / "teacher.js").read_text(encoding="utf-8-sig")
    admin_source = (js_root / "admin.js").read_text(encoding="utf-8-sig")
    assert './teacher/reporting.js' in teacher_source and './teacher/analytics.js' in teacher_source
    assert './admin/classes.js' in admin_source and './admin/import-preview.js' in admin_source

    import app as entrypoint

    extension = entrypoint.app.extensions.get("essay_platform")
    assert extension and extension["runtime"]["executor"]
    routes = [
        (rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
        for rule in entrypoint.app.url_map.iter_rules()
    ]
    assert len(routes) == len(set(routes)), "duplicate route and method pairs detected"
    print("architecture_test.py: OK")


if __name__ == "__main__":
    main()
