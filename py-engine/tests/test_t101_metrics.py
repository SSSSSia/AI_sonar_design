"""T10.1a/T10.3 埋点单测（PHASE-010）。

覆盖：/metrics 暴露与路由模板标签、阶段计时装饰器（成功/失败/软超时）、缓存计数联动、
检索时延打点、stage_duration 落库 kind 参数。不依赖 Redis/真实供应商（均 monkeypatch）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY


def _sample(name: str, labels: dict | None = None) -> float | None:
    return REGISTRY.get_sample_value(name, labels or {})


# ---------------------------------------------------------------------------
# /metrics 端点与 HTTP 中间件
# ---------------------------------------------------------------------------
class TestMetricsEndpoint:
    def test_metrics_exposed_and_counts_healthz(self):
        from api.main import app

        with TestClient(app) as client:  # with 语句触发 lifespan（T9.1 心跳/线程池）
            r0 = client.get("/metrics")
            assert r0.status_code == 200
            assert "py_http_requests_total" in r0.text
            client.get("/healthz")
            r1 = client.get("/metrics")
            assert 'route="/healthz"' in r1.text

    def test_route_label_is_template_not_id(self, monkeypatch):
        from api.main import app

        class _FakeResult:
            state = "PENDING"

            def successful(self):
                return False

            def failed(self):
                return False

        from worker import celery_app as celery_mod

        monkeypatch.setattr(celery_mod.celery_app, "AsyncResult", lambda task_id: _FakeResult())
        with TestClient(app) as client:
            client.get("/tasks/nonexistent-task-id")  # 不触真 Redis 后端
            r = client.get("/metrics")
            assert 'route="/tasks/{task_id}"' in r.text
            assert "nonexistent-task-id" not in r.text  # 原始 id 不进标签（防基数爆炸）


# ---------------------------------------------------------------------------
# 阶段计时（worker.tasks）
# ---------------------------------------------------------------------------
class TestStageSpan:
    @pytest.fixture(autouse=True)
    def _capture_log(self, monkeypatch):
        self.rows: list[tuple] = []

        def fake_append(session_id, level, text, kind="log"):
            self.rows.append((session_id, level, text, kind))

        from storage import log_store

        monkeypatch.setattr(log_store, "append", fake_append)

    def test_record_stage_ok_and_duration(self):
        from worker import tasks

        before = _sample("py_agent_task_total", {"stage": "A1", "result": "ok"}) or 0.0
        tasks._record_stage("sess-x", "A1", 12.5, True)
        assert (_sample("py_agent_task_total", {"stage": "A1", "result": "ok"}) or 0.0) == pytest.approx(before + 1)
        assert self.rows[-1][3] == "stage_duration"
        assert "A1" in self.rows[-1][2] and "12.5" in self.rows[-1][2]

    def test_record_stage_soft_timeout_counter(self):
        from worker import tasks

        before = _sample("py_agent_soft_timeout_total", {"stage": "A2"}) or 0.0
        tasks._record_stage("sess-x", "A2", 901.0, True)  # > 900s 软超时线
        assert (_sample("py_agent_soft_timeout_total", {"stage": "A2"}) or 0.0) == pytest.approx(before + 1)

    def test_agent_stage_decorator_error_path(self):
        from worker import tasks

        @tasks._agent_stage("A3")
        def boom(self, session_id):
            raise RuntimeError("沙箱炸了")

        before_err = _sample("py_agent_task_total", {"stage": "A3", "result": "error"}) or 0.0
        n_rows = len(self.rows)
        with pytest.raises(RuntimeError):
            boom(None, "sess-y")
        assert (_sample("py_agent_task_total", {"stage": "A3", "result": "error"}) or 0.0) == pytest.approx(before_err + 1)
        assert len(self.rows) == n_rows + 1 and self.rows[-1][1] == "info"  # 失败也留计时行

    def test_all_five_agent_tasks_decorated(self):
        from worker import tasks

        for fn in (tasks.a1_task, tasks.a2_task, tasks.a3_task, tasks.a4_task, tasks.a5_task):
            assert getattr(fn, "__wrapped__", None) is not None, f"{fn.name} 未挂阶段计时装饰器"


# ---------------------------------------------------------------------------
# 缓存 / 检索 / LLM 打点联动
# ---------------------------------------------------------------------------
class TestHookMetrics:
    def test_cache_bump_feeds_prometheus(self):
        from storage import cache

        cache.reset_stats()
        before = _sample("py_cache_ops_total", {"layer": "llm", "result": "hit"}) or 0.0
        cache._bump("llm", True)
        assert (_sample("py_cache_ops_total", {"layer": "llm", "result": "hit"}) or 0.0) == pytest.approx(before + 1)

    def test_kb_query_duration_labeled_by_level(self, monkeypatch):
        from knowledge.service import GraphRAGService, RetrievalResult
        from storage import cache

        monkeypatch.setattr(cache, "get_json", lambda layer, key: None)
        monkeypatch.setattr(cache, "set_json", lambda key, value, ttl: False)
        svc = GraphRAGService.__new__(GraphRAGService)  # 绕过重初始化（t96 同法）
        monkeypatch.setattr(svc, "_query_uncached", lambda q, kb_ids=None: RetrievalResult("内容", "graph"))
        before = _sample("py_kb_query_duration_seconds_count", {"level": "graph"}) or 0.0
        svc.query("拖曳阵总体设计")
        assert (_sample("py_kb_query_duration_seconds_count", {"level": "graph"}) or 0.0) == pytest.approx(before + 1)

    def test_llm_call_observed(self):
        from observability.metrics import observe_llm

        before = _sample("py_llm_call_total", {"provider": "unit-test", "result": "ok"}) or 0.0
        observe_llm("unit-test", 1.23, True)
        assert (_sample("py_llm_call_total", {"provider": "unit-test", "result": "ok"}) or 0.0) == pytest.approx(before + 1)

    def test_cadquery_outcome_counter(self):
        from observability.metrics import observe_a3_sandbox

        before = _sample("py_cadquery_exec_total", {"outcome": "fail"}) or 0.0
        observe_a3_sandbox(3.2, False)
        assert (_sample("py_cadquery_exec_total", {"outcome": "fail"}) or 0.0) == pytest.approx(before + 1)


# ---------------------------------------------------------------------------
# worker /metrics 启动器
# ---------------------------------------------------------------------------
class TestWorkerMetricsServer:
    def test_start_metrics_server_binds_configured_port(self, monkeypatch):
        captured = {}

        def fake_start(port, addr="0.0.0.0"):
            captured["port"] = port

        import observability.metrics as m

        monkeypatch.setattr(m._prom, "start_http_server", fake_start)
        assert m.start_metrics_server(9101) is True
        assert captured["port"] == 9101

    def test_metrics_port_default(self):
        from app_config import METRICS_PORT

        assert METRICS_PORT == 9101
