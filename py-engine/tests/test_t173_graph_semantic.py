# -*- coding: utf-8 -*-
"""T17.3 图路向量升级单测（PHASE-017；契约=ADR-016 §3）。

覆盖：_embedding_text 组装 / ensure_vector_index Cypher / find_entities_semantic（向量查询
+scope+deprecated 过滤+邻域 context_block）/ 索引不可用→Neo4jStoreError / embed_entity_keys
best-effort（embed 失败不抛）/ backfill 诚实计数 / 写路径 embedding 钩子（write_candidates+
write_extractions_to_graph）/ service 层 3 元组语义流+CONTAINS 回退+三路融合分区呈现。

store 层用 fake driver 注入（Neo4jStore(driver=fake)，不触真库）；service 层 monkeypatch
Neo4jStore 类与 kb.embeddings.embed_texts。CONTAINS 老查询回归由 test_t16/test_t62 既有桩覆盖。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from knowledge.service import GraphRAGService
from storage import neo4j_store
from storage.neo4j_store import Neo4jStore, Neo4jStoreError, _embedding_text


# ----------------------------------------------------------------------
# fake driver（routes(query, params, queries) -> list[dict]；queries 累积观测）
# ----------------------------------------------------------------------
class FakeResult:
    def __init__(self, data):
        self._data = data

    def data(self):
        return self._data

    def single(self):
        return {"c": 0}  # _deprecate_same_name 默认零计数


class FakeSession:
    def __init__(self, routes, queries):
        self._routes = routes
        self.queries = queries

    def run(self, query, **params):
        self.queries.append((query, params))
        return FakeResult(self._routes(query, params))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDriver:
    def __init__(self, routes):
        self._routes = routes
        self.queries: list[tuple[str, dict]] = []

    def session(self):
        return FakeSession(self._routes, self.queries)

    def verify_connectivity(self):
        pass

    def close(self):
        pass


def make_store(routes) -> Neo4jStore:
    return Neo4jStore(driver=FakeDriver(routes))


# ----------------------------------------------------------------------
# store 层
# ----------------------------------------------------------------------
class TestEmbeddingText:
    def test_name_description(self):
        assert _embedding_text({"name": "阵元间距", "description": "决定阵长"}) == "阵元间距：决定阵长"

    def test_expression_fallback(self):
        assert _embedding_text({"name": "c1", "expression": "f<100"}) == "c1：f<100"

    def test_params_json_fallback(self):
        assert _embedding_text({"name": "case1", "params_json": "[{\"a\":1}]"}) == 'case1：[{"a":1}]'

    def test_with_context(self):
        assert _embedding_text({"name": "n", "description": "d", "context": "ctx"}) == "n：d：ctx"

    def test_empty_material(self):
        assert _embedding_text({"name": "", "description": ""}) == ""
        assert _embedding_text({}) == ""


class TestEnsureVectorIndex:
    def test_cypher_shape(self):
        store = make_store(lambda q, p: [])
        assert store.ensure_vector_index() is True
        q = store._driver.queries[0][0]
        assert "CREATE VECTOR INDEX kg_entity_desc IF NOT EXISTS" in q
        assert "FOR (n:KnowledgeEntity) ON (n.description_embedding)" in q
        assert "`vector.dimensions`: 1024" in q and "'cosine'" in q

    def test_failure_returns_false(self):
        def routes(q, p):
            if "CREATE VECTOR INDEX" in q:
                raise RuntimeError("version too old")
            return []

        assert make_store(routes).ensure_vector_index() is False


class TestFindEntitiesSemantic:
    SEMANTIC_ROWS = [{
        "key": "k1", "label": "Component", "name": "换能器基阵", "description": "平面阵",
        "source_doc": "doc1", "kb_id": None, "score": 0.93,
    }]

    def _routes(self, queries):
        def routes(q, p):
            if "db.index.vector.queryNodes" in q:
                queries.append(("vector", q, p))
                return self.SEMANTIC_ROWS
            if "MATCH (e {key: $key})" in q:
                return [{"key": "k1", "label": "Component", "name": "换能器基阵", "deprecated": False}]
            if "[r*1.." in q:
                return [
                    {"key": "k2", "label": "DesignParameter", "name": "阵元间距", "deprecated": False},
                    {"key": "k3", "label": "DesignParameter", "name": "已废弃参数", "deprecated": True},
                ]
            if "MATCH (a)-[r]->(b)" in q:
                return [
                    {"source": "k1", "target": "k2", "rel_type": "HAS_PARAM"},
                    {"source": "k1", "target": "k3", "rel_type": "HAS_PARAM"},  # deprecated 邻居
                ]
            return []

        return routes

    def test_flow_context_block_and_filters(self):
        queries: list = []
        store = make_store(self._routes(queries))
        rows = store.find_entities_semantic([0.1] * 4, limit=5, kb_ids=["kb-9"])
        assert len(rows) == 1 and rows[0]["score"] == 0.93
        assert rows[0]["context_block"].startswith("[Component] 换能器基阵：平面阵（关联：")
        assert "[DesignParameter] 阵元间距 —HAS_PARAM→ [Component] 换能器基阵" not in rows[0]["context_block"]
        # 邻居边方向：k1 —HAS_PARAM→ k2 → 部件—参数；deprecated 邻居（k3）不呈现
        assert "阵元间距" in rows[0]["context_block"]
        assert "已废弃参数" not in rows[0]["context_block"]
        vq = next(x for x in queries if x[0] == "vector")
        assert "coalesce(node.deprecated, false) = false" in vq[1]
        assert "node.kb_id IN $scope_kbs" in vq[1]
        assert vq[2]["scope_kbs"] == ["kb-9"]
        assert vq[2]["k"] == 20  # limit*4 先出后滤余量

    def test_index_unavailable_raises(self):
        def routes(q, p):
            if "CREATE VECTOR INDEX" in q:
                raise RuntimeError("no vector index support")
            return []

        store = make_store(routes)
        with pytest.raises(Neo4jStoreError):
            store.find_entities_semantic([0.1] * 4)


class TestEmbedEntityKeys:
    def test_best_effort_write(self, monkeypatch):
        def routes(q, p):
            if "n.key IN $keys" in q:
                return [{"key": "k1", "label": "Component", "props": {"name": "n1", "description": "d1"}}]
            return []

        store = make_store(routes)
        monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: [[0.1] * 4 for _ in texts])
        assert store.embed_entity_keys(["k1"]) == 1
        unwind = [x for x in store._driver.queries if "UNWIND" in x[0]]
        assert unwind and unwind[0][1]["rows"][0]["vec"] == [0.1] * 4
        assert unwind[0][1]["rows"][0]["label"] == "Component"
        assert "SET n:KnowledgeEntity" in unwind[0][0]
        assert "labels(n)[0] = row.label" in unwind[0][0]  # 逐 (key,label) 定位防孪生连坐

    def test_same_key_cross_label_twins_embedded_independently(self, monkeypatch):
        # key=sha1(名) 设计使然：同名跨标签节点各自用本节点材料（首版 UNWIND 按 key 无标签
        # 匹配把孪生连坐嵌入首见材料——实测发现即修）
        def routes(q, p):
            if "n.key IN $keys" in q:
                return [
                    {"key": "k1", "label": "Component", "props": {"name": "X", "description": "部件材料"}},
                    {"key": "k1", "label": "DesignParameter", "props": {"name": "X", "description": "参数材料"}},
                ]
            return []

        store = make_store(routes)
        monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: [[0.1] * 4, [0.2] * 4])
        assert store.embed_entity_keys(["k1"]) == 2
        rows = [x for x in store._driver.queries if "UNWIND" in x[0]][0][1]["rows"]
        assert {r["label"] for r in rows} == {"Component", "DesignParameter"}
        assert rows[0]["vec"] != rows[1]["vec"]  # 各用本节点材料

    def test_embed_unavailable_returns_zero_no_raise(self, monkeypatch):
        def routes(q, p):
            if "n.key IN $keys" in q:
                return [{"key": "k1", "label": "Component", "props": {"name": "n1", "description": "d1"}}]
            return []

        store = make_store(routes)
        monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: None)
        assert store.embed_entity_keys(["k1"]) == 0
        monkeypatch.setattr("kb.embeddings.embed_texts",
                            lambda texts: (_ for _ in ()).throw(RuntimeError("down")))
        assert store.embed_entity_keys(["k1"]) == 0  # 不抛（A-16 加法元数据不阻塞）

    def test_empty_material_skipped(self, monkeypatch):
        store = make_store(lambda q, p: [{"key": "k1", "label": "Component",
                                          "props": {"name": "", "description": ""}}]
                           if "n.key IN $keys" in q else [])
        monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: [[0.1] * 4])
        assert store.embed_entity_keys(["k1"]) == 0  # 材料为空不嵌（A-16 不造空向量）


class TestBackfill:
    def test_counts_honest(self, monkeypatch):
        def routes(q, p):
            if "ORDER BY key" in q:
                return [
                    {"key": "k1", "label": "Component", "props": {"name": "n1", "description": "d1"}},
                    {"key": "k2", "label": "Component", "props": {"name": "n2", "description": "d2"}},
                    {"key": "k3", "label": "Component", "props": {"name": "", "description": ""}},  # 空材料
                ]
            return []

        store = make_store(routes)
        monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: [[0.1] * 4 for _ in texts])
        report = store.backfill_entity_embeddings()
        assert report == {"embedded": 2, "skipped_empty": 1, "failed": 0}
        assert any("SET n:KnowledgeEntity" in q and "n.key IS NOT NULL" in q
                   for q, _ in store._driver.queries)

    def test_failed_batch_counted(self, monkeypatch):
        def routes(q, p):
            if "ORDER BY key" in q:
                return [{"key": "k1", "label": "Component", "props": {"name": "n1", "description": "d1"}}]
            return []

        store = make_store(routes)
        monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: None)
        assert store.backfill_entity_embeddings() == {"embedded": 0, "skipped_empty": 0, "failed": 1}


class TestWritePathHooks:
    def test_write_candidates_embedding_hook(self, monkeypatch):
        def routes(q, p):
            if "n.key IN $keys" in q:
                return [
                    {"key": "k", "label": "DesignConstraint", "props": {"name": "c1", "expression": "e<10"}},
                    {"key": "s", "label": "Component", "props": {"name": "A"}},
                    {"key": "d", "label": "Component", "props": {"name": "B"}},
                ]
            return []

        store = make_store(routes)
        monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: [[0.1] * 4 for _ in texts])
        cands = [
            {"type": "constraint", "payload": {"name": "c1", "expression": "e<10"}},
            {"type": "relation", "payload": {"from": {"name": "A", "type": "Component"},
                                             "to": {"name": "B", "type": "Component"},
                                             "relation": "connects"}},
        ]
        stats = store.write_candidates(cands, "sess-1", kb_id="kb-9")
        assert stats["written"] == 2
        assert stats["embeddings"] == 3  # constraint 1 + 关系两端 2
        assert any(d.get("embed_keys") for d in stats["details"])

    def test_write_candidates_embed_fail_still_writes(self, monkeypatch):
        store = make_store(lambda q, p: [])
        monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: None)
        stats = store.write_candidates(
            [{"type": "constraint", "payload": {"name": "c1", "expression": "e<10"}}], "sess-1")
        assert stats["written"] == 1 and stats["embeddings"] == 0  # best-effort 不阻塞（A-10）

    def test_write_extractions_to_graph_hook(self, monkeypatch):
        from kb.index_build import write_extractions_to_graph

        def routes(q, p):
            if "n.key IN $keys" in q:
                return [{"key": "k", "label": "Component", "props": {"name": "E1", "description": "d"}}]
            return []

        store = make_store(routes)
        monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: [[0.1] * 4])
        counts = write_extractions_to_graph(store, [
            {"kind": "entity", "name": "E1", "etype": "Component", "payload": {"description": "d"}},
        ], "doc1")
        assert counts == {"entities": 1, "relations": 0, "embeddings": 1}
        merge_q = next(q for q, _ in store._driver.queries if "MERGE (n:" in q)
        assert "n:KnowledgeEntity" in merge_q  # 文档实体 MERGE 挂公共标签


# ----------------------------------------------------------------------
# service 层（monkeypatch Neo4jStore 类 + embed_texts）
# ----------------------------------------------------------------------
class FakeGraphStore:
    def __init__(self, semantic_rows=None, keyword_rows=None):
        self.semantic_rows = semantic_rows or []
        self.keyword_rows = keyword_rows or []
        self.calls: list[tuple] = []

    def available(self):
        return True

    def find_entities_semantic(self, vec, limit=5, doc_ids=None, kb_ids=None):
        self.calls.append(("semantic", doc_ids, kb_ids))
        return self.semantic_rows

    def find_entities_matching(self, query, limit=5, doc_ids=None, kb_ids=None):
        self.calls.append(("keyword", query, doc_ids, kb_ids))
        return self.keyword_rows


SEM_ROW = {
    "key": "k1", "label": "Component", "name": "换能器基阵", "description": "平面阵",
    "source_doc": "doc1", "kb_id": None, "score": 0.91,
    "context_block": "[Component] 换能器基阵：平面阵（关联：[DesignParameter] 阵元间距 —HAS_PARAM→ "
                     "[DesignParameter] 阵元间距）",
}
KW_ROW = {"label": "Component", "name": "换能器基阵", "description": "平面阵",
          "source_doc": "doc1", "kb_id": None, "key": "k1"}


def make_svc_min(monkeypatch, store: FakeGraphStore, embed_out):
    """最小构造：不走完整初始化（免 graphrag 重 import），只测 _try_neo4j 流。"""
    svc = GraphRAGService.__new__(GraphRAGService)
    svc._kb_probe_cache = {}
    monkeypatch.setattr(neo4j_store, "Neo4jStore", lambda *a, **kw: store)
    monkeypatch.setattr("kb.embeddings.embed_texts",
                        (lambda texts: embed_out) if not callable(embed_out) else embed_out)
    return svc


class TestServiceTryNeo4j:
    def test_semantic_3tuple(self, monkeypatch):
        svc = make_svc_min(monkeypatch, FakeGraphStore(semantic_rows=[SEM_ROW]), [[0.1] * 4])
        out = svc._try_neo4j("换能器基阵", doc_scope=None, kb_ids=None)
        assert out is not None
        content, refs, hits = out
        assert "语义+邻域" in content and SEM_ROW["context_block"] in content
        assert hits and hits[0]["kind"] == "graph" and hits[0]["score"] == 0.91
        assert hits[0]["location"] == "Component:换能器基阵"
        assert refs[0]["kind"] == "graph" and refs[0]["doc_id"] == "doc1"

    def test_contains_fallback_when_embed_unavailable(self, monkeypatch):
        store = FakeGraphStore(keyword_rows=[KW_ROW])
        svc = make_svc_min(monkeypatch, store, None)  # embed_texts → None
        content, refs, hits = svc._try_neo4j("声呐")
        assert "Neo4j 实体层）" in content and "语义+邻域" not in content  # 既有格式
        assert hits[0]["score"] is None  # 回退路不造分数（A-16）
        assert ("keyword", "声呐", None, None) in store.calls

    def test_contains_fallback_when_semantic_empty(self, monkeypatch):
        store = FakeGraphStore(semantic_rows=[], keyword_rows=[KW_ROW])
        svc = make_svc_min(monkeypatch, store, [[0.1] * 4])
        _, _, hits = svc._try_neo4j("生僻词")
        assert hits[0]["score"] is None
        assert ("keyword", "生僻词", None, None) in store.calls  # 语义零命中→回退补召回


# ----------------------------------------------------------------------
# 融合分区呈现（level 规则不变；chunk 侧不受实体影响=防饥饿锚）
# ----------------------------------------------------------------------
V_HITS = [{"doc_id": "d1", "location": "p1", "text": "向量命中一", "score": 0.9}]
B_HITS = [{"doc_id": "d2", "location": "p2", "text": "稀疏命中二", "score": 8.0}]
V_TUP = ("知识库向量检索结果（Milvus chunks）：\n\n[d1#p1]\n向量命中一", [{"doc_id": "d1", "location": "p1"}], V_HITS)
B_TUP = ("知识库稀疏检索结果（BM25）：\n\n[d2#p2]\n稀疏命中二", [{"doc_id": "d2", "location": "p2"}], B_HITS)
GH3 = (
    "知识图谱检索结果（Neo4j 实体层·语义+邻域）：\n\n[Component] 换能器基阵：平面阵",
    [{"doc_id": "kb-graph", "location": "Component:换能器基阵", "kind": "graph"}],
    [{"doc_id": "kb-graph", "location": "Component:换能器基阵",
      "text": "[Component] 换能器基阵：平面阵（关联：…）", "score": 0.9,
      "kind": "graph", "key": "k1", "label": "Component", "name": "换能器基阵"}],
)


def make_svc_full(tmp_path: Path, monkeypatch, bm25=None, vector=None, graph=None) -> GraphRAGService:
    svc = GraphRAGService(kb_dir=tmp_path / "kb")
    svc.sdk = None
    svc.enable_kb_levels = True
    monkeypatch.setattr(svc, "_try_bm25", lambda q, **kw: bm25)
    monkeypatch.setattr(svc, "_try_milvus", lambda q, **kw: vector)
    monkeypatch.setattr(svc, "_try_neo4j", lambda q, **kw: graph)
    return svc


class TestFusionPartition:
    def test_hybrid_graph_partition(self, tmp_path, monkeypatch):
        import threading
        import time as _t

        from storage import cache

        monkeypatch.setattr(cache, "_state",
                            {"client": None, "failed_at": _t.time(), "lock": threading.Lock()})
        svc = make_svc_full(tmp_path, monkeypatch, bm25=B_TUP, vector=V_TUP, graph=GH3)
        r = svc.query("换能器基阵@t173-hybridgraph")
        assert r.level == "hybrid+graph"
        # 分区：chunk 正文在前（bm25+vector 融合，实体不挤占），图上下文在后
        assert "稀疏命中二" in r.content and "向量命中一" in r.content
        assert r.content.index("向量命中一") < r.content.index("知识图谱上下文")
        assert "[Component] 换能器基阵" in r.content
        # hits 全局 RRF 混排：实体项带 kind/graph + paths 含 graph
        kinds = [h.get("kind") for h in r.hits]
        assert "graph" in kinds
        ent = next(h for h in r.hits if h.get("kind") == "graph")
        assert "graph" in ent["paths"] and "ranks" in ent
        # refs 文档在前图谱在后（T16 契约）
        assert r.refs[0]["doc_id"] in ("d1", "d2")
        assert any(x.get("kind") == "graph" for x in r.refs)

    def test_vector_graph_appends_context_but_hits_unchanged(self, tmp_path, monkeypatch):
        import threading
        import time as _t

        from storage import cache

        monkeypatch.setattr(cache, "_state",
                            {"client": None, "failed_at": _t.time(), "lock": threading.Lock()})
        svc = make_svc_full(tmp_path, monkeypatch, bm25=None, vector=V_TUP, graph=GH3)
        r = svc.query("换能器基阵@t173-vectorgraph")
        assert r.level == "vector+graph"
        assert r.content.startswith(V_TUP[0])  # 文档正文在前（字节前缀不变）
        assert "知识图谱上下文" in r.content  # 实体项追加在后
        assert r.hits == V_HITS[:10]  # 单路 hits 语义不变（实体仅经 refs/上下文呈现）

    def test_graph_only_semantic_content(self, tmp_path, monkeypatch):
        import threading
        import time as _t

        from storage import cache

        monkeypatch.setattr(cache, "_state",
                            {"client": None, "failed_at": _t.time(), "lock": threading.Lock()})
        svc = make_svc_full(tmp_path, monkeypatch, bm25=None, vector=None, graph=GH3)
        r = svc.query("换能器基阵@t173-graphonly")
        assert r.level == "graph"
        assert r.content == GH3[0]
        assert r.refs[0]["kind"] == "graph"

    def test_empty_entity_hits_legacy_bytes(self, tmp_path, monkeypatch):
        """实体项为空（T17.2 语义锚）→ 内容与 T17.2 逐字节一致。"""
        import threading
        import time as _t

        from storage import cache

        monkeypatch.setattr(cache, "_state",
                            {"client": None, "failed_at": _t.time(), "lock": threading.Lock()})
        GH_EMPTY = (GH3[0], GH3[1], [])
        svc = make_svc_full(tmp_path, monkeypatch, bm25=B_TUP, vector=V_TUP, graph=GH_EMPTY)
        r = svc.query("换能器基阵@t173-empty")
        assert r.level == "hybrid+graph"
        assert "知识图谱上下文" not in r.content  # 空实体项零追加
