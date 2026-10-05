# -*- coding: utf-8 -*-
"""T12.1 单测：LLM 重试指数退避——429/5xx/网络错误退避重试，4xx 快速失败。"""

import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm import adapter as adapter_mod
from llm.adapter import ProviderConfig, _should_backoff


def _provider(name="p1", url="http://prov.internal/v1"):
    return ProviderConfig(name=name, base_url=url, api_key="sk-x", model="m", timeout_s=5)


def _status_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://prov.internal/v1/chat/completions")
    return httpx.HTTPStatusError(f"{status}", request=req,
                                 response=httpx.Response(status, request=req))


class _FakeResp:
    def __init__(self, content="ok"):
        self._content = content

    def raise_for_status(self):
        pass

    def iter_lines(self):
        return iter([f'data: {{"choices":[{{"delta":{{"content":"{self._content}"}}}}]}}', "data: [DONE]"])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def _single(monkeypatch):
    monkeypatch.setattr(adapter_mod._store, "_providers", [_provider()])
    monkeypatch.setattr(adapter_mod._store, "_cached_at", time.time())
    monkeypatch.setattr(adapter_mod, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr(adapter_mod, "_FAILOVER_ENABLED", True)
    monkeypatch.setattr("app_config.LLM_CACHE_ENABLED", False)
    sleeps: list[float] = []
    monkeypatch.setattr(adapter_mod.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def test_429_backs_off_then_succeeds(monkeypatch, _single):
    sleeps = _single
    calls = []

    def fake_stream(method, url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise _status_error(429)
        return _FakeResp("after-429")

    monkeypatch.setattr(adapter_mod.httpx, "stream", fake_stream)
    result = adapter_mod.llm_adapter.chat([{"role": "user", "content": "hi"}])
    assert result == "after-429"
    assert len(calls) == 2
    assert len(sleeps) == 1 and sleeps[0] >= adapter_mod.LLM_BACKOFF_BASE_S  # 指数退避首次 = base


def test_404_fails_fast_without_retry(monkeypatch, _single):
    sleeps = _single
    calls = []

    def fake_stream(method, url, **kwargs):
        calls.append(url)
        raise _status_error(404)

    monkeypatch.setattr(adapter_mod.httpx, "stream", fake_stream)
    # 链路耗尽 → chat 抛 RuntimeError（转移动语义在 test_t116 覆盖）
    with pytest.raises(RuntimeError, match="仍失败"):
        adapter_mod.llm_adapter.chat([{"role": "user", "content": "hi"}])
    assert len(calls) == 1  # 404 不重试
    assert sleeps == []


def test_transport_error_backs_off(monkeypatch, _single):
    sleeps = _single
    calls = []

    def fake_stream(method, url, **kwargs):
        calls.append(url)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(adapter_mod.httpx, "stream", fake_stream)
    with pytest.raises(RuntimeError, match="仍失败"):
        adapter_mod.llm_adapter.chat([{"role": "user", "content": "hi"}])
    assert len(calls) == 2 and len(sleeps) == 1  # 网络错误退避重试


def test_should_backoff_classification():
    assert _should_backoff(_status_error(429)) is True
    assert _should_backoff(_status_error(503)) is True
    assert _should_backoff(httpx.ConnectError("x")) is True
    assert _should_backoff(_status_error(401)) is False
    assert _should_backoff(_status_error(422)) is False
