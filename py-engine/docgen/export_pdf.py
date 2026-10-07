# -*- coding: utf-8 -*-
"""docx → PDF 转换（T13.5，总体设计 §5.2「Word/PDF 设计说明书」交付物）。

转换走 gotenberg 容器（内置 LibreOffice，对 docx 的排版保真优于 HTML 重排路线），
完整复用既有 docx 模板链（T11.5 内置模板 / DOCX_TEMPLATE_PATH）。
端点由 DOCGEN_PDF_ENDPOINT 配置（缺省不启用——容器栈未部署 gotenberg 时端点 503 如实，
A-14）；转换失败/超时如实报错，不降级伪造。

边界如实：PDF 转换基于既有内置模板；「正式军标模板」仍待授权文本（外部依赖）。
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("sonarcad.docgen.pdf")

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def pdf_endpoint() -> str:
    return os.environ.get("DOCGEN_PDF_ENDPOINT", "").strip().rstrip("/")


def docx_to_pdf(docx_bytes: bytes, filename: str) -> bytes:
    """docx 字节流 → PDF 字节流（gotenberg POST /forms/libreoffice/convert）。

    未配置 → RuntimeError；HTTP 错误/超时 → httpx.HTTPError（调用方转 503）；
    返回非 %PDF 魔数 → RuntimeError（不把错误页当交付物，A-16）。
    """
    endpoint = pdf_endpoint()
    if not endpoint:
        raise RuntimeError("PDF 转换未配置（DOCGEN_PDF_ENDPOINT 缺省）——容器栈未部署 gotenberg")
    resp = httpx.post(
        f"{endpoint}/forms/libreoffice/convert",
        files={"files": (filename, docx_bytes, _DOCX_MIME)},
        timeout=httpx.Timeout(connect=10, read=120, write=30, pool=10),
    )
    resp.raise_for_status()
    if not resp.content.startswith(b"%PDF"):
        raise RuntimeError(
            f"gotenberg 返回非 PDF 内容（content-type={resp.headers.get('content-type')}）")
    return resp.content
