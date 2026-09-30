"""Prometheus 指标埋点（T10.1a/T10.3，PHASE-010）。

指标清单（对齐总体设计 §10.5 监控指标与 4 条核心告警的数据源）：
- py_http_requests_total / py_http_request_latency_seconds      API 计数与时延（route 用模板路径，防基数爆炸）
- py_agent_task_duration_seconds{stage}                         A1–A5 任务耗时直方图（含 900s 桶 = 告警①「>15min 占比」口径）
- py_agent_task_total{stage,result} / py_agent_soft_timeout_total{stage}
- py_cadquery_exec_total{outcome}                               沙箱每次执行计数（含修复循环，失败率=告警②）
- py_cadquery_fix_rounds / py_a3_codegen_seconds / py_a3_sandbox_seconds / py_geometry_inspect_seconds
- py_kb_query_duration_seconds{level}                           单条检索时延（P95=告警③）
- py_kb_query_batch_size                                        检索批量子查询数（T10.4 优化对照基线）
- py_llm_call_duration_seconds{provider} / py_llm_call_total{provider,result}
- py_cache_ops_total{layer,result}                              L1 缓存命中/未命中（命中率面板，§10.5 第 5 条告警的埋点预留）

暴露方式：
- API 进程：/metrics 显式路由（generate_latest；网关无该路由 = 天然不外暴，仅内网/宿主抓取）；
- worker 进程：worker_ready 时 start_http_server(METRICS_PORT)（celery_app.py）。
  两进程各自暴露本进程计数（缓存计数本就按进程独立，T9.6 既有口径）；
  Prometheus 把两目标当独立实例，面板按需 sum。

prometheus_client 缺失时全部静默降级为 no-op（主链路不受影响，/metrics 缺失如实）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("sonarcad.metrics")

try:
    from prometheus_client import Counter, Histogram, start_http_server

    HAS_PROMETHEUS = True
except ImportError:  # 依赖缺失时静默降级（A-16：主链路优先）
    HAS_PROMETHEUS = False

    class _NoopMetric:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def labels(self, *args, **kwargs) -> "_NoopMetric":
            return self

        def inc(self, *args, **kwargs) -> None:
            pass

        def observe(self, *args, **kwargs) -> None:
            pass

    class _NoopModule:
        Counter = Histogram = _NoopMetric

        @staticmethod
        @staticmethod
        def start_http_server(*args, **kwargs) -> None:
            logger.warning("prometheus-client 未安装，worker /metrics 未暴露（pip install prometheus-client）")

    _prom = _NoopModule()
else:
    import prometheus_client as _prom  # noqa: F401

# 告警①口径：任务耗时软超时线（总体设计 §10.5「Agent 执行超时」落地为 >15min 占比）
AGENT_SOFT_TIMEOUT_S = 900.0

HTTP_REQUESTS = _prom.Counter(
    "py_http_requests_total", "API 请求计数", ["method", "route", "status"]
)
HTTP_LATENCY = _prom.Histogram(
    "py_http_request_latency_seconds",
    "API 请求时延（秒）",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
AGENT_TASK_DURATION = _prom.Histogram(
    "py_agent_task_duration_seconds",
    "Agent 阶段任务耗时（秒，A1–A5）",
    ["stage"],
    buckets=(0.5, 1, 5, 10, 30, 60, 120, 300, 600, 900, 1200, 1800, 2400, 3600),
)
AGENT_TASK_TOTAL = _prom.Counter(
    "py_agent_task_total", "Agent 阶段任务计数", ["stage", "result"]
)
AGENT_SOFT_TIMEOUT = _prom.Counter(
    "py_agent_soft_timeout_total", "超 15 分钟软超时任务计数（告警①数据源）", ["stage"]
)
CADQUERY_EXEC = _prom.Counter(
    "py_cadquery_exec_total", "CadQuery 沙箱执行计数（含修复循环重跑；告警②数据源）", ["outcome"]
)
CADQUERY_FIX_ROUNDS = _prom.Histogram(
    "py_cadquery_fix_rounds", "A3 修复循环轮次", buckets=(0, 1, 2, 3, 4, 5)
)
A3_CODEGEN = _prom.Histogram(
    "py_a3_codegen_seconds",
    "A3 脚本生成耗时（秒，含缓存命中路径）",
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)
A3_SANDBOX = _prom.Histogram(
    "py_a3_sandbox_seconds",
    "CadQuery 沙箱执行耗时（秒，单次）",
    buckets=(0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)
GEOMETRY_INSPECT = _prom.Histogram(
    "py_geometry_inspect_seconds",
    "STEP 几何实测耗时（秒，geometry_inspect 子进程）",
    buckets=(0.5, 1, 2.5, 5, 10, 30, 60),
)
KB_QUERY_DURATION = _prom.Histogram(
    "py_kb_query_duration_seconds",
    "知识检索单条耗时（秒，含缓存命中；告警③数据源）",
    ["level"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 15, 30, 45, 60, 120, 300),
)
KB_QUERY_BATCH_SIZE = _prom.Histogram(
    "py_kb_query_batch_size", "检索批量的子查询条数（T10.4 优化对照基线）",
    buckets=(1, 2, 3, 4, 5, 6, 8, 10, 12, 16),
)
LLM_CALL_DURATION = _prom.Histogram(
    "py_llm_call_duration_seconds",
    "LLM 调用耗时（秒，单次尝试，不含缓存命中）",
    ["provider"],
    buckets=(0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)
LLM_CALL_TOTAL = _prom.Counter(
    "py_llm_call_total", "LLM 调用计数（单次尝试）", ["provider", "result"]
)
CACHE_OPS = _prom.Counter(
    "py_cache_ops_total", "L1 缓存操作计数（hit/miss）", ["layer", "result"]
)


# ---- 语义化打点入口（业务代码只调这些，不直接摸指标对象） ----
def observe_http(method: str, route: str, status: int, duration_s: float) -> None:
    label_route = route or "unmatched"
    HTTP_REQUESTS.labels(method=method, route=label_route, status=str(status)).inc()
    HTTP_LATENCY.labels(method=method, route=label_route).observe(duration_s)


def observe_agent_task(stage: str, duration_s: float, ok: bool) -> None:
    AGENT_TASK_DURATION.labels(stage=stage).observe(duration_s)
    AGENT_TASK_TOTAL.labels(stage=stage, result="ok" if ok else "error").inc()
    if duration_s > AGENT_SOFT_TIMEOUT_S:
        AGENT_SOFT_TIMEOUT.labels(stage=stage).inc()


def observe_cadquery(outcome: str) -> None:
    CADQUERY_EXEC.labels(outcome=outcome).inc()


def observe_a3_codegen(duration_s: float) -> None:
    A3_CODEGEN.observe(duration_s)


def observe_a3_sandbox(duration_s: float, ok: bool) -> None:
    A3_SANDBOX.observe(duration_s)
    observe_cadquery("ok" if ok else "fail")


def observe_fix_rounds(rounds: int) -> None:
    CADQUERY_FIX_ROUNDS.observe(rounds)


def observe_geometry_inspect(duration_s: float) -> None:
    GEOMETRY_INSPECT.observe(duration_s)


def observe_kb_query(duration_s: float, level: str) -> None:
    KB_QUERY_DURATION.labels(level=level).observe(duration_s)


def observe_kb_query_batch(n_queries: int) -> None:
    KB_QUERY_BATCH_SIZE.observe(n_queries)


def observe_llm(provider_name: str, duration_s: float, ok: bool) -> None:
    LLM_CALL_DURATION.labels(provider=provider_name).observe(duration_s)
    LLM_CALL_TOTAL.labels(provider=provider_name, result="ok" if ok else "error").inc()


def observe_cache(layer: str, hit: bool) -> None:
    CACHE_OPS.labels(layer=layer, result="hit" if hit else "miss").inc()


def start_metrics_server(port: int) -> bool:
    """worker 侧独立 /metrics 端口（与 API 进程互不聚合，按进程视角如实暴露）。"""
    if not HAS_PROMETHEUS:
        logger.warning("prometheus-client 未安装，worker /metrics 未暴露")
        return False
    try:
        _prom.start_http_server(port)
        logger.info("worker /metrics 已启动 :%s", port)
        return True
    except Exception as exc:  # noqa: BLE001 - 指标端口被占等故障不阻断 worker
        logger.warning("worker /metrics 启动失败（不影响任务执行）: %s", exc)
        return False
