# -*- coding: utf-8 -*-
"""视觉 LLM 客户端（T17.5 落地 = T18.1 提前交付，PHASE-017/018；契约=ADR-016 §5/§6）。

**照 kb/ocr.py 先例独立直调**（active_provider + OpenAI 兼容 chat/completions +
image_url base64），不动 LlmAdapter 主链——adapter 的缓存键/流式解析/脱敏三处纯文本
假设不碰（ADR-016 §6 决策）。供应商=llm_providers 现网 enabled 行（SiliconFlow：
zai-org/GLM-4.5V / Qwen/Qwen2.5-VL-32B-Instruct，env LLM_VISION_MODEL 可切）。

失败返回 None（调用方如实降级，A-14/A-16）；temperature 0.2（描述性任务，
低于生成默认、高于 OCR 的 0——caption 允许同图微差表述）。
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Optional

import httpx

import app_config

logger = logging.getLogger("sonarcad.llm.vision")


def describe_images(png_bytes_list: list[bytes], prompt: str,
                    model: Optional[str] = None) -> Optional[str]:
    """多图 → VLM 文本描述。失败/无供应商返回 None（调用方如实降级）。"""
    if not png_bytes_list:
        return None
    try:
        from llm.adapter import active_provider

        provider = active_provider()
    except RuntimeError as exc:
        logger.warning("vision 跳过：无可用供应商 %s", exc)
        return None
    content: list[dict] = []
    for png in png_bytes_list:
        b64 = base64.b64encode(png).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}})
    content.append({"type": "text", "text": prompt})
    url = provider.base_url.rstrip("/") + "/chat/completions"
    t0 = time.time()
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {provider.api_key.get_secret_value()}"},
            json={
                "model": model or app_config.LLM_VISION_MODEL,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.2,
                "stream": False,
            },
            timeout=app_config.LLM_VISION_TIMEOUT_S,
        )
    except Exception as exc:
        logger.warning("vision 请求异常: %s", exc)
        return None
    if resp.status_code != 200:
        logger.warning("vision HTTP %s: %s", resp.status_code, resp.text[:200])
        return None
    try:
        text = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("vision 响应结构异常: %s", exc)
        return None
    logger.info("vision 完成 %.1fs tokens=%s model=%s",
                time.time() - t0, resp.json().get("usage", {}).get("total_tokens"),
                model or app_config.LLM_VISION_MODEL)
    return str(text or "").strip() or None
