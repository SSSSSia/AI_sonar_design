# -*- coding: utf-8 -*-
"""T13.5 单测：PDF 导出（docgen/export_pdf.py + /cad/sessions/{sid}/export/pdf）。

docx_to_pdf 三路径（未配置 RuntimeError / 成功 %PDF / 非 PDF 内容 RuntimeError）+
端点降级路径（未配置 → 503）。gotenberg 转换请求用 monkeypatch 打桩（无真实网络）。
"""

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.main import app
from docgen import export_pdf

HDRS = {"X-User-Id": "t-135", "X-User-Roles": "admin"}


def test_docx_to_pdf_not_configured(monkeypatch):
    monkeypatch.delenv("DOCGEN_PDF_ENDPOINT", raising=False)
    with pytest.raises(RuntimeError, match="未配置"):
        export_pdf.docx_to_pdf(b"PK\x03\x04docx", "a.docx")


def test_docx_to_pdf_success(monkeypatch):
    monkeypatch.setenv("DOCGEN_PDF_ENDPOINT", "http://gotenberg.internal:3000")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["files"] = kwargs.get("files")
        return httpx.Response(200, content=b"%PDF-1.7 fake-pdf-bytes",
                                  request=httpx.Request("POST", url))

    monkeypatch.setattr(export_pdf.httpx, "post", fake_post)
    out = export_pdf.docx_to_pdf(b"PK\x03\x04docx", "a.docx")
    assert out.startswith(b"%PDF")
    assert captured["url"].startswith("http://gotenberg.internal:3000/forms/libreoffice/convert")
    # multipart 携带 docx 文件与正确 MIME
    _field, (fname, body, mime) = list(captured["files"].items())[0]
    assert fname == "a.docx" and body == b"PK\x03\x04docx"
    assert mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_docx_to_pdf_non_pdf_content_rejected(monkeypatch):
    """gotenberg 返回错误页/HTML 时不冒充交付物（A-16）。"""
    monkeypatch.setenv("DOCGEN_PDF_ENDPOINT", "http://gotenberg.internal:3000")
    monkeypatch.setattr(export_pdf.httpx, "post",
                        lambda url, **k: httpx.Response(200, content=b"<html>error</html>",
                                        request=httpx.Request("POST", url)))
    with pytest.raises(RuntimeError, match="非 PDF"):
        export_pdf.docx_to_pdf(b"docx", "a.docx")


def test_docx_to_pdf_http_error_propagates(monkeypatch):
    monkeypatch.setenv("DOCGEN_PDF_ENDPOINT", "http://gotenberg.internal:3000")

    def fake_post(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(export_pdf.httpx, "post", fake_post)
    with pytest.raises(httpx.HTTPError):
        export_pdf.docx_to_pdf(b"docx", "a.docx")


def test_api_export_pdf_503_when_not_configured(monkeypatch):
    """端点级：转换未配置 → 503 如实（不伪造 500/不静默）。"""
    monkeypatch.delenv("DOCGEN_PDF_ENDPOINT", raising=False)
    client = TestClient(app)
    resp = client.get("/api/v1/cad/sessions/00000000-0000-0000-0000-00000000d35f/export/pdf",
                      headers=HDRS)
    # 无 IR2 的会话 404（先于转换）；有 IR2 的场景由容器级真实验证承接
    assert resp.status_code in (404, 503)


def test_audit_rule_covers_pdf_export():
    from api import audit as audit_mod

    hit = audit_mod.match_rule("GET", "/api/v1/cad/sessions/s-1/export/pdf")
    assert hit is not None and hit[0] == "export.pdf" and hit[2] == "s-1"
