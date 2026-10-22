# -*- coding: utf-8 -*-
"""PDF report generation for the AI essay grading app."""

import os
import re
from html import escape as html_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (HRFlowable, KeepTogether, PageBreak,
                                Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)


def ensure_fonts():
    """Register a usable CJK font pair on Windows, Linux, or macOS."""
    candidates = [
        (r"C:\Windows\Fonts\msyh.ttc", "MSYH", 0, "normal"),
        (r"C:\Windows\Fonts\msyhbd.ttc", "MSYHBD", 0, "bold"),
        (r"C:\Windows\Fonts\simsun.ttc", "SIMSUN", 0, "normal"),
        (r"C:\Windows\Fonts\simhei.ttf", "SIMHEI", None, "bold"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "NOTOCJK", 0, "normal"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "NOTOCJKBD", 0, "bold"),
        ("/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Regular.otf", "SOURCEHAN", None, "normal"),
        ("/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Bold.otf", "SOURCEHANBD", None, "bold"),
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "WQYZH", 0, "normal"),
        ("/System/Library/Fonts/PingFang.ttc", "PINGFANG", 0, "normal"),
    ]
    normal, bold = None, None
    for path, name, idx, weight in candidates:
        if not os.path.exists(path):
            continue
        try:
            kwargs = {"subfontIndex": idx} if idx is not None else {}
            pdfmetrics.registerFont(TTFont("CJK_" + name, path, **kwargs))
        except Exception:
            continue
        if weight == "normal" and normal is None:
            normal = "CJK_" + name
        if weight == "bold" and bold is None:
            bold = "CJK_" + name
    if not normal or not bold:
        raise RuntimeError(
            "系统中未找到完整的中文常规/粗体字体，无法导出 PDF。Windows 请安装微软雅黑；"
            "Linux 请安装 fonts-noto-cjk 或思源黑体常规版与粗体版。")
    return {"normal": normal, "bold": bold}


def pdf_styles(fonts):
    n, b = fonts["normal"], fonts["bold"]
    return {
        "title": ParagraphStyle("title", fontName=b, fontSize=18, leading=26,
                                alignment=1, spaceAfter=12),
        "meta": ParagraphStyle("meta", fontName=n, fontSize=9.5, leading=15,
                               textColor=colors.HexColor("#555555")),
        "h2": ParagraphStyle("h2", fontName=b, fontSize=13.5, leading=20,
                             textColor=colors.HexColor("#1a3d7c"), spaceBefore=2, spaceAfter=4),
        "h3": ParagraphStyle("h3", fontName=b, fontSize=11, leading=17,
                             spaceBefore=6, spaceAfter=3),
        "body": ParagraphStyle("body", fontName=n, fontSize=10.5, leading=17,
                               wordWrap="CJK", spaceAfter=4),
        "bullet": ParagraphStyle("bullet", fontName=n, fontSize=10.5, leading=17,
                                 wordWrap="CJK", leftIndent=16, firstLineIndent=-12,
                                 spaceAfter=2),
    }


def build_report(path, doc_title, meta_lines, essays):
    """生成批改报告 PDF。essays: [{studentName, score, level, gradedAt, feedback}]"""
    fonts = ensure_fonts()
    st = pdf_styles(fonts)
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=58, rightMargin=58,
                            topMargin=52, bottomMargin=52, title=doc_title)
    story = [Paragraph(html_escape(doc_title), st["title"])]
    for m in meta_lines:
        story.append(Paragraph(html_escape(m), st["meta"]))
    story.append(HRFlowable(width="100%", thickness=1,
                            color=colors.HexColor("#c9cfe0"), spaceBefore=8, spaceAfter=12))

    rows = [["姓名", "分数", "等级"]] + [
        [e["studentName"], str(e.get("score") if e.get("score") is not None else "-"),
         e.get("level") or "-"] for e in essays]
    table = Table(rows, colWidths=[190, 90, 90])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), fonts["bold"]),
        ("FONTNAME", (0, 1), (-1, -1), fonts["normal"]),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2fb")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9cfe0")),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)

    for e in essays:
        story.append(PageBreak())
        fb = e.get("feedback") or {}
        head = f"{html_escape(e['studentName'])}　{e.get('score')} 分 · {html_escape(e.get('level') or '-')}"
        story.append(KeepTogether([
            Paragraph(head, st["h2"]),
            Paragraph("批改时间：" + html_escape(e.get("gradedAt") or "-"), st["meta"]),
        ]))
        story.append(Spacer(1, 4))
        if fb.get("summary"):
            story.append(Paragraph("总评：" + html_escape(str(fb["summary"])), st["body"]))
        dims = fb.get("dimensions") or {}
        if any(v is not None for v in dims.values()):
            story.append(Spacer(1, 6))
            story.append(Paragraph("分项评分", st["h3"]))
            dtab = Table([["评阅维度", "得分"]] +
                         [[k, str(v if v is not None else "-")] for k, v in dims.items()],
                         colWidths=[280, 90])
            dtab.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), fonts["bold"]),
                ("FONTNAME", (0, 1), (-1, -1), fonts["normal"]),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2fb")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9cfe0")),
                ("ALIGN", (1, 0), (1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(dtab)
        sections = [
            ("优点", fb.get("strengths"), "#166534"),
            ("不足与问题", fb.get("weaknesses"), "#9a3412"),
            ("具体修改意见", fb.get("suggestions"), "#1d4ed8"),
            ("好句示范", [fb["modelSnippet"]] if fb.get("modelSnippet") else [], "#0f766e"),
        ]
        for title, items, color in sections:
            if not items:
                continue
            story.append(Spacer(1, 7))
            h3 = ParagraphStyle("h3x", parent=st["h3"], textColor=colors.HexColor(color))
            story.append(Paragraph(title, h3))
            for j, item in enumerate(items, 1):
                story.append(Paragraph(f"{j}. {html_escape(str(item))}", st["bullet"]))
        if fb.get("upgrade"):
            story.append(Spacer(1, 8))
            h3 = ParagraphStyle("h3u", parent=st["h3"], textColor=colors.HexColor("#7c3aed"))
            story.append(Paragraph("✨ AI 润色提升版", h3))
            story.append(Paragraph(html_escape(str(fb["upgrade"])), st["body"]))
    doc.build(story)


def _paragraph_text(value):
    lines = str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "<br/>".join(html_escape(line) if line else "&nbsp;" for line in lines)


def build_assignment_report(path, doc_title, essays):
    """Build the teacher-facing A4 report without numeric scores."""
    fonts = ensure_fonts()
    styles = {
        "student": ParagraphStyle(
            "assignment-student", fontName=fonts["normal"], fontSize=12, leading=18,
            wordWrap="CJK"),
        "grade": ParagraphStyle(
            "assignment-grade", fontName=fonts["normal"], fontSize=14, leading=18,
            alignment=2, wordWrap="CJK"),
        "essay-title": ParagraphStyle(
            "assignment-title", fontName=fonts["bold"], fontSize=16, leading=24,
            alignment=1, spaceBefore=13, spaceAfter=12, wordWrap="CJK"),
        "essay-body": ParagraphStyle(
            "assignment-body", fontName=fonts["normal"], fontSize=14, leading=23,
            firstLineIndent=28, wordWrap="CJK", spaceAfter=8),
        "feedback": ParagraphStyle(
            "assignment-feedback", fontName=fonts["bold"], fontSize=14, leading=22,
            wordWrap="CJK", spaceAfter=7),
    }
    doc = SimpleDocTemplate(
        path, pagesize=A4, leftMargin=46, rightMargin=46, topMargin=42,
        bottomMargin=42, title=doc_title,
    )
    story = []
    for index, essay in enumerate(essays):
        if index:
            story.append(PageBreak())
        left = "姓名：{}　学号：{}　班级：{}".format(
            html_escape(str(essay.get("studentName") or "未关联")),
            html_escape(str(essay.get("studentNo") or "-")),
            html_escape(str(essay.get("className") or "-")),
        )
        right = "等第：" + html_escape(str(essay.get("grade") or "-"))
        header = Table(
            [[Paragraph(left, styles["student"]), Paragraph(right, styles["grade"])]],
            colWidths=[A4[0] - 92 - 105, 105],
        )
        header.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(header)
        story.append(Paragraph(
            html_escape(str(essay.get("title") or "作文")), styles["essay-title"]))
        story.append(Paragraph(_paragraph_text(essay.get("text")), styles["essay-body"]))
        story.append(Spacer(1, 8))
        story.append(Paragraph("批阅意见", styles["feedback"]))
        feedback = essay.get("feedback") or {}
        sections = [
            ("总评", feedback.get("summary")),
            ("优点", feedback.get("strengths")),
            ("不足", feedback.get("weaknesses")),
            ("修改建议", feedback.get("suggestions")),
            ("示范句", feedback.get("modelSnippet")),
        ]
        for label, value in sections:
            if not value:
                continue
            if isinstance(value, (list, tuple)):
                content = "；".join(str(item) for item in value if str(item).strip())
            else:
                content = str(value)
            if content:
                story.append(Paragraph(
                    f"{html_escape(label)}：{_paragraph_text(content)}", styles["feedback"]))
    doc.build(story)


def safe_filename(name):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name)[:60] or "报告"
