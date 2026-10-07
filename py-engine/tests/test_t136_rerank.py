# -*- coding: utf-8 -*-
"""T13.6 单测：交叉编码器重排序客户端（kb/rerank.py，配置驱动 §7.4）。

解析兼容（relevance_score/score 双形态）、排序截断、失败维持原序（A-14 如实降级）、
默认不开。无真实供应商 key——wire 真实性由 mock 请求体断言承接（A-16 如实标注
「实测待配置」）。
"""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kb import rerank as rerank_mod
from kb.rerank import RerankClient

HITS = [
    {"doc_id": "d1", "location": "L1", "text": "无关段落甲", "score": 0.91},
    {"doc_id": "d2", "location": "L2", "text": "高相关段落（应排第一）", "score": 0.62},
    {"doc_id": "d3", "location": "L3", "text": "中等相关段落", "score": 0.80},
]


@pytest.fixture(autouse=True)
def _reset_singleton():
    rerank_mod._reranker = None
    yield
    rerank_mod._reranker = None


def test_not_configured_by_default(monkeypatch):
    for k in ("KB_RERANK_ENDPOINT", "KB_RERANK_MODEL", "KB_RERANK_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    client = RerankClient()
    assert not client.available()
    # 未配置 → service 挂点直接跳过（fetch_k 分支判断），不会发起请求
    assert client.status()["configured"] is False


def test_rerank_request_and_parse(monkeypatch):
    monkeypatch.setenv("KB_RERANK_ENDPOINT", "http://rerank.internal/v1")
    monkeypatch.setenv("KB_RERANK_MODEL", "bge-reranker-v2-m3")
    monkeypatch.setenv("KB_RERANK_API_KEY", "sk-rerank-test")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.97},
            {"index": 2, "relevance_score": 0.55},
            {"index": 0, "relevance_score": 0.10},
        ]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(rerank_mod.httpx, "post", fake_post)
    client = RerankClient()
    ranked = client.rerank("查询", [h["text"] for h in HITS])
    assert [i for i, _ in ranked] == [1, 2, 0]  # 按分降序
    assert captured["url"].endswith("/rerank")
    assert captured["headers"]["Authorization"] == "Bearer sk-rerank-test"
    assert captured["json"]["model"] == "bge-reranker-v2-m3"
    assert captured["json"]["documents"] == [h["text"] for h in HITS]


def test_apply_reorders_and_truncates_with_score(monkeypatch):
    monkeypatch.setenv("KB_RERANK_ENDPOINT", "http://rerank.internal/v1")
    monkeypatch.setenv("KB_RERANK_MODEL", "m")
    monkeypatch.setenv("KB_RERANK_TOPN", "2")

    def fake_post(url, **kwargs):
        # 供应商忽略 top_n 返回 3 条 → 客户端仍按 top_n=2 截断
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.97},
            {"index": 0, "relevance_score": 0.30},
            {"index": 2, "relevance_score": 0.10},
        ]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(rerank_mod.httpx, "post", fake_post)
    client = RerankClient()
    assert client.top_n == 2
    out = client.apply("查询", HITS)
    assert len(out) == 2
    assert out[0]["doc_id"] == "d2" and out[0]["rerank_score"] == pytest.approx(0.97)
    assert out[1]["doc_id"] == "d1"


def test_apply_failure_keeps_original_order(monkeypatch):
    """重排失败 → 维持向量原序（主链路不受影响），last_error 如实记录。"""
    monkeypatch.setenv("KB_RERANK_ENDPOINT", "http://rerank.internal/v1")
    monkeypatch.setenv("KB_RERANK_MODEL", "m")

    def fake_post(url, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(rerank_mod.httpx, "post", fake_post)
    client = RerankClient()
    out = client.apply("查询", HITS)
    assert out == HITS
    assert "ConnectError" in (client.last_error or "")


def test_apply_accepts_cohere_score_field(monkeypatch):
    """兼容旧 Cohere 形态（score 而非 relevance_score）。"""
    monkeypatch.setenv("KB_RERANK_ENDPOINT", "http://rerank.internal/v1")
    monkeypatch.setenv("KB_RERANK_MODEL", "m")
    monkeypatch.setattr(rerank_mod.httpx, "post", lambda url, **kw: httpx.Response(
        200, json={"results": [{"index": 2, "score": 0.9}, {"index": 0, "score": 0.1}]},
        request=httpx.Request("POST", url)))
    client = RerankClient()
    out = client.apply("查询", HITS)
    assert out[0]["doc_id"] == "d3"


def test_service_hook_fetch_k_branch(monkeypatch):
    """挂点语义：可用时 fetch_k 扩量召回，不可用时维持 5。"""
    monkeypatch.setenv("KB_RERANK_ENDPOINT", "http://rerank.internal/v1")
    monkeypatch.setenv("KB_RERANK_MODEL", "m")
    client = rerank_mod.get_reranker()
    assert client.available() and client.fetch_k == 20
    monkeypatch.delenv("KB_RERANK_ENDPOINT")
    client2 = RerankClient()
    assert not client2.available()
