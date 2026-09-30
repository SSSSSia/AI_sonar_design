"""L1 多层缓存（T9.6，PHASE-009 §2.6）。

三层键与失效纪律：
- cache:llm:call:{sha256(model+messages+temperature等kwargs)}   TTL 7d   开关 LLM_CACHE_ENABLED（默认开）
  供应商配置变更时随 reload_providers 一并清空；
- cache:kb:query:{sha256(query+kb_ids)}                         TTL 24h  KB 任何变更必清 cache:kb:*
  （文档增删/抽取确认/索引重建/删库——kb_store 变更方法统一挂钩）；降级级 text/unavailable 不缓存，
  避免劣化结果被粘住；
- cache:codegen:{sha256(IR2 参数指纹)}                           TTL 30d  参数方案变更自然 miss；
  A3 修复循环（_fix）不查缓存，保证失败重试真实执行。

运行纪律：Redis 不可用一律静默旁路（缓存故障不阻断主链路，60s 后重试连接）；
命中/未命中计数线程安全，GET /api/v1/kb/status 暴露（阶段 10 告警数据源；
计数按进程独立——API 与 worker 是两个进程，/status 汇报的是 API 进程视角，如实标注）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

from app_config import REDIS_URL

logger = logging.getLogger("sonarcad.cache")

TTL_LLM_S = 7 * 86400
TTL_KB_S = 24 * 3600
TTL_CODEGEN_S = 30 * 86400

_RECONNECT_INTERVAL_S = 60.0
_state: dict[str, Any] = {"client": None, "failed_at": 0.0, "lock": threading.Lock()}
_counters: dict[str, list[int]] = {
    "llm": [0, 0], "kb": [0, 0], "codegen": [0, 0],
}
_counters_lock = threading.Lock()


def _get_client():
    """惰性 Redis 客户端；不可用时静默旁路（60s 后才重试，避免每次调用都吃连接超时）。"""
    now = time.time()
    if _state["client"] is None:
        if now - _state["failed_at"] < _RECONNECT_INTERVAL_S:
            return None
        with _state["lock"]:
            if _state["client"] is None:
                if time.time() - _state["failed_at"] < _RECONNECT_INTERVAL_S:
                    return None
                try:
                    import redis

                    client = redis.Redis.from_url(
                        REDIS_URL, socket_timeout=2, socket_connect_timeout=2,
                        decode_responses=True,
                    )
                    client.ping()
                    _state["client"] = client
                    logger.info("L1 缓存 Redis 已连接: %s", _mask(REDIS_URL))
                except Exception as exc:  # noqa: BLE001 - 缓存故障不阻断主链路
                    _state["failed_at"] = time.time()
                    logger.warning("L1 缓存 Redis 不可用，旁路（主链路不受影响）: %s", exc)
                    return None
    return _state["client"]


def _mask(url: str) -> str:
    if "://" in url and "@" in url:
        creds, _, host = url.rpartition("@")
        scheme, _, _ = creds.partition("://")
        return f"{scheme}://****@{host}"
    return url


def fingerprint(*parts: Any) -> str:
    """缓存键指纹：canonical JSON → sha256。"""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bump(layer: str, hit: bool) -> None:
    with _counters_lock:
        _counters[layer][0 if hit else 1] += 1
    # Prometheus 侧同步打点（T10.1a）：本进程计数，随 /metrics（API）或 :9101（worker）暴露
    try:
        from observability.metrics import observe_cache

        observe_cache(layer, hit)
    except Exception:
        pass  # 指标故障不影响缓存主链路


def get_str(layer: str, key: str) -> str | None:
    """命中返回缓存值；未命中/Redis 不可用返回 None（不可用时计数不动，避免污染命中率）。"""
    client = _get_client()
    if client is None:
        return None
    try:
        value = client.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("L1 缓存读取失败（旁路）: %s", exc)
        return None
    _bump(layer, value is not None)
    return value


def set_str(key: str, value: str, ttl_s: int) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(key, ttl_s, value)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("L1 缓存写入失败（旁路）: %s", exc)
        return False


def get_json(layer: str, key: str) -> Any | None:
    raw = get_str(layer, key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def set_json(key: str, value: Any, ttl_s: int) -> bool:
    return set_str(key, json.dumps(value, ensure_ascii=False, default=str), ttl_s)


# ---- 三层专用入口（键名约定集中于此） ----
def llm_cache_key(provider_tag: str, messages: list[dict[str, str]], kwargs: dict[str, Any]) -> str:
    """provider_tag = "{base_url}|{model}"（T9.6 修订：不含供应商标识会让 mock/换供应商
    场景跨实例命中旧响应——e2e 不可重复的实测教训）。timeout_s 不参与（不影响生成语义）。"""
    semantic = {k: v for k, v in kwargs.items() if k != "timeout_s"}
    return f"cache:llm:call:{fingerprint(provider_tag, messages, semantic)}"


def kb_cache_key(query: str, kb_ids: list[str] | None) -> str:
    return f"cache:kb:query:{fingerprint(query, sorted(kb_ids or []))}"


def codegen_cache_key(ir2: dict[str, Any], provider_tag: str = "") -> str:
    """IR2 参数指纹：design_document + parameters（A2 产物变动即 miss）+ 供应商标识
    （同 IR2 换供应商应 miss——同上修订）。"""
    return f"cache:codegen:{fingerprint(ir2.get('design_document', ''), ir2.get('parameters', []), provider_tag)}"


def invalidate_kb() -> int:
    """KB 任何变更必清 cache:kb:*（返回删除键数；Redis 不可用返回 -1 如实）。"""
    client = _get_client()
    if client is None:
        return -1
    deleted = 0
    try:
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match="cache:kb:*", count=500)
            if keys:
                deleted += client.delete(*keys)
            if cursor == 0:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache:kb:* 清理失败: %s", exc)
        return -1
    if deleted:
        logger.info("KB 变更：已清空 cache:kb:* 共 %d 键", deleted)
    return deleted


def invalidate_llm() -> int:
    """供应商配置变更时清 cache:llm:call:*（reload_providers 钩子调用）。"""
    client = _get_client()
    if client is None:
        return -1
    deleted = 0
    try:
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match="cache:llm:call:*", count=500)
            if keys:
                deleted += client.delete(*keys)
            if cursor == 0:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache:llm:call:* 清理失败: %s", exc)
        return -1
    return deleted


def stats() -> dict[str, Any]:
    """命中率计数（阶段 10 告警数据源；本进程视角）。先触发惰性连接（含 60s 退避），
    保证 redis_available 不因「尚无缓存操作」而失真。"""
    client = _get_client()
    with _counters_lock:
        layers = {
            name: {"hit": c[0], "miss": c[1]}
            for name, c in _counters.items()
        }
    for name, c in layers.items():
        total = c["hit"] + c["miss"]
        c["hit_rate"] = round(c["hit"] / total * 100, 2) if total else None  # A-16：无数据不造假
    return {"redis_available": client is not None, "layers": layers}


def reset_stats() -> None:
    """测试用：清零本进程计数。"""
    with _counters_lock:
        for c in _counters.values():
            c[0] = 0
            c[1] = 0
