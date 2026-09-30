# -*- coding: utf-8 -*-
"""T9.6 L1 多层缓存单测（PHASE-009 §2.6）。

- 纯单测：指纹稳定性、命中/未命中计数（A-16 无数据 hit_rate=None）、Redis 旁路纪律、
  kb_store 变更挂钩、检索缓存（text 级不缓存）；
- 真 Redis（可达才跑）：LLM chat 边界缓存命中、cache:kb:* 失效清理。
"""

import fnmatch
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import cache, kb_store


class _FakeRedis:
    """最小内存 Redis 替身（get/setex/scan/delete/ping）。"""

    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def scan(self, cursor=0, match=None, count=None):
        keys = [k for k in self.store if fnmatch.fnmatch(k, match or "*")]
        return 0, keys

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                n += 1
        return n

    def ping(self):
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(cache, "_state", {"client": fake, "failed_at": 0.0, "lock": __import__("threading").Lock()})
    cache.reset_stats()
    yield fake
    cache.reset_stats()


# ----------------------------------------------------------------------
# 纯单测
# ----------------------------------------------------------------------
def test_fingerprint_stable_and_distinct():
    a = cache.fingerprint("m", [{"role": "user", "content": "hi"}], {"temperature": 0.2})
    b = cache.fingerprint("m", [{"role": "user", "content": "hi"}], {"temperature": 0.2})
    c = cache.fingerprint("m", [{"role": "user", "content": "hi"}], {"temperature": 0.9})
    assert a == b and a != c
    assert cache.llm_cache_key("m", [], {}).startswith("cache:llm:call:")
    assert cache.kb_cache_key("q", ["b1", "b2"]) == cache.kb_cache_key("q", ["b2", "b1"])  # kb_ids 序无关


def test_counters_and_hit_rate(fake_redis):
    cache.set_str("k1", "v1", 60)
    assert cache.get_str("llm", "k1") == "v1"          # 命中
    assert cache.get_str("llm", "missing") is None      # 未命中
    s = cache.stats()
    assert s["layers"]["llm"] == {"hit": 1, "miss": 1, "hit_rate": 50.0}
    assert s["layers"]["kb"] == {"hit": 0, "miss": 0, "hit_rate": None}  # A-16：无数据不造假


def test_redis_unavailable_bypasses_silently(monkeypatch):
    """Redis 不可用：读返回 None、写返回 False、计数不动、绝不抛异常（旁路纪律）。"""
    monkeypatch.setattr(cache, "_state", {"client": None, "failed_at": __import__("time").time(),
                                          "lock": __import__("threading").Lock()})
    cache.reset_stats()
    assert cache.get_str("llm", "any") is None
    assert cache.set_str("any", "v", 60) is False
    assert cache.invalidate_kb() == -1
    assert cache.stats()["layers"]["llm"] == {"hit": 0, "miss": 0, "hit_rate": None}
    cache.reset_stats()


def test_kb_store_mutations_wired_to_invalidation(monkeypatch):
    """kb_store 变更方法已挂失效包装（__wrapped__ 存在 = functools.wraps 生效）。"""
    wired = [n for n in ("create_document", "delete_document", "replace_chunks",
                         "set_extraction_status", "delete_base", "add_docs_to_base")
             if hasattr(getattr(kb_store, n), "__wrapped__")]
    assert len(wired) == 6, f"未挂钩: {wired}"


def test_kb_query_cache_skips_text_level(monkeypatch, fake_redis):
    """检索缓存：非降级级缓存命中；text 级不缓存（劣化结果不粘住）。"""
    from knowledge.service import GraphRAGService, RetrievalResult

    svc = GraphRAGService.__new__(GraphRAGService)  # 绕过 __init__（不碰语料）
    calls = {"n": 0}

    def _uncached_vector(self, q, kb_ids=None):
        calls["n"] += 1
        return RetrievalResult(f"vec:{q}", "vector", [], [{"doc_id": "d1", "location": "1"}])

    monkeypatch.setattr(GraphRAGService, "_query_uncached", _uncached_vector)
    r1 = svc.query("拖曳阵设计")
    r2 = svc.query("拖曳阵设计")
    assert calls["n"] == 1 and r2.content == "vec:拖曳阵设计" and r2.level == "vector"

    def _uncached_text(self, q, kb_ids=None):
        calls["n"] += 1
        return RetrievalResult("关键词命中", "text")

    monkeypatch.setattr(GraphRAGService, "_query_uncached", _uncached_text)
    cache.reset_stats()
    svc.query("另一个问题")
    svc.query("另一个问题")
    assert calls["n"] >= 2, "text 级不得写入缓存"
    assert cache.stats()["layers"]["kb"]["hit"] == 0


def test_codegen_key_tracks_ir2_parameters():
    ir2_a = {"design_document": "d", "parameters": [{"name": "p", "value": 1}]}
    ir2_b = {"design_document": "d", "parameters": [{"name": "p", "value": 2}]}
    assert cache.codegen_cache_key(ir2_a) != cache.codegen_cache_key(ir2_b)


# ----------------------------------------------------------------------
# 真 Redis（不可达跳过）
# ----------------------------------------------------------------------
def _redis_reachable() -> bool:
    try:
        client = cache._get_client()
        return client is not None
    except Exception:
        return False


@pytest.mark.skipif(not _redis_reachable(), reason="Redis 不可达（未 source .env 或 redis 未启动）")
class TestWithLiveRedis:
    def test_llm_chat_boundary_cache_hit(self, monkeypatch):
        """缓存命中时不发起网络调用（无供应商网络也能返回缓存文本）。"""
        from llm import adapter

        class _P:
            name = "fake"
            model = "fake-model"

        monkeypatch.setattr(adapter, "active_provider", lambda: _P())
        messages = [{"role": "user", "content": "T9.6 缓存边界测试"}]
        key = cache.llm_cache_key("fake-model", messages, {})
        assert cache.set_str(key, "CACHED-ANSWER", cache.TTL_LLM_S)
        assert adapter.llm_adapter.chat(messages) == "CACHED-ANSWER"

    def test_invalidate_kb_clears_pattern(self):
        assert cache.set_str("cache:kb:query:x1", "v", 60)
        assert cache.set_str("cache:kb:query:x2", "v", 60)
        assert cache.set_str("cache:llm:call:y", "v", 60)
        deleted = cache.invalidate_kb()
        assert deleted >= 2
        assert cache.get_str("kb", "cache:kb:query:x1") is None
        assert cache.get_str("llm", "cache:llm:call:y") == "v"  # 只清 kb 层
        cache.invalidate_llm()
