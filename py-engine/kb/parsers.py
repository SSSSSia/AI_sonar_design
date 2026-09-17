"""文档解析器（T3.1，宪法 §6 ②）。

按格式路由解析，产物为**带位置信息**的文本单元列表（页码/章节路径/段落/工作表行），
供分块与溯源引用使用。OCR（MinerU/PP-Structure）为可选插件（D-14）——当前未接入，
PDF 空文本页（扫描页）如实计入 ocr_pending 并标注，绝不伪造文本。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

SUPPORTED_FORMATS = ("pdf", "docx", "xlsx", "md", "txt")


class UnsupportedFormatError(ValueError):
    """上传格式不在白名单（§10.4）。"""


def detect_format(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_FORMATS:
        raise UnsupportedFormatError(
            f"不支持的格式 .{ext}（白名单: {', '.join(SUPPORTED_FORMATS)}）"
        )
    return ext


@dataclass
class ParsedUnit:
    """一个带位置的文本单元（分块与溯源的最小输入）。"""

    location: str   # 段落N / 章节路径 / 第N页 / 工作表!行N
    text: str


@dataclass
class ParseResult:
    units: list[ParsedUnit] = field(default_factory=list)
    ocr_pending: int = 0      # 扫描页计数（仅 pdf；OCR 插件待接，D-14 如实标注）
    page_count: int = 0       # pdf 页数
    section_count: int = 0    # md/docx 章节数


def parse_document(filename: str, data: bytes) -> ParseResult:
    """按扩展名路由解析器。任何格式解析异常由调用方处理（doc 状态=failed，不中断服务）。"""
    fmt = detect_format(filename)
    parser = {
        "txt": _parse_txt, "md": _parse_md, "pdf": _parse_pdf,
        "docx": _parse_docx, "xlsx": _parse_xlsx,
    }[fmt]
    return parser(data)


# ----------------------------------------------------------------------
def _parse_txt(data: bytes) -> ParseResult:
    text = data.decode("utf-8", errors="replace")
    result = ParseResult()
    for i, para in enumerate(p.strip() for p in re.split(r"\n\s*\n", text)):
        if para:
            result.units.append(ParsedUnit(location=f"段落{i + 1}", text=para))
    return result


def _parse_md(data: bytes) -> ParseResult:
    """Markdown：按标题树切章节，location = 章节路径（§6 ② 章节位置）。"""
    text = data.decode("utf-8", errors="replace")
    result = ParseResult()
    path: list[str] = []          # 当前标题路径（# > ## > ###）
    buf: list[str] = []
    pending_root = "（前言）"

    def flush() -> None:
        body = "\n".join(b for b in buf if b.strip()).strip()
        buf.clear()
        if body:
            result.units.append(ParsedUnit(location=" > ".join(path) or pending_root, text=body))

    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            path = path[: level - 1] + [title]
            result.section_count += 1
        else:
            buf.append(line)
    flush()
    return result


def _parse_pdf(data: bytes) -> ParseResult:
    """PDF：pypdf 逐页提取，location=第N页；空文本页=扫描页，计入 ocr_pending（D-14）。"""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    result = ParseResult(page_count=len(reader.pages))
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            result.units.append(ParsedUnit(location=f"第{i}页", text=text))
        else:
            result.ocr_pending += 1   # 扫描页：OCR 插件待接（D-14），如实计数不伪造
    return result


def _parse_docx(data: bytes) -> ParseResult:
    """Word：heading 样式驱动章节路径，location = 章节#段落N。"""
    import docx

    document = docx.Document(io.BytesIO(data))
    result = ParseResult()
    chapter: list[str] = []
    para_no = 0
    buf: list[str] = []

    def flush() -> None:
        nonlocal para_no
        body = "\n".join(buf).strip()
        buf.clear()
        if body:
            para_no += 1
            loc = " > ".join(chapter) if chapter else "（正文）"
            result.units.append(ParsedUnit(location=f"{loc}#段落{para_no}", text=body))

    for para in document.paragraphs:
        text = (para.text or "").strip()
        style = (para.style.name or "") if para.style is not None else ""
        if style.startswith(("Heading", "标题")) and text:
            flush()
            try:
                level = int(style.split()[-1]) if style.split()[-1].isdigit() else 1
            except ValueError:
                level = 1
            chapter = chapter[: level - 1] + [text]
            result.section_count += 1
        elif text:
            buf.append(text)
    flush()
    return result


def _parse_xlsx(data: bytes) -> ParseResult:
    """Excel：逐工作表逐行，location = 工作表!行N（行内单元格 | 连接）。"""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    result = ParseResult()
    for ws in wb.worksheets:
        for row_idx, row in enumerate(ws.iter_rows(values_only=True), 1):
            cells = ["" if v is None else str(v).strip() for v in row]
            line = " | ".join(c for c in cells if c)
            if line:
                result.units.append(ParsedUnit(location=f"{ws.title}!行{row_idx}", text=line))
    wb.close()
    return result
