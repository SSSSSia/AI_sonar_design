# -*- coding: utf-8 -*-
"""T17.2 RRF 融合层单测（PHASE-017；契约=ADR-016 §2）。

覆盖：_rrf_fuse 纯函数（重叠文档排序在先 / k=60 数值 / 重复 identity 防御）、level 命中
矩阵（hybrid / bm25 / hybrid+graph / bm25+graph 新增 + vector / vector+graph / graph / text
既有级不变）、paths/ranks 诚实透传、bm25_enabled=False 关闭态逐字节回归（回滚保证）、
kb_cache_key fusion 参数。全部桩 _try_neo4j/_try_milvus/_try_bm25（不触真库/真 Milvus/真语料）。

隔离双保险：①_bypass_l1_cache（test_t16 同款，60s 重连窗口内读写全旁路）②每用例唯一
query 串——本套件单进程可能超 60s（graphrag SDK 重 import），窗口后 _get_client 会重连
真 Redis，唯一串使任何残留写入不跨用例碰撞。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from knowledge.service import GraphRAGService, _rrf_fuse
from storage.cache import kb_cache_key


@pytest.fixture(autouse=True)
def _bypass_l1_cache(monkeypatch):
    """L1 缓存隔离（test_t16 同款）：矩阵用例共用融合键，真 Redis 跨测试污染会让
    后续用例直接吃到首例缓存结果（stub 不再执行）——query() 直落 _query_uncached。"""
    import threading
    import time as _t

    from storage import cache

    monkeypatch.setattr(cache, "_state",
                        {"client": None, "failed_at": _t.time(), "lock": threading.Lock()})


def make_svc(tmp_path: Path) -> GraphRAGService:
    svc = GraphRAGService(kb_dir=tmp_path / "kb")
    svc.sdk = None
    svc.enable_kb_levels = True
    return svc


GRAPH_HIT = (
    "实体：阵元间距 —决定→ 阵长",
    [{"doc_id": "kb-graph", "location": "Parameter:阵元间距", "kind": "graph"}],
)
V_HITS = [
    {"doc_id": "d1", "location": "p1", "text": "向量命中一", "score": 0.9},
    {"doc_id": "d2", "location": "p2", "text": "向量命中二", "score": 0.8},
    {"doc_id": "d3", "location": "p3", "text": "向量命中三", "score": 0.7},
]
B_HITS = [
    {"doc_id": "d2", "location": "p2", "text": "稀疏命中二", "score": 12.5},
    {"doc_id": "d4", "location": "p4", "text": "稀疏命中四", "score": 9.0},
    {"doc_id": "d1", "location": "p1", "text": "稀疏命中一", "score": 5.0},
]
V_TUP = ("知识库向量检索结果（Milvus chunks）：\n\n[d1#p1]\n向量命中一", [{"doc_id": "d1", "location": "p1"}], V_HITS)
B_TUP = ("知识库稀疏检索结果（BM25）：\n\n[d2#p2]\n稀疏命中二", [{"doc_id": "d2", "location": "p2"}], B_HITS)


def stub_paths(monkeypatch, svc, bm25=None, vector=None, graph=None):
    monkeypatch.setattr(svc, "_try_bm25", lambda q, **kw: bm25)
    monkeypatch.setattr(svc, "_try_milvus", lambda q, **kw: vector)
    monkeypatch.setattr(svc, "_try_neo4j", lambda q, **kw: graph)


class TestRrfFuse:
    def test_overlap_docs_rank_first(self):
        # d2（bm25#1+vector#2）与 d1（bm25#3+vector#1）双路命中 → 单路命中的 d3/d4 之前
        out = _rrf_fuse(B_HITS, V_HITS)
        ids = [e["doc_id"] for e in out]
        assert ids.index("d2") < ids.index("d4") and ids.index("d1") < ids.index("d3")
        assert set(ids) == {"d1", "d2", "d3", "d4"}

    def test_k60_score_value(self):
        out = _rrf_fuse([{"doc_id": "x", "location": "l", "text": "t"}], [])
        assert out[0]["score"] == pytest.approx(1.0 / 61)

    def test_paths_and_ranks_honest(self):
        out = _rrf_fuse(B_HITS, V_HITS)
        d2 = next(e for e in out if e["doc_id"] == "d2")
        assert d2["paths"] == ["bm25", "vector"]
        assert d2["ranks"] == {"bm25": 1, "vector": 2}
        d3 = next(e for e in out if e["doc_id"] == "d3")
        assert d3["paths"] == ["vector"] and d3["ranks"] == {"vector": 3}

    def test_duplicate_identity_first_rank_wins(self):
        dup = [B_HITS[0], dict(B_HITS[0])]  # 同 identity 两次
        out = _rrf_fuse(dup, [])
        assert len(out) == 1
        assert out[0]["score"] == pytest.approx(1.0 / 61)  # 只计首个名次，不重复加分

    def test_text_preserved_from_first_path(self):
        # 同 identity 两路 text 不同 → 取首见路（bm25 先尝试）；生产两路同 chunk 文本一致
        out = _rrf_fuse(B_HITS, V_HITS)
        d2 = next(e for e in out if e["doc_id"] == "d2")
        assert d2["text"] == "稀疏命中二"


class TestLevelMatrix:
    def test_hybrid_level_and_fused_content(self, tmp_path, monkeypatch):
        svc = make_svc(tmp_path)
        stub_paths(monkeypatch, svc, bm25=B_TUP, vector=V_TUP)
        r = svc.query("阵元间距@t172-hybrid")
        assert r.level == "hybrid"
        # 双路 chunk 同呈：bm25 独有 chunk（d4）与 vector 独有 chunk（d3）都在融合正文
        assert "稀疏命中四" in r.content and "向量命中三" in r.content
        assert r.hits[0]["paths"] == ["bm25", "vector"]
        assert r.degraded_from == ["graph"]  # graph 未命中如实记录；bm25/vector 命中不记

    def test_bm25_only_level(self, tmp_path, monkeypatch):
        svc = make_svc(tmp_path)
        stub_paths(monkeypatch, svc, bm25=B_TUP, vector=None)
        r = svc.query("阵元间距@t172-bm25only")
        assert r.level == "bm25"
        assert "稀疏命中二" in r.content
        assert r.hits[0]["score"] == 12.5  # 单路原始分（无融合不造 RRF 值，A-16）
        assert "paths" not in r.hits[0]  # 非融合层级不造路名（kb.py 投影 paths=None）
        assert set(r.degraded_from) == {"graph", "vector"}

    def test_bm25_plus_graph(self, tmp_path, monkeypatch):
        svc = make_svc(tmp_path)
        stub_paths(monkeypatch, svc, bm25=B_TUP, vector=None, graph=GRAPH_HIT)
        r = svc.query("阵元间距@t172-bm25graph")
        assert r.level == "bm25+graph"
        kinds = [(x.get("doc_id"), x.get("kind")) for x in r.refs]
        assert kinds[0] == ("d2", None)  # 文档引用在前
        assert ("kb-graph", "graph") in kinds  # 图谱实体引用在后

    def test_hybrid_plus_graph(self, tmp_path, monkeypatch):
        svc = make_svc(tmp_path)
        stub_paths(monkeypatch, svc, bm25=B_TUP, vector=V_TUP, graph=GRAPH_HIT)
        r = svc.query("阵元间距@t172-hybridgraph")
        assert r.level == "hybrid+graph"
        assert ("kb-graph", "graph") in [(x.get("doc_id"), x.get("kind")) for x in r.refs]

    def test_vector_only_unchanged(self, tmp_path, monkeypatch):
        """bm25 未命中 → 单路 vector 语义与融合前逐字节一致。"""
        svc = make_svc(tmp_path)
        stub_paths(monkeypatch, svc, bm25=None, vector=V_TUP)
        r = svc.query("阵元间距@t172-vectoronly")
        assert r.level == "vector"
        assert r.content == V_TUP[0]
        assert r.hits == V_HITS[:10]

    def test_vector_plus_graph_unchanged(self, tmp_path, monkeypatch):
        """bm25 未命中 + 图谱命中 → 既有 "vector+graph" 语义与融合前一致（T16 回归锚）。"""
        svc = make_svc(tmp_path)
        stub_paths(monkeypatch, svc, bm25=None, vector=V_TUP, graph=GRAPH_HIT)
        r = svc.query("阵元间距@t172-vectorgraph")
        assert r.level == "vector+graph"
        assert r.content == V_TUP[0]  # 文档正文为主
        kinds = [(x.get("doc_id"), x.get("kind")) for x in r.refs]
        assert kinds[0] == ("d1", None) and ("kb-graph", "graph") in kinds

    def test_graph_only_unchanged(self, tmp_path, monkeypatch):
        svc = make_svc(tmp_path)
        stub_paths(monkeypatch, svc, bm25=None, vector=None, graph=GRAPH_HIT)
        r = svc.query("阵元间距@t172-graphonly")
        assert r.level == "graph"
        assert r.refs[0]["kind"] == "graph"

    def test_all_miss_falls_to_text(self, tmp_path, monkeypatch):
        svc = make_svc(tmp_path)
        stub_paths(monkeypatch, svc, bm25=None, vector=None, graph=None)
        svc.kb_text = "拖曳线列阵声呐由阵列传感器组成。"
        r = svc.query("阵列传感器@t172-allmiss")
        assert r.level == "text"
        assert set(r.degraded_from) >= {"graph", "bm25", "vector"}

    def test_degenerate_bm25_zero_hits_falls_to_vector(self, tmp_path, monkeypatch):
        """退化语料 BM25 整体零命中（T17.1 实发）→ 如实降级落 vector 语义，不造路。"""
        svc = make_svc(tmp_path)
        stub_paths(monkeypatch, svc, bm25=None, vector=V_TUP)
        r = svc.query("量子纠缠@t172-degenerate")
        assert r.level == "vector"  # bm25 None 已记录 degraded，不产出伪稀疏结果

    def test_disabled_legacy_bytes(self, tmp_path, monkeypatch):
        """bm25_enabled=False：不尝试/不降级不记/level 与融合前逐字节一致（回滚保证）。"""
        svc = make_svc(tmp_path)
        svc.bm25_enabled = False
        called = {"n": 0}

        def _bm25(q, **kw):
            called["n"] += 1
            return B_TUP

        monkeypatch.setattr(svc, "_try_bm25", _bm25)
        monkeypatch.setattr(svc, "_try_milvus", lambda q, **kw: V_TUP)
        monkeypatch.setattr(svc, "_try_neo4j", lambda q, **kw: GRAPH_HIT)
        r = svc.query("阵元间距@t172-disabled")
        assert called["n"] == 0  # 关闭态不尝试
        assert r.level == "vector+graph"  # 既有语义
        assert "bm25" not in r.degraded_from  # 不降级不记


class TestCacheKeyFusion:
    def test_fusion_changes_key(self):
        assert kb_cache_key("q", None) != kb_cache_key("q", None, fusion="bm25:1|fp:x")

    def test_none_fusion_same_as_legacy(self):
        # 关闭态：fusion=None 键与融合前逐字节一致（None 不入指纹材料）
        assert kb_cache_key("q", ["k"]) == kb_cache_key("q", ["k"], fusion=None)

    def test_same_fusion_stable_and_scope_aware(self):
        a = kb_cache_key("q", ["k2", "k1"], fusion="fp1")
        b = kb_cache_key("q", ["k1", "k2"], fusion="fp1")
        c = kb_cache_key("q", ["k1", "k2"], fusion="fp2")
        assert a == b  # 范围集合排序入键（T14.8 语义保留）
        assert a != c  # 语料指纹变化 → 换键
