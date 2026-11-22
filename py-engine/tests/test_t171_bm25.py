# -*- coding: utf-8 -*-
"""T17.1 BM25 稀疏路单测（PHASE-017；契约=ADR-016 §1）。

纯注入测试：不触真库/真 Milvus（corpus_loader 可注入）；TTL 语义经 monkeypatch time.time
驱动；检索域邻接回归（test_t16_retrieval/test_u310/test_t62）零改动由跑批确认——本模块
不被任何生产代码 import（T17.2 才接线），纯加法。
"""
from __future__ import annotations

import pytest

from kb.bm25 import tokenize, Bm25Index, Bm25Retriever

CORPUS = [
    {"doc_id": "d1", "location": "页1", "text": "拖曳线列阵声呐由拖曳线列阵湿端和舰载干端组成。"},
    {"doc_id": "d2", "location": "页2", "text": "换能器基阵是声呐系统的核心部件，阵元间距影响波束形成。"},
    {"doc_id": "d3", "location": "页3", "text": "The towed array sonar is deployed behind the vessel."},
]


class TestTokenize:
    def test_latin_words_lowercased_and_len_gt1(self):
        # service.py:475 同口径：小写化 + 长度>1 保留
        assert tokenize("Sonar ARRAy") == ["sonar", "array"]

    def test_latin_single_char_dropped(self):
        assert tokenize("a I go") == ["go"]

    def test_cjk_bigram(self):
        # 连续汉字段 2-gram：「拖曳阵」→ 拖曳 / 曳阵
        assert tokenize("拖曳阵") == ["拖曳", "曳阵"]

    def test_cjk_single_char_kept(self):
        assert tokenize("阵") == ["阵"]

    def test_punctuation_whitespace_stripped(self):
        # 语料侧必要修正：标点/空白不入 token 空间（文本兜底级对原始串切 gram，此处不同）
        toks = tokenize("声呐（主动）: sonar！")
        assert "（" not in toks and "！" not in toks and ":" not in toks
        assert "声呐" in toks and "sonar" in toks

    def test_mixed_text(self):
        toks = tokenize("拖曳阵 towed array 2024")
        assert "towed" in toks and "2024" in toks and "拖曳" in toks

    def test_empty_or_none(self):
        assert tokenize("") == []
        assert tokenize(None) == []


class TestBm25Index:
    def _index(self) -> Bm25Index:
        return Bm25Index(CORPUS)

    def test_chinese_query_ranks_relevant_first(self):
        hits = self._index().search("拖曳线列阵")
        assert hits and hits[0]["doc_id"] == "d1"

    def test_english_query_ranks_relevant_first(self):
        hits = self._index().search("towed array")
        assert hits and hits[0]["doc_id"] == "d3"

    def test_hits_shape_and_score_nonnegative(self):
        for h in self._index().search("声呐"):
            assert set(h) >= {"doc_id", "location", "text", "score"}
            assert h["score"] >= 0

    def test_top_k_truncation(self):
        # 命中文档须 < 半数才有正 IDF——rank_bm25 BM25Okapi 对命中过半的词取负 IDF 下限
        # （epsilon×average_idf，可为负），「零分不产命中」口径会如实过滤掉退化语料
        # （首版测试 8 文档全同即触发 0 命中——固有行为，非模块缺陷，故语料须可分）
        corpus = [
            {"doc_id": f"hit{i}", "location": "", "text": f"声呐 目标检测 方案{i} 海况{i}"} for i in range(3)
        ] + [
            {"doc_id": f"other{i}", "location": "", "text": f"导航 数据链 记录{i} 状态{i}"} for i in range(5)
        ]
        hits = Bm25Index(corpus).search("声呐", top_k=3)
        assert len(hits) == 3
        assert all(h["doc_id"].startswith("hit") for h in hits)

    def test_doc_ids_none_filter_strict_empty(self):
        idx = self._index()
        assert idx.search("声呐", doc_ids=None)  # 全量
        scoped = idx.search("基阵", doc_ids=["d2"])
        assert scoped and all(h["doc_id"] == "d2" for h in scoped)
        # 严格空集（显式安全口径，与 vector_store「空列表=不过滤」不同，docstring 明示）
        assert idx.search("声呐", doc_ids=[]) == []

    def test_empty_corpus_no_crash(self):
        idx = Bm25Index([])
        assert idx.size == 0
        assert idx.fingerprint is None
        assert idx.search("声呐") == []

    def test_zero_score_produces_no_hit(self):
        # 语料无「潜艇」相关 token → 零分不入结果（无信号不造命中，A-16）
        assert self._index().search("潜艇") == []

    def test_empty_query_tokens(self):
        assert self._index().search("！") == []

    def test_fingerprint_changes_with_corpus(self):
        fp1 = Bm25Index(CORPUS).fingerprint
        fp2 = Bm25Index(CORPUS + [{"doc_id": "d4", "location": "", "text": "海况等级"}]).fingerprint
        assert fp1 and fp2 and fp1 != fp2


class TestBm25Retriever:
    def _patch_clock(self, monkeypatch, t):
        monkeypatch.setattr("time.time", lambda: t["now"])

    def test_ttl_no_reload_within_window(self, monkeypatch):
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return CORPUS

        t = {"now": 1000.0}
        self._patch_clock(monkeypatch, t)
        r = Bm25Retriever(corpus_loader=loader, ttl_s=30)
        r.search("声呐")
        r.search("基阵")
        assert calls["n"] == 1
        t["now"] = 1020.0  # TTL 内（30s 窗口未满）
        r.search("波束")
        assert calls["n"] == 1

    def test_ttl_expiry_rebuilds(self, monkeypatch):
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return CORPUS

        t = {"now": 1000.0}
        self._patch_clock(monkeypatch, t)
        r = Bm25Retriever(corpus_loader=loader, ttl_s=30)
        r.search("声呐")
        t["now"] = 1031.0  # TTL 过期
        r.search("声呐")
        assert calls["n"] == 2

    def test_loader_failure_degrades_and_no_hammering(self, monkeypatch):
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            raise RuntimeError("db down")

        t = {"now": 1000.0}
        self._patch_clock(monkeypatch, t)
        r = Bm25Retriever(corpus_loader=loader)
        assert r.search("声呐") is None
        assert r.last_error and "RuntimeError" in r.last_error
        t["now"] = 1010.0  # 失败也计时（service.py:149 先例）：TTL 内不打库
        assert r.search("声呐") is None
        assert calls["n"] == 1
        assert r.status()["last_error"]

    def test_failure_keeps_previous_index(self, monkeypatch):
        # service.py:146「沿用内存语料」同口径：重载失败保留上一份可用索引
        state = {"fail": False}

        def loader():
            if state["fail"]:
                raise RuntimeError("db down")
            return CORPUS

        t = {"now": 1000.0}
        self._patch_clock(monkeypatch, t)
        r = Bm25Retriever(corpus_loader=loader)
        assert r.search("声呐")  # 首次成功
        state["fail"] = True
        t["now"] = 1100.0  # TTL 过期触发重载失败
        assert r.search("声呐")  # 沿用上一份索引
        assert r.last_error  # 且失败原因如实记录

    def test_empty_corpus_returns_none(self):
        r = Bm25Retriever(corpus_loader=lambda: [])
        assert r.search("声呐") is None

    def test_status_honest(self):
        r = Bm25Retriever(corpus_loader=lambda: CORPUS)
        r.search("声呐")
        st = r.status()
        assert st["size"] == 3 and st["loaded"] is True and st["last_error"] is None
        assert st["ttl_s"] == 30

    def test_fingerprint_via_retriever(self, monkeypatch):
        t = {"now": 1000.0}
        self._patch_clock(monkeypatch, t)
        r = Bm25Retriever(corpus_loader=lambda: CORPUS)
        assert r.fingerprint is None  # 从未加载
        r.search("声呐")
        assert r.fingerprint == Bm25Index(CORPUS).fingerprint

    def test_module_singleton_and_entrypoint(self, monkeypatch):
        import kb.bm25 as m

        r = m.Bm25Retriever(corpus_loader=lambda: CORPUS)
        monkeypatch.setattr(m, "_retriever", r)
        assert m.get_bm25_retriever() is r
        assert m.bm25_search("基阵") and m.bm25_search("基阵")[0]["doc_id"] == "d2"
