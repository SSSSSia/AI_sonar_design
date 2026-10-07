# -*- coding: utf-8 -*-
"""交叉编码器重排序客户端（T13.6，总体设计 §7.4「交叉编码器重排序」）。

配置驱动、默认不开：KB_RERANK_ENDPOINT + KB_RERANK_MODEL 配置后才启用（密钥入
.env，宪法 §10）。请求格式兼容 Jina / Cohere / SiliconFlow / vLLM 的 /rerank 形态：
POST {model, query, documents, top_n} → {results: [{index, relevance_score|score}]}。

语义口径（A-14 如实）：
- 重排序由供应商侧交叉编码器承担（HTTP API），非本地 torch 模型——避免 py 容器
  重依赖；「多路召回+重排序」中本模块覆盖 vector 路召回的重排，graph 路为实体引用
  （无 chunk 文本可排）维持既有融合顺序；
- 未配置/失败一律跳过并维持原序（检索主链路不受影响），失败原因落日志；
- 实测状态如实标注：无真实供应商 key 时仅 mock 单测覆盖（A-16 不造假）。
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("sonarcad.kb.rerank")

# 默认召回扩量：先取 fetch_k 条候选，重排后截断回 final_k（§7.4 混合检索语义）
DEFAULT_FETCH_K = 20
DEFAULT_FINAL_K = 5


class RerankClient:
    """/rerank HTTP 客户端。available() = endpoint+model 均已配置；单例经 get_reranker()。"""

    def __init__(self) -> None:
        self.endpoint = os.environ.get("KB_RERANK_ENDPOINT", "").strip().rstrip("/")
        self.model = os.environ.get("KB_RERANK_MODEL", "").strip()
        self.api_key = os.environ.get("KB_RERANK_API_KEY", "").strip()
        self.fetch_k = int(os.environ.get("KB_RERANK_FETCH_K", str(DEFAULT_FETCH_K)))
        self.top_n = int(os.environ.get("KB_RERANK_TOPN", str(DEFAULT_FINAL_K)))
        self.last_error: str | None = None

    def available(self) -> bool:
        return bool(self.endpoint and self.model)

    def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[tuple[int, float]]:
        """重排：返回 [(原索引, 相关性分)] 按分数降序。失败抛异常（调用方决定降级）。"""
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = httpx.post(
            f"{self.endpoint}/rerank",
            headers=headers,
            json={"model": self.model, "query": query, "documents": documents,
                  "top_n": top_n if top_n is not None else self.top_n},
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
        )
        resp.raise_for_status()
        payload = resp.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ValueError(f"rerank 响应缺少 results 数组：{type(payload).__name__}")
        out: list[tuple[int, float]] = []
        for r in results:
            idx = r.get("index")
            score = r.get("relevance_score", r.get("score"))
            if idx is None or score is None:
                raise ValueError("rerank 响应缺少 index/relevance_score 字段")
            out.append((int(idx), float(score)))
        out.sort(key=lambda x: x[1], reverse=True)
        return out

    def apply(self, query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """对 vector hits（[{text, ...}]）重排并截断至 top_n；失败维持原序（A-14 如实降级）。"""
        if not hits:
            return hits
        docs = [h.get("text") or "" for h in hits]
        try:
            ranked = self.rerank(query, docs)
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - 重排失败不影响检索主链路
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("重排序失败，维持向量原序（如实降级）：%s", self.last_error)
            return hits
        out: list[dict[str, Any]] = []
        for idx, score in ranked[: self.top_n]:
            if 0 <= idx < len(hits):
                out.append({**hits[idx], "rerank_score": score})
        return out if out else hits

    def status(self) -> dict[str, Any]:
        """诚实上报（/knowledge/status）：配置态与最近一次失败原因（不含密钥）。"""
        return {
            "configured": self.available(),
            "endpoint": self.endpoint or None,
            "model": self.model or None,
            "fetch_k": self.fetch_k,
            "top_n": self.top_n,
            "last_error": self.last_error,
        }


_reranker: RerankClient | None = None


def get_reranker() -> RerankClient:
    """进程内单例（env 热更不支持——重排序配置变更随进程重启，如实）。"""
    global _reranker
    if _reranker is None:
        _reranker = RerankClient()
    return _reranker
