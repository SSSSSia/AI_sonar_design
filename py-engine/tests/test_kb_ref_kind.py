# -*- coding: utf-8 -*-
"""知识检索引用来源标注单测（2026-09-24 修正：图谱实体引用丢 kb_id/kind 的问题）。

需要 Neo4j 在跑（数据底座容器）；有 A5 沉淀实体时走真实图谱路径，
无数据时退化为纯函数断言（_refs_with_kb 保留已有标注）。
"""

from __future__ import annotations

from knowledge.service import _refs_with_kb


def test_refs_with_kb_preserves_existing_tag():
    """图谱实体自带的 kb_id 不允许被 doc 归属查找覆盖为空（原缺陷）。"""
    refs = [{"doc_id": "kb-graph", "location": "DesignCase:拖曳线列阵", "kb_id": "kb-9"}]
    out = _refs_with_kb(refs)
    assert out[0]["kb_id"] == "kb-9"


def test_refs_with_kb_fills_missing_from_doc_ownership():
    """无标注的普通文档引用按文档归属库补齐；查不到如实留空。"""
    refs = [{"doc_id": "nonexistent-doc", "location": "p1"}]
    out = _refs_with_kb(refs)
    assert out[0]["kb_id"] == ""


def test_try_neo4j_refs_shape():
    """图谱命中：kind=graph、实体行自带 kb_id 透传（Neo4j 有实体时）。"""
    import pytest

    from storage.neo4j_store import Neo4jStore

    store = Neo4jStore()
    if not store.available():
        pytest.skip("Neo4j 未运行")
    rows = store.find_entities_matching("声呐", limit=3)
    if not rows:
        pytest.skip("图谱无实体数据")

    from knowledge.service import GraphRAGService

    svc = GraphRAGService.__new__(GraphRAGService)  # 只测 _try_neo4j，不走完整初始化
    svc._kb_probe_cache = {}
    out = svc._try_neo4j("声呐")
    assert out is not None
    _, refs = out
    assert refs and refs[0]["kind"] == "graph"
    if rows[0].get("kb_id"):
        assert refs[0]["kb_id"] == str(rows[0]["kb_id"])
