# -*- coding: utf-8 -*-
"""T11.6 单测：LLM 前置脱敏（出站 wire 级断言）+ 供应商失败转移链。无真实网络。"""

import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm import adapter as adapter_mod
from llm.sanitize import sanitize_messages, sanitize_text

SECRET_MSG = [{"role": "user", "content": "用 key sk-abcdefghijklmnop1234 调用，密码 password=hunter42"}]


# ----------------------------------------------------------------------
# sanitize_text / sanitize_messages
# ----------------------------------------------------------------------
def test_sanitize_patterns():
    out, hits = sanitize_text("sk-abcdefghijklmnop1234 Bearer eyJhbGciOiJIUzI1NiIsInR5 password=hunter42 "
                              "postgres://user:secretpw@db:5432/sonarcad")
    assert "sk-abc" not in out and "[REDACTED:api_key]" in out
    assert "eyJhbGciOi" not in out and "[REDACTED:bearer_token]" in out
    assert "hunter42" not in out and "[REDACTED:password]" in out
    assert "secretpw@db" not in out and "[REDACTED:connection_url]" in out
    assert set(hits) >= {"api_key", "bearer_token", "password", "connection_url"}


def test_sanitize_env_pair_multiline():
    text = "普通第一行\nLLM_API_KEY=sk-secret-value-123\n普通末行"
    out, hits = sanitize_text(text)
    assert "sk-secret-value" not in out and "[REDACTED:env_pair]" in out
    assert hits.get("env_pair") == 1


def test_sanitize_normal_text_untouched():
    out, hits = sanitize_text("拖曳阵由 24 个阵元组成，间距 1.5 米，n=16")
    assert out == "拖曳阵由 24 个阵元组成，间距 1.5 米，n=16"
    assert hits == {}


def test_sanitize_messages_transforms_and_audits(monkeypatch):
    monkeypatch.setenv("SANITIZE_LLM_CONTENT", "1")
    out, hits = sanitize_messages([{"role": "user", "content": "key: sk-abcdefghijklmnop1234"},
                                   {"role": "system", "content": "正常系统提示"}])
    assert "[REDACTED:api_key]" in out[0]["content"]
    assert out[1]["content"] == "正常系统提示"  # 未命中不改写


def test_sanitize_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SANITIZE_LLM_CONTENT", "0")
    out, hits = sanitize_messages(SECRET_MSG)
    assert "sk-abcdefghijklmnop1234" in out[0]["content"] and hits == {}


# ----------------------------------------------------------------------
# wire 级：chat() 出站脱敏 + 失败转移（httpx.stream 打桩）
# ----------------------------------------------------------------------
class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def _two_providers(monkeypatch):
    """broken（优先级高，连不上）+ healthy（次高，正常应答）两家的失败转移链。"""
    from llm.adapter import ProviderConfig

    broken = ProviderConfig(name="vllm-broken", base_url="http://broken.internal/v1",
                            api_key="sk-b", model="m", timeout_s=5)
    healthy = ProviderConfig(name="vllm-ok", base_url="http://healthy.internal/v1",
                             api_key="sk-h", model="m", timeout_s=5)
    monkeypatch.setattr(adapter_mod._store, "_providers", [broken, healthy])
    monkeypatch.setattr(adapter_mod._store, "_cached_at", time.time())
    monkeypatch.setattr(adapter_mod, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr(adapter_mod, "_FAILOVER_ENABLED", True)
    monkeypatch.setattr("app_config.LLM_CACHE_ENABLED", False)
    return broken, healthy


def test_chat_failover_and_outbound_sanitized(monkeypatch, _two_providers):
    captured: list[dict] = []

    def fake_stream(method, url, **kwargs):
        captured.append({"url": url, "json": kwargs["json"]})
        if "broken" in url:
            raise httpx.ConnectError("connection refused")
        return _FakeResp(['data: {"choices":[{"delta":{"content":"vllm-ok"}}]}', "data: [DONE]"])

    monkeypatch.setattr(adapter_mod.httpx, "stream", fake_stream)
    result = adapter_mod.llm_adapter.chat([{"role": "user", "content": "key sk-abcdefghijklmnop1234 请出方案"}])
    assert result == "vllm-ok"  # 主供应商 2 次重试全败 → 次高供应商成功
    assert len(captured) == 3  # broken×2 + healthy×1
    # wire 级脱敏断言：出站请求体不含原始密钥（T11.6 核心承诺）
    for c in captured:
        assert "sk-abcdefghijklmnop1234" not in str(c["json"])
        assert "[REDACTED:api_key]" in str(c["json"])


def test_chat_no_failover_single_provider(monkeypatch, _two_providers):
    monkeypatch.setattr(adapter_mod, "_FAILOVER_ENABLED", False)
    calls = []

    def fake_stream(method, url, **kwargs):
        calls.append(url)
        raise httpx.ConnectError("down")

    monkeypatch.setattr(adapter_mod.httpx, "stream", fake_stream)
    with pytest.raises(RuntimeError, match="仍失败"):
        adapter_mod.llm_adapter.chat([{"role": "user", "content": "hi"}])
    assert len(calls) == 2  # 关闭转移：仅主供应商 LLM_MAX_RETRIES=2 次


def test_provider_health_recorded(monkeypatch, _two_providers):
    def fake_stream(method, url, **kwargs):
        if "broken" in url:
            raise httpx.ConnectError("down")
        return _FakeResp(['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"])

    monkeypatch.setattr(adapter_mod.httpx, "stream", fake_stream)
    adapter_mod.llm_adapter.chat([{"role": "user", "content": "hi"}])
    health = {h["name"]: h for h in adapter_mod.provider_health()}
    assert health["vllm-broken"]["ok"] is False and "down" in (health["vllm-broken"]["last_error"] or "")
    assert health["vllm-ok"]["ok"] is True
