"""Shared spreadsheet header, grade and class normalization rules."""

import datetime
import re
from essay_platform.services.directory import clean_cell

HEADER_ALIASES = {
    "grade": ("年级", "grade", "就读年级"),
    "klass": ("班级", "class", "klass", "就读班级"),
    "studentNo": ("学号", "studentno", "student_id", "studentid", "学生编号"),
    "admissionYear": ("入学年份", "入学年", "admissionyear", "enrollmentyear", "entryyear"),
    "classStudentNo": ("班内学号", "班级学号", "班内序号", "座号", "classstudentno", "seatno"),
    "sourceSequence": ("序号", "编号", "行号", "no", "number"),
    "display_name": ("姓名", "name", "学生姓名"),
    "username": ("用户名", "账号", "username", "login", "登录账号"),
    "password": ("密码", "password", "初始密码"),
}

CHINESE_NUMBERS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}

NUMBER_TO_CHINESE = {value: key for key, value in CHINESE_NUMBERS.items()}

STAGE_GRADE_NUMBERS = {
    "初一": 7, "初二": 8, "初三": 9,
    "高一": 10, "高二": 11, "高三": 12,
}

COMBINED_CLASS_PATTERNS = (
    re.compile(
        r"^(?:小学)?(?P<grade>初[一二三]|高[一二三]|[一二三四五六七八九十]{1,2}|\d{1,2})"
        r"(?:年级|级)?[（(](?P<klass>[一二三四五六七八九十\d]{1,3})[)）]班?$"),
    re.compile(
        r"^(?:小学)?(?P<grade>初[一二三]|高[一二三]|[一二三四五六七八九十]{1,2}|\d{1,2})"
        r"(?:年级|级)(?P<klass>[一二三四五六七八九十\d]{1,3})班$"),
    re.compile(
        r"^(?P<grade>[一二三四五六七八九十]{1,2})"
        r"(?P<klass>[一二三四五六七八九十\d]{1,3})班$"),
)
def normalize_header(value):
    s = clean_cell(value).lower()
    return re.sub(r"[\s_\-:：/\\（）()]", "", s)

HEADER_LOOKUP = {
    normalize_header(alias): key
    for key, aliases in HEADER_ALIASES.items()
    for alias in aliases
}

def default_academic_year_start(today=None):
    current = today or datetime.date.today()
    return current.year if current.month >= 9 else current.year - 1

def parse_academic_year_start(value):
    raw = clean_cell(value)
    if not raw:
        return default_academic_year_start()
    if not re.fullmatch(r"\d{4}", raw):
        raise ValueError("当前学年起始年份应为 4 位年份，如 2025")
    year = int(raw)
    if year < 2000 or year > datetime.date.today().year + 1:
        raise ValueError("当前学年起始年份超出合理范围")
    return year

def parse_grade_number(value):
    raw = re.sub(r"\s+", "", clean_cell(value))
    if not raw:
        return None
    raw = raw.removeprefix("小学").removesuffix("年级").removesuffix("级")
    if raw in STAGE_GRADE_NUMBERS:
        return STAGE_GRADE_NUMBERS[raw]
    if raw.isdigit():
        number = int(raw)
        return number if 1 <= number <= 12 else None
    return CHINESE_NUMBERS.get(raw)

def parse_small_number(value):
    raw = re.sub(r"\s+", "", clean_cell(value))
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    if raw in CHINESE_NUMBERS:
        return CHINESE_NUMBERS[raw]
    if "十" not in raw or raw.count("十") != 1:
        return None
    tens, ones = raw.split("十", 1)
    tens_value = 1 if not tens else CHINESE_NUMBERS.get(tens)
    ones_value = 0 if not ones else CHINESE_NUMBERS.get(ones)
    if tens_value is None or ones_value is None:
        return None
    return tens_value * 10 + ones_value

def canonical_grade_name(number, original=""):
    compact = re.sub(r"\s+", "", clean_cell(original))
    if compact.startswith(("初", "高")):
        return compact.removesuffix("年级").removesuffix("级")
    chinese = NUMBER_TO_CHINESE.get(number)
    return f"{chinese}年级" if chinese else clean_cell(original)

def parse_class_number(value):
    raw = re.sub(r"\s+", "", clean_cell(value)).removesuffix("班")
    number = parse_small_number(raw)
    return number if number is not None and 1 <= number <= 99 else None

def grade_year_in_stage(grade_number):
    if not isinstance(grade_number, int):
        return None
    if 1 <= grade_number <= 6:
        return grade_number
    if 7 <= grade_number <= 9:
        return grade_number - 6
    if 10 <= grade_number <= 12:
        return grade_number - 9
    return None

def parse_combined_class(value):
    raw = re.sub(r"\s+", "", clean_cell(value))
    for pattern in COMBINED_CLASS_PATTERNS:
        match = pattern.fullmatch(raw)
        if not match:
            continue
        grade_number = parse_grade_number(match.group("grade"))
        class_number = parse_class_number(match.group("klass"))
        if grade_number and class_number:
            return grade_number, class_number
    return None

def normalize_import_class(raw_grade, raw_klass):
    grade_text = clean_cell(raw_grade)
    klass_text = clean_cell(raw_klass)
    combined_source = ""
    combined = None
    for candidate in (klass_text, grade_text):
        combined = parse_combined_class(candidate)
        if combined:
            combined_source = candidate
            break
    errors = []
    if combined:
        grade_number, class_number = combined
        separate_grade = parse_grade_number(grade_text) if grade_text != combined_source else None
        separate_class = parse_class_number(klass_text) if klass_text != combined_source else None
        if separate_grade and separate_grade != grade_number:
            errors.append("年级列与组合班级不一致")
        if separate_class and separate_class != class_number:
            errors.append("班级列与组合班级不一致")
        grade = canonical_grade_name(grade_number, grade_text or match_grade_source(combined_source))
        return grade, f"{class_number}班", grade_number, class_number, combined_source, errors

    grade_number = parse_grade_number(grade_text)
    class_number = parse_class_number(klass_text)
    grade = canonical_grade_name(grade_number, grade_text) if grade_number else grade_text
    klass = f"{class_number}班" if class_number else klass_text
    return grade, klass, grade_number, class_number, "", errors

def match_grade_source(combined_source):
    compact = re.sub(r"\s+", "", clean_cell(combined_source))
    for pattern in COMBINED_CLASS_PATTERNS:
        match = pattern.fullmatch(compact)
        if match:
            return match.group("grade")
    return ""

def generated_student_no(admission_year, grade_number, class_number, class_student_no):
    values = (admission_year, grade_number, class_number, class_student_no)
    if not all(isinstance(value, int) for value in values):
        raise ValueError("缺少生成学号所需的入学年份、年级、班级或班内号")
    if not 2000 <= admission_year <= 2100:
        raise ValueError("入学年份格式不正确")
    if not 1 <= grade_number <= 12 or not 1 <= class_number <= 99:
        raise ValueError("年级或班级编号超出范围")
    if not 1 <= class_student_no <= 99:
        raise ValueError("班内号应在 1 到 99 之间")
    return f"{admission_year:04d}{grade_number:02d}{class_number:02d}{class_student_no:02d}"

def import_class_sort_key(class_key):
    grade, klass = class_key
    return (parse_grade_number(grade) or 99, parse_class_number(klass) or 999,
            grade, klass)
