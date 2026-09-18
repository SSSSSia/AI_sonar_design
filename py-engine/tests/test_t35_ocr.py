# -*- coding: utf-8 -*-
"""OCR 插件单测（D-14 落地）：版面标记清洗 / 扫描页识别接线 / 降级与页数预算 / 真实 API（可跳过）。"""

import io
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 预载仓库根 .env（last-wins，与 e2e conftest 同语义）——须在 kb/app_config 导入前
_env_file = Path(__file__).resolve().parents[2] / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k, _v)

from kb import ocr as kb_ocr
from kb.parsers import parse_document
from tests.test_t31_kb import _make_pdf


def _blank_pdf(pages: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_strip_layout_markers():
    raw = "\ntowed array knowledge<|LOC_116|><|LOC_73|><|LOC_421|><|LOC_73|>\nDI = 10 lg N<|LOC_120|><|LOC_103|>"
    assert kb_ocr.strip_layout_markers(raw) == "towed array knowledge\nDI = 10 lg N"
    assert kb_ocr.strip_layout_markers("") == ""


def test_scanned_pages_ocr_success(monkeypatch):
    """扫描页经 OCR 插件识别出文本单元（location 保持 第N页），ocr_pages 计数。"""
    monkeypatch.setattr(kb_ocr, "KB_OCR_ENABLED", True)
    data = _blank_pdf(2)
    result = parse_document("扫描件.pdf", data, ocr_page=lambda page: "识别出的正文", ocr_max_pages=10)
    assert result.ocr_pages == 2 and result.ocr_pending == 0
    assert [u.location for u in result.units] == ["第1页", "第2页"]
    assert result.units[0].text == "识别出的正文"


def test_scanned_pages_ocr_failure_degrades_honestly(monkeypatch):
    """OCR 失败/空结果 → ocr_pending 如实降级（A-16），不伪造文本。"""
    monkeypatch.setattr(kb_ocr, "KB_OCR_ENABLED", True)
    result = parse_document("扫描件.pdf", _blank_pdf(2),
                            ocr_page=lambda page: None, ocr_max_pages=10)
    assert result.ocr_pages == 0 and result.ocr_pending == 2 and result.units == []
    # OCR 抛异常同样降级
    def boom(page):
        raise RuntimeError("识别服务挂了")

    result = parse_document("扫描件.pdf", _blank_pdf(1), ocr_page=boom, ocr_max_pages=10)
    assert result.ocr_pending == 1 and result.units == []


def test_ocr_page_budget(monkeypatch):
    """页数预算护栏：超出预算的扫描页维持 pending（成本护栏，如实计数）。"""
    monkeypatch.setattr(kb_ocr, "KB_OCR_ENABLED", True)
    result = parse_document("扫描件.pdf", _blank_pdf(3),
                            ocr_page=lambda page: "识别文本", ocr_max_pages=1)
    assert result.ocr_pages == 1 and result.ocr_pending == 2


def test_ocr_disabled_keeps_pending_path(monkeypatch):
    """OCR 未启用（回调 None）＝阶段 3 原行为：全部 pending。"""
    result = parse_document("扫描件.pdf", _blank_pdf(2), ocr_page=None, ocr_max_pages=10)
    assert result.ocr_pending == 2 and result.ocr_pages == 0


def test_make_page_ocr_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(kb_ocr, "KB_OCR_ENABLED", False)
    assert kb_ocr.make_page_ocr() is None


def test_ocr_image_bytes_real_api():
    """真实调用 SiliconFlow PaddleOCR-VL-1.5（无供应商则跳过）：文本页渲染 → OCR → 内容还原。"""
    import httpx

    from app_config import LLM_API_KEY, LLM_BASE_URL

    if os.environ.get("KB_OCR_REALTEST", "") not in ("1", "true"):
        pytest.skip("真实 OCR API 调用默认关闭（KB_OCR_REALTEST=1 开启）")
    if not (LLM_BASE_URL and LLM_API_KEY and not LLM_API_KEY.startswith("your_api")):
        pytest.skip("无可用供应商")
    if "siliconflow" not in LLM_BASE_URL.lower():
        pytest.skip("非 SiliconFlow 供应商，模型不可用")

    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1200, 400), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    draw.text((60, 80), "sonar array knowledge page", fill="black", font=font)
    draw.text((60, 200), "DI = 10 lg N", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    text = kb_ocr.ocr_image_bytes(buf.getvalue())
    assert text is not None
    assert "<|LOC_" not in text  # 版面标记必须被清洗
    low = text.lower()
    assert "sonar" in low and "10" in low, f"识别结果意外: {text!r}"


def test_ocr_image_bytes_http_failure_returns_none(monkeypatch):
    class FakeProvider:
        base_url = "http://127.0.0.1:1"
        api_key = None

        def get_secret_value(self):
            return "k"

    monkeypatch.setattr(kb_ocr, "KB_OCR_ENABLED", True)
    monkeypatch.setattr("llm.adapter.active_provider", lambda: FakeProvider())
    assert kb_ocr.ocr_image_bytes(b"png") is None  # 连接失败如实 None
