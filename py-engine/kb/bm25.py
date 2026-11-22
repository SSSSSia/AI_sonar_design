# -*- coding: utf-8 -*-
"""BM25 稀疏检索路（T17.1，PHASE-017；契约=ADR-016 §1）。

三路混合检索（bm25/vector/graph → RRF）的稀疏路基础设施。本模块只做「语料 → BM25 索引
→ 检索」与 TTL 懒加载，**不接检索主链**（T17.2 融合层接线并演进 level 枚举）；
纯加法模块，零现有行为改动。

- 不建持久索引：内存重建秒级（语料 439 chunks 量级），进程重启即重建；语料 TTL 懒加载
  对齐 GraphRAGService._ensure_corpus（service.py:132-149）跨进程一致性口径——kb_build
  在 worker 进程写库，本进程按 TTL 懒加载，三条纪律逐条对齐：TTL 内不重载 / **失败也
  计时**（避免每查询打库，service.py:149 先例）/ 失败沿用上一份内存索引（service.py:146
  「沿用内存语料」同口径）。
- 分词口径（ADR-016 §1：沿全仓「空格词+中文 2-gram」，零新分词依赖）：查询侧与语料侧
  共用 tokenize 保证 token 空间一致；文本兜底级（service.py:474-477）对原始串直接切
  gram（含标点），BM25 语料侧长文档先剥离标点再切 gram——同一口径的语料侧必要修正，
  文本兜底级不动、与本模块独立。
- doc_ids 语义为**显式安全口径**：None=不过滤、[] = 严格空集（必然零命中）——与
  kb/vector_store「空列表=不过滤」的已知 footgun 不同（该处已由 service.py:197-203
  上游护栏兜住），本模块选择更安全契约并在 search docstring 明示。
- 诚实边界（A-14/A-16）：BM25 分数仅用于排序，不做绝对相关度语义（零分不产命中）；
  语料空/加载失败 → bm25_search 返回 None（调用方降级处理），last_error 如实上报。
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("sonarcad.kb.bm25")

# TTL 对齐 GraphRAGService._ensure_corpus（service.py:136）
CORPUS_TTL_S = 30
DEFAULT_TOP_K = 5

# 拉丁/数字词；CJK 连续段（基本区，与 service.py:476 判定范围一致）
_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: Optional[str]) -> list[str]:
    """查询/语料两侧共用的分词（ADR-016 §1 口径；零新分词依赖）。

    - 拉丁/数字：小写化按非字母数字切分，保留长度>1 的词（service.py:475 同口径）；
    - 中文：连续汉字段 2-gram（单字段保留单字）；
    - 标点/空白剥离（语料侧必要修正，见模块 docstring）。
    """
    if not text:
        return []
    lower = str(text).lower()
    tokens: list[str] = [w for w in _WORD_RE.findall(lower) if len(w) > 1]
    for run in _CJK_RE.findall(lower):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class Bm25Index:
    """内存 BM25 索引。corpus = [{doc_id, location, text}]（kb_store.all_indexed_chunks 同形）。"""

    def __init__(self, corpus: Optional[list[dict[str, Any]]]) -> None:
        self._docs: list[dict[str, str]] = [
            {
                "doc_id": str(c.get("doc_id") or ""),
                "location": str(c.get("location") or ""),
                "text": str(c.get("text") or ""),
            }
            for c in (corpus or [])
        ]
        self._tokenized = [tokenize(d["text"]) for d in self._docs]
        if self._docs:
            from rank_bm25 import BM25Okapi  # 延迟 import：空语料/纯分词测试不触依赖

            self._bm25 = BM25Okapi(self._tokenized)
        else:
            self._bm25 = None

    @property
    def size(self) -> int:
        return len(self._docs)

    @property
    def fingerprint(self) -> Optional[str]:
        """语料指纹（T17.2 缓存键融合地基）：sha1[:16] over doc_id|location|len(text)。"""
        if not self._docs:
            return None
        material = "|".join(
            f"{d['doc_id']}:{d['location']}:{len(d['text'])}" for d in self._docs
        )
        return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]

    def search(
        self, query: str, top_k: int = DEFAULT_TOP_K, doc_ids: Optional[list[str]] = None
    ) -> list[dict[str, Any]]:
        """按 BM25 分降序返回 [{doc_id, location, text, score}]。

        doc_ids 语义（显式安全口径，与 vector_store 不同，见模块 docstring）：
        None = 不过滤；[] = 严格空集（必然零命中）。
        零分不入结果（无信号不造命中，A-16）。
        """
        if self._bm25 is None or not query:
            return []
        if doc_ids is not None and not doc_ids:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores = self._bm25.get_scores(q_tokens)
        order = sorted(range(len(self._docs)), key=lambda i: float(scores[i]), reverse=True)
        allowed: Optional[set[str]] = None if doc_ids is None else set(doc_ids)
        out: list[dict[str, Any]] = []
        for i in order:
            score = float(scores[i])
            if score <= 0:
                break  # 分数降序，其后全为零分
            if allowed is not None and self._docs[i]["doc_id"] not in allowed:
                continue
            out.append({**self._docs[i], "score": score})
            if len(out) >= top_k:
                break
        return out


def _default_corpus_loader() -> list[dict[str, Any]]:
    from storage import kb_store  # 延迟 import：注入 loader 的测试不触 DB

    return kb_store.all_indexed_chunks()


class Bm25Retriever:
    """TTL 懒加载 BM25 检索器（纪律逐条对齐 service.py _ensure_corpus，见模块 docstring）。"""

    def __init__(
        self,
        corpus_loader: Optional[Callable[[], list[dict[str, Any]]]] = None,
        ttl_s: int = CORPUS_TTL_S,
    ) -> None:
        self._loader = corpus_loader or _default_corpus_loader
        self._ttl_s = ttl_s
        self._index: Optional[Bm25Index] = None
        self._loaded_at: float = 0.0
        self._load_attempted: bool = False
        self.last_error: Optional[str] = None

    def _ensure_index(self) -> Optional[Bm25Index]:
        now = time.time()
        if self._load_attempted and now - self._loaded_at < self._ttl_s:
            return self._index
        try:
            corpus = self._loader() or []
            self._index = Bm25Index(corpus)
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - 检索路失败不中断主链路（A-14 如实降级）
            # 沿用上一份内存索引（service.py:146「沿用内存语料」同口径）；从未成功过则 None
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("BM25 语料懒加载失败（沿用内存索引，TTL 内不再重试）: %s", self.last_error)
        self._loaded_at = now  # 失败也计时（service.py:149 先例：避免每查询打库）
        self._load_attempted = True
        return self._index

    def search(
        self, query: str, top_k: int = DEFAULT_TOP_K, doc_ids: Optional[list[str]] = None
    ) -> Optional[list[dict[str, Any]]]:
        """语料空/从未成功加载 → None（调用方降级）；否则委托 Bm25Index.search。"""
        idx = self._ensure_index()
        if idx is None or idx.size == 0:
            return None
        return idx.search(query, top_k=top_k, doc_ids=doc_ids)

    def status(self) -> dict[str, Any]:
        """诚实上报（T17.2 接 /knowledge/status）：加载态与最近失败原因（不含敏感）。"""
        idx = self._index
        return {
            "size": idx.size if idx else 0,
            "loaded": idx is not None,
            "loaded_at": self._loaded_at or None,
            "ttl_s": self._ttl_s,
            "last_error": self.last_error,
        }

    @property
    def fingerprint(self) -> Optional[str]:
        return self._index.fingerprint if self._index else None


_retriever: Optional[Bm25Retriever] = None


def get_bm25_retriever() -> Bm25Retriever:
    """进程内单例（索引构建廉价但避免每查询重建）。"""
    global _retriever
    if _retriever is None:
        _retriever = Bm25Retriever()
    return _retriever


def bm25_search(
    query: str, top_k: int = DEFAULT_TOP_K, doc_ids: Optional[list[str]] = None
) -> Optional[list[dict[str, Any]]]:
    """便捷入口（T17.2 融合层调用形态）：语料空/加载失败 → None。"""
    return get_bm25_retriever().search(query, top_k=top_k, doc_ids=doc_ids)
