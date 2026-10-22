# -*- coding: utf-8 -*-
"""Generate a small PDF that matches the default platform seed student `stu`."""

import os

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from reportlab.lib.styles import ParagraphStyle


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE_DIR, "平台测试作文_stu.pdf")


def register_font():
    for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc"):
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont("CJK", path, subfontIndex=0))
            return "CJK"
    return "Helvetica"


def main():
    font = register_font()
    style = ParagraphStyle("body", fontName=font, fontSize=12, leading=20, wordWrap="CJK")
    title = ParagraphStyle("title", parent=style, fontSize=15, leading=24, spaceAfter=10)
    doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=60, rightMargin=60, topMargin=60, bottomMargin=60)
    story = [
        Paragraph("STU学生", title),
        Paragraph("我的一次成长", title),
        Paragraph(
            "那天放学后，我主动留下来整理教室。开始时我觉得这只是一件小事，可当我看到整齐的桌椅和干净的地面时，心里忽然有一种踏实的快乐。"
            "我明白了，成长不一定是轰轰烈烈的大事，也可以是一次认真负责的选择。以后我想把这种认真带到学习和生活中，做一个更可靠的人。",
            style,
        ),
        Spacer(1, 20),
    ]
    doc.build(story)
    print(OUT)


if __name__ == "__main__":
    main()
