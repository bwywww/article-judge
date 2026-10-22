"""History, teacher records, and PDF export routes."""

from .base import RouteSpec, create_blueprint
import io
import os
import secrets
import copy
import database as db
from flask import jsonify, request, send_file
from openpyxl import Workbook
from essay_platform.adapters.reports import build_assignment_report, build_report, safe_filename
from essay_platform.core.auth import student_required, teacher_required
from essay_platform.core.config import ASSIGNMENTS_PATH, DATA_DIR, HISTORY_PATH, USERS_PATH
from essay_platform.core.state import io_lock, upgrade_in_flight
from essay_platform.core.time import content_hash
from essay_platform.persistence.datasets import load_json, save_json
from essay_platform.services.assignments import (
    find_assignment, find_essay, find_teacher_assignment, find_user_submission,
    is_assignment_owner,
)
from essay_platform.services.directory import class_label, paginate, pagination_args
from essay_platform.services.reporting import assignment_report_item, delayed_cleanup, report_grade
from essay_platform.adapters.ai import request_upgrade_text
from essay_platform.services.operations import ai_quota_error
from essay_platform.repositories import analytics as analytics_repository

MANIFEST = {
    "key": "reporting", "dependencies": [], "permissions": ["teacher", "student"],
    "navigation": ["history", "reports"], "tasks": [], "migrations": ["history"],
}

ROUTES = (
    RouteSpec("GET", "/api/student/history", "api_student_history"),
    RouteSpec("GET", "/api/assignments/<aid>/export", "api_assignments_export"),
    RouteSpec("GET", "/api/assignments/<aid>/export/<eid>", "api_assignments_export_one"),
    RouteSpec("GET", "/api/assignments/<aid>/completion.xlsx", "api_assignment_completion_xlsx"),
    RouteSpec("GET", "/api/student/export/<hid>", "api_student_export"),
    RouteSpec("GET", "/api/teacher/records", "api_teacher_records"),
    RouteSpec("GET", "/api/teacher/records/<hid>/export", "api_teacher_record_export"),
)

@student_required
def api_student_history():
    data = load_json(HISTORY_PATH, {"items": []})
    user = request.user
    items = [i for i in data["items"] if i["username"] == user["username"]]
    history_type = (request.args.get("type") or "").strip()
    if history_type == "assignment":
        items = [item for item in items if item.get("assignmentId")]
    elif history_type == "practice":
        items = [item for item in items if not item.get("assignmentId")]
    elif history_type:
        return jsonify({"error": "历史类型无效"}), 400
    page, page_size = pagination_args(request.args)
    items, total = paginate(items, page, page_size)
    artifacts = db.history_artifacts_for(user["username"], [item["id"] for item in items])
    return jsonify({"history": [{
        "id": i["id"], "title": i.get("title", ""), "gradedAt": i.get("gradedAt", ""),
        "prompt": i.get("prompt", ""), "text": i.get("text", ""),
        "assignmentId": i.get("assignmentId", ""),
        "assignmentTitle": i.get("assignmentTitle", ""),
        "classId": i.get("classId", ""),
        "sourceEssayId": i.get("sourceEssayId", ""),
        "submissionVersionId": i.get("submissionVersionId", ""),
        "publicationNo": i.get("publicationNo", 1),
        "academicYearId": i.get("academicYearId", ""),
        "classNameSnapshot": i.get("classNameSnapshot", ""),
        "score": i["result"].get("score"), "level": i["result"].get("level", ""),
        "result": {
            **copy.deepcopy(i["result"]),
            **({"upgrade": artifacts[i["id"]]["upgrade"].get("upgrade", "")}
               if artifacts.get(i["id"], {}).get("upgrade") else {}),
        },
    } for i in items], "total": total, "page": page, "pageSize": page_size})

@student_required
def api_student_history_upgrade(hid):
    key = f"history:{request.user['username']}:{hid}"
    with io_lock:
        data = load_json(HISTORY_PATH, {"items": []})
        item = next((entry for entry in data.get("items", [])
                     if entry.get("id") == hid
                     and entry.get("username") == request.user["username"]), None)
        if not item:
            return jsonify({"error": "记录不存在"}), 404
        expected_hash = content_hash(item.get("text", ""))
        artifact = db.get_history_artifact(
            hid, request.user["username"], "upgrade", expected_hash)
        cached = (artifact or {}).get("upgrade", "")
        if cached:
            return jsonify({"upgrade": cached, "cached": True})
        quota_error = ai_quota_error()
        if quota_error:
            return quota_error
        if key in upgrade_in_flight:
            return jsonify({"error": "润色稿正在生成，请勿重复点击"}), 409
        upgrade_in_flight.add(key)
        title = item.get("title", "")
        prompt = item.get("prompt", "")
        text = item.get("text", "")
    try:
        try:
            upgrade = request_upgrade_text(title, prompt, text)
        except Exception as exc:
            return jsonify({"error": f"生成润色稿失败：{exc}"}), 400
        stored = db.save_history_artifact(
            hid, request.user["username"], "upgrade", expected_hash,
            {"upgrade": upgrade}, model="configured-upgrade-model")
        if not stored.get("ok"):
            return jsonify({"error": "作文正文已变化，润色稿未写入"}), 409
        return jsonify({"upgrade": upgrade, "cached": False})
    finally:
        with io_lock:
            upgrade_in_flight.discard(key)

@teacher_required
def api_assignments_export(aid):
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    if not assignment:
        return jsonify({"error": "作业不存在"}), 404
    essays = [e for e in assignment.get("essays", []) if e.get("status") == "done"]
    if not essays:
        return jsonify({"error": "还没有批改完成的学生，无法导出"}), 400
    users = {user["username"]: user
             for user in load_json(USERS_PATH, {"users": []}).get("users", [])}
    path = os.path.join(DATA_DIR, f"tmp_{secrets.token_hex(6)}.pdf")
    try:
        try:
            build_assignment_report(
                path, f"《{assignment.get('title', '')}》作文批改报告",
                [assignment_report_item(assignment, essay, users) for essay in essays],
            )
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500
        return send_file(path, as_attachment=True,
                         download_name=f"批改报告_{safe_filename(assignment['title'])}.pdf")
    finally:
        delayed_cleanup(path)

@teacher_required
def api_assignments_export_one(aid, eid):
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    essay = find_essay(assignment, eid) if assignment else None
    if not essay or essay.get("status") != "done":
        return jsonify({"error": "该作文还没有批改结果"}), 400
    users = {user["username"]: user
             for user in load_json(USERS_PATH, {"users": []}).get("users", [])}
    path = os.path.join(DATA_DIR, f"tmp_{secrets.token_hex(6)}.pdf")
    try:
        try:
            build_assignment_report(
                path, f"{essay['studentName']}《{assignment.get('title', '')}》批改意见",
                [assignment_report_item(assignment, essay, users)],
            )
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500
        return send_file(path, as_attachment=True,
                         download_name=f"{safe_filename(essay['studentName'])}_批改意见.pdf")
    finally:
        delayed_cleanup(path)

@teacher_required
def api_assignment_completion_xlsx(aid):
    if Workbook is None:
        return jsonify({"error": "缺少 openpyxl，无法导出 Excel"}), 500
    data = load_json(ASSIGNMENTS_PATH, {"assignments": []})
    assignment = find_teacher_assignment(data, aid, request.user)
    if not assignment:
        return jsonify({"error": "作业不存在"}), 404
    ranking = analytics_repository.assignment_ranking(aid, request.user["username"])
    if ranking is None:
        return jsonify({"error": "作业班级记录不存在"}), 404
    essays = {item.get("id"): item for item in assignment.get("essays", [])}
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "完成情况"
    sheet.append(["姓名", "学号", "提交状态", "等第"])
    status_labels = {
        "not_submitted": "未提交", "pending": "待批改", "queued": "排队中",
        "grading": "批改中", "error": "批改失败", "done": "已完成",
    }
    for row in ranking:
        essay = essays.get(row.get("essayId"))
        grade = report_grade((essay or {}).get("feedback")) if essay and essay.get("status") == "done" else ""
        sheet.append([
            row.get("displayName", ""), row.get("studentNo", ""),
            status_labels.get(row.get("status"), row.get("status", "")), grade,
        ])
    for column, width in (("A", 16), ("B", 20), ("C", 14), ("D", 10)):
        sheet.column_dimensions[column].width = width
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        download_name=f"完成情况_{safe_filename(assignment.get('title', '作业'))}.xlsx",
    )

@student_required
def api_student_export(hid):
    data = load_json(HISTORY_PATH, {"items": []})
    user = request.user
    item = next((i for i in data["items"]
                 if i["id"] == hid and i["username"] == user["username"]), None)
    if not item:
        return jsonify({"error": "记录不存在"}), 404
    path = os.path.join(DATA_DIR, f"tmp_{secrets.token_hex(6)}.pdf")
    try:
        title = item.get("title") or "自主练习作文"
        build_report(path, f"《{title}》批改意见",
                     [f"学生：{user.get('display_name') or user['username']}",
                      f"批改时间：{item.get('gradedAt', '')}"],
                     [{"studentName": user.get("display_name") or user["username"],
                       "score": item["result"].get("score"),
                       "level": item["result"].get("level", ""),
                       "gradedAt": item.get("gradedAt", ""),
                       "feedback": item["result"]}])
        return send_file(path, as_attachment=True,
                         download_name=f"批改意见_{safe_filename(title)}.pdf")
    finally:
        delayed_cleanup(path)

@teacher_required
def api_teacher_records():
    """查看本老师发布作业的学生提交记录，可按作业筛选；不展示学生自主练习。"""
    assignment_id = (request.args.get("assignmentId") or "").strip()
    users = {u["username"]: u
             for u in load_json(USERS_PATH, {"users": []})["users"]}
    assignments = load_json(ASSIGNMENTS_PATH, {"assignments": []})["assignments"]
    owned_assignment_ids = {a["id"] for a in assignments if is_assignment_owner(a, request.user)}
    items = load_json(HISTORY_PATH, {"items": []})["items"]
    if assignment_id:
        if assignment_id not in owned_assignment_ids:
            return jsonify({"records": []})
        items = [i for i in items if i.get("assignmentId") == assignment_id]
    else:
        items = [i for i in items if i.get("assignmentId") in owned_assignment_ids]
    query = (request.args.get("q") or "").strip().lower()
    if query:
        items = [i for i in items if query in " ".join([
            str(i.get("username", "")),
            str(users.get(i.get("username"), {}).get("display_name", "")),
            str(i.get("title", "")), str(i.get("assignmentTitle", "")),
        ]).lower()]
    page, page_size = pagination_args(request.args, default_size=30)
    items, total = paginate(items, page, page_size)
    records = []
    for i in items:
        u = users.get(i.get("username"), {})
        records.append({
            "id": i["id"],
            "username": i.get("username", ""),
            "displayName": u.get("display_name") or i.get("username", ""),
            "title": i.get("title", ""),
            "prompt": i.get("prompt", ""),
            "assignmentId": i.get("assignmentId", ""),
            "assignmentTitle": i.get("assignmentTitle", ""),
            "score": i["result"].get("score"),
            "level": i["result"].get("level", ""),
            "gradedAt": i.get("gradedAt", ""),
            "text": i.get("text", ""),
            "result": i["result"],
        })
    return jsonify({"records": records, "total": total,
                    "page": page, "pageSize": page_size})

@teacher_required
def api_teacher_record_export(hid):
    items = load_json(HISTORY_PATH, {"items": []})["items"]
    item = next((i for i in items if i["id"] == hid), None)
    if not item:
        return jsonify({"error": "记录不存在"}), 404
    assignment_id = item.get("assignmentId") or ""
    if not assignment_id:
        return jsonify({"error": "记录不存在"}), 404
    assignments = load_json(ASSIGNMENTS_PATH, {"assignments": []})["assignments"]
    owner = next((a for a in assignments if a["id"] == assignment_id), None)
    if not is_assignment_owner(owner, request.user):
        return jsonify({"error": "记录不存在"}), 404
    users = {u["username"]: u
             for u in load_json(USERS_PATH, {"users": []})["users"]}
    u = users.get(item.get("username"), {})
    name = u.get("display_name") or item.get("username", "")
    path = os.path.join(DATA_DIR, f"tmp_{secrets.token_hex(6)}.pdf")
    try:
        title = item.get("title") or item.get("assignmentTitle") or "作文"
        try:
            build_assignment_report(path, f"《{title}》批改意见", [{
                "studentName": name,
                "studentNo": u.get("studentNo", ""),
                "className": class_label(item.get("classId", "")),
                "title": title,
                "text": item.get("text", ""),
                "grade": report_grade(item.get("result")),
                "feedback": item.get("result") or {},
            }])
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500
        return send_file(path, as_attachment=True,
                         download_name=f"{safe_filename(name)}_批改意见_{safe_filename(title)}.pdf")
    finally:
        delayed_cleanup(path)

def build_blueprint():
    return create_blueprint("reporting", __name__, ROUTES, default_feature="reporting", namespace=globals())
