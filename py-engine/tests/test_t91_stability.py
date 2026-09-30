"""T9.1 单测（PHASE-009）：事件循环心跳探针 + 线程池容量 + 长轮询 async 化回归护栏。

复现脚本见 tests/load/hang_repro.py（机制 A/B 对照）；本文件覆盖可确定性断言的部分。
"""

from __future__ import annotations

import inspect
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import api.main as main_mod
from api.cad_agent import poll_tool_calls


def _client() -> TestClient:
    return TestClient(main_mod.app)


def test_lifespan_raises_threadpool_and_starts_heartbeat():
    with _client() as client:
        # 线程池上限在 lifespan（事件循环内）调整并记录到 heartbeat，供此处观测
        assert main_mod.heartbeat["pool_tokens"] == main_mod.API_THREAD_POOL_SIZE, \
            "lifespan 应把 AnyIO 线程池上限提到配置值"

        assert main_mod.heartbeat["enabled"] is True
        t0 = main_mod.heartbeat["ts"]
        time.sleep(1.3)
        assert main_mod.heartbeat["ts"] > t0, "心跳协程应每秒刷新时间戳"

        r = client.get("/healthz")
        assert r.status_code == 200  # broker 不可达时也应 200 degraded，而非 5xx
        body = r.json()
        assert body["event_loop"] == "ok"
        assert body["event_loop_lag_s"] < main_mod.API_HEARTBEAT_STALE_S


def test_healthz_heartbeat_stale_returns_503(monkeypatch):
    """心跳滞后超阈值（事件循环被同步调用卡死）→ 503，供容器探针联动重启。"""
    with _client() as client:
        # 把阈值调成 -1 使任意滞后都判定 stale，避免与心跳协程的 1s 刷新产生竞态
        monkeypatch.setattr(main_mod, "API_HEARTBEAT_STALE_S", -1.0)
        r = client.get("/healthz")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "unhealthy"
        assert "event_loop" in body


def test_healthz_heartbeat_disabled_stays_200():
    """心跳未启用（如测试直连）时不误报 503。"""
    with _client() as client:
        main_mod.heartbeat["enabled"] = False
        try:
            r = client.get("/healthz")
            assert r.status_code == 200
            assert r.json()["event_loop_lag_s"] == 0.0
        finally:
            main_mod.heartbeat["enabled"] = True


def test_longpoll_endpoint_is_async():
    """回归护栏：cad_agent 长轮询必须保持 async（同步实现会占线程池线程最长 25s）。"""
    assert inspect.iscoroutinefunction(poll_tool_calls), \
        "poll_tool_calls 被改回同步 def 会重新引入线程池耗尽风险（T9.1）"
