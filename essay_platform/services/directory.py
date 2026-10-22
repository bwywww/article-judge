"""User, class and public profile helpers."""

import re
from essay_platform.core.config import CLASSES_PATH, USERS_PATH
from essay_platform.persistence.datasets import load_json

def class_name(grade="", klass=""):
    grade = (grade or "").strip()
    klass = (klass or "").strip()
    if grade and klass:
        return grade + klass
    return grade or klass or "默认班级"


_CN_NUMBERS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}


def _natural_number(value, default=10_000):
    """Extract a human number from labels such as 五年级 / 10班."""
    text = str(value or "").strip()
    match = re.search(r"\d+", text)
    if match:
        return int(match.group())
    for token in sorted(_CN_NUMBERS, key=len, reverse=True):
        if token in text:
            return _CN_NUMBERS[token]
    return default


def _grade_order(value):
    text = str(value or "").strip()
    number = _natural_number(text)
    if "高" in text:
        return 2, number
    if "初" in text:
        return 1, number
    if number in (7, 8, 9):
        return 1, number - 6
    if number != 10_000:
        return 0, number
    return 9, number


def class_sort_key(item):
    """Stable grade-then-class natural ordering used by every class picker."""
    grade = str((item or {}).get("grade") or "")
    klass = str((item or {}).get("klass") or "")
    return (
        *_grade_order(grade), grade,
        _natural_number(klass), klass,
        str((item or {}).get("name") or ""), str((item or {}).get("id") or ""),
    )


def find_class(data, cid):
    for c in data.get("classes", []):
        if c.get("id") == cid:
            return c
    return None


def is_class_teacher(cls, user):
    return bool(cls and user and user.get("username") in (cls.get("teacherUsernames") or []))


def first_teacher_class(classes_data, username):
    return next((c for c in classes_data.get("classes", [])
                 if username in (c.get("teacherUsernames") or [])), None)


def ensure_teacher_default_class(user):
    data = load_json(CLASSES_PATH, {"classes": []})
    cls = first_teacher_class(data, user["username"])
    return cls


def teacher_classes(username):
    data = load_json(CLASSES_PATH, {"classes": []})
    classes = [c for c in data.get("classes", [])
               if c.get("status", "active") == "active"
               and username in (c.get("teacherUsernames") or [])]
    return sorted(classes, key=class_sort_key)


def teacher_class_ids(username):
    return {c["id"] for c in teacher_classes(username)}


def student_class_ids(username):
    data = load_json(CLASSES_PATH, {"classes": []})
    return {c["id"] for c in data.get("classes", [])
            if c.get("status", "active") == "active"
            and username in (c.get("studentUsernames") or [])}


def class_label(cid):
    if not cid:
        return ""
    cls = find_class(load_json(CLASSES_PATH, {"classes": []}), cid)
    return cls.get("name", "") if cls else ""


def class_students(class_id):
    users = {u["username"]: u for u in load_json(USERS_PATH, {"users": []})["users"]}
    cls = find_class(load_json(CLASSES_PATH, {"classes": []}), class_id)
    return [public_user(users[u]) for u in (cls or {}).get("studentUsernames", [])
            if u in users and users[u].get("role") == "student"
            and users[u].get("active", True)]


def class_student_usernames(class_id):
    cls = find_class(load_json(CLASSES_PATH, {"classes": []}), class_id)
    return set((cls or {}).get("studentUsernames") or [])

def public_user(user):
    return {
        "username": user.get("username", ""),
        "display_name": user.get("display_name") or user.get("username", ""),
        "role": user.get("role", ""),
        "studentNo": user.get("studentNo", ""),
        "employeeNo": user.get("employeeNo", ""),
        "subject": user.get("subject", ""),
        "grade": user.get("grade", ""),
        "klass": user.get("klass", ""),
        "active": bool(user.get("active", True)),
        "schoolStatus": user.get("schoolStatus", "active"),
        "mustChangePassword": bool(user.get("mustChangePassword")),
    }


def public_student(user):
    return public_user(user)


def pagination_args(params, default_size=20, max_size=100):
    try:
        page = max(1, int(params.get("page", 1)))
        page_size = max(1, min(max_size, int(params.get("pageSize", default_size))))
    except (TypeError, ValueError):
        page, page_size = 1, default_size
    return page, page_size


def paginate(items, page, page_size):
    total = len(items)
    start = (page - 1) * page_size
    return items[start:start + page_size], total


USERNAME_RE = re.compile(r"[\w\u4e00-\u9fa5-]{2,32}")
STUDENT_NO_RE = re.compile(r"[\w-]{2,32}")
EMPLOYEE_NO_RE = re.compile(r"[\w-]{2,32}")


def clean_cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def validate_username(username):
    return bool(USERNAME_RE.fullmatch(username or ""))


def validate_student_no(student_no):
    return bool(STUDENT_NO_RE.fullmatch(student_no or ""))


def validate_employee_no(employee_no):
    return bool(EMPLOYEE_NO_RE.fullmatch(employee_no or ""))


def student_no_exists(users, student_no, exclude_username=""):
    if not student_no:
        return False
    return any(
        user.get("role") == "student"
        and user.get("studentNo") == student_no
        and user.get("username") != exclude_username
        for user in users
    )


def user_by_student_no(users, student_no):
    return next(
        (user for user in users
         if user.get("role") == "student" and user.get("studentNo") == student_no),
        None,
    )


def find_class_by_grade_klass(classes, grade, klass):
    return next(
        (item for item in classes.get("classes", [])
         if item.get("status", "active") == "active"
         and item.get("grade", "") == grade
         and item.get("klass", "") == klass),
        None,
    )
