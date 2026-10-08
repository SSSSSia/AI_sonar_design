# -*- coding: utf-8 -*-
"""T6.2 检索三层按 doc 集合过滤 + 未归库排除（PHASE-006 D6-2/D6-5/D6-9）+ D6-3 库路由。

fakes 沿用 test_t16 模式；kb_scope 打桩，不触真库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge.service import GraphRAGService, RetrievalResult
from storage import kb_scope


# ----------------------------------------------------------------------
# 夹具：假语料（动态 chunks 三个 doc）+ kb_scope 打桩
# ----------------------------------------------------------------------
CHUNKS = [
    ("doc-a1", "第1页", "拖曳线列阵声呐的系统组成包括水听器基阵与预处理机。"),
    ("doc-a2", "第1页", "声呐方程与优质因子的计算方法。"),
    ("doc-b1", "第1页", "舷侧阵基阵的波束成形要点。"),
]


@pytest.fixture(autouse=True)
def _scope_cache():
    kb_scope.invalidate()
    yield
    kb_scope.invalidate()


@pytest.fixture
def svc(monkeypatch):
    """无 SDK、无 KB 扩展级的服务 + 动态语料打桩。

    T12.7 后 graphrag 包真实安装——fixture 显式置空 SDK，保持「SDK 不可用降级」
    语义的确定性（否则随环境漂移）。SDK 在位的分级行为由 test_t127 覆盖。"""
    s = GraphRAGService()
    s.sdk = None  # T12.7 后包真实在装——显式置空保持「SDK 不可用」语义确定性
    s._dynamic_sources = list(CHUNKS)
    monkeypatch.setattr(GraphRAGService, "_ensure_corpus", lambda self: None)
    return s


def patch_scope(monkeypatch, mapping, all_assigned):
    """mapping: kb_id -> doc 列表；all_assigned: "全部库"语义的并集。"""

    def fake_resolve(kb_ids):
        if not kb_ids:
            return sorted(set(all_assigned))
        out: set[str] = set()
        for k in kb_ids or []:
            out.update(mapping.get(k, []))
        return sorted(out)

    monkeypatch.setattr(kb_scope, "resolve_doc_ids", fake_resolve)


# ----------------------------------------------------------------------
# query(kb_ids=...) 三层行为（本服务未启用扩展级 → 走文本级）
# ----------------------------------------------------------------------
def test_scoped_text_only_hits_scope_docs(svc, monkeypatch):
    patch_scope(monkeypatch, {"kb-a": ["doc-a1", "doc-a2"], "kb-b": ["doc-b1"]},
                all_assigned=["doc-a1", "doc-a2", "doc-b1"])
    r = svc.query("波束成形", kb_ids=["kb-a"])
    assert r.level == "text"
    assert all(ref["doc_id"] in ("doc-a1", "doc-a2") for ref in r.refs)
    assert "doc-b1" not in r.content


def test_scoped_shared_doc_visible_from_both(svc, monkeypatch):
    patch_scope(monkeypatch, {"kb-a": ["doc-a1", "doc-x"], "kb-b": ["doc-b1", "doc-x"]},
                all_assigned=["doc-a1", "doc-b1", "doc-x"])
    monkeypatch.setattr(svc, "_dynamic_sources",
                        CHUNKS + [("doc-x", "第1页", "多库共享的通用设计知识段落。")])
    for kb in ("kb-a", "kb-b"):
        r = svc.query("通用设计知识", kb_ids=[kb])
        assert r.ok
        assert any(ref["doc_id"] == "doc-x" for ref in r.refs)


def test_scoped_empty_scope_unavailable(svc, monkeypatch):
    patch_scope(monkeypatch, {"kb-empty": []}, all_assigned=[])
    r = svc.query("任意", kb_ids=["kb-empty"])
    assert r.level == "unavailable"
    assert "未归库" in r.content or "无可检索" in r.content


def test_scoped_marks_sdk_skip(svc, monkeypatch):
    patch_scope(monkeypatch, {"kb-a": ["doc-a1"]}, all_assigned=["doc-a1"])

    class FakeSdk:
        def query_local_sync(self, q):
            return {"success": True, "response": "sdk hit"}

    svc.sdk = FakeSdk()
    r = svc.query("拖曳", kb_ids=["kb-a"])
    # D6-5：SDK 级被跳过（无法按库过滤），落到范围内文本级
    assert r.level == "text"
    assert "sdk_kb_scope_unsupported" in r.degraded_from


def test_unscoped_behavior_unchanged(svc, monkeypatch):
    """kb_ids 缺省（None）时行为与现状一致：全语料文本级、无 sdk 标注。"""
    r = svc.query("波束成形")
    assert r.level == "text"
    assert any(ref["doc_id"] == "doc-b1" for ref in r.refs)
    assert r.degraded_from == ["graphrag_sdk_unavailable"]


# ----------------------------------------------------------------------
# route_bases（D6-3）：Agent 库路由与兜底
# ----------------------------------------------------------------------
def test_route_bases_selects_hit_bases(svc, monkeypatch):
    patch_scope(monkeypatch, {"kb-a": ["doc-a1"], "kb-b": ["doc-b1"]}, all_assigned=[])

    class FakeBasesStore:
        @staticmethod
        def list_bases():
            return [{"id": "kb-a", "name": "A"}, {"id": "kb-b", "name": "B"}]

    import storage.kb_store as real_kb_store
    monkeypatch.setattr(real_kb_store, "list_bases", FakeBasesStore.list_bases)

    def fake_query(q, kb_ids=None):
        if kb_ids == ["kb-a"]:
            return RetrievalResult("x", "text", [], [{"doc_id": "doc-a1", "location": "1"}])
        return RetrievalResult("选定知识库范围内无可检索知识", "unavailable", [])

    monkeypatch.setattr(svc, "query", fake_query)
    sel = svc.route_bases(["拖曳线列阵", "波束"])
    assert sel["mode"] == "auto"
    by_id = {b["kb_id"]: b for b in sel["bases"]}
    # T14.8 逐条查询探测：两条查询都在 kb-a 命中 → hits 合并计数（此前只测首条=1）
    assert by_id["kb-a"]["selected"] is True and by_id["kb-a"]["hits"] == 2
    assert by_id["kb-b"]["selected"] is False


def test_route_bases_fallback_all_when_zero_hits(svc, monkeypatch):
    patch_scope(monkeypatch, {"kb-a": [], "kb-b": []}, all_assigned=[])

    import storage.kb_store as real_kb_store
    monkeypatch.setattr(real_kb_store, "list_bases",
                        lambda: [{"id": "kb-a", "name": "A"}, {"id": "kb-b", "name": "B"}])
    monkeypatch.setattr(svc, "query", lambda q, kb_ids=None: RetrievalResult("x", "unavailable", []))
    sel = svc.route_bases(["无命中查询"])
    assert sel["fallback_all"] is True
    assert all(b["selected"] for b in sel["bases"])


# ----------------------------------------------------------------------
# query_batch(kb_ids=...) 逐条走 scoped 路径
# ----------------------------------------------------------------------
def test_query_batch_scoped(svc, monkeypatch):
    patch_scope(monkeypatch, {"kb-a": ["doc-a1"]}, all_assigned=["doc-a1"])
    results = svc.query_batch(["拖曳线列阵", "波束成形"], kb_ids=["kb-a"])
    assert len(results) == 2
    for r in results:
        assert all(ref["doc_id"] == "doc-a1" for ref in r.refs)


# ----------------------------------------------------------------------
# Milvus search doc 集合过滤表达式（fake client）
# ----------------------------------------------------------------------
class FakeSearchClient:
    def __init__(self):
        self.filters = []

    def has_collection(self, name):
        return True

    def search(self, collection, data, limit, filter=None, output_fields=None):
        self.filters.append(filter)
        return [[]]


def test_milvus_search_doc_filter_expr(monkeypatch):
    from kb.vector_store import ChunkVectorStore

    fake = FakeSearchClient()
    vs = ChunkVectorStore(client=fake)
    vs.search([0.0] * 4, top_k=5, doc_ids=["d1", "d2"])
    assert fake.filters[-1] == 'doc_id in ["d1", "d2"]'
    vs.search([0.0] * 4, top_k=5)
    assert fake.filters[-1] is None  # 不过滤路径


# ----------------------------------------------------------------------
# Neo4j find_entities_matching scope 参数（fake driver）
# ----------------------------------------------------------------------
class FakeSession:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        self.store.cypher = cypher
        self.store.params = params
        return self

    def data(self):
        return []


class FakeDriverStore:
    def __init__(self):
        self.cypher = ""
        self.params: dict = {}

    def _get_driver(self):
        class D:
            def __init__(self, outer):
                self.outer = outer

            def session(self):
                return FakeSession(self.outer)

        return D(self)


def test_neo4j_scope_filter_building():
    from storage.neo4j_store import Neo4jStore

    store = FakeDriverStore()
    Neo4jStore.find_entities_matching(store, "拖曳", limit=5,
                                      doc_ids=["d1"], kb_ids=["kb-9"])
    assert "e.source_doc IN $scope_docs" in store.cypher
    assert "e.kb_id IN $scope_kbs" in store.cypher
    assert store.params["scope_docs"] == ["d1"]
    assert store.params["scope_kbs"] == ["kb-9"]

    store2 = FakeDriverStore()
    Neo4jStore.find_entities_matching(store2, "拖曳", limit=5)
    assert "scope_docs" not in store2.cypher  # 未 scoped 行为不变
