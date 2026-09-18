"""OCR 插件（D-14 落地，T3.1 后续）：扫描页 → PaddleOCR-VL-1.5（SiliconFlow）。

接口形态（2026-09-18 实测）：OpenAI 兼容 chat/completions，图片走 base64 data URL；
返回文本带版面坐标标记 `<|LOC_x|>`（每行四个角点），清洗后为纯文本。
每页 ~1250 vision tokens、约 2s；失败/超限按页如实降级回 ocr_pending（A-16，不伪造文本）。
"""

from __future__ import annotations

import base64
import io
import logging
import re
import time
from typing import Any, Callable

import httpx

from app_config import (
    KB_OCR_ENABLED,
    KB_OCR_MAX_PAGES,
    KB_OCR_MODEL,
    KB_OCR_RENDER_SCALE,
    KB_OCR_TIMEOUT_S,
)

logger = logging.getLogger("sonarcad.kb.ocr")

_LOC_MARKER = re.compile(r"<\|LOC_\d+\|>")


def strip_layout_markers(text: str) -> str:
    """清洗 PaddleOCR-VL 输出的版面坐标标记（保留正文）。"""
    return _LOC_MARKER.sub("", text or "").strip()


def render_pdf_page_png(page: Any, scale: float = KB_OCR_RENDER_SCALE) -> bytes:
    """pypdfium2 页对象 → PNG 字节（供 VL 模型识别）。"""
    import pypdfium2 as pdfium

    if not isinstance(page, pdfium.PdfPage):
        raise TypeError(f"期望 pypdfium2.PdfPage，得到 {type(page)}")
    bitmap = page.render(scale=scale)
    buf = io.BytesIO()
    bitmap.to_pil().save(buf, format="PNG")
    return buf.getvalue()


def ocr_image_bytes(png: bytes) -> str | None:
    """单页 PNG → OCR 文本（已清洗坐标标记）。失败返回 None（调用方如实降级）。"""
    if not KB_OCR_ENABLED:
        return None
    try:
        from llm.adapter import active_provider

        provider = active_provider()
    except RuntimeError as exc:
        logger.warning("OCR 跳过：无可用供应商 %s", exc)
        return None
    url = provider.base_url.rstrip("/") + "/chat/completions"
    b64 = base64.b64encode(png).decode()
    t0 = time.time()
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {provider.api_key.get_secret_value()}"},
            json={
                "model": KB_OCR_MODEL,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": "Transcribe all the text content in the image."},
                ]}],
                "temperature": 0,   # OCR 要确定性输出
                "stream": False,
            },
            timeout=KB_OCR_TIMEOUT_S,
        )
    except Exception as exc:
        logger.warning("OCR 请求异常: %s", exc)
        return None
    if resp.status_code != 200:
        logger.warning("OCR HTTP %s: %s", resp.status_code, resp.text[:200])
        return None
    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("OCR 响应结构异常: %s", exc)
        return None
    logger.info("OCR 单页完成 %.1fs tokens=%s",
                time.time() - t0, resp.json().get("usage", {}).get("total_tokens"))
    return strip_layout_markers(content)


def make_page_ocr() -> Callable[[Any], str | None] | None:
    """给解析器的整页回调：pdfium 页对象 → 文本（None=该页 OCR 失败，调用方降级）。

    返回 None 表示 OCR 未启用/无供应商（解析器据此维持 ocr_pending 老路径）。
    """

    def _ocr_page(page: Any) -> str | None:
        png = render_pdf_page_png(page)
        return ocr_image_bytes(png)

    return _ocr_page if KB_OCR_ENABLED else None


# 供解析器使用的默认配置出口（避免解析器直接依赖本模块常量）
OCR_MAX_PAGES = KB_OCR_MAX_PAGES
