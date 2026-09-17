# -*- coding: utf-8 -*-
"""
知识库检索服务（T1.6，迁移自旧仓库 graphrag_service/service.py ★直接迁移 + v2 适配）
=====================================================================================

对 Agent 暴露统一检索接口。v2 适配点：
1. **结构化结果**：返回 RetrievalResult（content + 命中层级 + 降级轨迹），
   降级事件对上游可见（A-14/A-16 诚实交代），Agent 不得把降级结果伪装成图谱命中；
2. **lazy import**：GraphRAG SDK（graphrag/litellm 重依赖）延迟加载，
   未安装时自动落到文本级，测试与轻量环境可跑；
3. **路径可配**：KB 目录走 app_config（KNOWLEDGE_KB_DIR，默认 py-engine/knowledge/kb）。

三级降级策略（A-14）：local（图谱检索）→ basic（语义向量）→ text（关键词），
任一级失败降级不报错中断。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("sonarcad.knowledge")

from app_config import KNOWLEDGE_KB_DIR


@dataclass
class RetrievalResult:
    """统一检索结果。level ∈ {local, basic, text, unavailable}。"""

    content: str
    level: str
    degraded_from: list[str] = field(default_factory=list)  # 经过的降级轨迹，如 ["local","basic"]

    @property
    def ok(self) -> bool:
        return self.level != "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return {"content": self.content, "level": self.level, "degraded_from": self.degraded_from}


def _load_sdk(kb_dir: Path):
    """lazy 加载 GraphRAG SDK；不可用返回 None（自然落到文本级）。"""
    try:
        from knowledge.sdk import GraphRAGSDK  # 延迟 import：graphrag 是重依赖

        return GraphRAGSDK(root_dir=str(kb_dir))
    except Exception as exc:
        logger.warning("GraphRAG SDK 不可用（未安装或初始化失败），检索将使用文本级: %s", exc)
        return None


class GraphRAGService:
    """知识库检索服务（同步接口；worker/A1 在同步上下文使用）。

    使用示例:
        svc = GraphRAGService()
        r = svc.query("拖曳线列阵声呐的系统组成")
        r.content / r.level / r.degraded_from
    """

    def __init__(self, kb_dir: Optional[str | Path] = None):
        self.kb_dir = Path(kb_dir or KNOWLEDGE_KB_DIR).resolve()
        self.sdk = _load_sdk(self.kb_dir)

        # 文本级降级的语料：input 目录全部 txt
        self.kb_text: Optional[str] = None
        input_dir = self.kb_dir / "input"
        if input_dir.is_dir():
            texts = sorted(input_dir.glob("*.txt"))
            if texts:
                self.kb_text = "\n\n".join(t.read_text(encoding="utf-8", errors="ignore") for t in texts)

    # ------------------------------------------------------------------
    # 单条查询（三级降级）
    # ------------------------------------------------------------------
    def query(self, query: str) -> RetrievalResult:
        degraded: list[str] = []

        if self.sdk is not None:
            # 第一级 local：图谱检索
            result = self._try_sdk("query_local_sync", query)
            if result is not None:
                return RetrievalResult(result, "local", degraded)
            degraded.append("local")

            # 第二级 basic：语义向量
            result = self._try_sdk("query_basic_sync", query)
            if result is not None:
                return RetrievalResult(result, "basic", degraded)
            degraded.append("basic")

        # 第三级 text：关键词
        if self.kb_text:
            if self.sdk is None and not degraded:
                degraded.append("graphrag_sdk_unavailable")  # 如实交代为何直接落在文本级
            return RetrievalResult(self._search_text(query), "text", degraded)

        return RetrievalResult(
            "知识库不可用：GraphRAG SDK 未初始化且知识库文本未加载", "unavailable", degraded
        )

    # ------------------------------------------------------------------
    # 批量查询（并发 + 逐条降级，A1 的 4~8 条子查询用）
    # ------------------------------------------------------------------
    def query_batch(self, queries: list[str]) -> list[RetrievalResult]:
        if self.sdk is not None:
            try:
                results = self.sdk.query_batch_sync_with_fallback(queries)
                out: list[RetrievalResult] = []
                for q, r in zip(queries, results):
                    if r.get("success"):
                        out.append(RetrievalResult(r.get("response", ""), "local"))
                    elif self.kb_text:
                        out.append(RetrievalResult(self._search_text(q), "text", ["local", "basic"]))
                    else:
                        out.append(RetrievalResult(f"查询失败: {r.get('error', '未知错误')}", "unavailable"))
                return out
            except Exception as exc:
                logger.warning("批量查询异常，降级到逐条文本搜索: %s", exc)

        if self.kb_text:
            return [RetrievalResult(self._search_text(q), "text") for q in queries]
        return [RetrievalResult("知识库不可用", "unavailable") for _ in queries]

    # ------------------------------------------------------------------
    # 索引构建（T3.2 全管线接管前的手工入口）
    # ------------------------------------------------------------------
    def build_index(self, verbose: bool = True) -> dict:
        if self.sdk is None:
            return {"success": False, "message": "GraphRAG SDK 未初始化"}
        return self.sdk.build_index_sync(verbose=verbose, is_update=False)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _try_sdk(self, method: str, query: str) -> Optional[str]:
        try:
            result = getattr(self.sdk, method)(query)
            if result.get("success"):
                return result.get("response", "")
            logger.warning("%s 失败: %s", method, result.get("error", ""))
        except Exception as exc:
            logger.warning("%s 异常: %s", method, exc)
        return None

    def _search_text(self, query: str) -> str:
        """第三级：按段落关键词匹配（迁移自旧 service._search_text）。"""
        if not self.kb_text:
            return "知识库文本不可用"

        paragraphs = [p.strip() for p in self.kb_text.split("\n\n") if p.strip()]
        # 中英文混合：按空格 + 简单 2-gram 提关键词
        keywords = [kw.lower() for kw in query.split() if len(kw) > 1]
        if any("\u4e00" <= ch <= "\u9fff" for ch in query):
            keywords += [query[i : i + 2] for i in range(len(query) - 1)]

        scored: list[tuple[int, str]] = []
        for para in paragraphs:
            para_lower = para.lower()
            score = sum(1 for kw in keywords if kw in para_lower)
            if score > 0:
                scored.append((score, para))

        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored:
            return f"未找到精确匹配。知识库概览：\n\n{self.kb_text[:1500]}..."

        return (
            "知识库搜索结果（文本降级模式）：\n\n"
            + "\n\n---\n\n".join(para for _, para in scored[:5])
        )


_service: Optional[GraphRAGService] = None


def get_knowledge_service() -> GraphRAGService:
    """进程内单例（索引数据加载昂贵）。"""
    global _service
    if _service is None:
        _service = GraphRAGService()
    return _service
