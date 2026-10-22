import tempfile
import sys
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reporting import build_assignment_report, ensure_fonts


def sample_essay(name, student_no, title, text, score, grade):
    return {
        "studentName": name,
        "studentNo": student_no,
        "className": "三年级一班",
        "title": title,
        "text": text,
        "grade": grade,
        "score": score,
        "feedback": {
            "score": score,
            "level": grade,
            "summary": "人工总评内容",
            "strengths": ["内容具体", "结构清楚"],
            "weaknesses": ["结尾略短"],
            "suggestions": ["补写结尾照应题目"],
            "modelSnippet": "春风轻轻吹过校园。",
            "dimensions": {"立意与内容": score, "结构与逻辑": score, "语言表达": score},
        },
    }


def chars_for_text(page, text):
    chars = page.chars
    joined = "".join(char.get("text", "") for char in chars)
    start = joined.find(text)
    assert start >= 0, f"未找到文字：{text}"
    return chars[start:start + len(text)]


def test_layout_and_score_redaction(tmp):
    path = tmp / "teacher-layout.pdf"
    essays = [
        sample_essay("学生甲", "SA", "春天来了", "第一自然段。\n第二自然段。", 92, "优"),
        sample_essay("学生乙", "SB", "难忘的一天", "这是一篇完整的作文原文。", 68, "中"),
    ]
    build_assignment_report(str(path), "老师报告", essays)
    reader = PdfReader(path)
    assert len(reader.pages) == 2
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    for forbidden in ("分数", "总分", "平均分", "92", "68", "分项评分", "整篇提升版"):
        assert forbidden not in text
    assert "姓名：学生甲" in text and "学号：SA" in text
    assert "等第：优" in text and "等第：中" in text
    assert "第一自然段" in text and "人工总评内容" in text

    fonts = ensure_fonts()
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        assert {round(char["size"], 1) for char in chars_for_text(page, "姓名：学生甲")} == {12.0}
        assert {round(char["size"], 1) for char in chars_for_text(page, "等第：优")} == {14.0}
        assert {round(char["size"], 1) for char in chars_for_text(page, "春天来了")} == {16.0}
        assert {round(char["size"], 1) for char in chars_for_text(page, "第一自然段")} == {14.0}
        feedback_chars = chars_for_text(page, "人工总评内容")
        assert {round(char["size"], 1) for char in feedback_chars} == {14.0}
        assert all("bold" in char["fontname"].lower() or "bd" in char["fontname"].lower()
                   for char in feedback_chars)


def test_overflow_starts_next_student_on_new_page(tmp):
    path = tmp / "teacher-overflow.pdf"
    long_text = "\n".join(
        f"第{i}自然段，这是用于验证长作文完整续页和下一名学生另起新页的正文。"
        for i in range(1, 90)
    )
    essays = [
        sample_essay("长文学生", "LONG", "长作文", long_text, 86, "优"),
        sample_essay("下一名学生", "NEXT", "下一篇作文", "下一名学生的正文。", 75, "良"),
    ]
    build_assignment_report(str(path), "超长报告", essays)
    reader = PdfReader(path)
    assert len(reader.pages) >= 3
    page_texts = [page.extract_text() or "" for page in reader.pages]
    next_pages = [index for index, text in enumerate(page_texts) if "姓名：下一名学生" in text]
    assert len(next_pages) == 1 and next_pages[0] >= 2
    assert "下一名学生的正文" in page_texts[next_pages[0]]
    assert "第89自然段" in "\n".join(page_texts[:next_pages[0]])


def main():
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        test_layout_and_score_redaction(tmp)
        test_overflow_starts_next_student_on_new_page(tmp)
    print("reporting_test.py: OK")


if __name__ == "__main__":
    main()
