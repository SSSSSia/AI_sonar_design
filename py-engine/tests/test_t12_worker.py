"""T1.2 单测：LLM 适配器（脱敏/热切换/重试上限）与任务定义。不依赖真实 broker/DB。"""

from __future__ import annotations

import json

import pytest

import llm.adapter as adapter_mod
from llm.adapter import LlmAdapter, ProviderConfig

@pytest.fixture(autouse=True)
def _bypass_l1_cache(monkeypatch):
    """T9.6 缓存隔离：本文件的用例针对直连/降级语义，L1 缓存（真 Redis 跨测试污染）
    一律旁路——chat() 不查缓存，query() 直落 _query_uncached。"""
    import threading
    import time as _t

    from storage import cache

    monkeypatch.setattr(cache, "_state",
                        {"client": None, "failed_at": _t.time(), "lock": threading.Lock()})


class TestProviderConfig:
    def test_redacted_hides_api_key(self):
        p = ProviderConfig(name="p1", base_url="http://x", api_key="sk-real-secret", model="m")
        red = p.redacted()
        assert red["api_key"] == "****"
        assert "sk-real-secret" not in json.dumps(red)

    def test_model_dump_contains_secret_only_via_get(self):
        p = ProviderConfig(name="p1", base_url="http://x", api_key="sk-real", model="m")
        assert p.api_key.get_secret_value() == "sk-real"


class TestHotSwitch:
    def test_active_provider_switches_after_db_change(self, monkeypatch):
        store = adapter_mod._ProviderStore(ttl_s=30)
        first = ProviderConfig(name="a", base_url="http://a", api_key="k1", model="m1")
        second = ProviderConfig(name="b", base_url="http://b", api_key="k2", model="m2")
        monkeypatch.setattr(adapter_mod, "_load_providers_from_db", lambda: [first])
        assert store.active().name == "a"
        # TTL 内不重载
        monkeypatch.setattr(adapter_mod, "_load_providers_from_db", lambda: [second])
        assert store.active().name == "a"
        # TTL 过期后重载生效（热切换）
        store._cached_at = 0.0
        assert store.active().name == "b"

    def test_db_empty_keeps_previous(self, monkeypatch):
        store = adapter_mod._ProviderStore(ttl_s=30)
        first = ProviderConfig(name="a", base_url="http://a", api_key="k1", model="m1")
        monkeypatch.setattr(adapter_mod, "_load_providers_from_db", lambda: [first])
        assert store.active().name == "a"
        store._cached_at = 0.0
        monkeypatch.setattr(adapter_mod, "_load_providers_from_db", lambda: None)
        assert store.active().name == "a"  # 库不可用不阻断，保持原供应商


class _FakeStreamResp:
    """流式响应替身：上下文管理器 + SSE 行。"""

    def __init__(self, sse_lines: list[str]):
        self._lines = sse_lines

    def raise_for_status(self):
        pass

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _sse(content: str) -> list[str]:
    return [
        'data: {"choices":[{"delta":{"content":"%s"}}]}' % content,
        "data: [DONE]",
    ]


class TestLlmAdapterRetry:
    def test_retry_then_success(self, monkeypatch):
        calls = {"n": 0}

        def flaky_stream(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return _FakeStreamResp(_sse("ok"))

        monkeypatch.setattr(adapter_mod, "active_provider",
                            lambda: ProviderConfig(name="t", base_url="http://t", api_key="k", model="m"))
        monkeypatch.setattr(adapter_mod.httpx, "stream", flaky_stream)
        assert LlmAdapter().chat([{"role": "user", "content": "hi"}]) == "ok"
        assert calls["n"] == 2

    def test_retry_upper_bound(self, monkeypatch):
        calls = {"n": 0}

        def always_error(*args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("down")

        monkeypatch.setattr(adapter_mod, "active_provider",
                            lambda: ProviderConfig(name="t", base_url="http://t", api_key="k", model="m"))
        monkeypatch.setattr(adapter_mod.httpx, "stream", always_error)
        with pytest.raises(RuntimeError, match="重试"):
            LlmAdapter().chat([{"role": "user", "content": "hi"}])
        assert calls["n"] == adapter_mod.LLM_MAX_RETRIES  # A-13：有界，不死循环

    def test_stream_assembles_chunks(self, monkeypatch):
        def multi_chunk(*args, **kwargs):
            return _FakeStreamResp([
                'data: {"choices":[{"delta":{"content":"你"}}]}',
                'data: {"choices":[{"delta":{"content":"好"}}]}',
                "data: [DONE]",
            ])

        monkeypatch.setattr(adapter_mod, "active_provider",
                            lambda: ProviderConfig(name="t", base_url="http://t", api_key="k", model="m"))
        monkeypatch.setattr(adapter_mod.httpx, "stream", multi_chunk)
        assert LlmAdapter().chat([{"role": "user", "content": "hi"}]) == "你好"


class TestNoProvider:
    def test_missing_provider_raises(self, monkeypatch):
        store = adapter_mod._ProviderStore(ttl_s=30)
        monkeypatch.setattr(adapter_mod, "_load_providers_from_db", lambda: None)
        monkeypatch.setattr(adapter_mod, "LLM_BASE_URL", "")
        monkeypatch.setattr(adapter_mod, "LLM_API_KEY", "")
        monkeypatch.setattr(adapter_mod, "_store", store)
        with pytest.raises(RuntimeError, match="无可用 LLM 供应商"):
            adapter_mod.active_provider()
