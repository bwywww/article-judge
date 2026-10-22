"""PDF, DOCX and TXT extraction and essay splitting."""

import io
import re
import zipfile

from pypdf import PdfReader
try:
    from docx import Document as load_docx
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph as DocxParagraph
except ImportError:
    load_docx = DocxTable = DocxParagraph = None

from essay_platform.core.config import (
    MAX_DOCX_PARAGRAPHS, MAX_DOCX_UNCOMPRESSED_BYTES, MAX_PDF_PAGES,
    MAX_UPLOAD_BYTES,
)

NAME_PREFIX = re.compile(r"^(?:姓名|名字|作者|学生)\s*[:：]\s*([\u4e00-\u9fa5·]{2,8})\s*$")

ALLOWED_IMAGE_EXTS = {"png", "jpg", "jpeg"}


def is_scanned_pdf(content: bytes) -> bool:
    """PDF 无文字层（扫描件）时为 True。"""
    try:
        reader = PdfReader(io.BytesIO(content))
        return not any((p.extract_text() or "").strip() for p in reader.pages)
    except Exception:
        return False
INLINE_NAME = re.compile(
    r"(?:学生姓名|姓名|名字|作者|学生)\s*[:：]\s*([\u4e00-\u9fa5·]{2,8})"
    r"(?=\s|　|学号|学生编号|班级|年级|$)"
)
INLINE_STUDENT_NO = re.compile(
    r"(?:学号|学生编号|student\s*no)\s*[:：]\s*([0-9A-Za-z_-]{1,32})",
    re.IGNORECASE,
)
NAME_LINE = re.compile(r"^[\u4e00-\u9fa5·]{2,8}$")
PAGE_NO = re.compile(r"^[\s\-—–·]*\d{1,3}[\s\-—–·/]*\d{0,3}\s*页?[\s\-—–·]*$|^第\s*\d{1,3}\s*页\s*$")


def join_lines(lines):
    """PDF 行转正文：同段行直接拼接（中文排版），空行保留为分段"""
    out = []
    for line in lines:
        s = line.strip()
        out.append(s if s else "\n")
    text = "".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_name_line(line, next_line, first_of_page=False):
    """判断一行是否可能是学生姓名行。
    - "姓名：张三" 形式 → 确定是
    - 2-4 字短行后跟短行（作文标题）→ 典型"姓名+题目"格式，是
    - 2-4 字短行后跟长正文：若在页首，按"姓名+正文"格式处理（无标题排版）；否则多半是标题，不算
    """
    if NAME_PREFIX.match(line):
        return True
    if not NAME_LINE.match(line) or not next_line:
        return False
    if next_line.startswith("《"):          # 下一行是标题，那这行多半是标题而不是名字
        return False
    n = len(next_line)
    if n <= 12:                             # 名字后跟标题（短）
        return True
    if first_of_page:                       # 页首"名字+长正文"（无标题排版）
        return True
    return False


def inline_identity(line):
    name_match = INLINE_NAME.search(line or "")
    number_match = INLINE_STUDENT_NO.search(line or "")
    return (
        name_match.group(1).strip() if name_match else "",
        number_match.group(1).strip() if number_match else "",
    )


def is_identity_metadata_line(line):
    return bool(re.search(r"(?:姓名|学生姓名|作者|学号|学生编号|班级|年级)\s*[:：]", line or ""))


def is_likely_title_line(line):
    text = (line or "").strip()
    return (2 <= len(text) <= 30
            and not is_identity_metadata_line(text)
            and not re.search(r"[。！？!?；;，,：:]$", text))


def likely_essay_title(lines):
    """Return a conservative title hint without removing it from the正文."""
    for line in lines:
        text = str(line or "").strip()
        if not text or is_identity_metadata_line(text) or PAGE_NO.match(text):
            continue
        return text[:80] if is_likely_title_line(text) else ""
    return ""


def split_metadata_essays(lines, *, preserve_paragraphs=False):
    """按“班级/姓名/学号”元数据拆分，适配学校导出的 Word 表头。"""
    cleaned = [str(line or "").strip() for line in lines]
    cleaned = [line for line in cleaned if line and not PAGE_NO.match(line)]
    anchors = []
    for index, line in enumerate(cleaned):
        name, student_no = inline_identity(line)
        if name:
            anchors.append({"index": index, "name": name, "studentNoHint": student_no})
    if not anchors:
        return []

    starts = []
    for anchor_index, anchor in enumerate(anchors):
        start = anchor["index"]
        previous_anchor = anchors[anchor_index - 1]["index"] if anchor_index else -1
        if start > previous_anchor + 1 and is_likely_title_line(cleaned[start - 1]):
            start -= 1
        starts.append(start)

    essays = []
    for position, anchor in enumerate(anchors):
        start = starts[position]
        end = starts[position + 1] if position + 1 < len(starts) else len(cleaned)
        body = [line for index, line in enumerate(cleaned[start:end], start)
                if index != anchor["index"] and not is_identity_metadata_line(line)]
        text = "\n".join(body).strip() if preserve_paragraphs else join_lines(body)
        if len(text) < 10:
            continue
        essays.append({
            "studentName": anchor["name"],
            "studentNoHint": anchor["studentNoHint"],
            "studentTitle": likely_essay_title(body),
            "text": text,
            "chars": len(text),
        })
    return essays


PLACEHOLDER_NAME = re.compile(r"^第\d+篇$")


def is_placeholder_name(name):
    """名字为空或是「第N篇」占位符，说明还没识别出真实姓名"""
    name = (name or "").strip()
    return not name or bool(PLACEHOLDER_NAME.match(name))


def split_lines_to_essays(lines):
    """把 TXT 文本行拆成多篇作文（按姓名行切分），返回 [{studentName, text, chars}]"""
    metadata_essays = split_metadata_essays(lines)
    if metadata_essays:
        return metadata_essays
    pages = ["\n".join(lines)]
    return split_pages_to_essays(pages)


def split_pages_to_essays(pages):
    """把 PDF 各页文本拆成多篇作文：按『姓名行』分页拆分，跨页内容自动并入上一篇"""
    flattened = []
    for page in pages:
        flattened.extend((page or "").split("\n"))
    metadata_essays = split_metadata_essays(flattened)
    if metadata_essays:
        return metadata_essays

    essays = []
    cur_name, cur_lines = None, []

    def push(name, lines):
        text = join_lines(lines)
        if len(text) < 10:
            return
        essays.append({
            "studentName": name, "studentTitle": likely_essay_title(lines),
            "text": text, "chars": len(text),
        })

    def flush():
        nonlocal cur_name, cur_lines
        if cur_lines:
            push(cur_name or f"第{len(essays) + 1}篇", cur_lines)
        cur_name, cur_lines = None, []

    for page in pages:
        lines = [l.strip() for l in (page or "").split("\n")]
        lines = [l for l in lines if l and not PAGE_NO.match(l)]
        if not lines:
            continue
        splits = []  # (行号, 姓名)
        for i, line in enumerate(lines):
            m = NAME_PREFIX.match(line)
            if m:
                splits.append((i, m.group(1)))
                continue
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if is_name_line(line, nxt, first_of_page=(i == 0)):
                splits.append((i, line))
        if not splits:
            cur_lines.extend(lines)          # 整页没有名字行 → 跨页续文
            continue
        first_idx = splits[0][0]
        if first_idx > 0:                    # 页首无名字的部分属于上一篇
            cur_lines.extend(lines[:first_idx])
        flush()                              # 收尾上一篇
        for k, (idx, name) in enumerate(splits):
            end = splits[k + 1][0] if k + 1 < len(splits) else len(lines)
            body = lines[idx + 1:end]
            if k == len(splits) - 1:
                cur_name, cur_lines = name, body   # 最后一篇可能跨页
            else:
                push(name, body)
    flush()
    return essays


def split_pdf_pages(pages, mode="auto"):
    """Split PDF text using automatic boundaries or a known paper layout."""
    if mode == "auto":
        return split_pages_to_essays(pages)
    if mode not in ("one_page", "two_pages"):
        raise ValueError("不支持的作文纸拆分方式")
    group_size = 1 if mode == "one_page" else 2
    essays = []
    for start in range(0, len(pages), group_size):
        group = [str(page or "") for page in pages[start:start + group_size]]
        lines = [line.strip() for page in group for line in page.splitlines()
                 if line.strip() and not PAGE_NO.match(line.strip())]
        text = join_lines(lines)
        if len(text) < 10:
            continue
        name, student_no = inline_identity("\n".join(lines[:12]))
        essays.append({
            "studentName": name or f"第{len(essays) + 1}篇",
            "studentNoHint": student_no,
            "studentTitle": likely_essay_title(
                [line for line in lines if not is_identity_metadata_line(line)]),
            "text": text,
            "chars": len(text),
            "sourcePages": list(range(start + 1, min(len(pages), start + group_size) + 1)),
        })
    return essays


def iter_docx_content(document):
    """按文档顺序遍历 Word 段落和表格。"""
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield DocxParagraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield DocxTable(child, document)


def parse_docx_content(content):
    if load_docx is None:
        raise ValueError("缺少 Word 解析依赖，请运行 pip install python-docx")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            expanded_size = sum(item.file_size for item in archive.infolist())
            if expanded_size > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise ValueError(
                    f"Word 解压后不能超过 {MAX_DOCX_UNCOMPRESSED_BYTES // 1024 // 1024} MB")
    except ValueError:
        raise
    except zipfile.BadZipFile as exc:
        raise ValueError("Word 文件损坏或不是有效的 .docx 文件") from exc

    try:
        document = load_docx(io.BytesIO(content))
    except Exception as exc:
        raise ValueError(f"Word 解析失败：{exc}") from exc

    blocks = [[]]
    paragraph_count = 0
    explicit_page_breaks = 0
    for item in iter_docx_content(document):
        if isinstance(item, DocxParagraph):
            paragraph_count += 1
            if paragraph_count > MAX_DOCX_PARAGRAPHS:
                raise ValueError(f"Word 段落不能超过 {MAX_DOCX_PARAGRAPHS} 个")
            text = (item.text or "").replace("\v", "\n")
            blocks[-1].extend(line.strip() for line in text.splitlines() if line.strip())
            if item._p.xpath(".//w:br[@w:type='page']"):
                explicit_page_breaks += 1
                if blocks[-1]:
                    blocks.append([])
        else:
            for row in item.rows:
                line = " ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if line:
                    blocks[-1].append(line)

    blocks = [block for block in blocks if block]
    if not blocks:
        raise ValueError("未能从 Word 中提取到文字，请确认文档不是纯图片或扫描件")

    essays = []
    if explicit_page_breaks:
        for block in blocks:
            split = split_metadata_essays(block, preserve_paragraphs=True)
            if split:
                essays.extend(split)
                continue
            text = "\n".join(block).strip()
            if len(text) >= 10:
                essays.append({
                    "studentName": f"第{len(essays) + 1}篇",
                    "studentNoHint": "",
                    "text": text,
                    "chars": len(text),
                })
        return essays

    all_lines = [line for block in blocks for line in block]
    split = split_metadata_essays(all_lines, preserve_paragraphs=True)
    if split:
        return split
    text = "\n".join(all_lines).strip()
    return ([{"studentName": "第1篇", "studentNoHint": "", "text": text,
              "chars": len(text)}] if len(text) >= 10 else [])


def parse_upload(file):
    """解析上传的 PDF / Word / TXT，返回作文候选列表。"""
    filename = file.filename or ""
    if not filename:
        raise ValueError("请选择要上传的文件")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    content = file.read()
    if not content:
        raise ValueError("上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    if ext == "pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            if len(reader.pages) > MAX_PDF_PAGES:
                raise ValueError(f"PDF 不能超过 {MAX_PDF_PAGES} 页")
            pages = [(p.extract_text() or "") for p in reader.pages]
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"PDF 解析失败：{e}")
        if not any(p.strip() for p in pages):
            raise ValueError("未能从 PDF 中提取到文字。请确认是文字版 PDF（扫描件/图片版无法识别，需要文字版或重新生成 PDF）")
        return split_pages_to_essays(pages)
    if ext in ("txt", "md"):
        text = content.decode("utf-8", errors="replace")
        lines = [l for l in text.split("\n")]
        return split_lines_to_essays(lines)
    if ext == "docx":
        return parse_docx_content(content)
    raise ValueError("仅支持 PDF、Word（.docx）或 TXT 文件")
