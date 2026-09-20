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
    """统一检索结果。level ∈ {local, basic, text, unavailable}。

    refs = 来源引用列表 [{doc_id, location}]（E2 溯源最小单元，宪法 §6 ②）：
    - text 级：doc_id=来源文件名、location=段落序号（T3 分块管线上线后升级为页码/章节）
    - local/basic 级：由 GraphRAG context 提供，当前为空（SDK 接入后补全）
    """

    content: str
    level: str
    degraded_from: list[str] = field(default_factory=list)
    refs: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.level != "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "level": self.level,
            "degraded_from": self.degraded_from,
            "refs": self.refs,
        }


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

    A-14 三级降级（T3.2 补全，enable_kb_levels=True 时启用后两级）：
      local(SDK 图谱) → basic(SDK 向量) → graph(自建 Neo4j 实体层) → vector(Milvus) → text(关键词)
    测试场景可直接构造 GraphRAGService()（默认关 KB 扩展级，行为与阶段 1 一致）。
    """

    def __init__(self, kb_dir: Optional[str | Path] = None, enable_kb_levels: bool = False):
        self.kb_dir = Path(kb_dir or KNOWLEDGE_KB_DIR).resolve()
        self.sdk = _load_sdk(self.kb_dir)
        self.enable_kb_levels = enable_kb_levels

        # 文本级降级的语料：input 目录全部 txt（保留 (文件名, 段落) 结构供溯源）
        self.kb_text: Optional[str] = None
        self._paragraph_sources: list[tuple[str, int, str]] = []  # (doc_id, para_idx, text)
        input_dir = self.kb_dir / "input"
        if input_dir.is_dir():
            chunks: list[str] = []
            for t in sorted(input_dir.glob("*.txt")):
                for i, para in enumerate(
                        p.strip() for p in t.read_text(encoding="utf-8", errors="ignore").split("\n\n")
                ):
                    if para:
                        chunks.append(para)
                        self._paragraph_sources.append((t.name, i, para))
            if chunks:
                self.kb_text = "\n\n".join(chunks)

        # T3.2 动态语料（KB 管线 indexed 文档的 chunks）与 KB 扩展级可用性缓存
        self._dynamic_sources: list[tuple[str, str, str]] = []  # (doc_id, location, text)
        self._kb_probe_cache: dict[str, tuple[bool, float]] = {}  # name -> (available, ts)
        self._corpus_loaded_at: float = 0.0  # 动态语料懒加载时间戳（TTL 30s）

    # ------------------------------------------------------------------
    # PHASE-006 库范围解析（D6-2/D6-9）：kb_ids → 允许 doc 集合；None = 不过滤（图谱接口等全量场景）
    # ----------------------------------------------------------------------
    def _scope_doc_ids(self, kb_ids: Optional[list[str]]) -> Optional[list[str]]:
        if kb_ids is None:
            return None  # 调用方明确要全量（不经归属过滤）
        from storage import kb_scope

        return kb_scope.resolve_doc_ids(kb_ids or None)  # [] = "全部库"（有任一归属）

    # ------------------------------------------------------------------
    # T3.2 动态语料注册（kb_build 语料层调用 / 查询时按 TTL 懒加载）
    # ------------------------------------------------------------------
    def refresh_dynamic_corpus(self, sources: list[tuple[str, str, str]]) -> int:
        """用 KB 管线 indexed 文档的 chunks 替换动态语料（doc_id, location, text）。"""
        self._dynamic_sources = list(sources)
        import time

        self._corpus_loaded_at = time.time()
        logger.info("KB 动态语料已刷新: %d chunks", len(sources))
        return len(self._dynamic_sources)

    def _ensure_corpus(self) -> None:
        """跨进程一致性（T3.2）：kb_build 在 worker 进程写库，本进程按 TTL 懒加载。"""
        import time

        if self._corpus_loaded_at and time.time() - self._corpus_loaded_at < 30:
            return
        try:
            from storage import kb_store

            chunks = kb_store.all_indexed_chunks()
            self.refresh_dynamic_corpus(
                [(c["doc_id"], c["location"], c["text"]) for c in chunks]
            )
        except Exception as exc:
            logger.warning("KB 动态语料懒加载失败（沿用内存语料）: %s", exc)
            import time

            self._corpus_loaded_at = time.time()  # 失败也计时，避免每查询打库

    def _all_text_sources(self) -> list[tuple[str, int, str]]:
        """文本级检索的合并语料：静态 KB 段落 + 动态 chunks（doc_id 直接作键）。"""
        return self._paragraph_sources + [
            (doc_id, -1, text) for doc_id, _loc, text in self._dynamic_sources
        ]

    def _dynamic_locations(self) -> dict[str, str]:
        """动态 chunk 的 (doc_id, text) → location 映射（溯源引用用）。"""
        return {text: loc for doc_id, loc, text in self._dynamic_sources}

    # ------------------------------------------------------------------
    # 单条查询（三级降级；kb_ids = PHASE-006 会话选库范围，空列表 = 全部库，None = 不过滤）
    # ------------------------------------------------------------------
    def query(self, query: str, kb_ids: Optional[list[str]] = None) -> RetrievalResult:
        degraded: list[str] = []
        scoped = kb_ids is not None
        doc_scope = self._scope_doc_ids(kb_ids) if scoped else None
        if scoped and self.sdk is not None:
            # D6-5：SDK 是单一全局索引，无法按库范围过滤——定向检索如实跳过
            degraded.append("sdk_kb_scope_unsupported")

        if self.sdk is not None and not scoped:
            # 第一级 local：图谱检索（无库范围时维持原行为）
            result = self._try_sdk("query_local_sync", query)
            if result is not None:
                return RetrievalResult(result, "local", degraded)
            degraded.append("local")

            # 第二级 basic：语义向量
            result = self._try_sdk("query_basic_sync", query)
            if result is not None:
                return RetrievalResult(result, "basic", degraded)
            degraded.append("basic")

        if self.enable_kb_levels:
            self._ensure_corpus()  # 动态语料懒加载（跨进程一致性）
            # 第三级 graph：自建 Neo4j 实体层（T3.2；SDK 未装时的真实图谱级）
            hit = self._try_neo4j(query, doc_scope=doc_scope, kb_ids=kb_ids if scoped else None)
            if hit is not None:
                content, refs = hit
                if self.sdk is None and "graphrag_sdk_unavailable" not in degraded:
                    degraded.append("graphrag_sdk_unavailable")
                return RetrievalResult(content, "graph", degraded, refs)
            degraded.append("graph")

            # 第四级 vector：Milvus chunk 向量检索（T3.2）
            hit = self._try_milvus(query, doc_scope=doc_scope)
            if hit is not None:
                content, refs = hit
                return RetrievalResult(content, "vector", degraded, refs)
            degraded.append("vector")

        # 兜底 text：关键词（语料按 doc 集合过滤；全空 = 范围内零语料，如实返回不可用）
        if scoped:
            filtered = [s for s in self._dynamic_sources if s[0] in set(doc_scope or [])]
            if not filtered:
                return RetrievalResult(
                    "选定知识库范围内无可检索知识（未归库不参与检索）", "unavailable", degraded
                )
            content, refs = self._search_text_scoped(query, doc_scope or [])
            if self.sdk is None and not degraded:
                degraded.append("graphrag_sdk_unavailable")  # 如实交代为何直接落在文本级
            return RetrievalResult(content, "text", degraded, _refs_with_kb(refs))

        if self.kb_text or self._dynamic_sources:
            if self.sdk is None and not degraded:
                degraded.append("graphrag_sdk_unavailable")  # 如实交代为何直接落在文本级
            content, refs = self._search_text(query)
            return RetrievalResult(content, "text", degraded, _refs_with_kb(refs))

        return RetrievalResult(
            "知识库不可用：GraphRAG SDK 未初始化且知识库文本未加载", "unavailable", degraded
        )

    # ------------------------------------------------------------------
    # T3.2 KB 扩展级（graph / vector），带 TTL 可用性探测
    # ------------------------------------------------------------------
    def _probe_ok(self, name: str, probe) -> bool:
        import time

        cached = self._kb_probe_cache.get(name)
        now = time.time()
        if cached and now - cached[1] < 30:
            return cached[0]
        try:
            ok = bool(probe())
        except Exception:
            ok = False
        self._kb_probe_cache[name] = (ok, now)
        return ok

    def _try_neo4j(self, query: str, doc_scope: Optional[list[str]] = None,
                   kb_ids: Optional[list[str]] = None) -> Optional[tuple[str, list[dict[str, str]]]]:
        from storage.neo4j_store import Neo4jStore

        if not self._probe_ok("neo4j", lambda: Neo4jStore().available()):
            return None
        store = Neo4jStore()
        # scoped 时传 doc/kb 集合过滤；未 scoped 传 None（全量，行为不变）
        rows = store.find_entities_matching(
            query, limit=5,
            doc_ids=doc_scope if doc_scope is not None else None,
            kb_ids=kb_ids,
        )
        if not rows:
            return None
        content = "知识图谱检索结果（Neo4j 实体层）：\n\n" + "\n\n".join(
            f"[{r.get('label', '?')}] {r.get('name', '?')}"
            + (f"：{r['description']}" if r.get("description") else "")
            for r in rows
        )
        refs = [
            {"doc_id": r.get("source_doc") or "kb-graph",
             "location": f"{r.get('label', '?')}:{r.get('name', '?')}"}
            for r in rows
        ]
        return content, _refs_with_kb(refs)

    def _try_milvus(self, query: str, doc_scope: Optional[list[str]] = None) -> Optional[tuple[str, list[dict[str, str]]]]:
        from kb.embeddings import embed_texts
        from kb.vector_store import ChunkVectorStore

        vs = ChunkVectorStore()
        if not self._probe_ok("milvus", vs.available):
            return None
        vectors = embed_texts([query])
        if not vectors:
            return None
        hits = vs.search(vectors[0], top_k=5, doc_ids=doc_scope if doc_scope is not None else None)
        if not hits:
            return None
        content = "知识库向量检索结果（Milvus chunks）：\n\n" + "\n\n---\n\n".join(
            f"[{h.get('doc_id', '?')}#{h.get('location', '?')}]\n{h.get('text', '')}"
            for h in hits
        )
        refs = [
            {"doc_id": h.get("doc_id") or "?", "location": h.get("location") or "?"}
            for h in hits
        ]
        return content, _refs_with_kb(refs)

    # ------------------------------------------------------------------
    # 批量查询（并发 + 逐条降级，A1 的 4~8 条子查询用）
    # ------------------------------------------------------------------
    def query_batch(self, queries: list[str], kb_ids: Optional[list[str]] = None) -> list[RetrievalResult]:
        scoped = kb_ids is not None
        if self.sdk is not None and not scoped:
            try:
                results = self.sdk.query_batch_sync_with_fallback(queries)
                out: list[RetrievalResult] = []
                for q, r in zip(queries, results):
                    if r.get("success"):
                        out.append(RetrievalResult(r.get("response", ""), "local"))
                    elif self.kb_text:
                        content, refs = self._search_text(q)
                        out.append(RetrievalResult(content, "text", ["local", "basic"], refs))
                    else:
                        out.append(RetrievalResult(f"查询失败: {r.get('error', '未知错误')}", "unavailable"))
                return out
            except Exception as exc:
                logger.warning("批量查询异常，降级到逐条文本搜索: %s", exc)

        # 逐条 query（scoped 时走库范围过滤路径；未 scoped 且无 SDK 时行为不变）
        out = []
        for q in queries:
            out.append(self.query(q, kb_ids=kb_ids) if scoped else self.query(q))
        return out

    # ------------------------------------------------------------------
    # PHASE-006 D6-3：Agent 库路由（会话不选库时自主圈定相关知识库，依据留痕 IR1.kb_selection）
    # ------------------------------------------------------------------
    def route_bases(self, queries: list[str]) -> dict[str, Any]:
        """逐库轻量探测相关度：首条子查询在每库范围内检索，命中数 > 0 的库入选。

        返回 kb_selection 契约结构：
          {mode: "auto", bases: [{kb_id, kb_name, hits, selected}]}
        全部零命中 → 兜底"全部库"（各库 selected=true + 兜底标注，A-16 如实）。
        """
        from storage import kb_store

        bases = kb_store.list_bases()
        probe = queries[0] if queries else ""
        rows: list[dict[str, Any]] = []
        for b in bases:
            hits = 0
            if probe:
                try:
                    r = self.query(probe, kb_ids=[str(b["id"])])
                    hits = len(r.refs) if r.ok else 0
                except Exception as exc:
                    logger.warning("库路由探测失败 base=%s: %s", b["id"], exc)
            rows.append({"kb_id": str(b["id"]), "kb_name": b["name"], "hits": hits})
        if not any(r["hits"] > 0 for r in rows):
            for r in rows:
                r["selected"] = True
            return {"mode": "auto", "bases": rows, "fallback_all": True}
        for r in rows:
            r["selected"] = r["hits"] > 0
        return {"mode": "auto", "bases": rows}

    # ------------------------------------------------------------------
    # 索引构建（T3.2 全管线接管前的手工入口）
    # ------------------------------------------------------------------
    def build_index(self, verbose: bool = True) -> dict:
        if self.sdk is None:
            return {"success": False, "message": "GraphRAG SDK 未初始化"}
        return self.sdk.build_index_sync(verbose=verbose, is_update=False)

    # ------------------------------------------------------------------
    # 知识沉淀去重（T2.3）：候选名与既有语料的归一化匹配
    # ------------------------------------------------------------------
    def find_name_matches(self, name: str, limit: int = 3) -> list[dict[str, str]]:
        """在 KB 语料中查找与候选名匹配的段落（去重的文本级依据）。

        返回 [{doc_id, location, excerpt}]；空列表表示知识库中无同名知识。
        """
        normalized = _normalize_name(name)
        if not normalized:
            return []
        hits: list[dict[str, str]] = []
        for doc_id, para_idx, para in self._paragraph_sources:
            if normalized in _normalize_name(para):
                hits.append({
                    "doc_id": doc_id,
                    "location": f"段落{para_idx + 1}",
                    "excerpt": para[:120],
                })
                if len(hits) >= limit:
                    break
        return hits

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

    def _search_text(self, query: str) -> tuple[str, list[dict[str, str]]]:
        """第三级：按段落关键词匹配（迁移自旧 service._search_text）。

        语料 = 静态 KB 段落 + KB 管线动态 chunks（T3.2）。
        返回 (内容, 来源引用 [{doc_id, location}])——静态源 location 为段落序号，
        动态源 location 为解析页码/章节（T3 分块管线上线后已升级）。
        """
        all_sources = self._all_text_sources()
        if not all_sources:
            return "知识库文本不可用", []

        # 中英文混合：按空格 + 简单 2-gram 提关键词
        keywords = [kw.lower() for kw in query.split() if len(kw) > 1]
        if any("\u4e00" <= ch <= "\u9fff" for ch in query):
            keywords += [query[i : i + 2] for i in range(len(query) - 1)]

        scored: list[tuple[int, int, str, int, str]] = []  # (score, seq, doc_id, para_idx, para)
        dynamic_locs = self._dynamic_locations()
        for seq, (doc_id, para_idx, para) in enumerate(all_sources):
            para_lower = para.lower()
            score = sum(1 for kw in keywords if kw in para_lower)
            if score > 0:
                scored.append((score, seq, doc_id, para_idx, para))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        if not scored:
            overview = self.kb_text or "\n\n".join(p for _, _, _, _, p in all_sources[:20])
            return f"未找到精确匹配。知识库概览：\n\n{overview[:1500]}...", []

        top = scored[:5]
        content = (
            "知识库搜索结果（文本降级模式）：\n\n"
            + "\n\n---\n\n".join(para for _, _, _, _, para in top)
        )
        refs = []
        for _, _, doc_id, idx, para in top:
            location = dynamic_locs.get(para, f"段落{idx + 1}")
            refs.append({"doc_id": doc_id, "location": location})
        return content, refs

    def _search_text_scoped(self, query: str, doc_ids: list[str]) -> tuple[str, list[dict[str, str]]]:
        """文本级库范围版（PHASE-006 D6-2）：语料限定 doc_ids，检索逻辑同 _search_text。

        动态语料即 KB 管线 chunks（doc_id 为键），静态 kb_text 段落无 doc 归属、不参与范围检索。
        """
        allowed = set(doc_ids)
        sources = [(doc_id, -1, text) for doc_id, _loc, text in self._dynamic_sources
                   if doc_id in allowed]
        if not sources:
            return "选定知识库范围内无可检索知识", []
        keywords = [kw.lower() for kw in query.split() if len(kw) > 1]
        if any("\u4e00" <= ch <= "\u9fff" for ch in query):
            keywords += [query[i : i + 2] for i in range(len(query) - 1)]

        scored: list[tuple[int, int, str, str]] = []  # (score, seq, doc_id, para)
        dynamic_locs = self._dynamic_locations()
        for seq, (doc_id, _para_idx, para) in enumerate(sources):
            para_lower = para.lower()
            score = sum(1 for kw in keywords if kw in para_lower)
            if score > 0:
                scored.append((score, seq, doc_id, para))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        if not scored:
            overview = "\n\n".join(p for _, _, p in sources[:20])
            return f"选定知识库内未找到精确匹配。范围内知识概览：\n\n{overview[:1500]}...", []
        top = scored[:5]
        content = (
            "知识库搜索结果（文本降级模式，限定选定知识库范围）：\n\n"
            + "\n\n---\n\n".join(para for _, _, _, para in top)
        )
        refs = [
            {"doc_id": doc_id, "location": dynamic_locs.get(para, "")}
            for _, _, doc_id, para in top
        ]
        return content, refs


_service: Optional[GraphRAGService] = None


def _normalize_name(s: Any) -> str:
    """去重用归一化：去空白 + 小写（中英文通用）。"""
    return "".join(str(s or "").split()).lower()


def get_knowledge_service() -> GraphRAGService:
    """进程内单例（索引数据加载昂贵）。KB 扩展级（graph/vector）经配置启用（T3.2）。"""
    global _service
    if _service is None:
        import app_config

        _service = GraphRAGService(
            enable_kb_levels=app_config.KB_RETRIEVAL_LEVELS.lower() in ("1", "true", "yes"),
        )
    return _service


def _refs_with_kb(refs: list[dict[str, str]]) -> list[dict[str, str]]:
    """PHASE-006 D6-7：refs 补 kb_id（doc 首个归属库；悬空/未归库留空串，前端容错）。

    打库失败不阻塞检索（refs 原样返回，kb_id 缺失 = 前端显示"—"）。
    """
    if not refs:
        return refs
    try:
        from storage import kb_store

        doc_ids = list({r["doc_id"] for r in refs if r.get("doc_id")})
        tags: dict[str, str] = {}
        for doc_id in doc_ids:
            bases = kb_store.doc_base_ids(doc_id)
            if bases:
                tags[doc_id] = str(bases[0])  # psycopg uuid 列返回 UUID 对象，契约要 str
        return [dict(r, kb_id=tags.get(r.get("doc_id", ""))) for r in refs]
    except Exception as exc:
        logger.warning("refs 补 kb_id 失败（原样返回）: %s", exc)
        return refs
