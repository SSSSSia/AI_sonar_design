"""embedding 客户端（T3.2）：复用当前 LLM 供应商的 /embeddings 接口（BAAI/bge-m3，1024 维）。

不可用返回 None——调用方如实降级（向量层跳过、检索落文本级），绝不伪造向量（A-16）。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app_config import KB_EMBED_DIM, KB_EMBED_MODEL, LLM_MAX_RETRIES

logger = logging.getLogger("sonarcad.kb.embed")


def embed_texts(texts: list[str], timeout_s: int = 60) -> list[list[float]] | None:
    """批量向量化。失败/维度不符返回 None（降级），成功返回与输入等长的向量列表。"""
    if not texts:
        return []
    try:
        from llm.adapter import active_provider

        provider = active_provider()
    except RuntimeError as exc:
        logger.warning("embedding 跳过：无可用供应商 %s", exc)
        return None
    url = provider.base_url.rstrip("/") + "/embeddings"
    headers = {"Authorization": f"Bearer {provider.api_key.get_secret_value()}"}
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            resp = httpx.post(
                url,
                headers=headers,
                json={"model": KB_EMBED_MODEL, "input": texts},
                timeout=timeout_s,
            )
            if resp.status_code != 200:
                logger.warning("embedding HTTP %s（第 %d 次）: %s",
                               resp.status_code, attempt + 1, resp.text[:200])
                continue
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            vectors = [d["embedding"] for d in data]
            if len(vectors) != len(texts):
                logger.warning("embedding 返回数量不符 %d != %d", len(vectors), len(texts))
                return None
            if vectors and len(vectors[0]) != KB_EMBED_DIM:
                logger.warning("embedding 维度不符 %d != %d", len(vectors[0]), KB_EMBED_DIM)
                return None
            return vectors
        except Exception as exc:
            logger.warning("embedding 异常（第 %d 次）: %s", attempt + 1, exc)
    return None
